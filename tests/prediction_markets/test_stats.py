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
    assert abs(ufc["total_bought"] - 1150.0) < 1e-6
    assert abs(ufc["roi"] - (-150.0 / 1150.0)) < 1e-6
    assert abs(ufc["win_rate"] - 0.5) < 1e-6
    assert abs(ufc["avg_win_price"] - 0.60) < 1e-6                        # won ufc row avg_price
    assert ufc["n_excluded"] == 0 and ufc["data_quality"] is None
    assert cs["mlb"]["n_resolved"] == 2 and abs(cs["mlb"]["net_realized_pnl"] - (-100.0)) < 1e-6


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
