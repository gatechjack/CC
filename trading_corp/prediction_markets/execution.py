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
from ..data import ufc_poly_kalshi_match as U   # B2: the UFC matcher (category dispatch below); pure/stdlib like M
from . import arm   # R5 arm/kill control plane -- stdlib-only at import (its engine writer is lazy)

_LOG = logging.getLogger(__name__)

# Config DEFAULTS live in CODE (Jack ruled: config values in code, not migrations; a DDL-NULL cap falls back here).
CONFIG_DEFAULTS = {
    "fixed_stake_usd": 5.0,
    # flat-contracts sizing (sizing_mode='contracts'): a FLAT whole-contract count per copy. The DDL default is the
    # ruled 5 (migration 014, NOT NULL DEFAULT 5); this is the code fallback if the column is ever NULL. Read per
    # cycle -- change the number, no restart. NOT the fixed_stake_usd-floors-to-1 hack.
    "contracts": 5,
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

# ★ C (2026-09-03, Jack RULED): the ACCOUNT-LEVEL AGGREGATE caps. Adding a category must NOT silently grow an account's
# total exposure, so the account total STAYS $150/day + 50 orders ACROSS ALL its categories (NOT per-category, which
# would double to $300/100). Config-in-code (Jack's rule: config values in code, not migrations). Gate 5b/8b in
# evaluate enforce these on the shared account-keyed Journal, race-free under Option C. BYTE-IDENTICAL with one category
# at the ruled $150/50 (the account cap == the per-category cap, and gate 5/8 fire first).
ACCOUNT_DAILY_USD_CAP = 150.0
ACCOUNT_MAX_ORDERS_PER_DAY = 50


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
    close_source: str | None = None  # None=whale-exit | 'opposed'=cancellation-by-disagreement (two whales disagree
                                     # -> market off the books). BOTH close PER-WALLET; the opposed flatten emits one
                                     # per holding whale (detect_opposing_closes), summing to the full account flatten.


@dataclass(frozen=True)
class SubConfig:
    account_id: str
    category: str
    market_types: tuple       # e.g. ("moneyline","total","spread") -- R2 filter
    sizing_mode: str          # 'fixed' (flat DOLLARS, legacy); 'contracts' (flat CONTRACTS, R8); 'kelly' shape carried, NOT built
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
    # flat-contracts count (sizing_mode='contracts'): the whole-contract count per copy, read per cycle from
    # pm_subdivision.contracts (migration 014, DDL default 5). Defaulted here for direct construction (tests);
    # production sets it via sub_config_from_row. Ignored when sizing_mode != 'contracts'.
    contracts: int = 5

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
    # B2 (2026-09-03): the UFC fight+distance index (KalshiFight by (date_iso, fighter-pair)). Optional + defaulted so
    # the MLB construction MarketContext(ml, tot, spr, dates, markets) is BYTE-IDENTICAL (fight_index stays None); the
    # ufc ctx builder sets fight_index and leaves ml/tot/spr empty. Read only by the ufc matcher adapter below.
    fight_index: dict | None = None


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
        fixed_stake_usd=float(cap("fixed_stake_usd")), contracts=int(cap("contracts")),
        per_order_usd_cap=float(cap("per_order_usd_cap")),
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


def _leg_exit_bid(market: dict, leg: str):
    """The per-contract BID of OUR leg -- the price we can SELL into IMMEDIATELY (a marketable reduce_only IOC
    EXIT). ENTRIES price to BUY at the ASK (`_leg_ask`); EXITS must price to SELL at the BID or the IOC never
    crosses. ★ The shipped exit path fed `_leg_ask` for BOTH directions, so an exit's limit was ask-slip -- which
    fills ONLY when spread <= slip (2c); on any wider book the reduce_only IOC 0-fills = a MISSED EXIT, defeating
    Option D's purpose (the whole point is to GET OUT). This returns the leg BID so the exit crosses:
      * YES leg  -> `yes_bid` (sell YES: side=ask at yes_bid - slip, crosses the bid).
      * NO leg   -> the NO bid = (1 - `yes_ask`) (selling NO = buying YES at the ask; YES-centric, and it uses
                    yes_ask -- always populated on the live path -- rather than a possibly-absent no_bid field).
    None (no bid to sell into) -> fail-closed skip:no_quote. Out-of-band prices -> None (same guard as _leg_ask)."""
    if not isinstance(market, dict):
        return None
    try:
        if leg == "yes":
            v = market.get("yes_bid_dollars")
            p = float(v)
        else:
            ya = market.get("yes_ask_dollars")
            p = 1.0 - float(ya)                       # NO bid = 1 - YES ask (sell NO = buy YES at the ask)
        return p if 0.0 < p < 1.0 else None
    except (TypeError, ValueError):
        return None


def _top_of_book_depth_usd(market: dict, leg: str) -> float:
    """The LEG-CORRECT top-of-book $ depth available to FILL our BUY, from Kalshi top-of-book SIZE (contracts) x
    price (dollars). ★ `liquidity_dollars` is a DEPRECATED always-'0.0000' Kalshi stub (docs 2026-08-30), so depth
    comes from `yes_bid_size_fp` / `yes_ask_size_fp` (merged from the RAW payload; the SDK object drops them). LEG
    LENS: buying a YES leg lifts the ASK -> `yes_ask_size` contracts at `yes_ask`; buying a NO leg is a YES SELL, so
    it lifts the YES BIDS -> `yes_bid_size` contracts at the NO price (1 - yes_bid). ★ UNITS: `*_size_fp` is a
    CONTRACT COUNT (string like '4.00'); the gate-3 floor is a DOLLAR amount -> $depth = size * price. Missing / bad
    size -> 0.0 -> gate 3 FAILS CLOSED (skip). Multi-level depth above the touch is backlog (C) (/orderbook)."""
    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    m = market or {}
    if leg == "no":
        size = f(m.get("yes_bid_size_fp")); price = 1.0 - f(m.get("yes_bid_dollars"))   # buy NO = lift the YES bids
    else:
        size = f(m.get("yes_ask_size_fp")); price = f(m.get("yes_ask_dollars"))         # buy YES = lift the ask
    if size <= 0.0 or not (0.0 < price < 1.0):
        return 0.0
    return size * price


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
                    # ★ ENTRIES ONLY (is_exit=0): the runtime accumulator commit_would_place fires ONLY for entries
                    # (execution.evaluate: `if not signal.is_exit: journal.commit_would_place`), so the RESTART seed
                    # must match that basis -- else the counters re-seed on a DIFFERENT basis than they accumulate.
                    # This also keeps a reduce_only EXIT (Option D) and a SETTLEMENT-close (R-d) row -- both is_exit=1
                    # -- out of orders_today (gate 8 caps ENTRIES) and the daily/open USD (an exit/settlement is not a
                    # daily SPEND and must not inflate the count ceiling on the next restart).
                    "SELECT category, COUNT(*) n, COALESCE(SUM(CASE WHEN outcome_leg='yes' "
                    "  THEN submitted_count*submitted_price ELSE submitted_count*(1.0-submitted_price) END),0) usd "
                    "FROM pm_subdivision_order "
                    "WHERE account_id=? AND dry_run=0 AND outcome_status='filled' AND is_exit=0 AND response_ts>=? "
                    "GROUP BY category", (aid, self._day0)).fetchall():
                    self._daily_usd[(aid, r["category"])] = float(r["usd"] or 0.0)
                    self._orders_today[(aid, r["category"])] = int(r["n"] or 0)
                    self._open_usd[aid] = self._open_usd.get(aid, 0.0) + float(r["usd"] or 0.0)
                for r in conn.execute(
                    "SELECT client_order_id FROM pm_subdivision_order "
                    "WHERE account_id=? AND dry_run=0 AND client_order_id IS NOT NULL", (aid,)).fetchall():
                    self._placed_coids.add(r["client_order_id"])
        # ★ R7 venue-exposure rebase (2026-09-02): snapshot the open_usd SEED so in_cycle_open_usd(aid) can isolate
        # THIS cycle's commit_would_place increments from the construction-time DB seed. On the LIVE path gate 6
        # ignores the DB seed and uses (venue exposure + in_cycle_open_usd) as the base; the paper/test path still
        # uses the full journal open_usd. Captured OUTSIDE the table-exists guard so it is always defined ({} if empty).
        self._open_usd_seed = dict(self._open_usd)

    def already_placed(self, coid): return coid in self._placed_coids
    def daily_usd(self, aid, cat): return self._daily_usd.get((aid, cat), 0.0)
    def open_usd(self, aid): return self._open_usd.get(aid, 0.0)
    def in_cycle_open_usd(self, aid):
        """The open_usd ADDED this cycle via commit_would_place (current minus the construction seed). Gate 6's R7
        venue rebase adds this (PM's in-flight placements, not yet reflected on the venue) to the venue base."""
        return self._open_usd.get(aid, 0.0) - self._open_usd_seed.get(aid, 0.0)
    def orders_today(self, aid, cat): return self._orders_today.get((aid, cat), 0)
    # ★ C (2026-09-03): the ACCOUNT-LEVEL aggregates -- the sum across ALL of the account's categories in the SAME
    # shared Journal. DERIVED from the per-category counters (not a separate accumulator), so they cannot diverge from
    # the per-category numbers and need no seed/commit changes. Under Option C (ONE task/account, sequential categories,
    # ONE shared Journal) these see every category's in-cycle commit -> gate 5b/8b are race-free by construction, the
    # IDENTICAL mechanism as gate 6's account-keyed open cap. Categories per account are a handful, so the sum is O(1).
    def daily_usd_account(self, aid): return sum(v for (a, c), v in self._daily_usd.items() if a == aid)
    def orders_today_account(self, aid): return sum(n for (a, c), n in self._orders_today.items() if a == aid)

    def commit_would_place(self, aid, cat, usd: float) -> None:
        """Accumulate a would-place against the in-memory counters so LATER signals in the same run are gated by the
        caps (a dry-run of many copies must trip the daily/exposure/count ceilings). O(1)."""
        self._daily_usd[(aid, cat)] = self.daily_usd(aid, cat) + usd
        self._open_usd[aid] = self.open_usd(aid) + usd
        self._orders_today[(aid, cat)] = self.orders_today(aid, cat) + 1


