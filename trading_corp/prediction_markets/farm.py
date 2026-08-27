"""Farm-league READ-ONLY queries (CP3b-1). Reads EXISTING tables only -- pm_watchlist (farm status),
pm_roster (last_polled_ts), pm_category_stats (+ score/onesided joins = the scoreboard context), pm_whale
(user_name/backfill), pm_paper_trade (open count). NO new table, NO paper_rollup (that is CP3b-4). Mirrors
`stats.query_scoreboard`'s row shape so the page reuses the SAME caveat columns + `stats.scoreboard_flags`
(one deriver -- the farm page and the scoreboard can never diverge on a caveat).

Builds against the SHIPPED five-status pm_paper_trade (open | pending_adjudication | closed | stale | void)
-- NOT the pre-CP3a spec (Finding A). Today every paper row is 'open'; the other four states have never
existed in live data, so any status breakdown must read them as "none yet", never "broken".

THREE-STATE ZERO (Jack 2026-08-25): `last_polled_ts` is a TIMESTAMP (CP3a amendment G) precisely so a "0" is
not ambiguous across three genuinely different states, which MUST stay visually distinct:
  never_polled      last_polled_ts IS NULL    -- an absence of OBSERVATION, not of activity (never read as "does nothing")
  polled_none_open  polled, 0 open            -- observed, nothing currently open
  polled_has_open   polled, n open            -- observed, n currently open
"""
from __future__ import annotations

from . import stats

PINNED = "pinned"
CANDIDATE = "candidate"

# three-state poll tokens -- the value the template switches its badge on (kept here, not in the template)
POLL_NEVER = "never_polled"
POLL_NONE_OPEN = "polled_none_open"
POLL_HAS_OPEN = "polled_has_open"

# human labels for the three states (module-owned so the page + any report read the same words).
# never_polled is deliberately NOT "0" and NOT "—": it is an absence of observation, stated in words.
POLL_LABELS = {
    POLL_NEVER: "never polled",
    POLL_NONE_OPEN: "polled · 0 open",
    POLL_HAS_OPEN: "open",   # the template appends the count (e.g. "3 open")
}


def poll_state(last_polled_ts, n_open) -> str:
    """The three-state zero. A NULL last_polled_ts is NEVER-observed -- distinct from 'polled, nothing open'.
    'has open' means n currently-open pm_paper_trade rows (the 'OPEN' label), which today equals total rows
    (every row is 'open'); once the adjudicator runs, closed/stale rows do not count as open here."""
    if last_polled_ts is None:
        return POLL_NEVER
    return POLL_HAS_OPEN if (n_open or 0) > 0 else POLL_NONE_OPEN


def farm_categories(conn, status: str = PINNED) -> list[str]:
    """Categories with >=1 pair at this farm status -- DRIVES THE TABS (data-driven, never a hardcoded 4;
    MLB/UFC/NBA/Fed being the 'live' set is a P3 account-attachment concern, not this page's tabs)."""
    # Stage-0 funnel gate (008): active=1 -> a removed category (all its pairs off-funnel) yields NO tile.
    # This read IS the tile/tab set: miss the gate and a tile renders for a category outside the ruled set.
    return [r["category"] for r in conn.execute(
        "SELECT DISTINCT category FROM pm_watchlist WHERE status=? AND active=1 AND category IS NOT NULL "
        "ORDER BY category",
        (status,)).fetchall()]


# Mirrors stats.query_scoreboard's SELECT (same caveat columns) but: base table = pm_watchlist (so EVERY
# pinned pair is DISPLAYED, never filtered out); NO n_resolved gate; + last_polled_ts + the open-paper count.
# pm_category_stats is LEFT-joined so a pair with no stats row still shows (honest-empty), not dropped.
#
# PINNED vs CANDIDATE stats basis (Stage 1 fix):
#   PINNED  -> stats come from pm_paper_category_stats (forward paper data, gamma-adjudicated).
#   CANDIDATE -> stats come from pm_category_stats (completed legacy whale data; original behaviour).
# This is the "substitution bug" fix: the pinned list previously sourced numbers from pm_category_stats
# (legacy completed-lane), which (a) has the loss-omission finding baked in and (b) mixes legacy whale
# history with forward paper performance. The two SQL variants share the same SELECT column aliases so
# the rendering layer is unchanged.

