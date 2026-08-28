"""CP3b Stage 0 -- reversible off-funnel removal (pm_watchlist.active). Offline; tmp DB only.

RULED 2026-08-26: a removed (wallet, category) pair leaves the ACTIVE funnel (no longer polled, no new paper
trades) but its record is PRESERVED and the removal is REVERSIBLE. The mechanism is a boolean flag
(`active`), NOT a status value, precisely so a single flip restores the PRIOR status with no bookkeeping.
The three exclusion states live in the DATA via `removal_reason` ('not_probed' / 'dormant_calendar' /
'structural') -- two return, one never does, and that difference is readable from the row without a doc.

These are BASIS tests (P2_PLAN anti-drift): each proves what a list is FILTERED BY, not merely that it has
rows. A test here FAILS if a removed pair reappears in ANY consumer, or if a tile renders for a category
outside the active set. NO live DB, NO data write of the real 22 rows -- fixtures only.
"""
import sqlite3

import pytest

from trading_corp.prediction_markets import db, farm, paper, stats

NOW = 1_700_000_000

# Representative sample. IN = a handful of ruled-IN categories; EXCLUDED = the three removed categories, each
# with its DISTINCT reason string (mirrors the real cbb/fifwc/unknown ruling, fixture-scale).
IN_CATS = ["mlb", "nba", "nfl", "nhl", "fed", "ufc"]
EXCLUDED = [("cbb", "not_probed"), ("fifwc", "dormant_calendar"), ("unknown", "structural")]


