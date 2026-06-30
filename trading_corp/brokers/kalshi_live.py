"""Kalshi live order broker — Phase K5·1.

K5 builds the live Kalshi execution path by WIRING the official `pykalshi` 1.0.6
SDK (RSA-PSS signing is internal to the SDK and already PROVEN LIVE for reads via
`KalshiBroker`). This mirrors the Polymarket E1/E2 template
(`brokers/polymarket_live.py`): a placement-legal `Broker` that COMPOSES the
read-only `KalshiBroker` for connect/disconnect/snapshot/quote and adds
`place_order`/`cancel_order` over the same authed `pykalshi` client.

Locked design (see plan `tidy-wandering-gadget.md`):

  * Order type = marketable **IOC** (`TimeInForce.IOC`) with the limit as a
    CEILING (buys) / FLOOR (sells): `base_price +/- max_slippage_cents` (default
    2c). A Kalshi limit IOC fills at the best ask <= ceiling (so a favorable lag
    move — whale @ $0.75, now $0.70 — fills us at $0.70, price improvement) and
    cancels any unfilled remainder. **Partials accepted** — a smaller copy is
    harmless at 1-6 contracts. `fok`/`gtc` are selectable via `order_type`.
  * `ProposedOrder.qty` is the **USD copy size** (the sizing tier output), NOT a
    contract count — so `place_order` converts USD -> contracts via
    `floor(copy_usd / base_price)` (min 1).
  * `ProposedOrder.extra` carries `ticker` (clean Kalshi market ticker) +
    `outcome` ("yes"/"no", the side from side-detection); `order.side` is
    buy(entry)/sell(exit) -> Kalshi `action` BUY/SELL. (Kalshi's `action`
    BUY/SELL and `side` YES/NO are ORTHOGONAL — both are required.)
  * Exits (SELL) are placed `reduce_only=True` — a deliberate safety default so a
    copy-exit can only REDUCE an existing position, never accidentally open an
    opposite-side short on a tracked-vs-venue position mismatch. Any unfilled
    residual is reconciled by the loop's `record_exit_fill` (K5·3).

Idempotency: `client_order_id` is a deterministic UUID5 of
`(division, whale_handle, ticker, outcome, signal_id)` so a duplicate submit of
the same logical copy is a no-op at Kalshi (resubmit returns the existing order).

Fundless/mocked in tests: the real exchange is hit only with a live, funded
client. The pure mapping/sizing helpers import without the SDK; the
SDK-touching methods import `pykalshi` enums lazily.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone

from trading_corp.brokers.base import Broker
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.persistence.models import FillEvent

log = logging.getLogger(__name__)

_CENTS_PER_DOLLAR = 100
# Kalshi contracts trade in [$0.01, $0.99]; a postable limit price must be a whole
# cent inside that band.
_MIN_PRICE = 0.01
_MAX_PRICE = 0.99
_DEFAULT_MAX_SLIPPAGE_CENTS = 2
_IOC = "ioc"
_NATIVE_ORDER_TYPES = frozenset({"ioc", "fok", "gtc"})
# IOC/FOK resolve to terminal essentially immediately; keep the confirm wait short.
_TERMINAL_TIMEOUT_SEC = 10.0
# Fixed namespace so client_order_id is stable for the same logical copy signal.
_COID_NAMESPACE = uuid.UUID("5f1b6e2a-0c3d-4a7e-9b6c-6b7a5e4d3c2b")


class OrderPlacementError(RuntimeError):
    """A Kalshi order was rejected by the exchange, or terminated in a way that is
    a genuine failure (auth / bad-request / exchange reject). Propagates LOUD to
    the loop's outer handler. We never fabricate a phantom FillEvent."""


