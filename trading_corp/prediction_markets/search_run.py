"""Stage 4 SEARCH -- RUNG 2: DISCOVERY + on-demand FIRST-SIGHT BACKFILL orchestration + the run record.

Ruling 1 (Jack, 2026-08-29) -- backfill is ON-DEMAND, not per-run:
  - a whale that ALREADY HAS A COMPLETE BACKFILL is read from the DB, NEVER auto-re-pulled (the ~30-call
    full page is paid ONCE, on purpose);
  - a whale LACKING a complete backfill (never-seen OR a prior partial/failed) gets ONE full-page backfill
    on discovery -- i.e. we backfill until it is complete once, then never automatically again;
  - the REFRESH BUTTON (wired in R4) does an ad-hoc FULL re-pull of one whale, on demand (`refresh_one`);
  - NO staleness threshold, NO forced refresh -- Jack's call, always. "Last updated"
    (`pm_whale.last_refresh_ts`) makes staleness VISIBLE, never silent.

Confirmed read-only, NOT assumed (R1 box-scratch + R2 API probe, 2026-08-29): `/closed-positions` is NOT
newest-first (not even within a page) AND honors NO date/since param -> incremental-at-source is impossible,
so backfill is FULL-PAGE only. `search.page_new_rows` (R1) is the guardrail that PROVED this -- it would
raise rather than silently skip -- and R2 deliberately does NOT call it (there is nothing safe to stop at);
it full-pages via the existing `ingest.backfill_wallet`.

THE SILENT-GAP GUARD (the adversarial target -- a partial/failed backfill leaving a whale half-populated AND
ranked): the WRITE side is airtight here. `ingest.backfill_wallet` stamps `pm_whale.backfill_complete=1` ONLY
on a genuine complete pull (a short/empty page reached AND pulled==stored), else 'partial'; a mid-pagination
hard-failure RAISES before any upsert or stamp (nothing half-written); per-wallet ISOLATION means one wallet's
failure never aborts the run; a partial/failed whale is re-attempted on the next discovery (still not-complete)
and is VISIBLE in the run summary.
** THE R3 CONTRACT (load-bearing, do NOT skip): the READ side owns the exclusion. R3's candidate SELECTION
must gate `pm_whale.backfill_complete = 1` -- a whale marked partial/failed here must NEVER be written as a
`status='candidate'` row nor ranked. R2 hands R3 the flag; R3 must honor it. (Adversarial review 2026-08-29
also found the SHARED `stats.query_scoreboard` ranker selects backfill_complete as a display column but does
NOT filter on it -- a pre-existing inconsistency surfaced to Jack as a separate decision; the Stage-4 prospects
path is protected as long as R3 writes complete-only candidates.) **

RUNG 2 = discovery + first-sight backfill + the `pm_search_run` record. Candidate SELECTION + write is R3
(it also sets `n_candidates_written`); the /farm screen (column-sortable, cost-ROI-desc default, F-1 caveat,
refresh button) is R4. Injectable client/conn/clock -> tests run offline. Independent of R7 (order path
untouched -- imports only ingest/search/stdlib).

Spec: reports/prediction_markets/STAGE4_SEARCH_PLAN_2026-08-29.md sec 8A/9A/9C/9D.
"""
from __future__ import annotations

import json
from typing import Callable

from . import ingest, search

# Discovery bucket default (Q5: accept ~50/bucket). The leaderboard's COARSE buckets
# (Politics/Sports/Crypto/Tech/Mentions) are the only discovery axis; fine-category selection is R3.
DEFAULT_LEADERBOARD_CATEGORY = "Sports"
DEFAULT_LEADERBOARD_LIMIT = 250   # asked; the API caps ~50/bucket -- a POOL, not a cap we depend on.


