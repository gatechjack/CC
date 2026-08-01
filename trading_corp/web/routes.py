"""HTTP routes for the command center.

Phase 1 ships:
  GET /                       Command Center page
  GET /partials/trade-flow    Trade-flow rail fragment (HTMX-polled)
  GET /partials/stat-cards    Stat cards fragment (HTMX-polled)
  GET /partials/equity-curve  JSON for the equity-curve chart
  GET /partials/market-ribbon Top market overview ribbon

Phase 2 adds:
  GET /division/{slug}                  Drill-down page for a division
  GET /division/{slug}/llm-analysis     Lazy HTMX-loaded LLM analysis for PMCC
  GET /partials/division-equity-curve/{slug}  JSON for per-account equity chart
  GET /partials/donchian-chart/{slug}         JSON for the Donchian 6h price chart

Phases 3+ will add /trades, /system.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from threading import Lock

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from trading_corp.persistence.models import AccountState, StrategyState
from trading_corp.utils.time import format_et_full

from trading_corp.web import data

log = logging.getLogger(__name__)

# ── LLM analysis cache (per-division) ─────────────────────────────────────
# Module-level cache so the LLM analysis (slow, multi-call) is reused across
# tabs and quick refreshes within the TTL window. 5 min is long enough that
# "scroll back from another page" is fast, short enough that mid-day
# revisits get fresh analysis.
_LLM_CACHE_TTL_SEC = 300
_LLM_CACHE: dict[str, tuple[str, float]] = {}     # slug → (html, fetched_at)
_LLM_LOCK = Lock()

# P1 (2026-07-31): max adverse credit drift (per-share net) tolerated between the
# APPROVED snapshot and the dispatch reprice before the division dispatch aborts +
# re-surfaces (shown == fired). A hair above the reprice give_up (0.02) so normal
# tick noise doesn't bail, but a real quote move does.
_REPRICE_MAX_ADVERSE_NET = 0.05

# Deferral: when the user clicks "Defer 24h" on a recommendation, we suppress
# new analysis for that (slug, symbol) for this many hours. Stored as audit
# events (kind=pair_deferred) so it survives restarts and is auditable.
_DEFER_TTL_HOURS = 24


def _resolve_ic_division_ctx(deps, division: str) -> dict:
    """Map a `division` query-string value to the right strategy/registry/
    batcher handles on `deps`. Two IC divisions are supported today:

      - "robinhood_joint" → deps.ic_* fields (default)
      - "tasty_options"   → deps.tasty_* fields

    Unknown values fall back to robinhood_joint silently — defensive against
    bookmark drift. Returns a dict keyed by the field names the IC-live +
    grader endpoints use, so the call sites stay uniform.
    """
    if division == "tasty_options":
        return {
            "division_slug": "tasty_options",
            "division_name": "Tasty Options",
            "strategy_slug": "tasty_options_iron_condor",
            "broker_key": "tasty_options",
            "strategy": getattr(deps, "tasty_strategy", None),
            "division": getattr(deps, "tasty_division", None),
            "pending_combo_registry": getattr(
                deps, "tasty_pending_combo_registry", None,
            ),
            "telegram_batcher": getattr(
                deps, "tasty_telegram_batcher", None,
            ),
        }
    # Default / "robinhood_joint" — preserves pre-2026-05-24 behavior.
    return {
        "division_slug": "robinhood_joint",
        "division_name": "Robinhood Joint",
        "strategy_slug": "robinhood_joint_iron_condor",
        "broker_key": "robinhood_joint",
        "strategy": getattr(deps, "ic_strategy", None),
        "division": getattr(deps, "ic_division", None),
        "pending_combo_registry": getattr(deps, "pending_combo_registry", None),
        "telegram_batcher": getattr(deps, "ic_telegram_batcher", None),
    }


def _group_index_entries(entries: list) -> list[dict]:
    """Group `/approvals` index entries by pmcc_pair_id (Phase B.3).

    Solo entries pass through as `kind='solo'` rows. Pairs (two entries
    sharing pmcc_pair_id) collapse into ONE `kind='paired'` row whose
    `primary_order_id` is the close leg (action contains 'close') if
    discoverable, else the first entry encountered. The list preserves
    newest-first ordering by the row's most-recent added_at.
    """
    by_pair: dict[str, list] = {}
    solos: list[dict] = []
    for e in entries:
        pid = e.pmcc_pair_id
        if not pid:
            solos.append({
                "kind": "solo",
                "entries": [e],
                "is_paired": False,
                "primary_order_id": e.request.order_id,
                "division": e.division,
                "summary": e.request.summary,
                "added_at": e.added_at,
                "pair_id": None,
            })
            continue
        by_pair.setdefault(pid, []).append(e)

    rows: list[dict] = list(solos)
    for pid, group in by_pair.items():
        if len(group) == 1:
            e = group[0]
            rows.append({
                "kind": "solo",
                "entries": [e],
                "is_paired": False,
                "primary_order_id": e.request.order_id,
                "division": e.division,
                "summary": e.request.summary,
                "added_at": e.added_at,
                "pair_id": pid,
            })
            continue
        # Two-leg pair (or more, defensively) — pick the 'close' leg as
        # the primary so the URL anchors there. If neither leg has a
        # parseable action, just use the first.
        close_leg = next(
            (e for e in group if "close" in (
                _summary_action_hint(e.request.summary).lower()
            )),
            group[0],
        )
        sym = _summary_symbol(close_leg.request.summary)
        combined_summary = (
            f"ROLL · {sym} · close + open" if sym else "ROLL · close + open"
        )
        rows.append({
            "kind": "paired",
            "entries": group,
            "is_paired": True,
            "primary_order_id": close_leg.request.order_id,
            "division": close_leg.division,
            "summary": combined_summary,
            "added_at": min(e.added_at for e in group),
            "pair_id": pid,
        })

    rows.sort(key=lambda r: r["added_at"], reverse=True)
    return rows


def _summary_action_hint(summary: str) -> str:
    """Best-effort: pull an action token out of the rich approval summary.
    Used purely to pick a 'close leg' anchor for paired index rows.
    Returns empty string when the format isn't recognized."""
    if not summary:
        return ""
    head = summary.split("\n", 1)[0]
    return head


def _summary_symbol(summary: str) -> str:
    r"""Best-effort: pull the underlying symbol out of the approval
    summary's first line. The format-style is `📤 *ROLL: BUY TO CLOSE*
    · \`MSTR\` · robinhood_pmcc`. Returns the first backtick-wrapped
    token or empty string."""
    if not summary:
        return ""
    head = summary.split("\n", 1)[0]
    # Find the first `...` token.
    import re
    m = re.search(r"`([^`]+)`", head)
    return m.group(1) if m else ""


