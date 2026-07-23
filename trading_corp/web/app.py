"""FastAPI app factory + lifespan.

The app is created with `create_app(deps)` where `deps` is a `WebDeps` dataclass
holding references to the live agents/brokers from main.py. Templates and static
assets are served from this package's `templates/` and `static/` directories.

The app runs on port 8000 by default (configurable). It's hosted as an asyncio
task inside the same process as trading_corp — see main.py's idle loop.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
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
    # BitUnix Futures Phase 3.0 observer (additive, observer-mode only —
    # no orders, no risk-gate, just classifies inbound Otter/Cypher
    # signals into bias-only tiers and writes audit_event rows).
    bitunix_observer: Any = None    # BitunixFuturesObserver | None
    # PR 2 — BitUnix HTF context provider. Wraps the 1H/4H/1D
    # LiveBarCaches plus a funding-rate fetcher. Read-only in PR 2:
    # the dashboard panel calls `provider.regime_snapshot(config)`
    # to render the live regime classification. PR 3 wires the
    # observer to consult it before passing trades through the gate.
    bitunix_htf_provider: Any = None  # BitUnixHTFContextProvider | None

    # Phase IC1 (2026-05-17): Robinhood Joint Iron Condor strategy
    # + division shell. The web app's HITL approval handler reads
    # `ic_strategy` (the strategy module) when a Board approve POST
    # arrives on a combo, and calls
    # `_ic_orchestration.dispatch_approved_ic_combo(...)` to fire
    # `data_exec.place_combo` + the state callback in one chain.
    # The batcher is per-strategy so other strategies can share or
    # bring their own.
    ic_division: Any = None             # RobinhoodJointAgent | None
    ic_strategy: Any = None             # RobinhoodJointIronCondorAgent | None
    ic_telegram_batcher: Any = None     # TelegramBatcher | None
    pending_combo_registry: Any = None  # PendingComboRegistry | None — read
                                        # by /approvals routes to render +
                                        # resolve combo approval cards.

    # Tasty Options sibling division (2026-05-24, Commit 4/5). Parallel
    # to the ic_* fields above — separate strategy/division/batcher/
    # registry so audit ownership stays clean across the two IC
    # divisions. /telemetry/iron_condor?division=tasty_options switches
    # the live-view + grader endpoints onto these handles.
    tasty_division: Any = None             # TastyOptionsAgent | None
    tasty_strategy: Any = None             # TastyOptionsIronCondorAgent | None
    tasty_telegram_batcher: Any = None     # TelegramBatcher | None
    tasty_pending_combo_registry: Any = None  # PendingComboRegistry | None
    pmcc_pending_combo_registry: Any = None   # PendingComboRegistry | None — PMCC roll_short atomic combos (Phase A)


def create_app(deps: WebDeps) -> FastAPI:
    """Build the FastAPI app and wire routes."""
    app = FastAPI(
        title="Trading Corp Command Center",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # Live deps available on every request via request.app.state.deps
    app.state.deps = deps

    # Stage-1 paper-mode dashboard surfaces:
    #   • git_sha           — short SHA of the deployed commit. Set via
    #                         GIT_SHA env-var by the deploy script; falls
    #                         back to "unknown" when unset (current state
    #                         until the redeploy script gets the follow-up
    #                         enhancement to populate this).
    #   • live_since_utc    — moment this process started serving. Used by
    #                         the Stage-1 header badge to show the "live"
    #                         duration of the current bitunix execution_mode
    #                         (which is read once at startup from
    #                         strategies.yaml and never hot-reloaded).
    app.state.git_sha = os.environ.get("GIT_SHA", "unknown")
    app.state.live_since_utc = datetime.now(timezone.utc)

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
    # Stage-1 header badge resolver — called by base.html with `request`
    # so it can read app.state.deps + app.state.git_sha + app.state.live_since_utc.
    templates.env.globals["stage1_badge"] = _stage1_badge_data
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


def _format_live_since(start_utc: datetime, now_utc: datetime) -> str:
    """Compact human duration: 'just now', '47m', '2h 14m', '3d 4h'."""
    delta = now_utc - start_utc
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hrs = mins // 60
    mins_rem = mins % 60
    if hrs < 24:
        return f"{hrs}h {mins_rem}m" if mins_rem else f"{hrs}h"
    days = hrs // 24
    hrs_rem = hrs % 24
    return f"{days}d {hrs_rem}h" if hrs_rem else f"{days}d"


def _stage1_badge_data(request: Request) -> dict:
    """Resolve the Stage-1 paper-mode header badge data from app state.

    Read at template-render time (not request-arrival) so each page sees a
    fresh `live_since` label without coupling to the route handlers. The
    badge surfaces the bitunix_futures division specifically — that is
    where Stage-1 lives. Multi-division execution_mode visibility is a
    separate tile concept and is intentionally out of scope.

    Returns a dict with these keys (all strings, safe for direct render):
      - execution_mode: 'paper' | 'live' | 'unwired' | 'unknown'
      - git_sha: short SHA or 'unknown'
      - live_since_label: '2h 14m' etc
      - live_since_iso: ISO-8601 UTC for the tooltip
      - division: the division this badge tracks (always 'bitunix_futures')
    """
    app_state = request.app.state
    deps = getattr(app_state, "deps", None)
    obs = getattr(deps, "bitunix_observer", None) if deps else None
    if obs is None:
        execution_mode = "unwired"
    else:
        execution_mode = getattr(obs, "execution_mode", "unknown") or "unknown"

    git_sha_full = getattr(app_state, "git_sha", "unknown") or "unknown"
    git_sha = git_sha_full[:7] if git_sha_full != "unknown" else "unknown"

    live_since_utc = getattr(app_state, "live_since_utc", None)
    if live_since_utc is None:
        live_since_label = "—"
        live_since_iso = "—"
    else:
        now_utc = datetime.now(timezone.utc)
        live_since_label = _format_live_since(live_since_utc, now_utc)
        live_since_iso = live_since_utc.strftime("%Y-%m-%d %H:%M:%SZ")

    return {
        "execution_mode": execution_mode,
        "git_sha": git_sha,
        "live_since_label": live_since_label,
        "live_since_iso": live_since_iso,
        "division": "bitunix_futures",
    }


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