async def discover_wallets(client, *, category: str = DEFAULT_LEADERBOARD_CATEGORY,
                           limit: int = DEFAULT_LEADERBOARD_LIMIT) -> list[tuple[str, str]]:
    """Leaderboard discovery pass: fetch one coarse bucket -> a de-duped pool of (wallet, user_name),
    wallet lowercased (the storage key), leaderboard-rank order preserved. NO backfill here (that is
    `ensure_backfilled`, and only for whales lacking a complete backfill)."""
    entries = await client.fetch_leaderboard(category=category, limit=limit)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for e in entries or []:
        w = str(getattr(e, "proxy_wallet", "") or "").lower()
        if not w or w in seen:
            continue
        seen.add(w)
        out.append((w, str(getattr(e, "user_name", "") or "")))
    return out


def _is_backfill_complete(conn, wallet: str) -> bool:
    """True iff the wallet ALREADY has a complete backfill (Ruling 1: never auto-re-pull such a whale)."""
    row = conn.execute(
        "SELECT backfill_complete FROM pm_whale WHERE wallet = ?", (wallet.lower(),)).fetchone()
    return bool(row) and bool(row[0])


async def ensure_backfilled(conn, wallet: str, *, client, now_ts: int, fetch_events=None,
                            limit: int = 50, cap: int = 8000, sleep=None) -> dict:
    """First-sight-only backfill (Ruling 1). If the wallet ALREADY has a complete backfill -> SKIP the
    pull (read from DB): return {'wallet', 'action': 'skipped_complete'}. Otherwise (never-seen OR a prior
    partial/failed) -> ONE full-page `ingest.backfill_wallet`, returning its result + 'action':'backfilled'
    (with 'verdict' complete|partial). NEVER auto-re-pulls a complete whale. A pull failure PROPAGATES so
    the caller can ISOLATE it (never a silent partial)."""
    wallet = wallet.lower()
    if _is_backfill_complete(conn, wallet):
        return {"wallet": wallet, "action": "skipped_complete"}
    res = await ingest.backfill_wallet(conn, wallet, client=client, now_ts=now_ts,
                                       fetch_events=fetch_events, limit=limit, cap=cap,
                                       backfill=True, sleep=sleep)
    res["action"] = "backfilled"
    return res


async def refresh_one(conn, wallet: str, *, client, now_ts: int, fetch_events=None,
                      limit: int = 50, cap: int = 8000, sleep=None) -> dict:
    """The REFRESH BUTTON's backend (R4 wires it): an ad-hoc FULL re-pull of ONE whale, on demand,
    IGNORING backfill_complete (Ruling 1: Jack's call, always -- no threshold, no auto). Same completeness
    safety as a first-sight backfill: a failed/partial refresh stamps backfill_complete correctly and never
    half-ranks. Stamps `pm_whale.last_refresh_ts` -> the UI 'last updated'."""
    wallet = wallet.lower()
    res = await ingest.refresh_wallet(conn, wallet, client=client, now_ts=now_ts,
                                      fetch_events=fetch_events, limit=limit, cap=cap, sleep=sleep)
    res["action"] = "refreshed"
    return res


def open_search_run(conn, *, started_ts: int, leaderboard_category: str, leaderboard_limit: int,
                    min_resolved: int, recency_window_days: int, thin_sample_target: int) -> int:
    """Insert a `pm_search_run` row (status='running') and return its run_id. Records the ruled knobs in
    effect so the run -- and the candidates R3 will stamp with this run_id -- are reproducible + auditable."""
    cur = conn.execute(
        "INSERT INTO pm_search_run (started_ts, leaderboard_category, leaderboard_limit, min_resolved, "
        "recency_window_days, thin_sample_target, status) VALUES (?, ?, ?, ?, ?, ?, 'running')",
        (started_ts, leaderboard_category, leaderboard_limit, min_resolved, recency_window_days,
         thin_sample_target))
    conn.commit() if hasattr(conn, "commit") else None
    return int(cur.lastrowid)


def close_search_run(conn, run_id: int, *, finished_ts: int, n_discovered: int, n_backfilled: int,
                     status: str, summary: str, n_candidates_written: int = 0) -> None:
    """Finalize a `pm_search_run` row. `n_candidates_written` stays 0 at R2 -- selection + write is R3,
    which updates this row. `status` is 'ok' | 'error'; `summary` carries the per-verdict breakdown JSON."""
    conn.execute(
        "UPDATE pm_search_run SET finished_ts = ?, n_discovered = ?, n_backfilled = ?, "
        "n_candidates_written = ?, status = ?, summary = ? WHERE run_id = ?",
        (finished_ts, n_discovered, n_backfilled, n_candidates_written, status, summary, run_id))
    conn.commit() if hasattr(conn, "commit") else None


