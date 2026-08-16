"""Kalshi placement for the Poly->Kalshi MLB copy strategy (Phase 1, CP2).

DUPLICATE, not shared: per the ratified plan the ~40-line KalshiLiveBroker
placement pattern (build_v2_event_order + client_order_id UUID5 + the V2 POST) is
COPIED here so this strategy has an independent placement path and its own broker
instance. `kalshi_copy_trader` is NOT imported and NOT touched.

The block marked "COPIED VERBATIM" below is a byte-for-byte copy of the pure
helpers in `trading_corp/brokers/kalshi_live.py` (round_to_cent / usd_to_contracts
/ client_order_id / v2_side_and_price / build_v2_event_order). The CP2 report shows
`diff` == empty for that block. Everything under "NEW" is this strategy's own
translation + dry-run executor.

Side mapping (explicit): the CP1 matcher (`mlb_poly_kalshi_match`) resolves the
KXMLBGAME ticker whose YES side IS the club the whale bet — e.g. a bet on the
Yankees resolves `...NYYTOR-NYY`, "does NYY win == YES". So this strategy ALWAYS
trades the YES leg (`outcome="yes"`); it never places NO. A bet on the opponent
resolves to the opponent's own YES ticker instead. Whale BUY == entry (V2 side
`bid`); whale SELL == exit (V2 side `ask`, `reduce_only=True`).
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# COPIED VERBATIM from trading_corp/brokers/kalshi_live.py (lines 56-180 region).
# Do not edit here; if the source changes, re-copy and re-diff. Kept byte-identical
# so the duplication is auditable (see CP2 report: `diff` on this block is empty).
# ─────────────────────────────────────────────────────────────────────────────
_CENTS_PER_DOLLAR = 100
# Kalshi contracts trade in [$0.01, $0.99]; a postable limit price is a whole cent.
_MIN_PRICE = 0.01
_MAX_PRICE = 0.99
# our order_type -> V2 time_in_force string.
_TIF = {"ioc": "immediate_or_cancel", "fok": "fill_or_kill", "gtc": "good_till_canceled"}
# Fixed namespace so client_order_id is stable for the same logical copy signal.
_COID_NAMESPACE = uuid.UUID("5f1b6e2a-0c3d-4a7e-9b6c-6b7a5e4d3c2b")
_V2_ORDERS_PATH = "/portfolio/events/orders"
_SELF_TRADE_PREVENTION = "taker_at_cross"


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
# ───────────────────────────── end COPIED VERBATIM ──────────────────────────


# ═════════════════════════════════ NEW ══════════════════════════════════════
DIVISION = "poly_kalshi_mlb"
# CP1-review decision: auto-execute only at match confidence >= 0.97. Below
# (incl. doubleheader_ambiguous @ 0.50) -> skip + log, never place.
AUTO_EXEC_MIN_CONFIDENCE = 0.97
# Market order == marketable IOC with a max-price (slippage) cap. The cap value is
# the CP3 slippage guardrail; a placeholder is used until then.
_ORDER_TYPE = "ioc"
_DRYRUN_MAX_SLIPPAGE_CENTS = 2   # CP3 [G-slip] owns the real value


@dataclass(frozen=True)
class ProposedKalshiOrder:
    whale: str
    action: str            # "entry" (whale BUY) | "exit" (whale SELL)
    ticker: str
    outcome: str           # always "yes" (matcher resolves the bet-club's YES ticker)
    v2_side: str           # "bid" (buy YES) | "ask" (sell YES)
    count: int
    yes_price: float       # marketable limit (base_price -/+ slippage), clamped 1-99c
    stake_usd: float
    tif: str
    idempotency_key: str   # UUID5 client_order_id
    reduce_only: bool
    base_price: float
    confidence: float
    body: dict = field(default_factory=dict)   # the exact V2 POST body


def translate_whale_action(
    *, whale: str, kalshi_ticker: str, confidence: float, whale_side: str,
    base_price: float, stake_usd: float, max_slippage_cents: int = _DRYRUN_MAX_SLIPPAGE_CENTS,
) -> ProposedKalshiOrder:
    """A CP1-matched whale MLB action -> a fully-formed Kalshi order object.

    `whale_side` is the Poly activity side of a TRADE: BUY == entry, SELL == exit.
    Only TRADE BUY/SELL are copy signals — REDEEM / *_REBATE rows carry an empty side
    and are NOT signals (they resolved/settled), so anything but BUY/SELL is rejected
    here rather than silently treated as an exit. `kalshi_ticker` is the CP1-resolved
    ticker whose YES side is the club the whale bet, so `outcome` is always 'yes'.
    Pure — no network."""
    side_up = str(whale_side).upper()
    if side_up not in ("BUY", "SELL"):
        raise ValueError(
            f"whale_side must be BUY/SELL (TRADE copy signals only); got {whale_side!r}. "
            "REDEEM/rebate rows are not copy signals — filter type=='TRADE' upstream."
        )
    action = "entry" if side_up == "BUY" else "exit"
    is_buy = action == "entry"
    outcome = "yes"
    coid = client_order_id(DIVISION, whale, kalshi_ticker, outcome, action)
    body, count, yes_price = build_v2_event_order(
        ticker=kalshi_ticker, outcome=outcome, is_buy=is_buy, base_price=base_price,
        copy_usd=stake_usd, max_slippage_cents=max_slippage_cents,
        tif=_TIF[_ORDER_TYPE], client_order_id=coid,
    )
    return ProposedKalshiOrder(
        whale=whale, action=action, ticker=body["ticker"], outcome=outcome,
        v2_side=body["side"], count=count, yes_price=yes_price, stake_usd=stake_usd,
        tif=body["time_in_force"], idempotency_key=coid,
        reduce_only=bool(body.get("reduce_only", False)), base_price=base_price,
        confidence=confidence, body=body,
    )


class PolyKalshiExecutor:
    """Routes ProposedKalshiOrders to Kalshi. Dry-run by default (CP2): builds the
    order + idempotency key + logs what WOULD be sent, and STOPS before the network
    POST. The live branch (default off) is the duplicated V2 POST via this strategy's
    OWN broker instance's client.

    Guardrail insertion points are marked [G-*] in submit(); only [G-idem] (this CP)
    and the [G-conf] threshold are active here. The rest are wired in CP3."""

    def __init__(self, *, broker=None, dry_run: bool = True):
        self._broker = broker            # own KalshiLiveBroker instance (live path only)
        self._dry_run = bool(dry_run)
        self._placed: dict[str, ProposedKalshiOrder] = {}   # coid -> order (in-memory idempotency)
        self.log: list[dict] = []

    async def submit(self, order: ProposedKalshiOrder) -> dict:
        # [G-halt]  CP3: daily-loss auto-halt (RiskAgent / StrategyState.persist_halt) gates here.
        # [G-size]  CP3: per-trade size cap validates order.stake_usd here (stake is fixed).
        # [G-conf]  auto-execute threshold (ACTIVE, this CP's decision):
        if order.confidence < AUTO_EXEC_MIN_CONFIDENCE:
            return self._record("skip_below_threshold", order)
        # [G-idem]  idempotency (ACTIVE, THIS CP): one whale action -> at most one order.
        if order.idempotency_key in self._placed:
            return self._record("suppressed_duplicate", order)
        # [G-daily] CP3: in-memory daily deployment cap (running USD counter, NOT an
        #           audit_event aggregate query) gates here.
        # [G-slip]  CP3: max-slippage guard. The cap is already applied in build
        #           (max_slippage_cents -> yes_price); CP3 adds the live-book check.

        if self._dry_run:
            self._placed[order.idempotency_key] = order
            return self._record("DRY_RUN_would_place", order)

        # ── LIVE path (default OFF in CP2; not exercised) ──
        if self._broker is None:
            raise RuntimeError("PolyKalshiExecutor: live submit requires a connected broker")
        resp = await self._broker._client().post(_V2_ORDERS_PATH, order.body)  # duplicated V2 POST
        self._placed[order.idempotency_key] = order
        rec = self._record("placed", order)
        rec["resp"] = resp
        return rec

    def _record(self, status: str, order: ProposedKalshiOrder) -> dict:
        rec = {
            "status": status, "whale": order.whale, "action": order.action,
            "ticker": order.ticker, "side": order.v2_side, "outcome": order.outcome,
            "count": order.count, "stake_usd": order.stake_usd,
            "order_type": _ORDER_TYPE, "tif": order.tif, "price": order.body.get("price"),
            "idempotency_key": order.idempotency_key, "reduce_only": order.reduce_only,
            "confidence": order.confidence, "dry_run": self._dry_run,
        }
        self.log.append(rec)
        return rec
