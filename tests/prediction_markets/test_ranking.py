"""Tests for the ranking adapter in stats.py vs known-good kalshi_whale_stats primitives. Offline.

Spec: reports/prediction_markets/P1_PLAN.md §7, §11.
"""
import json
from pathlib import Path

from trading_corp.prediction_markets import db, ingest, stats
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow
from trading_corp.data.kalshi_whale_stats import wilson_lcb_95

NOW = 1_700_000_000
_FIX = Path(__file__).parent / "fixtures" / "closed_positions"


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


class _Cli:
    def __init__(self, page):
        self._page = page

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        return [ClosedPositionRow.from_api(r) for r in (self._page if offset == 0 else [])]


async def _noev(slug, **kw):
    return []


def test_score_net_roi_matches_primitive():
    score, w, e = stats.score_net_roi(7, 10, 0.2)
    assert abs(w - wilson_lcb_95(7, 10)) < 1e-9
    assert abs(e - 1.2) < 1e-9
    assert abs(score - w * 1.2) < 1e-9


def test_edge_factor_clip_bounds():
    assert abs(stats.score_net_roi(5, 10, 3.0)[2] - 3.0) < 1e-9    # roi clipped +2.0 -> edge 3.0
    assert abs(stats.score_net_roi(5, 10, -1.0)[2] - 0.5) < 1e-9   # roi clipped -0.5 -> edge 0.5
    assert abs(stats.score_net_roi(0, 0, 0.0)[0] - 0.0) < 1e-9     # n=0 -> wilson 0 -> score 0


def test_recency_weighting_favors_recent_outcomes():
    old = NOW - 200 * 86400
    improving = [(False, old)] * 5 + [(True, NOW)] * 5    # was losing, now winning
    declining = [(True, old)] * 5 + [(False, NOW)] * 5    # was winning, now losing
    s_imp, _, _, wr_imp, _ = stats.score_recency_weighted(improving, now_ts=NOW, roi=0.2)
    s_dec, _, _, wr_dec, _ = stats.score_recency_weighted(declining, now_ts=NOW, roi=0.2)
    assert wr_imp > 0.5 > wr_dec        # recent outcomes dominate the weighted rate
    assert s_imp > s_dec


def test_recency_weighted_equal_age_is_raw_rate():
    same_age = [(True, NOW)] * 5 + [(False, NOW)] * 5     # equal weights -> weighted rate == raw 0.5
    _, _, _, wr, _ = stats.score_recency_weighted(same_age, now_ts=NOW, roi=0.0)
    assert abs(wr - 0.5) < 1e-6


async def test_compute_scores_snapshot_both_routines(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xtestwhale", client=_Cli(_load("loser_mix_page.json")),
                                     now_ts=NOW, fetch_events=_noev)
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
        snaps = conn.execute("SELECT category, routine, params_json FROM pm_score_snapshot").fetchall()
    routines = {(s["category"], s["routine"]) for s in snaps}
    assert ("ufc", "net_roi") in routines and ("ufc", "recency_weighted") in routines
    assert ("mlb", "net_roi") in routines and ("mlb", "recency_weighted") in routines
    pj = json.loads(snaps[0]["params_json"])
    assert pj["excludes_suspect"] is True and "n_excluded" in pj
