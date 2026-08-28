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
        "pm_analysis_cache", "pm_analysis_cost",   # migration 007 (CP3b-2)
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
    assert count == 10  # migrations 001..010 recorded exactly once each (009 Stage-1 paper stats, 010 Stage-3 money layer)
    assert maxv == 10


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
    # grace SEED = 172800 (48h, migration-005 INSERT OR IGNORE). init_db does NOT re-tune it: migration 009 is
    # pure DDL (FIX-2 option ii). The 72h ruling is the CODE DEFAULT (CONFIG_DEFAULTS, see test_paper) and is
    # written to the LIVE row by an explicit Stage-1 rung step, NOT by init_db -- so a fresh DB reads the seed.
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
    # defaults: roster.active=1, watchlist.status='watchlist' (VESTIGIAL frozen 006 default; vocab is
    # 'candidate'|'pinned' -- this asserts the immutable migration, NOT the current vocabulary; see db.py DDL note)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_roster (wallet, category) VALUES ('0xw', 'ufc')")
        conn.execute("INSERT INTO pm_watchlist (wallet, category) VALUES ('0xw', 'ufc')")
        active = conn.execute("SELECT active FROM pm_roster WHERE wallet='0xw'").fetchone()[0]
        status = conn.execute("SELECT status FROM pm_watchlist WHERE wallet='0xw'").fetchone()[0]
    assert active == 1
    assert status == "watchlist"   # FROZEN 006 default (vestigial); vocab is 'candidate'|'pinned', but 006 is applied+immutable -- do NOT "fix" this to 'candidate'


def test_migration_010_money_layer_schema(tmp_path):
    """Migration 010 (Stage 3 R1): the money-layer schema. pm_account (credential REFERENCE + NULLABLE
    owner_identity; identity is Authelia's -> NO pm_user/pm_role/pm_grant), the sub-division (account,
    category) entity with sizing/risk/market_types config (FIXED sizing per ruling #1; Kelly column shape
    carried-not-built; market_types carries 'spread' even if Kalshi lists no run-line), and a per-sub-division
    live-order log shaped to brokers/kalshi_live.py's actual returns."""
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    with db.connect(p) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        acc = {r[1] for r in conn.execute("PRAGMA table_info(pm_account)")}
        acc_pk = {r[1] for r in conn.execute("PRAGMA table_info(pm_account)") if r[5] > 0}
        sub = {r[1] for r in conn.execute("PRAGMA table_info(pm_subdivision)")}
        sub_pk = {r[1] for r in conn.execute("PRAGMA table_info(pm_subdivision)") if r[5] > 0}
        odr = {r[1] for r in conn.execute("PRAGMA table_info(pm_subdivision_order)")}
    assert {"pm_account", "pm_subdivision", "pm_subdivision_order"} <= tables
    # identity tables must be ABSENT -- Authelia owns identity (the app owns only the login->account mapping)
    assert not ({"pm_user", "pm_role", "pm_grant"} & tables)
    # pm_account: credential REFERENCE + nullable owner_identity
    assert acc_pk == {"account_id"}
    assert {"secret_ref", "owner_identity", "venue", "active"} <= acc
    # sub-division (account, category) + sizing/risk/market_types config
    assert sub_pk == {"account_id", "category"}
    assert {"sizing_mode", "fixed_stake_usd", "kelly_fraction", "market_types",
            "per_order_usd_cap", "daily_usd_cap", "max_open_usd", "max_orders_per_day",
            "max_slippage_cents"} <= sub
    # live-order log shaped to kalshi_live's return: idempotency key, submitted V2 body, outcome, fill facts
    assert {"client_order_id", "signal_id", "ticker", "order_side", "outcome_leg", "is_exit",
            "submitted_count", "submitted_price", "time_in_force", "outcome_status",
            "broker_order_id", "fill_count", "fill_price", "remaining_count", "fee",
            "error_detail", "dry_run"} <= odr
    # DDL defaults: owner_identity NULLABLE (empty until Authelia logins), fixed sizing (ruling #1),
    # market_types carries all three incl 'spread' (Jack's scope ruling) without a future migration.
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_account (account_id) VALUES ('kalshi_jack')")
        conn.execute("INSERT INTO pm_subdivision (account_id, category) VALUES ('kalshi_jack', 'mlb')")
        owner, venue = conn.execute(
            "SELECT owner_identity, venue FROM pm_account WHERE account_id='kalshi_jack'").fetchone()
        sm, mt = conn.execute(
            "SELECT sizing_mode, market_types FROM pm_subdivision WHERE account_id='kalshi_jack'").fetchone()
    assert owner is None          # NULLABLE, empty until family logins arrive
    assert venue == "kalshi"
    assert sm == "fixed"          # ruling #1
    assert "moneyline" in mt and "total" in mt and "spread" in mt   # Jack's scope ruling, carried w/o a migration


def test_migration_010_is_pure_ddl():
    """Jack RULED (mirroring 009's FIX-2): migration 010 is PURE DDL -- no config/data writes. Every statement
    is a CREATE (table/index); no INSERT/UPDATE/DELETE. So init_db 9->10 adds NO config row."""
    for stmt in db.MIGRATION_010:
        head = stmt.strip().split()[0].upper()
        assert head == "CREATE", "migration 010 must be PURE DDL; got a non-CREATE statement: %r" % stmt[:60]