def register(app: FastAPI) -> None:
    templates = app.state.templates
    deps = app.state.deps

    # External webhooks (TradingView etc.) live in their own module so
    # the route table here stays focused on the dashboard. Webhooks
    # share the same FastAPI app and the same `deps`, so registering
    # them here keeps everything reachable on a single port.
    from trading_corp.web import webhooks
    webhooks.register(app)
    from trading_corp.web import sfp_cockpit_view
    sfp_cockpit_view.register(app)
    # SFP CONSTRUCT cockpit (situational-awareness view for the live construct) at /sfp/construct.
    # Display layer only; REVIEW build mounted alongside the live /sfp cockpit (no cutover yet).
    from trading_corp.web import sfp_construct_cockpit_view
    sfp_construct_cockpit_view.register(app)
    # SFP LLM Trade Analysis (the 3rd SFP screen) at /sfp/llm. Display-only; reads the Market-Context
    # Recorder's market_context.db READ-ONLY (engine is not a writer of it) — shows the blind LLM
    # shadow-logger rows + news sentiment + outcome per trade. Honest-empty; verdict gated at n>=30.
    from trading_corp.web import sfp_llm_analysis_view
    sfp_llm_analysis_view.register(app)

    # ── PWA: serve service worker + manifest at root scope ─────────────
    # The service worker MUST be served from the root path (`/sw.js`),
    # not from `/static/sw.js` — its scope is determined by the URL
    # path it's served from. A SW served under `/static/` could only
    # intercept requests to `/static/*`, defeating the purpose.
    # FastAPI's StaticFiles mount is at `/static/`, so we add a couple
    # of explicit root-level routes that proxy to the same files on disk.
    from fastapi.responses import FileResponse
    from pathlib import Path as _P
    _STATIC = _P(__file__).parent / "static"
    _TEMPLATES = _P(__file__).parent / "templates"

    @app.get("/sw.js", include_in_schema=False)
    async def serve_service_worker():
        # Service-Worker-Allowed extends the SW's scope; without it the
        # default scope is the directory the SW was served from. Setting
        # to "/" explicitly is belt-and-suspenders since we serve at root
        # already, but defends against future path changes.
        return FileResponse(
            _STATIC / "sw.js",
            media_type="application/javascript",
            headers={
                "Service-Worker-Allowed": "/",
                # Don't let browsers cache the SW itself for long — when
                # we ship a SW update, clients should pick it up within
                # a few minutes. The SW caches the rest of the app, but
                # the SW itself must be fresh.
                "Cache-Control": "no-cache, max-age=0",
            },
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def serve_manifest_root():
        # Some PWA implementations look for the manifest at root. We
        # primarily reference it via `/static/manifest.webmanifest` in
        # base.html, but the alias is cheap insurance.
        return FileResponse(
            _STATIC / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/offline.html", include_in_schema=False)
    async def serve_offline():
        # Plain HTML fallback shown by the SW when network is unavailable
        # AND the requested page isn't in the page cache.
        return FileResponse(
            _TEMPLATES / "offline.html",
            media_type="text/html",
        )

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, stage1: int = 0):
        # `?stage1=1` filters the trade-flow strip to Stage-1 (bitunix paper)
        # events only. Defaults off so the home page is byte-identical when
        # the toggle is untouched.
        stage1_only = bool(stage1)
        snap = await data.build_command_center(deps)
        flow = data.trade_flow(deps.db_url, limit=100, stage1_only=stage1_only)
        return templates.TemplateResponse(
            request, "home.html",
            {
                "snap": snap, "flow": flow,
                "stage1_only": stage1_only,
            },
        )

    @app.get("/partials/trade-flow", response_class=HTMLResponse)
    async def partial_trade_flow(request: Request, stage1: int = 0):
        stage1_only = bool(stage1)
        flow = data.trade_flow(deps.db_url, limit=100, stage1_only=stage1_only)
        return templates.TemplateResponse(
            request, "partials/trade_flow.html",
            {"flow": flow, "stage1_only": stage1_only},
        )

    @app.get("/partials/stat-cards", response_class=HTMLResponse)
    async def partial_stat_cards(request: Request):
        snap = await data.build_command_center(deps)
        return templates.TemplateResponse(
            request, "partials/stat_cards.html", {"snap": snap},
        )

    @app.get("/partials/equity-curve")
    async def partial_equity_curve():
        snap = await data.build_command_center(deps)
        return JSONResponse({"points": snap.equity_curve})

    @app.get("/partials/market-ribbon", response_class=HTMLResponse)
    async def partial_market_ribbon(request: Request):
        snap = await data.build_command_center(deps)
        return templates.TemplateResponse(
            request, "partials/market_ribbon.html", {"snap": snap},
        )

    # ── Prediction Markets dashboard (K2.4 Option C) ─────────────────────
    # Single dashboard for all prediction-market divisions with a division
    # dropdown. `/prediction-markets/` is the "All" combined view; the
    # slugged variant pre-selects one division. Tiles on the home page
    # link directly to /prediction-markets/{slug}; the dropdown navigates
    # within the dashboard. Same template, different data.

    @app.get("/prediction-markets/", response_class=HTMLResponse)
    async def prediction_markets_all(
        request: Request,
        pm_watch_sort: str | None = None,
        pm_watch_desc: int = 1,
        kalshi_watch_sort: str | None = None,
        kalshi_watch_desc: int = 1,
        kalshi_hide_uncopyable: int = 0,
        kalshi_hide_net_neg: int = 0,
        kalshi_selected_sort: str = "",
        kalshi_selected_desc: int = 1,
        kalshi_sel_hide_uncopyable: int = 0,
        kalshi_sel_hide_net_neg: int = 0,
    ):
        return await _render_pm_dashboard(
            request, division=None,
            pm_watch_sort=pm_watch_sort, pm_watch_desc=bool(pm_watch_desc),
            kalshi_watch_sort=kalshi_watch_sort, kalshi_watch_desc=bool(kalshi_watch_desc),
            kalshi_hide_uncopyable=bool(kalshi_hide_uncopyable),
            kalshi_hide_net_neg=bool(kalshi_hide_net_neg),
            kalshi_selected_sort=kalshi_selected_sort or None,
            kalshi_selected_desc=bool(kalshi_selected_desc),
            kalshi_sel_hide_uncopyable=bool(kalshi_sel_hide_uncopyable),
            kalshi_sel_hide_net_neg=bool(kalshi_sel_hide_net_neg),
        )

    @app.get("/prediction-markets/{division}", response_class=HTMLResponse)
    async def prediction_markets_one(
        request: Request, division: str,
        pm_watch_sort: str | None = None,
        pm_watch_desc: int = 1,
        kalshi_watch_sort: str | None = None,
        kalshi_watch_desc: int = 1,
        kalshi_hide_uncopyable: int = 0,
        kalshi_hide_net_neg: int = 0,
        kalshi_selected_sort: str = "",
        kalshi_selected_desc: int = 1,
        kalshi_sel_hide_uncopyable: int = 0,
        kalshi_sel_hide_net_neg: int = 0,
        wr_mode: str = "live",
    ):
        return await _render_pm_dashboard(
            request, division=division,
            pm_watch_sort=pm_watch_sort, pm_watch_desc=bool(pm_watch_desc),
            kalshi_watch_sort=kalshi_watch_sort, kalshi_watch_desc=bool(kalshi_watch_desc),
            kalshi_hide_uncopyable=bool(kalshi_hide_uncopyable),
            kalshi_hide_net_neg=bool(kalshi_hide_net_neg),
            kalshi_selected_sort=kalshi_selected_sort or None,
            kalshi_selected_desc=bool(kalshi_selected_desc),
            kalshi_sel_hide_uncopyable=bool(kalshi_sel_hide_uncopyable),
            kalshi_sel_hide_net_neg=bool(kalshi_sel_hide_net_neg),
            wr_mode=wr_mode,
        )

    async def _render_pm_dashboard(
        request: Request, division: str | None,
        *,
        pm_watch_sort: str | None = None,
        pm_watch_desc: bool = True,
        kalshi_watch_sort: str | None = None,
        kalshi_watch_desc: bool = True,
        kalshi_hide_uncopyable: bool = False,
        kalshi_hide_net_neg: bool = False,
        kalshi_selected_sort: str | None = None,
        kalshi_selected_desc: bool = True,
        kalshi_sel_hide_uncopyable: bool = False,
        kalshi_sel_hide_net_neg: bool = False,
        wr_mode: str = "live",
    ):
        # Paper/Live/All stats scope — whitelist; anything else → 'live'.
        wr_mode = wr_mode if wr_mode in ("all", "paper", "live") else "live"
        cmd_snap, view = await asyncio.gather(
            data.build_command_center(deps),
            data.build_prediction_market_view(
                deps, division,
                pm_watch_sort=pm_watch_sort, pm_watch_desc=pm_watch_desc,
                kalshi_watch_sort=kalshi_watch_sort, kalshi_watch_desc=kalshi_watch_desc,
                kalshi_hide_uncopyable=kalshi_hide_uncopyable,
                kalshi_hide_net_neg=kalshi_hide_net_neg,
                kalshi_selected_sort=kalshi_selected_sort,
                kalshi_selected_desc=kalshi_selected_desc,
                kalshi_sel_hide_uncopyable=kalshi_sel_hide_uncopyable,
                kalshi_sel_hide_net_neg=kalshi_sel_hide_net_neg,
                wr_mode=wr_mode,
            ),
        )
        if view is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown prediction-market division: {division}",
            )
        return templates.TemplateResponse(
            request, "prediction_markets_dashboard.html",
            {"snap": cmd_snap, "view": view},
        )

    # Partial endpoints — return JUST the dashboard body (no base.html
    # chrome) for the division-dropdown HTMX swap. Skips build_command_center
    # too: the outer page's header/footer doesn't change when only the
    # division does, so a partial swap doesn't need corp-wide snap data.
    # This is also what makes the swap fast — the heavy broker.snapshot()
    # fan-out from build_command_center doesn't run on swap.

    @app.get("/partials/prediction-markets/", response_class=HTMLResponse)
    async def prediction_markets_partial_all(
        request: Request,
        pm_watch_sort: str | None = None,
        pm_watch_desc: int = 1,
        kalshi_watch_sort: str | None = None,
        kalshi_watch_desc: int = 1,
        kalshi_hide_uncopyable: int = 0,
        kalshi_hide_net_neg: int = 0,
        kalshi_selected_sort: str = "",
        kalshi_selected_desc: int = 1,
        kalshi_sel_hide_uncopyable: int = 0,
        kalshi_sel_hide_net_neg: int = 0,
    ):
        return await _render_pm_partial(
            request, division=None,
            pm_watch_sort=pm_watch_sort, pm_watch_desc=bool(pm_watch_desc),
            kalshi_watch_sort=kalshi_watch_sort, kalshi_watch_desc=bool(kalshi_watch_desc),
            kalshi_hide_uncopyable=bool(kalshi_hide_uncopyable),
            kalshi_hide_net_neg=bool(kalshi_hide_net_neg),
            kalshi_selected_sort=kalshi_selected_sort or None,
            kalshi_selected_desc=bool(kalshi_selected_desc),
            kalshi_sel_hide_uncopyable=bool(kalshi_sel_hide_uncopyable),
            kalshi_sel_hide_net_neg=bool(kalshi_sel_hide_net_neg),
        )

    @app.get("/partials/prediction-markets/{division}", response_class=HTMLResponse)
    async def prediction_markets_partial_one(
        request: Request, division: str,
        pm_watch_sort: str | None = None,
        pm_watch_desc: int = 1,
        kalshi_watch_sort: str | None = None,
        kalshi_watch_desc: int = 1,
        kalshi_hide_uncopyable: int = 0,
        kalshi_hide_net_neg: int = 0,
        kalshi_selected_sort: str = "",
        kalshi_selected_desc: int = 1,
        kalshi_sel_hide_uncopyable: int = 0,
        kalshi_sel_hide_net_neg: int = 0,
        wr_mode: str = "live",
    ):
        return await _render_pm_partial(
            request, division=division,
            pm_watch_sort=pm_watch_sort, pm_watch_desc=bool(pm_watch_desc),
            kalshi_watch_sort=kalshi_watch_sort, kalshi_watch_desc=bool(kalshi_watch_desc),
            kalshi_hide_uncopyable=bool(kalshi_hide_uncopyable),
            kalshi_hide_net_neg=bool(kalshi_hide_net_neg),
            kalshi_selected_sort=kalshi_selected_sort or None,
            kalshi_selected_desc=bool(kalshi_selected_desc),
            kalshi_sel_hide_uncopyable=bool(kalshi_sel_hide_uncopyable),
            kalshi_sel_hide_net_neg=bool(kalshi_sel_hide_net_neg),
            wr_mode=wr_mode,
        )

    async def _render_pm_partial(
        request: Request, division: str | None,
        *,
        pm_watch_sort: str | None = None,
        pm_watch_desc: bool = True,
        kalshi_watch_sort: str | None = None,
        kalshi_watch_desc: bool = True,
        kalshi_hide_uncopyable: bool = False,
        kalshi_hide_net_neg: bool = False,
        kalshi_selected_sort: str | None = None,
        kalshi_selected_desc: bool = True,
        kalshi_sel_hide_uncopyable: bool = False,
        kalshi_sel_hide_net_neg: bool = False,
        wr_mode: str = "live",
    ):
        # Paper/Live/All stats scope — whitelist; anything else → 'live'.
        wr_mode = wr_mode if wr_mode in ("all", "paper", "live") else "live"
        view = await data.build_prediction_market_view(
            deps, division,
            pm_watch_sort=pm_watch_sort, pm_watch_desc=pm_watch_desc,
            kalshi_watch_sort=kalshi_watch_sort, kalshi_watch_desc=kalshi_watch_desc,
            kalshi_hide_uncopyable=kalshi_hide_uncopyable,
            kalshi_hide_net_neg=kalshi_hide_net_neg,
            kalshi_selected_sort=kalshi_selected_sort,
            kalshi_selected_desc=kalshi_selected_desc,
            kalshi_sel_hide_uncopyable=kalshi_sel_hide_uncopyable,
            kalshi_sel_hide_net_neg=kalshi_sel_hide_net_neg,
            wr_mode=wr_mode,
        )
        if view is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown prediction-market division: {division}",
            )
        return templates.TemplateResponse(
            request, "partials/pm_dashboard_body.html",
            {"view": view},
        )

    # ── Division drill-down (Phase 2) ────────────────────────────────────

    @app.get("/division/{slug}", response_class=HTMLResponse)
    async def division_view(request: Request, slug: str):
        # Need the corp-wide snapshot so the base template's header/footer
        # (mode badge, broker dots, regime/VIX) still has data to display.
        cmd_snap, view = await asyncio.gather(
            data.build_command_center(deps),
            data.build_division_view(deps, slug),
        )
        if view is None:
            raise HTTPException(status_code=404, detail=f"Unknown division: {slug}")
        return templates.TemplateResponse(
            request, "division.html",
            {"snap": cmd_snap, "view": view},
        )

    @app.get("/division/{slug}/llm-analysis", response_class=HTMLResponse)
    async def division_llm_analysis(slug: str):
        """Lazy HTMX-loaded LLM expert analysis for a division.

        Returns rendered HTML (not a full template — meant to swap into a
        container on the division page). PMCC-only for now; other divisions
        get a "no analysis available" message.
        """
        # Cache hit
        with _LLM_LOCK:
            entry = _LLM_CACHE.get(slug)
            if entry is not None:
                html, ts = entry
                if time.time() - ts < _LLM_CACHE_TTL_SEC:
                    return HTMLResponse(html)

        # Only PMCC currently has an analysis generator
        if slug != "robinhood_pmcc" or deps.pmcc_agent is None:
            html = _llm_placeholder_html(slug)
            with _LLM_LOCK:
                _LLM_CACHE[slug] = (html, time.time())
            return HTMLResponse(html)

        broker = deps.data_exec.brokers.get(slug)
        if broker is None:
            return HTMLResponse(_llm_placeholder_html(slug))

        # Pull regime once for the analysis context
        try:
            reading = deps.trend_agent.read() if deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        try:
            md = await deps.pmcc_agent.analyze_portfolio(broker, regime=regime)
            html = _render_markdown(md)
        except Exception as e:
            log.warning("LLM analysis for %s failed: %s", slug, e)
            html = (
                '<div class="text-muted text-sm italic">'
                f'Analysis unavailable: {str(e)[:120]}'
                '</div>'
            )

        with _LLM_LOCK:
            _LLM_CACHE[slug] = (html, time.time())
        return HTMLResponse(html)

    @app.get("/partials/division-equity-curve/{slug}")
    async def partial_division_equity(slug: str):
        view = await data.build_division_view(deps, slug)
        if view is None:
            raise HTTPException(status_code=404)
        return JSONResponse({"points": view.equity_curve})

    @app.get("/partials/polymarket-analysis/{event_id}", response_class=HTMLResponse)
    async def partial_polymarket_analysis(event_id: int, request: Request):
        """Render the full LLM analysis snapshot for one Polymarket
        audit event. Loaded into the right rail when the user clicks
        "Show analysis →" on any polymarket activity row.

        Source: audit_event row payload (point-in-time; never recomputed).
        Contains the LLM's full reasoning text + key unknowns + the
        decision-time probability snapshot. Critical for fine-tuning
        + post-mortem of bad calls.
        """
        import json as _json
        import sqlite3
        path = deps.db_url.replace("sqlite:///", "")
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, ts, actor, kind, payload_json FROM audit_event WHERE id = ?",
                    (int(event_id),),
                ).fetchone()
        except Exception as e:
            return HTMLResponse(
                f'<div class="text-loss text-sm">Error loading event {event_id}: {e}</div>',
                status_code=500,
            )
        if row is None:
            return HTMLResponse(
                f'<div class="text-muted text-sm italic text-center py-8">Audit event {event_id} not found.</div>',
                status_code=404,
            )
        if row["actor"] != "polymarket_arbitrage":
            return HTMLResponse(
                f'<div class="text-muted text-sm italic text-center py-8">'
                f'Event {event_id} is not a Polymarket event (actor={row["actor"]}).'
                f'</div>',
                status_code=400,
            )
        try:
            payload = _json.loads(row["payload_json"] or "{}")
        except (_json.JSONDecodeError, ValueError):
            payload = {}

        # Flatten the audit-event row + payload into the template's
        # event dict shape. Keep field names consistent with the data
        # layer's `_query_division_activity` polymarket sub-dict so the
        # template can be reused if we ever want to render it inline
        # in the activity rail too.
        from datetime import datetime, timezone
        ts = row["ts"]
        try:
            ts_dt = datetime.fromisoformat(ts)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            ts_short = format_et_full(ts_dt)
        except (TypeError, ValueError):
            ts_short = ts

        skipped = (
            row["kind"] == "polymarket_llm_probability_called"
            and payload.get("would_emit") is False
        )
        event = {
            "id": row["id"],
            "ts": ts,
            "ts_short": ts_short,
            "kind": row["kind"],
            "skipped": skipped,
            "market_slug": payload.get("market_slug") or payload.get("slug"),
            "market_question": payload.get("market_question") or payload.get("question"),
            "category": payload.get("category"),
            "series": payload.get("series"),
            "outcome": payload.get("outcome"),
            "implied_prob": payload.get("implied_prob_at_entry") or payload.get("implied_prob_yes"),
            "llm_prob": payload.get("llm_prob_estimate") or payload.get("llm_prob_yes"),
            "llm_confidence": payload.get("llm_confidence"),
            "llm_reasoning": payload.get("llm_reasoning"),
            "key_unknowns": payload.get("key_unknowns") or [],
            "divergence_pct": payload.get("divergence_pct"),
            "min_divergence_pct": payload.get("min_divergence_pct"),
            "qty": payload.get("qty"),
            "limit_price": payload.get("limit_price"),
            "risk_verdict": payload.get("risk_verdict"),
            "risk_reason": payload.get("risk_reason"),
            "resolves_at": payload.get("resolves_at"),
            "condition_id": payload.get("condition_id"),
        }
        return templates.TemplateResponse(
            request, "partials/polymarket_analysis.html", {"event": event},
        )

    @app.get("/partials/kalshi-llm-analysis/{event_id}", response_class=HTMLResponse)
    async def partial_kalshi_llm_analysis(event_id: int, request: Request):
        """Phase K6.1 — Kalshi LLM analysis right-rail. Loaded via HTMX
        from "Show analysis →" on any kalshi_llm_arbitrage activity row.

        Same shape as `partial_polymarket_analysis`. Reuses the polymarket
        analysis template (field names align by design — `event_title`
        plays polymarket's `market_question` role, `ticker` plays the
        `market_slug` role).
        """
        import json as _json
        import sqlite3
        path = deps.db_url.replace("sqlite:///", "")
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, ts, actor, kind, payload_json FROM audit_event WHERE id = ?",
                    (int(event_id),),
                ).fetchone()
        except Exception as e:
            return HTMLResponse(
                f'<div class="text-loss text-sm">Error loading event {event_id}: {e}</div>',
                status_code=500,
            )
        if row is None:
            return HTMLResponse(
                f'<div class="text-muted text-sm italic text-center py-8">Audit event {event_id} not found.</div>',
                status_code=404,
            )
        if row["actor"] != "kalshi_llm_arbitrage":
            return HTMLResponse(
                f'<div class="text-muted text-sm italic text-center py-8">'
                f'Event {event_id} is not a Kalshi LLM event (actor={row["actor"]}).'
                f'</div>',
                status_code=400,
            )
        try:
            payload = _json.loads(row["payload_json"] or "{}")
        except (_json.JSONDecodeError, ValueError):
            payload = {}

        from datetime import datetime, timezone
        ts = row["ts"]
        try:
            ts_dt = datetime.fromisoformat(ts)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            ts_short = format_et_full(ts_dt)
        except (TypeError, ValueError):
            ts_short = ts

        skipped = (
            row["kind"] == "kalshi_llm_probability_called"
            and payload.get("would_emit") is False
        )

        # Map Kalshi field names onto the polymarket template's expected
        # field names — same template renders for both venues.
        event = {
            "id": row["id"],
            "ts": ts,
            "ts_short": ts_short,
            "kind": row["kind"],
            "skipped": skipped,
            "market_slug": payload.get("ticker"),  # polymarket: market_slug → kalshi: ticker
            "market_question": payload.get("event_title"),
            "category": payload.get("category"),
            "series": payload.get("subtitle"),  # subtitle as the secondary tag
            "outcome": payload.get("outcome"),
            "implied_prob": payload.get("implied_prob_at_entry") or payload.get("implied_prob_yes"),
            "llm_prob": payload.get("llm_prob_estimate") or payload.get("llm_prob_yes"),
            "llm_confidence": payload.get("llm_confidence"),
            "llm_reasoning": payload.get("llm_reasoning"),
            "key_unknowns": payload.get("key_unknowns") or [],
            "divergence_pct": payload.get("divergence_pct"),
            "min_divergence_pct": payload.get("min_divergence_pct"),
            "qty": payload.get("qty"),
            "limit_price": payload.get("limit_price"),
            "risk_verdict": payload.get("risk_verdict"),
            "risk_reason": payload.get("risk_reason"),
            "resolves_at": payload.get("expires_at"),
            "condition_id": payload.get("event_ticker"),  # event_ticker is the kalshi event id
        }
        return templates.TemplateResponse(
            request, "partials/polymarket_analysis.html", {"event": event},
        )

    @app.get("/partials/kalshi-analysis/{event_id}", response_class=HTMLResponse)
    async def partial_kalshi_analysis(event_id: int, request: Request):
        """Render full payload + raw audit JSON for one Kalshi audit event.
        Loaded into the right rail when the user clicks "Show details →"
        on any Kalshi activity row.

        Source: audit_event row payload (point-in-time; never recomputed).
        Different event kinds have different rich content (scan summaries
        show counts + thresholds; would_have_placed shows ticker + edge +
        leg + set/pair linkage; risk_rejected shows the reason).
        """
        import json as _json
        import sqlite3
        path = deps.db_url.replace("sqlite:///", "")
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, ts, actor, kind, payload_json FROM audit_event WHERE id = ?",
                    (int(event_id),),
                ).fetchone()
        except Exception as e:
            return HTMLResponse(
                f'<div class="text-loss text-sm">Error loading event {event_id}: {e}</div>',
                status_code=500,
            )
        if row is None:
            return HTMLResponse(
                f'<div class="text-muted text-sm italic text-center py-8">Audit event {event_id} not found.</div>',
                status_code=404,
            )
        if row["actor"] not in ("kalshi_tail_price_arb", "kalshi_temporal_bucket_arb"):
            return HTMLResponse(
                f'<div class="text-muted text-sm italic text-center py-8">'
                f'Event {event_id} is not a Kalshi event (actor={row["actor"]}).'
                f'</div>',
                status_code=400,
            )
        try:
            payload = _json.loads(row["payload_json"] or "{}")
        except (_json.JSONDecodeError, ValueError):
            payload = {}

        from datetime import datetime, timezone
        ts = row["ts"]
        try:
            ts_dt = datetime.fromisoformat(ts)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            ts_short = format_et_full(ts_dt)
        except (TypeError, ValueError):
            ts_short = ts

        # Pretty-print the full payload as JSON for the raw section.
        try:
            payload_pretty = _json.dumps(payload, indent=2, sort_keys=True)
        except Exception:
            payload_pretty = str(payload)

        event = {
            "id": row["id"],
            "ts": ts,
            "ts_short": ts_short,
            "actor": row["actor"],
            "kind": row["kind"],
            "payload": payload,
            "payload_pretty": payload_pretty,
        }
        return templates.TemplateResponse(
            request, "partials/kalshi_analysis.html", {"event": event},
        )

    @app.get("/partials/donchian-chart/{slug}")
    async def partial_donchian_chart(slug: str):
        """OHLCV + Donchian band overlay + fill markers for the
        coinbase_spot division chart. Returns 404 for any other slug —
        the chart tile is currently single-strategy."""
        if slug != "coinbase_spot":
            raise HTTPException(status_code=404)
        payload = await data.build_donchian_chart_data(deps.db_url)
        if payload is None:
            return JSONResponse({"empty": True})
        return JSONResponse(payload)

    # Per-pair LLM analysis cache: (slug, symbol) → (html, ts).
    _pair_cache: dict[tuple[str, str], tuple[str, float]] = {}
    _PAIR_CACHE_TTL_SEC = 300

    @app.get("/division/{slug}/pair-analysis/{symbol}", response_class=HTMLResponse)
    async def division_pair_analysis(slug: str, symbol: str, request: Request):
        """Per-pair expert analysis — clicked-row → right-panel content.

        Calls PMCCAgent on a single underlying (vs the full-portfolio sweep
        of /llm-analysis). Renders structured HTML directly rather than
        markdown so the panel can layout the action / confidence / warnings
        cleanly. 5-min cache per (slug, symbol).

        Honors deferrals — if user clicked "Defer 24h", returns a deferred
        panel instead of running fresh analysis. Override with ?force=1.
        """
        sym = symbol.upper()
        force = request.query_params.get("force") == "1"

        # Deferral check — short-circuit before cache + LLM
        if not force:
            expires_at = _deferred_until(deps.logger_agent, slug, sym)
            if expires_at is not None:
                return HTMLResponse(_render_deferred_panel(
                    slug, sym, fresh=False, expires_at=expires_at,
                ))

        # Unified-record reader: the PMCC Expert panel reflects the ONE
        # timestamped decision the tile shows (scan or manual expert). The LLM
        # only re-runs on an explicit Re-analyze (?force=1) below — keeping the
        # tile and panel in lockstep and cutting per-click Anthropic calls.
        if slug == "robinhood_pmcc" and deps.pmcc_agent is not None and not force:
            return HTMLResponse(await _render_pmcc_record_panel(deps, slug, sym))

        key = (slug, sym)
        with _LLM_LOCK:
            entry = _pair_cache.get(key)
            if entry is not None and not force:   # force=1 (Re-analyze) => fresh
                html, ts = entry
                if time.time() - ts < _PAIR_CACHE_TTL_SEC:
                    return HTMLResponse(html)

        # Robinhood IRA — deterministic covered-call analysis. Produces
        # the same PMCCAnalysis + TradeRecommendation shapes PMCC does
        # and renders via the shared `_render_pair_analysis` so the
        # panel matches PMCC visually (action header + confidence +
        # warnings + concrete trade legs with broker-fetched prices).
        # `show_execute_button=False` because IRA has no automated
        # execution wired — user executes the recommended trade
        # manually in Robinhood.
        if slug == "robinhood_ira":
            view = await data.build_division_view(deps, slug)
            if view is None or view.ira_view is None:
                return HTMLResponse(_pair_unavailable_html(sym, "IRA view unavailable."))
            cc = next(
                (c for c in view.ira_view.get("covered_calls", [])
                 if c.underlying.upper() == sym),
                None,
            )
            if cc is None:
                return HTMLResponse(_pair_unavailable_html(sym, "No covered call on this symbol."))
            broker_for_chain = (
                deps.data_exec.brokers.get(slug) if deps.data_exec is not None else None
            )
            try:
                analysis, recommendation = await _analyze_ira_covered_call(
                    cc, broker_for_chain, deps,
                )
            except Exception as e:
                log.warning("ira analysis(%s, %s) raised: %s", slug, sym, e, exc_info=True)
                return HTMLResponse(_pair_unavailable_html(sym, str(e)[:160]))
            html = _render_pair_analysis(
                analysis, recommendation=recommendation,
                slug=slug, symbol=sym,
                show_execute_button=False,
            )
            with _LLM_LOCK:
                _pair_cache[key] = (html, time.time())
            return HTMLResponse(html)

        if slug != "robinhood_pmcc" or deps.pmcc_agent is None:
            return HTMLResponse(_pair_unavailable_html(sym, "Per-pair analysis is wired for Robinhood PMCC only."))

        broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        if broker is None:
            return HTMLResponse(_pair_unavailable_html(sym, "Broker not registered."))

        try:
            reading = deps.trend_agent.read() if deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        try:
            analysis = await deps.pmcc_agent.analyze_symbol(broker, sym, regime=regime)
        except Exception as e:
            log.warning("pair_analysis(%s, %s) raised: %s", slug, sym, e)
            return HTMLResponse(_pair_unavailable_html(sym, str(e)[:160]))

        if analysis is None:
            return HTMLResponse(_pair_unavailable_html(sym, "No matching open position found."))

        # Manual Re-analyze -> persist a fresh source='expert' verdict to the
        # unified decision record (expert ALWAYS overwrites) so the tile updates
        # immediately and stays in sync with this panel. Best-effort.
        try:
            from trading_corp.agents.divisions import _pmcc_status
            from datetime import datetime as _dt, timezone as _tz
            _pmcc_status.record_pmcc_decision(
                sym, status=analysis.action, source="expert",
                computed_at=_dt.now(_tz.utc).isoformat(), db_url=deps.db_url,
                urgency=analysis.urgency, confidence=analysis.confidence,
                summary=analysis.summary, rationale=analysis.rationale,
                warnings=analysis.warnings,
                target_delta_low=getattr(analysis, "target_delta_low", None),
                target_delta_high=getattr(analysis, "target_delta_high", None),
                target_dte=getattr(analysis, "target_dte", None),
            )
        except Exception as e:
            log.warning("pmcc expert record write(%s) failed: %s", sym, e)

        # Build the concrete combo ONCE (preview=True: a Re-analyze render is NOT a
        # dispatch attempt → no ABORTED/earnings exec-alert or audit; invariant
        # 2026-07-30). Derive the recommendation, the live debit/credit/net consent
        # estimate, AND the dispatch stash from the SAME order list, so the strike
        # shown, the estimate shown, and the strike fired are guaranteed identical.
        # Failure here is non-fatal — we still want the textual analysis.
        rec = None
        roll_extras = None
        preview_token = None
        try:
            orders = await deps.pmcc_agent.propose_orders_for_pair(
                broker, sym, analysis, preview=True)
            if orders:
                rec = await deps.pmcc_agent.build_trade_recommendation(
                    broker, sym, analysis, prebuilt_orders=orders)
                import types as _types
                from trading_corp.web.pmcc_roll_card import build_pmcc_roll_card_extras
                from trading_corp.web import pmcc_preview
                roll_extras = await build_pmcc_roll_card_extras(
                    _types.SimpleNamespace(orders=orders, underlying=sym),
                    broker, deps.pmcc_agent,
                )
                # Carry the exact previewed combo forward to the Approve dispatch.
                preview_token = pmcc_preview.stash_preview(
                    slug, sym, orders, action=analysis.action,
                )
        except Exception as e:
            log.warning("pmcc consent build(%s) raised: %s", sym, e)

        html = _render_pair_analysis(
            analysis, recommendation=rec, slug=slug, symbol=sym,
            status_banner=_pmcc_status_banner(slug, sym, state="fresh",
                                              age_h=0.0, source="expert"),
            roll_extras=roll_extras, preview_token=preview_token,
        )
        with _LLM_LOCK:
            _pair_cache[key] = (html, time.time())
        # Refresh the left-rail tile badge out-of-band (FIX 2) from the record
        # this Re-analyze just wrote (line ~950), so tile + panel match with no
        # full page reload.
        return HTMLResponse(html + _pmcc_tile_badge_oob(templates, deps, sym))

    @app.post("/division/{slug}/pair/{symbol}/refresh-pricing", response_class=HTMLResponse)
    async def refresh_pricing(slug: str, symbol: str):
        """LLM-FREE pricing refresh (P1) — re-price the roll from the STORED judgment's
        δ band (NO Anthropic call), rewrite the consent stash from the same pull, and
        re-render the panel. The judgment banner is unchanged (this doesn't re-judge)."""
        sym = symbol.upper()
        if slug != "robinhood_pmcc" or deps.pmcc_agent is None:
            return HTMLResponse(_pair_unavailable_html(sym, "Refresh pricing is wired for Robinhood PMCC only."))
        broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        if broker is None:
            return HTMLResponse(_pair_unavailable_html(sym, "Broker not registered."))
        from trading_corp.web import pmcc_pricing
        from trading_corp.agents.divisions import _pmcc_status
        from datetime import datetime as _dt, timezone as _tz
        rec = _pmcc_status.load_decision(sym, db_url=deps.db_url)
        if not rec:
            # No stored judgment to price against — send them to Re-analyze.
            return HTMLResponse(await _render_pmcc_record_panel(deps, slug, sym))
        pr = await pmcc_pricing.price_and_stash(deps.pmcc_agent, broker, slug, sym, deps.db_url)
        analysis = _pmcc_analysis_from_record(rec)
        roll_extras = None
        if pr.estimate is not None or pr.estimate_reason is not None or pr.earnings is not None:
            roll_extras = {"earnings": pr.earnings, "estimate": pr.estimate,
                           "estimate_reason": pr.estimate_reason}
        cfg = (getattr(deps.pmcc_agent, "_cfg", {}) or {}).get("tile_status", {}) or {}
        stale_h = float(cfg.get("staleness_hours", _pmcc_status.DEFAULT_STALENESS_HOURS))
        _now = _dt.now(_tz.utc)
        _state = _pmcc_status.classify_freshness(rec, _now, stale_h)
        html = _render_pair_analysis(
            analysis, recommendation=None, slug=slug, symbol=sym,
            status_banner=_pmcc_status_banner(
                slug, sym, state=_state,
                age_h=_pmcc_status.age_hours(rec, _now), source=rec.get("source")),
            roll_extras=roll_extras, preview_token=pr.stash_token,
        )
        return HTMLResponse(html + _pmcc_tile_badge_oob(templates, deps, sym))

    @app.get("/division/{slug}/pmcc-pricing", response_class=HTMLResponse)
    async def division_pmcc_pricing(slug: str):
        """45s interval pricing refresh (P1) — re-price all cached PMCC tiles (RH-only,
        NO LLM, market-hours-gated inside refresh_division → no pull off-hours) and
        rewrite their stashes, returning per-symbol hx-swap-oob spans that update each
        tile's pricing chip in place. Empty response when off-hours-idle or nothing is
        cached (nothing to swap; the interval keeps firing cheaply)."""
        if slug != "robinhood_pmcc" or deps.pmcc_agent is None:
            return HTMLResponse("")
        broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        if broker is None:
            return HTMLResponse("")
        from trading_corp.web import pmcc_pricing
        syms = pmcc_pricing.symbols_for(slug)
        if not syms:
            return HTMLResponse("")
        try:
            await pmcc_pricing.refresh_division(
                deps.pmcc_agent, broker, slug, syms, deps.db_url)
        except Exception as e:      # noqa: BLE001 — an OOB refresh must never 500
            log.warning("division_pmcc_pricing(%s) refresh failed: %s", slug, e)
        parts = []
        for s in syms:
            try:
                pricing = pmcc_pricing.tile_pricing_view(pmcc_pricing.cached(slug, s))
                inner = templates.get_template("partials/_pmcc_pricing.html").render(pricing=pricing)
                parts.append(
                    f'<span id="pmcc-pricing-{s}" hx-swap-oob="true" class="contents">'
                    f'{inner}</span>')
            except Exception as e:  # noqa: BLE001
                log.warning("division_pmcc_pricing(%s) render %s failed: %s", slug, s, e)
        return HTMLResponse("".join(parts))

    @app.post("/division/{slug}/pair/{symbol}/defer", response_class=HTMLResponse)
    async def defer_pair(slug: str, symbol: str):
        """Record a 24h deferral on this pair's recommendation.

        While deferred, /pair-analysis returns a 'deferred' state instead of
        re-running the LLM. The user can clear it via /resume.
        """
        sym = symbol.upper()
        if deps.logger_agent is not None:
            deps.logger_agent.log_event(
                actor="board", kind="pair_deferred",
                payload={
                    "slug": slug, "symbol": sym,
                    "ttl_hours": _DEFER_TTL_HOURS,
                    "via": "web_button",
                },
            )
        with _LLM_LOCK:
            _pair_cache.pop((slug, sym), None)
        # Compute expiration for the freshly-stamped panel so user sees the deadline
        expires_at = datetime.now(timezone.utc) + timedelta(hours=_DEFER_TTL_HOURS)
        return HTMLResponse(_render_deferred_panel(
            slug, sym, fresh=True, expires_at=expires_at,
        ))

    @app.post("/division/{slug}/pair/{symbol}/resume", response_class=HTMLResponse)
    async def resume_pair(slug: str, symbol: str, request: Request):
        """Clear an active deferral and return fresh analysis.

        Logs a `pair_resumed` event that supersedes any earlier `pair_deferred`
        within the TTL window. Subsequent /pair-analysis calls behave normally
        (no deferred state) until the user defers again.
        """
        sym = symbol.upper()
        if deps.logger_agent is not None:
            deps.logger_agent.log_event(
                actor="board", kind="pair_resumed",
                payload={"slug": slug, "symbol": sym, "via": "web_button"},
            )
        # Drop the LLM cache so the next analysis is fresh
        with _LLM_LOCK:
            _pair_cache.pop((slug, sym), None)
        # Re-run the analysis pipeline now and return its panel directly
        return await division_pair_analysis(slug, symbol, request)

    @app.post("/division/{slug}/pair/{symbol}/execute", response_class=HTMLResponse)
    async def execute_pair_orders(slug: str, symbol: str, request: Request):
        """Approve & execute the LLM-recommended action for one pair.

        Translates the cached analysis into ProposedOrders, runs each through
        the Risk Agent (deterministic caps) and — if approved — places via
        DataExecAgent. In PAPER mode this hits the PaperExecutionBroker so
        no live orders go out; in LIVE mode it routes to the real broker.

        This bypasses the LangGraph Telegram-interrupt path entirely — the
        button click IS the Board approval. The flow is fully synchronous
        for clean web UX (loading spinner → result inline).
        """
        sym = symbol.upper()
        # AUTONOMY SEAM (P3b, source-parameterized): this handler is the HITL panel
        # gate — the operator's click IS the approval, so source='board'. The dispatch
        # cores below (single-leg place / combo place_combo) thread `source` and hard-
        # code NO HITL, so a future autonomous loop can call the same cores with
        # source='auto' after applying _check_auto_execute's caps (max_close_debit /
        # VIX — RETAINED in ceo_graph as the AUTO-path safety layer, DORMANT on this
        # HITL path). Flipping auto_execute:true + moving the trigger from click to loop
        # is then the only change — no execution re-plumbing.
        source = "board"
        # Dashboard Approve = USER-INITIATED: mark the origin so exec-alerts
        # (build-abort + place_combo outcome) BYPASS dedupe — every click gets a
        # guaranteed ping even if identical to a prior one in-window.
        try:
            from trading_corp.comms.exec_alert import mark_user_origin
            mark_user_origin()
        except Exception:
            pass
        # Consent token from the Approve form (present when the operator approved a
        # Re-analyze render): the previewed combo's id + shape fingerprint.
        preview_id = None
        approved_fp = None
        try:
            _form = await request.form()
            preview_id = _form.get("preview_id") or None
            approved_fp = _form.get("fingerprint") or None
        except Exception:
            pass
        if slug != "robinhood_pmcc" or deps.pmcc_agent is None:
            return HTMLResponse(_exec_error_html(sym, "Approve & Execute is wired for Robinhood PMCC only."))
        broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        if broker is None:
            return HTMLResponse(_exec_error_html(sym, "Broker not registered."))

        # Fresh regime read (don't reuse possibly-stale cached value)
        try:
            reading = deps.trend_agent.read() if deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        # ── Consent + display source (FORK 2, 2026-07-30) ────────────────────
        # The Approve form carries the previewed combo's id + shape fingerprint.
        #   HIT  → fire those exact legs AND synthesize the dispatch view from the
        #          stash: NO analyze_symbol (LLM) call, so the Approve click carries
        #          no LLM latency and the view can't contradict the approved rec.
        #          The dispatch reprice still re-quotes the PRICE (strike/legs fixed).
        #   MISS → a genuine from-scratch dispatch: regenerate the LLM analysis
        #          (needed to rebuild the combo) and rebuild, fingerprint-guarding
        #          against a drifted contract.
        # `regime` above is a cheap trend read (not the LLM) and is kept for risk eval.
        from trading_corp.web import pmcc_preview
        orders = None
        analysis = None
        hit = pmcc_preview.load_preview(slug, sym, preview_id, approved_fp)
        if hit is not None:
            orders = hit.orders
            analysis = _synth_analysis_from_stash(hit.action)   # display only; no LLM
            # A clicked Approve IS a genuine dispatch attempt — re-check the earnings
            # gate here (the stash is up to 15 min old). Blocked → bail + ABORTED
            # alert (fires because this IS a dispatch), position untouched.
            try:
                _earn = deps.pmcc_agent.earnings_card_state(sym)
            except Exception:
                _earn = None
            if _earn is not None and _earn.get("kind") == "blocked":
                try:
                    from trading_corp.comms.exec_alert import ExecOutcome, emit_exec_alert
                    emit_exec_alert(ExecOutcome(
                        tier="ABORTED", symbol=sym, strategy="robinhood_pmcc",
                        reason=("earnings within buffer at approval — roll not placed; "
                                "let the short expire, roll after the print"),
                        position_changed=False,
                    ))
                except Exception:
                    pass
                deps.logger_agent.log_event(
                    actor="board", kind="pmcc_consent_earnings_block",
                    payload={"symbol": sym, "via": "web_button",
                             "date": _earn.get("date")},
                )
                return HTMLResponse(_exec_abort_html(
                    sym, analysis, {"reason": "earnings within buffer at approval"}))
        if orders is None:
            try:
                analysis = await deps.pmcc_agent.analyze_symbol(broker, sym, regime=regime)
            except Exception as e:
                log.warning("execute: analyze_symbol(%s) raised: %s", sym, e)
            if analysis is None:
                return HTMLResponse(_exec_error_html(sym, "Could not regenerate analysis."))
            try:
                orders = await deps.pmcc_agent.propose_orders_for_pair(broker, sym, analysis)
            except Exception as e:
                log.warning("execute: propose_orders_for_pair(%s) raised: %s", sym, e)
                return HTMLResponse(_exec_error_html(sym, str(e)[:160]))
            # If the operator approved a SPECIFIC combo (fingerprint present) but the
            # live rebuild differs (chain moved, stale page), do NOT silently fire a
            # different contract — bail + re-surface with the fresh estimate.
            if approved_fp and orders and pmcc_preview.fingerprint(orders) != approved_fp:
                deps.logger_agent.log_event(
                    actor="board", kind="pmcc_consent_fingerprint_mismatch",
                    payload={"symbol": sym, "via": "web_button",
                             "approved_fp": approved_fp,
                             "rebuilt_fp": pmcc_preview.fingerprint(orders)},
                )
                return HTMLResponse(_exec_consent_mismatch_html(sym))

        if not orders:
            # A roll/close action that yields NO orders is a B4 atomic abort
            # (e.g. no liquid weekly to roll into) — NOT "no action needed".
            # Surface the actual reason from the agent's in-memory diag (set by
            # `_find_best_weekly`/`_find_best_leap` on abort) instead of the
            # misleading "action requires no orders" message.
            act = (analysis.action or "").lower()
            if act not in ("hold", "watch") and deps.pmcc_agent is not None:
                diag = (getattr(deps.pmcc_agent, "_last_weekly_diag", None)
                        or getattr(deps.pmcc_agent, "_last_leap_diag", None))
                return HTMLResponse(_exec_abort_html(sym, analysis, diag))
            return HTMLResponse(_exec_no_action_html(sym, analysis))

        # Account snapshot for the Risk Agent
        try:
            snap = await broker.snapshot()
            account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception:
            account_equity = 0.0
        account = AccountState(
            account=getattr(snap, "account", slug) if 'snap' in dir() else slug,
            equity=account_equity or 100_000.0,
            peak_equity=account_equity or 100_000.0,
        )
        strat_state = StrategyState.from_persistence("robinhood_pmcc", db_url=deps.db_url)

        # execution_mode reflects the REAL registered broker, stamped BEFORE any
        # dispatch so the persisted proposed_order row is accurate even when
        # placement raises (fixes the stale 'paper' label on live-rejected rows).
        _live_mode = "paper" if getattr(broker, "paper", True) else "live"

        # ── Dispatch: partition into combo groups + single-leg actions ────────
        # A PMCC roll (and any combo-tagged action) is ONE multi-leg SPREAD via
        # data_exec.place_combo -> place_multi_leg (a single all-or-nothing POST);
        # it never legs in. The single-leg loop is for genuine single-leg actions
        # ONLY — a combo leg sent down it trips _place_option_order's fail-closed
        # guard, which STAYS as the backstop. partition_combo_orders RAISES on a
        # malformed batch (a lone combo leg, or >1 untagged option leg) → place
        # NOTHING (the interim backstop for close_all / Scout OPEN until tagged).
        from trading_corp.agents.strategies._pmcc_combo import (
            partition_combo_orders, snapshot_combo_for_consent,
            assess_combo_reprice_consent,
        )
        try:
            combo_groups, singles = partition_combo_orders(orders)
        except ValueError as e:
            for o in orders:
                o.status = "risk_rejected"
                o.execution_mode = _live_mode
                deps.logger_agent.log_proposed_order(o)
            deps.logger_agent.log_event(
                actor="data_exec", kind="combo_partition_refused",
                payload={"symbol": sym, "reason": str(e), "via": "web_button"},
            )
            return HTMLResponse(_render_execute_results(sym, analysis, [
                {"order": o, "outcome": "execute_error",
                 "detail": f"Refused (multi-leg safety): {str(e)[:150]}"}
                for o in orders
            ]))

        results: list[dict] = []

        # Each combo group → risk all-or-nothing (a `reject` aborts the whole
        # combo; `resize` ignored, can't resize one leg), re-price from live
        # quotes at dispatch (proposal-time mid is stale/non-marketable by
        # approval), then place as ONE spread.
        for _cid, _legs in combo_groups.items():
            _rej = None
            for order in _legs:
                order.execution_mode = _live_mode
                v = deps.risk_agent.evaluate(order, account, strat_state, regime, None)
                order.risk_reason = v.reason
                if v.verdict == "reject":
                    _rej = (order, v)
                    break
            if _rej is not None:
                order, v = _rej
                for o in _legs:
                    o.status = "risk_rejected"
                    deps.logger_agent.log_proposed_order(o)
                deps.logger_agent.log_event(
                    actor="risk", kind="combo_rejected_by_risk",
                    payload={"combo_id": _cid, "symbol": sym, "rejected_leg": order.id,
                             "reason": v.reason, "via": "web_button"},
                )
                results.extend({
                    "order": o, "outcome": "risk_rejected",
                    "detail": (f"Combo aborted (all-or-nothing): leg "
                               f"{(order.extra or {}).get('action')} rejected — {v.reason}")}
                    for o in _legs)
                continue
            _consent_snap = snapshot_combo_for_consent(_legs)
            try:
                await deps.pmcc_agent.reprice_combo(_legs, broker)
            except Exception as e:
                log.warning("execute_pair: reprice_combo(%s) failed: %s "
                            "— keeping proposal-time limit", _cid, e)
            # FINAL consent guard (P1, 2026-07-31): what fires must match what was
            # shown. Abort the combo if the dispatch reprice drifted adversely vs the
            # approved snapshot — sign flip, strike drift, credit collapse beyond
            # tolerance, or a stale/wide-quote hold — placing NOTHING and
            # re-surfacing for re-approval.
            _consent_ok, _consent_why = assess_combo_reprice_consent(
                _legs, _consent_snap,
                max_adverse_net_deviation=_REPRICE_MAX_ADVERSE_NET,
            )
            if not _consent_ok:
                for o in _legs:
                    o.status = "risk_rejected"
                    deps.logger_agent.log_proposed_order(o)
                deps.logger_agent.log_event(
                    actor="board", kind="pmcc_consent_reprice_drift",
                    payload={"combo_id": _cid, "symbol": sym,
                             "reason": _consent_why, "via": "web_button"},
                )
                results.extend({
                    "order": o, "outcome": "risk_rejected",
                    "detail": (f"Combo not placed — dispatch price drifted from what you "
                               f"approved ({_consent_why}). Re-price and approve again.")}
                    for o in _legs)
                continue
            for order in _legs:
                order.status = "board_approved"
                order.board_reason = f"approved via panel combo (source={source})"
                deps.logger_agent.log_proposed_order(order)
                deps.logger_agent.log_event(
                    actor="board", kind="board_approved",
                    payload={"order_id": order.id, "symbol": sym, "via": "web_button",
                             "qty": order.qty, "combo_id": _cid},
                )
            try:
                fills = await deps.data_exec.place_combo(_legs, division=slug)
            except Exception as e:
                log.warning("execute_pair: place_combo(%s) raised: %s", _cid, e)
                deps.logger_agent.log_event(
                    actor="data_exec", kind="execution_error",
                    payload={"symbol": sym, "combo_id": _cid, "error": str(e)},
                )
                results.extend({"order": o, "outcome": "execute_error",
                                "detail": str(e)[:160]} for o in _legs)
                continue
            if not fills:
                results.extend({
                    "order": o, "outcome": "execute_error",
                    "detail": "combo did not fill (limit not marketable) — nothing placed; no naked leg"}
                    for o in _legs)
                continue
            _fbi = {f.order_id: f for f in fills}
            for o in _legs:
                f = _fbi.get(o.id)
                results.append({"order": o, "outcome": "filled",
                                "fill_price": f.price if f else None,
                                "venue": f.venue if f else None})

        # ── single-leg actions (genuine single-leg only; combos handled above) ─
        for order in singles:
            order.execution_mode = _live_mode
            verdict = deps.risk_agent.evaluate(
                order, account, strat_state, regime, None,
            )
            order.risk_reason = verdict.reason
            is_option = bool((order.extra or {}).get("is_option", False))
            # Option safety: a "resize" on one leg of a paired trade
            # (e.g. PMCC roll = close + open) creates an asymmetric pair —
            # close 5, open 2 → leaves 3 LEAPs uncovered. Reject any option
            # resize so the strategy remains structurally intact. Operator
            # can override by editing risk caps if desired.
            if verdict.verdict == "resize" and is_option:
                order.status = "risk_rejected"
                deps.logger_agent.log_proposed_order(order)
                deps.logger_agent.log_event(
                    actor="risk", kind="risk_rejected",
                    payload={"order_id": order.id, "symbol": sym,
                             "reason": (
                                 f"option resize → reject (would break paired-roll integrity): "
                                 f"{verdict.reason}"
                             ),
                             "via": "web_button"},
                )
                results.append({
                    "order": order, "outcome": "risk_rejected",
                    "detail": (
                        f"Option qty would be resized ({order.qty} → "
                        f"{verdict.new_qty}). Rejected to preserve paired-roll "
                        f"integrity. Reason: {verdict.reason}"
                    ),
                })
                continue
            if verdict.verdict == "reject":
                order.status = "risk_rejected"
                deps.logger_agent.log_proposed_order(order)
                deps.logger_agent.log_event(
                    actor="risk", kind="risk_rejected",
                    payload={"order_id": order.id, "symbol": sym,
                             "reason": verdict.reason, "via": "web_button"},
                )
                results.append({
                    "order": order, "outcome": "risk_rejected",
                    "detail": verdict.reason,
                })
                continue
            if verdict.verdict == "resize" and verdict.new_qty is not None:
                order.qty = float(verdict.new_qty)
            # SINGLE-LEG dispatch core (close_short buy-to-close / open_short sell-
            # cover). Source-parameterized: no HITL hardcoded here — an auto loop would
            # reach this same risk-eval + place with source='auto'. RiskAgent.evaluate
            # (real-equity BP, halt, caps) already ran above; place() applies the
            # advisory guard. LEAP-mandate: these orders are short-call only.
            order.status = "board_approved"
            order.board_reason = f"approved via panel (source={source})"
            deps.logger_agent.log_proposed_order(order)
            deps.logger_agent.log_event(
                actor="board", kind="board_approved",
                payload={"order_id": order.id, "symbol": sym,
                         "via": "web_button", "qty": order.qty},
            )
            try:
                fill = await deps.data_exec.place(order, division=slug)
                results.append({
                    "order": order, "outcome": "filled",
                    "fill_price": fill.price, "venue": fill.venue,
                })
            except Exception as e:
                log.warning("execute_pair: place(%s) raised: %s", order.id, e)
                deps.logger_agent.log_event(
                    actor="data_exec", kind="execution_error",
                    payload={"order_id": order.id, "symbol": sym, "error": str(e)},
                )
                results.append({
                    "order": order, "outcome": "execute_error",
                    "detail": str(e)[:160],
                })

        # Invalidate the per-pair analysis cache so the next view fetches fresh
        with _LLM_LOCK:
            _pair_cache.pop((slug, sym), None)

        # Consume the acted-on decision (FIX 1). A TERMINAL FILL means the
        # standing ROLL/CLOSE recommendation is DONE, so record a HOLD. Written
        # as source='executed': it always overwrites the record it acted on
        # (even a fresh expert ROLL SHORT) so the tile flips to HOLD and the
        # Approve button self-disables on the next read — but it is treated like
        # a scan verdict for FUTURE precedence, so the next scan re-raises a
        # signal if this just-rolled position moves (no 8h blind spot). Only on
        # a genuine fill (not rest / no-fill / reject). Best-effort.
        if any(r["outcome"] == "filled" for r in results):
            try:
                from trading_corp.agents.divisions import _pmcc_status
                from datetime import datetime as _dt, timezone as _tz
                _pmcc_status.record_pmcc_decision(
                    sym, status="hold", source="executed",
                    computed_at=_dt.now(_tz.utc).isoformat(), db_url=deps.db_url,
                    urgency="routine",
                    summary="Position rolled/closed — holding to collect theta.",
                    rationale=("An approved action filled, so the prior actionable "
                               "recommendation is consumed. The next scan re-evaluates "
                               "and re-raises a signal if the position moves."),
                )
            except Exception as e:
                log.warning("pmcc executed-decision write(%s) failed: %s", sym, e)

        # Push the updated decision to the left-rail tile out-of-band (FIX 2) so
        # the badge + Approve affordance refresh from the just-written record
        # without a full page reload. Best-effort ('' on failure / no such row).
        return HTMLResponse(
            _render_execute_results(sym, analysis, results)
            + _pmcc_tile_badge_oob(templates, deps, sym)
        )

    # ── Scout — fresh PMCC opening candidates ───────────────────────────

    # Scout report cache: slug → (ScoutReport, ts)
    _scout_cache: dict[str, tuple[Any, float]] = {}
    _SCOUT_CACHE_TTL_SEC = 600   # 10 min — chain scans are expensive

    async def _render_scout_panel(slug: str, request: Request, force: bool) -> HTMLResponse:
        """Shared scout-panel renderer used by GET /scout and POST /scout/refresh."""
        if slug != "robinhood_pmcc" or deps.pmcc_agent is None:
            return HTMLResponse(
                '<div class="text-muted text-sm italic p-4">'
                'Scout is wired for Robinhood PMCC only.'
                '</div>'
            )
        broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        if broker is None:
            return HTMLResponse(
                '<div class="text-muted text-sm italic p-4">'
                'Broker not registered.'
                '</div>'
            )

        with _LLM_LOCK:
            entry = _scout_cache.get(slug)
            if entry is not None and not force:
                report, ts = entry
                if time.time() - ts < _SCOUT_CACHE_TTL_SEC:
                    return templates.TemplateResponse(
                        request, "partials/pmcc_scout.html",
                        {"slug": slug, "scout": report,
                         "fmt_age_sec": int(time.time() - ts)},
                    )

        try:
            reading = deps.trend_agent.read() if deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        try:
            report = await deps.pmcc_agent.scout_candidates(broker, regime=regime)
        except Exception as e:
            log.warning("scout_candidates(%s) raised: %s", slug, e)
            return HTMLResponse(
                f'<div class="p-4 text-loss font-mono text-sm">'
                f'Scout failed: {str(e)[:200]}'
                f'</div>'
            )

        with _LLM_LOCK:
            _scout_cache[slug] = (report, time.time())

        return templates.TemplateResponse(
            request, "partials/pmcc_scout.html",
            {"slug": slug, "scout": report, "fmt_age_sec": 0},
        )

    @app.get("/division/{slug}/scout", response_class=HTMLResponse)
    async def division_scout(slug: str, request: Request):
        """Render the scout panel HTML (cached 10 min)."""
        force = request.query_params.get("force") == "1"
        return await _render_scout_panel(slug, request, force=force)

    @app.post("/division/{slug}/scout/refresh", response_class=HTMLResponse)
    async def division_scout_refresh(slug: str, request: Request):
        """Force-refresh the scout report (bypasses cache)."""
        with _LLM_LOCK:
            _scout_cache.pop(slug, None)
        return await _render_scout_panel(slug, request, force=True)

    @app.post("/division/{slug}/scout/{symbol}/execute", response_class=HTMLResponse)
    async def execute_scout_open(slug: str, symbol: str):
        """Approve & execute a fresh PMCC OPEN on `symbol`.

        Mirrors execute_pair_orders but uses propose_opening_orders() (LEAP +
        first short) instead of the manage-existing-pair pipeline.
        """
        sym = symbol.upper()
        # Scout Approve = USER-INITIATED (see execute_pair_orders) — bypass dedupe.
        try:
            from trading_corp.comms.exec_alert import mark_user_origin
            mark_user_origin()
        except Exception:
            pass
        if slug != "robinhood_pmcc" or deps.pmcc_agent is None:
            return HTMLResponse(_exec_error_html(sym, "Scout is wired for Robinhood PMCC only."))
        broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        if broker is None:
            return HTMLResponse(_exec_error_html(sym, "Broker not registered."))

        # Build opening orders (LEAP buy + first weekly short sell)
        try:
            orders = await deps.pmcc_agent.propose_opening_orders(sym, broker)
        except Exception as e:
            log.warning("scout execute: propose_opening_orders(%s) raised: %s", sym, e)
            return HTMLResponse(_exec_error_html(sym, str(e)[:160]))

        if not orders:
            return HTMLResponse(
                '<div class="space-y-2 p-3 rounded-md bg-edge/40 border border-edge">'
                f'<div class="text-mono font-mono font-semibold text-sm">'
                f'ℹ️  {sym} · no orders proposed</div>'
                '<div class="text-xs text-muted">Earnings buffer / liquidity gates blocked '
                'this opening trade. Try again after the gate clears.</div>'
                '</div>'
            )

        # Account snapshot for risk gate
        try:
            snap = await broker.snapshot()
            account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception:
            account_equity = 0.0
        account = AccountState(
            account=getattr(snap, "account", slug) if 'snap' in dir() else slug,
            equity=account_equity or 100_000.0,
            peak_equity=account_equity or 100_000.0,
        )
        strat_state = StrategyState.from_persistence("robinhood_pmcc", db_url=deps.db_url)

        # Regime read (used by risk for counter-trend sizing)
        try:
            reading = deps.trend_agent.read() if deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        results: list[dict] = []
        for order in orders:
            verdict = deps.risk_agent.evaluate(
                order, account, strat_state, regime, None,
            )
            order.risk_reason = verdict.reason
            is_option = bool((order.extra or {}).get("is_option", False))
            # Option safety: see execute_pair_orders for rationale —
            # asymmetric pairs are structurally unsafe; reject instead.
            if verdict.verdict == "resize" and is_option:
                order.status = "risk_rejected"
                deps.logger_agent.log_proposed_order(order)
                deps.logger_agent.log_event(
                    actor="risk", kind="risk_rejected",
                    payload={"order_id": order.id, "symbol": sym,
                             "reason": (
                                 f"option resize → reject (would break paired-open integrity): "
                                 f"{verdict.reason}"
                             ),
                             "via": "scout_button"},
                )
                results.append({
                    "order": order, "outcome": "risk_rejected",
                    "detail": (
                        f"Option qty would be resized ({order.qty} → "
                        f"{verdict.new_qty}). Rejected to preserve paired-open "
                        f"integrity. Reason: {verdict.reason}"
                    ),
                })
                continue
            if verdict.verdict == "reject":
                order.status = "risk_rejected"
                deps.logger_agent.log_proposed_order(order)
                deps.logger_agent.log_event(
                    actor="risk", kind="risk_rejected",
                    payload={"order_id": order.id, "symbol": sym,
                             "reason": verdict.reason, "via": "scout_button"},
                )
                results.append({
                    "order": order, "outcome": "risk_rejected",
                    "detail": verdict.reason,
                })
                continue
            if verdict.verdict == "resize" and verdict.new_qty is not None:
                order.qty = float(verdict.new_qty)
            order.status = "board_approved"
            order.board_reason = "approved via scout open-position button"
            deps.logger_agent.log_proposed_order(order)
            deps.logger_agent.log_event(
                actor="board", kind="board_approved",
                payload={"order_id": order.id, "symbol": sym,
                         "via": "scout_button", "qty": order.qty,
                         "action": (order.extra or {}).get("action", "open_pmcc")},
            )
            try:
                fill = await deps.data_exec.place(order, division=slug)
                results.append({
                    "order": order, "outcome": "filled",
                    "fill_price": fill.price, "venue": fill.venue,
                })
            except Exception as e:
                log.warning("scout execute: place(%s) raised: %s", order.id, e)
                deps.logger_agent.log_event(
                    actor="data_exec", kind="execution_error",
                    payload={"order_id": order.id, "symbol": sym, "error": str(e)},
                )
                results.append({
                    "order": order, "outcome": "execute_error",
                    "detail": str(e)[:160],
                })

        # Invalidate scout cache so the next view reflects the new position
        with _LLM_LOCK:
            _scout_cache.pop(slug, None)

        # Reuse the existing pair-execute results renderer for consistency
        # (a pseudo-analysis object satisfies the renderer's signature).
        class _OpenAnalysis:
            action = "open_pmcc"
        return HTMLResponse(_render_execute_results(sym, _OpenAnalysis(), results))

    # ── Manual order entry (Phase B: Coinbase Spot) ──────────────────────

    @app.post("/audit/{audit_id}/replay-research", response_class=HTMLResponse)
    async def replay_signal_to_research(audit_id: int):
        """Re-run the research firm consult against a past TV-signal audit row.

        Loads the audit_event by id, synthesizes a placeholder ProposedOrder
        from its payload, and routes through `consult_research_for_trade_confirmation`.
        Writes a `research_replay_completed` audit row tagged with the source
        audit_event id. Returns an HTML fragment suitable for htmx swap into
        the originating dashboard tile.

        Authorization: behind Authelia at the reverse-proxy layer (no
        additional check here).
        """
        from trading_corp.agents.research.signal_replay import replay_signal_research

        with data.db.connect(deps.db_url) as conn:
            row = conn.execute(
                "SELECT id, ts, actor, kind, payload_json "
                "FROM audit_event WHERE id = ?",
                (audit_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit_event id={audit_id} not found")
        row_dict = dict(row)

        # Only allow replay on TV-signal-shaped audit rows.
        allowed_kinds = {"webhook_received", "alert_ignored", "would_have_placed"}
        if row_dict.get("kind") not in allowed_kinds:
            raise HTTPException(
                status_code=400,
                detail=f"audit_event kind '{row_dict.get('kind')}' is not replayable",
            )

        result = await replay_signal_research(
            row_dict,
            research_firm=deps.research_firm,
            logger_agent=deps.logger_agent,
        )

        # Render a compact HTML fragment. htmx swap target on the
        # dashboard side decides where this lands.
        verdict = (result.verdict_kind or "?").upper()
        decision = (result.decision or "?").upper()
        rationale = (result.rationale or "(no rationale)").replace("<", "&lt;").replace(">", "&gt;")
        verdict_color = {
            "confirm": "text-gain",
            "conditional": "text-warn",
            "push_back": "text-loss",
            "no_research": "text-muted",
            "timeout": "text-warn",
            "error": "text-loss",
        }.get(result.verdict_kind, "text-muted")

        html = (
            f'<div class="border-l-2 border-edge pl-3 mt-2 text-[11px] font-mono">'
            f'  <div class="flex gap-2 items-baseline">'
            f'    <span class="text-muted uppercase tracking-wider">research →</span>'
            f'    <span class="{verdict_color} font-semibold uppercase">{verdict}</span>'
            f'    <span class="text-muted">decision={decision.lower()}</span>'
            f'  </div>'
            f'  <div class="mt-1 text-mono leading-snug whitespace-pre-wrap">{rationale}</div>'
            f'</div>'
        )
        return HTMLResponse(html)

    @app.post("/division/{slug}/order/manual", response_class=HTMLResponse)
    async def submit_manual_order(slug: str, request: Request):
        """Submit a manual ad-hoc order through the risk + execution pipeline.

        Phase B scope: Coinbase Spot only. The form lives on the division
        drilldown page; on submit we build a ProposedOrder from the form
        fields, run it through the risk gate (same code path as PMCC scout
        and pair-management orders), log proposal/approval, then call
        data_exec.place(). Result HTML swaps into a panel below the form.

        Why a slug whitelist: this route accepts user input and routes to a
        live broker. We want to be deliberate about which divisions can
        trigger it. Today only coinbase_spot is wired. Adding others is a
        one-line whitelist change once we've validated the broker family.
        """
        # Whitelist of divisions where manual entry is enabled.
        if slug not in ("coinbase_spot",):
            return HTMLResponse(_manual_order_error_html(
                "Manual orders are only enabled for Coinbase Spot in Phase B."
            ))

        form = await request.form()

        # Parse + validate form fields. We do server-side validation even
        # though the form has client-side checks — never trust the client.
        try:
            symbol = (form.get("symbol") or "").strip().upper()
            side = (form.get("side") or "").strip().lower()
            order_type = (form.get("order_type") or "limit").strip().lower()
            qty_raw = (form.get("qty") or "").strip()
            qty = float(qty_raw) if qty_raw else 0.0
            limit_price_raw = (form.get("limit_price") or "").strip()
            limit_price = float(limit_price_raw) if limit_price_raw else None
            rationale = (form.get("rationale") or "").strip()
        except (ValueError, TypeError) as e:
            return HTMLResponse(_manual_order_error_html(
                f"Invalid form input: {e}"
            ))

        # Symbol must be unified format ("BTC/USD"). The broker also accepts
        # "BTC-USD" and normalizes, but we require the slash here so the
        # form result panel renders consistently.
        if not symbol or "/" not in symbol or len(symbol) > 20:
            return HTMLResponse(_manual_order_error_html(
                "Symbol must be in unified format (e.g. 'BTC/USD')."
            ))
        if side not in ("buy", "sell"):
            return HTMLResponse(_manual_order_error_html(
                "Side must be 'buy' or 'sell'."
            ))
        if order_type not in ("market", "limit"):
            return HTMLResponse(_manual_order_error_html(
                "Order type must be 'market' or 'limit'."
            ))
        if qty <= 0:
            return HTMLResponse(_manual_order_error_html(
                "Quantity must be greater than 0."
            ))
        if order_type == "limit" and (not limit_price or limit_price <= 0):
            return HTMLResponse(_manual_order_error_html(
                "Limit orders require a positive limit price."
            ))

        broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        if broker is None:
            return HTMLResponse(_manual_order_error_html(
                "Broker not registered for this division."
            ))

        from trading_corp.persistence.models import ProposedOrder
        order = ProposedOrder(
            strategy=f"manual_{slug}",
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            rationale=rationale or "manual ad-hoc order via dashboard",
            extra={
                "manual": True,
                "asset_type": "crypto",
                "via": "manual_order_form",
            },
        )
        # Stash the user-submitted qty before any risk-gate mutation so the
        # result panel can disclose downsizes ("you asked for 0.5, risk gate
        # capped at 0.0177").
        original_qty = float(qty)

        # Build account/strategy state for the risk gate. Fall back to a
        # safe placeholder equity if the snapshot fails — better to let the
        # risk gate run on something than to skip it entirely.
        snap = None
        try:
            snap = await broker.snapshot()
            account_equity = float(getattr(snap, "equity", 0.0) or 0.0)
        except Exception as e:
            log.warning("manual order: broker.snapshot() failed: %s", e)
            account_equity = 0.0
        account = AccountState(
            account=getattr(snap, "account", slug) if snap else slug,
            equity=account_equity or 100_000.0,
            peak_equity=account_equity or 100_000.0,
        )
        strat_state = StrategyState.from_persistence(order.strategy, db_url=deps.db_url)

        try:
            reading = deps.trend_agent.read() if deps.trend_agent else None
            regime = getattr(reading, "regime", "unknown") if reading else "unknown"
        except Exception:
            regime = "unknown"

        # Risk gate (same path as scout / pair management).
        verdict = deps.risk_agent.evaluate(
            order, account, strat_state, regime, None,
        )
        order.risk_reason = verdict.reason

        if verdict.verdict == "reject":
            order.status = "risk_rejected"
            deps.logger_agent.log_proposed_order(order)
            deps.logger_agent.log_event(
                actor="risk", kind="risk_rejected",
                payload={
                    "order_id": order.id, "symbol": symbol,
                    "reason": verdict.reason, "via": "manual_order",
                },
            )
            return HTMLResponse(_render_manual_order_result(
                order, "risk_rejected", detail=verdict.reason,
            ))

        if verdict.verdict == "resize" and verdict.new_qty is not None:
            # For crypto spot, resize is fine (no paired-roll integrity
            # issue like options). Just update qty and proceed.
            log.info(
                "manual order: risk gate resized %s qty %s → %s",
                symbol, order.qty, verdict.new_qty,
            )
            order.qty = float(verdict.new_qty)

        # Board approval is implicit — the user already clicked submit on
        # the form, and the hx-confirm popup gave them a final chance to
        # back out. Log explicitly so the audit trail is unambiguous.
        order.status = "board_approved"
        order.board_reason = "submitted via manual order form"
        deps.logger_agent.log_proposed_order(order)
        deps.logger_agent.log_event(
            actor="board", kind="board_approved",
            payload={
                "order_id": order.id, "symbol": symbol,
                "via": "manual_order", "qty": order.qty,
                "side": side, "type": order_type,
                "limit_price": limit_price,
            },
        )

        # Pass original_qty/reason to the renderer only when the risk gate
        # actually changed the qty. Renderer treats `original_qty=None` as
        # "no resize disclosure needed", which is the common case.
        resize_kwargs = {}
        if abs(original_qty - float(order.qty)) > 1e-12:
            resize_kwargs["original_qty"] = original_qty
            resize_kwargs["risk_resize_reason"] = verdict.reason

        # Execute. data_exec.place() handles dry_run short-circuit and
        # broker dispatch + fill logging.
        try:
            fill = await deps.data_exec.place(order, division=slug)
            return HTMLResponse(_render_manual_order_result(
                order, "filled", fill_price=fill.price, venue=fill.venue,
                **resize_kwargs,
            ))
        except Exception as e:
            log.warning("manual order: place(%s) raised: %s", order.id, e)
            deps.logger_agent.log_event(
                actor="data_exec", kind="execution_error",
                payload={
                    "order_id": order.id, "symbol": symbol,
                    "error": str(e), "via": "manual_order",
                },
            )
            return HTMLResponse(_render_manual_order_result(
                order, "execute_error", detail=str(e)[:200],
                **resize_kwargs,
            ))

    # ── Phase 3+ stubs ───────────────────────────────────────────────────

    @app.get("/trades", response_class=HTMLResponse)
    async def trades_view(request: Request):
        return templates.TemplateResponse(
            request, "placeholder.html",
            {"title": "Trade Center", "phase": "3"},
        )

    @app.get("/system", response_class=HTMLResponse)
    async def system_view(request: Request):
        return templates.TemplateResponse(
            request, "placeholder.html",
            {"title": "System", "phase": "later"},
        )

    # ── HITL approval surface (Phase B.1 → B.3) ──────────────────────────
    # The dashboard surface for Board approvals. Reads + resolves the
    # in-process PendingApprovalRegistry held on deps.pending_registry
    # (constructed in main.py). Coexists with the Telegram inline-keyboard
    # path during the soak window — first decision wins, second one gets
    # a 409 (web) or "already decided" (Telegram). B.2 added rich
    # rendering via comms/position_context. B.3 added pair-coalescing —
    # paired-roll legs render in ONE card with ONE atomic decision.

    @app.get("/approvals", response_class=HTMLResponse)
    async def approvals_index(request: Request):
        """Index of pending approvals. Empty state when none, or
        explanatory note when the registry isn't wired (CLI dev /
        no-Telegram fallback). B.3 — paired entries (same pmcc_pair_id)
        are grouped into a single row with a combined headline."""
        snap = await data.build_command_center(deps)
        registry = deps.pending_registry
        entries = registry.list_pending() if registry is not None else []
        rows = _group_index_entries(entries)
        return templates.TemplateResponse(
            request, "approvals.html",
            {
                "snap": snap,
                "rows": rows,
                "total_legs": len(entries),
                "registry_unavailable": registry is None,
            },
        )

    # ── IC Combo HITL (Phase IC1, 2026-05-17) ────────────────────────────
    # Iron-condor combos are atomic at the broker — one Board click
    # authorizes all 4 legs together — so they don't fit the per-order
    # `wait()` model used for single-leg /approvals. Combo registry is
    # `deps.pending_combo_registry` (sibling of `deps.pending_registry`).
    # Registered BEFORE the catch-all `/approvals/{order_id}` so FastAPI
    # matches the literal `combos` segment first.

    @app.get("/approvals/combos/{combo_id}", response_class=HTMLResponse)
    async def combo_approval_detail(request: Request, combo_id: str):
        """Detail page for one pending IC combo. 404 when the combo is
        no longer pending (already resolved or never registered)."""
        from trading_corp.web.combo_approval_view import build_combo_card_payload
        registry = deps.pending_combo_registry
        if registry is None:
            raise HTTPException(
                status_code=404, detail="combo approval registry unavailable",
            )
        entry = registry.get(combo_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"combo_id {combo_id} not pending",
            )
        view = build_combo_card_payload(entry)
        snap = await data.build_command_center(deps)
        return templates.TemplateResponse(
            request, "approval_combo_detail.html",
            {"snap": snap, "view": view, "entry": entry},
        )

    @app.post("/approvals/combos/{combo_id}/decide", response_class=HTMLResponse)
    async def combo_approval_decide(request: Request, combo_id: str):
        """POST decision endpoint for an IC combo.

        Body (form-encoded from the in-page form OR JSON):
          - `decision`: "approve" | "reject"
          - `reason`:   optional string

        On approve: pops the registry entry, writes `board_combo_approved`,
        fires `dispatch_approved_ic_combo` (atomic place_combo +
        on_combo_filled state callback). On reject: pops + audits only.
        409 if the combo was already resolved; 400 on invalid decision;
        404 if the registry is not wired.
        """
        from trading_corp.agents.strategies._ic_orchestration import (
            dispatch_approved_ic_combo,
        )
        registry = deps.pending_combo_registry
        if registry is None:
            raise HTTPException(
                status_code=404, detail="combo approval registry unavailable",
            )
        ctype = (request.headers.get("content-type") or "").lower()
        if ctype.startswith("application/json"):
            body = await request.json()
            decision_str = body.get("decision")
            reason = body.get("reason") or ""
        else:
            form = await request.form()
            decision_str = form.get("decision")
            reason = form.get("reason") or ""
        if decision_str not in ("approve", "reject"):
            raise HTTPException(
                status_code=400,
                detail="decision must be 'approve' or 'reject'",
            )
        entry = registry.resolve(
            combo_id, decision=decision_str, reason=reason, source="web",
        )
        if entry is None:
            raise HTTPException(
                status_code=409,
                detail=f"combo_id {combo_id} already resolved or not pending",
            )
        if decision_str == "approve":
            try:
                fills = await dispatch_approved_ic_combo(
                    entry.orders,
                    strategy=deps.ic_strategy,
                    data_exec=deps.data_exec,
                    division=entry.division,
                )
            except Exception as e:
                log.exception(
                    "combo_approval_decide: dispatch_approved_ic_combo raised "
                    "for combo %s — combo is APPROVED in the registry but "
                    "place_combo failed; investigate audit log",
                    combo_id,
                )
                return HTMLResponse(
                    f'<div class="text-loss text-sm font-mono">'
                    f'Decision recorded: APPROVE, but place_combo failed: '
                    f'{type(e).__name__}. Check audit log for combo '
                    f'{combo_id[:8]}.</div>',
                    status_code=500,
                )
            leg_count = len(fills) if fills else 0
            return HTMLResponse(
                f'<div class="text-gain text-sm font-mono">'
                f'Decision recorded: APPROVE · combo {combo_id[:8]} · '
                f'{leg_count} leg(s) filled.</div>',
            )
        return HTMLResponse(
            f'<div class="text-mono text-sm font-mono">'
            f'Decision recorded: REJECT · combo {combo_id[:8]}.</div>',
        )

    # ── IC live trades view (Phase IC1, 2026-05-17) ──────────────────────
    # Operator debugging surface. Sections 1/3/5 htmx-refresh every 30s
    # via the `/partials/live` endpoint; sections 2/4/6 render on
    # page load.

    @app.get("/telemetry/iron_condor", response_class=HTMLResponse)
    async def iron_condor_live(request: Request, division: str = "robinhood_joint"):
        from trading_corp.agents import ic_live_view as _icv
        ctx = _resolve_ic_division_ctx(deps, division)
        broker = deps.data_exec.brokers.get(ctx["broker_key"]) or deps.paper_broker
        positions = await _icv.open_positions_detail(
            broker=broker, db_url=deps.db_url,
        )
        activity = _icv.recent_activity(db_url=deps.db_url, limit=50)
        pending = _icv.pending_combos_view(
            registry=ctx["pending_combo_registry"],
            batcher=ctx["telegram_batcher"],
        )
        scan_results = await _icv.todays_scan_results(
            broker=broker, db_url=deps.db_url,
        )
        health = _icv.strategy_health(
            ic_strategy=ctx["strategy"],
            ic_division=ctx["division"],
            pending_combo_registry=ctx["pending_combo_registry"],
            telegram_batcher=ctx["telegram_batcher"],
            db_url=deps.db_url,
        )
        closed = _icv.recent_closed_combos(db_url=deps.db_url, limit=10)
        from datetime import datetime as _dt, timezone as _tz
        now_iso = _dt.now(_tz.utc).isoformat(timespec="seconds")
        # Tasty activation tile (relocated from the retired home-page Stage-1
        # monitoring row) — only meaningful on the tasty_options division.
        # Reads the tastytrade broker session state + scanner-tick rate.
        tasty = None
        if ctx["division_slug"] == "tasty_options":
            brokers_map = (
                getattr(deps.data_exec, "brokers", None)
                if deps.data_exec is not None else None
            )
            tasty = data.tasty_activation_status(brokers_map)
        return templates.TemplateResponse(
            request, "iron_condor_live.html",
            {
                "now_iso": now_iso,
                "positions": positions,
                "activity": activity,
                "pending": pending,
                "scan_results": scan_results,
                "health": health,
                "closed": closed,
                "division_slug": ctx["division_slug"],
                "division_name": ctx["division_name"],
                "tasty": tasty,
            },
        )

    @app.get(
        "/telemetry/iron_condor/partials/live", response_class=HTMLResponse,
    )
    async def iron_condor_live_partial(
        request: Request, division: str = "robinhood_joint",
    ):
        """HTMX-refreshing partial for sections 1 / 3 / 5 (open positions,
        pending combos, strategy health)."""
        from trading_corp.agents import ic_live_view as _icv
        ctx = _resolve_ic_division_ctx(deps, division)
        broker = deps.data_exec.brokers.get(ctx["broker_key"]) or deps.paper_broker
        positions = await _icv.open_positions_detail(
            broker=broker, db_url=deps.db_url,
        )
        pending = _icv.pending_combos_view(
            registry=ctx["pending_combo_registry"],
            batcher=ctx["telegram_batcher"],
        )
        health = _icv.strategy_health(
            ic_strategy=ctx["strategy"],
            ic_division=ctx["division"],
            pending_combo_registry=ctx["pending_combo_registry"],
            telegram_batcher=ctx["telegram_batcher"],
            db_url=deps.db_url,
        )
        return templates.TemplateResponse(
            request, "partials/iron_condor_live_sections.html",
            {
                "positions": positions,
                "pending": pending,
                "health": health,
            },
        )

    @app.post(
        "/telemetry/iron_condor/grade", response_class=HTMLResponse,
    )
    async def iron_condor_grade(
        request: Request, division: str = "robinhood_joint",
    ):
        """Grade an operator-pasted Barchart screener block against live
        IC strategy rules.  Research/grading only — no order surface.

        Body (form-encoded): `paste` = textarea content.
        Query: `division` ∈ {"robinhood_joint", "tasty_options"} — selects
        which strategy's universe/watchlist + thresholds to grade against.
        Default preserves backwards compatibility with the original UI.
        Response: partial HTML fragment for htmx swap into #grader-result.
        """
        from trading_corp.agents.strategies.ic_candidate_grader import (
            grade_paste,
        )
        from trading_corp.utils.iv import _get_configured_provider
        form = await request.form()
        paste = str(form.get("paste") or "")
        ctx = _resolve_ic_division_ctx(deps, division)
        if ctx["strategy"] is None:
            raise HTTPException(
                status_code=503,
                detail=f"{ctx['strategy_slug']} not available",
            )
        provider = _get_configured_provider()
        result = await grade_paste(
            paste,
            strategy=ctx["strategy"],
            provider=provider,
            logger=deps.logger_agent,
            division=ctx["division_slug"],
            strategy_slug=ctx["strategy_slug"],
        )
        return templates.TemplateResponse(
            request, "partials/iron_condor_grader_result.html",
            {"result": result, "division_slug": ctx["division_slug"]},
        )

    # ── Robinhood PEAD operations dashboard (read-layer) ─────────────────
    # Single-screen command deck for the robinhood_pead division: a page shell
    # + a 15s-polled `/partials/live` fragment (the open book / pressures hero,
    # health strip, funnel, attribution) + a kill-switch POST. Read-only
    # everywhere except the halt button. Renders pressure-empty until the
    # Phase-2 exit engine writes the extra_json primitives, then lights up.

    @app.get("/telemetry/pead", response_class=HTMLResponse)
    async def pead_live(request: Request):
        from trading_corp.web.pead_view import build_pead_view
        view = await build_pead_view(deps)
        return templates.TemplateResponse(request, "pead_live.html", {"v": view})

    @app.get("/telemetry/pead/partials/live", response_class=HTMLResponse)
    async def pead_live_partial(request: Request):
        from trading_corp.web.pead_view import build_pead_view
        view = await build_pead_view(deps)
        return templates.TemplateResponse(
            request, "partials/pead_live_sections.html", {"v": view},
        )

    @app.post("/telemetry/pead/halt", response_class=HTMLResponse)
    async def pead_halt(request: Request):
        """Kill switch — the ONLY write surface on the PEAD dashboard.

        Durably halts new PEAD entries (the Phase-2 engine checks
        agent_state robinhood_pead/halt before entering) and best-effort
        flattens any open book via the division broker if wired (a no-op
        before Phase 2), then audits. Read-only everywhere else.
        """
        import inspect
        from trading_corp.web.pead_view import DIVISION
        db_url = deps.db_url
        reason = "dashboard_kill_switch"
        _db_mod.set_agent_state(
            DIVISION, "halt",
            {"halted": True, "reason": reason, "ts": _now_iso(),
             "source": "dashboard_button"},
            db_url=db_url,
        )
        closed = 0
        broker = None
        if getattr(deps, "data_exec", None) is not None:
            broker = deps.data_exec.brokers.get(DIVISION)
        if broker is not None and hasattr(broker, "flatten"):
            try:
                res = broker.flatten()
                if inspect.isawaitable(res):
                    res = await res
                closed = int(res.get("closed", 0)) if isinstance(res, dict) else 0
            except Exception as e:  # noqa: BLE001
                log.warning("pead_halt: flatten failed: %s", e)
        if deps.logger_agent is not None:
            deps.logger_agent.log_event(
                "pead_operations", "pead_halt_initiated",
                {"strategy": "robinhood_pead", "division": DIVISION,
                 "reason": reason, "closed_positions": closed,
                 "halted_iso": _now_iso(), "source": "dashboard_button"},
            )
        log.info("pead_halt_initiated: reason=%s closed=%d", reason, closed)
        return _render_action_pill(f"PEAD HALTED · {closed} closed")

    # ITEM2-RH-ROUTES: RH session health + refresh button ------------------------------
    _RH_AGENT, _RH_KEY = "robinhood_session", "refresh_status"
    _BUTTON_REAUTH_TIMEOUT_S = 90

    def _rh_health_ctx():
        import os, time
        from trading_corp.brokers import robinhood as _rh
        pickle = os.path.expanduser("~/.tokens/robinhood.pickle")
        try:
            age_s = int(time.time() - os.stat(pickle).st_mtime)
        except OSError:
            age_s = -1
        rec = _db_mod.load_agent_state(_RH_AGENT, _RH_KEY, db_url=deps.db_url)
        st = rec[0] if (rec and isinstance(rec[0], dict)) else {}
        return {"auth_state": "down" if getattr(_rh, "_auth_down", False) else "valid",
                "down_since": getattr(_rh, "_auth_down_since", None),
                "last_good": getattr(_rh, "_auth_last_good", None),
                "pickle_age_s": age_s, "refresh": st.get("state", "idle"),
                "refresh_msg": st.get("msg", "")}

    @app.get("/api/rh/session-health", response_class=HTMLResponse)
    async def rh_session_health(request: Request):
        return templates.TemplateResponse(request, "rh_session_panel.html", {"health": _rh_health_ctx()})

    @app.post("/api/rh/refresh-session", response_class=HTMLResponse)
    async def rh_refresh_session(request: Request):
        db_url = deps.db_url
        rec = _db_mod.load_agent_state(_RH_AGENT, _RH_KEY, db_url=db_url)
        if rec and isinstance(rec[0], dict) and rec[0].get("state") in ("running", "push_sent"):
            return templates.TemplateResponse(request, "rh_session_panel.html", {"health": _rh_health_ctx()})
        broker = deps.data_exec.brokers.get("robinhood_pead") or next(
            (b for k, b in deps.data_exec.brokers.items() if k.startswith("robinhood")), None)

        async def _run():
            try:
                _db_mod.set_agent_state(_RH_AGENT, _RH_KEY,
                    {"state": "push_sent", "msg": "approve the push on your phone", "ts": _now_iso()}, db_url=db_url)
                ok = False
                if broker is not None and hasattr(broker, "_attempt_reauth"):
                    ok = await broker._attempt_reauth(force=True, timeout=_BUTTON_REAUTH_TIMEOUT_S)
                _db_mod.set_agent_state(_RH_AGENT, _RH_KEY,
                    {"state": "valid" if ok else "failed",
                     "msg": "" if ok else "login not confirmed (push not approved / sms-email / creds)",
                     "ts": _now_iso()}, db_url=db_url)
            except Exception as e:  # noqa: BLE001
                _db_mod.set_agent_state(_RH_AGENT, _RH_KEY,
                    {"state": "failed", "msg": str(e), "ts": _now_iso()}, db_url=db_url)

        _db_mod.set_agent_state(_RH_AGENT, _RH_KEY, {"state": "running", "msg": "", "ts": _now_iso()}, db_url=db_url)
        asyncio.create_task(_run())
        return templates.TemplateResponse(request, "rh_session_panel.html", {"health": _rh_health_ctx()})

    @app.get("/approvals/{order_id}", response_class=HTMLResponse)
    async def approval_detail(request: Request, order_id: str):
        """Detail page for a single pending approval. 404 when the
        order is no longer pending (already resolved or never
        registered). B.3 — when the order has a paired sibling
        currently pending, both legs render in ONE card with combined
        Net Debit/Credit and ONE Approve button."""
        from trading_corp.comms.position_context import (
            build_approval_view, coalesce_paired_view,
        )
        registry = deps.pending_registry
        if registry is None:
            raise HTTPException(
                status_code=404, detail="approval registry unavailable",
            )
        entry = registry.get_entry(order_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"order_id {order_id} not pending",
            )
        primary_view = build_approval_view(entry.request.detail or {})
        primary_view["_order_id"] = order_id

        sibling_view = None
        sibling_req = registry.find_sibling(order_id)
        if sibling_req is not None:
            sibling_view = build_approval_view(sibling_req.detail or {})
            sibling_view["_order_id"] = sibling_req.order_id
            view = coalesce_paired_view([primary_view, sibling_view])
            view["_order_id"] = order_id   # POST target stays primary
        else:
            view = primary_view

        snap = await data.build_command_center(deps)
        return templates.TemplateResponse(
            request, "approval_detail.html",
            {
                "snap": snap,
                "entry": entry,
                "req": entry.request,
                "view": view,
                "is_paired": bool(sibling_view),
                "sibling_order_id": (
                    sibling_view["_order_id"] if sibling_view else None
                ),
            },
        )

    @app.post("/approvals/{order_id}/decide", response_class=HTMLResponse)
    async def approval_decide(request: Request, order_id: str):
        """POST decision endpoint.

        Body (form-encoded from the in-page form OR JSON for API
        callers):
          - `decision`: "approve" | "reject" | "modify"
          - `reason`:   optional string
          - `new_qty`:  required when decision=="modify" (float > 0)
          - `also_resolve_paired`: optional bool (B.3) — when truthy
            AND the order has a paired sibling currently pending, the
            sibling is resolved with the SAME decision in the same
            call. The detail-page form sets this when rendering a
            paired card.

        Returns 200 + a small HTML fragment on accept; 409 if the
        registry entry was already resolved (Telegram or another
        browser tab won the race); 400 on invalid decision / missing
        new_qty for modify; 404 if the registry is not wired.
        """
        from trading_corp.graph.interrupts import BoardDecision
        registry = deps.pending_registry
        if registry is None:
            raise HTTPException(
                status_code=404, detail="approval registry unavailable",
            )
        ctype = (request.headers.get("content-type") or "").lower()
        if ctype.startswith("application/json"):
            body = await request.json()
            decision_str = body.get("decision")
            reason = body.get("reason") or ""
            new_qty_raw = body.get("new_qty")
            new_limit_raw = body.get("new_limit_price")
            also_paired = bool(body.get("also_resolve_paired"))
        else:
            form = await request.form()
            decision_str = form.get("decision")
            reason = form.get("reason") or ""
            new_qty_raw = form.get("new_qty")
            new_limit_raw = form.get("new_limit_price")
            also_paired = (form.get("also_resolve_paired") or "").lower() in (
                "1", "true", "on", "yes",
            )
        if decision_str not in ("approve", "reject", "modify"):
            raise HTTPException(
                status_code=400,
                detail="decision must be 'approve', 'reject', or 'modify'",
            )
        new_qty: float | None = None
        new_limit: float | None = None
        if decision_str == "modify":
            # B.5 — modify accepts new_qty AND/OR new_limit_price.
            # Either alone (or both together) is valid; at least one is
            # required. Quick-modify buttons in the UI typically set
            # only one (½× size sets new_qty; limit-5% sets new_limit).
            if (new_qty_raw is None or new_qty_raw == "") and (
                new_limit_raw is None or new_limit_raw == ""
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "modify requires at least one of new_qty / "
                        "new_limit_price"
                    ),
                )
            if new_qty_raw not in (None, ""):
                try:
                    new_qty = float(new_qty_raw)
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail=f"new_qty must be a number (got {new_qty_raw!r})",
                    )
                if new_qty <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"new_qty must be > 0 (got {new_qty})",
                    )
            if new_limit_raw not in (None, ""):
                try:
                    new_limit = float(new_limit_raw)
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail=f"new_limit_price must be a number (got {new_limit_raw!r})",
                    )
                if new_limit <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"new_limit_price must be > 0 (got {new_limit})",
                    )
        decision = BoardDecision(
            decision=decision_str,
            reason=reason or "via web",
            new_qty=new_qty,
            new_limit_price=new_limit,
        )
        accepted = registry.resolve(
            order_id, decision, source="web",
            also_resolve_paired=also_paired,
        )
        if not accepted:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"order_id {order_id} already resolved or not pending"
                ),
            )
        bits = []
        if new_qty is not None:
            bits.append(f"qty={new_qty:g}")
        if new_limit is not None:
            bits.append(f"limit=${new_limit:.2f}")
        modify_suffix = f" ({', '.join(bits)})" if bits else ""
        msg = (
            f"Decision recorded: {decision_str.upper()}"
            + modify_suffix
            + (" · both legs resolved" if also_paired else "")
        )
        return HTMLResponse(
            f'<div class="text-gain text-sm font-mono">'
            f'{msg}. '
            f'<a href="/approvals" class="underline">Back to pending list</a>.'
            f'</div>'
        )

    # ── Promote / Demote (watch list ↔ selected whales) ───────────────────
    # Four endpoints — one promote + one demote per venue. Promote moves a
    # whale from watch_only_whales into selected_whales AND pins it via
    # pinned_whales (so the next refresh_*_whales.py run doesn't evict it).
    # Demote removes from selected + pinned, calls force_close_whale_positions
    # to flatten the paper book via synthetic SELL audits, and adds the entry
    # back to watch_only_whales so we keep observing the whale's track record.
    # Strategy reloads selected_whales every cycle so the change takes effect
    # on the next poll (Polymarket 60s, Kalshi 600s) without restart.
    #
    # Pinning rationale: refresh_*_whales.py scripts overwrite selected_whales
    # with the algorithm's top-N. Without pinned_whales, any manual promotion
    # would be silently evicted on the next refresh run.

    from trading_corp.persistence import db as _db_mod

    def _render_action_pill(msg: str, *, success: bool = True) -> HTMLResponse:
        cls = "text-gain" if success else "text-loss"
        # HX-Refresh tells htmx to reload the page after this response, which
        # re-renders both the Selected Whales and Watch List panels from the
        # updated agent_state slots. Without this, only the clicked row vanishes
        # (the pill div replaces it via outerHTML) and the user thinks the
        # action didn't take effect.
        return HTMLResponse(
            f'<div class="{cls} text-[11px] font-mono uppercase tracking-wider">'
            f'{msg}</div>',
            headers={"HX-Refresh": "true"},
        )

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @app.post("/api/kalshi/watchlist/promote/{handle}", response_class=HTMLResponse)
    async def kalshi_watchlist_promote(handle: str):
        """Promote a Kalshi whale from watch_only_whales → selected_whales.

        Mutations:
          - selected_whales (list[str]): append handle (idempotent)
          - pinned_whales   (list[str]): append handle (idempotent)
          - watch_only_whales (list[dict]): remove the matching row
        """
        db_url = deps.db_url
        # selected (list[str])
        sel_rec = _db_mod.load_agent_state(
            "kalshi_copy_trader", "selected_whales", db_url=db_url,
        )
        selected: list[str] = list(sel_rec[0]) if sel_rec and isinstance(sel_rec[0], list) else []
        if handle not in selected:
            selected.append(handle)
        # pinned (list[str])
        pin_rec = _db_mod.load_agent_state(
            "kalshi_copy_trader", "pinned_whales", db_url=db_url,
        )
        pinned: list[str] = list(pin_rec[0]) if pin_rec and isinstance(pin_rec[0], list) else []
        if handle not in pinned:
            pinned.append(handle)
        # We intentionally do NOT mutate watch_only_whales here. The watch
        # list panel filters out entries whose handle is in selected_whales,
        # so promoting hides the row automatically while preserving the
        # original Apify-scraped stats for if/when the whale is demoted.

        _db_mod.set_agent_state("kalshi_copy_trader", "selected_whales", selected, db_url=db_url)
        _db_mod.set_agent_state("kalshi_copy_trader", "pinned_whales", pinned, db_url=db_url)

        if deps.logger_agent is not None:
            deps.logger_agent.log_event(
                "kalshi_copy_trader", "kalshi_whale_promoted",
                {"strategy": "kalshi_copy_trader",
                 "division": "kalshi_copy_trading",
                 "handle": handle,
                 "promoted_iso": _now_iso(),
                 "source": "dashboard_button"},
            )
        log.info("kalshi_whale_promoted: %s", handle)
        return _render_action_pill(f"@{handle} promoted")

    @app.post("/api/kalshi/whales/demote/{handle}", response_class=HTMLResponse)
    async def kalshi_whales_demote(handle: str):
        """Demote a Kalshi whale: stop copy-trading + flatten paper book.

        Calls `kalshi_copy_trader.force_close_whale_positions` to emit
        synthetic SELL audits for every tracked open position, then moves
        the entry from selected_whales/pinned_whales back to
        watch_only_whales.
        """
        from trading_corp.agents.strategies import kalshi_copy_trader
        db_url = deps.db_url

        close_summary = kalshi_copy_trader.force_close_whale_positions(
            handle, db_url=db_url, logger_agent=deps.logger_agent,
        )

        sel_rec = _db_mod.load_agent_state(
            "kalshi_copy_trader", "selected_whales", db_url=db_url,
        )
        selected: list[str] = list(sel_rec[0]) if sel_rec and isinstance(sel_rec[0], list) else []
        selected_after = [h for h in selected if h != handle]

        pin_rec = _db_mod.load_agent_state(
            "kalshi_copy_trader", "pinned_whales", db_url=db_url,
        )
        pinned: list[str] = list(pin_rec[0]) if pin_rec and isinstance(pin_rec[0], list) else []
        pinned_after = [h for h in pinned if h != handle]

        # Ensure the demoted whale appears in watch_only_whales. Auto-selected
        # finalists may never have been in watch_only_whales (they were seeded
        # directly into selected_whales by the seed script), so removing them
        # from selected_whales would make them vanish from BOTH panels. We add
        # a stub entry if absent so they reappear in the Watch List panel.
        # If they were already in watch_only_whales (previously promoted from
        # there), the existing entry is preserved with its Apify-scraped stats.
        wo_rec = _db_mod.load_agent_state(
            "kalshi_copy_trader", "watch_only_whales", db_url=db_url,
        )
        watch_only: list[dict] = list(wo_rec[0]) if wo_rec and isinstance(wo_rec[0], list) else []
        if not any(isinstance(w, dict) and w.get("handle") == handle for w in watch_only):
            watch_only.append({
                "handle": handle,
                "tier": None,
                "source_x_handle": None,
                "notes": "demoted_via_dashboard",
                "included_iso": _now_iso(),
            })
            _db_mod.set_agent_state(
                "kalshi_copy_trader", "watch_only_whales", watch_only, db_url=db_url,
            )

        _db_mod.set_agent_state("kalshi_copy_trader", "selected_whales", selected_after, db_url=db_url)
        _db_mod.set_agent_state("kalshi_copy_trader", "pinned_whales", pinned_after, db_url=db_url)

        if deps.logger_agent is not None:
            deps.logger_agent.log_event(
                "kalshi_copy_trader", "kalshi_whale_demoted",
                {"strategy": "kalshi_copy_trader",
                 "division": "kalshi_copy_trading",
                 "handle": handle,
                 "demoted_iso": _now_iso(),
                 "source": "dashboard_button",
                 "n_synthetic_sells": close_summary.get("n_closed", 0),
                 "positions_closed": close_summary.get("positions", [])},
            )
        log.info(
            "kalshi_whale_demoted: %s (n_synthetic_sells=%d)",
            handle, close_summary.get("n_closed", 0),
        )
        n = close_summary.get("n_closed", 0)
        suffix = f" · closed {n} position{'s' if n != 1 else ''}" if n else ""
        return _render_action_pill(f"@{handle} demoted{suffix}")

    # ── Kalshi whale discovery (cost-controlled, async / SUNDAY SEED) ───
    #
    # COST NOTE: The daily `trading-corp-watchlist-stats.timer` (runs at
    # 12:00 UTC, refreshes ALL watch_only handles unconditionally via
    # refresh_kalshi_watchlist_stats.py) is the DOMINANT Apify cost sink
    # (~$196/period, ~135k events).  This button covers only the SUNDAY
    # SEED (`seed_kalshi_watchlist_deep.py`), which is the smaller driver
    # (~$3-5/run, warm-cache even less).  The paired ops step —
    # `systemctl disable --now trading-corp-watchlist-deep.timer` (root)
    # — is operator-only once this button is live.  The daily stats timer
    # is the bigger lever and has been DEFERRED per operator decision:
    # address `trading-corp-watchlist-stats.timer` before raising the
    # Apify spend cap.

    def _discovery_control_html(*, running: bool, last_run: dict | None) -> str:
        """Render the Run Discovery button + status fragment.

        Returns a self-contained `<div id="kalshi-discovery-control">` that
        either polls (when running) or shows last-run telemetry + a ready
        button (when idle).
        """
        if running:
            return (
                '<div id="kalshi-discovery-control"'
                ' hx-get="/api/kalshi/watchlist/discover/status"'
                ' hx-trigger="every 3s"'
                ' hx-target="this"'
                ' hx-swap="outerHTML">'
                '<button disabled'
                ' class="px-2 py-1 text-[10px] font-mono uppercase tracking-wider'
                ' border border-muted/40 text-muted/40 cursor-not-allowed">'
                'Discovery running…'
                '</button>'
                '</div>'
            )
        # Idle / complete
        btn = (
            '<button'
            ' hx-post="/api/kalshi/watchlist/discover"'
            ' hx-target="#kalshi-discovery-control"'
            ' hx-swap="outerHTML"'
            ' hx-confirm="Run whale discovery? Triggers a paid Apify scrape (~$3-5 warm, ~$5-15 cold)."'
            ' class="px-2 py-1 text-[10px] font-mono uppercase tracking-wider'
            ' border border-warn/60 text-warn/80 hover:bg-warn/10 hover:text-warn">'
            'Run Discovery'
            '</button>'
        )
        if last_run:
            ts = last_run.get("ts", "")[:19].replace("T", " ") if last_run.get("ts") else "—"
            cost = last_run.get("est_cost_usd")
            found = last_run.get("whales_found", 0)
            cost_str = f"~${cost:.2f}" if cost is not None else "?"
            telemetry = (
                f'<span class="text-[10px] font-mono text-muted/60 ml-2">'
                f'last: {ts} · {found} found · {cost_str}'
                f'</span>'
            )
        else:
            telemetry = ""
        return (
            f'<div id="kalshi-discovery-control" class="flex items-center gap-1">'
            f'{btn}{telemetry}'
            f'</div>'
        )

    @app.post("/api/kalshi/watchlist/discover", response_class=HTMLResponse)
    async def kalshi_watchlist_discover():
        """Launch whale discovery as a background task (Sunday seed, async).

        Immediately returns the 'running' fragment so the page stays
        responsive. The discovery coroutine runs in the background and
        writes `discovery_run_status` + `discovery_last_run` to agent_state
        when complete.

        Cost: ~$3-5 per run with warm 30-day cache; ~$5-15 cold.
        NOTE: the dominant cost sink is the DAILY stats refresh timer
        (trading-corp-watchlist-stats.timer) — see comment above this block.
        """
        from trading_corp.utils.secrets import load_secrets
        from trading_corp.scripts.seed_kalshi_watchlist_deep import deep_seed_watchlist

        db_url = deps.db_url

        # Idempotency: if a run is already in-flight, return the running
        # fragment without launching another task.
        existing = _db_mod.load_agent_state(
            "kalshi_copy_trader", "discovery_run_status", db_url=db_url,
        )
        if existing is not None and isinstance(existing[0], dict):
            if existing[0].get("state") == "running":
                log.info("kalshi_discovery: already running — returning in-flight fragment")
                return HTMLResponse(_discovery_control_html(running=True, last_run=None))

        secrets = load_secrets()
        if not secrets.apify_api_token:
            log.warning("kalshi_discovery: APIFY_API_TOKEN not set — cannot run")
            return HTMLResponse(
                '<div id="kalshi-discovery-control">'
                '<span class="text-[10px] font-mono text-loss/80">'
                'Error: APIFY_API_TOKEN not set'
                '</span></div>'
            )

        # Mark as running BEFORE launching the task so the status endpoint
        # returns "running" even if the task yields immediately.
        _db_mod.set_agent_state(
            "kalshi_copy_trader", "discovery_run_status",
            {"state": "running", "started_iso": _now_iso()},
            db_url=db_url,
        )

        async def _run_discovery():
            try:
                summary = await deep_seed_watchlist(
                    apify_token=secrets.apify_api_token,
                    db_url=db_url,
                )
                # Compute cost telemetry from summary.
                lb_events = sum(
                    int(lb.get("rows") or 0)
                    for lb in summary.get("leaderboards_pulled", [])
                    if not lb.get("error")
                )
                # Profile actor: estimate 30 rows/probe (20 profile + 10 closed
                # position floor; actual varies by handle).
                profile_events = summary.get("newly_probed", 0) * 30
                est_cost = round(profile_events * 0.0015 + lb_events * 0.001, 4)
                _db_mod.set_agent_state(
                    "kalshi_copy_trader", "discovery_last_run",
                    {
                        "ts": _now_iso(),
                        "profile_events": profile_events,
                        "leaderboard_events": lb_events,
                        "est_cost_usd": est_cost,
                        "whales_found": summary.get("found_count", 0),
                    },
                    db_url=db_url,
                )
                _db_mod.set_agent_state(
                    "kalshi_copy_trader", "discovery_run_status",
                    {"state": "complete", "finished_iso": _now_iso()},
                    db_url=db_url,
                )
                log.info(
                    "kalshi_discovery: complete — %d whales found, ~$%.4f est cost",
                    summary.get("found_count", 0), est_cost,
                )
            except Exception as exc:
                log.error("kalshi_discovery background task failed: %s", exc, exc_info=True)
                _db_mod.set_agent_state(
                    "kalshi_copy_trader", "discovery_run_status",
                    {"state": "error", "error": str(exc), "finished_iso": _now_iso()},
                    db_url=db_url,
                )

        asyncio.create_task(_run_discovery())
        log.info("kalshi_discovery: background task launched")
        return HTMLResponse(_discovery_control_html(running=True, last_run=None))

    @app.get("/api/kalshi/watchlist/discover/status", response_class=HTMLResponse)
    async def kalshi_watchlist_discover_status():
        """Poll endpoint for the Run Discovery button.

        Returns the running fragment (with hx-trigger polling) when a
        discovery is in-flight, or the idle fragment (button enabled +
        last-run telemetry) when complete/idle.
        """
        db_url = deps.db_url
        status_rec = _db_mod.load_agent_state(
            "kalshi_copy_trader", "discovery_run_status", db_url=db_url,
        )
        last_run_rec = _db_mod.load_agent_state(
            "kalshi_copy_trader", "discovery_last_run", db_url=db_url,
        )
        status = (status_rec[0] if status_rec and isinstance(status_rec[0], dict) else {})
        last_run = (last_run_rec[0] if last_run_rec and isinstance(last_run_rec[0], dict) else None)
        running = status.get("state") == "running"
        return HTMLResponse(_discovery_control_html(running=running, last_run=last_run))

    @app.post("/api/polymarket/watchlist/promote/{proxy_wallet}", response_class=HTMLResponse)
    async def polymarket_watchlist_promote(proxy_wallet: str):
        """Promote a Polymarket whale from watch_only_whales → selected_whales.

        Mutations:
          - selected_whales (list[dict {wallet, user_name, ...}]): append (idempotent on wallet)
          - pinned_whales   (list[dict): append (idempotent on wallet)
          - watch_only_whales (list[dict {proxy_wallet, ...}]): remove
        """
        db_url = deps.db_url
        wallet_lower = proxy_wallet.lower()

        # Read user_name / category from the existing watch_only_whales entry
        # (if present) so we can stamp them onto selected/pinned. We do NOT
        # delete the watch list entry — the panel filters on selected_whales
        # membership at render time instead, which preserves the original
        # leaderboard stats for if/when the whale is demoted.
        wo_rec = _db_mod.load_agent_state(
            "polymarket_copy_trader", "watch_only_whales", db_url=db_url,
        )
        watch_only: list[dict] = list(wo_rec[0]) if wo_rec and isinstance(wo_rec[0], list) else []
        existing = next(
            (w for w in watch_only
             if isinstance(w, dict)
             and str(w.get("proxy_wallet") or "").lower() == wallet_lower),
            None,
        )
        user_name = (existing or {}).get("user_name", "") if existing else ""
        best_category = (existing or {}).get("best_category", "") if existing else ""

        sel_rec = _db_mod.load_agent_state(
            "polymarket_copy_trader", "selected_whales", db_url=db_url,
        )
        selected: list[dict] = list(sel_rec[0]) if sel_rec and isinstance(sel_rec[0], list) else []
        if not any(isinstance(s, dict) and str(s.get("wallet") or s.get("proxy_wallet") or "").lower() == wallet_lower for s in selected):
            selected.append({
                "wallet": wallet_lower, "user_name": user_name,
                "category": best_category, "promoted_iso": _now_iso(),
                "source": "dashboard_button",
            })

        pin_rec = _db_mod.load_agent_state(
            "polymarket_copy_trader", "pinned_whales", db_url=db_url,
        )
        pinned: list[dict] = list(pin_rec[0]) if pin_rec and isinstance(pin_rec[0], list) else []
        if not any(isinstance(p, dict) and str(p.get("wallet") or p.get("proxy_wallet") or "").lower() == wallet_lower for p in pinned):
            pinned.append({
                "wallet": wallet_lower, "user_name": user_name,
                "category": best_category, "promoted_iso": _now_iso(),
                "source": "dashboard_button",
            })

        _db_mod.set_agent_state("polymarket_copy_trader", "selected_whales", selected, db_url=db_url)
        _db_mod.set_agent_state("polymarket_copy_trader", "pinned_whales", pinned, db_url=db_url)

        if deps.logger_agent is not None:
            deps.logger_agent.log_event(
                "polymarket_copy_trader", "polymarket_whale_promoted",
                {"strategy": "polymarket_copy_trader",
                 "division": "polymarket_copy_trading",
                 "wallet": wallet_lower, "user_name": user_name,
                 "promoted_iso": _now_iso(),
                 "source": "dashboard_button"},
            )
        log.info("polymarket_whale_promoted: %s (%s)", wallet_lower[:10], user_name)
        label = user_name or wallet_lower[:10]
        return _render_action_pill(f"@{label} promoted")

    @app.post("/api/polymarket/whales/demote/{proxy_wallet}", response_class=HTMLResponse)
    async def polymarket_whales_demote(proxy_wallet: str):
        """Demote a Polymarket whale: stop copy-trading + flatten paper book.

        Calls `polymarket_copy_trader.force_close_whale_positions` to emit
        synthetic SELL audits for every tracked open position, then moves
        the entry from selected_whales/pinned_whales back to
        watch_only_whales.
        """
        from trading_corp.agents.strategies import polymarket_copy_trader
        db_url = deps.db_url
        wallet_lower = proxy_wallet.lower()

        close_summary = polymarket_copy_trader.force_close_whale_positions(
            wallet_lower, db_url=db_url, logger_agent=deps.logger_agent,
        )

        sel_rec = _db_mod.load_agent_state(
            "polymarket_copy_trader", "selected_whales", db_url=db_url,
        )
        selected: list[dict] = list(sel_rec[0]) if sel_rec and isinstance(sel_rec[0], list) else []
        # Capture the user_name from the selected list so we can preserve
        # display identity in watch_only after demotion.
        existing = next(
            (s for s in selected
             if isinstance(s, dict)
             and str(s.get("wallet") or s.get("proxy_wallet") or "").lower() == wallet_lower),
            None,
        )
        user_name = (existing or {}).get("user_name", "") if existing else ""

        selected_after = [
            s for s in selected
            if not (isinstance(s, dict)
                    and str(s.get("wallet") or s.get("proxy_wallet") or "").lower() == wallet_lower)
        ]

        pin_rec = _db_mod.load_agent_state(
            "polymarket_copy_trader", "pinned_whales", db_url=db_url,
        )
        pinned: list[dict] = list(pin_rec[0]) if pin_rec and isinstance(pin_rec[0], list) else []
        pinned_after = [
            p for p in pinned
            if not (isinstance(p, dict)
                    and str(p.get("wallet") or p.get("proxy_wallet") or "").lower() == wallet_lower)
        ]

        # We intentionally do NOT mutate watch_only_whales here. The watch
        # list panel includes anyone in watch_only_whales who is NOT in
        # selected_whales. By only updating selected_whales/pinned_whales,
        # a previously-promoted whale falls back to its original watch list
        # entry (with original leaderboard PnL, win-rate, etc.) automatically
        # — no API refetch needed.

        _db_mod.set_agent_state("polymarket_copy_trader", "selected_whales", selected_after, db_url=db_url)
        _db_mod.set_agent_state("polymarket_copy_trader", "pinned_whales", pinned_after, db_url=db_url)

        if deps.logger_agent is not None:
            deps.logger_agent.log_event(
                "polymarket_copy_trader", "polymarket_whale_demoted",
                {"strategy": "polymarket_copy_trader",
                 "division": "polymarket_copy_trading",
                 "wallet": wallet_lower, "user_name": user_name,
                 "demoted_iso": _now_iso(),
                 "source": "dashboard_button",
                 "n_synthetic_sells": close_summary.get("n_closed", 0),
                 "positions_closed": close_summary.get("positions", [])},
            )
        log.info(
            "polymarket_whale_demoted: %s (n_synthetic_sells=%d)",
            wallet_lower[:10], close_summary.get("n_closed", 0),
        )
        n = close_summary.get("n_closed", 0)
        suffix = f" · closed {n} position{'s' if n != 1 else ''}" if n else ""
        label = user_name or wallet_lower[:10]
        return _render_action_pill(f"@{label} demoted{suffix}")

    # ── Polymarket whale on-demand audit (Phase B, read-only review) ────

    @app.post(
        "/api/polymarket/watchlist/analyze/{proxy_wallet}",
        response_class=HTMLResponse,
    )
    async def polymarket_watchlist_analyze(
        proxy_wallet: str, request: Request, force: int = 0,
    ):
        """Run the per-whale audit on demand from a dashboard button.

        Returns a rendered `partials/analyze_whale_result.html` fragment
        that htmx swaps into a sibling `<tr>` under the whale's row. The
        fragment shows the same six sections as the
        `analyze_polymarket_whale` CLI: clustering, sell footprint, edge
        profile, category concentration, REDEEM-grounded realized PnL,
        and the Haiku-narrated verdict (or a reasoned-null when the LLM
        is disabled / unavailable / capped / errored).

        Read-only: NO writes to promotion-relevant slots
        (`watch_only_whales`, `selected_whales`, `pinned_whales`,
        `metrics_epoch`). The audit-cache namespace
        (`polymarket_whale_analyst`) is structurally isolated — see
        `polymarket_whale_audit_cache.py` for the invariant.

        Query: `?force=1` → evict the cache entry first (the "Re-analyze"
        link inside the rendered partial uses this).

        Telemetry: emits one `polymarket_whale_analyzed` audit_event
        with `source="dashboard"`, `wallet`, `user_name`, `cache_hit`,
        `llm_cost_usd`, `llm_tokens_in/out`, `verdict_emitted`,
        `null_reason`, `duration_ms`.
        """
        from trading_corp.agents.polymarket_whale_analyst import WhaleAnalyst
        from trading_corp.agents.research.polymarket_whale_audit_cache import (
            evict_audit, read_audit, write_audit,
        )
        from trading_corp.data.polymarket_data_api_client import (
            PolymarketDataAPIClient, PolymarketDataAPIError,
        )
        from trading_corp.data.polymarket_whale_audit import (
            DEFAULT_PARTIAL_SELL_THRESHOLD, build_audit_report,
        )

        wallet_lower = proxy_wallet.lower()
        # Validate wallet shape early. The dashboard hands us the slot's
        # `proxy_wallet` field directly, so this is defensive; we still
        # want a clean error render rather than a 500.
        import re as _re
        if not _re.match(r"^0x[0-9a-f]{40}$", wallet_lower):
            return HTMLResponse(
                f'<div class="text-loss text-xs font-mono p-2 border border-loss/40">'
                f'invalid wallet format: {proxy_wallet[:20]}…'
                f'</div>'
            )

        db_url = deps.db_url
        started = datetime.now(timezone.utc)
        cache_hit = False
        report = None

        try:
            async with PolymarketDataAPIClient() as client:
                # 1. Peek for activity_max_ts (cache key)
                head = await client.fetch_activity(
                    wallet_lower, limit=1, offset=0,
                )
                if not head:
                    raise RuntimeError(
                        "no activity rows found — wallet has no Polymarket "
                        "trades, or the data-api is rate-limited (try later)."
                    )
                activity_max_ts = head[0].timestamp

                # 2. Cache lookup (or force-evict)
                if force:
                    evict_audit(wallet_lower, activity_max_ts, db_url=db_url)
                else:
                    cached = read_audit(
                        wallet_lower, activity_max_ts, db_url=db_url,
                    )
                    if cached is not None:
                        report = cached
                        cache_hit = True

                # 3. Cache miss → full fetch + compute
                if report is None:
                    activity: list = []
                    for page_idx in range(10):  # 10 pages × 500 = 5000 row ceiling
                        offset = page_idx * 500
                        try:
                            page = await client.fetch_activity(
                                wallet_lower, limit=500, offset=offset,
                            )
                        except PolymarketDataAPIError as e:
                            log.warning(
                                "analyze: activity fetch fail offset=%d "
                                "wallet=%s: %s — stopping",
                                offset, wallet_lower[:10], e,
                            )
                            break
                        if not page:
                            break
                        activity.extend(page)
                        if len(page) < 500:
                            break

                    if not activity:
                        raise RuntimeError(
                            "no activity rows after full fetch"
                        )
                    activity_max_ts = max(
                        activity_max_ts,
                        max(a.timestamp for a in activity),
                    )
                    unique_cids = {
                        a.condition_id for a in activity if a.condition_id
                    }
                    resolutions = await client.fetch_market_resolutions(
                        list(unique_cids),
                    )
                    # Best-effort leaderboard lookup (top-50, no pagination)
                    leaderboard_entry = None
                    try:
                        lb_rows = await client.fetch_leaderboard(
                            category=None, limit=50, offset=0,
                        )
                        for lb_r in lb_rows:
                            if lb_r.proxy_wallet.lower() == wallet_lower:
                                leaderboard_entry = lb_r
                                break
                    except PolymarketDataAPIError:
                        pass

            # No more network from here — pure compute + LLM
            if report is None:
                report = build_audit_report(
                    leaderboard_entry=leaderboard_entry,
                    activity_rows=activity,
                    resolutions=resolutions,
                    proxy_wallet=wallet_lower,
                    partial_sell_threshold=DEFAULT_PARTIAL_SELL_THRESHOLD,
                )
                # Run the narrator
                analyst = WhaleAnalyst(
                    narrator_enabled=True,
                    db_url=db_url,
                )
                narration_result = await analyst.narrate(report)
                import dataclasses as _dc
                report = _dc.replace(
                    report,
                    verdict_narration=narration_result.narration,
                    verdict_null_reason=narration_result.null_reason,
                    llm_cost_usd=narration_result.cost_usd,
                    llm_tokens_in=narration_result.tokens_in,
                    llm_tokens_out=narration_result.tokens_out,
                )
                # Cache the fresh result for future cache-hit reads
                # (shared with the CLI; same cache key, same namespace)
                write_audit(report, db_url=db_url)

        except RuntimeError as e:
            # Surface a render-able error fragment rather than a 500 —
            # the htmx swap target still gets useful content.
            return HTMLResponse(
                f'<div class="text-loss text-xs font-mono p-3 '
                f'border border-loss/40 bg-loss/5">'
                f'<div class="font-semibold mb-1">Analyze failed</div>'
                f'<div class="text-muted/80">{str(e)[:200]}</div>'
                f'<div class="text-muted/60 mt-1 text-[10px]">'
                f'wallet {wallet_lower[:18]}… · '
                f'{(datetime.now(timezone.utc) - started).total_seconds():.1f}s'
                f'</div></div>'
            )
        except Exception as e:
            log.exception("analyze: unexpected failure wallet=%s", wallet_lower[:10])
            return HTMLResponse(
                f'<div class="text-loss text-xs font-mono p-3 '
                f'border border-loss/40 bg-loss/5">'
                f'<div class="font-semibold">Analyze errored — check logs.</div>'
                f'<div class="text-muted/70 text-[10px] mt-1">{type(e).__name__}</div>'
                f'</div>'
            )

        duration_s = (datetime.now(timezone.utc) - started).total_seconds()

        # Telemetry — single audit_event row per call
        if deps.logger_agent is not None:
            try:
                deps.logger_agent.log_event(
                    "polymarket_copy_trader", "polymarket_whale_analyzed",
                    {
                        "strategy": "polymarket_copy_trader",
                        "division": "polymarket_copy_trading",
                        "source": "dashboard",
                        "wallet": wallet_lower,
                        "user_name": report.user_name,
                        "cache_hit": cache_hit,
                        "force_requested": bool(force),
                        "llm_cost_usd": report.llm_cost_usd,
                        "llm_tokens_in": report.llm_tokens_in,
                        "llm_tokens_out": report.llm_tokens_out,
                        "verdict_emitted": report.verdict_narration is not None,
                        "verdict_null_reason": report.verdict_null_reason,
                        "duration_ms": int(duration_s * 1000),
                        "n_resolved_decisions": report.n_resolved_decisions,
                        "clustering_ratio": report.clustering.clustering_ratio,
                        "pnl_inflation_ratio": report.realized_pnl.pnl_inflation_ratio,
                    },
                )
            except Exception:
                # Never fail the response on a logging hiccup.
                log.exception("analyze: failed to emit audit_event")

        return templates.TemplateResponse(
            request,
            "partials/analyze_whale_result.html",
            {
                "report": report,
                "cache_hit": cache_hit,
                "duration_s": duration_s,
            },
        )

    # ── Research firm dashboard (v3 — read-only) ─────────────────────────

    @app.get("/research", response_class=HTMLResponse)
    async def research_view(request: Request):
        """Research firm landing — engagement log, recommendation outcomes
        view, latency view (P50/P95/P99 + weekly P95 time-series).

        Reads from `audit_event` rows with `actor='research_firm'`. The
        engagement runner writes the canonical product row before any
        routing branch, so this view always reflects ground truth.
        """
        snap = await data.build_command_center(deps)
        view = _build_research_view(deps)
        return templates.TemplateResponse(
            request, "research.html",
            {"snap": snap, "view": view},
        )


