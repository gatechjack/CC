"""Tests for migration-004 caveat analytics (P2 CP1): classify_market_shape, two_sided_pct,
single_game_pct (+ ambiguous FLOOR and NULL-for-Fed), the one-sided directional slice, and the
e5 permanent guard (_STATS_COLS must cover every pm_category_stats column, or INSERT OR REPLACE
silent-zeros the unlisted ones). Offline; tmp DB only.

Spec: reports/prediction_markets/P2_PLAN.md §5.1; P2_KICKOFF_2026-08-23.md (e5/e7 rulings).
"""
from trading_corp.prediction_markets import category, db, ingest, stats
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow

NOW = 1_700_000_000


class _Cli:
    def __init__(self, page):
        self._page = page

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        return [ClosedPositionRow.from_api(r) for r in (self._page if offset == 0 else [])]


async def _noev(slug, **kw):
    return []


async def _ingest(tmp_path, page, wallet="0xw"):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, wallet, client=_Cli(page), now_ts=NOW, fetch_events=_noev)
    return p


def _row(cid, oi, slug, *, won, avg=0.5, tb=100.0, rp=10.0, wallet="0xw", event=None):
    return {"proxyWallet": wallet, "conditionId": cid, "slug": slug, "eventSlug": event or slug,
            "outcome": "Yes" if oi == 0 else "No", "outcomeIndex": oi,
            "avgPrice": avg, "totalBought": tb, "realizedPnl": rp,
            "curPrice": 1.0 if won else 0.0, "timestamp": 1}


# ---------- classify_market_shape (pure) ----------
def test_classify_single_game_dated():
    assert category.classify_market_shape("mlb-lad-sf-2026-08-23", "mlb-lad-sf-2026-08-23", "LAD vs SF") == "single_game"


def test_classify_futures_keyword():
    assert category.classify_market_shape("nba-2026-champion", "nba-2026-champion", "NBA Champion") == "futures"


def test_classify_ambiguous_is_not_single_game():
    assert category.classify_market_shape("ufc-someprop", "ufc-someprop", "a prop") == "ambiguous"


def test_classify_futures_wins_over_date():
    # a dated futures market is still futures (bias-down: keeps it OUT of the single-game count)
    assert category.classify_market_shape("nba-champion-2026-06-15", None, None) == "futures"


def test_classify_empty_is_ambiguous():
    assert category.classify_market_shape(None, None, None) == "ambiguous"


# ---------- two_sided_pct (over ALL rows) ----------
async def test_two_sided_pct_over_all_rows(tmp_path):
    page = [
        _row("0xA", 0, "ufc-a-b-2026-01-01", won=True),      # 0xA held on BOTH outcome_index -> two-sided
        _row("0xA", 1, "ufc-a-b-2026-01-01", won=False),
        _row("0xB", 0, "ufc-c-d-2026-01-02", won=True),      # one-sided
        _row("0xC", 0, "ufc-e-f-2026-01-03", won=False),     # one-sided
    ]
    p = await _ingest(tmp_path, page)
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)
        r = conn.execute("SELECT n_condition_ids, n_two_sided, two_sided_pct "
                         "FROM pm_category_stats WHERE category='ufc'").fetchone()
    assert r["n_condition_ids"] == 3                      # 0xA, 0xB, 0xC distinct
    assert r["n_two_sided"] == 1                          # only 0xA on >1 outcome_index
    assert abs(r["two_sided_pct"] - (1 / 3)) < 1e-9       # REAL non-zero value (e5), not a silent 0


# ---------- single_game_pct: ambiguous FLOOR + NULL for Fed ----------
async def test_single_game_pct_ambiguous_is_floor(tmp_path):
    page = [
        _row("0x1", 0, "ufc-a-b-2026-01-01", won=True),    # single_game (dated)
        _row("0x2", 0, "ufc-2026-champion", won=True),     # futures
        _row("0x3", 0, "ufc-weirdprop", won=False),        # ambiguous -> NOT single-game (floor)
    ]
    p = await _ingest(tmp_path, page)
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)
        r = conn.execute("SELECT n_single_game, n_futures_like, single_game_pct "
                         "FROM pm_category_stats WHERE category='ufc'").fetchone()
    assert r["n_single_game"] == 1 and r["n_futures_like"] == 1
    assert abs(r["single_game_pct"] - (1 / 3)) < 1e-9      # 1 single of 3 total; ambiguous floored out


