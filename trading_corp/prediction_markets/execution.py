"""Prediction Markets -- the CENTRAL execution CHOKEPOINT (Stage 3 R4), DRY-RUN terminal.

ONE chokepoint every copy transits: SIGNAL (whale copy) -> SIZING/RISK (FIXED amount, ruling #1) -> 8 GATES ->
per-sub-division LOG. This rung's terminal step COMPUTES the exact Kalshi V2 body + idempotency key and LOGS it to
pm_subdivision_order (dry_run=1). It is STRUCTURALLY UNABLE TO PLACE: it imports ONLY kalshi_live's PURE builders
(`build_v2_event_order` / `client_order_id` / `usd_to_contracts`); it never imports the broker, holds no broker
object, takes no broker argument, has no async and no network. There is no code path from here to an order. (R7 will
WRAP `kalshi_live.place_order` VERBATIM -- not reimplement it -- and hand it the SAME gate-approved body this rung
computes, so the dry-run body is byte-parity with what would be sent.)

GATE ORDER (a reject at any gate returns early -- NO budget consumed, NO idempotency key registered):
  1. disarm  (global then per-sub-division; a read error means DISARMED; ABSENT state means DISARMED). The arming
     MECHANISM is R5; R4 wires only the CHECK. In DRY-RUN the disarm verdict is RECORDED (always disarmed at R4) but
     does NOT stop body computation -- a dry-run exists to preview the exact body; live placement (R7) is what
     disarm blocks.
  2. per-order notional cap on USD (count * OUTCOME-LEG cost, NOT a raw contract count). (2a) fixed stake <= cap,
     checked BEFORE the match (cheap config sanity); (2b) count*leg_cost <= cap AFTER sizing -- a per-order USD
     ceiling on the money actually committed. (base_price is the Kalshi market's leg ASK -- never the whale's
     Polymarket price -- and the IOC limit bounds the fill, so a mis-sized order self-limits; 2b is the USD ceiling,
     NOT a substitute for a leg-ask-vs-Poly-price plausibility gate, which is a later refinement, not this rung.)
  3. strike-exists-EXACT + liquid  (R2 `match_bet`; never round to a neighbour; `liquidity_ok` at match time). The
     leg PRICE gate 2b needs comes from the matched market, so 3 is evaluated between 2a and 2b (data dependency).
  4. idempotency  (deterministic UUID5 over (sub_division, WALLET, ticker, outcome-leg, STABLE signal_id) -- WALLET
     not display name; signal_id stable across restarts). Checked against the DURABLE journal (already placed -> skip).
  5. per-sub-division daily cap  (in-memory USD counter).
  6. per-account exposure cap    (in-memory USD counter, across sub-divisions on one account).
  7. slippage within cap  (clamped INSIDE build_v2_event_order; fail-closed here if the leg has no quote).
  8. count ceiling        (in-memory per-day order count).
  ** EVERY CAP IS AN O(1) IN-MEMORY COUNTER, seeded ONCE at construction from a BOUNDED, indexed journal query
     (today's placed rows for THIS sub-division) -- NEVER a scan on the order path. The engine's RiskAgent aggregate
     caps were removed after a synchronous ~1.19M-row audit_event scan froze the event loop; do not reintroduce it. **

WHALE-EXIT = Option D (Jack RULED): /activity SELL as trigger, /positions size-reduction as confirmation.
`detect_exit_signals` emits an exit CopySignal ONLY when BOTH agree within the window; if they do NOT co-occur, NO
exit is emitted -- a MISSED exit, the accepted failure direction (bias-down). There is NO single-signal fallback. An
exit is an order and transits the SAME chokepoint with reduce_only.
"""
from __future__ import annotations

import calendar
import hashlib
import logging
import time
from dataclasses import dataclass

# ONLY the PURE builders -- NOT KalshiLiveBroker, NOT place_order. This import line IS the structural guarantee.
from ..brokers.kalshi_live import (build_v2_event_order, client_order_id as _kalshi_coid,
                                   usd_to_contracts, v2_side_and_price)
from ..data import mlb_poly_kalshi_match as M
from . import arm   # R5 arm/kill control plane -- stdlib-only at import (its engine writer is lazy)

_LOG = logging.getLogger(__name__)

