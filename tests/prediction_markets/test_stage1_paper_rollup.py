"""Stage 1 -- paper_rollup + farm_rows(PINNED) basis tests.

Anti-drift core: these tests prove WHAT the pinned list sources its numbers from (paper stats,
NOT legacy completed-lane), and that the R1 gate (active=1 AND pinned) is enforced at the
aggregation layer. Offline; tmp_path sqlite, db.init_db, helper seeders.

Key tests:
  - R1 gate: deactivated pair's pm_paper_trade rows SURVIVE, no pm_paper_category_stats row for it.
  - BASIS test: a pinned pair with pm_category_stats win_rate=0.89 AND pm_paper_category_stats
    win_rate=0.40 -> farm_rows(PINNED) returns 0.40 (paper), NOT 0.89 (legacy). This FAILS against
    the old farm.py and PASSES with the Stage 1 fix.
  - Honest-empty: a pinned pair with only OPEN paper trades -> paper_rollup gives n_closed=0/win_rate None.
"""
from trading_corp.prediction_markets import db, farm, paper
from trading_corp.prediction_markets.category import derive_category_from_slug

NOW = 1_700_000_000
MLB_CAT = derive_category_from_slug("mlb-team-a-team-b-2026-09-01")[0]
UFC_CAT = derive_category_from_slug("ufc-fighter-a-fighter-b-2026-09-01")[0]
WALLET = "0xwhale"


def _db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _pin(conn, wallet, category, *, active=1):
    """Seed a pinned (wallet, category) in pm_watchlist + pm_roster."""
    conn.execute("INSERT OR IGNORE INTO pm_roster (wallet, category, active, added_ts) VALUES (?,?,1,?)",
                 (wallet, category, NOW))
    conn.execute(
        "INSERT OR IGNORE INTO pm_watchlist (wallet, category, status, active, added_ts) VALUES (?,?,'pinned',?,?)",
        (wallet, category, active, NOW))


def _paper_closed(conn, wallet, category, cid, *, oi=0, entry_price=0.40, size_basis=100.0,
                  won, entry_ts=NOW):
    """Seed a closed paper trade with the given outcome."""
    realized = (size_basis - size_basis * entry_price) if won else -(size_basis * entry_price)
    conn.execute(
        "INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, entry_observed_ts, "
        "entry_price_avg_at_observation, size_basis, cost_basis, market_end_date, status, "
        "won, realized_pnl, close_source, resolved_ts, opened_ts, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2020-01-01', 'closed', ?, ?, 'gamma_resolution', ?, ?, ?)",
        (wallet, category, cid, oi, entry_ts, entry_price, size_basis, size_basis * entry_price,
         1 if won else 0, realized, NOW, NOW, NOW))


def _paper_open(conn, wallet, category, cid, *, oi=0, entry_ts=NOW):
    """Seed an open paper trade."""
    conn.execute(
        "INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, entry_observed_ts, "
        "entry_price_avg_at_observation, size_basis, cost_basis, market_end_date, status, opened_ts, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, 0.50, 100.0, 50.0, '2030-01-01', 'open', ?, ?)",
        (wallet, category, cid, oi, entry_ts, NOW, NOW))


def _cstats(conn, wallet, category, *, win_rate=0.89, roi=0.20, n_resolved=50):
    """Seed pm_category_stats (legacy completed-lane)."""
    conn.execute(
        "INSERT OR IGNORE INTO pm_category_stats (wallet, category, n_resolved, win_rate, roi, updated_ts) "
        "VALUES (?, ?, ?, ?, ?, ?)", (wallet, category, n_resolved, win_rate, roi, NOW))


# ---- paper_rollup R1 gate ---------------------------------------------------------------

