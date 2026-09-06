"""pm_web ASGI app (FastAPI) -- standalone Prediction Markets web. Launched by scripts/pm_web.py (uvicorn).

STANDALONE by construction: imports ONLY fastapi + the PM package (db / stats / category). NO engine imports
(trading_corp.web / main / agents), NO WebDeps, NO agent handles -- proven by test_pm_web_imports_no_engine.
(stats pulls trading_corp.data.kalshi_whale_stats for the ranking primitives -- data layer, NOT web/main/agents.)
Reads/writes ONLY data/prediction_markets.db (prediction_markets.db._assert_not_legacy hard-guards the path).
Reuses the engine web IDIOM (FastAPI + the off-loop `asyncio.to_thread` read pattern, mace_view) but not the process.

ROUTES (M2 2026-09-01: multi-account -- / is the ACCOUNTS OVERVIEW, the new top of the hierarchy, R1):
  GET  /                              -> accounts overview (pm_accounts.html): per-account PM P&L, DISPLAY-ONLY
  GET  /account/{account_id}         -> one account's per-sub-division P&L (pm_account.html); display-only note if untraded
  GET  /farm                          -> the Farm-League category tiles (pm_farm_league.html)
  GET  /farm/{category}               -> the per-category page: Watchlist (paper) + Prospects (completed)
  GET  /whale/{wallet}[/{category}]   -> a PROSPECT / completed drill-through (reuses pm_position_rows.html)
  GET  /watchlist/{wallet}/{category} -> a WATCHLIST / paper detail (all paper trades + paper stats)
  POST /farm/analyze/{wallet}/{category} -> on-demand Analyze (the ONE write surface besides sync-names)
  GET  /healthz                       -> liveness + PM-DB readiness
Vendored assets ONLY (/static/htmx.min.js, /static/pm.css) -- NO CDN on a network-exposed host, no build step.
Spec: reports/prediction_markets/PM_REBUILD_PLAN_2026-08-26.md (Stage 2, the phase-3 repoint deliverable).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..db import connect
from .. import stats, positions, names, farm, farm_actions, analyze, subdivision, search, loss_grounding, arm, shard_snapshot, heartbeat
from . import authz   # M4: fail-closed identity/admin resolution + account-visibility scoping (reads headers+env only)
from ..market_describe import describe_market
from ..category import NON_SINGLE_GAME_CATEGORIES, derive_category_from_slug

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"

# loopback-only + behind Authelia => no OpenAPI/docs surface exposed.
app = FastAPI(title="pm_web", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
# Expose the non-single-game category set so the template can render single_game_pct as "n/a" (NOT 0%) for
# fed/unknown -- the whole reason OQ-2 was ruled NULL. Truth stays in category.py; the template only reads it.
templates.env.globals["non_single_game_categories"] = sorted(NON_SINGLE_GAME_CATEGORIES)
# R4: the F-1 loss-omission caveat has ONE definition (search.LOSS_OMISSION_CAVEAT) so the prospects screen
# and any report word the bias identically. The thin-sample FLOOR (candidates below it came via the <10-qualifier
# fallback) is likewise the ruled constant -- a candidate with n_resolved < floor is thin-sample by construction.
templates.env.globals["loss_omission_caveat"] = search.LOSS_OMISSION_CAVEAT
templates.env.globals["thin_sample_floor"] = search.DEFAULT_MIN_RESOLVED_FLOOR
# Plain-language market descriptions (interim item a): translate a Kalshi ticker (+ held leg) to a human sentence
# in the /live table. Pure/standalone -- market_describe imports only the data-layer team map + ticker parsers.
# The RAW ticker stays shown beneath it (translated + raw -- honest, and the raw is still there for precision).
templates.env.globals["describe_market"] = describe_market


def _utcdate(ts) -> str:
    """unix ts -> 'YYYY-MM-DD' (UTC); em-dash for missing/zero. Registered as the `utcdate` Jinja filter
    for the position-row renderer (honest-empty date, never a fake 1970)."""
    if isinstance(ts, (int, float)) and ts:
        return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))
    return "—"


templates.env.filters["utcdate"] = _utcdate


def _utcdatetime(ts) -> str:
    """unix ts -> 'YYYY-MM-DD HH:MM:SSZ' (UTC); em-dash for missing/zero. Registered as the `utcdt` Jinja filter
    for the live-trade rows, where an intraday order needs the TIME, not just the date (an order and its exit can
    share a date)."""
    if isinstance(ts, (int, float)) and ts:
        return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(int(ts)))
    return "—"


templates.env.filters["utcdt"] = _utcdatetime


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


# ── whale detail + drill-through (COMPLETED basis -- the PROSPECT drill, Ruling R5) ───────────────

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
    link (and JS-off navigation from a prospect caveat cell) lands on the right rows."""
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


