"""Prediction Markets -- the LIVE DRIVER (Stage 3 R7.c): the engine-task that finally DRIVES the inert chokepoint.

Everything beneath this is built + deployed but INERT: the chokepoint (`execution.py`), the arm/kill switch
(`arm.py`), the 3-dim matcher (`data/mlb_poly_kalshi_match.py`), and boot-reconcile (`boot_reconcile.py`).
`execution.run_arm_gated_cycle` is called from NOWHERE. This module is the driver R7 wires: an engine-side
`asyncio` task (mirroring `main.py:_scheduled_poly_kalshi_loop`, the proven live Kalshi copy path) that, per cycle,
(a) refreshes the Kalshi market index, (b) reads the attached whales' `/positions`, (c) runs each copy signal
through the chokepoint, and (d) FOR AN ARMED sub-division only, POSTs the gate-approved order.

** BUILD-ONLY (R7.c): this module + a thin `main.py:run()` block. It is NOT activated (the `main.py` block is
   gated OFF and lands in the R7.e deploy manifest). Box-scratch injects a STUB broker + runs DISARMED -> ZERO
   real POSTs. The first real order is R7.f, its own authorization, after the combined exclusivity+driver restart. **

THE PLACEMENT SEAM (Jack RULED option (b)): the `place_fn` POSTs the chokepoint's GATE-APPROVED body VERBATIM --
`decision.body` + `decision.client_order_id` -- NOT via `KalshiLiveBroker.place_order` (which would REBUILD the
body). The chokepoint's guarantees are ABOUT THE BODY IT APPROVED; a rebuild that differs by a cent still places
AN order -- just not the approved one -- a silent NO-leg-lens home. This is also the PRECEDENT
(`poly_kalshi_executor` POSTs a pre-built body). We REUSE the pure `fill_event_from_v2_response` + the
`KalshiNoFill`/`OrderPlacementError` split; the ONLY duplication is the POST try/except wrapper (noted in kalshi_live.py).

** PENDING-FIRST idempotency (R7.c adversarial-review fix): the coid is journaled (dry_run=0, outcome_status
   'submitting') BEFORE the POST, then finalized (filled/no_fill/error) after. So a POST/response/journal-write
   failure -- OR a network timeout -- CANNOT re-drive the same order next cycle (gate-4 dedup already sees the coid).
   The K9 window (order live at Kalshi, response/write lost) is thereby BOUNDED to boot-reconcile adjudicating an
   ambiguous 'submitting' row, NOT re-fired every ~poll_sec. Every downstream consumer filters outcome_status=
   'filled', so a 'submitting' row counts as NO position + NO budget (correct) but DOES register the coid for dedup
   (gate-4 filters dry_run=0 + coid, no status). ** A network transport error (httpx timeout/connect) is mapped to
   OrderPlacementError (LOUD, possibly-placed) so it feeds the consecutive-error kill-switch + gets a journal row --
   never escaping to the never-die loop un-latched (an adversarial-review HIGH).

SAFETY carried from R5 (all enforced HERE):
  * DISARM blocks EVERYTHING -- the cycle re-reads `arm.read_arm_verdict` immediately BEFORE EVERY order.
  * BOOT-RECONCILE runs at boot against the AUTHENTICATED portfolio; a mismatch latches boot_reconcile_mismatch, and
    a boot-reconcile RAISE (our own DB fault) ALSO force-latches (do not proceed armed on a system fault).
  * A loud `OrderPlacementError` (incl. a wrapped transport timeout) increments a consecutive-error latch; a 401/403
    latches the whole account (auth-failure) + flags open positions for MANUAL exit.
  * ENTRIES from /positions; EXITS (Option D) from a /positions size-REDUCTION CONFIRMED by an /activity SELL
    in-window (BOTH or MISSED). Every exit is reduce_only, sized at OUR journal net-open (FULL close, Fork B1),
    refused if we hold nothing (skip:not_held), and -- like an entry -- BLOCKED by DISARM (off is off).

R7.e VERIFY (read-only): the pykalshi `get_markets` market-object quote field names mapped into
`MarketContext.markets` (yes_ask/no_ask/liquidity). A miss leaves a ticker UNQUOTED -> evaluate skip:no_quote (safe).
And confirm Kalshi's `client_order_id` idempotency on a real duplicate (the pending-first design bounds the risk).

Spec: reports/prediction_markets/STAGE3_PLAN_2026-08-28.md sec 8/16 + R7_PLAN_2026-08-29.md.
"""
from __future__ import annotations

import inspect
import logging
import time as _time

from . import arm, boot_reconcile, db, execution, heartbeat, paper, settlement, shard_balance, venue_exposure
from ..data import mlb_poly_kalshi_match as M
from ..data import ufc_poly_kalshi_match as U   # B2: UFC fight/distance index builders for fetch_ufc_market_context
from ..data import tennis_poly_kalshi_match as TN   # tennis (atp/wta) match index builder for fetch_tennis_market_context
from ..data import sports_structural_match as SS   # rung 1 (2026-09-06): structural game-index builder for nfl/nba/nhl/wnba/cfb
# REUSE (pure builders + the benign/loud split) -- NOT KalshiLiveBroker, NOT place_order (structural: no rebuild).
from ..brokers.kalshi_live import (KalshiNoFill, OrderPlacementError, fill_event_from_v2_response,
                                   _is_benign_fok_nofill, _V2_ORDERS_PATH)

_LOG = logging.getLogger(__name__)
SERIES = ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD")   # Jack's scope ruling: moneyline+total+spread
UFC_SERIES = ("KXUFCFIGHT", "KXUFCDISTANCE")          # B2: UFC moneyline (per-fighter YES) + go-the-distance (binary)
# tennis (2026-09-04): the category -> Kalshi MATCH series map. atp/wta split men's/women's on BOTH venues; the ctx
# builder fetches exactly one series per category (match-winner only -- no set/game/futures/table-tennis).
TENNIS_SERIES = {"atp": "KXATPMATCH", "wta": "KXWTAMATCH"}
_SETTLED_LOOKBACK_SEC = 160 * 86400
# ★ SUSTAINED-SHARD-UNDERFUNDING alarm threshold (gate 6b, Jack RULED 2026-08-30: SURFACED, NOT latched). N=3 cycles:
# Kalshi auto-rebalances every 10s and the driver polls ~7s, so a transient gap while a rebalance is mid-flight lasts
# ~1-2 cycles; N=3 (~21s at poll=7s) clears that transient with margin, so the alarm fires only on a GENUINE sustained
# gap (no allocation set, or drained faster than refill) -- fast enough to matter, not so fast it cries on a transient.
_SHARD_UNDERFUNDED_ALARM_N = 3
# ★ R-d settlement-scan cadence: booking a settled position is BOOKKEEPING, not time-critical like a trade, and
# settlements are infrequent -- so scan on a THROTTLE (default 600s) inside the loop, NOT every ~7s cycle (which
# would cost ~12k /portfolio/settlements calls/day for no benefit). The BOOT scan (before boot-reconcile) is the
# load-bearing one -- it books a position that settled while the engine was DOWN so reconcile comes up CLEAN.
_SETTLE_SCAN_SEC = 600.0


# ── market context: fetch the live Kalshi catalog + build the 3-dim index ─────────────────────────────
def _market_quote_dict(m) -> dict:
    """Map a pykalshi market object -> the chokepoint's per-ticker quote dict. ** R7.e VERIFY the field names
    against a real get_markets response ** -- a miss leaves a ticker unquoted -> evaluate skip:no_quote (safe)."""
    def d(*names):
        for n in names:
            v = getattr(m, n, None)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None
    # ★ exchange_index is DROPPED by the pykalshi get_markets Market OBJECT (verified 2026-08-30: not in attrs, not in
    # model_dump) even though the RAW /markets payload carries it. So this getattr is ALWAYS None -- it is only the
    # fail-closed FALLBACK; the authoritative per-market shard is merged from the raw payload in fetch_market_context
    # (_merge_exchange_index). A market that never gets merged keeps None -> gate 6b FAILS CLOSED (skip).
    ei = getattr(m, "exchange_index", None)
    try:
        ei = int(ei) if ei is not None else None
    except (TypeError, ValueError):
        ei = None
    # ★ BID SIDES ARE REQUIRED (fix, 2026-08-30): gate 3's `M.liquidity_ok` reads `yes_bid_dollars` and rejects any
    # book with bid<=0 as ONE-SIDED / untradeable. The prior mapping omitted the bids, so EVERY market looked
    # one-sided -> evaluate skip:illiquid for every signal -> the driver could never place. Exposed by the rung-2
    # end-to-end driver test (the first to run the placement path); never caught before because the driver ran
    # DISARMED. no_bid is carried too for the exit-side re-check (K4).
    # ★ UNITS: the `*_dollars` fields are authoritative and are what KXMLB (a FRACTIONAL series) always populates, so
    # the `d(...)` fallback to the bare `yes_bid`/`no_bid` names is dead for MLB. NOTE (backlog, inert for MLB): those
    # bare names are INTEGER CENTS on a NON-fractional series; if a non-fractional series is ever added, the fallback
    # arm must /100 to match `brokers/kalshi.kalshi_quote_dollars`. Same pre-existing shape as yes_ask/no_ask above.
    return {"yes_ask_dollars": d("yes_ask_dollars", "yes_ask"),
            "no_ask_dollars": d("no_ask_dollars", "no_ask"),
            "yes_bid_dollars": d("yes_bid_dollars", "yes_bid"),
            "no_bid_dollars": d("no_bid_dollars", "no_bid"),
            "liquidity_dollars": d("liquidity_dollars", "liquidity"),
            "exchange_index": ei}