# Config DEFAULTS live in CODE (Jack ruled: config values in code, not migrations; a DDL-NULL cap falls back here).
CONFIG_DEFAULTS = {
    "fixed_stake_usd": 5.0,
    "per_order_usd_cap": 25.0,
    "daily_usd_cap": 50.0,
    "max_open_usd": 100.0,
    "max_orders_per_day": 25,
    "max_slippage_cents": 2,
    # ★ gate-3 liquidity floor = liquidity_ratio * THIS order's notional (Jack RULED 2026-08-29). THE SINGLE SOURCE
    # of the 0.75 default: a NULL `liquidity_ratio` column (migration 012) reads as 0.75 here. Operators change the
    # live floor by editing the pm_subdivision.liquidity_ratio NUMBER (read per cycle -- NO restart), NOT this code.
    "liquidity_ratio": 0.75,
}
_ENTRY_TIF = "immediate_or_cancel"   # marketable copy (IOC); exits use the same TIF + reduce_only


# ── inputs ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CopySignal:
    wallet: str               # the whale's WALLET (never a display name -- feeds idempotency)
    slug: str                 # Polymarket slug (the matcher's market-type discriminator)
    outcome: str              # the whale's Polymarket outcome (team / Over / Under)
    condition_id: str
    outcome_index: int
    signal_id: str            # STABLE across restarts: the whale's entry tx_hash (canonical) or a position-id hash
    is_exit: bool = False     # entry vs exit (Option D). An exit -> reduce_only on the SAME leg.


@dataclass(frozen=True)
class SubConfig:
    account_id: str
    category: str
    market_types: tuple       # e.g. ("moneyline","total","spread") -- R2 filter
    sizing_mode: str          # 'fixed' (R4); 'kelly' shape carried, NOT built
    fixed_stake_usd: float
    per_order_usd_cap: float
    daily_usd_cap: float
    max_open_usd: float
    max_orders_per_day: int
    max_slippage_cents: int
    # gate-3 liquidity floor multiplier: required book depth = liquidity_ratio * THIS order's notional (config, per
    # sub-division, read per cycle). The dataclass default MIRRORS CONFIG_DEFAULTS['liquidity_ratio'] and exists ONLY
    # for direct construction (tests); PRODUCTION always sets it explicitly via sub_config_from_row (DB value or the
    # CONFIG_DEFAULTS fallback), so the two 0.75s never diverge on the live path.
    liquidity_ratio: float = 0.75

    @property
    def division(self) -> str:
        return "%s:%s" % (self.account_id, self.category)


@dataclass(frozen=True)
class MarketContext:
    moneyline_index: dict
    total_index: dict
    spread_index: dict
    kalshi_dates: frozenset
    markets: dict             # ticker -> market dict (yes_ask_dollars/no_ask_dollars/liquidity_dollars/...)


@dataclass(frozen=True)
class Decision:
    status: str               # 'dry_run_would_place' | 'skip:<gate>' | 'reject:<gate>'
    signal_id: str
    market_type: str | None = None
    kalshi_ticker: str | None = None
    leg: str | None = None            # 'yes' | 'no' -- carried from R2, NEVER re-derived
    body: dict | None = None          # the exact Kalshi V2 body (byte-parity with place_order's)
    client_order_id: str | None = None
    count: int | None = None
    price: float | None = None        # yes-side limit price in the body
    notional_usd: float | None = None
    is_exit: bool = False
    disarm_armed: bool = False        # the recorded disarm verdict (R4: always False)
    reason: str | None = None


# ── helpers ─────────────────────────────────────────────────────────────────
def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _row_get(row, key):
    try:
        v = row[key]
    except (KeyError, IndexError):
        return None
    return v


