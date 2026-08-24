"""Drill-through position reads for Prediction Markets (CP2 Phase 3, Ruling R5).

Every scoreboard aggregate must reach its rows, and the drill's count MUST reconcile with the
aggregate cell it came from (the HARD BAR -- the Phase-3 analogue of the silent PK loss). This module
is the read layer behind the ONE shared renderer (web/templates/partials/pm_position_rows.html):
`drill_rows()` returns the exact row set a cell drills to, and `reconcile()` proves the count matches
the named pm_category_stats aggregate.

THREE reconciliation subtleties are load-bearing (a naive drill produces a FALSE mismatch):
  * two_sided + single_game are computed over ALL rows (structural), NOT scoreable-filtered.
  * two_sided reconciles on DISTINCT condition_id count (= n_two_sided), NOT raw row count
    (each two-sided condition_id contributes >=2 rows).
  * single_game is NOT stored per-row (no market_type column; market_type_source seam, deferred
    §13A(d)) -- the drill REUSES category.classify_market_shape, the SAME classifier the rollup used,
    so a change to the classifier fails a reconciliation test LOUDLY instead of silently desyncing the
    drill from the cell.

Reads ONLY prediction_markets.db, through the ONE §3A scoreable predicate (db.scoreable_where) --
never re-derives `pnl_suspect = 0` by hand.

Spec: reports/prediction_markets/P3_KICKOFF_2026-08-24.md; P2_PLAN.md §6.1/§6.3.
"""
from __future__ import annotations

import json
from collections import defaultdict

from .db import scoreable_where
from .category import classify_market_shape

# Columns the shared row renderer needs. condition_id is included so the renderer can group/mark
# two-sided legs; category_source is included because a mis-categorized row explains a weird stat.
_ROW_COLS = (
    "wallet, condition_id, event_slug, slug, title, category, category_source, outcome, outcome_index, "
    "avg_price, total_bought, cost_basis, realized_pnl, cur_price, won, "
    "pnl_suspect, suspect_reason, pnl_anomaly, anomaly_reason, resolved_ts"
)

# The drills a scoreboard cell can open. 'all' backs the whale-detail default table (scoreable toggle).
DRILLS = ("scoreable", "won", "two_sided", "single_game", "quarantined", "all")

# drill -> (pm_category_stats column its count reconciles to, measure). measure 'rows' = len(rows);
# 'distinct_cids' = distinct condition_ids (two_sided). 'all' has no single aggregate (context view).
_RECONCILES: dict[str, tuple[str, str]] = {
    "scoreable":   ("n_resolved",    "rows"),
    "won":         ("wins",          "rows"),
    "two_sided":   ("n_two_sided",   "distinct_cids"),
    "single_game": ("n_single_game", "rows"),
    "quarantined": ("n_excluded",    "rows"),
}

# Human labels for the drill panel header (what the user is looking at + how it reconciles).
DRILL_LABELS: dict[str, str] = {
    "scoreable":   "scoreable rows (pnl_suspect = 0)",
    "won":         "won scoreable rows (by avg price)",
    "two_sided":   "both-outcome condition_ids (held on >1 outcome)",
    "single_game": "single-game rows",
    "quarantined": "quarantined rows (excluded; see suspect_reason)",
    "all":         "all rows (scoreable + quarantined)",
}


def _fetch(conn, wallet: str, category: str, where: str, params: list) -> list[dict]:
    q = ("SELECT " + _ROW_COLS + " FROM pm_closed_position WHERE wallet = ? AND category = ?"
         + ((" AND " + where) if where else "")
         + " ORDER BY resolved_ts DESC, condition_id, outcome_index")
    return [dict(r) for r in conn.execute(q, [wallet, category, *params]).fetchall()]


def drill_rows(conn, wallet: str, category: str, drill: str) -> list[dict]:
    """Rows for one drill, matching the aggregate's semantics EXACTLY so the count reconciles."""
    wallet = (wallet or "").lower()
    if drill == "scoreable":
        return _fetch(conn, wallet, category, scoreable_where(), [])
    if drill == "won":
        return _fetch(conn, wallet, category, scoreable_where() + " AND won = 1", [])
    if drill == "quarantined":
        return _fetch(conn, wallet, category, "pnl_suspect = 1", [])
    if drill == "all":
        return _fetch(conn, wallet, category, "", [])
    if drill == "two_sided":
        # ALL rows on condition_ids the whale held on >1 outcome_index (structural; NOT scoreable-filtered)
        rows = _fetch(conn, wallet, category, "", [])
        outs: dict[str, set] = defaultdict(set)
        for r in rows:
            outs[r["condition_id"]].add(r["outcome_index"])
        two = {cid for cid, s in outs.items() if len(s) > 1}
        return [r for r in rows if r["condition_id"] in two]
    if drill == "single_game":
        # ALL rows classified single_game by the SAME classifier the rollup used (not stored per-row)
        rows = _fetch(conn, wallet, category, "", [])
        return [r for r in rows
                if classify_market_shape(r["slug"], r["event_slug"], r["title"]) == "single_game"]
    raise ValueError("unknown drill: %r" % (drill,))


