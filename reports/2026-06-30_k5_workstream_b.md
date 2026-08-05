# K5 Workstream B — Dashboard Defect Fixes + Discovery Button
**Branch:** `kalshi-k5-dashboard-2026-06-30`
**Date:** 2026-06-30
**Commits:** 82be137 / dd48ff9 / 92f676b (3 commits, web-only, no engine restart)

---

## Summary

Four items built and committed. All are web-only changes (routes.py, data.py, pm_dashboard_body.html) — no engine restart required; a web process reload is sufficient to deploy.

---

## Defect A — Demoted Kalshi whales disappear (FIXED)

**Commit:** 82be137  
**File:** `trading_corp/web/routes.py` (routes 2126-2165 in new state)

**Root cause:** `kalshi_whales_demote` removed a handle from `selected_whales`/`pinned_whales` but never added it back to `watch_only_whales`. Auto-selected finalists were seeded directly into `selected_whales` (never in `watch_only_whales`), so on demote they vanished from both panels.

**Fix:** After removing from selected/pinned, load `watch_only_whales` and append a stub `{"handle", "tier":None, "source_x_handle":None, "notes":"demoted_via_dashboard", "included_iso"}` if absent. Existing entries (handles previously promoted from the watch list) are untouched.

The existing comment saying "We intentionally do NOT mutate watch_only_whales here" was describing Polymarket behavior (where re-display is implicit because every whale was always in `watch_only_whales`). For Kalshi, auto-selected finalists need an explicit re-add.

---

## Defect B — Kalshi watchlist not sortable (FIXED)

**Commit:** dd48ff9  
**Files:** `trading_corp/web/data.py` (after line 4869), `trading_corp/web/routes.py` (routes 334-426), `trading_corp/web/templates/partials/pm_dashboard_body.html` (kalshi watch list thead)

**Changes:**

1. `data.py`: Added `_KALSHI_WATCH_SORT_KEYS` dict (mirrors `_PM_WATCH_SORT_KEYS`) with keys: handle, tier, resolved, wr, pnl (default), pnl_contract, open, top_category, last_refresh. Added `sort_key`/`sort_desc` params to `_query_kalshi_watch_only_rows`; replaced the hardcoded `out.sort(key=lambda w: (w.tier or 99, -w.total_pnl))` with whitelist-gated parametrized sort (None values sink to the bottom, same pattern as polymarket). Added `kalshi_watch_sort`/`kalshi_watch_desc` fields to `PMDashboardView`. Threaded both through `build_prediction_market_view`.

2. `routes.py`: Added `kalshi_watch_sort: str | None` and `kalshi_watch_desc: int = 1` query params to all 4 route handlers (`prediction_markets_all`, `prediction_markets_one`, `prediction_markets_partial_all`, `prediction_markets_partial_one`) and both internal `_render_pm_*` helpers.

3. `pm_dashboard_body.html`: Added `kalshi_watch_sort_link` macro (mirrors `pm_watch_sort_link`) before the kalshi watch list section. Applied macro to all 9 sortable `<th>` headers (handle, tier, resolved, wr, pnl (default_key=True), pnl_contract, open, top_category, last_refresh).

---

## Defect C — Promote/demote buttons fail on bad handles (FIXED)

**Commit:** dd48ff9 (same commit as B)  
**File:** `trading_corp/web/templates/partials/pm_dashboard_body.html`

**Root cause:** Kalshi keys buttons on the human `handle` (e.g. "john.doe"). The `hx-target="#whale-row-kalshi-john.doe"` is an invalid CSS selector (`.` is a class selector prefix). The `hx-post="/api/kalshi/watchlist/promote/john.doe"` URL-splits on `.` differently in some environments.

**Fixes applied to BOTH promote (watch list) and demote (selected whales) buttons:**

1. Added `{% if w.handle %}` guard; renders `—` with title tooltip when handle is empty.
2. `{{ w.handle | urlencode }}` in `hx-post` path — handles with `.`, `/`, `#`, `%` etc. are percent-encoded; FastAPI URL-decodes the path parameter transparently.
3. Demote button: switched `hx-target` from `#whale-row-kalshi-{{ w.actor_id or w.handle }}` to `[data-whale-id='{{ w.handle|e }}']` (attribute selector, no CSS special-char issues).
4. Added `data-whale-id="{{ w.handle|e }}"` attribute to the Selected Whales `<tr>` (conditional on `w.venue == "kalshi" and w.handle`).
5. Added `data-whale-id="{{ w.handle|e }}"` attribute to the Watch List `<tr>` (conditional on `w.handle`).