async def run_search(conn, *, client, clock: Callable[[], int],
                     category: str = DEFAULT_LEADERBOARD_CATEGORY,
                     leaderboard_limit: int = DEFAULT_LEADERBOARD_LIMIT,
                     min_resolved: int = search.DEFAULT_MIN_RESOLVED_FLOOR,
                     recency_window_days: int = search.DEFAULT_RECENCY_DAYS,
                     thin_sample_target: int = search.DEFAULT_THIN_TARGET,
                     fetch_events=None, limit: int = 50, cap: int = 8000, sleep=None) -> dict:
    """RUNG 2 orchestrator: open a run -> discover a wallet pool -> first-sight-backfill each (Ruling 1) with
    PER-WALLET ISOLATION -> close the run with a visible per-verdict breakdown. Writes `pm_closed_position`
    (via backfill) + one `pm_search_run` row. Does NOT write candidates (that is R3). Every wallet lands in
    exactly one bucket so nothing is silent:
        skipped_complete  -- had a complete backfill; NOT pulled (Ruling 1)
        backfilled_complete -- pulled to a genuine complete verdict (rankable)
        backfilled_partial  -- pulled but cap-truncated (backfill_complete=0 -> NOT ranked; retries next run)
        failed              -- pull raised after retries (isolated; NOT ranked; retries next run)
    Returns a summary dict. `clock()` supplies started/per-pull/finished timestamps (inject in tests)."""
    started = clock()
    run_id = open_search_run(conn, started_ts=started, leaderboard_category=category,
                             leaderboard_limit=leaderboard_limit, min_resolved=min_resolved,
                             recency_window_days=recency_window_days, thin_sample_target=thin_sample_target)
    discovered: list[tuple[str, str]] = []
    counts = {"skipped_complete": 0, "backfilled_complete": 0, "backfilled_partial": 0, "failed": 0}
    per_failed: list[dict] = []
    per_partial: list[str] = []
    status = "ok"
    error_detail = None
    try:
        discovered = await discover_wallets(client, category=category, limit=leaderboard_limit)
        for wallet, _name in discovered:
            try:
                res = await ensure_backfilled(conn, wallet, client=client, now_ts=clock(),
                                              fetch_events=fetch_events, limit=limit, cap=cap, sleep=sleep)
                if res.get("action") == "skipped_complete":
                    counts["skipped_complete"] += 1
                elif res.get("verdict") == "complete":
                    counts["backfilled_complete"] += 1
                else:
                    counts["backfilled_partial"] += 1
                    per_partial.append(wallet)
            except Exception as e:                       # per-wallet ISOLATION: one failure never aborts the run
                counts["failed"] += 1
                per_failed.append({"wallet": wallet, "error": repr(e)[:200]})
    except Exception as e:                               # discovery itself failed -> the whole run is 'error'
        status = "error"
        error_detail = repr(e)[:200]
    finally:
        # ALWAYS close the run row (finally): an unexpected raise anywhere above can never strand a
        # pm_search_run row in 'running' -- a crashed run is RECORDED (status stays 'error' if the outer
        # except set it), never left ambiguously open (visible, not silent).
        n_backfilled = counts["backfilled_complete"] + counts["backfilled_partial"]
        summary = json.dumps({"counts": counts, "partial_wallets": per_partial,
                              "failed": per_failed, "error": error_detail})
        close_search_run(conn, run_id, finished_ts=clock(), n_discovered=len(discovered),
                         n_backfilled=n_backfilled, status=status, summary=summary)
    return {"run_id": run_id, "n_discovered": len(discovered), "n_backfilled": n_backfilled,
            "status": status, "wallets": [w for w, _ in discovered], **counts}