def test_paper_rollup_r1_gate_active_pair_gets_row(tmp_path):
    """An active=1 pinned pair with closed trades -> pm_paper_category_stats row."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        _paper_closed(conn, WALLET, MLB_CAT, "0xw1", won=True)
        _paper_closed(conn, WALLET, MLB_CAT, "0xw2", won=False)
        conn.commit()
        n = paper.paper_rollup(conn, now_ts=NOW)
        row = conn.execute(
            "SELECT * FROM pm_paper_category_stats WHERE wallet=? AND category=?",
            (WALLET, MLB_CAT)).fetchone()

    assert n == 1
    assert row is not None
    assert row["n_closed"] == 2
    assert row["wins"] == 1
    assert row["losses"] == 1
    assert abs(row["win_rate"] - 0.5) < 1e-9


def test_paper_rollup_r1_gate_deactivated_pair_excluded(tmp_path):
    """R1 GATE (load-bearing): active=0 pair's pm_paper_trade rows SURVIVE in pm_paper_trade
    but NO pm_paper_category_stats row is written for it."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        # active=1 pair (should appear in stats)
        _pin(conn, "0xactive", MLB_CAT, active=1)
        _paper_closed(conn, "0xactive", MLB_CAT, "0xa1", won=True)
        _paper_closed(conn, "0xactive", MLB_CAT, "0xa2", won=True)
        # active=0 pair (rows survive in pm_paper_trade; must NOT appear in stats)
        _pin(conn, "0xremoved", MLB_CAT, active=0)
        _paper_closed(conn, "0xremoved", MLB_CAT, "0xr1", won=True)
        _paper_closed(conn, "0xremoved", MLB_CAT, "0xr2", won=False)
        conn.commit()
        n = paper.paper_rollup(conn, now_ts=NOW)
        # pm_paper_trade rows for the removed pair SURVIVE (no delete)
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM pm_paper_trade WHERE wallet='0xremoved'").fetchone()[0]
        # pm_paper_category_stats: ONLY the active pair
        stats_wallets = {r["wallet"] for r in conn.execute(
            "SELECT wallet FROM pm_paper_category_stats").fetchall()}

    assert n == 1                                        # only 1 pair aggregated
    assert raw_count == 2                                # rows preserved in pm_paper_trade
    assert "0xactive" in stats_wallets
    assert "0xremoved" not in stats_wallets              # R1 gate excludes the removed pair


def test_paper_rollup_r1_gate_correct_win_rate_for_active_pair(tmp_path):
    """The active pair's win_rate is correct (2 wins + 1 loss -> 2/3)."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        _paper_closed(conn, WALLET, MLB_CAT, "0x1", won=True)
        _paper_closed(conn, WALLET, MLB_CAT, "0x2", won=True)
        _paper_closed(conn, WALLET, MLB_CAT, "0x3", won=False)
        conn.commit()
        paper.paper_rollup(conn, now_ts=NOW)
        row = conn.execute(
            "SELECT * FROM pm_paper_category_stats WHERE wallet=? AND category=?",
            (WALLET, MLB_CAT)).fetchone()

    assert abs(row["win_rate"] - (2.0 / 3.0)) < 1e-9
    assert row["n_closed"] == 3
    assert row["wins"] == 2
    assert row["losses"] == 1


# ---- BASIS TEST: farm_rows(PINNED) sources from paper, NOT legacy -----------------------

def test_farm_rows_pinned_sources_from_paper_not_legacy(tmp_path):
    """BASIS TEST (load-bearing -- the substitution bug fix).

    A pinned (wallet, category) has:
      - pm_category_stats.win_rate = 0.89 (legacy completed-lane number)
      - pm_paper_category_stats.win_rate = 0.40 (forward paper number)

    farm_rows(PINNED) MUST return win_rate = 0.40 (paper), NOT 0.89 (legacy).

    This test FAILS against the old farm.py (which LEFT-JOINed pm_category_stats for both pinned
    and candidate) and PASSES with the Stage 1 fix (pinned JOINs pm_paper_category_stats)."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        # seed pm_category_stats (legacy lane) with a very different win_rate
        _cstats(conn, WALLET, MLB_CAT, win_rate=0.89, roi=0.30)
        # seed pm_paper_category_stats directly (as paper_rollup would produce)
        conn.execute(
            "INSERT OR REPLACE INTO pm_paper_category_stats "
            "(wallet, category, n_closed, wins, losses, win_rate, net_paper_pnl, cost_basis, roi, "
            " avg_entry_price, n_open, n_stale, n_void, updated_ts) "
            "VALUES (?, ?, 10, 4, 6, 0.40, -20.0, 400.0, -0.05, 0.42, 2, 0, 0, ?)",
            (WALLET, MLB_CAT, NOW))
        conn.commit()
        rows = farm.farm_rows(conn, status=farm.PINNED)

    assert len(rows) == 1
    r = rows[0]
    # The KEY assertion: win_rate comes from paper (0.40), NOT from legacy (0.89)
    assert r["win_rate"] is not None
    assert abs(r["win_rate"] - 0.40) < 1e-9, (
        "Expected paper win_rate=0.40, got %.4f. "
        "If this is 0.89 the substitution bug is NOT fixed." % r["win_rate"])
    # Also check roi comes from paper
    assert r["roi"] is not None
    assert abs(r["roi"] - (-0.05)) < 1e-9
    # Also check the paper-native counts are present
    assert r.get("n_closed") == 10
    assert r.get("n_open_paper") == 2