async def _merge_raw_market_fields(client, markets: dict, series_list=None) -> None:
    """★ Merge PER-MARKET fields the pykalshi get_markets Market OBJECT DROPS but the RAW /markets payload CARRIES
    (verified 2026-08-30, re-verified for UFC 2026-09-03): `exchange_index` (gate-6b shard) and `yes_bid_size_fp`/
    `yes_ask_size_fp` (gate-3 TOP-OF-BOOK depth). The SDK object exposes NEITHER as an attribute, and
    `liquidity_dollars` is a DEPRECATED always-'0.0000' Kalshi stub -- so without this both gates would FAIL CLOSED on
    every market (skip). The exchange_index is authoritative PER MARKET (correction 1: series-level is only right
    post-Aug-24). OPEN markets only (we only PLACE on open books). A raw-get failure leaves the fields absent -> gates
    3/6b fail-close (safe). `series_list` (B2) parameterises the series to merge; None -> the MLB SERIES (unchanged)."""
    for series in (series_list or SERIES):
        try:
            raw = client.get("/markets?series_ticker=%s&status=open&limit=1000" % series)
            if inspect.isawaitable(raw):
                raw = await raw
            for rm in ((raw.get("markets") or []) if isinstance(raw, dict) else []):
                tk = str(rm.get("ticker") or "").upper()
                if tk not in markets:
                    continue
                ei = rm.get("exchange_index")
                if ei is not None:
                    try:
                        markets[tk]["exchange_index"] = int(ei)
                    except (TypeError, ValueError):
                        pass
                for sk in ("yes_bid_size_fp", "yes_ask_size_fp"):   # top-of-book SIZE (contracts) for the gate-3 depth
                    if rm.get(sk) is not None:
                        markets[tk][sk] = rm.get(sk)
        except Exception as e:  # noqa: BLE001 -- raw-get failure -> fields absent -> gates 3/6b fail-close (safe)
            _LOG.warning("pm_live_driver: raw market-field merge failed for %s (gates 3/6b fail-close there): %s", series, e)


async def fetch_market_context(client, now_ts: int) -> execution.MarketContext:
    """Fetch OPEN + recent-SETTLED markets for the three MLB series and build the 3-dim MarketContext. Uses the
    SAME `client.get_markets(series_ticker=...)` the proven poly loop uses (main.py:5208)."""
    from pykalshi import MarketStatus
    game_t, total_t, spread_t = [], [], []
    markets: dict = {}
    dates: set = set()
    min_ts = int(now_ts) - _SETTLED_LOOKBACK_SEC
    per_series = {"KXMLBGAME": game_t, "KXMLBTOTAL": total_t, "KXMLBSPREAD": spread_t}
    for series in SERIES:
        for status, extra in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
            ms = await client.get_markets(series_ticker=series, status=status, limit=1000,
                                          fetch_all=(status == MarketStatus.SETTLED), **extra)
            for m in (ms or []):
                tk = getattr(m, "ticker", "") or ""
                if not tk:
                    continue
                per_series[series].append(tk)
                markets[tk.upper()] = _market_quote_dict(m)
    await _merge_raw_market_fields(client, markets)   # ★ SDK object drops exchange_index + size fields -> merge from raw
    game_idx = M.build_kalshi_game_index(game_t)
    total_idx = M.build_kalshi_total_index(total_t)
    spread_idx = M.build_kalshi_spread_index(spread_t)
    for tk in game_t:               # the matcher's exact-strike gate is the real guard; carry the game tickers
        dates.add(tk)
    return execution.MarketContext(game_idx, total_idx, spread_idx, frozenset(dates), markets)


async def fetch_ufc_market_context(client, now_ts: int) -> execution.MarketContext:
    """B2 (2026-09-03): fetch OPEN + recent-SETTLED KXUFCFIGHT/KXUFCDISTANCE markets and build the UFC MarketContext.
    MIRRORS fetch_market_context -- SAME `client.get_markets`, SAME `_market_quote_dict` quote fields, SAME raw
    `exchange_index` merge (UFC's exchange_index is ALSO SDK-dropped, raw=0 -> MMA shard 0; verified 2026-09-03). The
    UFC-specific parts: (1) the KXUFCFIGHT `title` ("{Fighter Full Name} wins") -- the field the matcher joins the Poly
    outcome against; `title` IS on the pykalshi MarketModel object (NOT SDK-dropped -- unlike exchange_index), so it is
    read via getattr, NO raw merge; and (2) the fight+distance index. ★ THE JOIN DATE is the card-LOCAL date encoded in
    the TICKER (BOTH Kalshi's ticker and Polymarket's slug use it, verified across a cross-midnight card 2026-09-03) --
    NOT occurrence_datetime/close_time; kalshi_dates is derived from the fight index (whose dates come from the ticker),
    so this builder NEVER reads occurrence_datetime. A market with no title is skipped by build_kalshi_fight_index."""
    from pykalshi import MarketStatus
    markets: dict = {}
    fight_markets: list = []       # [{ticker, title}] -> build_kalshi_fight_index (title carries the fighter full name)
    distance_markets: list = []    # [{ticker, title}] -> attach_distance_tickers (matched by the ticker's date+blob)
    min_ts = int(now_ts) - _SETTLED_LOOKBACK_SEC
    for series in UFC_SERIES:
        for status, extra in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
            ms = await client.get_markets(series_ticker=series, status=status, limit=1000,
                                          fetch_all=(status == MarketStatus.SETTLED), **extra)
            for m in (ms or []):
                tk = getattr(m, "ticker", "") or ""
                if not tk:
                    continue
                markets[tk.upper()] = _market_quote_dict(m)
                entry = {"ticker": tk, "title": getattr(m, "title", None)}
                (fight_markets if series == "KXUFCFIGHT" else distance_markets).append(entry)
    await _merge_raw_market_fields(client, markets, series_list=UFC_SERIES)   # exchange_index (SDK-dropped) from raw
    fight_idx = U.build_kalshi_fight_index(fight_markets)
    fight_idx = U.attach_distance_tickers(fight_idx, distance_markets)
    dates = frozenset(k[0] for k in fight_idx)          # ISO dates FROM THE TICKER (card-local), never occurrence
    return execution.MarketContext({}, {}, {}, dates, markets, fight_index=fight_idx)


async def fetch_tennis_market_context(client, now_ts: int, series: str) -> execution.MarketContext:
    """tennis (2026-09-04): fetch OPEN + recent-SETTLED markets for ONE match series (KXATPMATCH or KXWTAMATCH) and
    build the tennis MarketContext. MIRRORS fetch_ufc_market_context -- SAME `client.get_markets`, SAME `_market_quote_dict`
    quote fields, SAME raw `exchange_index` merge. ★ the `title` ("{Player} wins") IS on the SDK MarketModel object
    (getattr, NO raw merge -- like UFC); `exchange_index` is NOT on the object and MUST be raw-merged (tennis matches are
    exchange_index=3 = shard 3). The good news about `title` does NOT carry to `exchange_index` -- they are merged by
    DIFFERENT paths. The join date is the card-LOCAL date in the ticker (never occurrence_datetime). Match-winner only."""
    from pykalshi import MarketStatus
    markets: dict = {}
    match_markets: list = []       # [{ticker, title}] -> build_kalshi_match_index (title carries the player full name)
    min_ts = int(now_ts) - _SETTLED_LOOKBACK_SEC
    for status, extra in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await client.get_markets(series_ticker=series, status=status, limit=1000,
                                      fetch_all=(status == MarketStatus.SETTLED), **extra)
        for m in (ms or []):
            tk = getattr(m, "ticker", "") or ""
            if not tk:
                continue
            markets[tk.upper()] = _market_quote_dict(m)
            match_markets.append({"ticker": tk, "title": getattr(m, "title", None)})
    await _merge_raw_market_fields(client, markets, series_list=(series,))   # exchange_index (SDK-dropped) from raw
    match_idx = TN.build_kalshi_match_index(match_markets)
    dates = frozenset(match_idx.keys())                 # ISO dates FROM THE TICKER (card-local), never occurrence
    return execution.MarketContext({}, {}, {}, dates, markets, match_index=match_idx)


async def fetch_atp_market_context(client, now_ts: int) -> execution.MarketContext:
    return await fetch_tennis_market_context(client, now_ts, TENNIS_SERIES["atp"])


