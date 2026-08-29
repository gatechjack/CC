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

THE PLACEMENT SEAM (Jack RULED option (b), 2026-08-29): the `place_fn` POSTs the chokepoint's GATE-APPROVED body
VERBATIM -- `decision.body` + `decision.client_order_id` -- NOT via `KalshiLiveBroker.place_order` (which would
REBUILD the body from an order object). The chokepoint's guarantees (dry-run parity, the journal record, the
idempotency key) are ABOUT THE BODY IT APPROVED; a rebuild that differs by a cent still places AN order -- just not
the approved one -- a silent 7th home for the NO-leg lens. Posting the approved body makes every guarantee exact,
no reconstruction. This is also the PRECEDENT: `agents/strategies/poly_kalshi_executor.py` (the proven live path)
POSTs a pre-built body, not through `place_order`. We REUSE the pure `fill_event_from_v2_response` + the
`KalshiNoFill`/`OrderPlacementError` split; the ONLY duplication is the ~15-line POST try/except wrapper (a note
is left in `kalshi_live.py` so a future fix to that error handling knows it has two homes).

TWO FORCED DUPLICATIONS, both rooted in async (recorded so they are not mistaken for drift):
  1. The POST wrapper (above) -- `run_arm_gated_cycle`'s injected `place_fn` is SYNC, but the Kalshi client POST is
     async; and place_order rebuilds rather than posts the approved body.
  2. The arm-gated LOOP (`run_live_arm_gated_cycle` below) mirrors `execution.run_arm_gated_cycle` but is ASYNC (to
     await the POST). It REUSES `execution.evaluate` (the 8 gates) + `arm.read_arm_verdict` (the per-order re-read)
     VERBATIM -- only the loop shell is re-homed. The sync `run_arm_gated_cycle` stays as the dry-run/box-scratch seam.

SAFETY carried from R5 (all still enforced HERE):
  * DISARM blocks EVERYTHING -- the cycle re-reads `arm.read_arm_verdict` immediately BEFORE EVERY order (not once
    per cycle), so a mid-cycle kill stops the very next POST (residual one order wide).
  * BOOT-RECONCILE runs at boot against the AUTHENTICATED portfolio; a mismatch latches `boot_reconcile_mismatch`
    (fail-safe DISARMED until a human clears it) -- the driver comes up latched-if-mismatch.
  * A loud `OrderPlacementError` increments a consecutive-error latch; a 401/403 latches the whole account
    (auth-failure) and flags open positions for MANUAL exit (`arm.latch_*`).
  * ENTRIES ONLY for the first order (R7.f is a moneyline ENTRY). Option-D whale EXIT detection is a LATER rung.

R7.e VERIFY (read-only, documented like the sign convention): the pykalshi `get_markets` market-object quote field
names mapped into `MarketContext.markets` (yes_ask/no_ask/liquidity). A wrong guess makes a ticker UNQUOTED ->
`evaluate` skips it (`skip:no_quote`) -> NO order (safe). Confirm the real field names on a live `get_markets`.