# ── on-demand ANALYZE (CP3b-2) -- the [Analyze] button on the Watchlist rows ─────────────────────
# This is the ONE pm_web write surface besides sync-names: it writes pm_analysis_cache + pm_analysis_cost in
# the PM DB (both inside the unit's ReadWritePaths=data). A cache hit spends nothing; the $20/day cap and the
# "only successful verdicts are cached" rule live in analyze.analyze_whale. The key is NOT wired (e3) so today
# every call returns the llm_unavailable reasoned-null and the deterministic report renders without a verdict.

def _row_category(row) -> str:
    """Category of an Activity/Closed row via the platform's TIER-1 slug-prefix deriver (sync, NO network) -- the
    SAME axis pm_closed_position.category is built on, so the re-grounded loss set filters on the same category as
    the deterministic core. Rows tier-1 cannot place (unknown) fall OUT of the category filter -> for them a_only is
    a conservative LOWER bound, never an over-count. (No tier-2 gamma-tag join here: grounding is a caveat, and one
    network round per analyze click is already the /activity + /closed-positions paging.)"""
    return derive_category_from_slug(getattr(row, "event_slug", None), getattr(row, "slug", None))[0]


def _run_analyze(wallet: str, category: str, force: bool, now_ts: int, loss_grounding=None) -> dict:
    """Run Analyze on ONE short-lived connection, OFF the event loop. WRITES the PM DB (cache + cost ledger);
    reads/writes ONLY prediction_markets.db (db._assert_not_legacy guards the path). Sync (analyze narrates
    synchronously) so it drops straight into asyncio.to_thread with no nested event loop. `loss_grounding` (Stage 5)
    is the re-grounded loss set the async route fetched on a miss; None -> the report renders ungrounded."""
    with connect() as conn:
        rep = analyze.analyze_whale(conn, wallet, category, now_ts=now_ts, force=force, loss_grounding=loss_grounding)
        day = analyze._utc_day(now_ts)
        spent, n_calls = analyze.daily_cost(conn, day)
    return {"report": rep, "flags": analyze.analysis_flags(rep),
            "cost_today": spent, "cost_cap": analyze.PM_ANALYZE_DAILY_CAP_USD, "cost_day": day}


def _analysis_is_cached(wallet: str, category: str) -> bool:
    """Off-loop cache PEEK: is there a stored verdict for this (wallet, category, current skill_version)? Governs
    whether the route pays for the /activity loss-grounding fetch AT ALL -- a cache HIT skips the network entirely,
    so the 'a hit spends nothing' contract holds for the /activity fetch too, not just the LLM call."""
    with connect() as conn:
        return analyze.is_cached(conn, wallet, category)


async def _ground_losses(wallet: str, category: str):
    """Re-ground the whale's LOSS set for (wallet, category) from /activity (Stage 5, the F-1 held-to-worthless
    bias). Returns a LossGrounding or None. FAIL-SOFT: any network/parse failure -> None and Analyze proceeds
    UNGROUNDED (the honest-loss block simply does not render) -- the grounding is a caveat enrichment, NEVER
    load-bearing for the deterministic report. Network runs ON the loop (its awaits yield); the lazy import keeps
    the data-layer client off the module import surface (the standalone-imports guard, test_pm_web_imports_no_engine)."""
    try:
        from ...data.polymarket_data_api_client import PolymarketDataAPIClient
        async with PolymarketDataAPIClient() as client:
            g = await loss_grounding.fetch_and_ground_losses(client, wallet, category, category_of=_row_category)
        # Only surface a grounded block when /activity actually yielded in-category held-to-resolution decisions to
        # compare against. A zero-decision fetch has NOTHING to affirm -- rendering "0W/0L honest" would imply we
        # checked and found nothing when we may simply have no activity feed for this slice -- so treat it as
        # UNGROUNDED (no block), the honest degrade.
        return g if (g is not None and g.n_activity_held_resolved > 0) else None
    except Exception as exc:   # noqa: BLE001 -- caveat enrichment must never break Analyze; degrade to ungrounded
        log.warning("pm analyze: loss-grounding failed for %s/%s (%s) -- rendering ungrounded",
                    wallet[:10], category, type(exc).__name__)
        return None


@app.post("/farm/analyze/{wallet}/{category}", response_class=HTMLResponse)
async def farm_analyze(request: Request, wallet: str, category: str, force: str | None = None):
    """Analyze a (wallet, category) and swap in the result partial. POST because it may WRITE (narrate ->
    cache + cost). `?force=1` re-analyzes (evicts the cached verdict first). Identity is the (wallet, category),
    not which list the button sat in. Stage 5: re-ground the loss set from /activity ONLY when we will actually
    (re)build -- a cache HIT skips the /activity fetch, so a hit still spends nothing."""
    wallet = (wallet or "").lower()
    category = (category or "").strip().lower()
    do_force = str(force or "").strip().lower() in ("1", "true", "yes", "on")
    now_ts = int(time.time())
    grounding = None
    if do_force or not await asyncio.to_thread(_analysis_is_cached, wallet, category):
        grounding = await _ground_losses(wallet, category)               # async network ON the loop; None on failure
    data = await asyncio.to_thread(_run_analyze, wallet, category, do_force, now_ts, grounding)
    return templates.TemplateResponse(request, "partials/pm_analyze_result.html", {"request": request, **data})


