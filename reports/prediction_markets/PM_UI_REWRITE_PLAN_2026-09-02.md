# PM UI Rewrite — Implementation Plan (2026-09-02)

Branch/worktree: `pm-ui-rewrite-2026-09-02` @ base `e95e638` in `C:/Users/AA Incorporado/cc-pm-ui-rewrite-wt`.

## Isolation / base rationale
- pm_web code is NOT on `main-wip`/`prod-live`; it lives only on the `prediction-markets*` lineage
  (deployed to the box as file grafts — "box is truth"). Base MUST be a lineage commit.
- `e95e638` is the TIP of the display/pm_web lineage (multiaccount M3/M4 -> whale attribution ->
  loss-omission -> prospects-analyze). It has the most complete data-access spine and is on the DISPLAY
  side — the per-account TRADING agent forks separately off `f1e28cc` into `pm-per-account-trading-2026-09-02`.
  Basing on the fixed commit `e95e638` gives max data plumbing with ZERO entanglement with that agent.
- Confine ALL changes to `trading_corp/prediction_markets/web/` (+ pm_web-owned new modules and a
  pm_web-owned cache store). Touch NO engine/driver file, NO engine-owned table/schema, NO broker/creds.

## Hard constraints carried
- pm_web is standalone by construction: imports ONLY fastapi + the PM package + the data layer
  (`trading_corp.data.*`, as `market_describe` already does). NO engine/main/agents/brokers import; NO creds;
  can never place an order. (Guarded by `test_pm_web_imports_no_engine`.) Any Kalshi/feed fetch = a tiny
  standalone HTTP client, never `KalshiBroker`.
- FastAPI + Jinja2, server-rendered, progressive JS. No SPA/React/build step/client routing. Hash routes
  from the prototype become the existing server URLs.
- Vendored assets only (no CDN/external fonts/remote images). Dark theme. Desktop-first ~1600px; hold at 1280.
- ~60s poll refresh; no websockets.