async def fetch_wta_market_context(client, now_ts: int) -> execution.MarketContext:
    return await fetch_tennis_market_context(client, now_ts, TENNIS_SERIES["wta"])


async def fetch_structural_market_context(client, now_ts: int, cfg) -> execution.MarketContext:
    """rung 1 (2026-09-06): fetch OPEN + recent-SETTLED KX{X}GAME markets for a structural category (nfl/nba/nhl/
    wnba/cfb) and build the (date, team-pair) game index. MIRRORS fetch_tennis_market_context -- SAME
    `client.get_markets`, SAME `_market_quote_dict` quote fields, SAME raw `exchange_index` merge. MONEYLINE ONLY
    (Jack ruled): fetches ONLY the game series (no total/spread this pass). No title read -- the structural join is
    on the ticker's team CODES + date (via the team map), not a title. Returns a MarketContext with structural_index
    set (ml/tot/spr/fight/match left empty)."""
    from pykalshi import MarketStatus
    markets: dict = {}
    tickers: list = []
    min_ts = int(now_ts) - _SETTLED_LOOKBACK_SEC
    for status, extra in ((MarketStatus.OPEN, {}), (MarketStatus.SETTLED, {"min_close_ts": min_ts})):
        ms = await client.get_markets(series_ticker=cfg.game_series, status=status, limit=1000,
                                      fetch_all=(status == MarketStatus.SETTLED), **extra)
        for m in (ms or []):
            tk = getattr(m, "ticker", "") or ""
            if not tk:
                continue
            markets[tk.upper()] = _market_quote_dict(m)
            tickers.append(tk)
    await _merge_raw_market_fields(client, markets, series_list=(cfg.game_series,))   # exchange_index (SDK-dropped) from raw
    game_idx = SS.build_game_index(tickers, cfg)
    dates = frozenset(k[0] for k in game_idx)          # ISO dates FROM THE INDEX (never occurrence_datetime)
    return execution.MarketContext({}, {}, {}, dates, markets, structural_index=game_idx)


def _structural_ctx_builder(cfg):
    """A category-keyed builder closure carrying `cfg` (the ctx registry maps category -> builder(client, now_ts))."""
    async def _b(client, now_ts: int) -> execution.MarketContext:
        return await fetch_structural_market_context(client, now_ts, cfg)
    return _b


# ── signal source: attached whales' /positions -> entry CopySignals (chokepoint dedups already-placed) ───
def _stable_entry_key(condition_id: str, outcome_index) -> str:
    """A restart-STABLE entry key for a /positions row, which carries NO fill tx_hash/ts. The whale's holding of a
    specific (condition_id, outcome_index) IS the stable identity -> one copy per position. LIMITATION (noted,
    acceptable for the path-proving first order): a close-then-re-open of the SAME (condition_id, outcome_index)
    reuses the key -> the re-entry is not re-copied. A later refinement keys on an /activity tx_hash."""
    return "pos:%s:%s" % (condition_id or "", outcome_index)


def positions_to_entry_signals(rows, wallet: str) -> list:
    """Genuinely-open /positions rows for one whale -> entry CopySignals (is_exit=False). Reuses
    paper.is_genuinely_open + paper.pos_outcome_index. DEDUPES within the book on (condition_id, outcome_index) so a
    duplicate /positions row cannot emit two identical signals in one cycle (adversarial-review obs). EXITS later."""
    out, seen = [], set()
    for p in rows:
        if not paper.is_genuinely_open(p):
            continue
        cid = str(getattr(p, "condition_id", "") or "")
        oidx = paper.pos_outcome_index(p)
        if (cid, oidx) in seen:
            continue
        seen.add((cid, oidx))
        out.append(execution.CopySignal(
            wallet=wallet, slug=str(getattr(p, "slug", "") or ""),
            outcome=str(getattr(p, "outcome", "") or ""), condition_id=cid, outcome_index=oidx,
            signal_id=execution.stable_signal_id(wallet, cid, oidx, _stable_entry_key(cid, oidx)),
            is_exit=False, title=str(getattr(p, "title", "") or "")))   # tennis pair-keying reads title; mlb/ufc ignore it
    return out


# ── Option D (whale-exit) detection inputs: /activity SELLs + /positions size-reductions ───────────────
# The exit path is: /activity SELL (trigger) + /positions size-reduction (confirmation), matched in-window by
# execution.detect_exit_signals (BOTH agree or MISSED -- no single-signal fallback). These two PURE adapters build
# its inputs from the real ActivityRow / PositionRow shapes; the caller (the live loop) owns the prior /positions
# snapshot (Fork A: in-memory, boot-seeded). Neither adapter places or writes -- they only shape data.
_REDUCTION_EPS = 1e-6
# Fork D: the max lag between a whale's /activity SELL (the actual sell ts) and our /positions-reduction DETECTION
# (a poll-time ts) for detect_exit_signals to PAIR them. 300s covers data-api propagation + one poll; too tight
# misses confirmations, too loose is negligible risk (paired only on exact condition_id + outcome_index). Tunable.
_EXIT_WINDOW_SEC = 300


def activity_sells_from_activity(rows, wallet: str) -> list:
    """A whale's /activity rows -> the SELL dicts detect_exit_signals wants: {wallet, condition_id, outcome_index,
    ts, tx_hash}. Keeps ONLY discretionary sells: type=='TRADE' AND side=='SELL'. type=='TRADE' excludes REDEEM /
    settlement events (a redemption is not a discretionary exit); the SELL requirement then pairs with a /positions
    reduction. Uses the passed `wallet` (the attachment identity, mirroring positions_to_entry_signals) so the
    emitted key matches the reduction side's wallet exactly. tx_hash is the PER-SELL identity -> the exit's
    signal_id (distinct across re-sells, stable across restart) -- also the Finding-5 lever (a later sub-rung)."""
    out = []
    for r in rows:
        if str(getattr(r, "type", "") or "").upper() != "TRADE":
            continue
        if str(getattr(r, "side", "") or "").upper() != "SELL":
            continue
        cid = str(getattr(r, "condition_id", "") or "")
        if not cid:
            continue
        out.append({"wallet": wallet, "condition_id": cid,
                    "outcome_index": int(getattr(r, "outcome_index", 0) or 0),
                    "ts": int(getattr(r, "timestamp", 0) or 0),
                    "tx_hash": str(getattr(r, "transaction_hash", "") or "")})
    return out


def snapshot_open_positions(rows) -> dict:
    """Snapshot a whale's GENUINELY-OPEN /positions as {(condition_id, outcome_index): (size, slug, outcome)} --
    the prior-cycle baseline detect_position_reductions diffs against (Fork A: the live loop holds this in memory,
    boot-seeded so the FIRST cycle emits no reduction). slug/outcome are carried so a leg that is later FULLY SOLD
    (absent next cycle -> no current row to read them from) can still produce the confirmation payload from the
    PRIOR snapshot. Genuinely-open only (symmetric with positions_to_entry_signals); a settlement transition that
    also shrinks a leg is filtered by the SELL requirement in detect_exit_signals, not here."""
    snap: dict = {}
    for p in rows:
        if not paper.is_genuinely_open(p):
            continue
        cid = str(getattr(p, "condition_id", "") or "")
        oidx = paper.pos_outcome_index(p)
        key = (cid, oidx)
        size, _slug, _out = snap.get(key, (0.0, "", ""))
        snap[key] = (size + float(getattr(p, "size", 0.0) or 0.0),
                     str(getattr(p, "slug", "") or ""), str(getattr(p, "outcome", "") or ""))
    return snap


def detect_position_reductions(prior: dict, rows, wallet: str, now_ts: int) -> list:
    """Diff current genuinely-open /positions against `prior` (a snapshot_open_positions map from a previous cycle)
    -> reduction events {wallet, condition_id, outcome_index, ts, slug, outcome} for each leg whose genuinely-open
    size DROPPED (cur < prior - eps), INCLUDING a drop to 0 (fully sold -> vanished). `ts` is the DETECTION time
    (now_ts) -- a /positions row carries no change timestamp -- so detect_exit_signals' window must cover the
    (detection - sell) lag. slug/outcome are taken from the PRIOR snapshot so a vanished leg still matches its
    Kalshi ticker. PURE (reads only the passed snapshot + rows). A settlement/redemption also shrinks a position
    but has NO co-occurring SELL, so requiring BOTH in detect_exit_signals filters it -- this adapter does not try
    to distinguish it (the SELL is the authoritative discretionary-exit signal)."""
    cur = snapshot_open_positions(rows)
    out = []
    for key, (prev_size, slug, outcome) in prior.items():
        cid, oidx = key
        now_size = cur.get(key, (0.0, "", ""))[0]
        if now_size < float(prev_size) - _REDUCTION_EPS:
            out.append({"wallet": wallet, "condition_id": cid, "outcome_index": oidx,
                        "ts": int(now_ts), "slug": slug, "outcome": outcome})
    return out


