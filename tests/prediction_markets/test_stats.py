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


async def test_zero_or_null_avg_price_no_div_by_zero(tmp_path):
    # §13 dec 11 guard: a SCOREABLE row with avg_price=0 -> cost_basis=0. If it is the only scoreable
    # row, SUM(cost_basis)=0 -> roi MUST be None (guarded), never a ZeroDivisionError. roi_notional
    # still computes (uses total_bought). Proves no scoreable row reaches the denominator with a
    # zero/null cost basis and breaks it.
    rows = [
        {"proxyWallet": "0xz", "conditionId": "0xz1", "slug": "ufc-z-y-2026-01-01", "eventSlug": "ufc-z-y-2026-01-01",
         "avgPrice": 0.0, "totalBought": 100.0, "realizedPnl": -50.0, "curPrice": 0.0, "timestamp": 1},
    ]
    p = await _ingest_page(tmp_path, rows, "0xz")
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)            # must not raise
        r = conn.execute("SELECT * FROM pm_category_stats WHERE category='ufc'").fetchone()
        row = conn.execute("SELECT pnl_suspect, cost_basis FROM pm_closed_position").fetchone()
    assert row["pnl_suspect"] == 0 and abs(row["cost_basis"]) < 1e-12   # scoreable, zero cost
    assert r["n_resolved"] == 1
    assert abs(r["cost_basis"]) < 1e-12
    assert r["roi"] is None                                            # guarded -> no div-by-zero
    assert abs(r["roi_notional"] - (-50.0 / 100.0)) < 1e-6            # notional still computable


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