def won_avg_price(rows: list[dict]) -> float | None:
    """AVG(avg_price) over won rows -- reconciles with pm_category_stats.avg_win_price (the SECOND
    reconciliation the won drill owes: row count == wins AND this mean == avg_win_price)."""
    vals = [r["avg_price"] for r in rows if isinstance(r.get("avg_price"), (int, float))]
    return (sum(vals) / len(vals)) if vals else None


def reconcile(conn, wallet: str, category: str, drill: str, rows: list[dict]) -> dict | None:
    """Compare a drill's rows to its named pm_category_stats aggregate. Returns
    {aggregate, measure, expected, actual, ok} or None for drills with no single aggregate ('all').
    two_sided reconciles on DISTINCT condition_ids; the rest on row count. This is what the drill
    panel renders ('showing N -- reconciles with <cell> = M') and what the HARD-BAR test asserts."""
    if drill not in _RECONCILES:
        return None
    agg_col, measure = _RECONCILES[drill]
    r = conn.execute(
        "SELECT %s AS v FROM pm_category_stats WHERE wallet = ? AND category = ?" % agg_col,
        [(wallet or "").lower(), category]).fetchone()
    expected = int((r["v"] if r is not None and r["v"] is not None else 0))
    actual = len({row["condition_id"] for row in rows}) if measure == "distinct_cids" else len(rows)
    return {"aggregate": agg_col, "measure": measure, "expected": expected,
            "actual": actual, "ok": (actual == expected)}


# ── whale-detail reads (the "why is this ranked here" destination) ──────────────────────────────

def whale_row(conn, wallet: str) -> dict | None:
    """pm_whale row (user_name, freshness, backfill_complete) or None for an unknown wallet."""
    r = conn.execute("SELECT * FROM pm_whale WHERE wallet = ?", [(wallet or "").lower()]).fetchone()
    return dict(r) if r is not None else None


def whale_categories(conn, wallet: str) -> list[dict]:
    """The (wallet, category) slices this whale has stats for -- the overview on /whale/{wallet}."""
    return [dict(r) for r in conn.execute(
        "SELECT category, n_resolved, n_excluded, roi, net_realized_pnl, avg_win_price "
        "FROM pm_category_stats WHERE wallet = ? ORDER BY category", [(wallet or "").lower()]).fetchall()]


def category_stats_row(conn, wallet: str, category: str) -> dict | None:
    """The pm_category_stats aggregate row for (wallet, category) -- the caveat profile + the drill
    reconciliation targets. None if the slice has no rows."""
    r = conn.execute("SELECT * FROM pm_category_stats WHERE wallet = ? AND category = ?",
                     [(wallet or "").lower(), category]).fetchone()
    return dict(r) if r is not None else None


def onesided_row(conn, wallet: str, category: str) -> dict | None:
    """The one-sided directional slice (UPPER BOUND, §13A(f)) for (wallet, category), or None."""
    r = conn.execute("SELECT * FROM pm_category_onesided_stats WHERE wallet = ? AND category = ?",
                     [(wallet or "").lower(), category]).fetchone()
    return dict(r) if r is not None else None


def score_decomposition(conn, wallet: str, category: str) -> dict[str, dict]:
    """Both routines' score snapshots for (wallet, category), keyed by routine, with params_json
    unpacked -- so a rank is auditable ('why ranked': score = wilson_lcb x edge_factor)."""
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT routine, score, wilson_lcb, edge_factor, params_json FROM pm_score_snapshot "
        "WHERE wallet = ? AND category = ?", [(wallet or "").lower(), category]).fetchall():
        d = dict(r)
        try:
            d["params"] = json.loads(d.get("params_json") or "{}")
        except (TypeError, ValueError):
            d["params"] = {}
        out[r["routine"]] = d
    return out