# ── THE CHOKEPOINT ──────────────────────────────────────────────────────────
# ── per-category matcher dispatch (B2, 2026-09-03) ─────────────────────────────────────────────────────
# The chokepoint is category-AGNOSTIC after the match: ticker/leg/quote-lookup/gates/sizing/body are identical for
# every category. The ONE category-specific step is parsing the Poly bet + matching it to a Kalshi contract, which
# differs by market family (MLB moneyline/total/spread vs UFC fight/distance). Each adapter is a (parse, match) pair:
#   parse(slug, outcome)                      -> the matcher's ParsedPolyBet
#   match(parsed, ctx, allowed_market_types)  -> a MatchResult with the uniform fields evaluate reads below
#                                                (.status / .market_type / .reason / .kalshi_ticker / .leg).
# BOTH matchers already share that MatchResult surface. The MLB adapter delegates to the IDENTICAL M.match_bet call
# evaluate used inline before B2, so MLB behaviour is byte-identical by construction (see test_b2_dispatch). An
# UNKNOWN category has NO adapter -> evaluate fail-SAFE skips (never match with the wrong matcher -- the standing lens).
def _mlb_parse(slug, outcome):
    return M.parse_poly_mlb_bet(slug, outcome)


def _mlb_match(parsed, ctx, allowed_market_types):
    return M.match_bet(parsed, ctx.moneyline_index, ctx.total_index, ctx.spread_index, ctx.kalshi_dates,
                       allowed_market_types=allowed_market_types)