# ---------- fixtures ----------
def _fresh(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _pin(conn, wallet, category, *, status="pinned", active=1, roster=True):
    """Seed one (wallet, category) at a farm status + active flag. `roster=True` also gives it an ACTIVE
    pm_roster row (the weekly-refresh set the subset assertion checks against)."""
    if roster:
        conn.execute("INSERT OR IGNORE INTO pm_roster (wallet, category, active, added_ts) VALUES (?,?,1,?)",
                     (wallet, category, NOW))
    conn.execute("INSERT INTO pm_watchlist (wallet, category, status, active, added_ts) VALUES (?,?,?,?,?)",
                 (wallet, category, status, active, NOW))


def _remove(conn, wallet, category, reason, *, ts=NOW):
    """The removal write the mechanism performs (fixture-scale; the real 22-row write is a separate auth)."""
    conn.execute("UPDATE pm_watchlist SET active=0, removal_reason=?, removal_ts=? WHERE wallet=? AND category=?",
                 (reason, ts, wallet, category))


def _paper(conn, wallet, category, cid):
    conn.execute("INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, "
                 "entry_observed_ts, opened_ts) VALUES (?,?,?,0,?,?)", (wallet, category, cid, NOW, NOW))


def _cstats(conn, wallet, category, *, n_resolved=50, roi=0.1, awp=0.6):
    """A completed-lane stats row (pm_category_stats) -- the basis query_scoreboard ranks. Unset NOT NULL
    caveat columns fall back to their DEFAULT 0."""
    conn.execute("INSERT INTO pm_category_stats (wallet, category, n_resolved, roi, avg_win_price, updated_ts) "
                 "VALUES (?,?,?,?,?,?)", (wallet, category, n_resolved, roi, awp, NOW))


class _RecordingClient:
    """Records which wallets the poller actually fetches. Empty snapshots -> no capture; we assert on WHO
    was polled, which is exactly what the active=1 gate controls. The poller reads the FULL book via
    `fetch_positions_book` (T1), so that is the recorded call."""
    def __init__(self):
        self.fetched = []

    async def fetch_positions_book(self, wallet):
        from trading_corp.data.polymarket_data_api_client import PositionBook
        self.fetched.append(wallet)
        return PositionBook(rows=[], complete=True, pages=1, n=0)

    async def fetch_positions(self, wallet):
        self.fetched.append(wallet)
        return []


# ---------- migration 008: columns, index, head, defaults ----------
def test_migration_008_adds_columns_index_head_8(tmp_path):
    with db.connect(_fresh(tmp_path)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_watchlist)")}
        idx = {r[1] for r in conn.execute("PRAGMA index_list(pm_watchlist)")}
        maxv = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert {"active", "removal_reason", "removal_ts"} <= cols
    assert "ix_pm_watchlist_active" in idx
    assert maxv == 10  # full-init head is now 10 (migration 010 Stage-3 money layer); test name '..._head_8' is stale -- see SCHEMA_HEAD proposal


def test_fresh_insert_defaults_active_1(tmp_path):
    with db.connect(_fresh(tmp_path)) as conn:
        conn.execute("INSERT INTO pm_watchlist (wallet, category, status) VALUES ('0xnew','mlb','pinned')")
        conn.commit()
        r = conn.execute("SELECT active, removal_reason, removal_ts FROM pm_watchlist WHERE wallet='0xnew'").fetchone()
    assert r["active"] == 1 and r["removal_reason"] is None and r["removal_ts"] is None


def test_upgrade_backfills_existing_rows_to_active_1(tmp_path, monkeypatch):
    """The 114 board-locked pairs: prove `ADD COLUMN NOT NULL DEFAULT 1` backfills a PRE-008 row to active=1.
    Build a schema-7 DB by hand (pm_watchlist in its migration-006 shape, no active column), then let init_db
    apply ONLY 008."""
    p = str(tmp_path / "pm.db")
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE pm_watchlist ("
        " wallet TEXT NOT NULL, category TEXT NOT NULL, added_ts INTEGER, source TEXT,"
        " status TEXT NOT NULL DEFAULT 'watchlist', pinned_ts INTEGER, search_run_id INTEGER,"
        " updated_ts INTEGER, PRIMARY KEY (wallet, category))")
    conn.execute("INSERT INTO pm_watchlist (wallet, category, status) VALUES ('0xold','mlb','pinned')")
    for v in range(1, 8):                                   # stamp schema head at 7 -> init_db applies ONLY 008
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
    conn.commit()
    conn.close()

    # Pin init_db to migrations <=008 so this stays a migration-008-IN-ISOLATION test (its documented intent),
    # robust to later migrations (009 Stage-1 etc.) -- mirrors test_migration_004_idempotent's MIGRATIONS[:3].
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:8])
    db.init_db(p)                                           # applies migration 008 alone

    with db.connect(p) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_watchlist)")}
        row = conn.execute("SELECT active, removal_reason, removal_ts FROM pm_watchlist WHERE wallet='0xold'").fetchone()
        maxv = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert {"active", "removal_reason", "removal_ts"} <= cols
    assert maxv == 8
    assert row["active"] == 1                              # pre-existing (114-style) row backfilled IN-funnel
    assert row["removal_reason"] is None and row["removal_ts"] is None


# ---------- consumer gates: a removed pair is invisible to EACH ----------
async def test_removed_pair_invisible_to_poller(tmp_path):
    client = _RecordingClient()
    with db.connect(_fresh(tmp_path)) as conn:
        _pin(conn, "0xactive", "mlb", active=1)
        _pin(conn, "0xremoved", "mlb", active=0)           # off-funnel
        conn.commit()
        await paper.poll_pinned(conn, client=client, now_ts=NOW)
        traded = {r["wallet"] for r in conn.execute("SELECT DISTINCT wallet FROM pm_paper_trade")}
    assert "0xactive" in client.fetched                    # active pair IS polled
    assert "0xremoved" not in client.fetched               # removed pair NEVER polled (gate holds)
    assert "0xremoved" not in traded                       # -> and no paper trade accrues for it


def test_removed_categories_yield_no_tile(tmp_path):
    """BASIS: the tile/tab set is farm_categories(PINNED); a removed category must not produce a tile."""
    with db.connect(_fresh(tmp_path)) as conn:
        for i, c in enumerate(IN_CATS):
            _pin(conn, "0xin%d" % i, c, active=1)
        for i, (c, reason) in enumerate(EXCLUDED):
            _pin(conn, "0xex%d" % i, c, active=1)
            _remove(conn, "0xex%d" % i, c, reason)
        conn.commit()
        cats = farm.farm_categories(conn, farm.PINNED)
    assert set(cats) == set(IN_CATS)                        # EXACTLY the active set...
    for c, _ in EXCLUDED:
        assert c not in cats                               # ...no tile for a removed category