def _build_research_view(deps) -> dict:
    """Pull recent research-firm audit rows and shape them for the template.

    Returns a dict with:
      - engagement_log: chronological list of engagement summaries
      - engagements_by_id: per-engagement bundle (spec + reports + product)
      - outcomes: per-engagement act_rate joined with division-side
        candidate_acted_on / candidate_skipped rows (Phase 1a-2 will
        populate the act/skip rows; Phase 1a-1 surface is here so the
        view is in place when scout integration lands)
      - latency: P50/P95/P99 of engagement duration grouped by
        product_type + asset_class, plus weekly P95 time-series
        (Refinement 5)
    """
    if deps.logger_agent is None:
        return _empty_research_view()
    try:
        events = deps.logger_agent.recent_events(limit=600)
    except Exception as e:
        log.warning("research view: failed to read audit log: %s", e)
        return _empty_research_view()

    research_rows = [e for e in events if (e.get("actor") == "research_firm")]

    # Per-engagement bundle: keyed by engagement_id, collects all rows
    # for one engagement so the UI can render the full thread.
    engagements_by_id: dict[str, dict] = {}
    for e in research_rows:
        payload = e.get("payload") or {}
        eid = payload.get("engagement_id")
        if not eid:
            continue
        bundle = engagements_by_id.setdefault(eid, {
            "engagement_id": eid,
            "first_ts": e.get("ts"),
            "last_ts": e.get("ts"),
            "rows": [],
            "product_type": payload.get("product_type"),
            "asset_class": payload.get("asset_class"),
            "requesting_division": payload.get("requesting_division"),
            "product": None,
            "final_status": None,
            "duration_seconds": None,
        })
        bundle["rows"].append({
            "ts": e.get("ts"),
            "kind": e.get("kind"),
            "payload": payload,
        })
        # Keep the earliest ts as first_ts (events are returned newest-first).
        if (e.get("ts") or "") < (bundle["first_ts"] or "ZZZZ"):
            bundle["first_ts"] = e.get("ts")
        if (e.get("ts") or "") > (bundle["last_ts"] or ""):
            bundle["last_ts"] = e.get("ts")

        kind = e.get("kind", "")
        if kind in _TERMINAL_KINDS:
            bundle["final_status"] = kind
            started = payload.get("engagement_started_ts")
            completed = payload.get("engagement_completed_ts")
            if started and completed:
                bundle["duration_seconds"] = _duration_seconds(started, completed)
        if kind == "research_candidate_recommendation_emitted":
            bundle["product"] = payload.get("product")
        if kind == "research_thesis_emitted":
            bundle["product"] = payload.get("product")

    engagement_log: list[dict] = []
    for e in research_rows[:120]:
        payload = e.get("payload") or {}
        engagement_log.append({
            "ts": e.get("ts"),
            "kind": e.get("kind"),
            "engagement_id": payload.get("engagement_id"),
            "requesting_division": payload.get("requesting_division"),
            "product_type": payload.get("product_type"),
            "asset_class": payload.get("asset_class"),
            "summary": _summary_for_event(e),
            "payload_pretty": json.dumps(payload, indent=2, default=str, sort_keys=True),
        })

    # ── Recommendation outcomes view (design §7.2) ──
    # Engagement-side rows joined to division-side acted_on/skipped rows
    # (Phase 1a-2 will write those). The structure is in place now so the
    # view doesn't disappear when scout integration lands.
    division_rows = [
        e for e in events
        if e.get("kind") in ("research_candidate_acted_on",
                             "research_candidate_skipped")
    ]
    outcomes_by_eid: dict[str, dict] = {}
    for e in division_rows:
        payload = e.get("payload") or {}
        eid = payload.get("engagement_id")
        if not eid:
            continue
        slot = outcomes_by_eid.setdefault(eid, {
            "engagement_id": eid,
            "acted_on": 0,
            "skipped": 0,
            "skip_reasons": {},
        })
        if e.get("kind") == "research_candidate_acted_on":
            slot["acted_on"] += 1
        else:
            slot["skipped"] += 1
            reason = (payload.get("reason") or "unspecified")
            slot["skip_reasons"][reason] = slot["skip_reasons"].get(reason, 0) + 1

    outcomes: list[dict] = []
    for eid, slot in outcomes_by_eid.items():
        bundle = engagements_by_id.get(eid)
        total = slot["acted_on"] + slot["skipped"]
        outcomes.append({
            "engagement_id": eid,
            "requesting_division": (bundle or {}).get("requesting_division"),
            "product_type": (bundle or {}).get("product_type"),
            "ts": (bundle or {}).get("last_ts"),
            "acted_on": slot["acted_on"],
            "skipped": slot["skipped"],
            "total": total,
            "act_rate": (slot["acted_on"] / total) if total else 0.0,
            "skip_reasons": slot["skip_reasons"],
        })
    outcomes.sort(key=lambda r: r["ts"] or "", reverse=True)

    # ── Latency view (design §7.3 + Refinement 5) ──
    latency = _build_latency_view(engagements_by_id)

    # ── Thesis library (design §7.4 — Phase 1b) ──
    # Read-only list of all emitted Theses, newest-first. Each row carries
    # the full product dict (summary, drivers, risks, earnings flag) so
    # the template can expand details inline without a second fetch.
    theses: list[dict] = []
    for e in research_rows:
        if e.get("kind") != "research_thesis_emitted":
            continue
        payload = e.get("payload") or {}
        product = payload.get("product") or {}
        symbol = product.get("symbol")
        if not symbol:
            continue
        theses.append({
            "ts": e.get("ts"),
            "engagement_id": payload.get("engagement_id"),
            "symbol": symbol,
            "asset_class": payload.get("asset_class"),
            "summary": product.get("summary") or "",
            "key_drivers": product.get("key_drivers") or [],
            "key_risks": product.get("key_risks") or [],
            "earnings_window_clear": product.get("earnings_window_clear"),
            "cost_dollars": payload.get("cost_dollars"),
        })
    # research_rows is already newest-first, so theses inherits that order.

    # ── PositionContext audit trail (design §7.5 — Phase 1d) ──
    # Read-only audit-trail view: what the research firm told each
    # division and when. Required by design Q7's "universal-write" rule —
    # the audit row is written even when the consuming agent never
    # surfaces the result, and the dashboard is the surface that makes
    # it visible.
    position_contexts: list[dict] = []
    for e in research_rows:
        if e.get("kind") != "research_position_context_emitted":
            continue
        payload = e.get("payload") or {}
        product = payload.get("product") or {}
        symbol = product.get("symbol")
        if not symbol:
            continue
        position_contexts.append({
            "ts": e.get("ts"),
            "engagement_id": payload.get("engagement_id"),
            "symbol": symbol,
            "requesting_division": product.get("requesting_division"),
            "asset_class": payload.get("asset_class"),
            "time_horizon_hours": product.get("time_horizon_hours"),
            "macro_summary": product.get("macro_summary") or "",
            "sentiment_summary": product.get("sentiment_summary") or "",
            "risk_flags": product.get("risk_flags") or [],
            "confidence_score": product.get("confidence_score"),
            "cost_dollars": payload.get("cost_dollars"),
        })

    return {
        "engagement_log": engagement_log,
        "engagements_by_id": engagements_by_id,
        "engagement_count": len(engagements_by_id),
        "outcomes": outcomes,
        "latency": latency,
        "theses": theses,
        "position_contexts": position_contexts,
        "pmcc_validation": _build_pmcc_validation_view(deps),
    }