def _safe_liquidity_ratio(row) -> float:
    """The gate-3 liquidity ratio, FAIL-SAFE + LOUD (Jack ruled 2026-08-29). A liquidity floor whose ratio is
    <= 0 or NaN would SILENTLY STOP GATING (required_depth <= 0 -> any book passes, no skip:illiquid surfaced)
    -- the same 'present, wired, doing nothing' failure the R7.d dead latch_count_ceiling had. So: a NULL falls
    back to the code default (0.75, the ruling); a PRESENT non-positive-or-NaN value is CLAMPED to the default
    AND LOGGED at WARNING (NEVER clamp silently -- a deliberate 0 must learn it was overridden, not discover it
    later from behaviour). A valid positive ratio passes through unchanged."""
    default = float(CONFIG_DEFAULTS["liquidity_ratio"])
    v = _row_get(row, "liquidity_ratio")
    if v is None:
        return default                                   # NULL column -> code default (the R7.f NULL ruling)
    try:
        r = float(v)
    except (TypeError, ValueError):
        _LOG.warning("pm liquidity_ratio: UNPARSEABLE value %r -> CLAMPED to default %.4f (a bad liquidity floor "
                     "would silently stop gating -- any book would pass)", v, default)
        return default
    if not (r > 0.0):                                    # catches <= 0 AND NaN (NaN > 0.0 is False)
        _LOG.warning("pm liquidity_ratio: INVALID %r (non-positive or NaN) -> CLAMPED to default %.4f. A ratio "
                     "<= 0 makes required_depth <= 0 so the gate-3 depth floor SILENTLY STOPS GATING (any book "
                     "passes). If this was deliberate it was OVERRIDDEN -- set a positive ratio.", r, default)
        return default
    return r


def sub_config_from_row(row) -> SubConfig:
    """Build a SubConfig from a pm_subdivision row, falling back to CONFIG_DEFAULTS for any NULL cap (Jack: config
    values in code, DDL-NULL -> code default; no config WRITE). liquidity_ratio additionally FAIL-SAFE-clamps a
    present non-positive/NaN value to the default + LOGS it (a bad floor silently stops gating -- see below)."""
    def cap(k):
        v = _row_get(row, k)
        return v if v is not None else CONFIG_DEFAULTS[k]
    mt = str(_row_get(row, "market_types") or "").strip()
    types = tuple(t for t in (s.strip() for s in mt.split(",")) if t) or M.COPYABLE_MARKET_TYPES
    return SubConfig(
        account_id=_row_get(row, "account_id"), category=_row_get(row, "category"),
        market_types=types, sizing_mode=(_row_get(row, "sizing_mode") or "fixed"),
        fixed_stake_usd=float(cap("fixed_stake_usd")), per_order_usd_cap=float(cap("per_order_usd_cap")),
        daily_usd_cap=float(cap("daily_usd_cap")), max_open_usd=float(cap("max_open_usd")),
        max_orders_per_day=int(cap("max_orders_per_day")), max_slippage_cents=int(cap("max_slippage_cents")),
        liquidity_ratio=_safe_liquidity_ratio(row),   # NULL -> 0.75; non-positive/NaN -> CLAMPED to 0.75 + LOUD log; read PER CYCLE
    )


def stable_signal_id(wallet: str, condition_id: str, outcome_index, entry_key) -> str:
    """A restart-STABLE signal id from the whale-position identity (the /positions proxy for the canonical entry
    tx_hash). NOT a per-process counter. `entry_key` = the whale's tx_hash if available, else the stored
    entry_observed_ts (stable once observed)."""
    raw = "%s|%s|%s|%s" % ((wallet or "").lower(), condition_id or "", outcome_index, entry_key)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _utc_day_start(now_ts: int) -> int:
    g = time.gmtime(int(now_ts))
    return calendar.timegm((g.tm_year, g.tm_mon, g.tm_mday, 0, 0, 0, 0, 0, 0))


def is_armed(conn, account_id: str, category: str, *, legacy_db_path=None) -> bool:
    """The disarm CHECK -- R5 makes it REAL. ARMED only if BOTH the global master AND this sub-division's
    row are armed; ABSENT or UNREADABLE arm state means DISARMED (fail-safe OFF, the money-gate default,
    inverted from the engine's strategy-halt default). Arm state lives in the LEGACY agent_state
    (`arm.read_arm_verdict`, mode=ro -- the rosters read-only precedent), NOT in the PM DB, so `conn` (the
    PM connection) is unused here and kept only for call-site stability. See arm.py for why this bridge
    does not collapse PM-DB isolation."""
    return arm.is_armed(account_id, category, legacy_db_path=legacy_db_path)


def _leg_ask(market: dict, leg: str):
    """The per-contract ASK price of OUR leg: yes_ask for 'yes', no_ask for 'no'. This is the `base_price` fed to
    build_v2_event_order (which converts a 'no' leg to its YES-side equivalent internally). None if the quote is
    missing / out of band -> fail-closed (gate 7)."""
    if not isinstance(market, dict):
        return None
    try:
        v = market.get("yes_ask_dollars") if leg == "yes" else market.get("no_ask_dollars")
        p = float(v)
        return p if 0.0 < p < 1.0 else None
    except (TypeError, ValueError):
        return None


