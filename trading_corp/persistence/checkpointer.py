"""LangGraph checkpointer wired to SQLite for crash-resume across HITL pauses.

The saver runs its OWN SQLite connection. It must NOT share the primary
`trading_corp.db` file: on the shared file it was a competing writer that held
the single WAL write slot during HITL interrupt/resume bursts (e.g. PMCC roll
approvals) and starved every other division's writes — the 2026-07-10
"database is locked" storms. See reports/2026-07-10_db_lock_storm_diagnosis.md.
Isolating checkpoints onto their own file removes that contention entirely, as
main.py's Phase-1a note already prescribed ("swap to a separate saver instance
with its own DB file rather than re-sharing").
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator


def checkpoint_db_path(main_db_path: Path | str) -> Path:
    """Return the dedicated checkpointer DB path — a sibling of the main DB.

    e.g. `.../data/trading_corp.db` -> `.../data/checkpoints.db`. Keeping it a
    sibling (same directory) means backups/ops that target the data dir still
    capture it, while the file itself is a distinct SQLite database with its own
    write lock, so the saver never contends with the shared trading_corp.db.
    """
    return Path(main_db_path).with_name("checkpoints.db")


@asynccontextmanager
async def make_checkpointer(db_path: Path) -> AsyncIterator[object]:
    """Yield an AsyncSqliteSaver bound to `db_path`.

    `db_path` is the checkpointer's OWN file (see `checkpoint_db_path`), NOT the
    shared trading_corp.db. Imported lazily so test environments without
    langgraph can still import the persistence package.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        # Tune the saver's own connection. A freshly-created checkpoints.db
        # defaults to rollback journalling; WAL lets the boot-recovery reconciler
        # read (aget_tuple) concurrently with graph writes. busy_timeout is
        # generous because this is now the ONLY writer on its file. synchronous=
        # NORMAL is WAL-safe and roughly halves commit cost (only risks the last
        # transaction on OS/power loss, never corruption).
        conn = getattr(saver, "conn", None)
        if conn is not None:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA busy_timeout=30000;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
        yield saver
