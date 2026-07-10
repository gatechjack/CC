"""Checkpointer isolation + PRAGMA tuning (2026-07-10 lock-storm fix).

The LangGraph saver must bind to a dedicated checkpoints.db (sibling of the main
DB), never the shared trading_corp.db, and its connection must be WAL + generous
busy_timeout so it is not a competing writer on the shared file.
See reports/2026-07-10_db_lock_storm_diagnosis.md.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trading_corp.persistence.checkpointer import checkpoint_db_path


def test_checkpoint_db_path_is_isolated_sibling():
    p = checkpoint_db_path(Path("/srv/app/data/trading_corp.db"))
    assert p == Path("/srv/app/data/checkpoints.db")
    # It must NEVER be the shared DB file, and must live beside it.
    assert p.name == "checkpoints.db"
    assert p.parent == Path("/srv/app/data")


def test_checkpoint_db_path_accepts_str():
    assert checkpoint_db_path("data/trading_corp.db") == Path("data/checkpoints.db")


def test_make_checkpointer_binds_separate_wal_file(tmp_path):
    """make_checkpointer must create the exact file it is given, in WAL mode with
    a generous busy_timeout — proving the saver is isolated + tuned."""
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    from trading_corp.persistence.checkpointer import make_checkpointer

    main_db = tmp_path / "trading_corp.db"
    ckpt = checkpoint_db_path(main_db)

    async def _run():
        async with make_checkpointer(ckpt) as saver:
            mode = (await (await saver.conn.execute("PRAGMA journal_mode;")).fetchone())[0]
            busy = (await (await saver.conn.execute("PRAGMA busy_timeout;")).fetchone())[0]
            return mode, busy

    mode, busy = asyncio.run(_run())
    assert mode.lower() == "wal"
    assert busy >= 30000
    # The checkpoint DB was created as its own file, distinct from the main DB.
    assert ckpt.exists()
    assert ckpt.name == "checkpoints.db"
    assert not main_db.exists()  # make_checkpointer must not touch the shared DB
