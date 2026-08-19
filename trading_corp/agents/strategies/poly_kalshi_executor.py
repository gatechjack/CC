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


def _fill_fields_from_v2_resp(resp: dict | None, *, outcome: str) -> dict:
    """FLAG 1 — extract the persistable fill facts from a Kalshi V2 create-order
    response, so the REAL fill (not the limit) is journaled with the order row.

    `resp` is the flat dict returned by `client.post(_V2_ORDERS_PATH, body)` — the
    SAME dict the canonical parser `kalshi_live.fill_event_from_v2_response`
    consumes (kalshi_live.py:200-218), so the field reads here are byte-for-byte
    that parser's: `order_id`, `fill_count`, `average_fill_price`,
    `average_fee_paid`. Returns {order_id, fill_count, fill_price, fill_fee}.

    `fill_price` is the OUTCOME-LEG per-contract cost: for a NO leg it is
    (1 - yes_price) (kalshi_live.py:211 — the $163.84 book-side bug); this strategy
    is always YES (module docstring), so it equals `average_fill_price` here. Never
    raises: a missing/zero fill yields fill_count=0 / fill_price=None so the row
    still records the order_id (unlike the parser, which raises KalshiNoFill — we
    keep the audit row for a 0-fill placement rather than dropping it)."""
    resp = resp or {}
    try:
        filled = int(float(resp.get("fill_count") or 0))
    except (TypeError, ValueError):
        filled = 0
    avg = resp.get("average_fill_price")
    try:
        yes_px = float(avg) if avg not in (None, "") else None
    except (TypeError, ValueError):
        yes_px = None
    fill_price = None if yes_px is None else (
        (1.0 - yes_px) if str(outcome).lower() == "no" else yes_px)
    fee_avg = resp.get("average_fee_paid")
    try:
        fee_per = float(fee_avg) if fee_avg not in (None, "") else 0.0
    except (TypeError, ValueError):
        fee_per = 0.0
    return {
        "order_id": str(resp.get("order_id") or ""),
        "fill_count": filled,
        "fill_price": fill_price,
        "fill_fee": fee_per * filled,
    }