---

## Addition D — "Run Discovery" button (BUILT)

**Commit:** 92f676b  
**Files:** `trading_corp/web/routes.py` (2 new routes), `trading_corp/web/templates/partials/pm_dashboard_body.html` (header right slot)

**Routes added:**

- `POST /api/kalshi/watchlist/discover` — idempotency check (returns in-flight fragment if already running), loads secrets, marks `discovery_run_status = {state:"running", started_iso}`, fires `asyncio.create_task(_run_discovery())`, returns "running" fragment immediately. Background task calls `deep_seed_watchlist(apify_token, db_url)` (the importable async function in `trading_corp/scripts/seed_kalshi_watchlist_deep.py`), computes cost telemetry from the returned summary, and writes `discovery_last_run + discovery_run_status` to agent_state on completion or error.

- `GET /api/kalshi/watchlist/discover/status` — loads `discovery_run_status` and `discovery_last_run` from agent_state; returns running fragment (with `hx-trigger="every 3s"` self-poll) or idle fragment (button enabled + last-run telemetry).

**Template:** The Kalshi watch list panel header now has a right-side `<div id="kalshi-discovery-control" hx-get=".../status" hx-trigger="load">` that bootstraps the initial state from the status endpoint on page load.

**Cost telemetry stored:**
- `leaderboard_events` = sum of actual row counts from `summary["leaderboards_pulled"]` (accurate)
- `profile_events` = `newly_probed * 30` (estimate: 20 profile + 10 closed-position rows/probe)
- `est_cost_usd` = profile_events × $0.0015 + leaderboard_events × $0.001
- `whales_found` = `summary["found_count"]`

**hx-confirm dialog:** "Run whale discovery? Triggers a paid Apify scrape (~$3-5 warm, ~$5-15 cold)."

---

## COST FLAG — Daily stats refresh is the dominant sink (DEFERRED)

The daily `trading-corp-watchlist-stats.timer` (12:00 UTC, `refresh_kalshi_watchlist_stats.py`) runs an unconditional full-profile + full-closed-history pull for ALL watch_only handles (~$196/period, ~135k events). This button covers only the Sunday seed (~$3-5/run). Per operator decision, the daily stats refresh is left as-is for now.

**Action items (operator):**
1. `systemctl disable --now trading-corp-watchlist-deep.timer` (root, once this button is in service)
2. Address `trading-corp-watchlist-stats.timer` before raising the Apify spend cap — it is the dominant cost, not the Sunday seed

---

## Test Results

- **117 dashboard/kalshi tests passed, 0 failures** on targeted suite (test_prediction_markets_dashboard, test_promote_demote_fixes, test_polymarket_watch_only_sort, test_polymarket_analyze_route, test_kalshi_copy_trader, test_kalshi_resolver)
- **Full suite:** 30 pre-existing failures (bitunix config tests, robinhood multi-leg, iron condor, webhooks timing) — all in files not touched by this build; zero new regressions introduced
- Pre-existing failures confirmed at branch base 9bfd7ff: `test_boot_smoke::test_two_state_sfp_comes_up_trading_and_replay_disabled` (bitunix_futures now `mode: trading` per Phase 2c), `test_bitunix_observer_execution_mode::test_prod_strategies_yaml_ships_paper_default` (execution_mode now `live`), and others in unrelated modules

---

## Files Changed

| File | Change |
|------|--------|
| `trading_corp/web/routes.py` | Defect A: re-add to watch_only on demote; Defect B: kalshi sort params on 4 route handlers; Addition D: 2 new discover routes + `_discovery_control_html` helper |
| `trading_corp/web/data.py` | Defect B: `_KALSHI_WATCH_SORT_KEYS`, sort params on `_query_kalshi_watch_only_rows`, `kalshi_watch_sort`/`kalshi_watch_desc` on `PMDashboardView` + `build_prediction_market_view` |
| `trading_corp/web/templates/partials/pm_dashboard_body.html` | Defect B: `kalshi_watch_sort_link` macro + sortable `<th>` headers; Defect C: `{% if w.handle %}` guard + `urlencode` + attribute selector on promote/demote + `data-whale-id` on rows; Addition D: discovery control slot in header |
