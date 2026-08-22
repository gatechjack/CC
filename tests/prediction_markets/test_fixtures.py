"""End-to-end: recorded /closed-positions fixtures through parse -> ingest -> rollup -> score ->
scoreboard, + g0_validate. Offline. Spec: reports/prediction_markets/P1_PLAN.md §11.
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
    def __init__(self, pages):
        self._pages = pages

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        i = offset // limit
        rows = self._pages[i] if i < len(self._pages) else []
        return [ClosedPositionRow.from_api(r) for r in rows]


async def _noev(slug, **kw):
    return []


async def test_end_to_end_winner_page(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xwinnerwhale", client=_Cli([_load("winner_page.json")]),
                                     now_ts=NOW, fetch_events=_noev)
        stats.rollup(conn, now_ts=NOW)
        stats.compute_scores(conn, now_ts=NOW, min_resolved=1)
        board = stats.query_scoreboard(conn, min_resolved=1)
    cats = {r["category"] for r in board}
    assert {"ufc", "mlb", "nba"} <= cats
    assert all(r["net_realized_pnl"] > 0 for r in board)   # winner page -> positive nets


async def test_end_to_end_empty_page(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        res = await ingest.backfill_wallet(conn, "0xnobody", client=_Cli([_load("empty_page.json")]),
                                           now_ts=NOW, fetch_events=_noev)
        stats.rollup(conn, now_ts=NOW)
        n = conn.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0]
    assert res["rows"] == 0 and n == 0


async def test_g0_validate_detects_losers_and_winners():
    neg = _Cli([_load("negrisk_event.json")])   # has negative realized rows
    win = _Cli([_load("winner_page.json")])      # all positive
    r1 = await ingest.g0_validate(neg, [{"wallet": "0xd1k21", "user_name": "d1k21"}])
    assert r1["passed"] is True and r1["per_wallet"][0]["negative"] > 0
    r2 = await ingest.g0_validate(win, [{"wallet": "0xwin", "user_name": "win"}])
    assert r2["passed"] is False and r2["per_wallet"][0]["negative"] == 0