async def test_single_game_pct_null_for_fed(tmp_path):
    page = [
        _row("0xf1", 0, "fed-interest-rates-january-2025", won=True),
        _row("0xf2", 0, "fed-interest-rates-march-2025", won=False),
    ]
    p = await _ingest(tmp_path, page)
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)
        r = conn.execute("SELECT category, single_game_pct FROM pm_category_stats WHERE category='fed'").fetchone()
    assert r["category"] == "fed"
    assert r["single_game_pct"] is None                   # OQ-2: NULL, not 0 (Fed has no single-game notion)


# ---------- one-sided directional slice ----------
async def test_onesided_slice_excludes_two_sided(tmp_path):
    page = [
        _row("0xA", 0, "ufc-a-b-2026-01-01", won=True,  rp=50.0),     # two-sided market -> EXCLUDED
        _row("0xA", 1, "ufc-a-b-2026-01-01", won=False, rp=-40.0),
        _row("0xB", 0, "ufc-c-d-2026-01-02", won=True,  rp=30.0, avg=0.5, tb=100.0),    # one-sided
        _row("0xC", 0, "ufc-e-f-2026-01-03", won=False, rp=-20.0, avg=0.4, tb=100.0),   # one-sided
    ]
    p = await _ingest(tmp_path, page)
    with db.connect(p) as conn:
        stats.rollup(conn, now_ts=NOW)
        os_ = conn.execute("SELECT * FROM pm_category_onesided_stats WHERE category='ufc'").fetchone()
        cs = conn.execute("SELECT n_resolved FROM pm_category_stats WHERE category='ufc'").fetchone()
    assert cs["n_resolved"] == 4                           # full category has all 4 scoreable rows
    assert os_["n_resolved"] == 2 and os_["wins"] == 1 and os_["losses"] == 1   # only 0xB + 0xC
    assert os_["is_upper_bound"] == 1
    assert abs(os_["net_realized_pnl"] - (30.0 - 20.0)) < 1e-6    # 0xB + 0xC only, NOT 0xA's +50/-40
    assert abs(os_["cost_basis"] - (100 * 0.5 + 100 * 0.4)) < 1e-6   # 90
    assert abs(os_["roi"] - (10.0 / 90.0)) < 1e-6


# ---------- e5 permanent guard ----------
def test_stats_cols_covers_every_pm_category_stats_column(tmp_path):
    # e5: rollup INSERT OR REPLACE writes _STATS_COLS; a table column NOT in _STATS_COLS is silent-zeroed
    # to its DEFAULT on every run. Enforce EXACT coverage so a future edit cannot reintroduce the failure.
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        tbl = {r[1] for r in conn.execute("PRAGMA table_info(pm_category_stats)")}
    assert set(stats._STATS_COLS) == tbl


# ---------- migration 004 schema + idempotency ----------
def test_migration_004_schema(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        maxv = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        cs = {r[1] for r in conn.execute("PRAGMA table_info(pm_category_stats)")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert maxv == db.SCHEMA_HEAD                           # head of the migration chain (is-at-head -> db.SCHEMA_HEAD)
    assert {"n_condition_ids", "n_two_sided", "two_sided_pct", "n_single_game", "n_futures_like",
            "single_game_pct", "market_type_source"} <= cs
    assert "pm_category_onesided_stats" in tables


def test_migration_004_idempotent_on_p1_shaped_db(tmp_path, monkeypatch):
    # simulate the LIVE upgrade: a DB at schema_version 3 WITH a row, THEN 004 applied + re-applied.
    p = str(tmp_path / "pm.db")
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:3])    # apply only 1,2,3
    db.init_db(p)
    with db.connect(p) as conn:
        conn.execute(
            "INSERT INTO pm_closed_position (wallet, condition_id, outcome_index, category, avg_price, "
            "total_bought, cost_basis, realized_pnl, cur_price, won, resolved_ts) "
            "VALUES ('0xw','0xB',0,'ufc',0.5,100.0,50.0,10.0,1.0,1,1)")
        conn.commit()
        v_before = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    monkeypatch.undo()                                          # restore full MIGRATIONS (adds 004..010)
    db.init_db(p)                                               # apply 004..010 on the v3 DB with data
    db.init_db(p)                                               # re-run -> no-op
    with db.connect(p) as conn:
        v_after = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        cnt = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0]
        stats.rollup(conn, now_ts=NOW)                         # rollup must populate the new columns
        r = conn.execute("SELECT two_sided_pct FROM pm_category_stats WHERE category='ufc'").fetchone()
    assert v_before == 3 and v_after == db.SCHEMA_HEAD and cnt == len(db.MIGRATIONS)
    assert rows == 1                                            # existing P1 row intact
    assert r is not None                                       # rollup ran cleanly on the upgraded DB