# ── THE FARM-LEAGUE HIERARCHY (Stage 2) -- these ARE the app's screens ────────────────────────────
# Phase 3 repointed these onto the good URLs (/ , /farm, /farm/{category}) and RETIRED the flat scoreboard/farm
# pages they replace. READ-ONLY: no write, no rollup, no migration. The two per-category regions read SEPARATE
# bases by construction: Watchlist(pinned) -> pm_paper_category_stats (paper); Prospects(candidate) ->
# query_scoreboard over pm_category_stats (completed). The three-lists / three-bases invariant holds on one page.

def _load_farm_league() -> dict:
    """Farm-League tile read. Tiles = `farm.league_categories()` = the RULED 15-category allowlist (Jack
    2026-08-30, the tile-vanish fix). A category EXISTS iff it is in the allowlist and not deactivated (deactivated
    = not in the allowlist). NOT driven by pinned rows: an empty watchlist is legitimate, so a category with
    prospects-but-no-pinned (or with neither) STILL renders its tile -- data stranded behind a missing tile is the
    class of defect this closes. The pair-grain active flag governs list membership, not tile existence. The
    allowlist is a constant -> no DB read. OFF the loop."""
    return {"categories": farm.league_categories()}


def _load_farm_category(category: str, now_ts: int) -> dict | None:
    """Per-category read. Returns None when `category` is NOT a league category (not in the allowlist: deactivated
    / unknown / nonexistent) so the route can 404 -- a deactivated category must not be reachable by URL. Existence
    is `farm.is_league_category` = allowlist membership (Jack 2026-08-30), NOT pinned rows: an allowlist category
    with an EMPTY WATCHLIST renders normally (Watchlist honest-empty, Prospects populated -- the two sections read
    different bases, and Prospects does not depend on the watchlist at all), so its prospects are never stranded.

    THE BASIS SEPARATION IS THE POINT (three lists / three bases):
    - WATCHLIST (pinned) -> `farm.farm_rows(status=PINNED)` -> pm_paper_category_stats (PAPER basis).
    - PROSPECTS (candidate) -> the F-4 repurposed `stats.query_scoreboard` RANKER -> pm_category_stats
      (COMPLETED basis), SCOPED to this category's candidate set. query_scoreboard already active-gates,
      category-scopes and ranks; we filter its board to the candidate wallets so the section shows candidates
      ONLY (never pinned). The two sections never share a query or a table."""
    if not farm.is_league_category(category):   # existence = allowlist membership, NOT pinned rows (Jack 2026-08-30)
        return None
    with connect() as conn:
        watchlist = farm.farm_rows(conn, status=farm.PINNED, category=category)        # PAPER basis (pinned) -- [] when empty
        cand_wallets = {r["wallet"] for r in
                        farm.farm_rows(conn, status=farm.CANDIDATE, category=category)}   # candidate SET (active-gated)
        board = stats.query_scoreboard(conn, category=category)                         # completed-basis ranker (F-4)
        prospects = [r for r in board if r["wallet"] in cand_wallets]                   # ranked, candidates only
        # R4 DEFAULT ORDER = cost-ROI DESCENDING (Jack ruled): the first view is what gets looked at most. The
        # ranker's own ORDER BY leads with score; here the SCREEN's default axis is cost-ROI (roi-None sorts last).
        # The client-side column sort re-orders on demand; this only sets the LOAD order.
        prospects.sort(key=lambda r: (r.get("roi") is None, -(r.get("roi") or 0.0)))
        for r in prospects:
            r["flags"] = stats.scoreboard_flags(r)                                      # same tokens as CLI/scoreboard
            # THIN-SAMPLE (visible, not inferable): a candidate BELOW the N floor came in via the <10-qualifier
            # top-10 fallback (the normal path needs n>=floor), so n_resolved<floor IS thin-sample -- no new column.
            r["thin_sample"] = (r.get("n_resolved") or 0) < search.DEFAULT_MIN_RESOLVED_FLOOR
            # LAST-UPDATED per whale (on-demand ruling): staleness VISIBLE per-whale, never silent.
            r["last_refresh"] = stats.refresh_band_state(r.get("last_refresh_ts"), now_ts)
        refresh = stats.refresh_band_state(stats.max_refresh_ts(conn), now_ts)
        # R6: the ACTIVE accounts = the promote-to-LIVE targets. Auto-create (ruling 1) makes the (account,
        # category) sub-division on demand, so a Watchlist row offers "promote to <account>", not a pre-existing
        # sub-division. Empty until an account is provisioned (credentialed) -> the honest inert note.
        live_accounts = subdivision.active_accounts(conn)
    return {"category": category, "watchlist": watchlist, "prospects": prospects, "refresh": refresh,
            "live_accounts": live_accounts}