# ── the durable journal + O(1) counters (seeded ONCE from a BOUNDED indexed query) ───────────────────────────────
class Journal:
    """Durable-backed, O(1)-on-the-order-path budget. At CONSTRUCTION it seeds the daily + open + count counters
    from a BOUNDED indexed query over ONLY today's PLACED (dry_run=0, filled) rows for the given account(s); every
    per-order check after that is an in-memory dict lookup + increment. It also answers idempotency (has this
    client_order_id already been PLACED?). NEVER scans the whole table on the order path."""

    def __init__(self, conn, account_ids, now_ts: int):
        self._day0 = _utc_day_start(now_ts)
        self._daily_usd: dict = {}
        self._open_usd: dict = {}
        self._orders_today: dict = {}
        self._placed_coids: set = set()
        if _table_exists(conn, "pm_subdivision_order"):
            for aid in set(account_ids):
                for r in conn.execute(
                    # ★ LEG-AWARE seed (the $163.84 lens, applied to the RESTART path): the committed cash of
                    # a NO leg is count*(1 - yes_side_price), NOT count*price. submitted_price stores the
                    # yes-side limit; a NO row must seed at (1 - submitted_price) so the daily/open counters
                    # re-seed at the SAME basis commit_would_place uses at runtime (else NO-leg exposure
                    # under-seeds on restart -> a money-gate bypass -- the R4 caps bug, in the seed query).
                    "SELECT category, COUNT(*) n, COALESCE(SUM(CASE WHEN outcome_leg='yes' "
                    "  THEN submitted_count*submitted_price ELSE submitted_count*(1.0-submitted_price) END),0) usd "
                    "FROM pm_subdivision_order "
                    "WHERE account_id=? AND dry_run=0 AND outcome_status='filled' AND response_ts>=? "
                    "GROUP BY category", (aid, self._day0)).fetchall():
                    self._daily_usd[(aid, r["category"])] = float(r["usd"] or 0.0)
                    self._orders_today[(aid, r["category"])] = int(r["n"] or 0)
                    self._open_usd[aid] = self._open_usd.get(aid, 0.0) + float(r["usd"] or 0.0)
                for r in conn.execute(
                    "SELECT client_order_id FROM pm_subdivision_order "
                    "WHERE account_id=? AND dry_run=0 AND client_order_id IS NOT NULL", (aid,)).fetchall():
                    self._placed_coids.add(r["client_order_id"])

    def already_placed(self, coid): return coid in self._placed_coids
    def daily_usd(self, aid, cat): return self._daily_usd.get((aid, cat), 0.0)
    def open_usd(self, aid): return self._open_usd.get(aid, 0.0)
    def orders_today(self, aid, cat): return self._orders_today.get((aid, cat), 0)

    def commit_would_place(self, aid, cat, usd: float) -> None:
        """Accumulate a would-place against the in-memory counters so LATER signals in the same run are gated by the
        caps (a dry-run of many copies must trip the daily/exposure/count ceilings). O(1)."""
        self._daily_usd[(aid, cat)] = self.daily_usd(aid, cat) + usd
        self._open_usd[aid] = self.open_usd(aid) + usd
        self._orders_today[(aid, cat)] = self.orders_today(aid, cat) + 1


