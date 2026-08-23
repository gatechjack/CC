"""Tests for trading_corp.prediction_markets.stats (rollup + scoreboard). Offline.

Spec: reports/prediction_markets/P1_PLAN.md §6, §7, §11.
"""
import json
from pathlib import Path

from trading_corp.prediction_markets import db, ingest, stats
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow

_FIX = Path(__file__).parent / "fixtures" / "closed_positions"
NOW = 1_700_000_000


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


class _Cli:
    def __init__(self, page):
        self._page = page

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        return [ClosedPositionRow.from_api(r) for r in (self._page if offset == 0 else [])]


async def _noev(slug, **kw):
    return []


async def _ingest_page(tmp_path, page, wallet):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, wallet, client=_Cli(page), now_ts=NOW, fetch_events=_noev)
    return p


async def test_rollup_loser_mix_metrics(tmp_path):
    p = await _ingest_page(tmp_path, _load("loser_mix_page.json"), "0xtestwhale")
    with db.connect(p) as conn:
        n = stats.rollup(conn, now_ts=NOW)
        cs = {r["category"]: r for r in conn.execute("SELECT * FROM pm_category_stats").fetchall()}
    assert n == 2
    ufc = cs["ufc"]
    assert ufc["n_resolved"] == 2 and ufc["wins"] == 1 and ufc["losses"] == 1
    assert abs(ufc["net_realized_pnl"] - (400.0 - 550.0)) < 1e-6          # -150
    assert abs(ufc["total_bought"] - 1150.0) < 1e-6                       # NOTIONAL sum (600+550)
    assert abs(ufc["cost_basis"] - (600 * 0.60 + 550 * 0.55)) < 1e-6      # 662.5 real USDC cost
    assert abs(ufc["roi"] - (-150.0 / 662.5)) < 1e-6                      # RANKED: cost-based (§13 dec 11)
    assert abs(ufc["roi_notional"] - (-150.0 / 1150.0)) < 1e-6            # notional (NOT ranked)
    assert abs(ufc["avg_bet"] - (662.5 / 2)) < 1e-6                       # cost-based
    assert ufc["roi"] != ufc["roi_notional"]                             # unambiguously distinct
    assert abs(ufc["win_rate"] - 0.5) < 1e-6
    assert abs(ufc["avg_win_price"] - 0.60) < 1e-6                        # won ufc row avg_price
    assert ufc["n_excluded"] == 0 and ufc["data_quality"] is None
    mlb = cs["mlb"]
    assert mlb["n_resolved"] == 2 and abs(mlb["net_realized_pnl"] - (-100.0)) < 1e-6
    assert abs(mlb["cost_basis"] - (400 * 0.40 + 700 * 0.70)) < 1e-6      # 650
    assert abs(mlb["roi"] - (-100.0 / 650.0)) < 1e-6                      # cost-based


async def test_rollup_negrisk_quarantine_visible(tmp_path):
    p = await _ingest_page(tmp_path, _load("negrisk_event.json"),
                           "0x71ed0bc95433cdf1be29f43219725fce9addd9eb")
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)
        rows = conn.execute("SELECT * FROM pm_category_stats").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["n_resolved"] == 0 and r["n_excluded"] == 5          # all 5 legs quarantined
    assert r["data_quality"] == "contaminated"                    # 5/5 > 10%
    assert r["excluded_pnl"] < -1_000_000                         # ~ -574604.31*3 -535322.95 +24423.34


async def test_scoreboard_chalk_and_contested_flags(tmp_path):
    chalk_page = [
        {"proxyWallet": "0xc", "conditionId": "0xk1", "slug": "ufc-a-b-2026-01-01", "eventSlug": "ufc-a-b-2026-01-01",
         "avgPrice": 0.90, "totalBought": 900.0, "realizedPnl": 100.0, "curPrice": 1.0, "timestamp": 1},
        {"proxyWallet": "0xc", "conditionId": "0xk2", "slug": "ufc-c-d-2026-01-02", "eventSlug": "ufc-c-d-2026-01-02",
         "avgPrice": 0.88, "totalBought": 880.0, "realizedPnl": 120.0, "curPrice": 1.0, "timestamp": 2},
    ]
    p = await _ingest_page(tmp_path, chalk_page, "0xc")
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
        board = stats.query_scoreboard(conn, category="ufc", routine="net_roi", min_resolved=1)
    assert len(board) == 1
    assert board[0]["chalk"] is True and board[0]["contested"] is False   # avg_win_price 0.89
    assert board[0]["score"] is not None