_ROWS_SQL_CANDIDATE = (
    "SELECT wl.wallet AS wallet, wl.category AS category, wl.status AS farm_status, "
    "  r.last_polled_ts AS last_polled_ts, COALESCE(op.n_open, 0) AS n_open, "
    "  cs.n_resolved AS n_resolved, cs.wins AS wins, cs.losses AS losses, cs.win_rate AS win_rate, "
    "  cs.net_realized_pnl AS net_realized_pnl, cs.total_bought AS total_bought, cs.cost_basis AS cost_basis, "
    "  cs.roi AS roi, cs.roi_notional AS roi_notional, cs.avg_win_price AS avg_win_price, "
    "  cs.n_excluded AS n_excluded, cs.excluded_pnl AS excluded_pnl, cs.n_anomaly AS n_anomaly, "
    "  cs.dq_count_pct AS dq_count_pct, cs.dq_dollar_pct AS dq_dollar_pct, cs.data_quality AS data_quality, "
    "  cs.n_condition_ids AS n_condition_ids, cs.two_sided_pct AS two_sided_pct, "
    "  cs.single_game_pct AS single_game_pct, "
    "  ss.score AS score, "
    "  COALESCE(w.backfill_complete, 0) AS backfill_complete, w.user_name AS user_name, "
    "  os.roi AS onesided_roi, os.n_resolved AS onesided_n, os.is_upper_bound AS onesided_is_upper_bound "
    "FROM pm_watchlist wl "
    "LEFT JOIN pm_category_stats cs ON cs.wallet=wl.wallet AND cs.category=wl.category "
    "LEFT JOIN pm_score_snapshot ss ON ss.wallet=wl.wallet AND ss.category=wl.category AND ss.routine=? "
    "LEFT JOIN pm_whale w ON w.wallet=wl.wallet "
    "LEFT JOIN pm_category_onesided_stats os ON os.wallet=wl.wallet AND os.category=wl.category "
    "LEFT JOIN pm_roster r ON r.wallet=wl.wallet AND r.category=wl.category "
    "LEFT JOIN (SELECT wallet, category, COUNT(*) AS n_open FROM pm_paper_trade WHERE status='open' "
    "          GROUP BY wallet, category) op ON op.wallet=wl.wallet AND op.category=wl.category "
    "WHERE wl.status=? AND wl.active=1"   # Stage-0 funnel gate (008): removed pairs off the candidate list
)

# PINNED list: stats sourced from pm_paper_category_stats (paper-basis, gamma-adjudicated).
# Column aliases deliberately match _ROWS_SQL_CANDIDATE so the rendering layer is unchanged.
# Paper-specific columns (n_closed -> aliased n_resolved, net_paper_pnl -> net_realized_pnl, etc.) are
# mapped here; columns that only exist on pm_category_stats (roi_notional, avg_win_price, n_excluded,
# excluded_pnl, n_anomaly, dq_*) are returned as NULL for honest-empty rendering.
_ROWS_SQL_PINNED = (
    "SELECT wl.wallet AS wallet, wl.category AS category, wl.status AS farm_status, "
    "  r.last_polled_ts AS last_polled_ts, COALESCE(op.n_open, 0) AS n_open, "
    # paper stats aliased to the shared column names the rendering layer expects
    "  pcs.n_closed AS n_resolved, pcs.wins AS wins, pcs.losses AS losses, pcs.win_rate AS win_rate, "
    "  pcs.net_paper_pnl AS net_realized_pnl, NULL AS total_bought, pcs.cost_basis AS cost_basis, "
    "  pcs.roi AS roi, NULL AS roi_notional, NULL AS avg_win_price, "
    # paper has no quarantine / DQ columns; honest-empty NULLs
    "  NULL AS n_excluded, NULL AS excluded_pnl, NULL AS n_anomaly, "
    "  NULL AS dq_count_pct, NULL AS dq_dollar_pct, NULL AS data_quality, "
    "  NULL AS n_condition_ids, NULL AS two_sided_pct, NULL AS single_game_pct, "
    "  ss.score AS score, "
    "  COALESCE(w.backfill_complete, 0) AS backfill_complete, w.user_name AS user_name, "
    "  NULL AS onesided_roi, NULL AS onesided_n, NULL AS onesided_is_upper_bound, "
    # paper-native columns exposed for the pinned rendering
    "  pcs.n_closed AS n_closed, pcs.net_paper_pnl AS net_paper_pnl, "
    "  pcs.n_open AS n_open_paper, pcs.n_stale AS n_stale, pcs.n_void AS n_void "
    "FROM pm_watchlist wl "
    "LEFT JOIN pm_paper_category_stats pcs ON pcs.wallet=wl.wallet AND pcs.category=wl.category "
    "LEFT JOIN pm_score_snapshot ss ON ss.wallet=wl.wallet AND ss.category=wl.category AND ss.routine=? "
    "LEFT JOIN pm_whale w ON w.wallet=wl.wallet "
    "LEFT JOIN pm_roster r ON r.wallet=wl.wallet AND r.category=wl.category "
    "LEFT JOIN (SELECT wallet, category, COUNT(*) AS n_open FROM pm_paper_trade WHERE status='open' "
    "          GROUP BY wallet, category) op ON op.wallet=wl.wallet AND op.category=wl.category "
    "WHERE wl.status=? AND wl.active=1"   # Stage-0 funnel gate (008): removed pairs off the pinned list
)