# ── the placement seam (option b): POST the gate-approved body VERBATIM ────────────────────────────────
def make_place_fn(client):
    """Return an async `place_fn(decision) -> FillEvent` that POSTs the APPROVED `decision.body` verbatim and maps
    the response with the REUSED pure mapper. Raises KalshiNoFill (benign 0-fill / FOK-kill) or OrderPlacementError
    (loud). ** A KalshiError maps benign-vs-loud (the reused split); ANY OTHER transport exception (httpx
    timeout/connect -- NOT a KalshiError) maps to OrderPlacementError so a network failure feeds the latch path +
    gets a journal row, never escaping the never-die loop un-latched (adversarial-review HIGH). ** A timeout is
    POSSIBLY-PLACED; the pending-first row keeps its coid so it is not silently re-driven. fill_event_from_v2_response
    is OUTSIDE the try (a mapper bug raises loudly, not masked as a rejection). The POST try/except is the ONE
    duplication of place_order's wrapper (see the note in kalshi_live.py)."""
    async def place_fn(decision):
        from pykalshi.exceptions import KalshiError
        try:
            resp = await client.post(_V2_ORDERS_PATH, decision.body)          # POST the APPROVED body verbatim
        except KalshiError as e:
            if _is_benign_fok_nofill(e):
                raise KalshiNoFill("kalshi FOK order %s did not fill (insufficient resting volume)"
                                   % decision.client_order_id) from e
            raise OrderPlacementError("kalshi V2 POST rejected for %s (%s x%s): %s"
                                      % (decision.kalshi_ticker, decision.leg, decision.count, e)) from e
        except Exception as e:                                                # noqa: BLE001 -- transport error -> LOUD, possibly-placed
            raise OrderPlacementError("kalshi V2 POST TRANSPORT error for %s (%s x%s) -- POSSIBLY PLACED: %r"
                                      % (decision.kalshi_ticker, decision.leg, decision.count, e)) from e
        return fill_event_from_v2_response(
            resp, symbol="%s:%s" % (decision.kalshi_ticker, decision.leg),
            side=("sell" if decision.is_exit else "buy"),
            fallback_price=decision.price, fallback_order_id=decision.client_order_id, outcome=decision.leg)
    return place_fn


def _is_auth_failure(e) -> bool:
    """A 401/403 on the order path. Checks BOTH the structured status (status_code/code) AND the string, on the
    exception AND its wrapped cause -- robust to how pykalshi surfaces auth failures (adversarial-review hardening)."""
    for x in (e, getattr(e, "__cause__", None)):
        if x is None:
            continue
        code = str(getattr(x, "status_code", "") or getattr(x, "code", "") or getattr(x, "error_code", "") or "")
        if code in ("401", "403"):
            return True
        t = str(x).lower()
        if "401" in t or "403" in t or "unauthor" in t or "forbidden" in t:
            return True
    return False


def account_active_categories(conn, account_id: str, *, fallback_category: str | None = None) -> list:
    """EVERY active category on an account (from pm_subdivision, active=1) so a KEYPAIR-WIDE failure latches the
    WHOLE account, not just the caller's category. M2 (auth 401/403: the dead keypair is dead for every category)
    and M3 (a full-account boot-reconcile mismatch is the whole book) both need this: N distinct accounts are safe,
    but a 2nd category on ONE account shares the keypair, so a per-category latch leaves the sibling POSTing/arming
    on a failure that is account-wide. FAIL-SAFE: on ANY read error return just [fallback_category] -- never [] and
    never fail-OPEN (an auth-latch that could not read the category list must still latch the caller). Unions the
    fallback so a just-created sub is covered even if the read races it. Deterministic (sorted)."""
    cats: set = set()
    if fallback_category:
        cats.add(fallback_category)
    try:
        for r in conn.execute("SELECT DISTINCT category FROM pm_subdivision WHERE account_id=? AND active=1",
                              (account_id,)):
            c = r[0]
            if c:
                cats.add(c)
    except Exception as e:  # noqa: BLE001 -- fail-SAFE: latch at least the caller's category (never fail-open)
        _LOG.warning("account_active_categories(%s): pm_subdivision read failed -> latching fallback category only "
                     "(fail-safe, never fewer): %s", account_id, e)
    return sorted(cats)


def category_volume_order(conn, account_id: str, cats: list, *, now_ts: int, window_days: int = 30) -> list:
    """RANK `cats` for the per-cycle CLAIM ORDER: PROVEN-VOLUME FIRST, replacing the inherited alphabetical order
    (Jack RULED 2026-09-06). 'Volume' = THIS account's committed ENTRY dollars per category over the last
    `window_days`, from pm_subdivision_order on the SAME leg-aware committed-$ basis the Journal seeds from
    (yes: count*price ; no: count*(1-price)). Ranked DESC; TIEBREAK alphabetical. A category with NO recent
    orders (a NEW category) sums 0 -> ranks LAST, so an unproven category never crowds out a proven high-volume one.

    ★ POLICY + ITS CONSEQUENCE (written down, not incidental): under Option C the account's ONE $150/day + 50-order
    cap is SHARED across all its categories and claimed in THIS order each cycle. When the cap BINDS, the LATE
    (low-volume / quiet / new) categories are STARVED SYSTEMATICALLY. That is the ACCEPTED TRADE -- proven high-
    volume categories get first claim; the old alphabetical order starved whoever sorted last (atp first, wta last),
    which nobody chose.

    ★ MEASURED, not static -- the choice, justified: a static priority list is wrong the moment volumes shift and
    needs a hand-edit; a measured rank adapts. The cost of MEASURED is STALENESS: the `window_days` window means a
    surging category does not out-rank an established one until it accrues window volume, and a quiet one keeps its
    rank until its window decays; and the rank is recomputed at TASK START (engine restart), NOT every cycle -- so it
    lags real-time by up to the window + the restart cadence. Deliberate: 'proven' should be slow to change, and
    restarts are frequent (any deploy). Read-only, bounded (today-window, indexed by account); called ONCE at task
    start, NEVER on the order hot path. Any error -> deterministic alphabetical fallback (never raises)."""
    try:
        since = int(now_ts) - int(window_days) * 86400
        vol = {c: 0.0 for c in cats}
        for r in conn.execute(
            "SELECT category, COALESCE(SUM(CASE WHEN outcome_leg='yes' THEN submitted_count*submitted_price "
            "  ELSE submitted_count*(1.0-submitted_price) END), 0) AS usd "
            "FROM pm_subdivision_order WHERE account_id=? AND dry_run=0 AND outcome_status='filled' AND is_exit=0 "
            "  AND response_ts>=? GROUP BY category", (account_id, since)).fetchall():
            if r["category"] in vol:
                vol[r["category"]] = float(r["usd"] or 0.0)
        return sorted(cats, key=lambda c: (-vol.get(c, 0.0), c))
    except Exception as e:  # noqa: BLE001 -- a ranking read must NEVER stop the driver; fall back to deterministic order
        _LOG.warning("category_volume_order(%s) failed -> alphabetical fallback: %s", account_id, e)
        return sorted(cats)


# ── the durable live-order journal (dry_run=0): PENDING insert (pre-POST) + finalize (post-POST) ───────
def _record_order(conn, sub, signal, decision, *, outcome_status, fill=None, error_detail=None, now_ts):
    """INSERT the live-order row (dry_run=0). Called PRE-POST with outcome_status='submitting' (no fill) so the coid
    is journaled BEFORE the POST -- gate-4 dedup then prevents a re-drive on any failure. `fill_price` is the
    OUTCOME-LEG per-contract price (FillEvent.price is leg-corrected: 1-yes for a NO leg)."""
    conn.execute(
        "INSERT INTO pm_subdivision_order (account_id, category, wallet, condition_id, outcome_index, signal_id, "
        " client_order_id, ticker, order_side, outcome_leg, is_exit, submitted_count, submitted_price, "
        " time_in_force, outcome_status, broker_order_id, fill_count, fill_price, fee, error_detail, close_source, "
        " dry_run, submitted_ts, response_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
        (sub.account_id, sub.category, signal.wallet, signal.condition_id, signal.outcome_index, signal.signal_id,
         decision.client_order_id, decision.kalshi_ticker, decision.body.get("side"), decision.leg,
         1 if signal.is_exit else 0, decision.count, decision.price, decision.body.get("time_in_force"),
         outcome_status, (getattr(fill, "order_id", None) if fill else None),
         (float(getattr(fill, "qty", 0.0)) if fill else None), (float(getattr(fill, "price", 0.0)) if fill else None),
         (float(getattr(fill, "fee", 0.0)) if fill else None), error_detail,
         getattr(signal, "close_source", None),   # 'opposed' for a cancellation-by-disagreement; None for entry/whale-exit
         int(now_ts), int(now_ts)))
    if hasattr(conn, "commit"):
        conn.commit()