# ── Multi-account (M2, 2026-09-01): the accounts overview (the new top of the hierarchy, R1) + per-account page. ──
# DISPLAY-ONLY (ruled): these render PM's journal-derived P&L per account; they carry NO arm/attach control (R4:
# the global arm STATE is visible read-only, the CONTROL is admin-only + M5). An account with 0 PM sub-divisions is
# NOT traded by PM -- the page states that in the copy, never an empty frame implying it will fill (per-account
# TRADING is a filed, gated phase -- NOT_SCOPED_REVIEW_2026-09-01.md). realized/win-loss/SAMPLE/open-at-cost are
# shown SEPARATELY with the thin-sample caveat travelling WITH the number (the R2c display discipline).

def _annotate_pnl(a: dict, floor: int) -> None:
    a["pm_traded"] = a.get("n_subdivisions", 0) > 0
    a["thin_sample"] = a.get("n_closed", 0) < floor


# M4 account SCOPING: the overview + the account page are filtered by web.authz.visible_account_ids (fail-closed --
# admin sees all; a non-admin sees ONLY accounts whose owner_identity == their Authelia identity; a NULL owner is
# admin-only; no identity -> nothing). identity + admin are resolved ON the loop from the request (headers+env, cheap)
# and passed into the OFF-loop loader, so the thread never touches the request object. _FORBIDDEN distinguishes an
# account that EXISTS but this identity may not see (403) from one that does not exist (None -> 404).
_FORBIDDEN = object()


def _load_accounts_overview(identity: str | None = None, is_admin_flag: bool = False) -> dict:
    """Every VISIBLE active account with its PM realized P&L / win-loss / sample size / open-at-cost + whether PM
    trades it, a COMPACT balance (latest snapshot total + age band), plus the GLOBAL arm state (read-only, R4). The
    account set is SCOPED to `identity`/`is_admin_flag` (fail-closed) BEFORE any balance read -- a non-admin never
    sees, nor loads a balance for, an account that is not theirs. OFF the loop; PM DB + a read-only legacy
    agent_state read. NEVER a venue read (pm_web is credential-free)."""
    floor = search.DEFAULT_MIN_RESOLVED_FLOOR
    with connect() as conn:
        accounts = subdivision.accounts_overview(conn)                    # each row carries owner_identity (M4)
        visible = authz.visible_account_ids(identity, is_admin_flag, accounts)
        accounts = [a for a in accounts if a["account_id"] in visible]    # SCOPE first -> then read balances
        # L3 DRIVER LIVENESS (read-only): the EXPECTED (attachment-gated) set + latest heartbeats, banded by age. This
        # is the signal arm state cannot give -- 'is the driver actually cycling this sub right now?'. table_present
        # distinguishes 'migration 020 not applied' from 'applied, engine hasn't written yet'. Scoped per-account below
        # (a non-admin never sees another account's liveness -- `accounts` is already visibility-scoped).
        liveness_present = heartbeat.table_present(conn)
        all_liveness = heartbeat.read_liveness(conn)                       # now_ts defaults to time.time() (live age)
        for a in accounts:
            a["shard_snap"] = shard_snapshot.read_latest(conn, a["account_id"])   # None -> tile omits balance (honest)
            a["liveness"] = [r for r in all_liveness if r.account_id == a["account_id"]]
            # ★ ABSENT-vs-EMPTY: with the table ABSENT (monitor not deployed) read_liveness still classes the expected
            # set NEVER -- that must read NEUTRAL 'not deployed', NOT a red alarm (don't cry wolf about the monitor).
            a["liveness_alarm"] = liveness_present and heartbeat.any_alarm(a["liveness"])
    for a in accounts:
        _annotate_pnl(a, floor)
    # is_admin gates the HONEST cross-console arm link (M5): the arm/disarm CONTROL lives on the ENGINE console
    # (trading.jacksumner.com/pm/arm), NOT here -- pm_web only DISPLAYS the arm state (R4). Non-admins never see the link.
    visible_liveness = [r for r in all_liveness if r.account_id in visible]   # the panel: only visible accounts' subs
    return {"accounts": accounts, "global_arm": arm.read_status(), "thin_floor": floor, "is_admin": is_admin_flag,
            "liveness_present": liveness_present, "liveness": visible_liveness,
            "any_liveness_alarm": liveness_present and heartbeat.any_alarm(visible_liveness)}