# ════════════════════════════════════════════════════════════════════════════════════════════════
# RUNG 3 -- candidate SELECTION + WRITE (the funnel's candidate stage). DB-only core (offline-testable);
# the /positions recency refresh (network) is a separate injected step. Composed by `pm_cli search`:
#   run_search (R2) -> stats.rollup -> refresh_positions_for -> select_and_write_candidates (R3)
# ════════════════════════════════════════════════════════════════════════════════════════════════

async def refresh_positions_for(conn, wallets, *, client, now_ts: int) -> dict:
    """Refresh /positions -> pm_open_position for `wallets` (the open-position RECENCY proxy, Q2). PER-WALLET
    ISOLATION: one wallet's failure never aborts the pass -- that wallet simply lacks the open-position signal
    and can still qualify via settled-recency (last_resolved_ts). Network; inject `client` in tests. Returns
    per-wallet ok/failed."""
    ok = 0
    per_failed: list[dict] = []
    for w in sorted({(x or "").lower() for x in (wallets or []) if x}):
        try:
            await ingest.refresh_open_positions(conn, w, client=client, now_ts=now_ts)
            ok += 1
        except Exception as e:
            per_failed.append({"wallet": w, "error": repr(e)[:200]})
    return {"ok": ok, "failed": len(per_failed), "per_failed": per_failed}


def build_wallet_category_stats(conn, wallets) -> list:
    """Read pm_category_stats for `wallets` into `search.WalletCategoryStat` rows for select_candidates.

    ★ GATED backfill_complete=1 (the R2->R3 contract, and the SAME gate as stats.query_scoreboard): a
    partial/failed-backfill whale is NEVER a candidate. The JOIN to pm_whale drops a stats row that has no
    pm_whale row (unknown completeness -> excluded), and the `backfill_complete=1` predicate drops a
    partial one -- so only fully-backfilled whales reach selection. The open-position RECENCY proxy
    (has_open_position) is EXISTS a pm_open_position row for (wallet, category); last_resolved_ts + roi +
    win_rate + n_resolved come from pm_category_stats. Empty input -> []. Read-only."""
    wset = sorted({(w or "").lower() for w in (wallets or []) if w})
    if not wset:
        return []
    # CHUNK the IN(...) lists (the pool is ~50, but never LEAN on the API's ~50/bucket cap -- a future wider
    # discovery source must not blow SQLite's bound-variable limit). Collect the open-position pairs + the
    # gated stats rows across chunks, THEN build (has_open_position needs the full open-pairs set first).
    CHUNK = 500
    open_pairs: set = set()
    stat_rows: list = []
    for i in range(0, len(wset), CHUNK):
        chunk = wset[i:i + CHUNK]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            "SELECT DISTINCT wallet, category FROM pm_open_position WHERE wallet IN (%s)" % ph, chunk
        ):
            open_pairs.add(((r["wallet"] or "").lower(), (r["category"] or "").strip().lower()))
        for r in conn.execute(
            "SELECT s.wallet, s.category, s.n_resolved, s.roi, s.win_rate, s.last_resolved_ts, w.user_name "
            "FROM pm_category_stats s JOIN pm_whale w ON s.wallet = w.wallet "
            "WHERE s.wallet IN (%s) AND COALESCE(w.backfill_complete, 0) = 1 "
            "ORDER BY s.wallet, s.category" % ph, chunk
        ):
            stat_rows.append(((r["wallet"] or "").lower(), (r["category"] or "").strip().lower(),
                              int(r["n_resolved"] or 0), r["roi"], r["last_resolved_ts"],
                              r["win_rate"], r["user_name"]))
    return [search.WalletCategoryStat(
                wallet=wal, category=cat, n_resolved=n, roi=roi, last_resolved_ts=last_ts,
                has_open_position=((wal, cat) in open_pairs), win_rate=win, user_name=name)
            for wal, cat, n, roi, last_ts, win, name in stat_rows]