def _finalize_order(conn, coid, outcome_status, *, fill=None, error_detail=None, now_ts):
    """UPDATE the PENDING row (by coid) with the POST outcome + fill facts. The pending row was INSERTed pre-POST for
    idempotency; this stamps the result (filled/no_fill/error). A finalize FAILURE leaves the row 'submitting' -- the
    coid stays journaled (no re-drive) and boot-reconcile adjudicates it (the bounded K9 residual)."""
    conn.execute(
        "UPDATE pm_subdivision_order SET outcome_status=?, broker_order_id=?, fill_count=?, fill_price=?, fee=?, "
        " error_detail=?, response_ts=? WHERE client_order_id=? AND dry_run=0",
        (outcome_status, (getattr(fill, "order_id", None) if fill else None),
         (float(getattr(fill, "qty", 0.0)) if fill else None), (float(getattr(fill, "price", 0.0)) if fill else None),
         (float(getattr(fill, "fee", 0.0)) if fill else None), error_detail, int(now_ts), coid))
    if hasattr(conn, "commit"):
        conn.commit()


# ── the ASYNC arm-gated cycle (async twin of run_arm_gated_cycle; reuses evaluate + read_arm_verdict) ──
async def run_live_arm_gated_cycle(conn, sub, signals, ctx, journal, now_ts, *, place_fn, shard_balances=None,
                                   venue_exposure=None, legacy_db_path=None, log=None):
    """Every signal through the chokepoint's 8 gates (`execution.evaluate`), then -- for a gate-passing order and
    ONLY IF ARMED (re-read `arm.read_arm_verdict` immediately before EACH order) -- journal a PENDING row, await
    `place_fn` (the POST), and finalize the outcome. DISARMED blocks everything. A loud error (incl. a wrapped
    transport timeout) records + latches (consecutive; 401/403 -> whole-account auth-failure + manual-exit). NB:
    evaluate() already committed the entry budget to the Journal counters at would-place time (execution.py:298-299);
    the cycle does NOT re-commit (that would double-count the daily/open USD caps)."""
    log = log or _LOG
    signals = list(signals)
    n_would = n_skip = n_reject = placed = disarm_blocked = errors = n_shard_underfunded = 0
    consec_err = 0
    ceiling_latched = False
    for s in signals:
        d = execution.evaluate(s, sub, ctx, journal, conn, now_ts,               # gates + 6b (shard) + 6 (venue exposure)
                               shard_balances=shard_balances, venue_exposure=venue_exposure,
                               legacy_db_path=legacy_db_path)
        if d.status != "dry_run_would_place":
            if d.status.startswith("skip:"):
                n_skip += 1
                if d.status == "skip:shard_underfunded":                         # SURFACED (not latched): a fundable-later gap
                    n_shard_underfunded += 1
                    log.warning("pm_live_driver: skip:shard_underfunded (%s x%s) -- funding gap on the market's shard, "
                                "NOT a fault: %s", d.kalshi_ticker, d.count, d.reason)
                elif d.status == "skip:exposure_unknown":                        # R7: venue exposure unreadable -> fail-closed
                    log.warning("pm_live_driver: skip:exposure_unknown -- venue open-exposure read failed; "
                                "sizing against an unknown book is refused (fail-closed): %s", d.reason)
            else:
                n_reject += 1
                # ★ R7.d: the 4TH latching trigger, WIRED HERE. It was DEAD CODE -- arm.latch_count_ceiling
                # existed since R5 but NO caller ever fired it (neither this async cycle nor the R5 sync
                # run_arm_gated_cycle). The orders/day ceiling (gate 8) is a LATCHING circuit-breaker on Jack's
                # R5 list: on the reject, DISARM + require a human --clear-latch. WITHOUT it a runaway -- e.g.
                # the K9 re-drive class the R7.c review fixed -- would REJECT at gate 8 every ~poll_sec forever
                # while the arm state still read ARMED (silently ceiling-blocked, never surfaced). Gate on
                # ARMED: if already disarmed the ceiling is moot (off is off, and the count is phantom
                # would-place accounting); an armed account AT its ceiling latches so the NEXT order is blocked
                # and a human must acknowledge. Gate 8 is entry-only, so this NEVER fires for a reduce_only exit.
                if d.status == "reject:count_ceiling" and arm.read_arm_verdict(
                        sub.account_id, sub.category, legacy_db_path=legacy_db_path).armed:
                    arm.latch_count_ceiling(sub.account_id, sub.category,
                                            count=journal.orders_today(sub.account_id, sub.category),
                                            cap=sub.max_orders_per_day, legacy_db_path=legacy_db_path)
                    log.warning("pm_live_driver: orders/day ceiling -> LATCHED count_ceiling (account DISARMED "
                                "until a human --clear-latch): %s", d.reason)
                    ceiling_latched = True
                    break                                                   # disarmed; stop the cycle
            continue
        n_would += 1
        if not arm.read_arm_verdict(sub.account_id, sub.category, legacy_db_path=legacy_db_path).armed:  # RE-READ per order
            disarm_blocked += 1
            continue                                                    # off is off (entries AND exits)
        _record_order(conn, sub, s, d, outcome_status="submitting", now_ts=now_ts)   # ★ PENDING-first: coid journaled PRE-POST
        try:
            fill = await place_fn(d)                                    # the ONE seam: POST the approved body
        except KalshiNoFill:                                            # benign 0-fill -> finalize, no latch (coid stays journaled)
            _finalize_order(conn, d.client_order_id, "no_fill", now_ts=now_ts)
            continue
        except OrderPlacementError as e:                               # LOUD (incl. a wrapped transport timeout) -> finalize + latch
            errors += 1; consec_err += 1
            _finalize_order(conn, d.client_order_id, "error", error_detail=repr(e)[:400], now_ts=now_ts)
            if _is_auth_failure(e):
                # M2 (2026-09-02): a 401/403 is the KEYPAIR dead -> latch EVERY active category on the account, not
                # just this one. With one category per account this is a no-op; with a 2nd category on one account
                # it stops the sibling from POSTing on the same dead auth. arm.latch_auth_failure already loops the
                # list; the only gap was the caller passing [sub.category]. account_active_categories fail-SAFEs to
                # [sub.category] if the sub-list read fails, so a latch can never latch FEWER than the caller.
                cats = account_active_categories(conn, sub.account_id, fallback_category=sub.category)
                arm.latch_auth_failure(sub.account_id, cats,
                                       detail="order-path auth failure (dead keypair; latching %d account categ.: %s): %s"
                                              % (len(cats), ",".join(cats), repr(e)[:150]), legacy_db_path=legacy_db_path)
                break                                                   # WHOLE account disarmed; stop the cycle
            if consec_err >= 3:
                arm.latch_consecutive_errors(sub.account_id, sub.category, n=consec_err, legacy_db_path=legacy_db_path)
                break
            continue
        try:
            _finalize_order(conn, d.client_order_id, "filled", fill=fill, now_ts=now_ts)
        except Exception as e:                                         # noqa: BLE001 -- the bounded K9 residual: order LIVE, row stays 'submitting'
            log.error("pm_live_driver K9: order PLACED at Kalshi but the 'filled' journal update FAILED for coid=%s "
                      "(row stays 'submitting'; coid journaled -> NOT re-driven; boot-reconcile adjudicates): %s",
                      d.client_order_id, e)
        placed += 1
        consec_err = 0
    return {"account_id": sub.account_id, "category": sub.category, "n_signals": len(signals),
            "n_would_place": n_would, "placed": placed, "n_disarm_blocked": disarm_blocked, "errors": errors,
            "n_skip": n_skip, "n_reject": n_reject, "n_shard_underfunded": n_shard_underfunded,
            "posts_sent": placed, "ceiling_latched": ceiling_latched}


# ── boot-reconcile against the AUTHENTICATED portfolio (async fetch -> sync reconcile with a lambda) ────
def _raiser(exc):
    """A zero-arg callable that re-raises `exc`. Used so a FAILED async portfolio fetch flows through
    reconcile_account's OWN fail-safe latch (a sync fetcher raising -> latch) rather than duplicating the latch."""
    def _f():
        raise exc
    return _f


async def run_boot_reconcile(conn, sub, client, *, legacy_db_path=None):
    """Fetch the account's ACTUAL Kalshi positions (authenticated) and reconcile them against the journal. The async
    fetch happens HERE; `boot_reconcile.reconcile_account` is SYNC and gets a plain lambda returning the fetched
    list. A mismatch latches boot_reconcile_mismatch. A FETCH FAILURE hands reconcile_account a raising fetcher
    (`_raiser`) so its own fail-safe latch fires. (A JOURNAL-read fault raises OUT of here -- the caller
    force-latches; see scheduled_pm_live_loop.)

    ★ M3 (2026-09-02): the comparison is ACCOUNT-WIDE, so the LATCH must be too. We pass `latch_categories` = EVERY
    active category on the account (account_active_categories, fail-SAFE to [sub.category]); a whole-book mismatch or
    read failure then disarms every category on the shared keypair, closing the 2nd-category-on-one-account
    missed-latch (the sibling can no longer arm/trade against an unreconciled book). Under Option C this runs ONCE
    per account; under the current per-task model each task passes the same superset (redundant, still safe)."""
    async def _fetch():
        return list(await client.portfolio.get_positions(fetch_all=True))
    try:
        positions = await _fetch()
    except Exception as e:                                             # noqa: BLE001 -- fail-safe: cannot read -> latch
        fetch = _raiser(e)
    else:
        fetch = (lambda: positions)
    cats = account_active_categories(conn, sub.account_id, fallback_category=sub.category)   # M3: whole-account latch
    return boot_reconcile.reconcile_account(conn, sub.account_id, sub.category,
                                            fetch_positions=fetch, legacy_db_path=legacy_db_path,
                                            latch_categories=cats)