# ── THE CHOKEPOINT ──────────────────────────────────────────────────────────
def evaluate(signal: CopySignal, sub: SubConfig, ctx: MarketContext, journal: Journal, conn, now_ts: int,
             *, shard_balances=None, legacy_db_path=None) -> Decision:
    """One copy signal through the gates in order. DRY-RUN: COMPUTES the exact V2 body if gates pass and RECORDS
    the disarm verdict; it NEVER places (this module holds no broker). A reject at any gate returns early. The
    disarm verdict is a PREVIEW here -- the LIVE placement gate on `armed` lives in `run_arm_gated_cycle` (R5).

    `shard_balances` (a shard_balance.ShardBalances, or None): the PER-CYCLE per-shard funding read for gate 6b, the
    pre-flight shard-funding SKIP (Jack RULED 2026-08-30). ★ None DISABLES gate 6b -- ONLY the paper / dry-run / test
    path may pass None; the LIVE driver ALWAYS passes a ShardBalances (real, or an `has_breakdown=False` UNKNOWN on a
    balance-fetch failure), so on the live path gate 6b is always active and FAILS CLOSED. The value that makes gate
    6b pass everything is `shard_balances=None`; it is unreachable on the live path by construction (see
    live_driver.scheduled_pm_live_loop) -- the standing "a safety check that silently stops checking" guard."""
    sid = signal.signal_id
    armed = is_armed(conn, sub.account_id, sub.category, legacy_db_path=legacy_db_path)   # gate 1 (recorded)
    copy_usd = float(sub.fixed_stake_usd)

    if copy_usd > sub.per_order_usd_cap + 1e-9:                                       # gate 2a (config sanity)
        return Decision("reject:per_order_cap", sid, disarm_armed=armed, is_exit=signal.is_exit,
                        reason="fixed_stake_%.4f_gt_cap_%.4f" % (copy_usd, sub.per_order_usd_cap))

    parsed = M.parse_poly_mlb_bet(signal.slug, signal.outcome)                        # gate 3 (match, exact strike)
    match = M.match_bet(parsed, ctx.moneyline_index, ctx.total_index, ctx.spread_index, ctx.kalshi_dates,
                        allowed_market_types=sub.market_types)
    if match.status != "matched":
        return Decision("skip:%s" % match.status, sid, market_type=match.market_type, reason=match.reason,
                        is_exit=signal.is_exit, disarm_armed=armed)
    ticker, leg = match.kalshi_ticker, match.leg
    market = ctx.markets.get(ticker)
    base_price = _leg_ask(market, leg)                                               # gate 7 precondition (quote)
    if base_price is None:
        return Decision("skip:no_quote", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                        reason="no_two_sided_quote_for_leg", is_exit=signal.is_exit, disarm_armed=armed)
    # ★ Compute THIS order's own notional BEFORE the liquidity gate: the gate-3 floor is a RATIO of it (Jack RULED
    # 2026-08-29), so the depth required scales with the order actually being placed, not a fixed $. (Moved up from
    # below the liquidity gate; the caps that follow reuse the same values -- pure reordering, no double-compute.)
    count = usd_to_contracts(copy_usd, base_price)
    _side, limit_price = v2_side_and_price(outcome=leg, is_buy=(not signal.is_exit),
                                           base_price=base_price, max_slippage_cents=sub.max_slippage_cents)
    # ★ OUTCOME-LEG committed cash per contract (the $163.84 fix / the NO-leg lens -- bitten 6x): a NO leg costs
    # (1 - yes_side_price), NOT the yes-side price. EVERY USD gate -- the caps AND now the liquidity floor -- gates
    # on THIS, the money the account actually commits on THIS leg.
    leg_cost = limit_price if leg == "yes" else (1.0 - limit_price)
    notional = count * leg_cost
    # gate 3 (liquid): required book depth = liquidity_ratio * THIS order's notional (config, per sub-division, READ
    # PER CYCLE; NOT a fixed $, NOT per_order_usd_cap -- decoupled: the cap limits what we SPEND, the floor checks the
    # book can SERVE it). ** CONSEQUENCE (Jack ruled knowingly): at ratio < 1.0 the book may not FULLY fill the order
    # -- a partial fill / walking to the next price level is possible. At 1 contract that is fill-or-nothing
    # (meaningless); at larger sizes the residual price movement is bounded SEPARATELY by the slippage cap (gate 7,
    # clamped inside build_v2_event_order). This floor is a "can the book serve most of it" check, NOT a full-fill
    # guarantee. ** notional is leg-correct (above), so a NO-leg floor scales with (1-yes_price), never the yes side.
    required_depth = sub.liquidity_ratio * notional
    if not M.liquidity_ok(market, min_liquidity_usd=required_depth,                   # gate 3 (liquid) -- ratio x notional
                          max_spread_cents=max(1, sub.max_slippage_cents * 2)):
        return Decision("skip:illiquid", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                        count=count, notional_usd=notional, is_exit=signal.is_exit, disarm_armed=armed,
                        reason="liquidity_floor:need_%.4f=ratio_%.2f*notional_%.4f" % (required_depth, sub.liquidity_ratio, notional))
    if notional > sub.per_order_usd_cap + 1e-9:                                       # gate 2b (per-order USD ceiling)
        return Decision("reject:per_order_cap", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                        count=count, notional_usd=notional, is_exit=signal.is_exit, disarm_armed=armed,
                        reason="notional_%.4f_gt_cap_%.4f" % (notional, sub.per_order_usd_cap))

    coid = _kalshi_coid(sub.division, signal.wallet, ticker, leg, sid)                # gate 4 (idempotency)
    if journal.already_placed(coid):
        return Decision("skip:duplicate", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                        client_order_id=coid, reason="already_placed", is_exit=signal.is_exit, disarm_armed=armed)

    if not signal.is_exit:   # gates 5/6/8 are ENTRY caps -- a reduce_only EXIT reduces risk and must NEVER be capped
        if journal.daily_usd(sub.account_id, sub.category) + notional > sub.daily_usd_cap + 1e-9:   # gate 5
            return Decision("reject:daily_cap", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                            count=count, notional_usd=notional, reason="daily_cap", disarm_armed=armed)
        if journal.open_usd(sub.account_id) + notional > sub.max_open_usd + 1e-9:                   # gate 6
            return Decision("reject:exposure_cap", sid, market_type=match.market_type, kalshi_ticker=ticker,
                            leg=leg, count=count, notional_usd=notional, reason="exposure_cap", disarm_armed=armed)
        if journal.orders_today(sub.account_id, sub.category) + 1 > sub.max_orders_per_day:         # gate 8
            return Decision("reject:count_ceiling", sid, market_type=match.market_type, kalshi_ticker=ticker,
                            leg=leg, reason="orders_per_day", disarm_armed=armed)
        # gate 6b (PER-MARKET shard funding): Kalshi shards collateral by exchange_index and orders auto-route to the
        # market's shard, charging THAT shard -- a healthy TOTAL with an empty market-shard is Karen's silent death.
        # ★ PER MARKET, not per category: live markets never migrate, so an MLB market's shard depends on its creation
        # date -> read THIS market's exchange_index (not the series/category). This is a SKIP (a funding gap is
        # FUNDABLE-LATER, not a fault -- it must NOT feed the error-latch), and it FAILS CLOSED: an unknown market
        # shard (exchange_index None) or an unknown split (has_breakdown False -> can_fund None) or a too-thin shard
        # (can_fund False) all SKIP. Only can_fund True proceeds. None shard_balances disables the gate (test/paper
        # opt-out; unreachable on the live path -- see the evaluate docstring).
        if shard_balances is not None:
            order_shard = (market or {}).get("exchange_index")
            fundable = shard_balances.can_fund(order_shard, notional) if order_shard is not None else None
            if fundable is not True:
                return Decision("skip:shard_underfunded", sid, market_type=match.market_type, kalshi_ticker=ticker,
                                leg=leg, count=count, notional_usd=notional, is_exit=signal.is_exit, disarm_armed=armed,
                                reason="shard_%s underfunded for notional_%.4f (fundable=%r; per-market, fail-closed)"
                                       % (order_shard, notional, fundable))

    # all gates pass -> COMPUTE the exact V2 body (gate 7 slippage clamped INSIDE build_v2_event_order). DRY-RUN:
    # log-not-place; an exit is reduce_only (is_buy=False on the SAME leg).
    body, cnt, price = build_v2_event_order(
        ticker=ticker, outcome=leg, is_buy=(not signal.is_exit), base_price=base_price, copy_usd=copy_usd,
        max_slippage_cents=sub.max_slippage_cents, tif=_ENTRY_TIF, client_order_id=coid)
    if not signal.is_exit:                       # only ENTRIES consume the entry budget; a reduce_only exit does not
        journal.commit_would_place(sub.account_id, sub.category, notional)
    return Decision("dry_run_would_place", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                    body=body, client_order_id=coid, count=cnt, price=price, notional_usd=notional,
                    is_exit=signal.is_exit, disarm_armed=armed,
                    reason=("exit_reduce_only" if signal.is_exit else "entry"))


