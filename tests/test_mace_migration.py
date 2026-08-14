"""Migration test for the UI-rebuild schema additions (2026-08-14).

Covers the two net-new pieces of the MACE UI-rebuild data foundation:
  - `mace_rung_live` (CREATE TABLE IF NOT EXISTS in db.SCHEMA)
  - `mace_rung.entry_atm_iv` (_maybe_add_column idempotent ALTER)

Asserts the migration is idempotent (init_db twice = no error, same shape) and
that the standalone migrate script's verify() confirms both land. Mirrors the
Checkpoint-1 procedure (run against a copy, --verify-only against live).
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from trading_corp.persistence import db as dbmod

ROOT = Path(__file__).resolve().parents[1]


def _load_migrate_module():
    """Import scripts/migrate_mace_tables.py by path (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "migrate_mace_tables", ROOT / "scripts" / "migrate_mace_tables.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_mace_rung_live_table_created(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    dbmod.init_db(url)
    conn = sqlite3.connect(dbmod.resolve_db_path(url))
    try:
        assert "mace_rung_live" in _tables(conn)
        cols = _cols(conn, "mace_rung_live")
        assert cols == {"rung_id", "symbol", "mark", "spot", "ts"}
    finally:
        conn.close()


def test_entry_atm_iv_column_added(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    dbmod.init_db(url)
    conn = sqlite3.connect(dbmod.resolve_db_path(url))
    try:
        assert "entry_atm_iv" in _cols(conn, "mace_rung")
    finally:
        conn.close()


def test_migration_idempotent(tmp_path):
    """Running init_db twice must not error and must leave the same shape —
    this is exactly what the prod migrate script does (init_db is the whole
    migration), so a green here == a safe re-runnable prod migration."""
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    dbmod.init_db(url)
    conn = sqlite3.connect(dbmod.resolve_db_path(url))
    try:
        before_tables = _tables(conn)
        before_cols = _cols(conn, "mace_rung")
    finally:
        conn.close()
    # second run — must be a no-op, no duplicate-column / duplicate-table error
    dbmod.init_db(url)
    conn = sqlite3.connect(dbmod.resolve_db_path(url))
    try:
        assert _tables(conn) == before_tables
        assert _cols(conn, "mace_rung") == before_cols
        assert "mace_rung_live" in before_tables
        assert "entry_atm_iv" in before_cols
    finally:
        conn.close()


def test_migrate_script_verify_passes(tmp_path):
    """The standalone migrate script's verify() confirms the new table +
    column (so its prod --verify-only pre-check is trustworthy)."""
    mig = _load_migrate_module()
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    dbmod.init_db(url)
    assert mig.verify(url) is True
    assert "mace_rung_live" in mig.MACE_TABLES
    assert ("mace_rung", "entry_atm_iv") in mig.MACE_COLUMNS


def test_in_memory_schema_has_live_table():
    """The in-memory SCHEMA path (used by the fast unit tests) also carries the
    new table — so live-state tests can rely on dbmod.SCHEMA alone."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(dbmod.SCHEMA)
    assert "mace_rung_live" in _tables(conn)