# ── R-d: the AUTHENTICATED settlements read (raw payload -> parsed records) ─────────────────────────────
async def fetch_settlements(client) -> list:
    """READ Kalshi settlements via a RAW `client.get('/portfolio/settlements?limit=200')` -> [SettlementRecord].
    RAW (not the typed pykalshi model) because the raw payload carries `market_result` the SDK object drops -- the
    same 'read the raw payload' lesson as exchange_index. Parsing/validation is delegated to
    settlement.parse_settlements (a non-dict result -> [], never a mis-parse). This module imports NO broker; the
    caller passes the broker's authenticated raw client."""
    r = client.get("/portfolio/settlements?limit=200")
    if inspect.isawaitable(r):
        r = await r
    return settlement.parse_settlements(r)


# ── category -> the async market-context builder for that category's Kalshi series. The driver body is otherwise
# category-agnostic; the ONE category-specific thing a cycle needs is the right series catalog. mlb is registered
# here; the UFC matcher (Workstream B) registers 'ufc'. Injectable into scheduled_pm_live_loop for tests (a fake
# builder returns a canned MarketContext with NO pykalshi import). A category with NO builder is SKIPPED every cycle
# (fail-SAFE: no catalog -> no signals -> nothing placed). ★ M1 (Option C) seam: this is what lets ONE account task
# iterate N categories, each matched against its own catalog, while the account-level Journal/venue/shard reads are
# shared once per cycle (the shared Journal is what enforces the account open-cap JOINTLY across categories).
CATEGORY_CTX_BUILDERS = {"mlb": fetch_market_context, "ufc": fetch_ufc_market_context,   # B2: ufc registered
                         "atp": fetch_atp_market_context, "wta": fetch_wta_market_context}   # tennis: per-series builders
# rung 1 (2026-09-06): nfl/nba/nhl/wnba/cfb share the structural builder (one series each). A category with NO builder
# is still SKIPPED every cycle (fail-SAFE: no catalog -> no signals -> nothing placed).
for _scat in ("nfl", "nba", "nhl", "wnba", "cfb"):
    CATEGORY_CTX_BUILDERS[_scat] = _structural_ctx_builder(SS.LEAGUES[_scat])