def test_removed_pair_off_pinned_list_and_summary(tmp_path):
    with db.connect(_fresh(tmp_path)) as conn:
        _pin(conn, "0xa", "mlb", active=1)
        _pin(conn, "0xb", "fifwc", active=1)
        _remove(conn, "0xb", "fifwc", "dormant_calendar")
        conn.commit()
        rows = farm.farm_rows(conn, status=farm.PINNED)
        s = farm.farm_summary(conn)
    wallets = {r["wallet"] for r in rows}
    assert "0xa" in wallets and "0xb" not in wallets
    assert s["n_pinned"] == 1
    assert "fifwc" not in s["pinned_categories"]


def test_removed_candidate_not_counted(tmp_path):
    with db.connect(_fresh(tmp_path)) as conn:
        _pin(conn, "0xc1", "mlb", status="candidate", active=1)
        _pin(conn, "0xc2", "mlb", status="candidate", active=0)   # a removed candidate
        conn.commit()
        s = farm.farm_summary(conn)
        cand = {r["wallet"] for r in farm.farm_rows(conn, status=farm.CANDIDATE)}
    assert s["n_candidates"] == 1 and cand == {"0xc1"}


def test_removed_pinned_excluded_from_subset_assertion(tmp_path):
    """A removed pinned pair whose wallet is NOT refreshed must NOT trip the C2.3 FAIL-LOUD (it no longer
    paper-trades, so it needs no refresh guarantee)."""
    with db.connect(_fresh(tmp_path)) as conn:
        _pin(conn, "0xactive", "mlb", active=1, roster=True)       # refreshed -> fine
        _pin(conn, "0xghost", "cbb", active=0, roster=False)       # removed + unrefreshed
        conn.commit()
        rep = paper.assert_pinned_subset_of_refresh(conn)          # must NOT raise
    assert rep["unrefreshed"] == []
    assert rep["n_pinned"] == 1                                    # the removed pair is not even counted pinned


def test_subset_assertion_still_fires_for_ACTIVE_unrefreshed(tmp_path):
    """The gate must NOT neuter the invariant: an ACTIVE pinned pair that is unrefreshed still FAILS LOUD.
    (Bias down, never hide -- proves we did not silence a real problem while excluding removed pairs.)"""
    with db.connect(_fresh(tmp_path)) as conn:
        _pin(conn, "0xactivenoref", "mlb", active=1, roster=False)  # active + unrefreshed
        conn.commit()
        with pytest.raises(paper.PaperSubsetError):
            paper.assert_pinned_subset_of_refresh(conn)


# ---------- reversibility + preservation ----------
def test_roundtrip_restore_with_active_only_restores_prior_status(tmp_path):
    """Set 0 -> set 1, identical state, with NO other write. This is the whole justification for the flag."""
    with db.connect(_fresh(tmp_path)) as conn:
        _pin(conn, "0xrt", "cbb", status="pinned", active=1)
        conn.commit()
        _remove(conn, "0xrt", "cbb", "not_probed")
        conn.commit()
        assert farm.farm_categories(conn, farm.PINNED) == []       # invisible while removed
        assert farm.farm_rows(conn, status=farm.PINNED) == []
        conn.execute("UPDATE pm_watchlist SET active=1 WHERE wallet='0xrt' AND category='cbb'")  # ONLY active
        conn.commit()
        rows = farm.farm_rows(conn, status=farm.PINNED)
        st = conn.execute("SELECT status, active FROM pm_watchlist WHERE wallet='0xrt'").fetchone()
    assert len(rows) == 1 and rows[0]["wallet"] == "0xrt"
    assert st["status"] == "pinned" and st["active"] == 1          # PRIOR status restored, no bookkeeping