def _ufc_parse(slug, outcome):
    return U.parse_poly_ufc_bet(slug, outcome)


def _ufc_match(parsed, ctx, allowed_market_types):
    # UFC needs only the fight index (fight moneyline + go-the-distance attached) + the ISO dates present; there is no
    # total/spread. `fight_index or {}` fail-safes an MLB-shaped ctx (fight_index=None) to "no contract" rather than
    # crashing -- but the registry never routes UFC to an MLB ctx (the ctx builder is category-keyed too).
    return U.match_bet(parsed, ctx.fight_index or {}, ctx.kalshi_dates,
                       allowed_market_types=allowed_market_types)


MATCHER_ADAPTERS = {
    "mlb": (_mlb_parse, _mlb_match),
    "ufc": (_ufc_parse, _ufc_match),
}


def evaluate(signal: CopySignal, sub: SubConfig, ctx: MarketContext, journal: Journal, conn, now_ts: int,
             *, shard_balances=None, venue_exposure=None, legacy_db_path=None) -> Decision:
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
    # SIZING MODE (read per cycle): 'contracts' = a FLAT whole-contract count (R8); 'fixed'/other = flat DOLLARS
    # (legacy). In contracts mode fixed_stake_usd is IRRELEVANT (may be NULL) -- the per-order dollar bound is
    # gate 2b (notional vs per_order cap), not gate 2a. The USD gates all gate on the leg-correct NOTIONAL below.
    sizing_mode = (sub.sizing_mode or "fixed")
    if sizing_mode not in ("fixed", "contracts", "kelly"):                            # NEVER a silent fallback
        _LOG.warning("pm sizing_mode: UNRECOGNISED %r -> falling back to flat-dollars 'fixed' (check the "
                     "pm_subdivision.sizing_mode value; the built modes are 'fixed' and 'contracts')", sizing_mode)
    if sizing_mode == "contracts":
        copy_usd = 0.0                                                                # not a dollars stake
        flat_contracts = int(sub.contracts)                                          # per-cycle from pm_subdivision.contracts
        if flat_contracts < 1:                                                        # LOUD clamp (the liquidity_ratio=0 lens)
            _LOG.warning("pm contracts: INVALID %r (non-positive) -> CLAMPED to 1. A 0/negative contract count would "
                         "otherwise SILENTLY place 1 -- set a positive contracts value.", sub.contracts)
            flat_contracts = 1
    else:
        copy_usd = float(sub.fixed_stake_usd)
        flat_contracts = None
        if not signal.is_exit and copy_usd > sub.per_order_usd_cap + 1e-9:            # gate 2a (ENTRY flat-dollars config sanity ONLY)
            return Decision("reject:per_order_cap", sid, disarm_armed=armed, is_exit=signal.is_exit,
                            reason="fixed_stake_%.4f_gt_cap_%.4f" % (copy_usd, sub.per_order_usd_cap))

    adapter = MATCHER_ADAPTERS.get(sub.category)                                      # gate 3 (match, exact strike)
    if adapter is None:                                        # unknown category has NO matcher -> fail-safe skip
        return Decision("skip:no_matcher_for_category", sid, reason="no_matcher_for_category:%s" % sub.category,
                        is_exit=signal.is_exit, disarm_armed=armed)
    _parse_bet, _match_bet = adapter
    parsed = _parse_bet(signal.slug, signal.outcome)
    match = _match_bet(parsed, ctx, sub.market_types)
    if match.status != "matched":
        return Decision("skip:%s" % match.status, sid, market_type=match.market_type, reason=match.reason,
                        is_exit=signal.is_exit, disarm_armed=armed)
    ticker, leg = match.kalshi_ticker, match.leg
    market = ctx.markets.get(ticker)
    order_shard = (market or {}).get("exchange_index")   # PER-MARKET shard: gate 6b funding + rung-3 explicit routing
    if isinstance(order_shard, bool):                    # bool is an int subclass but is never a valid shard
        order_shard = None
    elif order_shard is not None:                        # coerce to int-or-None: a corrupt non-int FAILS CLOSED
        try:                                             # (gate 6b skips, body omits -> auto-route), never crashes
            order_shard = int(order_shard)
        except (TypeError, ValueError):
            order_shard = None
    # gate 7 precondition (a quote to price against). ENTRIES price to BUY at the leg ASK; EXITS price to SELL at
    # the leg BID so the reduce_only IOC actually CROSSES (an ask-referenced sell fills only when spread <= slip --
    # a latent under-fill = a missed exit; see _leg_exit_bid). base_price feeds v2_side_and_price + build_v2 below.
    base_price = _leg_exit_bid(market, leg) if signal.is_exit else _leg_ask(market, leg)
    if base_price is None:
        return Decision("skip:no_quote", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                        reason=("no_bid_to_sell_into" if signal.is_exit else "no_two_sided_quote_for_leg"),
                        is_exit=signal.is_exit, disarm_armed=armed)
    if signal.is_exit:
        # ★ HOLDING GUARD (fail-closed) + Fork-B1 FULL CLOSE. A reduce_only EXIT is a REAL SELL; firing one against a
        # position we do NOT hold is the K1 phantom-exit class -- the worst outcome here -- so size the close at OUR
        # journal NET-OPEN on THIS (ticker, leg) and REFUSE (skip:not_held) when it is <= 0 (nothing to close). B1
        # (Jack RULED): FULL close on any confirmed whale exit (not a mirror-ratio); reduce_only also caps at the
        # venue so we can never oversell. Read fresh from the durable journal (self-corrects within a cycle as
        # earlier exits finalize).
        # PER-WALLET net-open for BOTH a whale-exit AND an opposed-close. An opposed-close flattens the WHOLE market
        # (Jack RULED "flat means ALL of it"), but it does so by emitting ONE per-wallet close for EACH holding wallet
        # (detect_opposing_closes), summing to the full account flatten -- NOT one account-net row booked under one
        # wallet. That account-net-under-one-wallet approach was REVIEW-REJECTED: it drives the booking wallet's
        # per-wallet net NEGATIVE, and the PER-WALLET settlement scan then double-books the co-whales' still-positive
        # net (phantom P&L + a false boot_reconcile mismatch latch). Per-wallet closes keep every downstream per-wallet
        # view (settlement, a later whale-exit, the F1 seed) consistent while still flattening the whole account.
        held = journal_net_open_contracts(conn, sub.account_id, sub.category, ticker, leg, signal.wallet)
        count = int(round(held))
        if count <= 0:
            return Decision("skip:not_held", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                            is_exit=True, disarm_armed=armed, reason="no_net_open_to_exit(held=%.4f)" % held)
    else:
        count = flat_contracts if sizing_mode == "contracts" else usd_to_contracts(copy_usd, base_price)
    _side, limit_price = v2_side_and_price(outcome=leg, is_buy=(not signal.is_exit),
                                           base_price=base_price, max_slippage_cents=sub.max_slippage_cents)
    # ★ OUTCOME-LEG committed cash per contract (the $163.84 fix / the NO-leg lens -- bitten 6x): a NO leg costs
    # (1 - yes_side_price), NOT the yes-side price. Every ENTRY USD gate gates on THIS. For an EXIT it is the
    # per-contract sale value (informational: the caps below do not apply to a risk-reducing exit).
    leg_cost = limit_price if leg == "yes" else (1.0 - limit_price)
    notional = count * leg_cost
    # ★ gates 3 (liquidity/depth) + 2b (per-order USD cap) are ENTRY caps ONLY. A reduce_only EXIT reduces risk and
    # must NEVER be blocked by a size/liquidity cap: a rejected exit STRANDS the position (the Cubs failure). The
    # exit is instead guarded by the bid-price precondition above (a book to sell into) + the holding guard; the IOC
    # + slippage clamp (gate 7, inside build_v2) bound its fill, and reduce_only bounds its size at the venue.
    if not signal.is_exit:
        # gate 3 (liquid): required book depth = liquidity_ratio * THIS order's notional (config, per sub-division,
        # READ PER CYCLE; decoupled from the spend cap). (i) TWO-SIDED + SPREAD via M.liquidity_ok (ZERO $-floor --
        # liquidity_dollars is a deprecated always-'0.0000' stub); (ii) a REAL depth floor from LEG-CORRECT
        # TOP-OF-BOOK SIZE (yes_*_size_fp x price, merged from the raw payload). Both fail CLOSED.
        required_depth = sub.liquidity_ratio * notional
        if not M.liquidity_ok(market, min_liquidity_usd=0.0, max_spread_cents=max(1, sub.max_slippage_cents * 2)):
            return Decision("skip:illiquid", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                            count=count, notional_usd=notional, is_exit=False, disarm_armed=armed,
                            reason="no_two_sided_or_spread_over_cap")
        depth_usd = _top_of_book_depth_usd(market, leg)                               # gate 3 depth: leg-correct size x price
        if not (depth_usd + 1e-9 >= required_depth):
            return Decision("skip:illiquid", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                            count=count, notional_usd=notional, is_exit=False, disarm_armed=armed,
                            reason="depth_floor:need_%.4f=ratio_%.2f*notional_%.4f have_%.4f(top_of_book)"
                                   % (required_depth, sub.liquidity_ratio, notional, depth_usd))
        if notional > sub.per_order_usd_cap + 1e-9:                                   # gate 2b (per-order USD ceiling)
            return Decision("reject:per_order_cap", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                            count=count, notional_usd=notional, is_exit=False, disarm_armed=armed,
                            reason="notional_%.4f_gt_cap_%.4f" % (notional, sub.per_order_usd_cap))

    coid = _kalshi_coid(sub.division, signal.wallet, ticker, leg, sid)                # gate 4 (idempotency)
    if journal.already_placed(coid):
        return Decision("skip:duplicate", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                        client_order_id=coid, reason="already_placed", is_exit=signal.is_exit, disarm_armed=armed)

    if not signal.is_exit:   # gates 5/6/8 are ENTRY caps -- a reduce_only EXIT reduces risk and must NEVER be capped
        if journal.daily_usd(sub.account_id, sub.category) + notional > sub.daily_usd_cap + 1e-9:   # gate 5
            return Decision("reject:daily_cap", sid, market_type=match.market_type, kalshi_ticker=ticker, leg=leg,
                            count=count, notional_usd=notional, reason="daily_cap", disarm_armed=armed)
        # gate 5b (C 2026-09-03): the ACCOUNT-LEVEL daily aggregate across ALL categories stays $150/day -- adding a
        # category must NOT silently grow the account total. Race-free by construction under Option C (ONE task/account,
        # sequential categories, the SAME shared Journal -- a later category's evaluate sees the earlier's in-cycle
        # commit, exactly as gate 6). HEADROOM FLOWS: whichever category is active consumes the shared cap; a busy
        # category can take the whole $150 (bounded by its own per-category gate 5), leaving a quiet co-category only the
        # remainder -- the deliberate cost of the aggregate cap (chosen over 75/75 to not strand headroom on a quiet
        # night). BYTE-IDENTICAL with one category at the ruled $150 (gate 5 fires first at the same threshold).
        if journal.daily_usd_account(sub.account_id) + notional > ACCOUNT_DAILY_USD_CAP + 1e-9:      # gate 5b
            return Decision("reject:account_daily_cap", sid, market_type=match.market_type, kalshi_ticker=ticker,
                            leg=leg, count=count, notional_usd=notional, reason="account_daily_cap", disarm_armed=armed)
        # gate 6 (exposure cap): R7 VENUE REBASE (2026-09-02, RULING 5). The base is the ACCOUNT'S TRUE open
        # exposure read from the venue THIS cycle (co-tenant + manual + PM), not PM's journal sum -- so the cap is
        # correct regardless of PM-exclusivity (a journal sum under-counts a co-tenant on a shared keypair). The
        # journal accumulator adds THIS cycle's in-flight PM placements (not yet on the venue). ★ Mirrors gate 6b's
        # shard_balances contract: the LIVE path ALWAYS passes a VenueExposure (real, or has_data=False on a read
        # failure) so gate 6 FAILS CLOSED (has_data False -> skip:exposure_unknown, never size blind); venue_exposure
        # None DISABLES the rebase (paper / dry-run / test ONLY -- unreachable on the live path by construction, the
        # "safety check that silently stops checking" guard).
        if venue_exposure is None:
            open_base = journal.open_usd(sub.account_id)                         # paper/test: PM journal-summed base
        elif not venue_exposure.has_data:
            return Decision("skip:exposure_unknown", sid, market_type=match.market_type, kalshi_ticker=ticker,
                            leg=leg, count=count, notional_usd=notional, is_exit=False, disarm_armed=armed,
                            reason="venue open-exposure unreadable -> fail-closed (never size against an unknown book)")
        else:
            open_base = venue_exposure.open_dollars() + journal.in_cycle_open_usd(sub.account_id)   # R7 venue base
        if open_base + notional > sub.max_open_usd + 1e-9:                                          # gate 6
            return Decision("reject:exposure_cap", sid, market_type=match.market_type, kalshi_ticker=ticker,
                            leg=leg, count=count, notional_usd=notional, reason="exposure_cap", disarm_armed=armed)
        if journal.orders_today(sub.account_id, sub.category) + 1 > sub.max_orders_per_day:         # gate 8
            return Decision("reject:count_ceiling", sid, market_type=match.market_type, kalshi_ticker=ticker,
                            leg=leg, reason="orders_per_day", disarm_armed=armed)
        if journal.orders_today_account(sub.account_id) + 1 > ACCOUNT_MAX_ORDERS_PER_DAY:            # gate 8b (C)
            return Decision("reject:account_count_ceiling", sid, market_type=match.market_type, kalshi_ticker=ticker,
                            leg=leg, reason="account_orders_per_day", disarm_armed=armed)
        # gate 6b (PER-MARKET shard funding): Kalshi shards collateral by exchange_index and orders auto-route to the
        # market's shard, charging THAT shard -- a healthy TOTAL with an empty market-shard is Karen's silent death.
        # ★ PER MARKET, not per category: live markets never migrate, so an MLB market's shard depends on its creation
        # date -> read THIS market's exchange_index (not the series/category). This is a SKIP (a funding gap is
        # FUNDABLE-LATER, not a fault -- it must NOT feed the error-latch), and it FAILS CLOSED: an unknown market
        # shard (exchange_index None) or an unknown split (has_breakdown False -> can_fund None) or a too-thin shard
        # (can_fund False) all SKIP. Only can_fund True proceeds. None shard_balances disables the gate (test/paper
        # opt-out; unreachable on the live path -- see the evaluate docstring).
        if shard_balances is not None:
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
        max_slippage_cents=sub.max_slippage_cents, tif=_ENTRY_TIF, client_order_id=coid,
        # EXIT -> the net-open count (full close); ENTRY contracts-mode -> the flat count; ENTRY fixed-mode -> None
        # (derive from copy_usd). An exit never derives from copy_usd (it is 0 in contracts mode).
        count=(count if (signal.is_exit or sizing_mode == "contracts") else None))
    # ★ RUNG 3 (PM-only; no shared-broker edit): explicit exchange_index = the MARKET's OWN shard (read from the
    # market object -- authoritative). DETERMINISTIC routing instead of relying on Kalshi auto-route. Correction 4:
    # explicit targeting bills only THAT shard's Write budget (auto-route bills the unscoped budget AND every nonzero
    # shard's) + avoids the auto-route latency cost. Set ONLY when the shard is KNOWN; None (paper/opt-out/unknown)
    # -> omit -> auto-route (the prior behavior, byte-identical). Added to `body` BEFORE it becomes decision.body, so
    # the dry-run body and the POSTed body stay byte-identical (option-b parity).
    if order_shard is not None:
        body["exchange_index"] = int(order_shard)
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