def _empty_research_view() -> dict:
    return {
        "engagement_log": [],
        "engagements_by_id": {},
        "engagement_count": 0,
        "outcomes": [],
        "latency": {"groups": [], "weekly": []},
        "theses": [],
        "position_contexts": [],
        "pmcc_validation": _empty_pmcc_validation_view(),
    }


# ── PMCC research-as-consultant validation view (2026-05-02 realignment) ──
# Read-only review surface that joins:
#   research_candidate_recommendation_emitted  (per engagement; candidate list)
#   research_candidate_acted_on / _skipped     (per-candidate division outcome)
#   proposed_order.status                      (downstream lifecycle for acted_on)
#
# The realignment memo phrased the 05-05 criterion as "count of candidates
# that produced `would_have_placed` rows." That kind is Otter/Cypher-only
# (the webhook path writes it). PMCC's HITL flow runs through LangGraph
# and goes proposed → risk_approved → board_approved → filled — there is
# no `would_have_placed` row on this path. This view surfaces the actual
# proposed_order lifecycle status for each acted_on candidate, which is
# the equivalent decision-quality signal.
PMCC_OBSERVATION_PERIOD_START = "2026-05-02T00:00:00Z"


def _build_pmcc_validation_view(deps) -> dict:
    if deps.logger_agent is None:
        return _empty_pmcc_validation_view()
    try:
        events = deps.logger_agent.events_since(PMCC_OBSERVATION_PERIOD_START)
    except Exception as e:
        log.warning("pmcc validation view: failed to read audit log: %s", e)
        return _empty_pmcc_validation_view()

    # Engagement-level rows: one per PMCC research_candidate_recommendation_emitted.
    emitted_by_eid: dict[str, dict] = {}
    for e in events:
        if e.get("actor") != "research_firm":
            continue
        if e.get("kind") != "research_candidate_recommendation_emitted":
            continue
        payload = e.get("payload") or {}
        if payload.get("requesting_division") != "robinhood_pmcc":
            continue
        eid = payload.get("engagement_id")
        if not eid:
            continue
        product = payload.get("product") or {}
        emitted_by_eid[eid] = {
            "engagement_id": eid,
            "ts": e.get("ts"),
            "candidates": product.get("candidates") or [],
        }

    # Per-candidate division outcome rows, keyed by (engagement_id, symbol).
    # Symbol is the join key because acted_on/skipped both carry it; the
    # per-engagement candidate_index is also stable but symbol is what the
    # Board reads first.
    outcome_rows: dict[tuple[str, str], dict] = {}
    for e in events:
        if e.get("actor") != "robinhood_pmcc":
            continue
        kind = e.get("kind")
        if kind not in ("research_candidate_acted_on", "research_candidate_skipped"):
            continue
        payload = e.get("payload") or {}
        eid = payload.get("engagement_id")
        symbol = (payload.get("symbol") or "").upper()
        if not eid or not symbol:
            continue
        outcome_rows[(eid, symbol)] = {
            "kind": kind,
            "ts": e.get("ts"),
            "skip_reason": payload.get("reason"),
            "proposed_order_id": payload.get("proposed_order_id"),
        }

    # Bulk lookup of proposed_order.status for the acted_on rows' order ids.
    order_ids = [
        r["proposed_order_id"] for r in outcome_rows.values()
        if r["kind"] == "research_candidate_acted_on" and r.get("proposed_order_id")
    ]
    order_statuses = _lookup_order_statuses(deps, order_ids)

    engagements: list[dict] = []
    n_acted_on = 0
    n_skipped = 0
    n_board_approved_or_filled = 0
    n_filled = 0
    n_candidates_total = 0
    skip_reasons: dict[str, int] = {}

    for eid, eng in emitted_by_eid.items():
        candidates_view = []
        for cand in eng["candidates"]:
            sym = (cand.get("symbol") or "").upper()
            row = outcome_rows.get((eid, sym))
            if row is None:
                status = "no_outcome"
                skip_reason = None
                order_status = None
                proposed_order_id = None
            elif row["kind"] == "research_candidate_acted_on":
                status = "acted_on"
                skip_reason = None
                n_acted_on += 1
                proposed_order_id = row.get("proposed_order_id")
                order_status = order_statuses.get(proposed_order_id)
                if order_status in ("board_approved", "filled"):
                    n_board_approved_or_filled += 1
                if order_status == "filled":
                    n_filled += 1
            else:
                status = "skipped"
                skip_reason = row.get("skip_reason")
                n_skipped += 1
                proposed_order_id = None
                order_status = None
                if skip_reason:
                    skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
            candidates_view.append({
                "symbol": sym,
                "thesis": cand.get("thesis") or "",
                "conviction": cand.get("conviction"),
                "fit_score": cand.get("fit_score"),
                "fit_rationale": cand.get("fit_rationale") or "",
                "status": status,
                "skip_reason": skip_reason,
                "proposed_order_id": proposed_order_id,
                "order_status": order_status,
            })
            n_candidates_total += 1
        engagements.append({
            "engagement_id": eid,
            "ts": eng["ts"],
            "n_candidates": len(eng["candidates"]),
            "candidates": candidates_view,
        })
    engagements.sort(key=lambda r: r["ts"] or "", reverse=True)

    top_skip_reasons = sorted(skip_reasons.items(), key=lambda kv: kv[1], reverse=True)

    return {
        "observation_start": PMCC_OBSERVATION_PERIOD_START,
        "n_engagements": len(emitted_by_eid),
        "n_candidates": n_candidates_total,
        "n_acted_on": n_acted_on,
        "n_skipped": n_skipped,
        "n_board_approved_or_filled": n_board_approved_or_filled,
        "n_filled": n_filled,
        "top_skip_reasons": [{"reason": r, "count": c} for r, c in top_skip_reasons],
        "engagements": engagements,
    }


