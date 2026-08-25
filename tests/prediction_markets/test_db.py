"""Tests for trading_corp.prediction_markets.db — runs with NO engine, NO live DB.

Every test uses a tmp DB file (tmp_path) — never the default/legacy path.
Spec: reports/prediction_markets/P1_PLAN.md §11.
"""
import os

import pytest

from trading_corp.prediction_markets import db


def test_refuses_legacy_db_path():
    # relative default legacy path
    with pytest.raises(RuntimeError):
        with db.connect("data/trading_corp.db"):
            pass
    # absolute path pointing at the legacy file by name
    with pytest.raises(RuntimeError):
        with db.connect("/opt/whatever/trading_corp.db"):
            pass


def test_init_creates_all_tables(tmp_path):
    p = str(tmp_path / "pm.db")
    assert db.init_db(p) == p
    with db.connect(p) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "schema_version", "pm_whale", "pm_closed_position",
        "pm_category_stats", "pm_open_position", "pm_score_snapshot",
    } <= tables


def test_pragmas_match_legacy(tmp_path):
    p = str(tmp_path / "pm.db")
    with db.connect(p) as conn:
        assert conn.execute("PRAGMA journal_mode;").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == 5000
        assert conn.execute("PRAGMA synchronous;").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA foreign_keys;").fetchone()[0] == 1