def journal_net_open_contracts(conn, account_id: str, category: str, ticker: str, leg: str, wallet: str) -> float:
    """Read-only: THIS WHALE's NET-OPEN filled contracts on ONE (ticker, leg) = SUM(entry fills) - SUM(exit fills)
    from the durable journal (dry_run=0, filled), scoped to `wallet`. Two Option-D uses: (a) the HOLDING GUARD --
    never fire a reduce_only exit against a position we do not hold (fail-closed: 0 -> no exit); (b) the candidate
    exit SIZE for a FULL close (Fork B1, net-open contracts). ★ PER-WALLET (Jack RULED 2026-08-31): the rule is
    'we exit when THE WHALE exits' -- so an exit closes OUR copy of THAT whale's position, NOT a co-whale's position
    on the same (ticker, leg). Account-level scoping would close whale B because whale A sold (safe/bias-to-flat but
    WRONG -- it strands B's edge on A's decision). Ticker compared UPPER (the identity the order path sends to
    Kalshi). 0.0 if the table is absent or the leg is flat for this whale. NEVER negative for a real book (exits
    reduce_only cannot exceed entries); a negative would surface an over-exit / mis-booking, NOT clamped here."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return 0.0
    r = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) "
        "  ELSE -COALESCE(fill_count,0) END), 0) AS net "
        "FROM pm_subdivision_order WHERE account_id=? AND category=? AND wallet=? AND UPPER(ticker)=UPPER(?) "
        "  AND outcome_leg=? AND dry_run=0 AND outcome_status='filled'",
        (account_id, category, wallet, ticker, leg)).fetchone()
    return float(r["net"] or 0.0) if r else 0.0


def account_held_outcomes(conn, account_id: str, category: str) -> dict:
    """{condition_id: set(outcome_index)} for every ACCOUNT-level NET-OPEN>0 outcome (all whales). The opposition
    detector's view of what we HOLD, keyed on the SEMANTIC market (condition_id) + outcome_index -- the identity that
    makes 'opposing' unambiguous across market types (two outcomes of ONE Polymarket binary market are mutually
    exclusive: moneyline = two teams, total = over/under, spread = the two sides; a different LINE is a different
    condition_id, so it never opposes -- SDCIN total-9 vs total-10). Nets by (cid, oidx): a terminal-close row
    (whale-exit, settlement, or opposed) carries its OWN (cid, oidx) -- settlement.book_settlements stamps them, so a
    settled/exited side NETS FLAT here exactly as it does in the ticker-keyed boot_reconcile + live_positions views
    (else a phantom held outcome could false-contest a legitimate same-side re-signal -- REVIEW blocker, fixed).
    NB: 'any two distinct outcomes oppose' assumes BINARY markets; the matcher only ever produces single-game
    moneyline/total/spread (all binary), so a multi-outcome market never reaches here -- if the copied category set
    ever widens to a multi-outcome market this assumption must be revisited."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return {}
    rows = conn.execute(
        "SELECT condition_id, outcome_index, "
        " COALESCE(SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE -COALESCE(fill_count,0) END),0) AS net "
        "FROM pm_subdivision_order WHERE account_id=? AND category=? AND dry_run=0 AND outcome_status='filled' "
        "  AND condition_id IS NOT NULL AND outcome_index IS NOT NULL "
        "GROUP BY condition_id, outcome_index", (account_id, category)).fetchall()
    out: dict = {}
    for r in rows:
        if float(r["net"] or 0.0) > 1e-9:
            out.setdefault(r["condition_id"], set()).add(int(r["outcome_index"]))
    return out