Spec: reports/prediction_markets/STAGE3_PLAN_2026-08-28.md sec 8/16 + R7_PLAN_2026-08-29.md.
"""
from __future__ import annotations

import time as _time

from . import arm, boot_reconcile, db, execution, paper
from ..data import mlb_poly_kalshi_match as M
# REUSE (pure builders + the benign/loud split) -- NOT KalshiLiveBroker, NOT place_order (structural: no rebuild).
from ..brokers.kalshi_live import (KalshiNoFill, OrderPlacementError, fill_event_from_v2_response,
                                   _is_benign_fok_nofill, _V2_ORDERS_PATH)

# The three MLB series the sub-division copies (Jack's scope ruling: moneyline+total+spread).
SERIES = ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD")
_SETTLED_LOOKBACK_SEC = 160 * 86400   # match the poly loop's settled-window for the index


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
    return {"yes_ask_dollars": d("yes_ask_dollars", "yes_ask"),
            "no_ask_dollars": d("no_ask_dollars", "no_ask"),
            "liquidity_dollars": d("liquidity_dollars", "liquidity")}


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
    game_idx = M.build_kalshi_game_index(game_t)
    total_idx = M.build_kalshi_total_index(total_t)
    spread_idx = M.build_kalshi_spread_index(spread_t)
    # kalshi_dates: the game index keys carry the stem/date; reuse the game index's date set if the matcher
    # exposes one, else derive from the tickers (the matcher's exact-strike gate is the real guard).
    for tk in game_t:
        dates.add(tk)
    return execution.MarketContext(game_idx, total_idx, spread_idx, frozenset(dates), markets)


# ── signal source: attached whales' /positions -> entry CopySignals (chokepoint dedups already-placed) ───
def _stable_entry_key(condition_id: str, outcome_index) -> str:
    """A restart-STABLE entry key for a /positions row, which carries NO fill tx_hash/ts. The whale's holding of
    a specific (condition_id, outcome_index) IS the stable identity -> one copy per position (the durable-journal
    dedup then never re-copies it). LIMITATION (noted, acceptable for the path-proving first order): a
    close-then-re-open of the SAME (condition_id, outcome_index) reuses the key -> the re-entry is not re-copied.
    A later refinement keys on the pm_paper_trade entry_observed_ts or an /activity tx_hash to distinguish re-entries."""
    return "pos:%s:%s" % (condition_id or "", outcome_index)


def positions_to_entry_signals(rows, wallet: str) -> list:
    """Genuinely-open /positions rows for one whale -> entry CopySignals (is_exit=False). Reuses
    paper.is_genuinely_open (the D1 open-filter) + paper.pos_outcome_index. EXITS are a later rung (Option D)."""
    out = []
    for p in rows:
        if not paper.is_genuinely_open(p):
            continue
        cid = str(getattr(p, "condition_id", "") or "")
        oidx = paper.pos_outcome_index(p)
        out.append(execution.CopySignal(
            wallet=wallet, slug=str(getattr(p, "slug", "") or ""),
            outcome=str(getattr(p, "outcome", "") or ""), condition_id=cid, outcome_index=oidx,
            signal_id=execution.stable_signal_id(wallet, cid, oidx, _stable_entry_key(cid, oidx)),
            is_exit=False))
    return out


# ── the placement seam (option b): POST the gate-approved body VERBATIM ────────────────────────────────
def make_place_fn(client):
    """Return an async `place_fn(decision) -> FillEvent` that POSTs the chokepoint's APPROVED `decision.body`
    (with `decision.client_order_id`) verbatim and maps the response with the REUSED pure mapper. Raises
    KalshiNoFill (benign 0-fill / FOK-kill) or OrderPlacementError (loud reject) -- the SAME split place_order
    uses. ** The ~15-line try/except below is the ONE duplication of place_order's POST wrapper (see the note in
    kalshi_live.py). ** Injected by the caller so this module holds no broker object and never rebuilds a body."""
    async def place_fn(decision):
        from pykalshi.exceptions import KalshiError
        try:
            resp = await client.post(_V2_ORDERS_PATH, decision.body)      # POST the APPROVED body verbatim
        except KalshiError as e:
            if _is_benign_fok_nofill(e):                                   # benign FOK-kill -> no fill (reuse split)
                raise KalshiNoFill("kalshi FOK order %s did not fill (insufficient resting volume)"
                                   % decision.client_order_id) from e
            raise OrderPlacementError("kalshi V2 POST rejected for %s (%s x%s): %s"
                                      % (decision.kalshi_ticker, decision.leg, decision.count, e)) from e
        return fill_event_from_v2_response(                               # REUSE the pure response mapper
            resp, symbol="%s:%s" % (decision.kalshi_ticker, decision.leg),
            side=("sell" if decision.is_exit else "buy"),
            fallback_price=decision.price, fallback_order_id=decision.client_order_id, outcome=decision.leg)
    return place_fn


# ── the durable live-order journal write (dry_run=0; the fill facts) ───────────────────────────────────
def _record_order(conn, sub, signal, decision, *, outcome_status, fill=None, error_detail=None, now_ts):
    """Append the REAL (dry_run=0) live-order row to pm_subdivision_order -- the durable journal boot-reconcile +
    the idempotency dedup read. `fill_price` is the OUTCOME-LEG per-contract price (FillEvent.price is already
    leg-corrected: 1-yes for a NO leg). Written AFTER the POST with the outcome; a crash in the POST->write window
    is the K9 case that boot-reconcile catches at next boot."""
    conn.execute(
        "INSERT INTO pm_subdivision_order (account_id, category, wallet, condition_id, outcome_index, signal_id, "
        " client_order_id, ticker, order_side, outcome_leg, is_exit, submitted_count, submitted_price, "
        " time_in_force, outcome_status, broker_order_id, fill_count, fill_price, fee, error_detail, dry_run, "
        " submitted_ts, response_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
        (sub.account_id, sub.category, signal.wallet, signal.condition_id, signal.outcome_index, signal.signal_id,
         decision.client_order_id, decision.kalshi_ticker, decision.body.get("side"), decision.leg,
         1 if signal.is_exit else 0, decision.count, decision.price, decision.body.get("time_in_force"),
         outcome_status, (getattr(fill, "order_id", None) if fill else None),
         (float(getattr(fill, "qty", 0.0)) if fill else None), (float(getattr(fill, "price", 0.0)) if fill else None),
         (float(getattr(fill, "fee", 0.0)) if fill else None), error_detail, int(now_ts), int(now_ts)))
    if hasattr(conn, "commit"):
        conn.commit()


# ── the ASYNC arm-gated cycle (dup #2: async twin of run_arm_gated_cycle; reuses evaluate + read_arm_verdict) ──
async def run_live_arm_gated_cycle(conn, sub, signals, ctx, journal, now_ts, *, place_fn, legacy_db_path=None):
    """Every signal through the chokepoint's 8 gates (`execution.evaluate`), then -- for a gate-passing order and
    ONLY IF ARMED (re-read `arm.read_arm_verdict` immediately before EACH order) -- await `place_fn` (the POST) and
    record the outcome. DISARMED blocks everything (entries AND exits). A loud error latches (consecutive-errors;
    401/403 -> whole-account auth-failure + manual-exit flag). Returns a summary; `posts_sent` counts REAL POSTs
    (0 in box-scratch: place_fn is a stub / the cycle is disarmed)."""
    signals = list(signals)
    n_would = n_skip = n_reject = placed = disarm_blocked = errors = 0
    consec_err = 0
    for s in signals:
        d = execution.evaluate(s, sub, ctx, journal, conn, now_ts, legacy_db_path=legacy_db_path)   # the 8 gates (reused)
        if d.status != "dry_run_would_place":
            if d.status.startswith("skip:"):
                n_skip += 1
            else:
                n_reject += 1
            continue
        n_would += 1
        if not arm.read_arm_verdict(sub.account_id, sub.category, legacy_db_path=legacy_db_path).armed:  # RE-READ per order
            disarm_blocked += 1
            continue                                                    # off is off (entries AND exits)
        try:
            fill = await place_fn(d)                                    # the ONE seam: POST the approved body
            _record_order(conn, sub, s, d, outcome_status="filled", fill=fill, now_ts=now_ts)
            journal.commit_would_place(sub.account_id, sub.category, float(d.notional_usd or 0.0))
            placed += 1
            consec_err = 0
        except KalshiNoFill:                                            # benign 0-fill -> record, no latch, retry next signal
            _record_order(conn, sub, s, d, outcome_status="no_fill", now_ts=now_ts)
        except OrderPlacementError as e:                               # LOUD -> record + latch
            errors += 1
            consec_err += 1
            detail = repr(e)[:400]
            _record_order(conn, sub, s, d, outcome_status="error", error_detail=detail, now_ts=now_ts)
            if _is_auth_failure(e):
                arm.latch_auth_failure(sub.account_id, [sub.category], detail="order-path auth failure: %s" % detail,
                                       legacy_db_path=legacy_db_path)
                break                                                   # account disarmed; stop the cycle
            if consec_err >= 3:
                arm.latch_consecutive_errors(sub.account_id, sub.category, n=consec_err, legacy_db_path=legacy_db_path)
                break
    return {"account_id": sub.account_id, "category": sub.category, "n_signals": len(signals),
            "n_would_place": n_would, "placed": placed, "n_disarm_blocked": disarm_blocked, "errors": errors,
            "n_skip": n_skip, "n_reject": n_reject, "posts_sent": placed}


def _is_auth_failure(e) -> bool:
    t = str(e).lower()
    return "401" in t or "403" in t or "unauthor" in t or "forbidden" in t


# ── boot-reconcile against the AUTHENTICATED portfolio (async fetch -> sync reconcile with a lambda) ────
async def run_boot_reconcile(conn, sub, client, *, legacy_db_path=None):
    """Fetch the account's ACTUAL Kalshi positions (authenticated) and reconcile them against the journal. The
    async fetch happens HERE; `boot_reconcile.reconcile_account` is SYNC and gets a lambda returning the
    already-fetched list, so nothing in boot_reconcile.py changes. A mismatch latches boot_reconcile_mismatch
    (fail-safe DISARMED until a human clears it). A fetch failure is fail-safe-latched inside reconcile_account."""
    async def _fetch():
        return list(await client.portfolio.get_positions(fetch_all=True))
    try:
        positions = await _fetch()
    except Exception as e:                                             # noqa: BLE001 -- fail-safe: cannot read -> latch
        return boot_reconcile.reconcile_account(conn, sub.account_id, sub.category,
                                                fetch_positions=lambda: (_ for _ in ()).throw(e),
                                                legacy_db_path=legacy_db_path)
    return boot_reconcile.reconcile_account(conn, sub.account_id, sub.category,
                                            fetch_positions=lambda: positions, legacy_db_path=legacy_db_path)


# ── the engine task (mirrors main.py:_scheduled_poly_kalshi_loop) ──────────────────────────────────────
async def scheduled_pm_live_loop(pm_db_path, broker, *, account_id, category, poll_sec=7.0,
                                 index_refresh_sec=900.0, legacy_db_path=None, log=None, _max_cycles=None):
    """The engine-side driver task. BOOT: build the index + boot-reconcile (comes up latched-if-mismatch). STEADY:
    refresh the index when stale, read each attached whale's /positions, run the async arm-gated cycle, sleep. A
    bad cycle is logged and NEVER kills the loop. `_max_cycles` bounds the loop for box-scratch (None = forever)."""
    import logging
    log = log or logging.getLogger(__name__)
    client = broker._read._client
    ctx = None
    last_idx = 0.0
    # BOOT: index + reconcile (best-effort index; reconcile is fail-safe).
    try:
        ctx = await fetch_market_context(client, int(_time.time())); last_idx = _time.time()
    except Exception as e:  # noqa: BLE001
        log.warning("pm_live_driver: boot index build failed: %s", e)
    with db.connect(pm_db_path) as conn:
        sub = execution.sub_config_from_row(conn.execute(
            "SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (account_id, category)).fetchone())
        try:
            res = await run_boot_reconcile(conn, sub, client, legacy_db_path=legacy_db_path)
            log.info("pm_live_driver: boot-reconcile reconciled=%s latched=%s", res.reconciled, res.latched)
        except Exception as e:  # noqa: BLE001 -- our own DB read raising is a system fault; log loud, stay disarmed
            log.error("pm_live_driver: boot-reconcile FAILED (staying disarmed): %s", e)
    cycles = 0
    while _max_cycles is None or cycles < _max_cycles:
        cycles += 1
        try:
            if ctx is None or (_time.time() - last_idx) > index_refresh_sec:
                ctx = await fetch_market_context(client, int(_time.time())); last_idx = _time.time()
            now_ts = int(_time.time())
            with db.connect(pm_db_path) as conn:
                sub = execution.sub_config_from_row(conn.execute(
                    "SELECT * FROM pm_subdivision WHERE account_id=? AND category=?", (account_id, category)).fetchone())
                journal = execution.Journal(conn, [account_id], now_ts)
                wallets = [r["wallet"] for r in conn.execute(
                    "SELECT wallet FROM pm_subdivision_attachment WHERE account_id=? AND category=? AND active=1",
                    (account_id, category)).fetchall()]
                signals = []
                for w in wallets:
                    try:
                        book = await client_fetch_positions_book(broker, w)
                    except Exception as e:  # noqa: BLE001 -- per-whale isolation (a fetch error skips that whale)
                        log.warning("pm_live_driver: /positions fetch failed for %s: %s", w[:12], e); continue
                    if not getattr(book, "complete", False):
                        continue                                        # never act on a partial book
                    signals += positions_to_entry_signals(book.rows, w)
                place_fn = make_place_fn(client)
                summ = await run_live_arm_gated_cycle(conn, sub, signals, ctx, journal, now_ts,
                                                      place_fn=place_fn, legacy_db_path=legacy_db_path)
                if summ["placed"] or summ["errors"]:
                    log.info("pm_live_driver cycle: %s", summ)
        except Exception as e:  # noqa: BLE001 -- a bad cycle must never kill the loop
            log.exception("pm_live_driver: cycle failed: %s", e)
        await _sleep(poll_sec)


async def client_fetch_positions_book(broker, wallet):
    """The whale /positions read seam -- the SAME polymarket client read the paper poller uses
    (paper.poll_pinned -> client.fetch_positions_book). Kept as a small indirection so box-scratch can inject a
    fake. The polymarket client is constructed by the caller/engine; here we reach it via the broker adapter's
    hook if present, else the module-level _positions_client (box-scratch sets it)."""
    cli = _positions_client if _positions_client is not None else getattr(broker, "_pm_positions_client", None)
    if cli is None:
        raise RuntimeError("pm_live_driver: no polymarket positions client wired")
    return await cli.fetch_positions_book(wallet)


# box-scratch / R7.e wiring seams (kept module-level so tests inject without touching the engine):
_positions_client = None


async def _sleep(sec):
    import asyncio
    await asyncio.sleep(sec)