def _load_account(account_id: str, identity: str | None = None, is_admin_flag: bool = False):
    """One account's per-sub-division P&L breakdown + the global arm state (read-only). None -> 404 (account absent
    or inactive); `_FORBIDDEN` -> 403 (account EXISTS but this identity may not see it -- fail-closed scoping). The
    display-only note is data-driven (0 sub-divisions -> PM does not trade it)."""
    floor = search.DEFAULT_MIN_RESOLVED_FLOOR
    with connect() as conn:
        accts = {a["account_id"]: a for a in subdivision.active_accounts(conn)}   # carries owner_identity (M4)
        if account_id not in accts:
            return None                                                  # does not exist -> 404
        if account_id not in authz.visible_account_ids(identity, is_admin_flag, accts.values()):
            return _FORBIDDEN                                            # exists but not yours -> 403
        agg = subdivision.account_pnl(conn, account_id)
    meta = accts[account_id]
    agg["account_label"] = meta.get("account_label")
    agg["venue"] = meta.get("venue")
    _annotate_pnl(agg, floor)
    for b in agg["subdivisions"]:
        _annotate_pnl(b, floor)
    # M3 balance: the LATEST per-shard snapshot (+ its age band) + the two distinct honest-empty states (table absent
    # vs present-but-empty) + the shard-0-direction line (return-to-3 vs sweeping). All from the snapshot -- never the venue.
    with connect() as conn:
        snap = shard_snapshot.read_latest(conn, account_id)
        snap_table = shard_snapshot.table_present(conn)
        snap_dir = shard_snapshot.shard_direction(conn, account_id)
        # L3 DRIVER LIVENESS per sub-division (read-only), keyed by category so each breakdown row shows its badge.
        liveness_present = heartbeat.table_present(conn)
        live_rows = {r.category: r for r in heartbeat.read_liveness(conn) if r.account_id == account_id}
    for b in agg["subdivisions"]:
        b["liveness"] = live_rows.get(b.get("category"))                  # None -> sub with no expected-set row (rare)
    return {"account": agg, "global_arm": arm.read_status(), "thin_floor": floor,
            "shard_snap": snap, "shard_snap_table": snap_table, "shard_dir": snap_dir,
            "liveness_present": liveness_present, "liveness_rows": list(live_rows.values()),
            "liveness_alarm": liveness_present and heartbeat.any_alarm(list(live_rows.values()))}


@app.get("/", response_class=HTMLResponse)
async def accounts_overview_page(request: Request):
    """The ACCOUNTS OVERVIEW -- the top of the hierarchy (R1: replaces the old 2-card dashboard). Per-account PM
    P&L, whether PM trades each account, and the global arm state (read-only). Read-only; no arm/attach control.
    SCOPED (M4): the list shows only the accounts the requester may see (admin -> all)."""
    identity, is_admin_flag = authz.current_identity(request), authz.is_admin(request)
    data = await asyncio.to_thread(_load_accounts_overview, identity, is_admin_flag)
    return templates.TemplateResponse(request, "pm_accounts.html", {"request": request, **data})


@app.get("/account/{account_id}", response_class=HTMLResponse)
async def account_page(request: Request, account_id: str):
    """One account's PM sub-divisions with per-sub-division P&L (realized / win-loss / sample / open-at-cost). A
    display-only account (0 PM sub-divisions) states its limitation in the copy. Unknown/inactive -> 404; an account
    that exists but is not the requester's (and they are not admin) -> 403 (M4 scoping). Read-only."""
    account_id = (account_id or "").strip()
    identity, is_admin_flag = authz.current_identity(request), authz.is_admin(request)
    data = await asyncio.to_thread(_load_account, account_id, identity, is_admin_flag)
    if data is None:
        return templates.TemplateResponse(request, "pm_account_404.html",
                                          {"request": request, "account_id": account_id}, status_code=404)
    if data is _FORBIDDEN:
        return PlainTextResponse("forbidden: not your account", status_code=403)
    return templates.TemplateResponse(request, "pm_account.html", {"request": request, **data})


@app.get("/farm", response_class=HTMLResponse)
async def farm_league_page(request: Request):
    """The Farm-League category tiles (the active Kalshi-copyable categories, data-driven). Each tile links to its
    per-category page. (Phase 3 repointed this from /farm-league onto /farm; the flat farm page it replaces was
    retired.)"""
    data = await asyncio.to_thread(_load_farm_league)
    return templates.TemplateResponse(request, "pm_farm_league.html", {"request": request, **data})


@app.get("/farm/{category}", response_class=HTMLResponse)
async def farm_league_category(request: Request, category: str):
    """The per-category page: Watchlist (paper) on top, Prospects (completed) below. A category NOT in the active
    tile set (removed / unknown / nonexistent) is NOT reachable -> 404, never a fabricated page. (Phase 3 repointed
    this from /farm-league/{category} onto /farm/{category}.)"""
    category = (category or "").strip().lower()
    data = await asyncio.to_thread(_load_farm_category, category, int(time.time()))
    if data is None:
        return templates.TemplateResponse(
            request, "pm_category_404.html", {"request": request, "category": category}, status_code=404)
    return templates.TemplateResponse(request, "pm_farm_category.html", {"request": request, **data})


