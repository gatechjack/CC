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

from . import arm, boot_reconcile, db, execution, paper, settlement, shard_balance, venue_exposure
from ..data import mlb_poly_kalshi_match as M
# REUSE (pure builders + the benign/loud split) -- NOT KalshiLiveBroker, NOT place_order (structural: no rebuild).
from ..brokers.kalshi_live import (KalshiNoFill, OrderPlacementError, fill_event_from_v2_response,
                                   _is_benign_fok_nofill, _V2_ORDERS_PATH)

_LOG = logging.getLogger(__name__)
SERIES = ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD")   # Jack's scope ruling: moneyline+total+spread
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


async def _merge_raw_market_fields(client, markets: dict) -> None:
    """★ Merge PER-MARKET fields the pykalshi get_markets Market OBJECT DROPS but the RAW /markets payload CARRIES
    (verified 2026-08-30): `exchange_index` (gate-6b shard) and `yes_bid_size_fp`/`yes_ask_size_fp` (gate-3
    TOP-OF-BOOK depth). The SDK object exposes NEITHER as an attribute, and `liquidity_dollars` is a DEPRECATED
    always-'0.0000' Kalshi stub -- so without this both gates would FAIL CLOSED on every market (skip). The
    exchange_index is authoritative PER MARKET (correction 1: series-level is only right post-Aug-24). OPEN markets
    only (we only PLACE on open books). A raw-get failure leaves the fields absent -> gates 3/6b fail-close (safe)."""
    for series in SERIES:
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
            is_exit=False))
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
                arm.latch_auth_failure(sub.account_id, [sub.category],
                                       detail="order-path auth failure: %s" % repr(e)[:200], legacy_db_path=legacy_db_path)
                break                                                   # account disarmed; stop the cycle
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
    force-latches; see scheduled_pm_live_loop.)"""
    async def _fetch():
        return list(await client.portfolio.get_positions(fetch_all=True))
    try:
        positions = await _fetch()
    except Exception as e:                                             # noqa: BLE001 -- fail-safe: cannot read -> latch
        fetch = _raiser(e)
    else:
        fetch = (lambda: positions)
    return boot_reconcile.reconcile_account(conn, sub.account_id, sub.category,
                                            fetch_positions=fetch, legacy_db_path=legacy_db_path)


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


# ── the engine task (mirrors main.py:_scheduled_poly_kalshi_loop) ──────────────────────────────────────
async def scheduled_pm_live_loop(pm_db_path, broker, positions_client, *, account_id, category, poll_sec=7.0,
                                 index_refresh_sec=900.0, legacy_db_path=None, log=None, _max_cycles=None):
    """The engine-side driver task. `positions_client` (fetch_positions_book, the paper poller's read) is INJECTED by
    the caller (the R7.e main.py block passes the real one; box-scratch a fake). BOOT: build the index + boot-reconcile
    (comes up latched-if-mismatch; a boot-reconcile RAISE ALSO force-latches). STEADY: refresh the index when stale,
    read each attached whale's /positions, run the async arm-gated cycle, sleep. A bad cycle is logged and NEVER kills
    the loop. `_max_cycles` bounds the loop for box-scratch (None = forever)."""
    log = log or _LOG
    client = broker._read._client
    ctx = None
    last_idx = 0.0
    try:
        ctx = await fetch_market_context(client, int(_time.time())); last_idx = _time.time()
    except Exception as e:  # noqa: BLE001
        log.warning("pm_live_driver: boot index build failed: %s", e)
    last_settle = 0.0                                          # R-d: throttle timer for the periodic settlement-scan
    with db.connect(pm_db_path) as conn:
        sub = execution.sub_config_from_row(conn.execute(
            "SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (account_id, category)).fetchone())
        # ★ R-d BOOT SETTLEMENT-SCAN -- runs BEFORE boot-reconcile so a position that SETTLED WHILE THE ENGINE WAS
        # DOWN (the Cubs case) is booked FLAT on the way up, and boot-reconcile then comes up CLEAN instead of
        # latching R-b. FAIL-SAFE: if the settlements fetch fails, nothing is booked -> boot-reconcile still latches
        # R-b (the safe fallback, a human confirms + clears). Booking never places an order.
        try:
            bsumm = settlement.book_settlements(conn, account_id, category,
                                                await fetch_settlements(client), now_ts=int(_time.time()))
            if bsumm["n_booked"]:
                log.info("pm_live_driver: BOOT settlement-scan booked %d terminal-close(s) -- inspect the first: %s",
                         bsumm["n_booked"], bsumm["booked"])
        except Exception as e:  # noqa: BLE001 -- a settlements-read failure must not stop boot; reconcile then latches R-b
            log.warning("pm_live_driver: BOOT settlement-scan failed (nothing booked -> boot-reconcile may latch R-b, "
                        "the safe fallback): %s", e)
        last_settle = _time.time()                             # so the periodic scan waits a full interval after boot
        try:
            res = await run_boot_reconcile(conn, sub, client, legacy_db_path=legacy_db_path)
            log.info("pm_live_driver: boot-reconcile reconciled=%s latched=%s", res.reconciled, res.latched)
        except Exception as e:  # noqa: BLE001 -- our own DB read faulting is a SYSTEM FAULT: FORCE-LATCH (do NOT proceed armed)
            log.error("pm_live_driver: boot-reconcile FAULTED (system fault) -- force-latching to DISARM: %s", e)
            try:
                arm.latch_boot_reconcile_mismatch(account_id, category, detail="boot-reconcile system fault: %r" % e,
                                                  legacy_db_path=legacy_db_path)
            except Exception as e2:  # noqa: BLE001 -- RULING A (2026-08-31): a FAILED force-latch must NOT fall through
                log.critical("pm_live_driver: could NOT latch after a boot-reconcile fault (%s) -- REFUSING to enter "
                             "the trading loop: cannot confirm DISARM, and falling through would trade on an "
                             "UNVERIFIED journal (a 'safety check that silently stops checking'): %s", e, e2)
                return                                 # ★ do NOT proceed into the (possibly-armed) cycle on a double fault
    cycles = 0
    consec_underfunded = 0                                  # gate-6b sustained-underfunding alarm counter (cross-cycle)
    # ★ Option D (Fork A1): the PRIOR /positions snapshot per whale, held IN MEMORY (NOT persisted -- the journal is
    # the single source of truth for position state; a persisted snapshot would be a second one). {wallet ->
    # snapshot_open_positions(...)}. EMPTY at boot, so cycle 1 emits NO reduction (nothing to diff against) and seeds
    # it. A restart re-derives from /positions -- a reduction straddling the restart is a MISSED exit (accepted).
    prior_snapshots: dict = {}
    while _max_cycles is None or cycles < _max_cycles:
        cycles += 1
        # ★ fail-closed default, BOUND before anything can raise: even if a future refactor moved the placement call
        # out of the fetch's protection, gate 6b would see an UNKNOWN split (skip), never None/stale (adversarial
        # review, defensive). The per-cycle fetch below overwrites this on both success and failure.
        shard_bal = shard_balance.ShardBalances(total_dollars=0.0, by_shard={}, has_breakdown=False)
        # ★ R7 gate-6 input, SAME fail-closed discipline as shard_bal: bound to an UNKNOWN (has_data False) before
        # anything can raise, so gate 6 skips (never sizes against an unknown book) even on an early failure.
        venue_exp = venue_exposure.VenueExposure(total_dollars=0.0, has_data=False)
        try:
            if ctx is None or (_time.time() - last_idx) > index_refresh_sec:
                ctx = await fetch_market_context(client, int(_time.time())); last_idx = _time.time()
            now_ts = int(_time.time())
            # ★ gate 6b input: read the PER-SHARD split FRESH every cycle (balances DEPLETE with trading; the 900s
            # index cache would be stale). NEVER None on the live path -- a read FAILURE fails CLOSED to an UNKNOWN
            # ShardBalances (has_breakdown False) so every entry skips rather than places blind. This is the one
            # construction site that makes evaluate's gate-6b unbypassable on the live path.
            try:
                shard_bal = await shard_balance.fetch_shard_balances(client)
            except Exception as e:  # noqa: BLE001 -- fail-CLOSED: cannot read the split -> UNKNOWN -> all entries skip
                log.warning("pm_live_driver: shard-balance read FAILED -> UNKNOWN split (all entries skip:"
                            "shard_underfunded this cycle -- NEVER place blind): %s", e)
                shard_bal = shard_balance.ShardBalances(total_dollars=0.0, by_shard={}, has_breakdown=False)
            # ★ R7 gate-6 input: read the ACCOUNT'S TRUE open exposure from the venue FRESH every cycle (a co-tenant
            # can add exposure between cycles). NEVER None on the live path -- a read/parse failure fails CLOSED to an
            # UNKNOWN VenueExposure (has_data False) so every entry skip:exposure_unknown rather than sizing against a
            # journal-only (co-tenant-blind) base. This is the construction site that makes gate 6's rebase
            # unbypassable on the live path (the paper/dry-run path passes venue_exposure=None -> journal base).
            try:
                venue_exp = await venue_exposure.fetch_open_exposure(client)
            except Exception as e:  # noqa: BLE001 -- fail-CLOSED: cannot trust the venue exposure -> UNKNOWN -> entries skip
                log.warning("pm_live_driver: venue open-exposure read FAILED -> UNKNOWN (entries skip:exposure_unknown "
                            "this cycle -- NEVER size against an unknown book): %s", e)
                venue_exp = venue_exposure.VenueExposure(total_dollars=0.0, has_data=False)
            with db.connect(pm_db_path) as conn:
                sub = execution.sub_config_from_row(conn.execute(
                    "SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (account_id, category)).fetchone())
                # ★ R-d PERIODIC settlement-scan (throttled): book any position that settled since the last scan
                # BEFORE this cycle's Journal + evaluate, so a settled position is netted flat first (a stale
                # whale-exit for it then sees skip:not_held, and the exposure counters exclude it). A settled MLB
                # game we hold resolves mid-session; this drops it from /live + net-open within the throttle window.
                if (_time.time() - last_settle) > _SETTLE_SCAN_SEC:
                    try:
                        ssumm = settlement.book_settlements(conn, account_id, category,
                                                            await fetch_settlements(client), now_ts=now_ts)
                        if ssumm["n_booked"]:
                            log.info("pm_live_driver: periodic settlement-scan booked %d terminal-close(s): %s",
                                     ssumm["n_booked"], ssumm["booked"])
                    except Exception as e:  # noqa: BLE001 -- a settlements-read failure just skips this scan (retried next window)
                        log.warning("pm_live_driver: periodic settlement-scan failed (retried next window): %s", e)
                    last_settle = _time.time()
                journal = execution.Journal(conn, [account_id], now_ts)
                wallets = [r["wallet"] for r in conn.execute(
                    "SELECT wallet FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND active=1",
                    (account_id, category)).fetchall()]
                signals = []
                for w in wallets:
                    try:
                        book = await positions_client.fetch_positions_book(w)
                    except Exception as e:  # noqa: BLE001 -- per-whale isolation (a fetch error skips that whale)
                        log.warning("pm_live_driver: /positions fetch failed for %s: %s", w[:12], e); continue
                    if not getattr(book, "complete", False):
                        continue                                        # never act on a partial book (also: do NOT
                        # touch prior_snapshots on a partial -- a short book would look like a spurious reduction)
                    signals += positions_to_entry_signals(book.rows, w)
                    # ── Option D whale-EXIT detection: a /positions size-REDUCTION vs the prior snapshot, CONFIRMED by
                    # an /activity SELL in-window (execution.detect_exit_signals: BOTH or MISSED). /activity is pulled
                    # LAZILY -- ONLY when a reduction is actually seen -- so the ~7s cadence does not double the API
                    # load; the reduction cannot appear before the sell it confirms (a sell precedes its own
                    # position drop), so this misses nothing the window would have caught. Per-whale isolated.
                    reds = detect_position_reductions(prior_snapshots.get(w, {}), book.rows, w, now_ts)
                    confirmed = True
                    if reds:
                        try:
                            acts = await positions_client.fetch_activity(w)
                            sells = activity_sells_from_activity(acts, w)
                            exits = execution.detect_exit_signals(sells, reds, window_sec=_EXIT_WINDOW_SEC)
                            if exits:
                                log.info("pm_live_driver: %d confirmed whale-EXIT signal(s) for %s (reductions=%d, "
                                         "sells=%d)", len(exits), w[:12], len(reds), len(sells))
                            signals += exits
                        except Exception as e:  # noqa: BLE001 -- exit-confirm fetch FAILED -> no exit + RETRY next cycle
                            confirmed = False    # do NOT advance the snapshot -> the reduction is RE-DETECTED next cycle
                            log.warning("pm_live_driver: exit-confirm /activity fetch failed for %s (no exit this "
                                        "cycle; the reduction is RE-CHECKED next cycle -- snapshot not advanced): %s",
                                        w[:12], e)
                    # ★ advance the prior snapshot ONLY after a COMPLETED diff (+ a successful confirm when a reduction
                    # was seen). A failed /activity confirm keeps the OLD snapshot so the pending reduction retries
                    # rather than being silently lost to a transient blip (a missed exit is accepted, but not for a
                    # recoverable fetch error). No reduction seen -> confirmed stays True -> advance normally.
                    if confirmed:
                        prior_snapshots[w] = snapshot_open_positions(book.rows)
                # ★ OPPOSING-PAIR GUARD: when two whales disagree on ONE market (same condition_id, different
                # outcome_index), the bet comes OFF THE BOOKS -- CLOSE what we hold (close_source='opposed', ONE
                # PER-WALLET close per holding whale -> the whole account flattens) and SKIP both incoming sides
                # (place neither; signal ordering is not
                # information). Same-side stacking (same cid+outcome_index) is NEVER contested -> untouched (agreement
                # is conviction). The close is a reduce_only exit through the SAME chokepoint below -- DISARM still
                # blocks it, and the net-open guard makes it idempotent against a co-occurring whale-exit (no double
                # close). Detected on the SEMANTIC market identity, so 'opposing' is unambiguous across market types
                # and a different LINE (a different condition_id) never false-fires.
                _entries = [s for s in signals if not s.is_exit]
                _exits = [s for s in signals if s.is_exit]        # whale-EXIT signals -- pass through untouched
                # OPPOSED-MEMORY (2026-09-01): a cid EVER contested stays off the books for its life (survives a
                # signal flicker; the enter-close churn fix). Keyed on the market being contested, not the coid, so
                # it holds independently of gate-4 dedup (safe under the R7.h re-entry change).
                _kept, _opposed, _contested, _preexisting = execution.detect_opposing_closes(
                    _entries, execution.account_held_outcomes(conn, account_id, category),
                    execution.account_opposed_cids(conn, account_id, category))
                if _contested:
                    log.warning("pm_live_driver: OPPOSING-PAIR guard -- %d NEWLY-contested market(s) -> FLAT (close "
                                "held + skip both sides); opposed_closes=%d contested_cids=%s",
                                len(_contested), len(_opposed), sorted(_contested))
                if _preexisting:
                    # a pair we ALREADY hold both sides of -> LEFT ALONE (the guard prevents NEW pairs, it does not
                    # retroactively flatten; Jack RULED let a pre-existing pair settle). Logged for visibility.
                    log.info("pm_live_driver: OPPOSING-PAIR guard -- %d PRE-EXISTING pair(s) LEFT to settle (NOT "
                             "flattened): cids=%s", len(_preexisting), sorted(_preexisting))
                signals = _kept + _exits + _opposed
                place_fn = make_place_fn(client)
                summ = await run_live_arm_gated_cycle(conn, sub, signals, ctx, journal, now_ts, place_fn=place_fn,
                                                      shard_balances=shard_bal, venue_exposure=venue_exp,
                                                      legacy_db_path=legacy_db_path, log=log)
                if summ["placed"] or summ["errors"]:
                    # ★ str(summ): a lone non-empty dict as the sole %-logging arg is treated by stdlib logging as a
                    # %-MAPPING, not a value -> "TypeError: not all arguments converted" and the line is EATEN, exactly
                    # on active cycles (placed/errors truthy) when the summary matters most. Wrapping in str() defuses it.
                    log.info("pm_live_driver cycle: %s", str(summ))
                # ★ SUSTAINED shard-underfunding alarm (SURFACED, not latched): a funding gap persisting across N
                # cycles with ZERO placements means the auto-rebalancer has not refilled the market's shard -> a human
                # must move funds or set/adjust target_balance_allocation. Resets the moment anything places or the gap
                # clears; re-fires every N cycles while sustained so it stays visible.
                if summ.get("n_shard_underfunded", 0) > 0 and summ["placed"] == 0:
                    consec_underfunded += 1
                    if consec_underfunded % _SHARD_UNDERFUNDED_ALARM_N == 0:
                        log.warning("pm_live_driver: ALARM -- SUSTAINED SHARD UNDERFUNDING: %d consecutive cycles with "
                                    "a funding gap on the market's shard and ZERO placements (%s/%s). MOVE FUNDS to the "
                                    "market's shard or set/adjust target_balance_allocation. SURFACED, not latched.",
                                    consec_underfunded, account_id, category)
                else:
                    consec_underfunded = 0
        except Exception as e:  # noqa: BLE001 -- a bad cycle must never kill the loop
            log.exception("pm_live_driver: cycle failed: %s", e)
        await _sleep(poll_sec)


async def _sleep(sec):
    import asyncio
    await asyncio.sleep(sec)