# ---- honest-empty: open-only trades -> win_rate None -----------------------------------

def test_paper_rollup_honest_empty_open_only(tmp_path):
    """A pinned pair with only OPEN paper trades -> n_closed=0, win_rate=None (no decided trades)."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        _paper_open(conn, WALLET, MLB_CAT, "0xopen1")
        _paper_open(conn, WALLET, MLB_CAT, "0xopen2")
        conn.commit()
        n = paper.paper_rollup(conn, now_ts=NOW)
        row = conn.execute(
            "SELECT * FROM pm_paper_category_stats WHERE wallet=? AND category=?",
            (WALLET, MLB_CAT)).fetchone()

    assert n == 1
    assert row is not None
    assert row["n_closed"] == 0
    assert row["wins"] == 0
    assert row["losses"] == 0
    assert row["win_rate"] is None                       # honest-empty: no decided trades
    assert row["n_open"] == 2


def test_farm_rows_pinned_honest_empty_paper_values(tmp_path):
    """A pinned pair with only OPEN paper trades -> farm_rows(PINNED) shows honest-empty paper values,
    NOT a borrowed number from pm_category_stats."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        # legacy stats has a non-empty win_rate -- it must NOT bleed into the pinned rendering
        _cstats(conn, WALLET, MLB_CAT, win_rate=0.75, n_resolved=30)
        # paper_rollup seeds pm_paper_category_stats with the open-only honest state
        _paper_open(conn, WALLET, MLB_CAT, "0xop")
        conn.commit()
        paper.paper_rollup(conn, now_ts=NOW)
        rows = farm.farm_rows(conn, status=farm.PINNED)

    assert len(rows) == 1
    r = rows[0]
    # win_rate from paper: None (no closed trades) -- not 0.75 from legacy
    assert r["win_rate"] is None, (
        "Expected win_rate=None (honest-empty from paper), got %.4f (likely leaked from legacy)."
        % (r["win_rate"] or 0))


# ---- farm_rows(CANDIDATE) still uses pm_category_stats ----------------------------------

def test_farm_rows_candidate_still_uses_legacy_stats(tmp_path):
    """CANDIDATE rows still source stats from pm_category_stats (Stage 1 does NOT change the candidate basis).
    This ensures we haven't accidentally wired candidates to the paper table."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        # a candidate pair (status='candidate', not 'pinned')
        conn.execute(
            "INSERT OR IGNORE INTO pm_watchlist (wallet, category, status, active) VALUES (?,?,'candidate',1)",
            (WALLET, UFC_CAT))
        conn.execute(
            "INSERT OR IGNORE INTO pm_roster (wallet, category, active) VALUES (?,?,1)",
            (WALLET, UFC_CAT))
        # seed pm_category_stats with a distinctive win_rate
        _cstats(conn, WALLET, UFC_CAT, win_rate=0.65, n_resolved=40)
        # no pm_paper_category_stats row for this candidate (paper_rollup only covers pinned)
        conn.commit()
        rows = farm.farm_rows(conn, status=farm.CANDIDATE)

    assert len(rows) == 1
    r = rows[0]
    # candidate must show legacy win_rate=0.65
    assert r["win_rate"] is not None
    assert abs(r["win_rate"] - 0.65) < 1e-9, "Candidate must source win_rate from pm_category_stats"


# ---- migration 009 schema check ---------------------------------------------------------

def test_migration_009_creates_table_and_index(tmp_path):
    """migration 009 creates pm_paper_category_stats with the expected columns and the category/roi index."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_paper_category_stats)")}
        idx = {r[1] for r in conn.execute("PRAGMA index_list(pm_paper_category_stats)")}
        maxv = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]

    expected_cols = {
        "wallet", "category", "n_closed", "wins", "losses", "win_rate",
        "net_paper_pnl", "cost_basis", "roi", "avg_entry_price",
        "n_open", "n_stale", "n_void", "last_resolved_ts", "updated_ts",
    }
    assert expected_cols <= cols, "Missing columns: %s" % (expected_cols - cols)
    assert "ix_pm_pcs_category_roi" in idx
    assert maxv == db.SCHEMA_HEAD   # is-at-head tracks the constant (a bump touches ONE place)