def account_opposed_cids(conn, account_id: str, category: str) -> set:
    """The OPPOSED-MEMORY: {condition_id} this (account, category) has EVER contested. ★ R2 (2026-09-03): DECISION-keyed
    -- the UNION of (a) every cid carrying a close_source='opposed' row [the RESOLUTION: a booked flatten] and (b) the
    pm_opposed_marker rows [the DECISION: written when detect_opposing_closes DECIDES a cid is contested, EVEN when it
    generated no close]. (b) closes the latent gap where a contest we DECIDED to flatten but COULD NOT (we hold a side,
    no co-present entry to route the per-wallet close) left NO row -> the memory never learned and the held side rode
    to settlement un-flattened + re-detected every cycle. An opposed market stays OFF THE BOOKS for the rest of its
    life (Jack RULED: the disagreement does not resolve, the GAME does); it goes INERT at settlement (a resolved market
    emits no entry signals, so a persistent marker never false-contests -- proven in test). Keyed on the market being
    CONTESTED (not on the coid), independent of gate-4's stable-coid dedup. ★ TOLERANT of a pre-migration schema (the
    014 lesson): if pm_opposed_marker is absent (code precedes migration 018) this DEGRADES to the opposed-close-only
    read -- the engine cannot crash. Read-only; [] if the order table is absent (pre-migration-010)."""
    if not _table_exists(conn, "pm_subdivision_order"):
        return set()
    cids = {r[0] for r in conn.execute(
        "SELECT DISTINCT condition_id FROM pm_subdivision_order "
        "WHERE account_id = ? AND category = ? AND close_source = 'opposed' AND condition_id IS NOT NULL",
        (account_id, category)).fetchall()}
    if _table_exists(conn, "pm_opposed_marker"):                 # R2 decision markers (tolerant: skip if pre-migration)
        cids |= {r[0] for r in conn.execute(
            "SELECT condition_id FROM pm_opposed_marker WHERE account_id = ? AND category = ?",
            (account_id, category)).fetchall()}
    return cids