# ── THE THREE FARM ACTIONS (Stage 3 R6) -- the FIRST mutating POST routes besides Analyze ────────────────────
# WHAT PROTECTS THESE: Authelia at the reverse proxy (identity) + the M4 ADMIN GATE below (authorization). The app
# has NO pm_user/pm_role/pm_grant table (Authelia owns identity); the gate is a single fail-closed check --
# `_forbid_if_not_admin` -- enforced SERVER-SIDE at the top of each mutating route. This is the boundary, NOT the
# hidden button: a non-admin who POSTs directly (curl, replay, a stale tab) is REFUSED here regardless of what the
# UI rendered. Analyze is deliberately NOT gated (Karen may run it -- the promotion judge, spend-capped). WHAT IS
# STILL NOT COVERED: there is no CSRF token -- but every action is IDEMPOTENT (a double-submit / concurrent click is
# a no-op, never a duplicate); mutation is POST-ONLY (no GET mutates -- a crawler/prefetch/refresh cannot demote a
# whale); and NONE of these reaches the execution chokepoint (promote-to-live writes an ATTACHMENT row, never an
# order; pm_web imports no broker). Pattern = Post/Redirect/Get: the write runs OFF the loop, then a 303 redirect to
# the GET page (a browser refresh re-GETs, never re-POSTs -> double-submit-safe at the transport layer too).

def _forbid_if_not_admin(request: Request):
    """M4 SERVER-SIDE admin gate for the mutating farm/live POST routes. Returns a 403 PlainTextResponse if the
    requester is NOT an admin (fail-closed: no identity, or PM_ADMIN_IDENTITIES unset -> not admin), else None so the
    caller proceeds. Hiding the button is a UI HINT, not the gate -- THIS is the boundary; a direct POST from a
    non-admin (e.g. Karen) is refused here, server-side, whatever the page rendered. Analyze is NOT gated with this."""
    if not authz.is_admin(request):
        return PlainTextResponse("forbidden: admin only", status_code=403)
    return None


def _promote_watchlist(wallet: str, category: str, now_ts: int) -> dict:
    with connect() as conn:
        return farm_actions.promote_to_watchlist(conn, wallet, category, now_ts)


def _demote_prospect(wallet: str, category: str, now_ts: int) -> dict:
    with connect() as conn:
        return farm_actions.demote_to_prospect(conn, wallet, category, now_ts)


def _promote_live(account_id: str, category: str, wallet: str, now_ts: int) -> dict:
    with connect() as conn:
        return farm_actions.promote_to_live(conn, account_id, category, wallet, now_ts)


@app.post("/farm/{category}/promote/{wallet}")
async def promote_watchlist_action(request: Request, category: str, wallet: str):
    """PROMOTE-TO-WATCHLIST (Prospect -> Watchlist): candidate -> pinned. Idempotent; writes ONLY pm_watchlist.
    303 back to the category page (PRG). ADMIN-ONLY (M4, server-side)."""
    forbidden = _forbid_if_not_admin(request)
    if forbidden is not None:
        return forbidden
    category = (category or "").strip().lower()
    await asyncio.to_thread(_promote_watchlist, (wallet or "").lower(), category, int(time.time()))
    return RedirectResponse("/farm/%s" % category, status_code=303)


@app.post("/farm/{category}/demote/{wallet}")
async def demote_action(request: Request, category: str, wallet: str):
    """DEMOTE (Watchlist -> Prospect): pinned -> candidate. Idempotent; writes ONLY pm_watchlist -- pm_paper_trade
    rows are PRESERVED (F-5). 303 back to the category page. ADMIN-ONLY (M4, server-side)."""
    forbidden = _forbid_if_not_admin(request)
    if forbidden is not None:
        return forbidden
    category = (category or "").strip().lower()
    await asyncio.to_thread(_demote_prospect, (wallet or "").lower(), category, int(time.time()))
    return RedirectResponse("/farm/%s" % category, status_code=303)


async def _refresh_whale(wallet: str, now_ts: int) -> str:
    """The REFRESH BUTTON's work (Jack's on-demand ruling): a FULL ad-hoc re-pull of ONE whale's completed history
    (search_run.refresh_one -> ingest.refresh_wallet), then a rollup so the re-pulled data reflects in the
    completed-basis stats the Prospects table shows. NETWORK + SLOW (~30 calls). Returns the OUTCOME
    ('complete' | 'partial' | 'failed') so the caller can tell the operator WHY a whale changed/vanished.
    ★ SAFE ON FAILURE: a RAISED pull leaves the whale's prior complete data + backfill_complete=1 UNTOUCHED (it
    stays shown, unchanged); a CAP-TRUNCATED pull marks it partial (backfill_complete=0) -> the completeness gate
    drops it from the ranker (visibly), never half-populated-and-ranked. The pull runs ON the loop (its network
    yields); the blocking rollup runs OFF the loop (asyncio.to_thread) -- pm_web's loop is never stalled by the
    aggregate (this file's off-loop discipline). Lazy imports keep the client (data layer) + search_run off the
    module import surface (the standalone-imports guard)."""
    from ..search_run import refresh_one
    from ...data.polymarket_data_api_client import PolymarketDataAPIClient
    outcome = "failed"
    with connect() as conn:
        try:
            async with PolymarketDataAPIClient() as client:
                res = await refresh_one(conn, wallet, client=client, now_ts=now_ts)
            outcome = (res or {}).get("verdict") or "complete"   # 'complete' | 'partial'
        except Exception:
            outcome = "failed"   # a raised refresh -> the whale is UNCHANGED (safe); still rollup + re-render honest state
        await asyncio.to_thread(stats.rollup, conn, now_ts=now_ts)   # reflect the re-pull; OFF the loop (blocking aggregate)
    return outcome