def write_candidates(conn, candidates, *, run_id: int, now_ts: int) -> int:
    """Write each Candidate as a FUNNEL candidate: pm_watchlist(status='candidate', active=1, source='search',
    search_run_id=run_id) + a paired pm_roster row. Mirrors the pinned-seed precedent (paper.py) but 'candidate'.

    ★ INSERT OR IGNORE == NO-CLOBBER idempotency: an existing (wallet, category) row is LEFT UNTOUCHED -- so
    search NEVER un-pins a human's promotion ('pinned'), NEVER resurrects a removed pair (active=0), and a
    re-run NEVER double-writes. Only a genuinely NEW (wallet, category) is inserted. ★ THREE-BASES INVARIANT:
    touches ONLY the funnel (pm_watchlist) + the roster (pm_roster) -- NO completed/paper/live base is written;
    a candidate DISPLAYS the completed basis, it does not create paper or live rows. ★ NO AUTO-PROMOTION / NO
    AUTO-PAPER: status is 'candidate', never 'pinned' -- promotion (candidate->pinned) stays the manual /farm
    action (farm_actions.promote_to_watchlist), and the poller paper-trades PINNED rows only. Returns the count
    of NEWLY-written candidate rows."""
    candidates = list(candidates)
    if not candidates:
        return 0
    # ATOMIC batch (BEGIN IMMEDIATE): the paired pm_roster + pm_watchlist writes are all-or-nothing, so a crash
    # mid-loop can never leave a candidate half-written (a watchlist row without its roster row, or the reverse).
    # Mirrors farm_actions.promote_to_live's transaction discipline; OR IGNORE still keeps every write no-clobber.
    n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for c in candidates:
            wal, cat = (c.wallet or "").lower(), (c.category or "").strip().lower()
            conn.execute(
                "INSERT OR IGNORE INTO pm_roster (wallet, category, user_name, source, added_ts, active) "
                "VALUES (?, ?, ?, 'search', ?, 1)", (wal, cat, c.user_name or "", now_ts))
            cur = conn.execute(
                "INSERT OR IGNORE INTO pm_watchlist (wallet, category, added_ts, source, status, search_run_id, "
                "active, updated_ts) VALUES (?, ?, ?, 'search', 'candidate', ?, 1, ?)",
                (wal, cat, now_ts, run_id, now_ts))
            n += (cur.rowcount or 0)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return n


def select_and_write_candidates(conn, wallets, *, run_id: int, now_ts: int,
                                min_resolved: int = search.DEFAULT_MIN_RESOLVED_FLOOR,
                                recency_window_days: int = search.DEFAULT_RECENCY_DAYS,
                                thin_sample_target: int = search.DEFAULT_THIN_TARGET,
                                allowlist=search.CATEGORY_ALLOWLIST) -> dict:
    """RUNG 3 orchestrator (DB-only): read GATED stats for the run's discovered `wallets` -> R1's
    select_candidates (N>=50 + <target top-10 thin-sample fallback, 30d open-position/settled recency GATE,
    15-category allowlist, cost-ROI rank NEVER win%) -> write_candidates -> stamp pm_search_run
    .n_candidates_written. Assumes stats.rollup + refresh_positions_for already ran (caller's earlier steps).
    Returns the selection summary + n_written (newly-written; a re-run of already-written candidates writes 0)."""
    stats_rows = build_wallet_category_stats(conn, wallets)
    result = search.select_candidates(
        stats_rows, now_ts=now_ts, min_resolved=min_resolved, recency_window_days=recency_window_days,
        thin_sample_target=thin_sample_target, allowlist=allowlist)
    n_written = write_candidates(conn, result.candidates, run_id=run_id, now_ts=now_ts)
    conn.execute("UPDATE pm_search_run SET n_candidates_written = ? WHERE run_id = ?", (n_written, run_id))
    if hasattr(conn, "commit"):
        conn.commit()
    # n_stats_rows makes the silent-0 visible: 0 gated stats rows read from a non-empty backfilled pool almost
    # always means stats.rollup did not run (or ran empty) -- distinct from "ran fine, nothing qualified". The
    # caller (pm_cli search) can WARN when n_stats_rows==0 but wallets were backfilled this run.
    return {"n_selected": len(result.candidates), "n_written": n_written, "n_stats_rows": len(stats_rows),
            "excluded": result.excluded, "thin_sample_categories": result.thin_sample_categories}
