"""pm_web ASGI app (FastAPI) -- standalone Prediction Markets web (P2). Launched by scripts/pm_web.py (uvicorn).

STANDALONE by construction: imports ONLY fastapi + the PM package (db / stats / category). NO engine imports
(trading_corp.web / main / agents), NO WebDeps, NO agent handles -- proven by test_pm_web_imports_no_engine.
(stats pulls trading_corp.data.kalshi_whale_stats for the ranking primitives -- data layer, NOT web/main/agents.)
Reads/writes ONLY data/prediction_markets.db (prediction_markets.db._assert_not_legacy hard-guards the path).
Reuses the engine web IDIOM (FastAPI + the off-loop `asyncio.to_thread` read pattern, mace_view) but not the process.

CP2 Phase 1 = /healthz. CP2 Phase 2 = the scoreboard page (/ + /scoreboard partial). Drill-through = Phase 3.
Vendored assets ONLY (/static/htmx.min.js, /static/pm.css) -- NO CDN on a network-exposed host, no build step.
Spec: reports/prediction_markets/P2_PLAN.md §3.1, §6.0; P2_KICKOFF_2026-08-23.md.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..db import connect
from .. import stats, positions, names
from ..category import NON_SINGLE_GAME_CATEGORIES

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"

ALLOWED_ROUTINES = ("net_roi", "recency_weighted")

# loopback-only + behind Authelia => no OpenAPI/docs surface exposed.
app = FastAPI(title="pm_web", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
# Expose the non-single-game category set so the template can render single_game_pct as "n/a" (NOT 0%) for
# fed/unknown -- the whole reason OQ-2 was ruled NULL. Truth stays in category.py; the template only reads it.
templates.env.globals["non_single_game_categories"] = sorted(NON_SINGLE_GAME_CATEGORIES)


def _utcdate(ts) -> str:
    """unix ts -> 'YYYY-MM-DD' (UTC); em-dash for missing/zero. Registered as the `utcdate` Jinja filter
    for the position-row renderer (honest-empty date, never a fake 1970)."""
    if isinstance(ts, (int, float)) and ts:
        return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))
    return "—"


templates.env.filters["utcdate"] = _utcdate


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


# ── scoreboard (CP2 Phase 2) ────────────────────────────────────────────────────────────────────

def _clamp_params(category: str | None, routine: str | None, min_resolved) -> tuple[str | None, str, int]:
    """Coerce hand-editable query params to safe values (never 422 on a bad control value). Unknown routine
    -> net_roi; unparseable/negative min_resolved -> default/0; 'all'/'' category -> None (=> All)."""
    rt = routine if routine in ALLOWED_ROUTINES else "net_roi"
    try:
        mr = int(min_resolved)
    except (TypeError, ValueError):
        mr = stats.DEFAULT_MIN_RESOLVED
    mr = max(0, min(mr, 1_000_000))
    cat = category or None
    if cat is not None and cat.strip().lower() in ("", "all"):
        cat = None
    return cat, rt, mr


def _load_scoreboard(category: str | None, routine: str, min_resolved: int, now_ts: int) -> dict:
    """Read the ranked board + filter options + freshness on ONE short-lived read connection. Runs OFF the
    event loop (asyncio.to_thread). Reads ONLY the PM DB. Attaches the shared flag tokens per row so the page
    and the CLI report render the IDENTICAL flag set (parity)."""
    with connect() as conn:
        board = stats.query_scoreboard(conn, category=category, routine=routine, min_resolved=min_resolved)
        categories = stats.distinct_categories(conn)
        refresh = stats.refresh_band_state(stats.max_refresh_ts(conn), now_ts)
    for r in board:
        r["flags"] = stats.scoreboard_flags(r)   # THE shared deriver (same tokens the CLI emits)
    return {"board": board, "categories": categories, "refresh": refresh}


def _context(request: Request, category: str | None, routine: str, min_resolved: int, data: dict) -> dict:
    return {
        "request": request,
        "board": data["board"],
        "categories": data["categories"],
        "refresh": data["refresh"],
        "routines": ALLOWED_ROUTINES,
        "sel_category": category or "",
        "sel_routine": routine,
        "sel_min_resolved": min_resolved,
    }


@app.get("/", response_class=HTMLResponse)
async def scoreboard_page(request: Request, category: str | None = None,
                          routine: str | None = None, min_resolved: str | None = None):
    """Full scoreboard page. Query params echo the controls so a direct link is shareable and it works
    with JS disabled (the form is a plain GET; HTMX only upgrades it to a partial swap)."""
    cat, rt, mr = _clamp_params(category, routine, min_resolved)
    data = await asyncio.to_thread(_load_scoreboard, cat, rt, mr, int(time.time()))
    return templates.TemplateResponse(request, "pm_scoreboard.html", _context(request, cat, rt, mr, data))


@app.get("/scoreboard", response_class=HTMLResponse)
async def scoreboard_table(request: Request, category: str | None = None,
                           routine: str | None = None, min_resolved: str | None = None):
    """Table fragment for HTMX hx-get re-render. Same query as the full page; the controls swap THIS partial.
    Ordering + tie-breaks are decided server-side in query_scoreboard -- the browser never re-sorts."""
    cat, rt, mr = _clamp_params(category, routine, min_resolved)
    data = await asyncio.to_thread(_load_scoreboard, cat, rt, mr, int(time.time()))
    return templates.TemplateResponse(request, "partials/pm_scoreboard_table.html", _context(request, cat, rt, mr, data))


# ── whale detail + drill-through (CP2 Phase 3, Ruling R5) ────────────────────────────────────────

def _applicable_drills(category: str | None) -> list[str]:
    """Drill tabs valid for this category. single_game is dropped for fed/unknown (no single-game
    notion, OQ-2) so the toolbar never offers a drill that would render honest-empty by definition."""
    ds = ["scoreable", "won", "two_sided"]
    if category not in NON_SINGLE_GAME_CATEGORIES:
        ds.append("single_game")
    ds += ["quarantined", "all"]
    return ds


def _clamp_drill(drill: str | None, category: str | None) -> str:
    """Coerce a hand-editable ?drill= to a safe value (never 422). Unknown -> scoreable; single_game on
    fed/unknown -> scoreable (that category has no single-game notion)."""
    d = drill if drill in positions.DRILLS else "scoreable"
    if d == "single_game" and category in NON_SINGLE_GAME_CATEGORIES:
        d = "scoreable"
    return d


def _load_whale_overview(wallet: str, now_ts: int) -> dict:
    """Whale + its (wallet, category) slices + freshness, on ONE short-lived read connection, OFF the loop."""
    with connect() as conn:
        whale = positions.whale_row(conn, wallet)
        cats = positions.whale_categories(conn, wallet)
        refresh = stats.refresh_band_state(stats.max_refresh_ts(conn), now_ts)
    return {"whale": whale, "cats": cats, "refresh": refresh}


def _load_whale_detail(wallet: str, category: str, drill: str, now_ts: int) -> dict:
    """The whale-detail read: header + score decomposition + caveat profile + the selected drill's rows
    with its reconciliation, on ONE short-lived read connection, OFF the loop."""
    with connect() as conn:
        whale = positions.whale_row(conn, wallet)
        cstats = positions.category_stats_row(conn, wallet, category)
        onesided = positions.onesided_row(conn, wallet, category)
        decomp = positions.score_decomposition(conn, wallet, category)
        rows = positions.drill_rows(conn, wallet, category, drill)
        recon = positions.reconcile(conn, wallet, category, drill, rows)
        refresh = stats.refresh_band_state(stats.max_refresh_ts(conn), now_ts)
        name_sync = names.last_sync(conn)
    return {"whale": whale, "cstats": cstats, "onesided": onesided, "decomp": decomp, "rows": rows,
            "recon": recon, "refresh": refresh, "name_sync": name_sync,
            "drill_label": positions.DRILL_LABELS.get(drill)}


def _load_drill(wallet: str, category: str, drill: str) -> dict:
    """Just the selected drill's rows + reconciliation (the HTMX partial), OFF the loop."""
    with connect() as conn:
        rows = positions.drill_rows(conn, wallet, category, drill)
        recon = positions.reconcile(conn, wallet, category, drill, rows)
    return {"rows": rows, "recon": recon, "drill_label": positions.DRILL_LABELS.get(drill)}