## Design of record
`Downloads/pm-design-bundle/prediction-market/project/copy-desk.html` (Kalshi2 bundle, the newest and the
only one using the brief's `prediction-market/...` paths). MOCK object = the target server-context shape.

Screens (prototype hash -> server URL):
- Accounts `#/accounts` -> `/`  (pm_accounts.html)
- Account  `#/account/{id}` -> `/account/{account_id}`  (pm_account.html)
- Division `#/division/jack-mlb` -> `/live/{account_id}/mlb`  (pm_live_subdivision.html)  <-- the game-card page
- Farm     `#/farm` -> `/farm`  (existing pm_farm_league.html, kept as-is; only reachable from the new shell)

## Mandated deviations from the prototype (brief OVERRIDES the mock)
1. Accounts caveat: REMOVE "the other is funding-only" — both accounts are TRADING.
2. Do NOT render `settled.note` ("settled on the 10th run · game live") — use the note=null path. Report what
   joining Kalshi settlement-ts to the feed's game state would require.
3. Card/drawer current values = contracts × BID (held-leg bid), labelled "bid" in the caveat.
4. N accounts: kill the prototype's `if(a.id!=="jack") return "not part of prototype"` — Karen renders through
   the SAME template/code path. Nothing hardcodes "jack"; account list + scoping come from authz/subdivision.
5. Farm League: keep existing production templates; do not redesign.

## Data availability (verified via data-access map)
Already DB-derived (no feed/mark needed):
- Accounts / account P&L / owner_identity / shards / arm state:
  `subdivision.accounts_overview / account_pnl / active_accounts`, `shard_snapshot.read_latest/table_present/
  shard_direction`, `arm.read_status()`, scoped by `web.authz` (Authelia Remote-User + PM_ADMIN_IDENTITIES).
- Live sub-division journal + positions + per-whale copies:
  `subdivision.live_orders` (full journal: ticker, order_side, outcome_leg, is_exit, submitted_count/price,
  fill_count/price, fee, submitted_ts/response_ts, close_source ∈ {settlement, settlement_void, opposed, NULL=
  whale-exit}, realized_pnl, won, settled_ts, wallet, user_name, market_type),
  `live_positions_by_whale`, `live_copies_by_whale`, `attached_whales`, `get_subdivision`, `sizing_summary`.

Reusable (data layer, pm_web-importable):
- Ticker parser `trading_corp/data/mlb_poly_kalshi_match.py`: `parse_kalshi_mlb_ticker`
  (date YYMMMDD, HHMM time, yes_code/other_code, yes_name/other_name, game_no for DOUBLEHEADERS),
  `parse_kalshi_total_ticker`, `parse_kalshi_spread_ticker`; `market_describe.describe_market`.

Net-new (must build, pm_web-owned):
- Scope D sports-feed adapter (NO existing StatsAPI/ESPN code anywhere).
- Scope E mark poller + a pm_web-owned cache (NO mark table, NO cross-book open-ticker fn).
- Game-card assembly that groups the journal by GAME (via the ticker parser) and joins feed + marks.

## Scope-E probe result
Local read-only probe (public internet, not box access):
- Kalshi `GET https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open` ->
  HTTP 200, UNAUTHENTICATED, body carries `yes_bid_dollars/no_bid_dollars/no_ask_dollars/last_price_dollars/
  liquidity_dollars` per market. => Kalshi market data is PUBLIC => Scope E is BUILD (no creds in pm_web).
- StatsAPI schedule 200; ESPN scoreboard 200 — both keyless.
Still to confirm FROM THE BOX (brief's letter + rate-limit observation) via an AUTHORIZED .ps1 runner:
box egress reachability + observed Kalshi rate-limit headers. This is the one box-channel checkpoint.

## Architecture (all pm_web-owned, standalone-safe)
- `web/feed_mlb.py` — StatsAPI primary, ESPN fallback. Join Kalshi ticker -> game by HHMM start + team codes.
  Handle: team-code mismatches (Kalshi AZ vs feed ARI; SEABOS split), ET/DST, doubleheaders
  (StatsAPI doubleHeader/gameNumber vs game_no), postponed/suspended. Every failure -> "unavailable",
  never a wrong game.
- `web/marks.py` — standalone GET of the PUBLIC Kalshi `/markets?series_ticker=...` -> {ticker: {yes_bid,
  no_bid, as_of}}. One paginated call per series covers the whole slate.
- `web/ui_cache.py` — pm_web-owned store for feed + marks, each value with its own as_of. Store choice: a
  SEPARATE pm_web-owned SQLite file (e.g. `data/pm_web_ui_cache.db`) OR in-process cache — NOT the engine DB,
  NO shared-schema migration. (Finalize after checking uvicorn worker count in scripts/pm_web.py; lean
  separate-SQLite for multi-worker/restart safety.)
- `web/live_view.py` — assemble games[] context: group journal/positions by game, derive terminal state
  (open/settled/exit/opposed) from close_source/realized_pnl/won, compute retention (drop 24h after game END
  or last settlement), attach feed + marks with ages.
- Background asyncio task started on app startup: refresh feed + marks every 60s into ui_cache. Renders read
  the cache only (fast, no per-viewer fan-out). Task RUNS only on a pm_web (re)start (Jack's deploy) — building
  it is in scope; running it in prod is the deploy.
- Templates: rewrite pm_accounts.html, pm_account.html, pm_live_subdivision.html to the design; restyle
  pm_shell nav to the 3-peer shell (Accounts / Farm League / Live Sub-divisions). Keep farm templates as-is.
- Static: rewrite pm.css to the design tokens/components (card/diamond/bet/drawer). New pm_live.js for the
  age-ticker + 60s client poll + drawer/row-expand + Active/Complete toggle — progressive enhancement; the
  server-rendered base works JS-off.

## Honesty rules -> mechanics
- Per-value age chips banded fresh/stale (180s default / 120s feed / never-stale final); caveat beside the number.
- Feed `live===null` -> render nothing feed-derived, show "unavailable" (never blank, never stale-as-current).
- No mark -> defined "no mark" state on card + drawer (never $NaN/$0/cost-as-value).
- Cost basis vs current value labelled distinctly everywhere; small-sample caveats kept.
- Unknown != zero; a whale with no display name shows its wallet.
- Retention keys on game END / last settlement; "Complete" = every position settled.

## Build order
1. (checkpoint) Box probe runner for Scope E confirmation + rate limits — present + await authorization.
2. Feed adapter (feed_mlb.py) + tests (fixtures for the named failure modes).
3. ui_cache + marks (marks.py) + background poller.
4. live_view assembly + pm_live_subdivision.html + pm_live.js + pm.css (game card).
5. Rewrite pm_accounts.html + pm_account.html + shell nav.
6. Verify each page state (live/final-unsettled/partly-settled/complete/feed-unavailable/no-mark) with
   rendered HTML/screenshots against a read-only view of real data on a non-prod port.
7. Final report.

## Verification plan
- Run pm_web in the worktree on a non-prod port against a read-only copy of real data; never point at live order paths.
- Evidence (command/output) per claim; rendered HTML/screenshots per page state.
- Report observed/documented Kalshi quote rate limits (no guessing).

## Explicit non-goals / stop-conditions
- No deploy/restart/reload of any service (Board decision).
- No engine code / engine table / schema / broker / creds changes. If a task needs one: stop that item,
  document exactly what is needed, move on.
- The "settled on the 10th run" note stays UNBUILT; report the settlement-ts<->game-state join requirement.
