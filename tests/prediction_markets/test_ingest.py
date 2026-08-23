"""Tests for trading_corp.prediction_markets.ingest — offline (fake client + tmp DB).

Spec: reports/prediction_markets/P1_PLAN.md §8, §11.
"""
import json
from pathlib import Path

from trading_corp.prediction_markets import db, ingest
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow

_FIX = Path(__file__).parent / "fixtures" / "closed_positions"
NOW = 1_700_000_000


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


class FakeClient:
    """fetch_closed_positions returns ClosedPositionRow objects per page (offset/limit);
    optionally raises for a wallet substring (isolation test)."""
    def __init__(self, pages, *, raise_for=None):
        self._pages = pages
        self._raise_for = raise_for

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        if self._raise_for and self._raise_for in wallet:
            raise RuntimeError("boom %s" % wallet)
        idx = offset // limit
        rows = self._pages[idx] if idx < len(self._pages) else []
        return [ClosedPositionRow.from_api(r) for r in rows]

    async def fetch_positions(self, wallet):
        return []


async def _noev(slug, **kw):
    return []  # tier-2 fetch that yields no tags -> unknown


def _fresh_db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


async def test_backfill_upserts_and_idempotent(tmp_path):
    p = _fresh_db(tmp_path)
    cli = FakeClient([_load("loser_mix_page.json")])
    with db.connect(p) as conn:
        res = await ingest.backfill_wallet(conn, "0xTestWhale", client=cli, now_ts=NOW, fetch_events=_noev)
        assert res["rows"] == 4
        n1 = conn.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0]
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xTestWhale", client=cli, now_ts=NOW + 1, fetch_events=_noev)
        n2 = conn.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0]
    assert n1 == 4 and n2 == 4  # INSERT OR REPLACE -> idempotent


async def test_categories_won_shares_and_negative_stored(tmp_path):
    p = _fresh_db(tmp_path)
    cli = FakeClient([_load("loser_mix_page.json")])
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xtestwhale", client=cli, now_ts=NOW, fetch_events=_noev)
        rows = {r["condition_id"]: r for r in conn.execute(
            "SELECT condition_id, category, category_source, won, realized_pnl, shares_derived, pnl_suspect "
            "FROM pm_closed_position").fetchall()}
    win = rows["0xcp_ufc_win_1"]
    loss = rows["0xcp_mlb_loss_1"]
    assert win["category"] == "ufc" and win["category_source"] == "slug_prefix"
    assert rows["0xcp_mlb_win_1"]["category"] == "mlb"
    assert win["won"] == 1 and loss["won"] == 0            # cur 1.0 vs 0.0
    assert loss["realized_pnl"] == -700.0                   # negative stored
    assert abs(win["shares_derived"] - (600.0 / 0.60)) < 1e-6
    # all clean binary rows -> not suspect
    assert all(r["pnl_suspect"] == 0 for r in rows.values())


async def test_won_threshold_edges(tmp_path):
    p = _fresh_db(tmp_path)
    edge = [
        {"proxyWallet": "0xw", "conditionId": "0xa", "slug": "ufc-a-b-2026-01-01", "eventSlug": "ufc-a-b-2026-01-01",
         "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": -100.0, "curPrice": 0.89, "timestamp": 1},
        {"proxyWallet": "0xw", "conditionId": "0xb", "slug": "ufc-c-d-2026-01-02", "eventSlug": "ufc-c-d-2026-01-02",
         "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": 80.0, "curPrice": 0.90, "timestamp": 2},
        {"proxyWallet": "0xw", "conditionId": "0xc", "slug": "ufc-e-f-2026-01-03", "eventSlug": "ufc-e-f-2026-01-03",
         "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": 90.0, "curPrice": 0.95, "timestamp": 3},
    ]
    cli = FakeClient([edge])
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xw", client=cli, now_ts=NOW, fetch_events=_noev)
        won = {r["condition_id"]: r["won"] for r in conn.execute("SELECT condition_id, won FROM pm_closed_position").fetchall()}
    assert won["0xa"] == 0 and won["0xb"] == 1 and won["0xc"] == 1  # 0.89<0.9<=0.90,0.95


async def test_per_wallet_isolation(tmp_path):
    p = _fresh_db(tmp_path)
    cli = FakeClient([_load("clean_binary.json")], raise_for="0xbad")
    with db.connect(p) as conn:
        summary = await ingest.backfill_wallets(
            conn, ["0xgood1", "0xbad", "0xgood2"], client=cli, now_ts=NOW, fetch_events=_noev)
    assert len(summary["ok"]) == 2
    assert len(summary["failed"]) == 1 and summary["failed"][0]["wallet"] == "0xbad"
    assert {w["wallet"] for w in summary["ok"]} == {"0xgood1", "0xgood2"}


class PerWalletClient:
    """fetch_closed_positions returns a different first page per wallet (offset 0)."""
    def __init__(self, by_wallet):
        self._by = by_wallet

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        rows = self._by.get(wallet, []) if offset == 0 else []
        return [ClosedPositionRow.from_api(r) for r in rows]


async def test_pk_collision_guard_isolates_wallet(tmp_path):
    # §13A(i): two pulled rows with the SAME (wallet, condition_id, outcome_index) would silently collapse
    # under INSERT OR REPLACE -> the guard hard-fails that wallet LOUDLY into summary['failed'] (with the
    # colliding keys), and per-wallet isolation lets the batch continue for the clean wallet.
    collide = [
        {"proxyWallet": "0xbad", "conditionId": "0xC", "slug": "ufc-a-b-2026-01-01", "eventSlug": "ufc-a-b-2026-01-01",
         "outcome": "Yes", "outcomeIndex": 0, "avgPrice": 0.5, "totalBought": 100.0, "realizedPnl": 10.0, "curPrice": 1.0, "timestamp": 1},
        {"proxyWallet": "0xbad", "conditionId": "0xC", "slug": "ufc-a-b-2026-01-01", "eventSlug": "ufc-a-b-2026-01-01",
         "outcome": "Yes", "outcomeIndex": 0, "avgPrice": 0.5, "totalBought": 200.0, "realizedPnl": 20.0, "curPrice": 1.0, "timestamp": 2},
    ]
    p = _fresh_db(tmp_path)
    with db.connect(p) as conn:
        summary = await ingest.backfill_wallets(
            conn, ["0xbad", "0xgood"],
            client=PerWalletClient({"0xbad": collide, "0xgood": _load("clean_binary.json")}),
            now_ts=NOW, fetch_events=_noev)
    assert len(summary["failed"]) == 1 and summary["failed"][0]["wallet"] == "0xbad"
    assert "PK COLLISION" in summary["failed"][0]["error"]      # LOUD + names the defect
    assert {w["wallet"] for w in summary["ok"]} == {"0xgood"}   # batch continued for the clean wallet
