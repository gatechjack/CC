"""Kalshi live order broker — Phase K5·1b (V2 event-order endpoint).

K5·1 originally wired pykalshi 1.0.6's high-level `place_order`, but the
2026-06-30 prod shakedown proved that path POSTs the now-DEPRECATED v1
`/portfolio/orders` (HTTP 410 `deprecated_v1_order_endpoint` — Kalshi removed it
after 2026-05-06). K5·1b rebuilds the order path onto Kalshi's **V2 event-order
endpoint** `POST /portfolio/events/orders` (single-book, YES-centric bid/ask
shape), reusing pykalshi's PROVEN RSA-PSS signed transport (the low-level
`client.post` / `client.delete`) but bypassing its dead high-level order methods.
Reads (balance / markets / positions / fills) stay on pykalshi — they work.

Host: the recommended dedicated external Trade API host
`external-api.kalshi.com` (prod) / `external-api.demo.kalshi.co` (demo), plumbed
as an `api_base` override (pykalshi's built-in `api.elections.kalshi.com` is the
legacy-but-supported host).

V2 SIDE MODEL — load-bearing; grounded in
docs.kalshi.com/api-reference/orders/create-order-v2 (quoted):
  "bid means buy YES, ask means sell YES. Selling YES is economically equivalent
   to buying NO at 1 - price." There is NO separate NO ticker — the endpoint
  quotes everything from the YES side. So our (outcome, buy/sell) maps to
  (V2 side, yes-side price); `slip` = max_slippage_cents/100 always moves the
  yes-price in the fill-ENSURING direction (the venue then fills at the best
  available price = price improvement):

    buy  YES @P  -> side=bid, yes_price = P + slip          (entry)
    sell YES @P  -> side=ask, yes_price = P - slip          (exit, reduce_only)
    buy  NO  @P  -> side=ask, yes_price = (1 - P) - slip     (entry; = sell YES @ 1-P)
    sell NO  @P  -> side=bid, yes_price = (1 - P) + slip     (exit, reduce_only)

  `base_price` (P) is the per-contract price of OUR/the whale's leg — the YES
  price when outcome=yes, the NO price when outcome=no (the strategy's
  `limit_price` already carries the outcome-leg price). Prices clamp to Kalshi's
  1-99c band and format as 4-decimal dollar strings ("0.5600"); count is a
  whole-contract string ("1"). max_slippage_cents default 2 (locked), tunable.

The V2 create response carries `fill_count` / `average_fill_price` /
`average_fee_paid` directly, so no separate get_fills call is needed.

Fundless/mocked in tests; the real exchange is hit only with a live, funded
client. The pure mapping/sizing helpers import without the SDK.
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
# Kalshi contracts trade in [$0.01, $0.99]; a postable limit price is a whole cent.
_MIN_PRICE = 0.01
_MAX_PRICE = 0.99
_DEFAULT_MAX_SLIPPAGE_CENTS = 2
_IOC = "ioc"
_NATIVE_ORDER_TYPES = frozenset({"ioc", "fok", "gtc"})
# our order_type -> V2 time_in_force string.
_TIF = {"ioc": "immediate_or_cancel", "fok": "fill_or_kill", "gtc": "good_till_canceled"}
# Fixed namespace so client_order_id is stable for the same logical copy signal.
_COID_NAMESPACE = uuid.UUID("5f1b6e2a-0c3d-4a7e-9b6c-6b7a5e4d3c2b")
# V2 endpoint + recommended dedicated external hosts.
_PROD_API_BASE = "https://external-api.kalshi.com/trade-api/v2"
_DEMO_API_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"
_V2_ORDERS_PATH = "/portfolio/events/orders"
_SELF_TRADE_PREVENTION = "taker_at_cross"
# A FOK order that can't fill IMMEDIATELY + IN FULL returns this exchange code (HTTP
# 409). That is the "kill" half of fill-or-kill — a BENIGN no-fill, NOT a placement
# failure — so it maps to KalshiNoFill (same benign path as an IOC 0-fill). ONLY this
# specific code is treated as benign; every other KalshiError stays loud. (Demo
# validation 2026-06-30 surfaced this: a FOK on a crossed/thin book 409'd.)
_FOK_NOFILL_ERROR_CODES = ("fill_or_kill_insufficient_resting_volume",)


def _is_benign_fok_nofill(err: object) -> bool:
    """True iff `err` is the specific FOK-couldn't-fill exchange code (-> KalshiNoFill).
    Matches the pykalshi error message AND any structured code attribute; conservative
    — anything else is a genuine failure and stays loud."""
    text = str(err).lower()
    code = str(getattr(err, "code", "") or getattr(err, "error_code", "") or "").lower()
    return any(c in text or c in code for c in _FOK_NOFILL_ERROR_CODES)


class OrderPlacementError(RuntimeError):
    """A Kalshi order was rejected by the exchange, or terminated in a way that is
    a genuine failure (auth / bad-request / exchange reject). Propagates LOUD to
    the loop's outer handler. We never fabricate a phantom FillEvent."""