# ---- paper_rollup ROI math --------------------------------------------------------------

def test_paper_rollup_roi_and_pnl_math(tmp_path):
    """Verify roi = net_paper_pnl / cost_basis and the pnl signs are correct."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        # WIN: size_basis=100, entry_price=0.40 -> cost_basis=40, realized = 100-40 = 60
        _paper_closed(conn, WALLET, MLB_CAT, "0xw", won=True, entry_price=0.40, size_basis=100.0)
        # LOSS: size_basis=100, entry_price=0.60 -> cost_basis=60, realized = -60
        _paper_closed(conn, WALLET, MLB_CAT, "0xl", won=False, entry_price=0.60, size_basis=100.0)
        conn.commit()
        paper.paper_rollup(conn, now_ts=NOW)
        row = conn.execute(
            "SELECT * FROM pm_paper_category_stats WHERE wallet=? AND category=?",
            (WALLET, MLB_CAT)).fetchone()

    # net = 60 + (-60) = 0; cost = 40 + 60 = 100; roi = 0/100 = 0
    assert abs(row["net_paper_pnl"] - 0.0) < 1e-9
    assert abs(row["cost_basis"] - 100.0) < 1e-9
    assert row["roi"] is not None
    assert abs(row["roi"] - 0.0) < 1e-9


def test_paper_rollup_roi_none_when_no_closed(tmp_path):
    """roi is None when cost_basis=0 (no closed trades) -- never divide by zero."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        _paper_open(conn, WALLET, MLB_CAT, "0xop")
        conn.commit()
        paper.paper_rollup(conn, now_ts=NOW)
        row = conn.execute(
            "SELECT * FROM pm_paper_category_stats WHERE wallet=? AND category=?",
            (WALLET, MLB_CAT)).fetchone()

    assert row["roi"] is None


# ---- R1 airtight: deactivate-after-rollup removes the stale stats row --------------------

def test_paper_rollup_removes_stale_row_after_deactivation(tmp_path):
    """R1 airtight (table level, not just display): a pair that WAS active (has a stats row) then gets
    deactivated must have its pm_paper_category_stats row REMOVED on the next rollup -- while its
    pm_paper_trade rows SURVIVE (deactivation is reversible)."""
    p = _db(tmp_path)
    with db.connect(p) as conn:
        _pin(conn, WALLET, MLB_CAT, active=1)
        _paper_closed(conn, WALLET, MLB_CAT, "0xc1", won=True)
        _paper_closed(conn, WALLET, MLB_CAT, "0xc2", won=False)
        conn.commit()
        paper.paper_rollup(conn, now_ts=NOW)
        assert conn.execute(
            "SELECT COUNT(*) FROM pm_paper_category_stats WHERE wallet=?", (WALLET,)).fetchone()[0] == 1
        # now deactivate the pair and re-run the rollup
        conn.execute("UPDATE pm_watchlist SET active=0 WHERE wallet=? AND category=?", (WALLET, MLB_CAT))
        conn.commit()
        paper.paper_rollup(conn, now_ts=NOW)
        stats_after = conn.execute(
            "SELECT COUNT(*) FROM pm_paper_category_stats WHERE wallet=?", (WALLET,)).fetchone()[0]
        trades_after = conn.execute(
            "SELECT COUNT(*) FROM pm_paper_trade WHERE wallet=?", (WALLET,)).fetchone()[0]

    assert stats_after == 0, "deactivated pair's stale stats row must be removed (R1 airtight)"
    assert trades_after == 2, "pm_paper_trade rows must survive (deactivation is reversible)"