def _lookup_order_statuses(deps, order_ids: list[str]) -> dict[str, str]:
    """Bulk lookup proposed_order.status for the given ids.
    Returns {order_id -> status}; missing ids are absent."""
    if not order_ids:
        return {}
    from trading_corp.persistence import db
    out: dict[str, str] = {}
    try:
        with db.connect(deps.logger_agent.db_url) as conn:
            placeholders = ",".join("?" * len(order_ids))
            rows = conn.execute(
                f"SELECT id, status FROM proposed_order WHERE id IN ({placeholders})",
                order_ids,
            ).fetchall()
            for r in rows:
                out[r["id"]] = r["status"]
    except Exception as e:
        log.warning("pmcc validation view: order status lookup failed: %s", e)
    return out


def _empty_pmcc_validation_view() -> dict:
    return {
        "observation_start": PMCC_OBSERVATION_PERIOD_START,
        "n_engagements": 0,
        "n_candidates": 0,
        "n_acted_on": 0,
        "n_skipped": 0,
        "n_board_approved_or_filled": 0,
        "n_filled": 0,
        "top_skip_reasons": [],
        "engagements": [],
    }


_TERMINAL_KINDS = {
    "research_candidate_recommendation_emitted",
    "research_trade_confirmation_emitted",
    "research_position_context_emitted",
    "research_thesis_emitted",
    "research_engagement_aborted_kill_switch",
    "research_engagement_aborted_out_of_scope",
    "research_engagement_validation_failed",
    "research_engagement_no_action",
}