class KalshiNoFill(OrderPlacementError):
    """A marketable order matched ZERO contracts (best price beyond our limit, or
    nothing rested), OR the order could not be priced (e.g. a settled market).
    BENIGN and expected on a thin/moved/closed book: a SUBCLASS of
    OrderPlacementError so the copy loop can catch the benign no-fill BY TYPE —
    skip the order, no alarm — WITHOUT swallowing real placement failures."""


# ── Pure helpers (no SDK, no funds — fully box-testable) ──────────────────────


def round_to_cent(price: float) -> float:
    """Round a dollar price to the nearest whole cent and clamp into Kalshi's
    postable [$0.01, $0.99] band."""
    cents = round(float(price) * _CENTS_PER_DOLLAR)
    cents = min(int(_MAX_PRICE * _CENTS_PER_DOLLAR), max(int(_MIN_PRICE * _CENTS_PER_DOLLAR), cents))
    return cents / _CENTS_PER_DOLLAR


def usd_to_contracts(copy_usd: float, base_price: float) -> int:
    """Contracts = floor(USD copy size / per-contract price), min 1. `base_price`
    is the whale's per-contract (outcome-leg) price."""
    if base_price <= 0:
        raise ValueError(f"base_price must be > 0 to size contracts; got {base_price!r}")
    return max(1, int(math.floor(float(copy_usd) / float(base_price))))


def client_order_id(division: str, whale_handle: str, ticker: str, outcome: str, signal_id: str) -> str:
    """Deterministic idempotency key — a UUID5 over the logical-copy identity.
    Resubmitting the same logical copy returns the existing Kalshi order."""
    key = f"{division}|{whale_handle}|{ticker}|{outcome}|{signal_id}"
    return str(uuid.uuid5(_COID_NAMESPACE, key))