def test_migrations_idempotent(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    db.init_db(p)  # second run must be a no-op
    with db.connect(p) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        maxv = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert count == 6  # migrations 001..006 recorded exactly once each
    assert maxv == 6


def test_pk_includes_outcome_index(tmp_path):
    """Migration 002: two-sided holdings (same condition_id, distinct outcome_index) must both persist,
    so outcome_index is part of the PK on BOTH position tables (PRAGMA table_info pk field > 0)."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        cp_pk = {r[1] for r in conn.execute("PRAGMA table_info(pm_closed_position)") if r[5] > 0}
        op_pk = {r[1] for r in conn.execute("PRAGMA table_info(pm_open_position)") if r[5] > 0}
    assert cp_pk == {"wallet", "condition_id", "outcome_index"}
    assert op_pk == {"wallet", "condition_id", "outcome_index"}


def test_amendment_columns_present(tmp_path):
    """§3A/§6: pnl_suspect + suspect_reason on closed positions; n_excluded/
    excluded_pnl/data_quality on category stats; params_json on score snapshot;
    the scoreable index."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        cp = {r[1] for r in conn.execute("PRAGMA table_info(pm_closed_position)")}
        cs = {r[1] for r in conn.execute("PRAGMA table_info(pm_category_stats)")}
        ss = {r[1] for r in conn.execute("PRAGMA table_info(pm_score_snapshot)")}
        pw = {r[1] for r in conn.execute("PRAGMA table_info(pm_whale)")}
        idx = {r[1] for r in conn.execute("PRAGMA index_list(pm_closed_position)")}
    assert {"backfill_complete", "last_pulled", "last_stored"} <= pw   # migration 003 (Step-4 429 safety)
    assert {"won", "category_source", "pnl_suspect", "suspect_reason",
            "pnl_anomaly", "anomaly_reason", "cost_basis", "shares_derived", "realized_pnl"} <= cp
    assert {"n_excluded", "excluded_pnl", "n_anomaly", "dq_count_pct",
            "dq_dollar_pct", "cost_basis", "roi", "roi_notional", "data_quality"} <= cs
    assert "params_json" in ss
    assert "ix_pm_cp_scoreable" in idx


def test_scoreable_predicate_single_definition():
    # the ONE canonical §3A predicate; consumers must build from this
    assert db.SCOREABLE_PREDICATE_SQL == "pnl_suspect = 0"
    assert db.scoreable_where() == "pnl_suspect = 0"
    assert db.scoreable_where("cp") == "cp.pnl_suspect = 0"


def test_pm_db_path_env_override(monkeypatch, tmp_path):
    p = str(tmp_path / "custom_pm.db")
    monkeypatch.setenv("PM_DB_PATH", p)
    assert db.pm_db_path() == p
    db.init_db()  # no arg -> uses the env override
    assert os.path.exists(p)


def test_migration_005_paper_trade_lifecycle(tmp_path):
    """Migration 005 (CP3a): pm_paper_trade carries the COMPLETE open->pending_adjudication->
    closed|stale|void lifecycle in ONE migration; entry columns are observation-provenance (no entry_ts
    alias, addendum 2); pm_paper_config is seeded with the tunable poll interval + adjudication grace
    window + fixed size basis."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        pt = {r[1] for r in conn.execute("PRAGMA table_info(pm_paper_trade)")}
        pt_pk = {r[1] for r in conn.execute("PRAGMA table_info(pm_paper_trade)") if r[5] > 0}
        cfg = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM pm_paper_config")}
    assert {"pm_paper_trade", "pm_paper_config"} <= tables
    # PK: two-sided legs (outcome_index) + full-exit-then-re-enter over time (entry_observed_ts)
    assert pt_pk == {"wallet", "condition_id", "outcome_index", "entry_observed_ts"}
    # entry_observed_ts is the ONLY entry-time column -- no entry_ts alias (addendum 2 ruling)
    assert "entry_observed_ts" in pt and "entry_ts" not in pt
    # complete lifecycle present in ONE migration (the _STATS_COLS trap)
    assert {"status", "exit_observed_ts", "resolved_ts", "won", "realized_pnl",
            "close_source", "stale_ts", "stale_reason"} <= pt
    # scale-in / reduction observation (addendum 3), diagnostic-only
    assert {"n_observed_adds", "last_add_observed_ts", "n_observed_reductions",
            "last_reduction_observed_ts", "last_observed_size", "last_observed_ts"} <= pt
    # entry provenance + display-only whale size + parity columns
    assert {"entry_price_avg_at_observation", "whale_size_at_observation", "size_basis", "cost_basis",
            "poll_interval_sec", "entry_basis", "market_end_date", "pnl_suspect", "suspect_reason"} <= pt
    # pm_paper_config seeded (addendum 1 grace window; tunable poll interval; fixed size basis)
    assert cfg.get("poll_interval_sec") == "300"
    assert cfg.get("grace_window_sec") == "172800"
    assert cfg.get("size_basis") == "100"
    # status DEFAULTs to 'open' on insert of a minimal row
    with db.connect(p) as conn:
        conn.execute(
            "INSERT INTO pm_paper_trade (wallet, category, condition_id, outcome_index, "
            "entry_observed_ts, opened_ts) VALUES ('0xabc', 'mlb', '0xcond', 0, 111, 111)")
        st = conn.execute("SELECT status FROM pm_paper_trade WHERE wallet='0xabc'").fetchone()[0]
    assert st == "open"


def test_migration_006_roster_and_watchlist(tmp_path):
    """Migration 006 (CP3a): pm_roster (universal (wallet,category) roster; active=1 default -> the weekly
    refresh source) + pm_watchlist (per-(wallet,category) farm status candidate|pinned). The PINNING
    CATEGORY lives here as explicit provenance (C2.4), NOT derived from cross-category pm_category_stats."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        r_pk = {r[1] for r in conn.execute("PRAGMA table_info(pm_roster)") if r[5] > 0}
        r_cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_roster)")}
        w_pk = {r[1] for r in conn.execute("PRAGMA table_info(pm_watchlist)") if r[5] > 0}
        w_cols = {r[1] for r in conn.execute("PRAGMA table_info(pm_watchlist)")}
    assert {"pm_roster", "pm_watchlist"} <= tables
    assert r_pk == {"wallet", "category"}
    assert w_pk == {"wallet", "category"}
    assert {"user_name", "source", "added_ts", "active", "notes", "last_polled_ts"} <= r_cols
    assert {"status", "pinned_ts", "search_run_id", "updated_ts"} <= w_cols
    # defaults: roster.active=1, watchlist.status='candidate' (CP3b-0 rename from 'watchlist')
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_roster (wallet, category) VALUES ('0xw', 'ufc')")
        conn.execute("INSERT INTO pm_watchlist (wallet, category) VALUES ('0xw', 'ufc')")
        active = conn.execute("SELECT active FROM pm_roster WHERE wallet='0xw'").fetchone()[0]
        status = conn.execute("SELECT status FROM pm_watchlist WHERE wallet='0xw'").fetchone()[0]
    assert active == 1
    assert status == "candidate"