class KalshiNoFill(OrderPlacementError):
    """A marketable IOC matched ZERO contracts (the best ask sat above our
    ceiling, or nothing rested). BENIGN and expected on a thin/moved book: a
    SUBCLASS of OrderPlacementError so the copy loop can catch the benign no-fill
    BY TYPE — skip the order, no alarm — WITHOUT swallowing real placement
    failures, which keep raising plain OrderPlacementError. Mirrors Polymarket's
    `NoFillInWindow`."""


# ── Pure mapping / sizing helpers (no SDK, no funds — fully box-testable) ─────


def round_to_cent(price: float) -> float:
    """Round a dollar price to the nearest whole cent and clamp into Kalshi's
    postable [$0.01, $0.99] band. A limit that math'd to <= 0 or >= $1 (e.g. an
    aggressive slippage step off a 1c/99c book) would be rejected by the venue —
    this guarantees a postable price."""
    cents = round(float(price) * _CENTS_PER_DOLLAR)
    cents = min(int(_MAX_PRICE * _CENTS_PER_DOLLAR), max(int(_MIN_PRICE * _CENTS_PER_DOLLAR), cents))
    return cents / _CENTS_PER_DOLLAR


def ceiling_price(base_price: float, *, is_buy: bool, max_slippage_cents: int) -> float:
    """The marketable-limit price: whale `base_price` adjusted by the slippage
    tolerance in the ADVERSE direction — a buy will pay UP TO `base + slip`
    (ceiling), a sell will accept DOWN TO `base - slip` (floor) — then rounded to
    a postable cent. Because the venue fills a limit at the best available price,
    a favorable move still fills better than this bound (price improvement)."""
    slip = max(0, int(max_slippage_cents)) / _CENTS_PER_DOLLAR
    raw = base_price + slip if is_buy else base_price - slip
    return round_to_cent(raw)


def usd_to_contracts(copy_usd: float, base_price: float) -> int:
    """Contracts = floor(USD copy size / per-contract price), min 1. `base_price`
    is the whale's per-contract price (not the slipped ceiling) so the contract
    count reflects the intended dollar exposure."""
    if base_price <= 0:
        raise ValueError(f"base_price must be > 0 to size contracts; got {base_price!r}")
    return max(1, int(math.floor(float(copy_usd) / float(base_price))))


def client_order_id(division: str, whale_handle: str, ticker: str, outcome: str, signal_id: str) -> str:
    """Deterministic idempotency key — a UUID5 over the logical-copy identity.
    Resubmitting the same logical copy returns the existing Kalshi order."""
    key = f"{division}|{whale_handle}|{ticker}|{outcome}|{signal_id}"
    return str(uuid.uuid5(_COID_NAMESPACE, key))