# ── the DRY-RUN runner -- writes dry_run log rows; ZERO POSTs (no broker exists here) ────────────────────────────
def dry_run_subdivision(conn, sub: SubConfig, signals, ctx: MarketContext, now_ts: int) -> dict:
    """Run every signal through the chokepoint in DRY-RUN and LOG each would-place to pm_subdivision_order
    (dry_run=1). Returns a summary whose `posts_sent` is ALWAYS 0 -- this module holds no broker. Requires
    pm_subdivision_order (migration 010): the caller runs it against a schema-10 DB (a COPY at box-scratch)."""
    signals = list(signals)
    journal = Journal(conn, [sub.account_id], now_ts)
    decisions, n_would, n_skip, n_reject = [], 0, 0, 0
    for s in signals:
        d = evaluate(s, sub, ctx, journal, conn, now_ts)
        decisions.append(d)
        if d.status == "dry_run_would_place":
            n_would += 1
            conn.execute(
                "INSERT INTO pm_subdivision_order "
                "(account_id, category, wallet, condition_id, outcome_index, signal_id, client_order_id, ticker, "
                " order_side, outcome_leg, is_exit, submitted_count, submitted_price, time_in_force, outcome_status, "
                " dry_run, submitted_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                (sub.account_id, sub.category, s.wallet, s.condition_id, s.outcome_index, s.signal_id,
                 d.client_order_id, d.kalshi_ticker, d.body.get("side"), d.leg, 1 if s.is_exit else 0,
                 d.count, d.price, d.body.get("time_in_force"), None, int(now_ts)))   # outcome_status NULL: nothing was placed (dry_run=1 marks it)
        elif d.status.startswith("skip:"):
            n_skip += 1
        else:
            n_reject += 1
    if hasattr(conn, "commit"):
        conn.commit()
    return {"account_id": sub.account_id, "category": sub.category, "n_signals": len(signals),
            "n_would_place": n_would, "n_skip": n_skip, "n_reject": n_reject,
            "posts_sent": 0, "decisions": decisions}