def farm_rows(conn, *, status: str = PINNED, category: str | None = None,
              routine: str = "net_roi") -> list[dict]:
    """EVERY (wallet, category) pair at `status` -- board-locked, DISPLAYED, never filtered or ranked out
    (no min_resolved gate; a 0/quarantined pair still shows, with its reason). Ordered by display name then
    wallet then category (stable -- a roster, NOT a ranking). Adds chalk/contested + scoreboard_flags (the
    shared deriver), poll_state (three-state zero), and all_quarantined (n_resolved=0 but n_excluded>0 ->
    the 4751346/nfl case: 'quarantined', not a bare 0 reading as 'no activity').

    Stage 1 basis fix: PINNED rows source stats from pm_paper_category_stats (paper, gamma-adjudicated);
    CANDIDATE rows keep pm_category_stats (legacy completed-lane). This is the load-bearing change that
    prevents legacy completed-lane numbers (which have the /closed-positions loss-omission baked in) from
    being displayed as the forward paper performance of the pinned list."""
    q = _ROWS_SQL_PINNED if status == PINNED else _ROWS_SQL_CANDIDATE
    params: list = [routine, status]
    if category:
        q += " AND wl.category=?"
        params.append(category)
    q += " ORDER BY (w.user_name IS NULL), w.user_name COLLATE NOCASE, wl.wallet, wl.category"
    out = []
    for row in conn.execute(q, params).fetchall():
        d = dict(row)
        awp = d.get("avg_win_price")
        d["chalk"] = (awp is not None and awp >= stats.CHALK_HI)
        d["contested"] = (awp is not None and awp < stats.CONTESTED_LO)
        d["flags"] = stats.scoreboard_flags(d)
        d["poll_state"] = poll_state(d.get("last_polled_ts"), d.get("n_open"))
        d["all_quarantined"] = ((d.get("n_resolved") or 0) == 0 and (d.get("n_excluded") or 0) > 0)
        out.append(d)
    return out


def farm_summary(conn) -> dict:
    """Counts for the page header + the verification report: total pinned, per-three-state, categories,
    quarantined/unknown pair counts, and the candidate count (0 until Search runs). Read-only."""
    pinned = farm_rows(conn, status=PINNED)
    states = {POLL_NEVER: 0, POLL_NONE_OPEN: 0, POLL_HAS_OPEN: 0}
    for r in pinned:
        states[r["poll_state"]] = states.get(r["poll_state"], 0) + 1
    # Stage-0 funnel gate (008): removed candidates (if any) are off-funnel and not counted.
    n_cand = conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE status=? AND active=1", (CANDIDATE,)).fetchone()[0]
    return {
        "n_pinned": len(pinned),
        "pinned_categories": farm_categories(conn, PINNED),
        "states": states,
        "n_quarantined_pairs": sum(1 for r in pinned if r["all_quarantined"]),
        "n_unknown_pairs": sum(1 for r in pinned if r["category"] == "unknown"),
        "n_candidates": n_cand,
        "candidate_categories": farm_categories(conn, CANDIDATE),
    }
