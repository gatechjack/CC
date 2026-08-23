"""pm_web ASGI app (FastAPI) -- standalone Prediction Markets web (P2). Launched by scripts/pm_web.py (uvicorn).

STANDALONE by construction: imports ONLY fastapi + the PM package's own db layer. NO engine imports
(trading_corp.web / main / agents), NO WebDeps, NO agent handles -- proven by test_pm_web_imports_no_engine.
Reads/writes ONLY data/prediction_markets.db (prediction_markets.db._assert_not_legacy hard-guards the path).
Reuses the engine web IDIOM (FastAPI + the off-loop `asyncio.to_thread` read pattern, mace_view) but not the process.

CP2 Phase 1 = /healthz only. Product pages (scoreboard, drill-through) land in later CP2 phases.
Spec: reports/prediction_markets/P2_PLAN.md §3.1, §6.0.
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..db import connect

# loopback-only + behind Authelia => no OpenAPI/docs surface exposed.
app = FastAPI(title="pm_web", docs_url=None, redoc_url=None, openapi_url=None)


def _pm_db_schema_version() -> int | None:
    """Short-lived read connection; reads ONLY prediction_markets.db (db._assert_not_legacy guards the path)."""
    with connect() as conn:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"]) if row is not None and row["v"] is not None else None


@app.get("/healthz")
async def healthz():
    """Liveness + PM-DB readiness. The DB read runs OFF the event loop (`asyncio.to_thread`) so a slow/locked DB
    can never block pm_web. 200 when the PM DB is reachable + migrated; 503 'degraded' otherwise -- honest,
    never a faked 200."""
    try:
        version = await asyncio.to_thread(_pm_db_schema_version)
    except Exception as exc:  # noqa: BLE001 -- healthz must never raise; report degraded honestly
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "service": "pm_web", "error": type(exc).__name__},
        )
    return {"status": "ok", "service": "pm_web", "pm_db_schema_version": version}