# ── whale-exit signal detection -- Option D (BOTH signals must agree; else MISSED, no fallback) ──────────────────
def detect_exit_signals(activity_sells, position_reductions, *, window_sec: int) -> list:
    """Emit an exit CopySignal ONLY where an /activity SELL and a /positions size-reduction for the SAME leg agree
    within `window_sec`. If the two do NOT co-occur -> NO exit (a MISSED exit, the accepted failure direction per
    Jack's Option-D ruling). There is NO single-signal fallback.

    `activity_sells`: dicts {wallet, condition_id, outcome_index, ts, tx_hash}.
    `position_reductions`: dicts {wallet, condition_id, outcome_index, ts, slug, outcome}."""
    reds: dict = {}
    for r in position_reductions:
        reds.setdefault((str(r["wallet"]).lower(), r["condition_id"], r["outcome_index"]), []).append(r)
    out = []
    for a in activity_sells:
        key = (str(a["wallet"]).lower(), a["condition_id"], a["outcome_index"])
        conf = next((r for r in reds.get(key, []) if abs(int(r["ts"]) - int(a["ts"])) <= int(window_sec)), None)
        if conf is None:
            continue        # SELL not confirmed by a /positions reduction in-window -> MISSED exit (no fallback)
        out.append(CopySignal(
            wallet=a["wallet"], slug=conf.get("slug", ""), outcome=conf.get("outcome", ""),
            condition_id=a["condition_id"], outcome_index=a["outcome_index"],
            signal_id=stable_signal_id(a["wallet"], a["condition_id"], a["outcome_index"], a.get("tx_hash") or a["ts"]),
            is_exit=True))
    return out