def _duration_seconds(started_iso: str, completed_iso: str) -> float | None:
    """Compute duration in seconds from two ISO timestamps. Returns None
    on parse failure."""
    try:
        started = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_iso.replace("Z", "+00:00"))
        return max(0.0, (completed - started).total_seconds())
    except Exception:
        return None


def _percentile(values: list[float], pct: float) -> float | None:
    """Naive percentile (linear interpolation between adjacent ranks).
    Returns None on empty input. Avoids importing statistics/numpy so
    the dashboard stays light."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _build_latency_view(engagements_by_id: dict[str, dict]) -> dict:
    """Compute P50/P95/P99 by (product_type, asset_class) plus a weekly
    P95 time-series per group (Refinement 5)."""
    durations: dict[tuple[str, str], list[float]] = {}
    weekly_durations: dict[tuple[str, str, str], list[float]] = {}
    for bundle in engagements_by_id.values():
        d = bundle.get("duration_seconds")
        if d is None:
            continue
        ptype = bundle.get("product_type") or "unknown"
        aclass = bundle.get("asset_class") or "unknown"
        durations.setdefault((ptype, aclass), []).append(float(d))
        # Weekly bucket: ISO year-week, computed from last_ts.
        last_ts = bundle.get("last_ts") or ""
        try:
            dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            year, week, _ = dt.isocalendar()
            week_key = f"{year}-W{week:02d}"
        except Exception:
            week_key = "unknown"
        weekly_durations.setdefault((ptype, aclass, week_key), []).append(float(d))

    groups = []
    for (ptype, aclass), vals in sorted(durations.items()):
        groups.append({
            "product_type": ptype,
            "asset_class": aclass,
            "n": len(vals),
            "p50_seconds": _percentile(vals, 50),
            "p95_seconds": _percentile(vals, 95),
            "p99_seconds": _percentile(vals, 99),
        })

    weekly_p95 = []
    for (ptype, aclass, wk), vals in sorted(weekly_durations.items()):
        weekly_p95.append({
            "product_type": ptype,
            "asset_class": aclass,
            "week": wk,
            "n": len(vals),
            "p95_seconds": _percentile(vals, 95),
        })

    return {"groups": groups, "weekly": weekly_p95}


def _summary_for_event(e: dict) -> str:
    """One-line summary of a research audit row for the engagement log."""
    payload = e.get("payload") or {}
    kind = e.get("kind", "")
    if kind == "research_candidate_recommendation_emitted":
        prod = payload.get("product") or {}
        cands = prod.get("candidates") or []
        syms = [c.get("symbol", "") for c in cands]
        return f"candidate rec: {len(syms)} ({', '.join(syms[:5])})"
    if kind == "research_trade_confirmation_emitted":
        prod = payload.get("product") or {}
        return f"trade confirmation: verdict={prod.get('verdict')}"
    if kind == "research_position_context_emitted":
        prod = payload.get("product") or {}
        return f"position context: {prod.get('symbol')}"
    if kind == "research_thesis_emitted":
        prod = payload.get("product") or {}
        return f"thesis: {prod.get('symbol')}"
    if kind == "research_expert_completed":
        return (
            f"{payload.get('expert_role')} OK on {payload.get('symbol')} "
            f"(lean={payload.get('directional_lean')}, "
            f"conf={payload.get('confidence_score', 0):.2f})"
        )
    if kind == "research_expert_refused":
        return f"{payload.get('expert_role')} refused on {payload.get('symbol')}"
    if kind == "research_engagement_started":
        return "engagement started"
    if kind == "research_engagement_aborted_kill_switch":
        return "kill switch present"
    if kind == "research_engagement_aborted_out_of_scope":
        return f"out of scope: {(payload.get('reason') or '')[:80]}"
    if kind == "research_engagement_no_action":
        return f"no action: {(payload.get('reason') or '')[:80]}"
    if kind == "research_engagement_cost_warning":
        return (
            f"cost ${payload.get('cost_so_far_dollars', 0):.2f} "
            f"crossed soft cap ${payload.get('soft_cap_dollars', 0):.2f}"
        )
    if kind == "research_engagement_validation_failed":
        return f"validation failed: {(payload.get('reason') or '')[:80]}"
    if kind == "research_data_fetch_attempted":
        # Experts may write `error=None` explicitly on success-path failures,
        # so `.get('error', '')` returns None — guard before slicing.
        err = (payload.get("error") or "")
        return f"FETCH FAIL: {payload.get('source')} ({err[:60]})"
    return kind


def _render_markdown(md_text: str) -> str:
    """Render LLM markdown analysis to dark-theme-friendly HTML."""
    try:
        import markdown  # type: ignore
    except ImportError:
        # Fallback: just escape and preserve newlines
        return f"<pre class='text-sm whitespace-pre-wrap'>{md_text}</pre>"
    html = markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists"],
    )
    # Wrap with prose styling that works on dark theme
    return (
        '<div class="markdown-content text-sm text-mono leading-relaxed '
        'space-y-2">'
        + html
        + '</div>'
    )


def _deferred_until(logger_agent, slug: str, symbol: str) -> datetime | None:
    """Return the timestamp this pair's deferral expires at, or None.

    A pair is deferred when the most recent `pair_deferred` event on that
    (slug, symbol) is newer than the most recent `pair_resumed` event AND
    its timestamp + TTL is still in the future.
    """
    if logger_agent is None:
        return None
    try:
        events = logger_agent.recent_events(limit=200)
    except Exception:
        return None

    latest_defer_ts: str | None = None
    latest_resume_ts: str | None = None
    for evt in events:
        kind = evt.get("kind")
        if kind not in ("pair_deferred", "pair_resumed"):
            continue
        payload = evt.get("payload") or {}
        if payload.get("slug") != slug:
            continue
        if (payload.get("symbol") or "").upper() != symbol:
            continue
        ts = evt.get("ts") or ""
        if kind == "pair_deferred" and (latest_defer_ts is None or ts > latest_defer_ts):
            latest_defer_ts = ts
        elif kind == "pair_resumed" and (latest_resume_ts is None or ts > latest_resume_ts):
            latest_resume_ts = ts

    if latest_defer_ts is None:
        return None
    if latest_resume_ts is not None and latest_resume_ts > latest_defer_ts:
        return None  # resume is newer than defer → no longer deferred

    try:
        defer_dt = datetime.fromisoformat(latest_defer_ts.replace("Z", "+00:00"))
    except Exception:
        return None
    expires_at = defer_dt + timedelta(hours=_DEFER_TTL_HOURS)
    if expires_at <= datetime.now(timezone.utc):
        return None    # expired
    return expires_at


def _is_deferred(logger_agent, slug: str, symbol: str) -> bool:
    return _deferred_until(logger_agent, slug, symbol) is not None


def _render_deferred_panel(slug: str, symbol: str, fresh: bool, expires_at: datetime | None = None) -> str:
    """Right-panel HTML when a pair has been deferred.

    `fresh=True` is the immediate response after clicking Defer. `fresh=False`
    is shown on subsequent clicks while the deferral is still active.
    `expires_at` (when known) drives the "deferred until" stamp and a small
    countdown for clarity.
    """
    headline = "Deferred · 24h" if fresh else "Recommendation deferred"
    detail = (
        "Recommendation suppressed for 24h. The system won't re-analyze this position "
        "until the deferral expires or you resume manually."
        if fresh else
        "This pair is currently deferred. Click below to clear the deferral and "
        "see fresh analysis."
    )
    expiry_html = ""
    if expires_at is not None:
        # ISO-style stamp + relative phrasing
        remaining = expires_at - datetime.now(timezone.utc)
        if remaining.total_seconds() > 0:
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            if hours >= 1:
                rel = f"in ~{hours}h"
            else:
                rel = f"in ~{minutes}m"
            expiry_html = (
                f'<div class="text-[11px] text-muted/80 font-mono mt-1">'
                f'Auto-resumes {rel} '
                f'<span class="text-muted/60">'
                f'({format_et_full(expires_at)})'
                f'</span></div>'
            )
    return (
        f'<div class="space-y-3">'
        f'<div class="flex items-center gap-2">'
        f'<span class="text-base">⏸</span>'
        f'<span class="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded font-mono '
        f'border bg-edge text-muted border-edge">{headline}</span>'
        f'<span class="text-[11px] text-muted font-mono ml-auto">{symbol}</span>'
        f'</div>'
        f'<div class="text-sm text-muted leading-relaxed">{detail}</div>'
        f'{expiry_html}'
        f'<div class="mt-4 pt-3 border-t border-edge">'
        f'<button '
        f'  hx-post="/division/{slug}/pair/{symbol}/resume" '
        f'  hx-target="#pair-analysis" hx-swap="innerHTML" '
        f'  class="w-full px-3 py-2 rounded-md font-mono text-sm '
        f'         bg-pane-2 text-mono hover:bg-edge-2 transition-colors '
        f'         border border-edge">'
        f'Resume analysis now'
        f'</button>'
        f'<div class="text-[10px] text-muted/60 font-mono mt-1.5 text-center">'
        f'Clears the deferral and runs fresh analysis.'
        f'</div>'
        f'</div>'
        f'</div>'
    )


def _llm_placeholder_html(slug: str) -> str:
    return (
        '<div class="text-muted text-sm italic">'
        f'No expert analysis configured for division <code>{slug}</code>. '
        '(LLM analysis is wired for <code>robinhood_pmcc</code> today; '
        'others coming soon.)'
        '</div>'
    )


def _pair_unavailable_html(symbol: str, reason: str) -> str:
    return (
        f'<div class="text-muted text-sm">'
        f'<div class="font-mono font-semibold text-mono mb-1">{symbol}</div>'
        f'<div class="italic">Analysis unavailable: {reason}</div>'
        f'</div>'
    )


async def _analyze_ira_covered_call(cc, broker, deps) -> tuple[Any, Any]:  # noqa: ANN001
    """Deterministic IRA covered-call analyzer producing the SAME shapes
    PMCC produces (`PMCCAnalysis` + `TradeRecommendation`) so we can
    reuse `_render_pair_analysis` for visual parity.

    Rule-based (no LLM cost/latency). Rules applied:

      R1. **Profit-take ≥85%**: close current short (then sell new weekly).
          Captures most of the premium with negligible remaining decay.
      R2. **Roll up & out when ITM**: avoid assignment — roll to a higher
          strike with longer DTE for a credit.
      R3. **Terminal-DTE (≤2d)**: ITM → roll, OTM → let theta finish.
      R4. **Roll for credit only**: preserves the cost-basis advantage.
          If the next-week credit < current cost-to-close, hold instead.
      R5. **Partial coverage**: warn if shares < contracts×100 (would
          require selling more shares OR closing some contracts on
          assignment).

    Returns:
      (PMCCAnalysis, TradeRecommendation | None) — recommendation is None
      when the action is hold/watch (nothing to execute).
    """
    from trading_corp.agents.divisions.pmcc_robinhood import (
        PMCCAnalysis, TradeLegDetail, TradeRecommendation, _days_to,
    )

    short = cc.short_call
    sym = cc.underlying
    spot = cc.underlying_price or 0.0
    dte = short.dte if short.dte is not None else 0
    contracts = int(abs(short.qty))
    credit_per_sh = short.avg_per_share
    mark_per_sh = short.mark_per_share
    profit_pct = short.unrealized_pnl_pct  # fraction (0.50 = 50%)
    is_itm = cc.is_itm
    breach = (cc.breach_pct or 0.0) * 100  # percent

    # ── Decide action via the rules ──
    action: str
    urgency: str
    confidence: float
    target_strike: float | None = None
    target_dte: int | None = None
    target_delta: float | None = None
    rationale_paras: list[str] = []
    warnings: list[str] = []
    benefits: list[str] = []
    summary: str = ""

    # Rule 1: profit take
    profit85 = profit_pct is not None and profit_pct >= 0.85
    profit70 = profit_pct is not None and profit_pct >= 0.70

    if dte == 0 and is_itm:
        action, urgency, confidence = "roll_short_early", "urgent", 0.95
        target_strike = max(spot * 1.03, short.strike + 0.50)
        target_dte = 7
        summary = (
            f"{sym} expires TODAY ITM by +{breach:.1f}%. Roll up & out "
            f"now to avoid assignment at ${short.strike:.2f}."
        )
        rationale_paras.append(
            f"Rule R3 (terminal-DTE ITM): same-day expiry with shares "
            f"already $-{breach:.1f}% in the money. Without action, the "
            f"shares will be called away at ${short.strike:.2f} at close. "
            f"Roll up & out to next-week's expiry at a higher strike to "
            f"preserve the shares and capture another premium cycle."
        )
        warnings.append("Same-day expiry — must act before market close.")
        warnings.append(
            f"Assignment locks in cost-basis → ${short.strike:.2f} sale "
            f"(intrinsic floor)."
        )
        benefits.append("Avoids forced assignment + share liquidation.")
        benefits.append("Captures next-cycle credit instead of accepting strike sale.")
    elif dte == 0:
        action, urgency, confidence = "hold", "routine", 0.90
        summary = f"{sym} expires today OTM. Let it expire — full premium kept."
        rationale_paras.append(
            "Rule R3 (terminal-DTE OTM): same-day expiry with the short "
            "OTM. Let theta finish the job — the entire remaining "
            f"extrinsic of ${short.extrinsic_per_share or 0:.2f}/sh evaporates today."
        )
        benefits.append(
            f"Captures the full ${credit_per_sh * 100 * contracts:,.2f} credit."
        )
    elif profit85:
        action, urgency, confidence = "close_short", "elevated", 0.85
        target_dte = 7
        target_delta = 0.25
        summary = (
            f"{sym}: short already at {(profit_pct or 0)*100:.0f}% profit. "
            f"Close now + sell next weekly."
        )
        rationale_paras.append(
            f"Rule R1 (≥85% profit-take): {(profit_pct or 0)*100:.0f}% of "
            f"the original credit ${credit_per_sh:.2f}/sh has been "
            f"captured. Remaining extrinsic is small relative to the "
            f"assignment-risk + gap-risk you carry by holding through "
            f"expiry. Close-and-resell harvests the remaining risk-free."
        )
        benefits.append("Locks in ~85% of the original premium with no further holding risk.")
        benefits.append("Frees shares to write a fresh weekly at higher-extrinsic delta.")
    elif dte <= 2 and is_itm:
        action, urgency, confidence = "roll_short_early", "urgent", 0.90
        target_strike = max(spot * 1.03, short.strike + 0.50)
        target_dte = 7
        summary = (
            f"{sym} ITM by +{breach:.1f}% with {dte} DTE — roll up & out "
            "to preserve shares."
        )
        rationale_paras.append(
            f"Rule R2 + R3 (terminal-DTE ITM): short is ${breach:.1f}% in "
            f"the money with only {dte} day{'' if dte == 1 else 's'} until "
            "expiry. Intrinsic dominates extrinsic — limited remaining "
            "time-decay benefit. Roll up & out to a higher-strike, longer-"
            "DTE contract for a net credit to preserve cost-basis advantage."
        )
        warnings.append(
            f"ITM by +{breach:.1f}% — assignment risk if held through expiry."
        )
        warnings.append("Rule R4: roll must be for net credit; otherwise hold instead.")
        benefits.append("Avoids likely assignment + share liquidation.")
        benefits.append("Pushes the short strike up to maintain upside exposure.")
    elif dte <= 2:
        action, urgency, confidence = "hold", "routine", 0.80
        summary = (
            f"{sym} OTM with {dte} DTE — let theta finish, then sell new weekly."
        )
        rationale_paras.append(
            "Rule R3 (terminal-DTE OTM): short is OTM with ≤2 DTE. "
            "Almost all remaining value is extrinsic that will evaporate "
            "by expiry. Holding is the highest-EV move; sell a new "
            "weekly as soon as this one closes."
        )
        benefits.append(
            f"Captures full ${(credit_per_sh - (mark_per_sh or 0)) * 100 * contracts:,.2f} "
            "remaining decay."
        )
    elif profit70:
        action, urgency, confidence = "close_short", "elevated", 0.70
        target_dte = 7
        target_delta = 0.25
        summary = (
            f"{sym}: short at {(profit_pct or 0)*100:.0f}% profit — consider "
            f"close + reset for fresh premium."
        )
        rationale_paras.append(
            f"Rule R1 (early profit-take): {(profit_pct or 0)*100:.0f}% of "
            f"the original ${credit_per_sh:.2f}/sh credit captured. "
            "Remaining extrinsic is small enough that re-selling a fresh "
            "weekly captures more $/day than holding to maturity."
        )
        benefits.append("Opportunistic profit capture.")
        benefits.append("Resets DTE clock for higher remaining-extrinsic exposure.")
    elif is_itm:
        action, urgency, confidence = "watch", "elevated", 0.75
        summary = (
            f"{sym} ITM by +{breach:.1f}% but {dte}d to expiry — monitor; "
            "no urgency yet."
        )
        rationale_paras.append(
            f"Short is ITM by +{breach:.1f}%, but {dte} days remain. "
            "Not yet in the Rule R2/R3 action window (≤2 DTE or assignment-"
            "imminent). Monitor: if spot pulls back below strike, theta "
            "will continue to work. If it stays ITM, plan to roll within "
            "the terminal-DTE window."
        )
        warnings.append(
            f"Position will trigger Rule R2 if still ITM at ≤2 DTE."
        )
    else:
        action, urgency, confidence = "hold", "routine", 0.80
        summary = (
            f"{sym} OTM with {dte}d remaining — let theta work."
        )
        rationale_paras.append(
            f"Short is OTM with {dte}d to expiry — normal time-decay zone. "
            f"At ${credit_per_sh:.2f}/sh credit and ${mark_per_sh or 0:.2f}/sh "
            f"current mark, you've captured {(profit_pct or 0)*100:.0f}% of "
            "the premium with no pressing reason to act. Re-evaluate when "
            "either profit hits 70% (Rule R1) or DTE drops to 2 (Rule R3)."
        )

    # Rule R5: partial coverage warning
    if not cc.is_fully_covered:
        warnings.append(
            f"Coverage only {cc.coverage_pct*100:.0f}% "
            f"({int(cc.shares_qty)} shares vs {contracts*100} required) — "
            "uncovered contracts carry naked-short exposure on assignment."
        )

    # Decide whether to build trade legs. Roll/close actions always do;
    # watch+ITM also builds a HYPOTHETICAL roll preview so the user can
    # see what a defensive roll would look like even though rules say
    # "not yet urgent." hold/OTM cases produce no legs.
    propose_legs = action in ("roll_short_early", "close_short")
    if action == "watch" and is_itm:
        propose_legs = True
        target_strike = max(spot * 1.03, short.strike + 0.50)
        target_dte = 7
        benefits.append(
            "Preview only: rules say WATCH (not yet urgent) — these legs "
            "show what a defensive roll would look like if you wanted to "
            "execute today."
        )

    rationale = "\n\n".join(rationale_paras)

    analysis = PMCCAnalysis(
        symbol=sym,
        action=action,
        confidence=confidence,
        urgency=urgency,
        summary=summary,
        rationale=rationale,
        warnings=warnings,
        target_delta=target_delta,
        target_dte=target_dte,
        target_strike=target_strike,
    )

    if not propose_legs:
        return analysis, None

    legs: list[Any] = []
    next_weekly: dict | None = None

    # BTC current short (we have the data already — no fetch needed)
    btc_dollars = (mark_per_sh or 0.0) * 100 * contracts  # debit (positive)
    legs.append(TradeLegDetail(
        action_label="Buy to close",
        side="buy",
        position_effect="close",
        underlying=sym,
        expiry=short.expiry,
        strike=short.strike,
        option_type="call",
        qty=contracts,
        dte=dte,
        delta=short.delta,
        mark_per_share=mark_per_sh,
        bid=None,
        ask=None,
        estimated_dollars=btc_dollars,
    ))

    # STO new weekly — fetch via broker chain. Fires for both
    # roll_short_early (real action) and watch+ITM (informational preview).
    if (action == "roll_short_early" or (action == "watch" and is_itm)) \
            and broker is not None and target_strike is not None:
        try:
            dates = await broker.get_expiration_dates(sym)
            future = [d for d in dates if _days_to(d) > dte]
            weekly_dates = [d for d in future if 5 <= _days_to(d) <= 14]
            target_date = weekly_dates[0] if weekly_dates else (future[0] if future else None)
            if target_date is not None:
                calls = await broker.get_calls_for_expiry(sym, target_date)
                # Pick strike closest to target_strike with decent liquidity
                ranked = sorted(
                    [c for c in calls if (c.get("mark_price") or 0) > 0],
                    key=lambda c: abs((c.get("strike_price") or 0) - target_strike),
                )
                next_weekly = ranked[0] if ranked else None
        except Exception as e:
            log.warning("IRA chain fetch for %s failed: %s", sym, e)

    if action == "close_short":
        # Recommendation is just BTC — STO is a follow-on the user does
        # in the next session once cash settles.
        pass
    elif next_weekly is not None:
        sto_strike = next_weekly.get("strike_price") or 0
        sto_mark = next_weekly.get("mark_price")
        sto_bid = next_weekly.get("bid")
        sto_ask = next_weekly.get("ask")
        sto_delta = next_weekly.get("delta")
        sto_expiry = next_weekly.get("expiration_date") or ""
        sto_dte = next_weekly.get("dte")
        sto_dollars = -((sto_mark or 0) * 100 * contracts)  # credit (negative)
        legs.append(TradeLegDetail(
            action_label="Sell to open",
            side="sell",
            position_effect="open",
            underlying=sym,
            expiry=sto_expiry,
            strike=sto_strike,
            option_type="call",
            qty=contracts,
            dte=sto_dte,
            delta=sto_delta,
            mark_per_share=sto_mark,
            bid=sto_bid,
            ask=sto_ask,
            estimated_dollars=sto_dollars,
        ))

    net = sum(leg.estimated_dollars for leg in legs)
    # Cost confidence: tight if both legs known + small spread; medium otherwise
    cost_confidence = "medium"
    if len(legs) >= 2:
        spreads = [leg.spread_pct for leg in legs if leg.spread_pct is not None]
        if spreads and all(s < 0.05 for s in spreads):
            cost_confidence = "high"
        elif any(s > 0.15 for s in spreads):
            cost_confidence = "low"
    if net > 0 and action == "roll_short_early":
        warnings.append(
            f"Net DEBIT of ${net:,.2f} on this roll — Rule R4 says hold "
            "instead unless the new strike position is compelling on its own."
        )

    recommendation = TradeRecommendation(
        action=action,
        legs=legs,
        net_cost_dollars=net,
        cost_confidence=cost_confidence,
        benefits=benefits,
        wait_alternative=None,
    )
    return analysis, recommendation


def _render_ira_pair_analysis(cc) -> str:  # noqa: ANN001 — CoveredCallPosition
    """[DEPRECATED — replaced by _analyze_ira_covered_call + _render_pair_analysis]

    Kept temporarily for rollback safety; not called by the endpoint
    anymore. Will be removed in a follow-up.
    """
    import html as _html

    short = cc.short_call
    sym = _html.escape(cc.underlying)
    action_label, action_urgency = cc.recommended_action
    urgency_emoji = {"routine": "🟢", "elevated": "🟡", "urgent": "🔴"}.get(action_urgency, "⚪")
    urgency_class = {
        "urgent":   "bg-loss/15 text-loss border-loss/30",
        "elevated": "bg-warn/15 text-warn border-warn/30",
        "routine":  "bg-edge text-muted border-edge",
    }.get(action_urgency, "bg-edge text-muted border-edge")

    # ── Numbers we need ──
    spot = cc.underlying_price
    strike = short.strike
    credit_per_sh = short.avg_per_share          # $ per share at sale
    mark_per_sh = short.mark_per_share           # $ per share to close now
    contracts = abs(short.qty)
    dte = short.dte or 0

    total_credit = credit_per_sh * 100 * contracts
    current_close_cost = (mark_per_sh * 100 * contracts) if mark_per_sh is not None else None
    short_pnl = (credit_per_sh - mark_per_sh) * 100 * contracts if mark_per_sh is not None else None

    # Shares-side breakeven if we treat the call as a hedge: cost basis - credit/share
    shares_avg = cc.shares_avg_price or 0.0
    breakeven_per_sh = shares_avg - credit_per_sh if shares_avg > 0 else None

    # Max combined profit IF called away at strike (locks in cost-basis → strike gain + full credit)
    if shares_avg > 0:
        called_away_pnl_per_sh = (strike - shares_avg) + credit_per_sh
        # Apply to all 100×|qty| shares the call covers (which may be < total qty if partial coverage)
        shares_covered = contracts * 100
        called_away_pnl = called_away_pnl_per_sh * shares_covered
    else:
        called_away_pnl_per_sh = None
        called_away_pnl = None

    # Theta-decay efficiency: extrinsic / DTE
    extrinsic = short.extrinsic_per_share
    daily_decay = (extrinsic * 100 * contracts / dte) if (extrinsic is not None and dte > 0) else None

    # ── Reasoning narrative ──
    reasons: list[str] = []
    if short.dte == 0:
        if cc.is_itm:
            reasons.append(
                f"Expires today and is ITM (+{(cc.breach_pct or 0)*100:.1f}%). "
                "Without action the shares will be called away at ${:.2f}.".format(strike)
            )
        else:
            reasons.append("Expires today, currently OTM — premium will be kept in full.")
    elif short.dte is not None and short.dte <= 2:
        if cc.is_itm:
            reasons.append(
                "≤2 days to expiry AND ITM — high assignment risk. "
                "Roll up & out to a higher strike (next-week expiry) for a credit if available."
            )
        else:
            reasons.append("≤2 days to expiry, OTM — let it expire or roll out a week for fresh premium.")
    elif short.unrealized_pnl_pct is not None and short.unrealized_pnl_pct >= 0.85:
        reasons.append(
            f"≥85% of credit captured (currently {short.unrealized_pnl_pct*100:.0f}%). "
            "Close to free the shares + re-sell next week for fresh premium."
        )
    elif cc.is_itm:
        reasons.append(
            f"ITM by +{(cc.breach_pct or 0)*100:.1f}%. Not yet in the urgency window "
            f"({short.dte}d left) but worth watching."
        )
    elif short.unrealized_pnl_pct is not None and short.unrealized_pnl_pct >= 0.70:
        reasons.append(
            f"{short.unrealized_pnl_pct*100:.0f}% of credit captured. "
            "Consider closing-and-resell to harvest the remaining decay risk-free."
        )
    else:
        reasons.append("OTM with normal time to expiry — let theta work.")

    # Coverage note if not fully covered
    if not cc.is_fully_covered:
        reasons.append(
            f"Coverage is {cc.coverage_pct*100:.0f}% — only {int(cc.shares_qty)} shares "
            f"vs. {int(contracts)*100} required for full cover."
        )

    rationale_html = "".join(
        f'<div class="text-xs text-mono mb-2 leading-snug">{_html.escape(r)}</div>'
        for r in reasons
    )

    # ── Key metrics grid ──
    rows = []

    def _row(label: str, value: str, value_class: str = "text-mono") -> str:
        return (
            f'<div class="flex justify-between text-xs">'
            f'<span class="text-muted">{label}</span>'
            f'<span class="font-mono {value_class}">{value}</span></div>'
        )

    rows.append(_row("Credit received", f"${total_credit:,.2f} total"))
    if current_close_cost is not None:
        rows.append(_row("Cost to close now", f"${current_close_cost:,.2f}"))
    if short_pnl is not None:
        cls = "text-gain" if short_pnl >= 0 else "text-loss"
        sign = "+" if short_pnl >= 0 else ""
        rows.append(_row("Short P&L", f"{sign}${short_pnl:,.2f}", cls))
    if breakeven_per_sh is not None:
        rows.append(_row("Effective basis (cost − credit)", f"${breakeven_per_sh:.2f}/sh"))
    if called_away_pnl_per_sh is not None and called_away_pnl is not None:
        cls = "text-gain" if called_away_pnl_per_sh >= 0 else "text-loss"
        sign = "+" if called_away_pnl_per_sh >= 0 else ""
        rows.append(_row(
            "If called away at strike",
            f"{sign}${called_away_pnl:,.2f} ({sign}${called_away_pnl_per_sh:.2f}/sh)",
            cls,
        ))
    if daily_decay is not None:
        rows.append(_row("Theta-decay $/day", f"${daily_decay:,.2f}/day remaining"))

    metrics_html = (
        '<div class="mt-3 pt-3 border-t border-edge">'
        '<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-2">'
        'Key metrics</div>'
        '<div class="space-y-1">'
        + "".join(rows)
        + '</div></div>'
    )

    # ── Expiry scenarios ──
    scenarios = []
    if spot is not None:
        # OTM scenario
        scenarios.append(
            f"<strong>If OTM at expiry (spot &lt; ${strike:.2f}):</strong> "
            f"shares stay; full premium ${total_credit:,.2f} kept."
        )
        # At-strike
        scenarios.append(
            f"<strong>If pinned at ${strike:.2f}:</strong> "
            "uncertain assignment, broker discretion. Roll preemptively if you want certainty."
        )
        # ITM
        if shares_avg > 0:
            itm_pnl = (strike - shares_avg + credit_per_sh) * 100 * contracts
            cls = "text-gain" if itm_pnl >= 0 else "text-loss"
            sign = "+" if itm_pnl >= 0 else ""
            scenarios.append(
                f"<strong>If ITM at expiry (spot &gt; ${strike:.2f}):</strong> "
                f"shares called away at ${strike:.2f}; locked combined P&L "
                f'<span class="font-mono {cls}">{sign}${itm_pnl:,.2f}</span> on the {int(contracts)*100} covered shares.'
            )
    scenarios_html = (
        '<div class="mt-3 pt-3 border-t border-edge">'
        '<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-2">'
        'At expiry</div>'
        '<ul class="space-y-1.5 text-xs text-mono leading-snug">'
        + "".join(f'<li>{s}</li>' for s in scenarios)
        + '</ul></div>'
    )

    return (
        f'<div class="text-mono">'
        # Action header
        f'<div class="flex items-center gap-2 mb-2 flex-wrap">'
        f'<span class="text-[10px] uppercase font-mono tracking-wider px-2 py-0.5 rounded font-semibold border {urgency_class}">'
        f'{urgency_emoji} {_html.escape(action_label)}'
        f'</span>'
        f'<span class="text-xs text-muted font-mono">{sym} · covered call · {dte}d to expiry</span>'
        f'</div>'
        # Position summary
        f'<div class="text-xs text-mono mb-3 leading-snug">'
        f'{int(cc.shares_qty)} shares held at <span class="private-money">${shares_avg:.2f}</span>/sh '
        f'avg cost; short <span class="font-semibold">{int(contracts)}x</span> '
        f'<span class="font-semibold">{short.expiry} ${strike:.2f}C</span> '
        f'at <span class="private-money">${credit_per_sh:.2f}</span>/sh credit.'
        f'</div>'
        # Rationale
        f'<div class="mt-3 pt-3 border-t border-edge">'
        f'<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-2">'
        f'Reasoning</div>'
        f'{rationale_html}'
        f'</div>'
        # Metrics
        f'{metrics_html}'
        # Scenarios
        f'{scenarios_html}'
        f'</div>'
    )


def _render_pair_analysis(
    analysis, recommendation=None, slug: str = "", symbol: str = "",
    show_execute_button: bool = True, status_banner: str = "",
    roll_extras: dict | None = None, preview_token: tuple | None = None,
) -> str:
    """Render a PMCCAnalysis (+ optional TradeRecommendation) as dark-theme HTML.

    Layout:
      [URGENCY emoji]  ACTION  •  X% conf
      Summary (one-liner)
      ─
      Rationale (multi-line)
      ─
      Warnings (bullets, only if any)
      ─
      RECOMMENDED TRADE (concrete legs + dollars + cost confidence)
      ─
      EXPECTED BENEFIT (action-specific bullets)
      ─
      [ Approve & Execute ] button (if action is non-trivial)
    """
    import html as _html
    urgency = (analysis.urgency or "routine").lower()
    urgency_emoji = {"routine": "🟢", "elevated": "🟡", "urgent": "🔴"}.get(urgency, "⚪")
    urgency_class = {
        "urgent":   "bg-loss/15 text-loss border-loss/30",
        "elevated": "bg-warn/15 text-warn border-warn/30",
        "routine":  "bg-edge text-muted border-edge",
    }.get(urgency, "bg-edge text-muted border-edge")

    confidence_pct = int((analysis.confidence or 0) * 100)
    action_raw = (analysis.action or "").lower()
    action_str = (analysis.action or "—").upper().replace("_", " ")
    summary = _html.escape(analysis.summary or "")
    rationale = _html.escape(analysis.rationale or "")

    warnings_html = ""
    if analysis.warnings:
        warnings_html = (
            '<div class="mt-3 pt-3 border-t border-edge">'
            '<div class="text-[10px] uppercase tracking-wider text-warn font-semibold mb-1.5">'
            'Warnings</div>'
            '<ul class="space-y-1 text-xs text-mono">'
            + "".join(
                f'<li class="flex gap-2"><span class="text-warn">⚠</span>'
                f'<span>{_html.escape(w)}</span></li>'
                for w in analysis.warnings
            )
            + '</ul></div>'
        )

    # Recommended trade detail (dollar-priced legs + cost confidence)
    trade_html = _render_recommendation(recommendation) if recommendation else ""

    # Optional fallback: LLM-suggested target params, ONLY if we had no
    # concrete recommendation (the trade detail supersedes generic targets).
    params_html = ""
    if recommendation is None and (analysis.target_delta is not None or analysis.target_dte is not None):
        rows = []
        if analysis.target_delta is not None:
            rows.append(
                f'<div class="flex justify-between"><span class="text-muted">Target δ</span>'
                f'<span class="font-mono">{analysis.target_delta:.2f}</span></div>'
            )
        if analysis.target_dte is not None:
            rows.append(
                f'<div class="flex justify-between"><span class="text-muted">Target DTE</span>'
                f'<span class="font-mono">{analysis.target_dte}d</span></div>'
            )
        params_html = (
            '<div class="mt-3 pt-3 border-t border-edge">'
            '<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-1.5">'
            'Suggested parameters</div>'
            '<div class="space-y-1 text-xs">'
            + "".join(rows)
            + '</div></div>'
        )

    # Consent extras (2026-07-30) — earnings-imminent state + the live
    # debit/credit/net estimate, sourced from the SAME orders the Approve will fire
    # (roll_extras is built once from the previewed combo). Only present on the
    # division-panel Re-analyze roll path; every other caller passes roll_extras=None.
    earnings = (roll_extras or {}).get("earnings")
    estimate = (roll_extras or {}).get("estimate")
    estimate_reason = (roll_extras or {}).get("estimate_reason")
    offer_roll = earnings is None or earnings.get("offer_roll", True)

    earnings_html = ""
    if earnings and earnings.get("kind") == "blocked":
        _date = _html.escape(str(earnings.get("date") or "—"))
        _ver = "verified" if earnings.get("verified") else "unverified"
        _rec = _html.escape(str(earnings.get("recommendation") or ""))
        _cav = earnings.get("caveat")
        earnings_html = (
            '<div class="mt-3 rounded-md border border-loss/40 bg-loss/10 p-3">'
            '<div class="text-[10px] uppercase tracking-wider text-loss font-semibold mb-1">'
            f'Earnings {_date} · {_ver}</div>'
            f'<div class="text-xs text-mono">{_rec}</div>'
            + (f'<div class="text-xs text-warn mt-1">⚠ {_html.escape(str(_cav))}</div>' if _cav else '')
            + '<div class="text-[10px] text-muted mt-1">Roll not offered while earnings '
              'is within the buffer — the current short is left to expire.</div>'
            '</div>'
        )
    elif earnings and earnings.get("kind") == "unverified":
        _flag = _html.escape(str(earnings.get("flag") or "earnings date unverified"))
        earnings_html = (
            '<div class="mt-3 rounded-md border border-warn/40 bg-warn/10 p-2">'
            f'<div class="text-xs text-warn font-mono">⚠ {_flag}</div>'
            '</div>'
        )

    estimate_html = ""
    if estimate:
        _ce = "" if estimate.get("close_strike") is None else f"{estimate['close_strike']:.2f}"
        _oe = "" if estimate.get("open_strike") is None else f"{estimate['open_strike']:.2f}"
        _dir = estimate.get("direction", "credit")
        _dir_cls = "text-gain" if _dir == "credit" else "text-loss"
        estimate_html = (
            '<div class="mt-3 pt-3 border-t border-edge">'
            '<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-1.5">'
            'Roll estimate <span class="normal-case tracking-normal text-muted/70">'
            '— est., actual fill will differ slightly</span></div>'
            '<div class="space-y-1 text-xs font-mono">'
            f'<div class="flex justify-between gap-2"><span class="text-muted">'
            f'Debit · buy to close {_ce} C {_html.escape(str(estimate.get("close_expiration") or ""))}</span>'
            f'<span class="private-money">${estimate.get("debit", 0):.2f}</span></div>'
            f'<div class="flex justify-between gap-2"><span class="text-muted">'
            f'Credit · sell to open {_oe} C {_html.escape(str(estimate.get("open_expiration") or ""))}</span>'
            f'<span class="private-money">${estimate.get("credit", 0):.2f}</span></div>'
            f'<div class="flex justify-between gap-2 font-semibold"><span class="text-muted">Net {_dir}</span>'
            f'<span class="private-money {_dir_cls}">${estimate.get("net_abs", 0):.2f}</span></div>'
            '</div></div>'
        )
    elif estimate_reason:
        estimate_html = (
            '<div class="mt-3 pt-3 border-t border-edge">'
            f'<div class="text-xs text-muted font-mono">{_html.escape(str(estimate_reason))}</div>'
            '</div>'
        )

    # Hidden consent token — carries the previewed combo's id + shape fingerprint
    # into the Approve POST so dispatch fires the EXACT combo shown (not a fresh
    # re-analysis). Present only on the Re-analyze roll path.
    hidden_consent = ""
    if preview_token:
        _pid, _fp = preview_token
        hidden_consent = (
            f'<input type="hidden" name="preview_id" value="{_html.escape(str(_pid))}">'
            f'<input type="hidden" name="fingerprint" value="{_html.escape(str(_fp))}">'
        )

    _defer_form = (
        '<form'
        f' hx-post="/division/{slug}/pair/{symbol}/defer"'
        ' hx-target="#pair-analysis"'
        ' hx-swap="innerHTML">'
        '<button type="submit"'
        ' class="w-full px-3 py-1.5 rounded-md font-mono text-xs'
        '        bg-pane-2 text-muted hover:bg-edge-2 hover:text-mono'
        '        transition-colors border border-edge">'
        '⏸ Defer 24 hours · let theta work / re-evaluate later'
        '</button>'
        '</form>'
    )

    # Approve & Execute (primary) + Defer 24h (secondary) buttons.
    # Only render when the action is something we can actually translate
    # into orders. For 'hold' / 'watch' there's nothing to approve/defer.
    # Callers that have no automation pipeline (e.g. IRA dashboard which
    # is read-only — user executes manually in Robinhood) pass
    # show_execute_button=False to hide the buttons.
    # Earnings-imminent (offer_roll False): Approve is HIDDEN — we recommend letting
    # the short expire, not rolling — so the operator can only Defer.
    button_html = ""
    actionable = action_raw not in ("", "hold", "watch")
    # P1 (2026-07-31): Approve is gated on a CONCRETE built estimate — closes the
    # bare-Approve gap (a live Approve over "Target δ · DTE" with no numbers). When
    # the roll can't be priced now (pre-market / illiquid / sparse chain), suppress
    # Approve and say why.
    _has_estimate = bool(estimate)
    if actionable and slug and symbol and show_execute_button and offer_roll and _has_estimate:
        button_html = (
            '<div class="mt-4 pt-3 border-t border-edge space-y-2">'
            # Approve & Execute (primary)
            '<form'
            f' hx-post="/division/{slug}/pair/{symbol}/execute"'
            ' hx-target="#pair-analysis"'
            ' hx-swap="innerHTML"'
            ' hx-indicator="#approve-spinner"'
            ' hx-confirm="Approve and execute this trade now? '
            f'(Action: {action_str} on {symbol})">'
            f'{hidden_consent}'
            '<button type="submit"'
            ' class="w-full px-3 py-2 rounded-md font-mono text-sm font-semibold'
            '        bg-accent text-white hover:bg-accent/85 transition-colors'
            '        flex items-center justify-center gap-2">'
            '<span id="approve-spinner" class="htmx-indicator">⏳</span>'
            f'<span>Approve &amp; Execute · {action_str}</span>'
            '</button>'
            '</form>'
            + _defer_form +
            '<div class="text-[10px] text-muted/60 font-mono text-center pt-1">'
            'Risk gates apply on Approve. Defer suppresses re-analysis for 24h.'
            '</div>'
            '</div>'
        )
    elif actionable and slug and symbol and show_execute_button and offer_roll and not _has_estimate:
        # No concrete built estimate → do NOT show a priced Approve (the bare-Approve
        # gap). Tell the operator why + how to get a number.
        _why = _html.escape(str(estimate_reason)) if estimate_reason else (
            "the roll can't be priced right now (market closed, illiquid, or a sparse chain)")
        button_html = (
            '<div class="mt-4 pt-3 border-t border-edge space-y-2">'
            '<div class="w-full px-3 py-2 rounded-md font-mono text-xs text-center'
            '            bg-edge text-muted border border-edge">'
            f'Can\'t be priced right now — {_why}. Refresh pricing or Re-analyze in session.'
            '</div>'
            + _pmcc_refresh_pricing_button(slug, symbol)
            + _pmcc_reanalyze_button(slug, symbol)
            + '</div>'
        )
    elif actionable and slug and symbol and show_execute_button and not offer_roll:
        # Earnings within buffer — Approve suppressed; Defer only.
        button_html = (
            '<div class="mt-4 pt-3 border-t border-edge space-y-2">'
            '<div class="w-full px-3 py-2 rounded-md font-mono text-xs text-center'
            '            bg-edge text-muted border border-edge">'
            'Approve disabled — earnings within buffer; let the short expire, roll after the print'
            '</div>'
            + _defer_form +
            '</div>'
        )

    return (
        '<div class="space-y-3">'
        f'{status_banner}'
        f'<div class="flex items-center gap-2 flex-wrap">'
        f'<span class="text-base">{urgency_emoji}</span>'
        f'<span class="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded font-mono '
        f'border {urgency_class}">{action_str}</span>'
        f'<span class="text-[11px] text-muted font-mono ml-auto">{confidence_pct}% conf</span>'
        '</div>'
        f'<div class="text-sm text-mono font-medium leading-snug">{summary}</div>'
        f'<div class="text-xs text-muted leading-relaxed">{rationale}</div>'
        f'{warnings_html}'
        f'{trade_html}'
        f'{estimate_html}'
        f'{earnings_html}'
        f'{params_html}'
        f'{button_html}'
        '</div>'
    )


# ── Unified tile/expert decision: Expert-panel-as-reader helpers ────────────
# The Expert panel reads the SAME per-asset decision record the tile shows, so
# the two can no longer disagree. The LLM only re-runs on an explicit Re-analyze
# (?force=1), which writes a fresh source='expert' verdict.

def _pmcc_reanalyze_button(slug: str, symbol: str, label: str = "↻ Re-analyze now") -> str:
    """Explicit Re-analyze control — re-runs the LLM (?force=1) and writes a
    fresh expert verdict to the unified decision record."""
    return (
        f'<button hx-get="/division/{slug}/pair-analysis/{symbol}?force=1"'
        ' hx-target="#pair-analysis" hx-swap="innerHTML"'
        ' class="w-full mt-1 px-3 py-1.5 rounded-md font-mono text-xs'
        '        bg-pane-2 text-muted hover:bg-edge-2 hover:text-mono'
        '        transition-colors border border-edge">'
        f'{label}</button>'
    )


def _pmcc_refresh_pricing_button(slug: str, symbol: str, label: str = "⟳ Refresh pricing") -> str:
    """LLM-FREE pricing refresh (P1, 2026-07-31) — re-pulls the live chain, re-selects
    the strike within the STORED δ band, recomputes debit/credit/net, and rewrites the
    consent stash. NO Anthropic call (distinct from Re-analyze, which re-runs the LLM)."""
    return (
        f'<button hx-post="/division/{slug}/pair/{symbol}/refresh-pricing"'
        ' hx-target="#pair-analysis" hx-swap="innerHTML"'
        ' class="w-full mt-1 px-3 py-1.5 rounded-md font-mono text-xs'
        '        bg-pane-2 text-muted hover:bg-edge-2 hover:text-mono'
        '        transition-colors border border-edge">'
        f'{label}</button>'
    )


def _pmcc_status_banner(slug: str, symbol: str, *, state: str, age_h, source) -> str:
    """Freshness banner above a record-sourced Expert panel + a Re-analyze button.

    fresh -> subtle "latest {source} decision · Xh ago"; stale -> a muted
    "stale as of Xh" note (the tile shows the same 'stale' badge).

    NB: the banner no longer claims "tile & panel in sync" (FIX 3). That was an
    unconditional string, not a check — and once the execute / Re-analyze
    responses refresh the tile out-of-band (FIX 2) they ARE in sync, so the
    claim is redundant. A claim that can be wrong is worse than none.
    """
    age_txt = f"{age_h:.0f}h ago" if isinstance(age_h, (int, float)) else "—"
    src = source or "scan"
    if state == "stale":
        note = (
            '<div class="flex items-center gap-2 text-[11px] font-mono text-muted/60 italic'
            '            px-2 py-1 rounded border border-edge bg-transparent">'
            f'<span>⏳ stale as of {age_txt}</span>'
            f'<span class="text-muted/40">· last {src} verdict · a scan will refresh it</span>'
            '</div>'
        )
    else:
        note = (
            '<div class="flex items-center gap-2 text-[11px] font-mono text-muted/60'
            '            px-2 py-1 rounded border border-edge bg-pane-2/40">'
            f'<span>latest {src} decision · {age_txt}</span>'
            '</div>'
        )
    return note + _pmcc_reanalyze_button(slug, symbol)


def _pmcc_tile_badge_oob(templates, deps, symbol: str) -> str:
    """Render an out-of-band (hx-swap-oob) refresh of one pair's tile badge (FIX 2).

    After an execute or Re-analyze writes the unified decision record, the
    left-rail tile badge (in #pair-list) is stale until a full page reload — the
    panel swap only touches #pair-analysis. Emitting this fragment in the SAME
    response updates the badge (and thus the Approve affordance) from the
    just-written record, no reload. Renders the SAME `_pmcc_badge.html` partial
    the row uses, so the two can't drift. Best-effort: '' on any failure, or when
    there's no #pmcc-badge-{symbol} in the DOM (HTMX silently no-ops).
    """
    try:
        from datetime import datetime as _dt, timezone as _tz
        from trading_corp.web.data import _build_pmcc_tile_status
        sym = (symbol or "").upper()
        cfg = (getattr(getattr(deps, "pmcc_agent", None), "_cfg", {}) or {}).get(
            "tile_status", {}) or {}
        us = _build_pmcc_tile_status(
            sym, db_url=deps.db_url, now=_dt.now(_tz.utc), cfg=cfg,
        )
        inner = templates.get_template("partials/_pmcc_badge.html").render(us=us)
        return (
            f'<span id="pmcc-badge-{sym}" hx-swap-oob="true" class="contents">'
            f'{inner}</span>'
        )
    except Exception as e:  # noqa: BLE001 — an OOB refresh must never break the response
        log.warning("pmcc tile-badge OOB(%s) failed: %s", symbol, e)
        return ""


def _pmcc_no_signal_panel(slug: str, symbol: str, cfg: dict) -> str:
    """Panel shown when no decision exists this session (pre-open / scan aborted).
    Reads 'working, not broken' + a Re-analyze button to generate one now."""
    label = (cfg or {}).get("no_signal_label", "awaiting scan")
    return (
        '<div class="space-y-3">'
        '<div class="flex items-center gap-2 text-xs font-mono text-muted/60'
        '            px-2 py-2 rounded border border-dashed border-edge/60">'
        '<span class="text-base">🕓</span>'
        f'<span>No decision this session yet — {label}.</span>'
        '</div>'
        '<div class="text-[11px] text-muted/50 leading-relaxed">'
        'The post-open scan writes a verdict per position; pre-open (or if the '
        'scan aborted on this name) the tile stays blank by design. Re-analyze to '
        'generate a fresh expert verdict now.'
        '</div>'
        + _pmcc_reanalyze_button(slug, symbol)
        + '</div>'
    )


def _pmcc_analysis_from_record(rec: dict):
    """Duck-typed analysis object from a persisted decision record, for
    `_render_pair_analysis` (reads .action/.urgency/.confidence/.summary/
    .rationale/.warnings/.target_delta/.target_dte). P1: also surfaces the stored
    δ BAND (low/high) + DTE so the panel/pricing can read the consent envelope."""
    import types
    _lo = rec.get("target_delta_low")
    _hi = rec.get("target_delta_high")
    _mid = ((float(_lo) + float(_hi)) / 2.0) if (_lo is not None and _hi is not None) else None
    return types.SimpleNamespace(
        action=rec.get("status"),
        urgency=rec.get("urgency") or "routine",
        confidence=rec.get("confidence"),
        summary=rec.get("summary"),
        rationale=rec.get("rationale"),
        warnings=rec.get("warnings") or [],
        target_delta=_mid,
        target_dte=rec.get("target_dte"),
        target_delta_low=_lo,
        target_delta_high=_hi,
    )


async def _render_pmcc_record_panel(deps, slug: str, symbol: str) -> str:
    """Render the Expert panel from the UNIFIED decision record (NO LLM call):
    fresh/stale -> the persisted verdict + freshness banner; none -> awaiting-scan.

    P3 ADDITION 3: an actionable SHORT-side SINGLE-leg verdict (urgent close_short /
    open_short) gets a PRICED Approve straight from the LLM-FREE `price_and_stash`
    path — NO Re-analyze, NO _llm_analyze_position — routed through the SAME consent
    stash+fingerprint machinery as rolls (displayed == Approve-fires). Rolls + any
    unpriceable verdict keep the stored-only 'Re-analyze to build a live estimate' note.
    """
    from datetime import datetime as _dt, timezone as _tz
    from trading_corp.agents.divisions import _pmcc_status
    cfg = (getattr(deps.pmcc_agent, "_cfg", {}) or {}).get("tile_status", {}) or {}
    stale_h = float(cfg.get("staleness_hours", _pmcc_status.DEFAULT_STALENESS_HOURS))
    rec = _pmcc_status.load_decision(symbol, db_url=deps.db_url)
    now = _dt.now(_tz.utc)
    state = _pmcc_status.classify_freshness(rec, now, stale_h)
    if state == "none":
        return _pmcc_no_signal_panel(slug, symbol, cfg)
    banner = _pmcc_status_banner(
        slug, symbol, state=state,
        age_h=_pmcc_status.age_hours(rec, now), source=rec.get("source"),
    )
    action = (rec.get("status") or "").lower()

    # P3 ADDITION 3 — LLM-FREE priced Approve for a SHORT-side single-leg action.
    # The urgent close is the most time-critical action; requiring an LLM Re-analyze
    # just to see the buy-back price re-introduces the waste P1 killed. Price it cheap
    # + instant via price_and_stash (reconstructs PMCCAnalysis from the stored record —
    # NO _llm_analyze_position) and write the consent stash from the SAME pull.
    if action in ("close_short", "open_short"):
        _broker = deps.data_exec.brokers.get(slug) if deps.data_exec else None
        pr = None
        if _broker is not None:
            try:
                from trading_corp.web import pmcc_pricing
                pr = await pmcc_pricing.price_and_stash(
                    deps.pmcc_agent, _broker, slug, symbol, deps.db_url)
            except Exception as e:      # noqa: BLE001 — pricing must never break the panel
                log.warning("record-panel: LLM-free price_and_stash(%s) failed: %s", symbol, e)
                pr = None
        if pr is not None and pr.buildable and pr.stash_token is not None:
            return _render_pair_analysis(
                _pmcc_analysis_from_record(rec), recommendation=None,
                slug=slug, symbol=symbol, status_banner=banner,
                roll_extras={"earnings": pr.earnings, "estimate": pr.estimate,
                             "estimate_reason": pr.estimate_reason},
                preview_token=pr.stash_token, show_execute_button=True,
            )
        # Unpriceable right now (illiquid / market closed) -> fall through to the note.

    # Stored verdict with no live estimate -> Approve NOT offered; the estimate a
    # visible Approve carries is guaranteed to be the combo fired.
    panel = _render_pair_analysis(
        _pmcc_analysis_from_record(rec), recommendation=None,
        slug=slug, symbol=symbol, status_banner=banner,
        show_execute_button=False,
    )
    if action not in ("", "hold", "watch"):
        panel += (
            '<div class="mt-3 rounded-md border border-edge bg-pane-2/40 p-3">'
            '<div class="text-[11px] font-mono text-muted leading-relaxed">'
            'Approve isn\'t shown on a stored verdict — it needs a live strike, '
            'debit, credit &amp; net first. <span class="text-mono">Re-analyze</span> '
            '(above) to build the live estimate, then approve that exact combo.'
            '</div></div>'
        )
    return panel


def _render_recommendation(rec) -> str:
    """Render a TradeRecommendation as the 'Recommended Trade' + 'Expected Benefit' block."""
    import html as _html
    if not rec or not rec.legs:
        return ""

    # Per-leg row: action label · contract · qty · price → cost
    leg_rows: list[str] = []
    for leg in rec.legs:
        # Strike formatting (always 2 decimals — never round)
        strike_str = f"${leg.strike:,.2f}{leg.option_type[:1].upper()}"
        contract_str = f"{leg.expiry} · {strike_str}"
        if leg.dte is not None:
            contract_str += f" · {leg.dte}d"

        # Price string + spread quality dot when known. Per-share mark
        # is wrapped in .private-money so the privacy toggle blurs it
        # along with the per-leg dollar totals.
        if leg.mark_per_share is not None:
            price_str = (
                f'@ <span class="private-money">${leg.mark_per_share:,.2f}/sh</span>'
            )
        else:
            price_str = "@ —"

        # Spread indicator (only meaningful for new/open legs we just selected)
        spread_dot = ""
        if leg.position_effect == "open":
            sq = leg.spread_quality
            if sq != "unknown":
                color = {"tight": "gain", "medium": "warn", "wide": "loss"}[sq]
                spread_dot = (
                    f'<span title="bid-ask spread: {sq}" '
                    f'class="inline-block w-1.5 h-1.5 rounded-full bg-{color}/80"></span>'
                )

        # Cost cell (signed) — never round; cents matter for fills
        cost_dollars = leg.estimated_dollars
        cost_class = (
            "text-loss" if cost_dollars > 0       # debit
            else "text-gain" if cost_dollars < 0  # credit
            else "text-mono"
        )
        cost_str = (
            f'<span class="private-money">-${abs(cost_dollars):,.2f}</span>' if cost_dollars > 0
            else f'<span class="private-money">+${abs(cost_dollars):,.2f}</span>' if cost_dollars < 0
            else '<span class="private-money">$0.00</span>'
        )

        leg_rows.append(
            f'<div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px] font-mono py-1 border-b border-edge/40 last:border-0">'
            f'  <span class="text-mono font-semibold w-[78px] shrink-0">{leg.action_label}</span>'
            f'  <span class="text-mono">{leg.underlying}</span>'
            f'  <span class="text-muted">{_html.escape(contract_str)}</span>'
            f'  <span class="text-muted">×{leg.qty}</span>'
            f'  {spread_dot}'
            f'  <span class="text-muted">{price_str}</span>'
            f'  <span class="ml-auto font-semibold {cost_class}">{cost_str}</span>'
            f'</div>'
        )

    # Net cost line — exact cents, never rounded. Wrapped for privacy mode.
    net = rec.net_cost_dollars
    if net > 0:
        net_label = "Net debit"
        net_class = "text-loss"
        net_str = f'<span class="private-money">-${net:,.2f}</span>'
    elif net < 0:
        net_label = "Net credit"
        net_class = "text-gain"
        net_str = f'<span class="private-money">+${abs(net):,.2f}</span>'
    else:
        net_label = "Net"
        net_class = "text-mono"
        net_str = '<span class="private-money">$0.00</span>'

    confidence_label = {
        "high":   ("high",   "text-gain", "tight spreads → fill near mark"),
        "medium": ("medium", "text-warn", "moderate spreads → small slippage possible"),
        "low":    ("low",    "text-loss", "wide spreads → meaningful slippage risk"),
    }.get(rec.cost_confidence, ("medium", "text-warn", ""))

    benefits_html = ""
    if rec.benefits:
        benefits_html = (
            '<div class="mt-3 pt-3 border-t border-edge">'
            '<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-1.5">'
            'Expected benefit</div>'
            '<ul class="space-y-1 text-xs text-mono">'
            + "".join(
                f'<li class="flex gap-2 leading-snug">'
                f'<span class="text-gain shrink-0">✓</span>'
                f'<span>{_html.escape(b)}</span></li>'
                for b in rec.benefits
            )
            + '</ul></div>'
        )

    # ── "What if we wait?" alternative analysis (terminal-DTE only) ──
    wait_html = ""
    wait = getattr(rec, "wait_alternative", None)
    if wait is not None:
        scen_rows = []
        for s in wait.scenarios:
            sav = s.savings_vs_now
            cls = "text-gain" if sav > 0 else "text-loss" if sav < 0 else "text-mono"
            sav_str = (
                f'<span class="private-money">+${sav:,.2f}</span>' if sav > 0 else
                f'<span class="private-money">-${abs(sav):,.2f}</span>' if sav < 0 else
                '<span class="private-money">$0.00</span>'
            )
            close_str = (
                f'<span class="private-money">${s.close_cost:,.2f}</span>'
                if s.close_cost > 0 else '<span class="private-money">$0.00</span> (worthless)'
            )
            scen_rows.append(
                f'<tr>'
                f'<td class="px-2 py-1 text-muted">{s.label}</td>'
                f'<td class="px-2 py-1 text-right text-mono">${s.scen_spot:,.2f}</td>'
                f'<td class="px-2 py-1 text-right text-mono">{close_str}</td>'
                f'<td class="px-2 py-1 text-right font-semibold {cls}">{sav_str}</td>'
                f'</tr>'
            )

        wait_html = (
            '<div class="mt-3 pt-3 border-t border-edge">'
            '<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-1.5">'
            'Alternative · wait to expiration</div>'
            '<div class="rounded-md bg-pane-2/40 border border-edge/60 px-3 py-2.5">'
            f'<div class="text-xs text-mono leading-snug mb-2">{_html.escape(wait.summary)}</div>'
            f'<div class="text-[10px] text-muted/70 font-mono mb-1.5">'
            f'Roll-vs-wait breakeven: stock at expiry = '
            f'<span class="text-mono">${wait.breakeven_spot:,.2f}</span> '
            f'({wait.breakeven_pct*100:+.2f}% from current)</div>'
            '<table class="w-full text-[11px] font-mono">'
            '<thead class="text-[9px] uppercase tracking-wider text-muted/60">'
            '<tr>'
            '<th class="px-2 py-1 text-left">scenario</th>'
            '<th class="px-2 py-1 text-right">spot</th>'
            '<th class="px-2 py-1 text-right">close cost</th>'
            '<th class="px-2 py-1 text-right">vs roll-now</th>'
            '</tr></thead>'
            '<tbody>'
            + "".join(scen_rows) +
            '</tbody></table>'
            '</div></div>'
        )

    return (
        '<div class="mt-3 pt-3 border-t border-edge">'
        '<div class="text-[10px] uppercase tracking-wider text-muted font-semibold mb-2">'
        'Recommended trade</div>'
        '<div class="rounded-md bg-pane-2/40 border border-edge/60 px-3 py-2">'
        + "".join(leg_rows) +
        f'  <div class="flex items-baseline justify-between mt-2 pt-2 border-t border-edge font-mono text-sm">'
        f'    <span class="text-muted text-xs">{net_label}</span>'
        f'    <span class="font-semibold {net_class}">{net_str}</span>'
        f'  </div>'
        f'  <div class="flex items-baseline justify-between mt-1 text-[10px] font-mono">'
        f'    <span class="text-muted/70 uppercase tracking-wider">Cost confidence</span>'
        f'    <span class="{confidence_label[1]} font-semibold uppercase">{confidence_label[0]}</span>'
        f'  </div>'
        f'  <div class="text-[10px] text-muted/70 mt-0.5 italic">{confidence_label[2]}</div>'
        '</div>'
        f'{wait_html}'
        f'{benefits_html}'
        '</div>'
    )


def _exec_error_html(symbol: str, reason: str) -> str:
    return (
        '<div class="space-y-2 p-3 rounded-md bg-loss/10 border border-loss/30">'
        '<div class="text-loss font-mono font-semibold text-sm">'
        f'❌ {symbol} · execution error</div>'
        f'<div class="text-xs text-muted">{reason}</div>'
        '</div>'
    )


def _manual_order_error_html(msg: str) -> str:
    """Validation/setup error for the manual order form (pre-broker call)."""
    import html as _html
    return (
        '<div class="p-3 rounded-md bg-loss/10 border border-loss/30">'
        '<div class="text-loss font-mono font-semibold text-sm">'
        '⚠ Order rejected'
        '</div>'
        f'<div class="text-xs text-muted mt-1">{_html.escape(msg)}</div>'
        '</div>'
    )


def _render_manual_order_result(
    order,
    outcome: str,
    *,
    fill_price: float | None = None,
    venue: str | None = None,
    detail: str | None = None,
    original_qty: float | None = None,
    risk_resize_reason: str | None = None,
) -> str:
    """Render the result panel that swaps in below the manual order form.

    Outcomes:
      filled          — broker accepted the order. May be fully filled OR
                        sitting on the book — disambiguated by venue suffix:
                          venue="coinbase_spot"        → fully filled
                          venue="coinbase_spot:open"   → on book, not filled
                          venue="coinbase_spot:dry-run"→ dry-run synthetic
      risk_rejected   — risk gate said no
      execute_error   — broker raised on place_order

    `original_qty` and `risk_resize_reason` are populated by the route when
    the risk gate downsized the order. The renderer then surfaces a "resized
    by risk gate" line so the Board doesn't have to reverse-engineer why the
    submitted qty doesn't match the form input.
    """
    import html as _html

    venue_str = venue or ""
    is_dry = outcome == "filled" and venue_str.endswith(":dry-run")
    # Treat any non-dry, non-filled venue suffix as "accepted/on book".
    # ccxt's coinbase driver emits status="open" for accepted-but-not-yet-
    # crossed limit orders. We surface it explicitly because seeing
    # "FILLED" for an order that's actually resting on the book is
    # exactly how the user mistakes a placed order for an executed one.
    is_on_book = (
        outcome == "filled"
        and ":" in venue_str
        and not is_dry
    )
    is_truly_filled = (
        outcome == "filled"
        and not is_dry
        and not is_on_book
    )

    if is_dry:
        color, icon, label = "warn", "🧪", "DRY-RUN FILL"
    elif is_on_book:
        color, icon, label = "accent", "📥", "PLACED ON BOOK"
    elif is_truly_filled:
        color, icon, label = "gain", "✅", "FILLED"
    elif outcome == "risk_rejected":
        color, icon, label = "loss", "🛑", "RISK REJECTED"
    else:
        color, icon, label = "warn", "⚠", "EXECUTION ERROR"

    side_lower = (order.side or "").lower()
    side_upper = side_lower.upper()
    side_color = "loss" if side_lower == "buy" else "gain"

    # Quantity formatter — strip trailing zeros so 0.00100000 → 0.001
    qty_str = (
        f"{order.qty:.8f}".rstrip("0").rstrip(".")
        if order.qty < 1
        else f"{order.qty:g}"
    )

    # Body content varies by outcome.
    if outcome == "filled" and fill_price:
        gross = abs(float(fill_price) * float(order.qty))
        # Gross / price wrapped in private-money so the privacy toggle
        # blurs them along with everything else on the page.
        gross_str = f'<span class="private-money">${gross:,.2f}</span>'
        price_str = f'<span class="private-money">${float(fill_price):,.4f}</span>'
        venue_str = _html.escape(venue or "—")
        if is_on_book:
            # Limit order accepted but not yet filled. Phrasing makes
            # clear no money has changed hands yet.
            potential_label = "would debit" if side_lower == "buy" else "would credit"
            body_html = (
                '<div class="text-[11px] font-mono text-muted leading-snug">'
                f'{order.order_type.capitalize()} @ {price_str} · '
                f'{potential_label} {gross_str} when filled · '
                f'venue <span class="text-mono">{venue_str}</span>'
                '</div>'
                '<div class="text-[11px] font-mono text-accent/80 mt-0.5 leading-snug">'
                'Order is resting on the book — cancel via Coinbase if you '
                'want to abandon.'
                '</div>'
            )
        else:
            net_label = "Debit" if side_lower == "buy" else "Credit"
            body_html = (
                '<div class="text-[11px] font-mono text-muted leading-snug">'
                f'{order.order_type.capitalize()} · '
                f'fill {price_str}/unit · '
                f'{net_label} {gross_str} · '
                f'venue <span class="text-mono">{venue_str}</span>'
                '</div>'
            )
    elif outcome == "filled":
        # Filled but no price — rare (synthetic edge case).
        body_html = (
            '<div class="text-[11px] font-mono text-muted leading-snug">'
            f'{order.order_type.capitalize()} accepted · awaiting fill'
            '</div>'
        )
    else:
        # risk_rejected or execute_error: show requested params + the reason.
        if order.limit_price:
            limit_str = f'<span class="private-money">${order.limit_price:,.4f}</span>'
        else:
            limit_str = 'MKT'
        detail_str = _html.escape((detail or "")[:300])
        body_html = (
            '<div class="text-[11px] font-mono text-muted leading-snug">'
            f'Requested: {order.order_type.capitalize()} @ {limit_str}'
            '</div>'
            + (
                f'<div class="text-[11px] text-{color} font-mono mt-1 leading-snug">'
                f'{detail_str}</div>'
                if detail_str else ''
            )
        )

    # Resize disclosure: if the risk gate downsized the order, surface that
    # explicitly so the Board doesn't have to figure out why the form said
    # 0.5 but the result said ×0.0177. Shows requested → final qty + the
    # reason from the risk verdict.
    resize_html = ""
    if (
        original_qty is not None
        and abs(float(original_qty) - float(order.qty)) > 1e-12
    ):
        orig_str = (
            f"{float(original_qty):.8f}".rstrip("0").rstrip(".")
            if float(original_qty) < 1
            else f"{float(original_qty):g}"
        )
        reason_str = (
            _html.escape(risk_resize_reason)
            if risk_resize_reason else "per-trade cap"
        )
        resize_html = (
            '<div class="text-[11px] font-mono text-warn mt-1 leading-snug">'
            f'⚙ Risk gate resized: requested ×{orig_str} → final ×{qty_str} '
            f'<span class="text-muted">({reason_str})</span>'
            '</div>'
        )

    rationale_html = ""
    if (order.rationale or "").strip():
        rationale_html = (
            '<div class="text-[10px] text-muted/70 font-mono mt-1 leading-snug italic">'
            f'note: {_html.escape(order.rationale[:200])}'
            '</div>'
        )

    symbol_safe = _html.escape(order.symbol or "—")

    return (
        f'<div class="space-y-1 p-3 rounded-md bg-{color}/10 border border-{color}/30">'
        '<div class="flex items-center gap-2 flex-wrap">'
        f'<span class="text-base">{icon}</span>'
        f'<span class="text-{color} font-mono font-semibold text-sm">{label}</span>'
        f'<span class="text-[10px] uppercase font-mono tracking-wider px-1.5 py-0.5 rounded '
        f'bg-{side_color}/15 text-{side_color}">{side_upper}</span>'
        f'<span class="font-mono font-semibold text-mono text-sm">{symbol_safe}</span>'
        f'<span class="text-mono text-sm font-mono">×{qty_str}</span>'
        '</div>'
        f'{body_html}'
        f'{resize_html}'
        f'{rationale_html}'
        '</div>'
    )


def _exec_no_action_html(symbol: str, analysis) -> str:
    action_str = (analysis.action or "—").upper().replace("_", " ")
    return (
        '<div class="space-y-2 p-3 rounded-md bg-edge/40 border border-edge">'
        '<div class="text-mono font-mono font-semibold text-sm">'
        f'ℹ️  {symbol} · no orders to place</div>'
        f'<div class="text-xs text-muted">Action <code>{action_str}</code> '
        'does not require any orders. Position remains as-is.</div>'
        '</div>'
    )


def _synth_analysis_from_stash(action: str | None):
    """Minimal analysis stand-in for the dispatch view on a stash HIT (FORK 2,
    2026-07-30). Carries the APPROVED action so the post-execute summary + any
    earnings-block card render WITHOUT a fresh analyze_symbol (LLM) call. Only
    `.action` is read downstream; the rest are inert defaults so no consumer
    AttributeErrors if the render path changes."""
    import types as _types
    return _types.SimpleNamespace(
        action=action or "roll_short", urgency="routine", confidence=None,
        summary="", rationale="", warnings=[], target_delta=None, target_dte=None,
    )


def _exec_consent_mismatch_html(symbol: str) -> str:
    """Render a CONSENT MISMATCH — the operator approved a specific combo (from a
    Re-analyze render) but the stash was gone (expiry/restart) and the live rebuild
    picked DIFFERENT contracts (the chain moved), so nothing was placed. The panel
    tells them to Re-analyze and approve the fresh combo (2026-07-30 consent rule:
    never fire a contract other than the one shown)."""
    return (
        '<div class="space-y-2 p-3 rounded-md bg-warn/10 border border-warn/40">'
        '<div class="text-warn font-mono font-semibold text-sm">'
        f'⚠️  {symbol} · not placed — the combo changed since you looked</div>'
        '<div class="text-xs text-muted">The live option chain moved after this card '
        'was rendered, so approving now would fire a different contract than the one '
        'you saw. Nothing was sent. <span class="text-mono">Re-analyze</span> to see '
        'the current strike / debit / credit / net, then approve that.</div>'
        '</div>'
    )


def _exec_abort_html(symbol: str, analysis, diag: dict | None) -> str:
    """Render an ABORTED roll/close — `propose_orders_for_pair` returned no
    orders because a B4 atomic gate refused it (e.g. no liquid weekly). Shows the
    ACTUAL reason from the agent's `_last_weekly_diag`/`_last_leap_diag`, distinct
    from `_exec_no_action_html`'s genuine "no orders needed" (hold/watch)."""
    action_str = (analysis.action or "—").upper().replace("_", " ")
    d = diag or {}
    reason = d.get("reason") or "unknown"
    bits = [f"{k}={d[k]}" for k in ("considered", "liquid", "target_date", "after_dte")
            if d.get(k) is not None]
    detail = reason + (f" ({', '.join(bits)})" if bits else "")
    return (
        '<div class="space-y-2 p-3 rounded-md bg-warn/10 border border-warn/40">'
        '<div class="text-warn font-mono font-semibold text-sm">'
        f'⚠️  {symbol} · {action_str} aborted — no order placed</div>'
        f'<div class="text-xs text-muted">The roll could not be built '
        f'(atomic abort; nothing was sent to the broker): <code>{detail}</code>. '
        'Position is unchanged.</div>'
        '</div>'
    )


_ACTION_LABELS = {
    "roll_short_call_close": "Buy to close",
    "roll_short_call_open":  "Sell to open",
    "open_leap":             "Buy to open",
    "open_short_call":       "Sell to open",
    "close_short_call":      "Buy to close",
    "open_pmcc":             "Buy/Sell to open",
}


def _action_label_for(order) -> str:
    """Human-readable action label for an order's leg row."""
    extra = order.extra or {}
    action = extra.get("action") or ""
    if action in _ACTION_LABELS:
        return _ACTION_LABELS[action]
    # Fallback by side + position_effect
    pe = extra.get("position_effect", "")
    side = (order.side or "").lower()
    if side == "buy" and pe == "open":   return "Buy to open"
    if side == "buy" and pe == "close":  return "Buy to close"
    if side == "sell" and pe == "open":  return "Sell to open"
    if side == "sell" and pe == "close": return "Sell to close"
    return side.capitalize() or "Order"


def _render_execute_results(symbol: str, analysis, results: list[dict]) -> str:
    """Render the post-execute summary that swaps into the analysis panel.

    Shows full per-leg detail (action label · contract · qty · price · dollars)
    plus a net debit/credit roll-up at the bottom — same shape as the pre-trade
    Recommended-Trade card so the Board can confirm the trade matched intent.
    """
    import html as _html
    leg_rows: list[str] = []
    status_rows: list[str] = []

    any_filled = any(r["outcome"] == "filled" for r in results)
    any_failed = any(r["outcome"] != "filled" for r in results)
    any_dry_run = any(
        r["outcome"] == "filled"
        and isinstance(r.get("venue"), str)
        and r["venue"].endswith(":dry-run")
        for r in results
    )
    all_dry_run = any_dry_run and all(
        (r["outcome"] != "filled")
        or (isinstance(r.get("venue"), str) and r["venue"].endswith(":dry-run"))
        for r in results
    )

    # Sum debit/credit across legs for the net-result line.
    # buy at $X/sh × qty × 100 = debit (positive)
    # sell at $X/sh × qty × 100 = credit (negative dollars)
    net_dollars = 0.0
    has_signed_total = False    # True only if every filled leg has a price

    for r in results:
        order = r["order"]
        extra = order.extra or {}
        is_option = bool(extra.get("is_option", False))
        side = (order.side or "").lower()
        side_upper = side.upper()
        qty = order.qty
        action_label = _action_label_for(order)

        is_dry = (
            r["outcome"] == "filled"
            and isinstance(r.get("venue"), str)
            and r["venue"].endswith(":dry-run")
        )
        outcome = r["outcome"]

        # Status pill + icon
        if is_dry:
            status_color, status_icon, status_label = "warn", "🧪", "DRY-RUN"
        elif outcome == "filled":
            status_color, status_icon, status_label = "gain", "✅", "FILLED"
        elif outcome == "risk_rejected":
            status_color, status_icon, status_label = "loss", "🛑", "REJECTED"
        else:
            status_color, status_icon, status_label = "warn", "⚠", "ERROR"

        # Side badge color: buy = red (debit), sell = green (credit)
        side_color = "loss" if side == "buy" else "gain"

        # Contract description (option: expiry · strike · DTE · delta)
        contract_str = order.symbol
        if is_option:
            expiry = extra.get("expiration", "")
            strike = float(extra.get("strike") or 0)
            otype = (extra.get("option_type") or "call").upper()[:1]
            parts = [order.symbol]
            if expiry:
                parts.append(expiry)
            parts.append(f"${strike:,.2f}{otype}")
            if extra.get("dte") is not None:
                parts.append(f"{extra['dte']}d")
            if extra.get("delta") is not None:
                parts.append(f"δ{float(extra['delta']):.2f}")
            contract_str = " · ".join(parts)

        # Per-leg pricing + dollars
        price_per_share = r.get("fill_price")
        if price_per_share is None and outcome != "filled":
            # rejected/error rows show the original limit if known
            price_per_share = order.limit_price

        dollar_label = ""
        dollars_class = "text-muted"
        leg_dollars = 0.0
        if price_per_share is not None and outcome == "filled":
            multiplier = 100.0 if is_option else 1.0
            gross = price_per_share * qty * multiplier
            # Buy = debit (cost going out, positive); Sell = credit (negative)
            leg_dollars = gross if side == "buy" else -gross
            net_dollars += leg_dollars
            if leg_dollars > 0:
                dollar_label = f'<span class="private-money">-${gross:,.2f}</span> debit'
                dollars_class = "text-loss"
            elif leg_dollars < 0:
                dollar_label = f'<span class="private-money">+${gross:,.2f}</span> credit'
                dollars_class = "text-gain"
            else:
                dollar_label = '<span class="private-money">$0.00</span>'
        else:
            has_signed_total = False
            dollar_label = "(no fill)"

        # Track whether we have all the data needed for a meaningful net total
        if outcome == "filled" and price_per_share is not None:
            if not status_rows:   # first filled leg → flip on
                has_signed_total = True
        else:
            has_signed_total = has_signed_total and False if outcome != "filled" else has_signed_total

        # Leg row — 2 lines: contract + math
        price_str = (
            f'@ <span class="private-money">${price_per_share:,.2f}/sh</span>'
            if price_per_share is not None
            else "@ —"
        )
        venue_or_reason = ""
        if is_dry:
            venue_or_reason = f'<span class="text-[10px] text-warn ml-2">→ {_html.escape(r.get("venue",""))}</span>'
        elif outcome == "filled":
            venue_or_reason = f'<span class="text-[10px] text-muted ml-2">→ {_html.escape(r.get("venue",""))}</span>'
        elif outcome == "risk_rejected":
            venue_or_reason = (
                f'<div class="text-[10px] text-loss mt-1 leading-snug">'
                f'risk rejected: {_html.escape(r.get("detail",""))}</div>'
            )
        else:
            venue_or_reason = (
                f'<div class="text-[10px] text-warn mt-1 leading-snug">'
                f'error: {_html.escape(r.get("detail",""))}</div>'
            )

        leg_rows.append(
            f'<div class="py-2 border-b border-edge/40 last:border-0">'
            # Line 1: status icon + side pill + action label + contract details
            f'  <div class="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px] font-mono">'
            f'    <span class="text-base shrink-0">{status_icon}</span>'
            f'    <span class="text-[10px] uppercase px-1.5 py-0.5 rounded bg-{side_color}/15 text-{side_color} shrink-0">'
            f'{side_upper}</span>'
            f'    <span class="text-mono font-semibold w-[88px] shrink-0">{action_label}</span>'
            f'    <span class="text-mono">{_html.escape(contract_str)}</span>'
            f'    <span class="text-[10px] uppercase px-1.5 py-0.5 rounded bg-{status_color}/15 text-{status_color} ml-auto shrink-0">'
            f'{status_label}</span>'
            f'  </div>'
            # Line 2: qty × price = dollars
            f'  <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px] font-mono pl-7 mt-1">'
            f'    <span class="text-muted">×{qty} contracts {price_str}</span>'
            f'    <span class="ml-auto font-semibold {dollars_class}">{dollar_label}</span>'
            f'  </div>'
            # Optional venue/reason line
            f'  <div class="pl-7">{venue_or_reason}</div>'
            f'</div>'
        )
        status_rows.append(outcome)

    # Net result row (only meaningful if we have at least one filled leg with price)
    net_html = ""
    if has_signed_total and any_filled:
        if net_dollars > 0:
            net_label = "Net DEBIT (cost to execute)"
            net_str = f'<span class="private-money">-${net_dollars:,.2f}</span>'
            net_class = "text-loss"
        elif net_dollars < 0:
            net_label = "Net CREDIT (received)"
            net_str = f'<span class="private-money">+${abs(net_dollars):,.2f}</span>'
            net_class = "text-gain"
        else:
            net_label = "Net"
            net_str = '<span class="private-money">$0.00</span>'
            net_class = "text-mono"
        net_html = (
            '<div class="flex items-baseline justify-between mt-2 pt-2 border-t border-edge font-mono text-sm">'
            f'<span class="text-muted text-xs">{net_label}</span>'
            f'<span class="font-semibold {net_class}">{net_str}</span>'
            '</div>'
        )

    # Header
    if all_dry_run:
        header_color = "warn"
        header_text = "Dry-run · validated end-to-end (no live fills)"
    elif any_filled and not any_failed:
        header_color = "gain"
        header_text = "Executed"
    elif any_filled:
        header_color = "warn"
        header_text = "Partially executed"
    else:
        header_color = "loss"
        header_text = "Not executed"

    # Subhead — count summary
    n_filled = sum(1 for r in results if r["outcome"] == "filled")
    n_rejected = sum(1 for r in results if r["outcome"] == "risk_rejected")
    n_error = sum(1 for r in results if r["outcome"] not in ("filled", "risk_rejected"))
    subhead_parts = []
    if n_filled:   subhead_parts.append(f"{n_filled} placed")
    if n_rejected: subhead_parts.append(f"{n_rejected} rejected")
    if n_error:    subhead_parts.append(f"{n_error} error")
    subhead = " · ".join(subhead_parts) if subhead_parts else f"{len(results)} legs"

    return (
        '<div class="space-y-3">'
        # Header row
        f'<div class="flex items-baseline gap-2 flex-wrap">'
        f'  <span class="text-sm font-mono font-semibold text-{header_color}">{header_text}</span>'
        f'  <span class="text-[11px] text-muted font-mono">· {subhead}</span>'
        f'  <span class="text-[11px] text-muted font-mono ml-auto">{symbol}</span>'
        f'</div>'
        # Legs box
        '<div class="rounded-md bg-pane-2/40 border border-edge/60 px-3 py-1">'
        + "".join(leg_rows)
        + net_html +
        '</div>'
        # Footer caption
        '<div class="text-[10px] text-muted/70 font-mono pt-1">'
        + (
            "Real broker auth + risk gates ran. broker.place_order was skipped (dry-run)."
            if all_dry_run else
            "Refresh page or click another pair for fresh analysis."
        ) +
        '</div>'
        '</div>'
    )