async def test_cost_basis_zero_is_quarantined_ruling_a(tmp_path):
    # Ruling A (Jack, 2026-08-22): a row with avg_price=0 -> cost_basis=0 (no knowable cost) is
    # QUARANTINED (suspect_reason='no_cost_basis'), excluded from stats. The rollup must not raise
    # (div-by-zero guard remains as belt-and-braces: no scoreable rows -> cb=0 -> roi None).
    rows = [
        {"proxyWallet": "0xz", "conditionId": "0xz1", "slug": "ufc-z-y-2026-01-01", "eventSlug": "ufc-z-y-2026-01-01",
         "avgPrice": 0.0, "totalBought": 100.0, "realizedPnl": -50.0, "curPrice": 0.0, "timestamp": 1},
    ]
    p = await _ingest_page(tmp_path, rows, "0xz")
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)            # must not raise
        r = conn.execute("SELECT * FROM pm_category_stats WHERE category='ufc'").fetchone()
        row = conn.execute("SELECT pnl_suspect, suspect_reason, cost_basis FROM pm_closed_position").fetchone()
    assert row["pnl_suspect"] == 1 and row["suspect_reason"] == "no_cost_basis"   # QUARANTINED (Ruling A)
    assert r["n_resolved"] == 0 and r["n_excluded"] == 1                          # excluded from stats
    assert abs(r["excluded_pnl"] - (-50.0)) < 1e-6
    assert r["roi"] is None and r["roi_notional"] is None                         # no scoreable rows -> both None


async def test_notional_vs_cost_roi_both_retrievable_and_distinct(tmp_path):
    # both ROIs present, not confusable; cost < notional (avg<1) so the ranked (cost) ROI is more extreme.
    p = await _ingest_page(tmp_path, _load("loser_mix_page.json"), "0xtestwhale")
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)
        ufc = conn.execute(
            "SELECT roi, roi_notional, cost_basis, total_bought FROM pm_category_stats WHERE category='ufc'").fetchone()
    assert ufc["roi"] is not None and ufc["roi_notional"] is not None
    assert ufc["cost_basis"] < ufc["total_bought"]        # real cost < notional (avg_price < 1)
    assert abs(ufc["roi"]) > abs(ufc["roi_notional"])     # ranked (cost) ROI is the more extreme figure


class _FullPage:
    """Always returns a FULL page -> pagination never short-outs -> cap-hit -> PARTIAL verdict."""
    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        rows = [{"proxyWallet": "0xpart", "conditionId": "0xp%d" % (offset + j), "slug": "ufc-a-b-2026-01-01",
                 "eventSlug": "ufc-part-%d-2026-01-01" % (offset + j), "outcome": "Yes", "outcomeIndex": 0,
                 "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": 5.0, "curPrice": 1.0, "timestamp": offset + j}
                for j in range(limit)]
        return [ClosedPositionRow.from_api(r) for r in rows]


async def _noop_sleep(_s):
    return None


async def test_partial_wallet_excluded_from_ranking(tmp_path):
    # §13A(k): a PARTIAL backfill (cap-hit -> backfill_complete=0) is NOT scored and is flagged
    # INCOMPLETE-NOT-RANKED in the report; a COMPLETE wallet alongside it IS scored.
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        # complete wallet: arg matches loser_mix's proxyWallet (prod invariant: queried wallet == rows' proxy)
        await ingest.backfill_wallet(conn, "0xtestwhale", client=_Cli(_load("loser_mix_page.json")), now_ts=NOW, fetch_events=_noev)
        await ingest.backfill_wallet(conn, "0xpart", client=_FullPage(), now_ts=NOW, fetch_events=_noev, limit=2, cap=4, sleep=_noop_sleep)
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
        ok_snaps = conn.execute("SELECT COUNT(1) FROM pm_score_snapshot WHERE wallet='0xtestwhale'").fetchone()[0]
        part_snaps = conn.execute("SELECT COUNT(1) FROM pm_score_snapshot WHERE wallet='0xpart'").fetchone()[0]
        board = stats.query_scoreboard(conn, min_resolved=1)
    assert ok_snaps > 0 and part_snaps == 0                          # PARTIAL wallet not scored
    part = [r for r in board if r["wallet"] == "0xpart"]
    assert part and all(r["backfill_complete"] == 0 for r in part)   # surfaced, flagged incomplete
    assert "INCOMPLETE-NOT-RANKED" in stats.format_report(board)