# ── R5: the ARM-GATED live cycle -- the ONE seam through which a real order can ever leave ────────────
def run_arm_gated_cycle(conn, sub: SubConfig, signals, ctx: MarketContext, now_ts: int, *,
                        place_fn=None, legacy_db_path=None) -> dict:
    """Run every signal through the chokepoint, then place a gate-passing order ONLY IF ARMED -- by calling
    the CALLER-INJECTED `place_fn(decision)`. This is the whole point of R5: the mechanism that gates the
    R7 first live order.

      * The arm state is RE-READ immediately before EACH placement (`arm.read_arm_verdict`), so a mid-cycle
        kill stops the very NEXT order (the residual is one order wide -- see below).
      * DISARM blocks EVERYTHING -- entries AND exits (off is off). The exit-EXEMPT budget gates (5/6/8)
        answer a DIFFERENT question ("may a daily cap strand an exit?" -> no); the OFF switch is not a
        budget -- when disarmed, the human flattens open positions by hand on Kalshi (the auth-failure
        latch already mandates that manual-exit path). A future 'entries_only' soft-disarm is a deferred,
        rulable SEAM -- NOT built here (one behaviour, fully tested).
      * `place_fn` is INJECTED by the caller (R7 wraps `kalshi_live.place_order`); this module imports NO
        broker, so the structural "cannot place" guarantee is intact -- the ONLY way an order leaves is a
        caller-supplied callable that this arm gate guards. `place_fn=None` -> pure dry-run preview
        (identical to R4: computes + logs the body, never reaches a placement). R5 proves the mechanism
        with a STUB place_fn (counts calls, posts nothing); no real POST occurs in R5.

    RESIDUAL (Jack's point 6, stated honestly): a kill landing AFTER this re-read but BEFORE the caller's
    POST is ONE order wide. R7's actual POST site should do a SECOND arm re-check immediately before
    place_order to shrink it to near-zero; the remainder is irreducible without a broker-side pre-commit.

    Returns a summary: n_would_place (gate-passing), placements_attempted (place_fn calls), n_disarm_blocked
    (gate-passing but disarmed), n_skip, n_reject, posts_sent (ALWAYS 0 here -- no broker exists)."""
    signals = list(signals)
    journal = Journal(conn, [sub.account_id], now_ts)
    decisions = []
    n_would = n_skip = n_reject = placements = disarm_blocked = 0
    for s in signals:
        d = evaluate(s, sub, ctx, journal, conn, now_ts, legacy_db_path=legacy_db_path)
        decisions.append(d)
        if d.status == "dry_run_would_place":
            n_would += 1
            # RE-READ armed right before placing -> honours a mid-cycle kill for the NEXT order.
            armed_now = arm.read_arm_verdict(sub.account_id, sub.category, legacy_db_path=legacy_db_path).armed
            if not armed_now:
                disarm_blocked += 1            # DISARMED -> nothing leaves, entries AND exits (off is off)
            elif place_fn is not None:
                place_fn(d)                    # armed + the ONE seam; R7 supplies the real (arm-guarded) placer
                placements += 1
            # else: armed but no placer -> pure dry-run PREVIEW (neither placed nor disarm-blocked)
        elif d.status.startswith("skip:"):
            n_skip += 1
        else:
            n_reject += 1
    return {"account_id": sub.account_id, "category": sub.category, "n_signals": len(signals),
            "n_would_place": n_would, "placements_attempted": placements, "n_disarm_blocked": disarm_blocked,
            "n_skip": n_skip, "n_reject": n_reject, "posts_sent": 0, "decisions": decisions}


# ── R5: the manual-exit FLAG surface (the auth-failure latch's companion) ────────────────────────────
def open_positions_needing_manual_exit(conn) -> list:
    """PM-DB read (read-only): real (dry_run=0) FILLED entry legs whose filled EXIT count does not yet
    cover them -> net-open. On a 401/403 auth failure the account is auto-disarmed (`arm.latch_auth_failure`)
    and these must be flattened BY HAND on Kalshi -- the engine can no longer place the reduce_only exit.
    This is the FLAG surface (a pm_web display / operator read); it is NOT the position source of truth --
    that is boot-reconcile against Kalshi's portfolio (its own pre-R7 rung). Returns [] when no live order
    has ever been placed (R5: always [])."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return []
    rows = conn.execute(
        "SELECT account_id, category, ticker, outcome_leg, "
        "  SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE 0 END) AS entered, "
        "  SUM(CASE WHEN is_exit=1 THEN COALESCE(fill_count,0) ELSE 0 END) AS exited "
        "FROM pm_subdivision_order "
        "WHERE dry_run=0 AND outcome_status='filled' AND ticker IS NOT NULL "
        "GROUP BY account_id, category, ticker, outcome_leg").fetchall()
    out = []
    for r in rows:
        net = float(r["entered"] or 0) - float(r["exited"] or 0)
        if net > 1e-9:
            out.append({"account_id": r["account_id"], "category": r["category"], "ticker": r["ticker"],
                        "outcome_leg": r["outcome_leg"], "net_open_contracts": net})
    return out
