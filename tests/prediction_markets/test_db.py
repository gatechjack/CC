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
    assert count == 4  # migrations 001 + 002 + 003 + 004 recorded exactly once each
    assert maxv == 4


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