# The operator-facing notice for a refresh that did NOT cleanly complete -- so a whale that DROPS off the ranked
# list (partial) or stays unchanged (failed) is EXPLAINED, never a silent vanish (staleness/incompleteness visible).
_REFRESH_NOTICE = {
    "partial": "Refresh came back INCOMPLETE (the pull truncated) -- this whale is now marked partial and DROPPED "
               "from the ranked list until a clean refresh. It is never ranked on partial data.",
    "failed":  "Refresh FAILED (network) -- this whale is UNCHANGED; its prior complete data is intact.",
}


@app.post("/farm/{category}/refresh/{wallet}")
async def refresh_action(request: Request, category: str, wallet: str):
    """REFRESH one prospect whale (POST-only -- no GET mutates, R6 discipline). Re-pulls its full completed history
    on demand + rolls up, then re-renders the Prospects section. SLOW (~30 calls, up to ~1 min): htmx shows the
    button disabled while it runs (hx-disabled-elt) so the operator sees it working and cannot double-fire; JS-off
    blocks on the browser's native load, then a 303 back to the page. A failed/partial refresh is SAFE (see
    _refresh_whale) -- the whale is never left half-populated or ranked on incomplete data, and a NOTICE explains a
    partial/failed outcome so a dropped whale is never a silent vanish. ADMIN-ONLY (M4, Jack ruled 2026-09-01):
    Karen is the promotion JUDGE (Analyze is judgment, ungated), but refresh is a ~30-call API pull against a SHARED
    budget -- a data-operator action, so it joins promote/attach/demote behind the server-side gate."""
    forbidden = _forbid_if_not_admin(request)
    if forbidden is not None:
        return forbidden
    category = (category or "").strip().lower()
    outcome = await _refresh_whale((wallet or "").lower(), int(time.time()))
    if request.headers.get("HX-Request"):
        data = await asyncio.to_thread(_load_farm_category, category, int(time.time()))
        if data is None:
            return templates.TemplateResponse(
                request, "pm_category_404.html", {"request": request, "category": category}, status_code=404)
        return templates.TemplateResponse(request, "partials/pm_prospects_rows.html",
                                          {"request": request, "refresh_notice": _REFRESH_NOTICE.get(outcome), **data})
    return RedirectResponse("/farm/%s" % category, status_code=303)


@app.post("/live/{account_id}/{category}/attach/{wallet}")
async def promote_to_live_action(request: Request, account_id: str, category: str, wallet: str):
    """PROMOTE-TO-LIVE: attach a pinned pair to the (account_id, category) sub-division (joined ON CATEGORY).
    Creates the ATTACHMENT and nothing else -- NEVER an order. Idempotent (no duplicate attachment). 303 to the
    sub-division page so the operator SEES the whale now in its copy list; a bad target is a no-op (honest).
    ADMIN-ONLY (M4, server-side) -- attach is the highest-stakes farm action (it is what makes an account copy a
    whale), so the gate matters most here; a non-admin POST is refused before any write."""
    forbidden = _forbid_if_not_admin(request)
    if forbidden is not None:
        return forbidden
    # account_id is an EXACT-MATCH slug PK (strip-only, NOT lowercased) -- consistent with R3's /live/{account_id}
    # route; wallet/category are normalized because they are case-insensitive by nature, a slug is not. A mixed-case
    # account_id simply misses -> honest no_such_subdivision no-op (never a wrong write).
    account_id = (account_id or "").strip()
    category = (category or "").strip().lower()
    await asyncio.to_thread(_promote_live, account_id, category, (wallet or "").lower(), int(time.time()))
    return RedirectResponse("/live/%s/%s" % (account_id, category), status_code=303)


# ── LIVE sub-divisions (Stage 3 R3) -- the top-of-hierarchy Account-Category tiles. READ-ONLY: renders tiles +
# sub-division config + an honest-empty live list. Places NOTHING, arms NOTHING, reaches NO order path (execution
# is R4+; pm_web imports no broker). DEFENSIVE: subdivision.* tolerate pm_account/pm_subdivision being absent
# (pre-migration-010) -> honest-empty, so /live deploys on a pm_web restart independent of the migration-010 deploy.

def _load_live_list() -> dict:
    """LIVE list read: the ACTIVE sub-divisions as tiles (tile-on-CREATE -- a tile the moment the sub-division
    exists, before it trades). No live-trade data (P3). OFF the loop, read-only."""
    with connect() as conn:
        subdivisions = subdivision.list_subdivisions(conn)
    return {"subdivisions": subdivisions}