def parse_fp(value: object) -> float:
    """Parse a pykalshi fixed-point count string (e.g. '10.00', '-5.00') to a
    float. Defensive: 0.0 on any non-numeric input."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def build_kalshi_order_params(
    *, ticker: str, outcome: str, is_buy: bool, base_price: float,
    copy_usd: float, max_slippage_cents: int,
) -> dict:
    """ProposedOrder fields -> Kalshi place_order args (pure).

    Returns `{ticker, count_fp, price_field, price_dollars, price_float, count}`
    where `price_field` is `'yes_price_dollars'`/`'no_price_dollars'` (exactly one
    is passed to `place_order`) and `price_dollars` is the rounded ceiling/floor as
    a dollar STRING ('0.47'). count_fp is a WHOLE-number string (Kalshi contracts
    are integral when fractional trading is off)."""
    if outcome not in ("yes", "no"):
        raise ValueError(f"outcome must be 'yes'/'no'; got {outcome!r}")
    if base_price <= 0:
        raise ValueError(f"base_price must be > 0; got {base_price!r}")
    price = ceiling_price(base_price, is_buy=is_buy, max_slippage_cents=max_slippage_cents)
    count = usd_to_contracts(copy_usd, base_price)
    return {
        "ticker": str(ticker).upper(),
        "count_fp": str(count),
        "price_field": "yes_price_dollars" if outcome == "yes" else "no_price_dollars",
        "price_dollars": f"{price:.2f}",
        "price_float": price,
        "count": count,
    }


def compute_fill_economics(fills, *, outcome: str, fallback_price: float) -> tuple[float, float, str]:
    """Aggregate a list of pykalshi `FillModel` rows into (avg_price, total_fee,
    role) for the FILLED portion. `avg_price` is count-weighted from the side's
    per-fill price; `total_fee` sums `fee_cost_dollars`; `role` is taker/maker/
    mixed from `is_taker` (an IOC takes liquidity -> taker). Falls back to
    `fallback_price` (the order's limit) + fee 0 + role 'taker' when fills are
    unavailable (e.g. a get_fills error)."""
    total_count = 0.0
    notional = 0.0
    fee = 0.0
    taker = 0
    maker = 0
    price_attr = "yes_price_dollars" if outcome == "yes" else "no_price_dollars"
    for f in (fills or []):
        c = parse_fp(getattr(f, "count_fp", 0))
        if c <= 0:
            continue
        try:
            px = float(getattr(f, price_attr, 0) or 0)
        except (TypeError, ValueError):
            px = 0.0
        total_count += c
        notional += c * px
        try:
            fee += float(getattr(f, "fee_cost_dollars", 0) or 0)
        except (TypeError, ValueError):
            pass
        if getattr(f, "is_taker", True):
            taker += 1
        else:
            maker += 1
    avg_price = (notional / total_count) if total_count > 0 else float(fallback_price)
    if taker and maker:
        role = "mixed"
    elif maker:
        role = "maker"
    else:
        role = "taker"
    return avg_price, fee, role


def _ticker_from_symbol(symbol: str) -> str:
    """Clean ticker from the strategy's `"{ticker}:{side}"` symbol (fallback when
    extra['ticker'] is absent)."""
    return str(symbol or "").split(":", 1)[0]


def _outcome_from_symbol(symbol: str) -> str:
    parts = str(symbol or "").split(":", 1)
    return parts[1].strip().lower() if len(parts) == 2 else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── KalshiLiveBroker(Broker) ─────────────────────────────────────────────────


class KalshiLiveBroker(Broker):
    """Live Kalshi order broker — composes `KalshiBroker` (reads) + pykalshi order
    API (placement). A placement-legal `Broker` (NOT `ReadOnlyBroker`).

    Reads (connect/disconnect/snapshot/quote) delegate to the proven read adapter;
    `place_order`/`cancel_order` go through the same authed `pykalshi` client's
    `.portfolio`. `paper = False`.
    """

    name = "kalshi-live"
    paper = False

    def __init__(
        self,
        api_key_id: str | None = None,
        private_key_pem: str | None = None,
        *,
        demo: bool = False,
        order_type: str = _IOC,
        max_slippage_cents: int = _DEFAULT_MAX_SLIPPAGE_CENTS,
    ) -> None:
        ot = str(order_type).strip().lower()
        if ot not in _NATIVE_ORDER_TYPES:
            raise ValueError(
                f"unsupported order_type {order_type!r}; expected one of "
                f"{sorted(_NATIVE_ORDER_TYPES)}"
            )
        self._order_type = ot
        try:
            self._max_slippage_cents = max(0, int(max_slippage_cents))
        except (TypeError, ValueError):
            raise ValueError(f"max_slippage_cents must be an int; got {max_slippage_cents!r}")
        # Compose the read adapter for connect/disconnect/snapshot/quote + the
        # authed pykalshi client (its `.portfolio` is the order surface).
        self._read = KalshiBroker(
            api_key_id=api_key_id, private_key_pem=private_key_pem, demo=demo,
        )
        self._connected = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        await self._read.connect()
        # A LIVE broker MUST have real credentials and a funded account. Fail
        # CLOSED — a stub/unfunded live start raises here, not mid-trade.
        if self._read._stub or self._read._client is None:
            raise RuntimeError(
                "KalshiLiveBroker: credentials missing (stub) — cannot go live"
            )
        try:
            bal = await self._read._client.portfolio.get_balance()
            cash = bal.balance / _CENTS_PER_DOLLAR
        except Exception as e:
            raise RuntimeError(
                f"KalshiLiveBroker preflight: balance read failed: {e}"
            ) from e
        if cash <= 0:
            raise RuntimeError(
                "KalshiLiveBroker preflight: account holds $0 — fund the Kalshi "
                "account before going live"
            )
        self._connected = True
        log.info(
            "KalshiLiveBroker connected (balance=$%.2f, order_type=%s, slip=%dc)",
            cash, self._order_type, self._max_slippage_cents,
        )

    async def disconnect(self) -> None:
        await self._read.disconnect()
        self._connected = False

    async def snapshot(self):
        return await self._read.snapshot()

    async def quote(self, symbol: str) -> float:
        return await self._read.quote(symbol)

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("KalshiLiveBroker not connected — call connect() first")

    def _portfolio(self):
        self._require_connected()
        client = self._read._client
        if client is None:
            raise RuntimeError(
                "KalshiLiveBroker: read adapter has no client (stub?) — cannot place"
            )
        return client.portfolio

    # ── Placement ────────────────────────────────────────────────────────────

    async def place_order(self, order) -> FillEvent:
        """Map a copy `ProposedOrder` -> marketable IOC -> confirm -> FillEvent.

        Zero fill -> `KalshiNoFill` (benign skip). Exchange reject / bad request ->
        plain `OrderPlacementError` (loud). The filled qty on the FillEvent is the
        ACTUAL matched count, never the intended.
        """
        self._require_connected()
        from pykalshi import Action, Side, TimeInForce
        from pykalshi.exceptions import KalshiError

        extra = getattr(order, "extra", None) or {}
        ticker = str(extra.get("ticker") or "").strip() or _ticker_from_symbol(getattr(order, "symbol", ""))
        outcome = (str(extra.get("outcome") or "").strip().lower()
                   or _outcome_from_symbol(getattr(order, "symbol", "")))
        if outcome not in ("yes", "no"):
            raise OrderPlacementError(
                f"kalshi order: cannot resolve YES/NO side from order "
                f"(symbol={getattr(order, 'symbol', None)!r}, extra.outcome={extra.get('outcome')!r})"
            )
        if not ticker:
            raise OrderPlacementError(
                f"kalshi order: empty market ticker (symbol={getattr(order, 'symbol', None)!r})"
            )
        is_buy = str(getattr(order, "side", "")).strip().lower() == "buy"

        # Resolve the per-contract base price: the whale's matched price
        # (limit_price), else a current quote (mid; invert for a NO holding).
        base_price = getattr(order, "limit_price", None)
        try:
            base_price = float(base_price) if base_price is not None else None
        except (TypeError, ValueError):
            base_price = None
        if base_price is None or base_price <= 0:
            yes_mid = await self._read.quote(ticker)
            if yes_mid and yes_mid > 0:
                base_price = float(yes_mid) if outcome == "yes" else (1.0 - float(yes_mid))
        if not base_price or base_price <= 0:
            raise OrderPlacementError(
                f"kalshi order for {ticker}: no usable price (limit_price and "
                f"quote both unavailable) — cannot size/limit the order"
            )

        params = build_kalshi_order_params(
            ticker=ticker, outcome=outcome, is_buy=is_buy,
            base_price=base_price, copy_usd=float(getattr(order, "qty", 0.0)),
            max_slippage_cents=self._max_slippage_cents,
        )
        coid = client_order_id(
            str(extra.get("division", "")), str(extra.get("whale_handle", "")),
            ticker, outcome, str(getattr(order, "id", "")),
        )

        action = Action.BUY if is_buy else Action.SELL
        side_enum = Side.YES if outcome == "yes" else Side.NO
        tif = {"ioc": TimeInForce.IOC, "fok": TimeInForce.FOK, "gtc": TimeInForce.GTC}[self._order_type]
        place_kwargs = {
            params["price_field"]: params["price_dollars"],
            "time_in_force": tif,
            "client_order_id": coid,
        }
        # Exit (SELL): reduce_only so a copy-exit can only REDUCE an existing
        # venue position, never accidentally open an opposite-side short.
        if not is_buy:
            place_kwargs["reduce_only"] = True

        pf = self._portfolio()
        try:
            ko = await pf.place_order(
                params["ticker"], action, side_enum, params["count_fp"], **place_kwargs,
            )
        except KalshiError as e:
            raise OrderPlacementError(
                f"kalshi place_order rejected for {ticker} ({outcome} x{params['count']} "
                f"@ {params['price_dollars']}): {e}"
            ) from e

        # Confirm terminal. IOC/FOK resolve essentially immediately; a wait that
        # times out (resting GTC) still lets us read whatever matched.
        try:
            await ko.wait_until_terminal(timeout=_TERMINAL_TIMEOUT_SEC)
        except TimeoutError:
            try:
                await ko.refresh()
            except Exception as e:  # pragma: no cover - defensive
                log.warning("kalshi order %s refresh after timeout failed: %s",
                            getattr(ko, "order_id", "?"), e)

        filled = parse_fp(getattr(ko, "fill_count_fp", 0))
        if filled <= 0:
            raise KalshiNoFill(
                f"kalshi {self._order_type.upper()} order {getattr(ko, 'order_id', '?')} "
                f"for {ticker} matched 0 contracts (ask beyond ceiling "
                f"{params['price_dollars']}); no fill recorded"
            )

        avg_price, total_fee, role = await self._fetch_fill_economics(
            ko, outcome=outcome, fallback_price=params["price_float"],
        )
        order_id = str(getattr(ko, "order_id", "") or coid)
        return FillEvent(
            order_id=order_id,
            symbol=getattr(order, "symbol", ticker),
            side=getattr(order, "side", "buy" if is_buy else "sell"),
            qty=float(filled),
            price=float(avg_price),
            ts=_now_iso(),
            venue="kalshi",
            fee=float(total_fee),
            role=role,
            broker_order_id=order_id,
        )

    async def _fetch_fill_economics(self, ko, *, outcome: str, fallback_price: float):
        """Query the order's fills for the realized avg price + fee + role. On any
        error, fall back to the limit price (conservative) + fee 0 + taker."""
        pf = self._portfolio()
        fills = None
        try:
            fills = await pf.get_fills(order_id=getattr(ko, "order_id", None), fetch_all=True)
        except Exception as e:
            log.warning(
                "kalshi get_fills failed for order %s: %s — using limit price as fill price",
                getattr(ko, "order_id", "?"), e,
            )
        return compute_fill_economics(fills, outcome=outcome, fallback_price=fallback_price)

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a resting Kalshi order by id; return True iff cancelled, else
        False. **Never raises** (the Broker contract is `-> bool`). An IOC
        self-cancels its remainder so explicit cancel is rarely needed, but the
        contract requires it (and exit-management may use it later)."""
        oid = str(order_id or "")
        if not oid:
            return False
        try:
            pf = self._portfolio()
            await pf.cancel_order(oid)
            return True
        except Exception as e:
            log.warning("kalshi cancel_order(%s) failed: %s", oid[:24], e)
            return False


__all__ = [
    "KalshiLiveBroker",
    "OrderPlacementError",
    "KalshiNoFill",
    "build_kalshi_order_params",
    "ceiling_price",
    "round_to_cent",
    "usd_to_contracts",
    "client_order_id",
    "compute_fill_economics",
    "parse_fp",
]
