"""FastAPI app factory + lifespan.

The app is created with `create_app(deps)` where `deps` is a `WebDeps` dataclass
holding references to the live agents/brokers from main.py. Templates and static
assets are served from this package's `templates/` and `static/` directories.

The app runs on port 8000 by default (configurable). It's hosted as an asyncio
task inside the same process as trading_corp — see main.py's idle loop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent
_TEMPLATE_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"


@dataclass
class WebDeps:
    """Live references handed to the web app from main.py.

    The app reads these via `request.app.state.deps`. Held as plain references —
    no copies — so the dashboard always sees the latest state of the agents.
    """
    db_url: str
    db_path: str
    mode: str                       # "PAPER" or "LIVE"
    logger_agent: Any               # LoggerAgent
    data_exec: Any                  # DataExecAgent
    trend_agent: Any                # TrendAgent
    portfolio: Any                  # PortfolioAgent
    pmcc_agent: Any                 # PMCCAgent
    fidelity_agent: Any             # FidelityOptionsAgent
    paper_broker: Any               # PaperBroker (default fallback)
    secrets: Any                    # Secrets
    risk_agent: Any = None          # RiskAgent (used by Approve & Execute flow)
    dry_run: bool = False           # When True (LIVE only), broker.place_order is skipped
    # Optional — wired from main.py when the strategy is enabled.
    lord_otter_agent: Any = None    # LordOtterAgent (TradingView webhook recipient)
    market_cypher_agent: Any = None # MarketCypherAgent (second TV-driven agent, swing-style)
    telegram_channel: Any = None    # TelegramChannel for push notifications from webhooks
    # Research firm — Phase 1a only emits WatchlistRecommendation. None
    # in test envs. The agents/research/engagement.py:run_engagement
    # function is the public entry point; this dataclass holds the
    # compiled LangGraph + analysts so build cost is paid once at startup.
    research_firm: Any = None       # ResearchFirmDeps from agents/research/engagement.py
    # Phase B.1 of HITL-in-app — process-wide PendingApprovalRegistry.
    # The /approvals routes read this for the index/detail and resolve
    # via it for POST /decide. Constructed in main.py before
    # TelegramChannel so the channel can register its message-send as
    # a notifier. None in test envs that don't exercise the web HITL
    # surface.
    pending_registry: Any = None    # PendingApprovalRegistry from comms/pending_registry.py


def create_app(deps: WebDeps) -> FastAPI:
    """Build the FastAPI app and wire routes."""
    app = FastAPI(
        title="Trading Corp Command Center",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # Live deps available on every request via request.app.state.deps
    app.state.deps = deps

    # Templates
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
    # Useful Jinja filters for dollar/pct formatting
    templates.env.filters["money"] = _fmt_money
    templates.env.filters["money_signed"] = _fmt_money_signed
    templates.env.filters["strike"] = _fmt_strike
    templates.env.filters["pct"] = _fmt_pct
    templates.env.filters["pct_signed"] = _fmt_pct_signed
    templates.env.filters["compact_num"] = _fmt_compact
    # ET timestamp formatters (Board direction 2026-05-09: dashboard times in ET)
    from trading_corp.utils.time import format_et_hms, format_et_short, format_et_full
    templates.env.filters["et_hms"] = format_et_hms
    templates.env.filters["et_short"] = format_et_short
    templates.env.filters["et_full"] = format_et_full
    app.state.templates = templates

    # Static
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Routes — registered in routes.py to keep this file lean
    from trading_corp.web import routes
    routes.register(app)

    # Health endpoint (no template, just JSON) so the user can curl-test
    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "mode": deps.mode}

    # Generic 404 → render the base shell with a "not found" message
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return HTMLResponse(
            templates.get_template("not_found.html").render({"request": request}),
            status_code=404,
        )

    log.info("Web command center initialized — mode=%s", deps.mode)
    return app


# ── Jinja filters ─────────────────────────────────────────────────────────

def _privatize(formatted: str) -> Markup:
    """Wrap a formatted dollar value in a span the privacy toggle can mask.

    The privacy button in the header flips `body.privacy-on`, and CSS blurs
    every `.private-money` span. Only personal financial values get this
    wrapping — option strikes (strategy info, not personal wealth) stay
    visible. Returns Markup so Jinja doesn't re-escape the HTML.
    """
    return Markup(f'<span class="private-money">{formatted}</span>')


def _fmt_money(v: Any) -> str:
    """Always-precise dollar format. Never round.

    Trading dashboard rule: cents matter. A strike of $17.50 must NOT
    display as $18, an option mark of $0.05 must NOT display as $0, and
    even at six-figure equity totals, the user expects the exact value.
    """
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return _privatize(f"${n:,.2f}")


def _fmt_money_signed(v: Any) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if n >= 0 else "−"
    return _privatize(f"{sign}${abs(n):,.2f}")


def _fmt_strike(v: Any) -> str:
    """Option strike — always 2 decimals (matches every options platform).

    NOT wrapped in `.private-money` — strikes are strategy parameters
    (publicly visible on every options chain), not personal financial state.
    Hiding them would obscure the trade structure without protecting any
    actual personal financial information.
    """
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"${n:,.2f}"


def _fmt_pct(v: Any, places: int = 1) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{n*100:.{places}f}%"


def _fmt_pct_signed(v: Any, places: int = 2) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if n >= 0 else "−"
    return f"{sign}{abs(n)*100:.{places}f}%"


def _fmt_compact(v: Any) -> str:
    """Compact human-readable big numbers: 1.2K, 3.4M, 5.6B.

    Used for dollar amounts (equity, P&L) so wrap in `.private-money`.
    """
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    abs_n = abs(n)
    sign = "-" if n < 0 else ""
    if abs_n >= 1e9:
        formatted = f"{sign}{abs_n/1e9:.1f}B"
    elif abs_n >= 1e6:
        formatted = f"{sign}{abs_n/1e6:.1f}M"
    elif abs_n >= 1e3:
        formatted = f"{sign}{abs_n/1e3:.1f}K"
    else:
        formatted = f"{sign}{abs_n:.0f}"
    return _privatize(formatted)