def _load_live_subdivision(account_id: str, category: str) -> dict | None:
    """Per-sub-division read: its config + the whales it copies (R6 attachments) + the REAL live-trade journal
    (pm_subdivision_order, newest first) + the journal-derived open positions, or None -> 404. The live-trade
    section is now wired to real data (it was hardcoded honest-empty in R3, when the engine did not yet trade);
    it renders honest-empty only when this sub-division truly has no orders. Read-only -- no form, no order path,
    no arm control (detach is a CLI action, so /live stays read-only). `sizing_summary` states per-copy BEHAVIOUR
    (contracts), not just the stored stake (which floors to 1 contract at $0.01 and misleads about cost)."""
    with connect() as conn:
        sub = subdivision.get_subdivision(conn, account_id, category)
        if sub is None:
            return None
        attached = subdivision.attached_whales(conn, account_id, category)
        orders = subdivision.live_orders(conn, account_id, category)
        n_live_trades = subdivision.live_order_count(conn, account_id, category)   # uncapped -> honest 'N of M' when truncated
        # WHALE ATTRIBUTION (2026-09-01): the held table split PER (ticker, whale) so a stacked ticker shows which
        # whale each copy is from; and the LIVE-COPY record per whale (distinct from paper/prospect -- real money).
        positions_by_whale = subdivision.live_positions_by_whale(conn, account_id, category)
        floor = search.DEFAULT_MIN_RESOLVED_FLOOR
        copies_by_whale = subdivision.live_copies_by_whale(conn, account_id, category, thin_floor=floor)
        # L3 DRIVER LIVENESS for THIS sub (read-only): the one matching row from the expected-set liveness read.
        liveness_present = heartbeat.table_present(conn)
        _live = [r for r in heartbeat.read_liveness(conn) if r.account_id == account_id and r.category == category]
    return {"sub": sub, "attached": attached, "orders": orders, "n_live_trades": n_live_trades,
            "positions": positions_by_whale, "copies_by_whale": copies_by_whale, "thin_floor": floor,
            "sizing_summary": subdivision.sizing_summary(sub),
            "liveness_present": liveness_present, "liveness": _live[0] if _live else None}


@app.get("/live", response_class=HTMLResponse)
async def live_list_page(request: Request):
    """The LIVE sub-division tiles (top of the hierarchy). Honest-empty until a sub-division is created AND
    migration 010 is live. READ-ONLY -- no order path."""
    data = await asyncio.to_thread(_load_live_list)
    return templates.TemplateResponse(request, "pm_live_list.html", {"request": request, **data})


@app.get("/live/{account_id}/{category}", response_class=HTMLResponse)
async def live_subdivision_page(request: Request, account_id: str, category: str):
    """One Account-Category sub-division: its config + what it currently holds + its live-trade journal (real
    orders, newest first). Honest-empty ('no live trades yet') only when it truly has not traded -- the engine
    exists and its fills land here. A sub-division that doesn't exist -> 404. READ-ONLY (no order path)."""
    account_id = (account_id or "").strip()
    category = (category or "").strip().lower()
    data = await asyncio.to_thread(_load_live_subdivision, account_id, category)
    if data is None:
        return templates.TemplateResponse(
            request, "pm_live_404.html",
            {"request": request, "account_id": account_id, "category": category}, status_code=404)
    return templates.TemplateResponse(request, "pm_live_subdivision.html", {"request": request, **data})


# ── Watchlist whale detail (Stage 2, phase 2) -- a PINNED whale's PAPER trades + paper stats ──────
# BASIS: paper (pm_paper_trade / pm_paper_category_stats), read via positions.paper_trades / paper_stats_row.
# DELIBERATELY a separate route + template from the completed /whale/{wallet}/{category} detail: a pinned whale's
# detail shows OUR paper trades, a prospect's shows its COMPLETED trades -- wiring a pinned row to the completed
# detail would be the exact basis violation this stage guards.

def _load_watchlist_whale(wallet: str, category: str, now_ts: int) -> dict:
    """A pinned whale's PAPER detail read: whale header + the paper-basis stats + ALL its paper trades, on ONE
    short-lived read connection, OFF the loop. Reads the PAPER lane ONLY -- never pm_closed_position."""
    with connect() as conn:
        whale = positions.whale_row(conn, wallet)
        pstats = positions.paper_stats_row(conn, wallet, category)
        trades = positions.paper_trades(conn, wallet, category)
        refresh = stats.refresh_band_state(stats.max_refresh_ts(conn), now_ts)
    return {"whale": whale, "pstats": pstats, "trades": trades, "refresh": refresh}


@app.get("/watchlist/{wallet}/{category}", response_class=HTMLResponse)
async def watchlist_whale(request: Request, wallet: str, category: str):
    """A pinned (Watchlist) whale's PAPER detail: all its paper trades + the paper-basis stats. Distinct from the
    completed `/whale/{wallet}/{category}` detail (which the Prospects section links to). Honest-empty for an
    unknown wallet / a pair with no paper trades yet -- never a fabrication."""
    wallet = (wallet or "").lower()
    data = await asyncio.to_thread(_load_watchlist_whale, wallet, category, int(time.time()))
    return templates.TemplateResponse(
        request, "pm_watchlist_whale.html", {"request": request, "wallet": wallet, "category": category, **data})