# ── the engine task (mirrors main.py:_scheduled_poly_kalshi_loop) ──────────────────────────────────────
async def scheduled_pm_live_loop(pm_db_path, broker, positions_client, *, account_id, categories=None,
                                 category=None, poll_sec=7.0, index_refresh_sec=900.0, legacy_db_path=None,
                                 log=None, ctx_builders=None, _prior_snapshots=None, _max_cycles=None):
    """The engine-side driver task -- ONE task PER ACCOUNT, iterating that account's categories (M1/Option C, 2026-09-02).
    `positions_client` (fetch_positions_book, the paper poller's read) is INJECTED by the caller (the main.py block
    passes the real one; box-scratch a fake). `categories` is the account's category list (roster-ordered); the legacy
    single `category=` is still accepted (== categories=[category]) so existing callers/tests do not change.

    ★ WHY ONE TASK PER ACCOUNT (M1): the account open-exposure cap (gate 6) is ACCOUNT-keyed. Two SEPARATE tasks on
    one account each build their own per-cycle Journal + venue read and cannot see each other's in-flight placements,
    so they race the shared cap (up to ~2x over-place in one overlapping window). ONE task, iterating its categories
    SEQUENTIALLY and sharing ONE per-cycle Journal + ONE venue-exposure read, enforces the account cap JOINTLY with NO
    lock and NOTHING added to the order hot path (gate 6/evaluate/POST are unchanged): category B's evaluate sees
    category A's commit_would_place via the shared account-keyed Journal. It also never POSTs two same-account orders
    concurrently, so pykalshi's (un-vendored, unprovable) concurrent-POST safety is never relied on.

    BOOT: per-category index build + per-category settlement scan, then ONE ACCOUNT-WIDE boot-reconcile that latches
    EVERY category on a mismatch/fault (M3). STEADY: read the ACCOUNT-level shard + venue-exposure ONCE per cycle,
    build ONE account Journal, then for EACH category refresh its catalog, read its whales, run the async arm-gated
    cycle against the shared Journal, and surface its own sustained-underfunding alarm. A bad cycle is logged and
    NEVER kills the loop. `_max_cycles` bounds the loop for box-scratch (None = forever)."""
    log = log or _LOG
    cats = list(categories) if categories else ([category] if category else [])
    if not cats:
        log.error("pm_live_driver: scheduled_pm_live_loop for %s got NO categories -> nothing to drive (returning)",
                  account_id)
        return
    # ★ CYCLE ORDER (Jack RULED 2026-09-06): PROVEN-VOLUME FIRST, replacing the inherited alphabetical order, so the
    # busy categories claim the shared account cap before a quiet/new one. Computed ONCE at task start (refreshes on
    # restart) from a read-only bounded query; see category_volume_order for the policy + the ACCEPTED starvation
    # trade + the static-vs-measured justification. Any error -> deterministic alphabetical (never stops the driver).
    import sqlite3 as _sqlite3
    try:
        _vc = _sqlite3.connect("file:%s?mode=ro" % pm_db_path, uri=True); _vc.row_factory = _sqlite3.Row
        try:
            cats = category_volume_order(_vc, account_id, cats, now_ts=int(_time.time()))
        finally:
            _vc.close()
    except Exception as e:  # noqa: BLE001 -- the ranking must never stop the driver
        cats = sorted(cats)
        log.warning("pm_live_driver: volume-order connection failed for %s -> alphabetical %s: %s", account_id, cats, e)
    log.info("pm_live_driver: cycle order (proven-volume-first) for %s = %s", account_id, cats)
    builders = ctx_builders if ctx_builders is not None else CATEGORY_CTX_BUILDERS
    client = broker._read._client
    # ★ PER-CATEGORY state (M1 re-scopings): a single task must NOT share these across categories --
    #   ctx/last_idx (#17, functional): each category needs its OWN Kalshi series catalog + refresh timer;
    #   last_settle (#18, functional): a per-category settlement-scan throttle (a shared timer would let one
    #     category's scan starve another's cadence);
    #   consec_underfunded (#14, SAFETY): a per-category sustained-underfunding ALARM -- a SHARED counter would let
    #     one category's placements silently reset a sibling's genuine shard-starvation alarm (a safety check that
    #     stops checking); (consec_err (#15) is defended by construction -- it is a local INSIDE
    #     run_live_arm_gated_cycle, invoked once per category, so it never shares.)
    ctx_by_cat: dict = {c: None for c in cats}
    last_idx_by_cat: dict = {c: 0.0 for c in cats}
    last_settle_by_cat: dict = {c: 0.0 for c in cats}
    consec_underfunded_by_cat: dict = {c: 0 for c in cats}
    # ★ Option D (Fork A1): the PRIOR /positions snapshot, held IN MEMORY. ★ M1 re-scoping #16 (SAFETY): keyed by
    # (category, wallet), NOT wallet alone -- one wallet may be attached to TWO categories on this account, and a
    # wallet-only key would MERGE the two books under one task -> corrupt reduction/exit detection. EMPTY at boot, so
    # cycle 1 emits NO reduction (nothing to diff) and seeds it; a restart re-derives (a straddling reduction = a
    # MISSED exit, accepted). `_prior_snapshots` is a TEST seam (like `_max_cycles`): pass a dict to inspect the
    # (category, wallet) keys after a bounded run; production leaves it None -> a fresh dict.
    prior_snapshots: dict = _prior_snapshots if _prior_snapshots is not None else {}
    # BOOT: per-category catalog build.
    for c in cats:
        _bld = builders.get(c)
        if _bld is None:
            log.warning("pm_live_driver: no market-context builder for category %r (account %s) -> that category is "
                        "SKIPPED (fail-safe: no catalog, no signals). Register it before enabling.", c, account_id)
            continue
        try:
            ctx_by_cat[c] = await _bld(client, int(_time.time())); last_idx_by_cat[c] = _time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("pm_live_driver: boot index build failed for %s/%s: %s", account_id, c, e)
    with db.connect(pm_db_path) as conn:
        subs = {c: execution.sub_config_from_row(conn.execute(
            "SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (account_id, c)).fetchone()) for c in cats}
        # ★ R-d BOOT SETTLEMENT-SCAN (per category) -- runs BEFORE the account-wide boot-reconcile so a position that
        # SETTLED WHILE THE ENGINE WAS DOWN is booked FLAT on the way up and reconcile comes up CLEAN. FAIL-SAFE: a
        # fetch failure books nothing -> reconcile latches R-b (the safe fallback). Booking never places an order.
        for c in cats:
            try:
                bsumm = settlement.book_settlements(conn, account_id, c,
                                                    await fetch_settlements(client), now_ts=int(_time.time()))
                if bsumm["n_booked"]:
                    log.info("pm_live_driver: BOOT settlement-scan booked %d terminal-close(s) for %s/%s -- first: %s",
                             bsumm["n_booked"], account_id, c, bsumm["booked"])
            except Exception as e:  # noqa: BLE001 -- a settlements-read failure must not stop boot; reconcile latches R-b
                log.warning("pm_live_driver: BOOT settlement-scan failed for %s/%s (reconcile may latch R-b): %s",
                            account_id, c, e)
        _bootnow = _time.time()
        for c in cats:
            last_settle_by_cat[c] = _bootnow                   # periodic scan waits a full interval after boot
        # ★ M3: ONE ACCOUNT-WIDE boot-reconcile. The comparison is account-wide (journal_signed_positions reads every
        # category; the Kalshi side is the whole book); run_boot_reconcile passes account_active_categories so a
        # mismatch/read-failure latches EVERY category on the shared keypair -- not just the one whose task ran it.
        try:
            res = await run_boot_reconcile(conn, subs[cats[0]], client, legacy_db_path=legacy_db_path)
            log.info("pm_live_driver: boot-reconcile account=%s reconciled=%s latched=%s latched_categories=%s",
                     account_id, res.reconciled, res.latched, res.latched_categories)
        except Exception as e:  # noqa: BLE001 -- our own DB read faulting is a SYSTEM FAULT: FORCE-LATCH the WHOLE account
            log.error("pm_live_driver: boot-reconcile FAULTED (system fault) for %s -- force-latching ALL categories "
                      "to DISARM: %s", account_id, e)
            try:
                for c in cats:                                 # RULING A extended to the account (M3): latch every cat
                    arm.latch_boot_reconcile_mismatch(account_id, c, detail="boot-reconcile system fault: %r" % e,
                                                      legacy_db_path=legacy_db_path)
            except Exception as e2:  # noqa: BLE001 -- RULING A: a FAILED force-latch must NOT fall through
                log.critical("pm_live_driver: could NOT latch after a boot-reconcile fault (%s) for %s -- REFUSING to "
                             "enter the trading loop (cannot confirm DISARM; falling through would trade an UNVERIFIED "
                             "journal): %s", e, account_id, e2)
                return                                 # ★ do NOT proceed into the (possibly-armed) cycle on a double fault
    cycles = 0
    while _max_cycles is None or cycles < _max_cycles:
        cycles += 1
        # ★ LIVENESS task_alive beat -- per ACCOUNT, at the TOP of the loop, OUTSIDE the cycle try below, on its OWN
        # short-lived connection (the cycle's conn is opened INSIDE the try). This is the "the task is running" signal
        # pm_web reads: it fires every cycle regardless of whether any category completes, so a task that DIES or was
        # NEVER SPAWNED (the 2026-09-04 incident) simply stops writing -> the row goes stale -> the monitor alarms.
        # It CANNOT report healthy while the loop is dead (no timer/thread/at-boot write). safe_beat = fail-soft:
        # a liveness write NEVER kills a trading cycle.
        try:
            with db.connect(pm_db_path) as _hb_conn:
                heartbeat.safe_beat(heartbeat.upsert_task_alive, _hb_conn, account_id, int(_time.time()), log=log)
        except Exception as _hbc_e:  # noqa: BLE001 -- even opening the heartbeat connection must never break the loop
            log.warning("pm_live_driver: task-alive heartbeat connection failed for %s (non-fatal): %s", account_id, _hbc_e)
        # ★ M1: ACCOUNT-LEVEL reads happen ONCE per cycle and are SHARED across every category (fail-closed defaults
        # BOUND before anything can raise). The shared venue-exposure read is the account-wide base gate 6 rebases
        # onto; the shared Journal (below) accumulates every category's in-cycle placements against it.
        shard_bal = shard_balance.ShardBalances(total_dollars=0.0, by_shard={}, has_breakdown=False)
        venue_exp = venue_exposure.VenueExposure(total_dollars=0.0, has_data=False)
        try:
            now_ts = int(_time.time())
            # gate-6b input: the PER-SHARD split, FRESH each cycle (balances deplete). Account-wide (all shards).
            # Fail-CLOSED to UNKNOWN (has_breakdown False -> every entry skip:shard_underfunded, never place blind).
            try:
                shard_bal = await shard_balance.fetch_shard_balances(client)
            except Exception as e:  # noqa: BLE001 -- fail-CLOSED: cannot read the split -> UNKNOWN -> all entries skip
                log.warning("pm_live_driver: shard-balance read FAILED for %s -> UNKNOWN split (entries skip:"
                            "shard_underfunded this cycle -- never place blind): %s", account_id, e)
                shard_bal = shard_balance.ShardBalances(total_dollars=0.0, by_shard={}, has_breakdown=False)
            # ★ M1 gate-6 input: the ACCOUNT'S TRUE open exposure, read ONCE and SHARED across categories (a co-tenant
            # OR a sibling category can add exposure between cycles). Fail-CLOSED to UNKNOWN (has_data False -> every
            # entry skip:exposure_unknown, never size against an unknown book).
            try:
                venue_exp = await venue_exposure.fetch_open_exposure(client)
            except Exception as e:  # noqa: BLE001 -- fail-CLOSED: cannot trust the venue exposure -> UNKNOWN -> entries skip
                log.warning("pm_live_driver: venue open-exposure read FAILED for %s -> UNKNOWN (entries skip:"
                            "exposure_unknown this cycle -- never size against an unknown book): %s", account_id, e)
                venue_exp = venue_exposure.VenueExposure(total_dollars=0.0, has_data=False)
            with db.connect(pm_db_path) as conn:
                # ★ M1: ONE account-keyed Journal per cycle, SHARED across every category. gate 6 (open_usd) is
                # account-keyed, so a later category's evaluate sees an earlier category's in-cycle commit_would_place
                # through this ONE Journal -> the account open-cap is enforced JOINTLY (no lock, nothing added to the
                # order hot path). gate 5/8 (daily/count) remain (account, category)-keyed inside it, so each category
                # keeps its own daily/order budget.
                journal = execution.Journal(conn, [account_id], now_ts)
                for c in cats:                                  # ★ M1: iterate the account's categories SEQUENTIALLY
                    # ★ LIVENESS reached beat -- FIRST thing per category, BEFORE the no-catalog/no-ctx continues, so
                    # 'the loop reached this category this cycle' is recorded even if it then skips or (later) throws.
                    # 'reached fresh + evaluated stale' -> CATEGORY_STARVED (a category bug), distinct from a dead task.
                    heartbeat.safe_beat(heartbeat.upsert_reached, conn, account_id, c, int(_time.time()), log=log)
                    _bld = builders.get(c)
                    if _bld is None:
                        heartbeat.safe_beat(heartbeat.mark_skipped, conn, account_id, c, int(_time.time()),
                                            "skipped_no_builder", log=log)
                        continue                                # no catalog builder -> no signals (fail-safe skip)
                    if ctx_by_cat[c] is None or (_time.time() - last_idx_by_cat[c]) > index_refresh_sec:
                        try:
                            ctx_by_cat[c] = await _bld(client, int(_time.time())); last_idx_by_cat[c] = _time.time()
                        except Exception as e:  # noqa: BLE001 -- refresh failure keeps the last catalog (or None -> skip)
                            log.warning("pm_live_driver: index refresh failed for %s/%s: %s", account_id, c, e)
                    ctx = ctx_by_cat[c]
                    if ctx is None:
                        heartbeat.safe_beat(heartbeat.mark_skipped, conn, account_id, c, int(_time.time()),
                                            "skipped_no_ctx", log=log)
                        continue                                # still no catalog this cycle -> skip this category
                    sub = execution.sub_config_from_row(conn.execute(
                        "SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (account_id, c)).fetchone())
                    # ★ R-d PERIODIC settlement-scan (throttled PER CATEGORY, #18): book any position that settled
                    # since THIS category's last scan BEFORE evaluate, so a settled position is netted flat first (a
                    # stale whale-exit then sees skip:not_held; the exposure counters exclude it).
                    if (_time.time() - last_settle_by_cat[c]) > _SETTLE_SCAN_SEC:
                        try:
                            ssumm = settlement.book_settlements(conn, account_id, c,
                                                                await fetch_settlements(client), now_ts=now_ts)
                            if ssumm["n_booked"]:
                                log.info("pm_live_driver: periodic settlement-scan booked %d for %s/%s: %s",
                                         ssumm["n_booked"], account_id, c, ssumm["booked"])
                        except Exception as e:  # noqa: BLE001 -- a settlements-read failure just skips this scan
                            log.warning("pm_live_driver: periodic settlement-scan failed for %s/%s (retried next "
                                        "window): %s", account_id, c, e)
                        last_settle_by_cat[c] = _time.time()
                    wallets = [r["wallet"] for r in conn.execute(
                        "SELECT wallet FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND active=1",
                        (account_id, c)).fetchall()]
                    signals = []
                    for w in wallets:
                        try:
                            book = await positions_client.fetch_positions_book(w)
                        except Exception as e:  # noqa: BLE001 -- per-whale isolation (a fetch error skips that whale)
                            log.warning("pm_live_driver: /positions fetch failed for %s (%s): %s", w[:12], c, e); continue
                        if not getattr(book, "complete", False):
                            continue                            # never act on a partial book (nor touch the snapshot)
                        signals += positions_to_entry_signals(book.rows, w)
                        # Option D whale-EXIT detection: /positions size-REDUCTION vs the prior snapshot, CONFIRMED by
                        # an /activity SELL in-window (BOTH or MISSED; /activity pulled LAZILY only on a reduction).
                        # ★ #16: the prior snapshot is keyed (category, wallet) -- a wallet attached to TWO categories
                        # must not have its two books merged under this one task.
                        reds = detect_position_reductions(prior_snapshots.get((c, w), {}), book.rows, w, now_ts)
                        confirmed = True
                        if reds:
                            try:
                                acts = await positions_client.fetch_activity(w)
                                sells = activity_sells_from_activity(acts, w)
                                exits = execution.detect_exit_signals(sells, reds, window_sec=_EXIT_WINDOW_SEC)
                                if exits:
                                    log.info("pm_live_driver: %d confirmed whale-EXIT(s) for %s (%s; reductions=%d "
                                             "sells=%d)", len(exits), w[:12], c, len(reds), len(sells))
                                signals += exits
                            except Exception as e:  # noqa: BLE001 -- exit-confirm fetch FAILED -> no exit + RETRY next cycle
                                confirmed = False    # keep the OLD snapshot so the reduction re-checks next cycle
                                log.warning("pm_live_driver: exit-confirm /activity fetch failed for %s (%s; no exit; "
                                            "snapshot NOT advanced -> re-checked next cycle): %s", w[:12], c, e)
                        if confirmed:
                            prior_snapshots[(c, w)] = snapshot_open_positions(book.rows)   # ★ #16 keyed (category, wallet)
                    # ★ OPPOSING-PAIR GUARD (per category -- cross-category markets never share a condition_id, so
                    # per-category scoping is correct and a ufc market can never false-contest an mlb one). ONE
                    # per-wallet close per holding whale flattens the account; both incoming sides skipped; same-side
                    # stacking untouched. The close is a reduce_only exit through the SAME chokepoint (DISARM blocks it;
                    # the net-open guard makes it idempotent vs a co-occurring whale-exit). OPPOSED-MEMORY: a cid ever
                    # contested stays off the books for its life (keyed on the contested market, not the coid).
                    _entries = [s for s in signals if not s.is_exit]
                    _exits = [s for s in signals if s.is_exit]
                    _held = execution.account_held_outcomes(conn, account_id, c)
                    _mem = execution.account_opposed_cids(conn, account_id, c)
                    _kept, _opposed, _contested, _preexisting = execution.detect_opposing_closes(_entries, _held, _mem)
                    # ★ R1 (2026-09-03): DISTINGUISH a genuinely-NEW contest from a memory RE-SUPPRESSION. Before R1 the
                    # SAME warning fired whether the OPPOSED-MEMORY WORKED (re-suppressing a whale flicker on an
                    # already-off-the-books market -- benign) or a NEW pair was created -- and it said "close held + skip
                    # both sides" even when NOTHING was held or closed. So a working memory read like a live flatten
                    # firing every cycle (the 0x0f58 1816x noise) and was indistinguishable from a failure -- which cost
                    # a wrong hypothesis. NOW: WARN only on a NEW contest and/or an ACTUAL flatten, and say WHAT happened
                    # (flattened N held legs, not the aspirational "close held"); DEBUG the memory re-hits.
                    _new = _contested - _mem                            # in _contested but NOT already in the memory
                    # ★ R2 (2026-09-03): DECISION-keyed memory + un-flatten INSTRUMENTATION. (1) Record a marker for
                    # every NEWLY-decided contest -- EVEN one that generated no close -- so account_opposed_cids
                    # remembers the DECISION, not just a booked close_source='opposed' row. Next cycle these are memory
                    # re-hits (R1 DEBUG), not re-detections; and a contest whose flatten POST later fails is STILL
                    # remembered. (2) LOUDLY surface a contest we DECIDED to flatten but COULD NOT: we HOLD a side and
                    # generated NO close for it (no co-present entry to route the per-wallet close) -> the held side
                    # rides UN-flattened to settlement. This was INVISIBLE (the R2 history scan had to INFER the shape);
                    # now it announces itself. ONCE per occurrence (on _new only -> a persistent state is
                    # memory-suppressed next cycle, never re-spammed -- the R1 lesson), at ERROR (a real exposure).
                    if _new:
                        execution.mark_opposed_contested(conn, account_id, c, sorted(_new), now_ts=now_ts)
                    _closed_cids = {s.condition_id for s in _opposed}
                    _unflattened = [cid for cid in _new if _held.get(cid) and cid not in _closed_cids]
                    if _unflattened:
                        log.error("pm_live_driver: OPPOSING-PAIR guard %s/%s -- ★ UN-FLATTENED CONTESTED POSITION(S): "
                                  "decided to FLAT %d market(s) we HOLD but generated NO close (no co-present entry to "
                                  "route the per-wallet close) -> the held side rides to settlement UN-FLATTENED, a "
                                  "position we decided to close and did NOT. cids=%s",
                                  account_id, c, len(_unflattened), sorted(_unflattened))
                    if _new or _opposed:
                        log.warning("pm_live_driver: OPPOSING-PAIR guard %s/%s -- %d NEWLY-contested; flattened %d held "
                                    "leg(s) + skipped incoming both sides; new_cids=%s",
                                    account_id, c, len(_new), len(_opposed), sorted(_new))
                    elif _contested:
                        log.debug("pm_live_driver: OPPOSING-PAIR guard %s/%s -- %d already-contested market(s) "
                                  "re-suppressed via memory (whale flicker; no new order, nothing held/closed); cids=%s",
                                  account_id, c, len(_contested), sorted(_contested))
                    if _preexisting:
                        log.info("pm_live_driver: OPPOSING-PAIR guard %s/%s -- %d PRE-EXISTING pair(s) LEFT to settle: "
                                 "cids=%s", account_id, c, len(_preexisting), sorted(_preexisting))
                    signals = _kept + _exits + _opposed
                    place_fn = make_place_fn(client)
                    # ★ M1: the SHARED account Journal is passed to EVERY category -> the account open-cap is JOINT.
                    summ = await run_live_arm_gated_cycle(conn, sub, signals, ctx, journal, now_ts, place_fn=place_fn,
                                                          shard_balances=shard_bal, venue_exposure=venue_exp,
                                                          legacy_db_path=legacy_db_path, log=log)
                    # ★ LIVENESS evaluated beat -- this category fully evaluated + the cheap summary (IDLE vs PLACING,
                    # ceiling_latched=alive-but-intentionally-idle). Fail-soft: never let the monitor take a cycle down.
                    heartbeat.safe_beat(heartbeat.upsert_evaluated, conn, account_id, c, int(_time.time()), summ, log=log)
                    if summ["placed"] or summ["errors"]:
                        # str(summ): a lone non-empty dict as the sole %-arg is read as a %-MAPPING -> "not all args
                        # converted" and the line is EATEN on active cycles; str() defuses it.
                        log.info("pm_live_driver cycle %s/%s: %s", account_id, c, str(summ))
                    # ★ #14 SAFETY: per-CATEGORY sustained shard-underfunding alarm -- a per-account counter would let
                    # one category's placements silently reset a sibling's genuine shard-starvation alarm.
                    if summ.get("n_shard_underfunded", 0) > 0 and summ["placed"] == 0:
                        consec_underfunded_by_cat[c] += 1
                        if consec_underfunded_by_cat[c] % _SHARD_UNDERFUNDED_ALARM_N == 0:
                            log.warning("pm_live_driver: ALARM -- SUSTAINED SHARD UNDERFUNDING: %d consecutive cycles "
                                        "with a funding gap + ZERO placements (%s/%s). MOVE FUNDS to the market's shard "
                                        "or set/adjust target_balance_allocation. SURFACED, not latched.",
                                        consec_underfunded_by_cat[c], account_id, c)
                    else:
                        consec_underfunded_by_cat[c] = 0
        except Exception as e:  # noqa: BLE001 -- a bad cycle must never kill the loop
            log.exception("pm_live_driver: cycle failed: %s", e)
        await _sleep(poll_sec)


async def _sleep(sec):
    import asyncio
    await asyncio.sleep(sec)