def mark_opposed_contested(conn, account_id: str, category: str, condition_ids, *, now_ts: int) -> int:
    """★ R2 (2026-09-03): record the DECISION that a market was contested -- one pm_opposed_marker row per
    (account, category, condition_id) -- so account_opposed_cids remembers a contest EVEN when it generated no
    close_source='opposed' row (the held-side-with-no-co-present-entry case the close-keyed read missed). INSERT OR
    IGNORE (idempotent + monotonic: first_contested_ts is the first-seen). Called from the guard ONLY for NEWLY-decided
    contests (rare), off the order hot path. Returns the count of NEW markers written. ★ TOLERANT: a NO-OP if
    pm_opposed_marker is absent (code precedes migration 018) -> degrade to opposed-close-only, never crash."""
    condition_ids = [c for c in (condition_ids or []) if c]
    if not condition_ids or not _table_exists(conn, "pm_opposed_marker"):
        return 0
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO pm_opposed_marker (account_id, category, condition_id, first_contested_ts) "
        "VALUES (?, ?, ?, ?)", [(account_id, category, cid, int(now_ts)) for cid in condition_ids])
    if hasattr(conn, "commit"):
        conn.commit()
    return conn.total_changes - before


def detect_opposing_closes(entry_signals, held_outcomes: dict, opposed_cids=None):
    """PURE (no DB): given this cycle's ENTRY signals + `account_held_outcomes` ({cid: set(oidx)}) + the
    OPPOSED-MEMORY `opposed_cids` (every cid EVER contested -- account_opposed_cids), find every CONTESTED market and
    return (kept_entries, opposed_closes, contested_cids, preexisting_pair_cids).

    ★ THE OPPOSED-MEMORY (2026-09-01) closes the FLICKER bug: the guard used to contest only on the SAME-CYCLE
    signal union, so when the opposing side's signal briefly vanished the market read as uncontested and the other
    side ENTERED (then closed next cycle when the signal returned) -- a bounded but real enter-close churn. A cid in
    `opposed_cids` stays CONTESTED for the rest of its life (Jack RULED: the disagreement does not resolve, the GAME
    does), so its incoming entries are SKIPPED even when the opposition flickers off. Keyed on the market being
    CONTESTED (the opposed close in the journal), NOT on the coid -> the bound is INDEPENDENT of gate-4's stable-coid
    dedup, so it survives the R7.h /activity-tx_hash re-entry fix that would otherwise unbound the churn.

    ★ THE GUARD PREVENTS a new opposing pair; it does NOT retroactively flatten a pair we ALREADY hold both sides of
    (Jack RULED: let a pre-existing pair -- BALCOL -- SETTLE; a boot-time flatten would override that with two exit
    orders into a started game). So a cid where we already hold >= 2 distinct outcomes is a PRE-EXISTING pair: LEFT
    ALONE (returned separately for visibility; its re-entry signals flow through and are gate-4 deduped, so no new
    order). A cid is NEWLY CONTESTED only when we hold <= 1 side AND the union {held} u {incoming} reaches >= 2
    distinct outcomes -- i.e. an incoming signal is CREATING the disagreement this cycle. Then we go FLAT: CLOSE the
    outcome(s) we hold and SKIP all incoming entries for that market (place neither side; signal ordering carries no
    information). SAME-SIDE stacking (same cid+outcome_index, N wallets) is NEVER contested -- agreement is conviction.

    ★ PER-WALLET CLOSES (REVIEW fix): the flatten emits ONE opposed-close per HOLDING WHALE (each co-present entry
    signal on a held outcome), sized per-wallet in evaluate. The SUM flattens the whole account ('flat means ALL of
    it', Jack) WITHOUT the account-net-under-one-wallet negative that corrupted the per-wallet settlement scan. Each
    close carries that whale's own slug/outcome (correct routing) and wallet (a stable per-wallet coid). A holding
    wallet whose book failed this cycle has no co-present signal -> its close is DEFERRED (retried), never guessed;
    a co-present signal for a wallet that does NOT actually hold (per its journal net-open) is a safe no-op
    (evaluate's holding guard returns skip:not_held)."""
    opposed_cids = opposed_cids or frozenset()
    inc_by_cid: dict = {}                 # cid -> {oidx: [every co-present entry signal on that outcome]}
    for s in entry_signals:
        inc_by_cid.setdefault(s.condition_id, {}).setdefault(s.outcome_index, []).append(s)
    contested, preexisting = set(), set()
    for cid in set(inc_by_cid) | set(held_outcomes):
        held_oidx = set(held_outcomes.get(cid, set()))
        if len(held_oidx) >= 2:
            preexisting.add(cid)          # we ALREADY hold both sides -> pre-existing pair, LEAVE IT (let it settle)
            continue
        inc_oidx = set(inc_by_cid.get(cid, {}).keys())
        # CONTESTED = the disagreement is being created THIS cycle (held u incoming >= 2) OR the market is in the
        # OPPOSED-MEMORY (already contested earlier -> stays off the books through a signal flicker).
        if cid in opposed_cids or len(held_oidx | inc_oidx) >= 2:
            contested.add(cid)
    kept = [s for s in entry_signals if s.condition_id not in contested]   # pre-existing-pair entries flow (gate-4 dedups)
    closes = []
    for cid in contested:
        for oi in held_outcomes.get(cid, set()):
            for src in inc_by_cid.get(cid, {}).get(oi, []):    # ONE close per holding whale (per-wallet flatten)
                closes.append(CopySignal(
                    wallet=src.wallet, slug=src.slug, outcome=src.outcome, condition_id=cid, outcome_index=oi,
                    signal_id=stable_signal_id(src.wallet, cid, oi, "opposed"), is_exit=True, close_source="opposed"))
    return kept, closes, contested, preexisting