def test_paper_trades_survive_removal(tmp_path):
    with db.connect(_fresh(tmp_path)) as conn:
        _pin(conn, "0xph", "fifwc", active=1)
        _paper(conn, "0xph", "fifwc", "0xcondA")
        _paper(conn, "0xph", "fifwc", "0xcondB")
        conn.commit()
        before = conn.execute("SELECT COUNT(*) FROM pm_paper_trade WHERE wallet='0xph'").fetchone()[0]
        _remove(conn, "0xph", "fifwc", "dormant_calendar")
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM pm_paper_trade WHERE wallet='0xph'").fetchone()[0]
        rr = conn.execute("SELECT removal_reason FROM pm_watchlist WHERE wallet='0xph'").fetchone()[0]
    assert before == 2 and after == 2                              # removal is a flag flip; history preserved
    assert rr == "dormant_calendar"


def test_three_distinct_reasons_are_readable_from_the_row(tmp_path):
    with db.connect(_fresh(tmp_path)) as conn:
        for i, (c, reason) in enumerate(EXCLUDED):
            _pin(conn, "0xr%d" % i, c, active=1)
            _remove(conn, "0xr%d" % i, c, reason)
        conn.commit()
        reasons = {r[0] for r in conn.execute("SELECT DISTINCT removal_reason FROM pm_watchlist WHERE active=0")}
    assert reasons == {"not_probed", "dormant_calendar", "structural"}   # three states, IN THE DATA


def test_deactivated_pair_absent_from_query_scoreboard(tmp_path):
    """RULING (item 2, 2026-08-26): the deactivated pairs show NOWHERE -- including the F-4 prospects ranker
    (query_scoreboard). A pair ABSENT from pm_watchlist still ranks (LEFT JOIN); ONLY active=0 is excluded."""
    with db.connect(_fresh(tmp_path)) as conn:
        _cstats(conn, "0xkeep", "mlb")                     # prospect, active on the roster -> shown
        _cstats(conn, "0xgone", "mlb")                     # prospect, gets deactivated -> hidden
        _cstats(conn, "0xpure", "mlb")                     # prospect NOT on the roster at all -> still shown
        _pin(conn, "0xkeep", "mlb", active=1)
        _pin(conn, "0xgone", "mlb", active=1)
        _remove(conn, "0xgone", "mlb", "structural")       # active=0
        conn.commit()
        board = {r["wallet"] for r in stats.query_scoreboard(conn, min_resolved=1)}
    assert "0xkeep" in board                               # active=1 -> ranked
    assert "0xpure" in board                               # absent from pm_watchlist -> still ranked (LEFT JOIN)
    assert "0xgone" not in board                           # active=0 -> excluded from the ranker too


def test_query_scoreboard_join_is_pair_grain_no_fanout(tmp_path):
    """The gate JOINs pm_watchlist on (wallet, category) -- the PAIR grain -- NOT on wallet alone. This test
    (unlike the three-wallet one above) is the ONLY one that can catch a wallet-grain join: ONE wallet with
    TWO categories, one deactivated + one active. A wallet-grain join would (a) FAN OUT (each cs row joins
    every wl row for that wallet) AND (b) OVER-EXCLUDE / mis-include (the deactivated pair survives via the
    active pair's wl row). Correct pair-grain -> EXACTLY ONE row, the ACTIVE category. Assert the total row
    COUNT, not just presence."""
    with db.connect(_fresh(tmp_path)) as conn:
        _cstats(conn, "0xmulti", "nba")                    # active category -> must survive, rank normally
        _cstats(conn, "0xmulti", "unknown")                # deactivated category -> must drop
        _pin(conn, "0xmulti", "nba", active=1)
        _pin(conn, "0xmulti", "unknown", active=1)
        _remove(conn, "0xmulti", "unknown", "structural")  # deactivate ONLY the unknown pair
        conn.commit()
        board = stats.query_scoreboard(conn, min_resolved=1)
    assert len(board) == 1                                 # fan-out + over-inclusion check (wallet-grain gives 2)
    assert board[0]["wallet"] == "0xmulti" and board[0]["category"] == "nba"   # the ACTIVE pair, never dropped