def parse_fp(value: object) -> float:
    """Parse a Kalshi fixed-point string (e.g. '10.00') to float; 0.0 on bad input."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def v2_side_and_price(*, outcome: str, is_buy: bool, base_price: float, max_slippage_cents: int) -> tuple[str, float]:
    """Map (outcome yes/no, buy/sell, outcome-leg base price) -> (V2 side bid/ask,
    yes-side price clamped to 1-99c). See the module docstring for the grounded
    YES-centric mapping. `base_price` is the YES price when outcome=yes and the NO
    price when outcome=no (the latter is converted to its YES equivalent 1-P)."""
    if outcome not in ("yes", "no"):
        raise ValueError(f"outcome must be 'yes'/'no'; got {outcome!r}")
    if not (0.0 < float(base_price) < 1.0):
        raise ValueError(f"base_price must be a contract price in (0,1); got {base_price!r}")
    slip = max(0, int(max_slippage_cents)) / _CENTS_PER_DOLLAR
    if outcome == "yes":
        side, yp = ("bid", base_price + slip) if is_buy else ("ask", base_price - slip)
    else:
        # NO leg quotes from the YES side at (1 - no_price): buy NO = sell YES (ask);
        # sell NO = buy YES (bid).
        yes_equiv = 1.0 - float(base_price)
        side, yp = ("ask", yes_equiv - slip) if is_buy else ("bid", yes_equiv + slip)
    return side, round_to_cent(yp)


def build_v2_event_order(
    *, ticker: str, outcome: str, is_buy: bool, base_price: float, copy_usd: float,
    max_slippage_cents: int, tif: str, client_order_id: str,
) -> tuple[dict, int, float]:
    """Build the V2 `POST /portfolio/events/orders` request body (pure). Returns
    `(body, count, yes_price)`. `price` is a 4-decimal dollar string ('0.5600'),
    `count` a whole-contract string ('1'); exits carry `reduce_only=True`."""
    side, price = v2_side_and_price(
        outcome=outcome, is_buy=is_buy, base_price=base_price, max_slippage_cents=max_slippage_cents,
    )
    count = usd_to_contracts(copy_usd, base_price)
    body = {
        "ticker": str(ticker).upper(),
        "client_order_id": client_order_id,
        "side": side,
        "count": str(int(count)),
        "price": "%.4f" % price,
        "time_in_force": tif,
        "self_trade_prevention_type": _SELF_TRADE_PREVENTION,
        "post_only": False,
    }
    if not is_buy:
        body["reduce_only"] = True
    return body, count, price


def fill_event_from_v2_response(
    resp: dict, *, symbol, side, fallback_price: float, fallback_order_id: str,
    outcome: str | None = None,
) -> FillEvent:
    """Map a V2 create-order response dict -> FillEvent for the FILLED portion.
    Raises KalshiNoFill if nothing matched. `average_fee_paid` is treated as a
    PER-CONTRACT average (total = avg_fee * filled); for the 1-contract validation
    cases this is exact regardless. (DEMO-VERIFY the per-contract-vs-total fee
    convention against the balance delta.)

    YES-centric -> outcome-leg conversion: the V2 single book quotes everything
    from the YES side, so `average_fill_price` (and `fallback_price`) are YES-side
    prices even for a NO fill. For a NO leg the per-contract cost of the contract
    we actually hold is `1 - yes_price`; YES is unchanged. Without this, a NO copy
    at yes_price 0.987 records price 0.987 instead of the real 0.013 cost — the
    prod $163.84 bug (166 NO contracts booked at 166×0.987 = $163.84 instead of
    166×0.013 ≈ $2.16). `outcome` is the resolved leg ('yes'/'no'); when omitted we
    fall back to parsing it out of the FillEvent symbol."""
    resp = resp or {}
    filled = parse_fp(resp.get("fill_count"))
    if filled <= 0:
        raise KalshiNoFill(
            f"kalshi V2 order {resp.get('order_id') or fallback_order_id} matched 0 "
            f"contracts (remaining={resp.get('remaining_count')}); no fill recorded"
        )
    avg = resp.get("average_fill_price")
    yes_price = float(avg) if avg not in (None, "") else float(fallback_price)
    leg = (str(outcome).strip().lower() if outcome else _outcome_from_symbol(symbol))
    price = (1.0 - yes_price) if leg == "no" else yes_price
    fee_avg = resp.get("average_fee_paid")
    total_fee = (float(fee_avg) * filled) if fee_avg not in (None, "") else 0.0
    order_id = str(resp.get("order_id") or fallback_order_id)
    return FillEvent(
        order_id=order_id, symbol=symbol, side=side, qty=float(filled),
        price=float(price), ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        venue="kalshi", fee=float(total_fee), role="taker", broker_order_id=order_id,
    )


def _ticker_from_symbol(symbol: str) -> str:
    return str(symbol or "").split(":", 1)[0]


def _outcome_from_symbol(symbol: str) -> str:
    parts = str(symbol or "").split(":", 1)
    return parts[1].strip().lower() if len(parts) == 2 else ""


# ── KalshiLiveBroker(Broker) ─────────────────────────────────────────────────


class KalshiLiveBroker(Broker):
    """Live Kalshi order broker — composes `KalshiBroker` (reads) + the V2
    event-order endpoint (placement) over pykalshi's signed transport. A
    placement-legal `Broker` (NOT `ReadOnlyBroker`). `paper = False`."""

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
        api_base: str | None = None,
    ) -> None:
        ot = str(order_type).strip().lower()
        if ot not in _NATIVE_ORDER_TYPES:
            raise ValueError(
                f"unsupported order_type {order_type!r}; expected one of {sorted(_NATIVE_ORDER_TYPES)}"
            )
        self._order_type = ot
        try:
            self._max_slippage_cents = max(0, int(max_slippage_cents))
        except (TypeError, ValueError):
            raise ValueError(f"max_slippage_cents must be an int; got {max_slippage_cents!r}")
        # Recommended dedicated external host (overridable). The order POST + reads
        # both go through this host on the live broker's own client.
        self._api_base = api_base or (_DEMO_API_BASE if demo else _PROD_API_BASE)
        self._read = KalshiBroker(
            api_key_id=api_key_id, private_key_pem=private_key_pem,
            demo=demo, api_base=self._api_base,
        )
        self._connected = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        await self._read.connect()
        if self._read._stub or self._read._client is None:
            raise RuntimeError("KalshiLiveBroker: credentials missing (stub) — cannot go live")
        try:
            bal = await self._read._client.portfolio.get_balance()
            cash = bal.balance / _CENTS_PER_DOLLAR
        except Exception as e:
            raise RuntimeError(f"KalshiLiveBroker preflight: balance read failed: {e}") from e
        if cash <= 0:
            raise RuntimeError(
                "KalshiLiveBroker preflight: account holds $0 — fund the Kalshi account before live"
            )
        self._connected = True
        log.info(
            "KalshiLiveBroker connected (host=%s, balance=$%.2f, order_type=%s, slip=%dc)",
            self._api_base, cash, self._order_type, self._max_slippage_cents,
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

    def _client(self):
        self._require_connected()
        client = self._read._client
        if client is None:
            raise RuntimeError("KalshiLiveBroker: read adapter has no client (stub?) — cannot place")
        return client

    # ── Placement (V2 event-order endpoint) ──────────────────────────────────

    async def place_order(self, order) -> FillEvent:
        """Map a copy `ProposedOrder` -> V2 event-order body -> POST
        `/portfolio/events/orders` -> FillEvent. Zero fill / unpriceable ->
        `KalshiNoFill` (benign skip). Exchange reject -> plain `OrderPlacementError`
        (loud). Filled qty on the FillEvent is the ACTUAL matched count."""
        self._require_connected()
        from pykalshi.exceptions import KalshiError

        extra = getattr(order, "extra", None) or {}
        ticker = str(extra.get("ticker") or "").strip() or _ticker_from_symbol(getattr(order, "symbol", ""))
        outcome = (str(extra.get("outcome") or "").strip().lower()
                   or _outcome_from_symbol(getattr(order, "symbol", "")))
        if outcome not in ("yes", "no"):
            raise OrderPlacementError(
                f"kalshi order: cannot resolve YES/NO side (symbol={getattr(order, 'symbol', None)!r}, "
                f"extra.outcome={extra.get('outcome')!r})"
            )
        if not ticker:
            raise OrderPlacementError(f"kalshi order: empty market ticker (symbol={getattr(order, 'symbol', None)!r})")
        is_buy = str(getattr(order, "side", "")).strip().lower() == "buy"

        # Resolve the per-contract base (outcome-leg) price: limit_price, else a
        # current quote (yes-mid; invert for a NO leg).
        base_price = getattr(order, "limit_price", None)
        try:
            base_price = float(base_price) if base_price is not None else None
        except (TypeError, ValueError):
            base_price = None
        if base_price is None or not (0.0 < base_price < 1.0):
            yes_mid = await self._read.quote(ticker)
            if yes_mid and yes_mid > 0:
                base_price = float(yes_mid) if outcome == "yes" else (1.0 - float(yes_mid))
        if base_price is None or not (0.0 < base_price < 1.0):
            # No placeable price (e.g. a settled/closed market quoting 0/1) — benign skip.
            raise KalshiNoFill(
                f"kalshi order for {ticker}: no placeable price (limit/quote unavailable or "
                f"market settled); skipped"
            )

        coid = client_order_id(
            str(extra.get("division", "")), str(extra.get("whale_handle", "")),
            ticker, outcome, str(getattr(order, "id", "")),
        )
        body, count, yes_price = build_v2_event_order(
            ticker=ticker, outcome=outcome, is_buy=is_buy, base_price=base_price,
            copy_usd=float(getattr(order, "qty", 0.0)),
            max_slippage_cents=self._max_slippage_cents,
            tif=_TIF[self._order_type], client_order_id=coid,
        )

        # NOTE (PM Stage 3 R7.c, 2026-08-29): this POST try/except is DUPLICATED in
        # prediction_markets/live_driver.py:make_place_fn -- the PM live driver POSTs the chokepoint's pre-built body
        # DIRECTLY (Jack's option (b)), not through place_order, so the approved body+coid are placed verbatim. A fix
        # to the benign-FOK-vs-loud split below has TWO homes; update both. (live_driver's copy ALSO maps a raw
        # transport error -> OrderPlacementError -- a deliberate divergence: the PM path treats a lost POST as
        # possibly-placed and journals it pending-first; this legacy path leaves that to its caller.)
        try:
            resp = await self._client().post(_V2_ORDERS_PATH, body)
        except KalshiError as e:
            # K5.1c: a FOK that couldn't fill (insufficient resting volume) is a BENIGN
            # no-fill (the kill half of fill-or-kill), same as an IOC 0-fill -> skip. Every
            # OTHER KalshiError (auth / bad-request / unknown-ticker / 5xx / etc.) is a
            # genuine failure and PROPAGATES loud. Nothing else is swallowed.
            if _is_benign_fok_nofill(e):
                raise KalshiNoFill(
                    f"kalshi FOK order for {ticker} ({body['side']} x{count} @ {body['price']}) "
                    f"did not fill (insufficient resting volume); killed, no fill recorded"
                ) from e
            raise OrderPlacementError(
                f"kalshi V2 place_order rejected for {ticker} ({outcome} {body['side']} "
                f"x{count} @ {body['price']}): {e}"
            ) from e

        return fill_event_from_v2_response(
            resp, symbol=getattr(order, "symbol", ticker),
            side=getattr(order, "side", "buy" if is_buy else "sell"),
            fallback_price=yes_price, fallback_order_id=coid,
            outcome=outcome,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a resting order by id via V2 `DELETE /portfolio/events/orders/{id}`.
        Returns True iff the delete call succeeded; **never raises** (Broker contract
        is `-> bool`). IOC/FOK self-cancel their remainder so this is rarely needed."""
        oid = str(order_id or "")
        if not oid:
            return False
        try:
            await self._client().delete(f"{_V2_ORDERS_PATH}/{oid}")
            return True
        except Exception as e:
            log.warning("kalshi V2 cancel_order(%s) failed: %s", oid[:24], e)
            return False


__all__ = [
    "KalshiLiveBroker",
    "OrderPlacementError",
    "KalshiNoFill",
    "v2_side_and_price",
    "build_v2_event_order",
    "fill_event_from_v2_response",
    "round_to_cent",
    "usd_to_contracts",
    "client_order_id",
    "parse_fp",
]