@app.get("/whale/{wallet}", response_class=HTMLResponse)
async def whale_overview(request: Request, wallet: str):
    """Overview for a bare wallet: the categories this whale has, each linking to the drill-through detail.
    Honest-empty (200) for an unknown wallet -- never a 404-shaped fabrication."""
    wallet = (wallet or "").lower()
    data = await asyncio.to_thread(_load_whale_overview, wallet, int(time.time()))
    return templates.TemplateResponse(request, "pm_whale_overview.html", {"request": request, "wallet": wallet, **data})


@app.get("/whale/{wallet}/{category}", response_class=HTMLResponse)
async def whale_detail(request: Request, wallet: str, category: str, drill: str | None = None):
    """The 'why is this ranked here' destination + the drill panel. ?drill= pre-selects a drill so a direct
    link (and JS-off navigation from a scoreboard caveat cell) lands on the right rows."""
    wallet = (wallet or "").lower()
    d = _clamp_drill(drill, category)
    data = await asyncio.to_thread(_load_whale_detail, wallet, category, d, int(time.time()))
    ctx = {"request": request, "wallet": wallet, "category": category, "drill": d,
           "drills": _applicable_drills(category), **data}
    return templates.TemplateResponse(request, "pm_whale.html", ctx)


@app.get("/whale/{wallet}/{category}/positions", response_class=HTMLResponse)
async def whale_positions(request: Request, wallet: str, category: str, drill: str | None = None):
    """The ONE shared row renderer as an HTMX partial -- the drill-panel swap target. Same renderer the
    whale detail includes server-side, so the two consumers can never diverge (parity)."""
    wallet = (wallet or "").lower()
    d = _clamp_drill(drill, category)
    data = await asyncio.to_thread(_load_drill, wallet, category, d)
    return templates.TemplateResponse(request, "partials/pm_position_rows.html", {"request": request, **data})
