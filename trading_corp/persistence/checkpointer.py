"""LangGraph checkpointer wired to SQLite for crash-resume across HITL pauses."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator


@asynccontextmanager
async def make_checkpointer(db_path: Path) -> AsyncIterator[object]:
    """Yield an AsyncSqliteSaver bound to the given path.

    Imported lazily so test environments without langgraph can still import
    the persistence package.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        yield saver