@dataclass(frozen=True)
class ProposedKalshiOrder:
    whale: str             # display label (user_name) — NOT the idempotency key
    whale_wallet: str      # true identity; the idempotency key is keyed on THIS
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
    *, whale: str, whale_wallet: str, kalshi_ticker: str, confidence: float, whale_side: str,
    base_price: float, stake_usd: float, max_slippage_cents: int = _DRYRUN_MAX_SLIPPAGE_CENTS,
) -> ProposedKalshiOrder:
    """A CP1-matched whale MLB action -> a fully-formed Kalshi order object.

    `whale` is the display label (user_name); `whale_wallet` is the true identity.
    The idempotency key is keyed on WALLET, not the display name — the wallet is
    byte-stable everywhere and immune to display-name edits.

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
    if not whale_wallet:
        raise ValueError("whale_wallet is required — the idempotency key is keyed on wallet")
    action = "entry" if side_up == "BUY" else "exit"
    is_buy = action == "entry"
    outcome = "yes"
    # idempotency keyed on WALLET (true identity), NOT the display user_name.
    coid = client_order_id(DIVISION, whale_wallet, kalshi_ticker, outcome, action)
    body, count, yes_price = build_v2_event_order(
        ticker=kalshi_ticker, outcome=outcome, is_buy=is_buy, base_price=base_price,
        copy_usd=stake_usd, max_slippage_cents=max_slippage_cents,
        tif=_TIF[_ORDER_TYPE], client_order_id=coid,
    )
    return ProposedKalshiOrder(
        whale=whale, whale_wallet=whale_wallet, action=action, ticker=body["ticker"], outcome=outcome,
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

    def __init__(self, *, broker=None, dry_run: bool = True,
                 strategy: str = DIVISION,
                 db_url: str = "sqlite:///data/trading_corp.db",
                 per_trade_cap_usd: float = 5.00,
                 daily_deployment_cap_usd: float = 20.00,
                 max_slippage_cents: int = 2,
                 max_orders_per_day: int | None = 25,
                 logger=None, notify_fn=None):
        self._broker = broker            # own KalshiLiveBroker instance (live path only)
        self._dry_run = bool(dry_run)
        self._strategy = strategy        # [G-halt] key into the shared StrategyState halt row
        self._db_url = db_url
        self._logger = logger            # [audit] logger_agent.log_event(strategy, kind, payload)
        self._notify_fn = notify_fn      # [Part 2] async push(text, *, audit_path=...) — LIVE copies only
        # [G-count] real-time same-day trade-count ceiling (count-only; no P&L/settlement)
        self._max_orders_per_day = None if max_orders_per_day is None else int(max_orders_per_day)
        self._orders_today: int = 0
        # ── guardrail config. None => that cap is DISABLED (operator's launch
        #    choice: no per-trade cap, no daily-deployment cap; the $100 daily-loss
        #    halt is the active backstop). ──
        self._per_trade_cap_usd = None if per_trade_cap_usd is None else float(per_trade_cap_usd)          # [G-size]
        self._daily_deployment_cap_usd = None if daily_deployment_cap_usd is None else float(daily_deployment_cap_usd)  # [G-daily]
        self._max_slippage_cents = int(max_slippage_cents)                 # [G-slip]
        # ── state: IN-PROCESS ONLY. No audit_event / DB aggregate query. ──
        self._placed: dict[str, ProposedKalshiOrder] = {}   # [G-idem] coid -> order
        self._deployed_usd: float = 0.0                     # [G-daily] running in-memory counter
        self.log: list[dict] = []

    def _is_halted(self) -> bool:
        """[G-halt] Reuse the SAME cross-process halt every other division uses:
        StrategyState.from_persistence reads the `agent_state` row that RiskAgent's
        daily-loss branch writes via StrategyState.persist_halt. Read failure degrades
        to not-halted (the primitive's documented contract)."""
        from trading_corp.persistence.models import StrategyState
        return StrategyState.from_persistence(self._strategy, db_url=self._db_url).halted

    def _persist_halt(self, reason: str) -> None:
        """Trip the SAME persistent halt (survives restart); [G-halt] then blocks all."""
        from trading_corp.persistence.models import StrategyState
        StrategyState.persist_halt(self._strategy, reason, db_url=self._db_url)

    def _exceeds_slippage(self, order: ProposedKalshiOrder, quote: dict) -> bool:
        """[G-slip] Thin-book protection. True when the marketable fill price is more
        than max_slippage_cents from the whale's base price. `quote` = YES-side best
        prices {yes_ask, yes_bid} in (0,1)."""
        cap = self._max_slippage_cents / _CENTS_PER_DOLLAR
        if order.action == "entry":                      # buy YES crosses the ask
            return (float(quote.get("yes_ask", 1.0)) - order.base_price) > cap
        return (order.base_price - float(quote.get("yes_bid", 0.0))) > cap  # sell YES crosses the bid

    def _opposite_side_on_game(self, order: ProposedKalshiOrder) -> dict | None:
        """[G-conflict] Durable first-side-wins lookup. Returns:
          - None                                     -> no conflict (ALLOW).
          - {'status':'skip_conflict', ticker, side} -> the OPPOSITE side of this game is
            already held today (BLOCK).
          - {'status':'skip_gate_error'}             -> the durable lookup FAILED -> fail
            CLOSED (SKIP). This gate is loss-prevention; failing OPEN on a lookup error
            would reopen the guaranteed -100% both-sides leg exactly when the check breaks.

        Reads the audit journal (actor=self._strategy, kind=poly_kalshi_order, status
        placed/would-place) so the block survives an engine restart. An UNPARSEABLE
        ticker returns None (fail OPEN — can't be evaluated; idempotency + slippage are
        the backstop). Never raises."""
        try:
            from trading_corp.data.mlb_poly_kalshi_match import game_key_and_side
            from trading_corp.persistence import db as _db
            mine = game_key_and_side(order.ticker)
            if mine is None:
                return None                              # unparseable -> can't conflict -> ALLOW (fail open)
            my_key, my_side, date_str = mine
            like = "KXMLBGAME-" + date_str + "%"         # cheap same-date prefix scan
            with _db.connect(self._db_url) as conn:
                rows = conn.execute(
                    "SELECT json_extract(payload_json,'$.ticker') AS tkr "
                    "FROM audit_event "
                    "WHERE actor = ? AND kind = 'poly_kalshi_order' "
                    "  AND json_extract(payload_json,'$.status') IN ('placed','DRY_RUN_would_place') "
                    "  AND json_extract(payload_json,'$.ticker') LIKE ?",
                    (self._strategy, like),
                ).fetchall()
            for r in rows:
                got = game_key_and_side(r["tkr"] or "")
                if got is None:
                    continue
                held_key, held_side, _ = got
                if held_key == my_key and held_side != my_side:
                    return {"status": "skip_conflict", "ticker": r["tkr"], "side": held_side}
            return None
        except Exception:  # noqa: BLE001 — conflict lookup must never break the execute path
            # fail CLOSED: a lookup failure SKIPS the placement (Jack's ruling). Better to
            # miss a copy than to reopen the guaranteed-loss both-sides leg on a broken check.
            log.warning("poly_kalshi [G-conflict] lookup failed; failing CLOSED (skip)", exc_info=True)
            return {"status": "skip_gate_error"}

    async def submit(self, order: ProposedKalshiOrder, *, market_quote: dict | None = None,
                     trigger: dict | None = None) -> dict:
        """Route one order through the guardrails, in this fixed order:
        [G-halt] -> [G-size] -> [G-conf] -> [G-idem] -> [G-conflict] -> [G-daily] ->
        [G-slip] -> [G-count]. State (daily counter + idempotency key) is mutated ONLY
        after every gate passes, so a reject at ANY gate consumes no budget and burns
        no key."""
        # [G-halt] FIRST — short-circuit before any counter/idempotency mutation.
        if self._is_halted():
            return self._record("blocked_halt", order)
        # [G-size] per-trade size cap (None => disabled).
        if self._per_trade_cap_usd is not None and order.stake_usd > self._per_trade_cap_usd:
            return self._record("blocked_size_cap", order)
        # [G-conf] auto-execute threshold (>= 0.97).
        if order.confidence < AUTO_EXEC_MIN_CONFIDENCE:
            return self._record("skip_below_threshold", order)
        # [G-idem] idempotency READ (the key is burned only at commit, below).
        if order.idempotency_key in self._placed:
            return self._record("suppressed_duplicate", order)
        # [G-conflict] first-side-wins (Option B, DURABLE): once we've taken a side on a
        # game today, block a later OPPOSITE-side signal on that SAME game (either side,
        # any whale) — the wrong-side leg of a binary market is a guaranteed -100% loss.
        # SAME-side stacking (a 2nd whale on the same team) is still allowed. Reads the
        # audit journal so the block survives an engine restart (a same-cycle placement
        # is already committed by _record's log_event). An unparseable ticker fails OPEN
        # (allow — can't be evaluated; idempotency + slippage backstop); a lookup ERROR
        # fails CLOSED (skip_gate_error) — loss-prevention must not fail open on a broken
        # check. Either way the skip mutates no budget/count/key.
        conflict = self._opposite_side_on_game(order)
        if conflict is not None:
            extra = ({"conflict_held_side": conflict.get("side"),
                      "conflict_held_ticker": conflict.get("ticker")}
                     if conflict.get("status") == "skip_conflict" else None)
            return self._record(conflict.get("status", "skip_conflict"), order,
                                trigger=trigger, extra=extra)
        # [G-daily] in-memory daily deployment cap. Reads self._deployed_usd — a plain
        #           in-process float, NOT an audit_event aggregate query (that full-scan
        #           froze the engine; removed 2026-06-16).
        if (self._daily_deployment_cap_usd is not None
                and self._deployed_usd + order.stake_usd > self._daily_deployment_cap_usd):
            return self._record("blocked_daily_cap", order)
        # [G-slip] max-slippage on the market order. Evaluated whenever a book quote is
        #          present; live with NO quote fails CLOSED (cannot verify -> reject).
        if market_quote is not None:
            if self._exceeds_slippage(order, market_quote):
                return self._record("blocked_slippage", order)
        elif not self._dry_run:
            return self._record("blocked_slippage_no_quote", order)
        # [G-count] real-time same-day trade-count ceiling (count-only; no P&L/settlement
        # dependency). At the ceiling, trip a PERSISTENT halt so every subsequent submit
        # is [G-halt]-blocked. The counter increments at commit (would-place) and resets
        # on UTC day-rollover (loop._rollover_if_needed).
        if self._max_orders_per_day is not None and self._orders_today >= self._max_orders_per_day:
            self._persist_halt(
                f"trade-count ceiling: {self._orders_today} >= {self._max_orders_per_day}/day")
            return self._record("blocked_count_ceiling", order)

        # ── ALL GATES PASSED — commit state exactly once ──
        fill: dict | None = None
        if not self._dry_run:
            if self._broker is None:
                raise RuntimeError("PolyKalshiExecutor: live submit requires a connected broker")
            resp = await self._broker._client().post(_V2_ORDERS_PATH, order.body)  # duplicated V2 POST
            # FLAG 1: parse the REAL fill NOW and journal it WITH the row via
            # _record. The old code set rec["resp"] = resp AFTER _record had
            # already written the audit row, so the fill (order_id/count/price)
            # was never persisted — only the limit price was.
            fill = _fill_fields_from_v2_resp(resp, outcome=order.outcome)
        self._deployed_usd += order.stake_usd            # [G-daily] counts only would-place
        self._orders_today += 1                          # [G-count] same-day placed/would-place count
        self._placed[order.idempotency_key] = order      # [G-idem] key burned only on placement
        status = "DRY_RUN_would_place" if self._dry_run else "placed"
        rec = self._record(status, order, fill=fill, trigger=trigger)
        if status == "placed" and self._notify_fn is not None:
            # LIVE copy only — best-effort scannable Telegram; never breaks the trade path.
            await self._notify_live_copy(order, fill)
        return rec

    async def _notify_live_copy(self, order: ProposedKalshiOrder, fill: dict | None) -> None:
        """[Part 2] Scannable Telegram alert on a LIVE copy placement. bet_team/other_team come
        from the broker-free KXMLBGAME parser (falls back to the raw ticker for a non-two-club
        market). Uses the REAL fill count/price when present. Best-effort: any failure is logged,
        never raised (a notification must not break the placement path)."""
        try:
            from trading_corp.data.mlb_poly_kalshi_match import parse_kalshi_mlb_ticker
            pk = parse_kalshi_mlb_ticker(order.ticker or "")
            bet_team = pk.yes_name if pk else (order.ticker or "?")
            other_team = pk.other_name if pk else "?"
            f = fill or {}
            cnt = float(f.get("fill_count") or order.count)
            fp = f.get("fill_price")
            cost = (float(fp) * cnt) if fp is not None else float(order.stake_usd)
            fp_str = f"{float(fp):.2f}" if fp is not None else "?"
            head = f"⚡ {order.whale} → BUY {bet_team}"
            body = (f"vs {other_team} · {int(cnt)} @ ${fp_str} · "
                    f"${cost:.2f} · conf {float(order.confidence):.2f}")
            await self._notify_fn(f"{head}\n{body}", audit_path="poly_kalshi_copy")
        except Exception as e:  # noqa: BLE001 — notification must never break a live placement
            log.warning("poly_kalshi live-copy telegram notify failed: %s", e)

    def _record(self, status: str, order: ProposedKalshiOrder, *,
                fill: dict | None = None, trigger: dict | None = None,
                extra: dict | None = None) -> dict:
        rec = {
            # CP3: `division` scopes the dashboard OPEN query ($.division IN slugs);
            # it equals the audit actor (self._strategy) this row is logged under.
            "status": status, "division": self._strategy,
            "whale": order.whale, "whale_wallet": order.whale_wallet,
            "action": order.action, "ticker": order.ticker, "side": order.v2_side,
            "outcome": order.outcome, "count": order.count, "stake_usd": order.stake_usd,
            "order_type": _ORDER_TYPE, "tif": order.tif, "price": order.body.get("price"),
            "idempotency_key": order.idempotency_key, "reduce_only": order.reduce_only,
            "confidence": order.confidence, "dry_run": self._dry_run,
            "deployed_usd_after": self._deployed_usd, "orders_today_after": self._orders_today,
        }
        if trigger:
            # FLAG 2: the triggering Poly bet (poly_slug/poly_outcome/poly_side/
            # poly_market_type) — the "why", persisted WITH the row (was in-memory
            # shadow_log only, lost on restart) so the dashboard shows it per position.
            rec.update(trigger)
        if fill:
            # FLAG 1: the REAL fill facts (order_id/fill_count/fill_price/fill_fee)
            # are journaled IN this row now, not lost to a post-_record mutation.
            rec.update(fill)
        if extra:
            # [G-conflict] the held side/ticker that caused a skip_conflict, persisted
            # WITH the row so a future dashboard can render the conflict-skip state.
            rec.update(extra)
        self.log.append(rec)
        # [audit] durable journal row for EVERY outcome (placed / would-place / blocked_* /
        # suppressed / skip) so deployed_usd + placed-count are queryable without polling Kalshi.
        if self._logger is not None:
            try:
                self._logger.log_event(self._strategy, "poly_kalshi_order", rec)
            except Exception:  # noqa: BLE001 — audit must never break the execute path
                pass
        return rec
