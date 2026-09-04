# PM UI Rewrite — Final Report (2026-09-02)

Author: code agent (pm-ui-rewrite workstream). Scope: rewrite the Prediction Markets pm_web UI to the
copy-desk design, N-account from the start, pm_web-only, no deploy/restart, no engine/schema/broker change.

--------------------------------------------------------------------------------------------------------------
## 1. Branch / worktree and commits

- Worktree: `C:/Users/AA Incorporado/cc-pm-ui-rewrite-wt`
- Branch:   `pm-ui-rewrite-2026-09-02`  (base `e95e638` = tip of the pm_web/display lineage — see §5 for why)
- Not pushed to main; not rebased on / cherry-picked from any in-flight branch. Isolated from the per-account
  *trading* agent (which forks separately off `f1e28cc` into `pm-per-account-trading-2026-09-02`).

Commits (oldest first):
- `40ccff2` implementation plan (base + scope-E probe result)
- `5935134` Scope D: MLB sports-feed adapter (StatsAPI primary, ESPN fallback) + 14 tests
- `c1fbc2f` Scope E: Kalshi mark reader + in-process cache + 60s poller + 12 tests
- `6212ca6` Scope A/F: live sub-division assembly (journal x feed x marks -> game cards) + 7 tests
- `cc4b513` Scope A: live game-card page + design shell + poller wiring + JS + CSS
- `57688e6` empty-card suppression + verification harness + screenshots
- `8177f48` Scope B: accounts overview + account page rewrite

New pm_web modules: feed_mlb.py, marks.py, ui_cache.py, poller.py, live_view.py. New static: pm_desk.css,
pm_live.js. Rewritten templates: pm_shell, pm_accounts, pm_account, pm_live_subdivision, + new
partials/pm_trade_drawer.html. New tests: test_feed_mlb (14), test_marks (6), test_ui_poller (6),
test_live_view (9). Pre-existing pm_web page tests reconciled to the redesign (see §3): test_web_r6, test_live_r3,
test_stage2_nav, test_stage2_phase3, test_accounts_m2.

Test state (full `tests/prediction_markets/` suite): every pm_web / UI test PASSES. The only remaining failures
are the PRE-EXISTING env-gap baseline, unchanged by this work and failing identically on the base commit: the
ENGINE-driver tests (test_live_driver_r7c, test_kill_switch_r7d, test_shard_gate_r2, test_liquidity_floor_r7f,
test_sizing_contracts_r8) fail/ERROR on `No module named 'pykalshi'` (the broker lib is not installed in the
`.venv-webtest`), and test_search_r1::test_schema_head_is_15 is stale (base is schema 17). None of these touch
pm_web or any file I changed.

--------------------------------------------------------------------------------------------------------------
## 2. Probe results

### Kalshi quote auth requirement — PUBLIC (no credentials)
- LOCAL probe: `GET https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open`
  returned HTTP 200 UNAUTHENTICATED with `yes_bid_dollars / no_bid_dollars / no_ask_dollars /
  last_price_dollars / liquidity_dollars` per market.
- BOX probe (authorized runner `pm_ui_probe_kalshi.ps1`, board-approved, read-only): `HTTP/2 200` from the
  box, unauthenticated. So pm_web reads marks directly with the stdlib — NO broker import, NO signing, NO
  credentials (the "pm_web can never place an order / holds no creds" guarantee is preserved). Live-fetched
  335 open MLB markets during verification.
- NB: the engine's pykalshi client always RSA-PSS-signs every request and raises without creds; that is a
  library property, not an endpoint requirement. The endpoint itself is public, which is what matters here.

### Kalshi rate limits — observed + documented (not guessed)
- OBSERVED: the box probe captured NO `X-RateLimit-*` / `Retry-After` headers on the 200 response.
- DOCUMENTED (docs.kalshi.com/getting_started/rate_limits): token-bucket, most requests cost 10 tokens; basic
  -tier read bucket holds ~2s of burst budget; 429s carry NO Retry-After / X-RateLimit-* headers (matches the
  observation). Exact per-second budget is only available via the AUTHENTICATED `GET /account/limits`, which
  pm_web cannot call (no creds) — so I do not state a specific number for our unauthenticated reads.
- OUR VOLUME: the poller makes 3 series requests + up to ~15 trimmed last-play requests per 60s cycle
  (~0.3 req/s), independent of viewer count (single background poller, cached). Negligible under any tier.

### StatsAPI / ESPN reachability + join checks
- StatsAPI `schedule?sportId=1&hydrate=linescore,team` HTTP 200 (local + box). ESPN scoreboard HTTP 200 with
  curl's default UA; **403 with a browser (Chrome) User-Agent** — so the ESPN fallback sends a curl-style UA
  and degrades gracefully on any non-200.
- JOIN verified end-to-end: the Kalshi ticker's game key equals the feed key. Real datum: SD@CIN ticker
  `KXMLBGAME-26SEP02**1240**SDCIN` vs StatsAPI `gameDate 16:40Z` => 12:40 ET => the Kalshi HHMM is EASTERN.
  Join key = (ET calendar date, doubleheader#, unordered canonical team-name set); ET conversion is a
  deterministic US-Eastern DST rule (no tzdata dep), and it correctly rolls a 10:10pm ET night game
  (02:10Z next day) back to the ET date. Team-code variants (AZ/ARI, CWS/CHW, ATH/OAK) all canonicalize via
  the existing `MLB_TEAMS` map; SEABOS-style blobs split via the same map. 14 feed tests cover these.

--------------------------------------------------------------------------------------------------------------
## 3. What is built — evidence per page/state

All rendered offline via `reports/prediction_markets/ui_verify_harness.py` (TestClient, temp DB with a
representative journal, REAL Kalshi marks fetched live; synthetic feed states because today's games are
pre-game at the run hour). 14/14 harness assertions pass; standalone HTML + PNG in `ui_verify/`.

- **Live sub-division `/live/{account}/mlb` (Scope A)** — `01_active_live_mixed.png`:
  diamond + box score, bases lit (1st+3rd), TOP 6, B/S/O count pips to the RIGHT of the diamond, per-inning
  linescore, last-play overlay; three FIXED bet slots (ML/TOT/SPR) seated over home plate; ML slot valued at
  the REAL bid $2.90 (5 x live yes_bid 0.58); TOT/SPR "no mark" (those tickers not in the live book — the
  honest degrade even while other tickers are priced). Mixed card (MIL@CHC): green top-border, ML won $5.00
  (payout), SPR lost $0.00, TOT live "no mark", "2 SETTLED · 1 LIVE". Summary strip (games held, unsettled at
  cost, unsettled current value, realized today), Active/Complete toggle (server-rendered, works JS-off),
  legend, trade drawer.
- **Complete tab** — `02_complete_tab.png`: the all-settled game shows under Complete; a card drops 24h after
  the game's last settlement (retention verified in unit tests, both kept-<24h and dropped->=24h).
- **Feed unavailable** — `03_feed_unavailable.png`: dead diamond "FEED UNAVAILABLE", inning "NO FEED", count
  "unavailable" — nothing feed-derived is rendered; never a fabricated 0-0 or a stale score.
- **No mark** — `04_no_mark.png`: every open slot shows "no mark" (never $NaN/$0.00/cost-as-value); the strip
  "current value" shows "no mark".
- **Accounts overview `/` (Scope B)** — `05_accounts.png`: N-account panels (loop; nothing hardcodes jack),
  TRADING tag on each, the "funding-only" line REMOVED, GLOBAL DISARMED badge (read-only) + admin cross-console
  link, figs Realized P&L / Open-at-cost / Open current value. The open value shows **"$2.90 (partial: 1 of 4
  priced)"** — coverage honesty: only the priced positions are summed, the rest disclosed, never a $0 that
  means "unpriced".
- **Account page `/account/{id}` (Scope B)** — `06_account.png`: aggregate performance figs (Realized /
  Record / Open-at-cost / Open current value), per-sub-division rows (markets · whales · orders · realized ·
  open cost/value) with "Open division ->" link, cash-by-shard.
- **Farm League (Scope C)** — unchanged; reachable from the new nav ("Farm League" peer); renders under the
  new shell (proven by the r6 farm test). Not redesigned.
- **Trade drawer (Scope F)** — native `<details>` (works JS-off). Four terminal states: OPEN (row value =
  contracts x bid), SETTLED (realized), EXIT (realized), OPPOSED ("— not booked", never a guessed number). Whale
  attribution per trade (user_name or wallet; unknown != a name). Detail row (JS-toggled) adds order id,
  ticker, slippage, cost basis — honesty-critical values live in the always-visible main row, so nothing
  load-bearing is hidden JS-off.

Honesty rules honored (treated as spec): per-value age chips banded fresh/stale (180s default / 120s feed /
never-stale final); cost basis and current value labelled distinctly everywhere; small-sample caveats travel
with the P&L; unknown-whale -> wallet; retention keys on game-end/last-settlement.

Progressive enhancement: server-rendered base works with JS off (Active/Complete + Poll now are links, drawer
is native <details>, ages stamped server-side). `pm_live.js` adds a per-second age ticker, a 60s
fetch-and-swap refresh (no white flash), row expansion, value flash.

Standalone-import guard holds: none of the new modules pull trading_corp.brokers/main/web/agents.

TEST RECONCILIATION (the redesign replaced the old /live table + shell, so page-render tests written for the old
markup had to be updated — done honestly, preserving every safety/honesty property, and restoring genuine
features the first cut of the redesign had dropped):
 - RESTORED to the page: the /live sizing-behaviour + config line (the '$0.01 stake = 1 contract, not a cent'
   honesty + market_types/sizing_mode visibility); the account display-only limitation copy; the balance
   cadence hint. The account aggregate figs are now gated on pm_traded (a display-only account shows the
   limitation, not a zeroed P&L frame implying it will fill).
 - UPDATED assertions where wording/markup legitimately changed to the copy-desk design: the read-only guard
   forbids an arm/disarm ACTION endpoint ('/arm') instead of the substring 'disarm' (which now collides with the
   mandated read-only 'DISARMED' status badge — the <form/hx-post/submit/order tokens still guarantee no
   control, and pm_web has no arm route at all); the nav wraps its label in <b> ('>Accounts</b>'); the global
   arm badge reads 'GLOBAL DISARMED'; labels 'at cost' / 'Cash by shard'; settlement-vs-exit is asserted on the
   status CELL markup ('>SETTLED</td>' / '>EXIT</td>') because the drawer footer legitimately EXPLAINS all four
   states; the balance age is shown as a chip. No safety or honesty check was weakened.

--------------------------------------------------------------------------------------------------------------
## 4. Stopped-on items — what Jack must decide / the engine agent must provide

1. **"Settled during a live game" note — deliberately NOT built (per brief).** Settled positions render with
   note=null. To build the prototype's "settled on the 10th run · game live" note honestly, the render would
   need to compare the Kalshi SETTLEMENT timestamp (`pm_subdivision_order.settled_ts`, already in the DB) to
   the feed's GAME STATE at that instant (in_progress vs final). pm_web has the settlement ts and the CURRENT
   feed state, but NOT the game state AS OF the settlement moment (the poller caches current only, no history).
   Options if wanted later: (a) at settlement time compare settled_ts to the game's scheduled end / final ts
   and store a boolean "settled_while_live" — but the honest game-end ts is itself only knowable from the feed
   (the engine does not record it); (b) keep a small feed-state history in pm_web keyed by game and look back.
   Neither is required by the current scope; flagged for a decision, not built.

2. **Live/final feed states shown with synthetic box scores in the evidence** — because at the harness run
   hour every real MLB game for the date was pre-game (Preview). The real feed PIPELINE is proven end-to-end
   (real slate fetch, real marks, real join key equality); only the specific in_progress/final linescores in
   the screenshots are synthetic. Re-running the harness (or the live page) during a live slate will show real
   box scores with zero code change. No decision needed — just noting the evidence caveat.

3. **Shard table lacks shard NAMES and the "funds baseball" flag** that the prototype shows. The pm_web data
   source (`shard_snapshot.read_latest.by_shard`) is `{exchange_index: dollars}` + total + has_breakdown +
   age; it carries no per-shard name or funding-role. I render shard indices + balances + total + age +
   the honest funding caveat, without fabricating names. If the named/funding view is wanted, the engine (or a
   config) would need to expose per-shard metadata (name, funds-this-division) — an engine/data change, out of
   this workstream's scope.

4. **ESPN fallback is best-effort.** It 403s a browser UA and is IP/rate-sensitive; StatsAPI is the reliable
   primary. If both feeds are down, cards degrade to "feed unavailable" (verified). No action needed.

Nothing in this workstream required an engine code change, an engine-owned table, a schema migration, or any
broker/credential — so there is no blocked item waiting on the engine agent.

--------------------------------------------------------------------------------------------------------------
## 5. What a deploy would require (DESCRIBED — not done)

A deploy is a Board decision; I did not deploy, restart, or reload anything.

**Service:** `prediction-markets-web` (pm_web) ONLY. The engine is NOT touched. No schema migration (the mark
cache is in-process, not a table). One pm_web restart is required — to (a) load the new templates / static /
app code and (b) start the 60s feed/marks poller (an `@app.on_event("startup")` task). Until that restart the
poller does not run; renders read an empty cache and degrade to "warming up" / "no mark" / feed-unavailable
(all honest states).

**Files to graft onto the box** (all under `trading_corp/prediction_markets/web/` + tests, box-is-truth):
- NEW: `feed_mlb.py`, `marks.py`, `ui_cache.py`, `poller.py`, `live_view.py`, `static/pm_desk.css`,
  `static/pm_live.js`, `templates/partials/pm_trade_drawer.html`.
- REWRITTEN templates: `templates/pm_shell.html`, `pm_accounts.html`, `pm_account.html`, `pm_live_subdivision.html`.
- MODIFIED `app.py`: new imports (live_view/poller/ui_cache), 7 Jinja filters (money/signed/pnlcls/agefmt/
  ettime/etdate/etdt), the startup/shutdown poller hooks, and the rebuilt `/live`, `/`, `/account` loaders.
- `authz.py`, `db.py`, `subdivision.py` etc. are UNCHANGED (read-only consumers).

**★ app.py graft hazard (standing, from SW7):** HEAD/branch `web/app.py` may carry plumbing not on the box
(the box app.py is the deployed graft, not any branch tip). Per the box-is-truth discipline, the deployer must
RECONCILE app.py against the box hunk-by-hunk (graft the UI-rewrite additions onto the box's current app.py),
NOT wholesale-copy — the same discipline used for every prior pm_web deploy. Everything else (new files,
templates, static) is additive/wholesale-safe.

**Egress:** the box must reach `statsapi.mlb.com`, `site.api.espn.com`, `api.elections.kalshi.com` (all
verified reachable from the box, all public/keyless). No new secret, no Key Vault change.

**Recommended pre-restart gate:** run the pm_web test subset on the box venv (carry `-p no:pytest_ethereum`)
and a boot smoke of `GET /healthz`, `GET /`, `GET /live/<acct>/mlb` before advancing.

==============================================================================================================
# FIX PASS — 2026-09-02 (board review corrections 1-4, refinements 5-7, reconciliation)

Same branch `pm-ui-rewrite-2026-09-02`. Fix-pass commits: merge `e764eb5` (item 3) + `c5abd3e` (items 1/2/4-7).
NOT deployed, NOT restarted. Both accounts remain armed and trading unattended; nothing on the box was touched.

## Item 1 — display-only mode REMOVED entirely
- `pm_accounts.html`: dropped the `{% if a.pm_traded %} … {% else %}…not traded by Prediction Markets / Display-
  only…{% endif %}` gate — every account panel now renders the same figs (Realized / Open-at-cost / Open value).
- `pm_account.html`: dropped the `{% if not account.pm_traded %}…display-only…` gate on Aggregate performance and
  the "no Prediction Markets sub-divisions / display-only" note; the empty sub-divisions case is now a neutral
  "No sub-divisions on this account yet."
- Tests rewritten to expect NO display-only state: `test_accounts_m2::test_account_page_karen_states_display_only`
  -> `…_same_path_no_display_only` (asserts figs render + `display-only`/`not traded` ABSENT); the accounts-overview
  test drops the "not traded" assertion and asserts the absence of display-only wording.
- Evidence: `grep -c display-only reports/prediction_markets/ui_verify/06_account.html` -> `0`. Commit `c5abd3e`.

## Item 2 — arm badge reads the PERSISTED rows; false-disarm fixed; ts as age chip
- WHAT IT READ BEFORE: `arm.read_status()` -> `_row_armed(_load_row(GLOBAL_KEY))`, and `_load_row` COLLAPSES an
  INDETERMINATE mode=ro read ('error': locked / missing table / io error) to `None` -> DISARMED. That is exactly
  the false-disarm the engine team flagged: a status read near a restart shows a false DISARMED.
- FIX: new `arm.read_display()` (trading_corp/prediction_markets/arm.py) DISTINGUISHES armed / disarmed / absent /
  **unavailable**; an INDETERMINATE read is 'unavailable' (shown as a distinct amber "STATE UNAVAILABLE" badge),
  NEVER 'disarmed'. The persisted row + its `ts` are the truth; the row `ts` is rendered as the badge age chip
  (like every other feed value). Shared `partials/pm_arm_badge.html` macro renders a plain `<span>`.
- THE EXACT QUERY the badge now runs (per scope), on a `mode=ro` sqlite open of the legacy DB
  (`$PM_LEGACY_DB_PATH` else `data/trading_corp.db`):
      SELECT value_json FROM agent_state WHERE agent = 'pm_live' AND key = 'arm:global'
  and, for the live page's EFFECTIVE (sub) state:
      SELECT value_json FROM agent_state WHERE agent = 'pm_live' AND key = 'arm:kalshi_<acct>:<category>'
  A clean read with no row -> 'absent' (disarmed-by-absence); a read error -> 'unavailable'; `armed` is
  `value_json.armed is True` (strict); the age is `now - value_json.ts`. effective_state is 'armed' only if BOTH
  global and sub rows read OK+armed, and 'unavailable' if EITHER read errors.
- Evidence: `tests/prediction_markets/test_arm_display_fixpass.py` (4 tests) — absent != unavailable, an
  indeterminate read is 'unavailable' not 'disarmed', armed/disarmed rows read their true state + ts. Commit `c5abd3e`.

## Item 3 — branch base verified; per-account driver wiring MERGED IN (conflict-free)
- Evidence (all main.py hashes CR-STRIPPED per the Measurement Rule — `git show <c>:file | tr -d '\r' | sha256sum`):
    merge-base(HEAD, per-account tip `81d9938`) = `f1e28cc`
    main.py:  f1e28cc `cc733a17…`  ==  e95e638 `cc733a17…`  ==  my HEAD (pre-merge) `cc733a17…`   (I never touched it)
              per-account tip 81d9938 `9e8da82…`   (the ONLY one that added driver_roster)
  So my base e95e638 had ONLY the single-account `scheduled_pm_live_loop` (main.py:1561); the per-account tip has
  `driver_roster.active_driver_subdivisions` spawning both accounts. My branch's main.py == the merge-base, and the
  two branches touch DISJOINT files (`comm -12` of their changed-file lists is empty; the per-account branch touches
  main.py + engine drivers + driver_roster.py/venue_exposure.py + 3 engine tests, ZERO pm_web files).
- ACTION: merged the deployed per-account tip `81d9938` into the UI branch (commit `e764eb5`) — conflict-free
  (disjoint change sets), no main.py editing. My branch's main.py is now `9e8da82…` (driver-wired,
  `driver_roster.active_driver_subdivisions` present, `driver_roster.py` on disk), so the branch carries Karen's
  wiring and cannot regress it under either a git merge or a wholesale graft.
- NOTE: the earlier "main.py VC gap" (box hash matching no commit) was itself a CRLF artifact — the per-account
  branch's own SW9 wrap says the same; CR-stripping resolves it, matching the Measurement Rule.

## Item 4 — read-only guard strengthened; arm link removed
- Removed the M5 admin cross-console link (`trading.jacksumner.com/pm/arm`) from `pm_accounts.html` — the design
  says "arming is done from the service, not here" (no link), and item 4 forbids any LINK reaching an arm action.
- New strong guard `tests/prediction_markets/test_web_r6::test_pm_web_display_pages_have_no_arm_control` over `/`,
  `/account/{id}`, `/live/{acct}/mlb`: asserts NO `<form`/`<button`/`type="submit"`/`hx-post|put|delete|patch`;
  NO `href|action|hx-*` attribute reaches an `/arm` or `/disarm` route (anchored on the `/arm` PATH segment so
  `/farm` is not a false positive); and the arm badge is a plain `<span class="badge …">`, never `<a>`/`<button>`.
  `test_m4_gates` arm-link test updated to assert the link is gone for everyone. Commit `c5abd3e`.

## Item 5 — sizing/config line moved into the trade-drawer footer
- Removed from the live page's h1 area; added to `partials/pm_trade_drawer.html` footer
  ("Markets: … · sizing: …"). Still in the page HTML, so `test_live_r3::test_sizing_display_*` /
  `test_config_visible` still find it (now in the drawer). Evidence: render grep shows
  "sizing: fixed · max(1, floor($5.00 / price)) …" in the drawer. Commit `c5abd3e`.

## Item 6 — zoneinfo replaces the hand-rolled DST rule
- `feed_mlb.py`: `_ET = ZoneInfo("America/New_York")`; `utc_to_eastern` = `dt.astimezone(_ET)`. Removed
  `_nth_sunday`/`_eastern_is_dst`. The Linux box has the system IANA tz db; the Windows test venv got `tzdata`
  (pure-data package; NOT a prod dependency — the box uses its system tzdb). The existing DST/rollover tests
  (`test_feed_mlb`: EDT, EST, and the 02:10Z-Sep3 -> 22:10 ET-Sep2 rollover) pass unchanged against zoneinfo. Commit `c5abd3e`.

## Item 7 — empty-card suppression comment
- `live_view.py`: the suppression is now commented "INTENTIONAL (board-accepted 2026-09-02, fix-pass item 7 -- do
  NOT 'fix' this back)" with the reason (a game with every position off-book has no card-worthy slot; its trades
  stay in the drawer). Commit `c5abd3e`.

## Reconciliation — full `tests/prediction_markets/` run
- All pm_web / UI tests PASS. 18 failures remain, ALL pre-existing env-gap, NONE from this work:
    17 pykalshi (engine driver -- broker lib absent in `.venv-webtest`): test_live_driver_r7c (7),
       test_kill_switch_r7d (4), test_shard_gate_r2 (4), test_liquidity_floor_r7f (1, ERROR at setup),
       test_sizing_contracts_r8 (1, ERROR at setup)
    1  stale schema: test_search_r1::test_schema_head_is_15 (base is schema 17, not 15)
- CORRECTION to my prior report: the true baseline is 18, not 16 — I earlier under-counted by missing the two
  collection-ERROR pykalshi tests (test_liquidity_floor_r7f, test_sizing_contracts_r8). The +2 is that under-count,
  not new failures. The item-3 merge added 3 engine test files (test_per_account_driver_n2,
  test_shard_snapshot_task_m3, test_venue_exposure_r7) — all PASS (not in the failing list). The one genuine
  regression from this fix pass (test_m4_gates arm-link, a consequence of item 4) is FIXED.

## Screenshots regenerated (item 1)
- `reports/prediction_markets/ui_verify/{05_accounts,06_account}.png` (+ the 4 live states) regenerated after the
  display-only removal + arm-badge change; harness still 14/14. 06_account shows the figs always rendered, the
  read-only "GLOBAL DISARMED" badge, and "snapshots are written every 5 minutes"; no "display-only" anywhere.

## NOT DEPLOYED, NOT RESTARTED
Nothing on the box was touched. No service was deployed, restarted, or reloaded. No engine code, engine-owned
table, or credential was modified (the item-3 merge INCORPORATED the already-deployed per-account engine code; it
authored no engine change). The armed, trading accounts were not touched.

==============================================================================================================
# DEPLOY -- 2026-09-02 14:11-14:56Z (Board-authorized, per-step). pm_web ONLY. Engine untouched.

Deployed the pm_web UI rewrite to prod as a pm_web-only file graft + two pm_web restarts (the second for the
entity fix). The engine (trading-corp PID 163519, NRestarts 0, up since 12:41:51Z) was NEVER restarted, reloaded,
or touched. Executed as separate board-authorized `.ps1` runners in `cc\` (recon / pull / deploy / restart /
verify / entity-fix / engcheck). Branch source `pm-ui-rewrite-2026-09-02 @ 9c2eeb3`.

## Pre-deploy checks (Step 1 recon + Step 2 local, all passed)
1. main.py CR-stripped sha16 = `9e8da82de3b8bfcf` (== the merged per-account tip; driver-wired). main.py NOT
   copied in any step.
2. Box app.py is M4: `grep -c is_admin` = 10, `grep -c /pm/arm` = 0. (Confirmed before AND after the graft.)
3. Engine PID 163519, pm_web PID 155543, schema 17; arm rows all armed=True/latched=False (global ts
   2026-08-31T02:35:38, jack ts 2026-08-31T21:49:39, karen ts 2026-09-02T12:53:23). Order baseline: jack 90,
   karen 5.
4. Backups (the rollback): main deploy `~/pm_ui_deploy_backup_20260902T142654Z` (app.py, arm.py, 4 templates);
   entity fix `~/pm_ui_entityfix_backup_20260902T144621Z` (3 templates).
   MEASUREMENT RULE applied throughout: all commit-vs-box comparisons CR-stripped (`| tr -d '\r'`); the box is
   LF so `sha256sum` on the box == the CR-stripped commit hash. No raw `git show | sha256sum` was trusted.

## The app.py GRAFT (never wholesale)
The box M4 app.py differs from my base (e95e638, M5) by EXACTLY ONE spot -- `_load_accounts_overview`'s return
adds a 2-line M5 comment + `"is_admin": is_admin_flag` (that IS the is_admin 12-vs-10 and the `/pm/arm` comment).
Constructed `target_app.py` = HEAD app.py MINUS that one M5 addition (my rewritten pm_accounts no longer uses
is_admin). VERIFIED locally: `grep -c is_admin` = 10, `grep -c /pm/arm` = 0, py_compile OK, and box(M4)->target
diff = 100% my UI edits with ZERO M5 leak. Post-graft ON THE BOX: is_admin stays 10, /pm/arm stays 0 (Gate-A).
Likewise `pm_accounts.html`: the box M4 template differed from base by only the M5 cross-console arm link (pulled
+ diffed to confirm); my M5-clean rewrite (0 `/pm/arm`) replaced it wholesale.

## Files shipped (16 write ops, each sha16-verified on write)
- NEW (9): feed_mlb.py, marks.py, ui_cache.py, poller.py, live_view.py, static/pm_desk.css, static/pm_live.js,
  templates/partials/pm_arm_badge.html, templates/partials/pm_trade_drawer.html.
- REPLACED (4 templates, box==base confirmed): pm_shell.html, pm_account.html, pm_live_subdivision.html;
  pm_accounts.html (M5-clean rewrite). ADDITIVE (1): arm.py (box==base; adds read_display only).
- GRAFT (1): app.py (constructed M5-clean). main.py NOT shipped.
- Entity fix re-ship (3, second restart): pm_account.html, pm_live_subdivision.html, partials/pm_trade_drawer.html.
Gate-A on the box after the main graft: py_compile OK; app imports; forbidden engine imports NONE (standalone).

## Restart (pm_web ONLY, az-root)
`az vm run-command ... systemctl restart prediction-markets-web`. pm_web PID 155543 -> 165582 (main) ->
166025 (entity fix); ActiveState active/running. Engine trading-corp PID = 163519 immediately after EACH restart.

## Post-deploy verification (Step 6, after a full poll cycle -- all green)
10. `/`, `/account/kalshi_jack`, `/account/kalshi_karen`, `/live/kalshi_jack/mlb`, `/live/kalshi_karen/mlb`,
    `/farm`, `/farm/mlb` all return 200 (curl loopback 127.0.0.1:8081, Remote-User forged as the proxy sends).
11. Both accounts render the SAME template: "display-only" occurrences = 0, "funding-only" = 0, both account
    pages carry TRADING + Aggregate performance.
11b. NO double-escaped entities: `&amp;middot;` = 0, `&amp;mdash;` = 0 (the entity fix -- see below). Karen's
    sub-division label source is `Karen &middot; MLB` -> renders "Karen . MLB".
12. Arm badge reads the PERSISTED rows: it shows ARMED with an age chip whose data-age0 (216747s) equals the
    global row's ts age (~216746s); all three rows armed=True.
13. Open positions are bid-valued or a defined empty state: jack bet-slots $2.25..$5.10; karen $2.55..$3.05; the
    rest render em-dash (not-held slots). ZERO NaN / torn-empty `bv`. Pollage "updated 17s ago" (poller live).
14. Engine sanity: PID 163519 unchanged; NRestarts 0; ZERO engine ERROR lines (`journalctl -u trading-corp -p
    err --since 25min` = "No entries" -- the verify's earlier "1" was a FALSE POSITIVE: `grep -c .` counted the
    literal "-- No entries --" message). Order counts moved jack 90->91, karen 5->6 -- this is the ENGINE's
    NORMAL unattended trading across the ~27-min deploy window; pm_web placed nothing (it is credential-free and
    imports no broker). No orders were added by the deploy or the restarts.
15. Farm League unchanged: `/farm/mlb` has 30 "Analyze" occurrences and 29 loss-omission markers.

## The entity fix (Board caught 'Karen &middot; MLB' rendering literally)
HTML entities placed INSIDE a Jinja `{{ }}` expression are autoescaped (`&` -> `&amp;`), so they showed
literally. Fixed 3 templates: the account/live sub-label fallback now emits `&middot;` as RAW HTML (outside the
`{{ }}`); the drawer/linescore/matchup em-dash fallbacks use `|safe` (matching the existing farm partials);
`'Realized P&amp;L'` -> `'Realized P&L'` (single-escape). Only the no-sub_label path was affected (Karen).
Committed `9c2eeb3`; re-shipped + restarted; verified `&amp;middot;` = 0 live.

## Nothing skipped except by instruction
main.py was deliberately NOT copied (it carries the live per-account driver wiring). No package was installed, no
venv/systemd change was made, no engine file was touched. Rollback was available at every step (backups above)
and never needed.

---

# POST-DEPLOY PASS -- 2026-09-02 (build + test ONLY; NOTHING deployed, NOTHING restarted)

Board follow-up on the deployed UI (branch `pm-ui-rewrite-2026-09-02`, from the deployed code `9c2eeb3`).
Four items, all built + tested locally. **No restart, no deploy -- those remain Board decisions. The engine
(trading-corp PID 163519, ARMED, trading two accounts unattended) was not touched.** The one box action was a
single READ-ONLY check (`pm_postdeploy_ro.sh`): it reads box files, the PM DB `mode=ro`, and Kalshi's PUBLIC
market endpoint via `marks.py` (no creds, no orders) -- it writes nothing and restarts nothing. That check
re-confirmed: trading-corp PID **163519**, **NRestarts=0**, ActiveState=active; pm_web PID 166025 unchanged.

## Item 1 -- mark-coverage label shown under EVERY current-value figure (incl. full coverage and 0 positions)
The `N of M priced` coverage label now travels with every current-value figure, not only the partial case:
- **`live_view.build_live_context`** now emits `unsettled_priced` / `unsettled_total` on `summary` (counted over
  the open, unsettled bet-slots across the board).
- **`pm_accounts.html` + `pm_account.html`** -- the `openvalue` macro renders four honest states: `$0.00
  (0 positions)` when M==0; `no mark (0 of N priced)` when nothing is priced; `$V (N of M priced)` NEUTRAL
  (`dim`) at full coverage (N==M); `$V (partial: N of M priced)` AMBER (`wsm`) when 0 < N < M.
- **`pm_live_subdivision.html`** -- the "Unsettled -- current value" strip cell carries the same label always
  (`0 positions` / `N of M priced` neutral / `partial: N of M priced` amber). The value shown is unchanged from
  before (priced==0 -> "no mark"; total==0 -> "$0.00"); only the coverage label was added. Full != partial is
  visually distinct (neutral grey vs amber).

**Full-coverage render (regenerated locally, marks primed so every position prices):**
```
/ (accounts overview) : jack  $0.62 (1 of 1 priced)   karen  $0.00 (0 positions)
/account/kalshi_jack  : aggregate $0.62 (1 of 1 priced) ; subdivision row open $0.60 at cost / $0.62 (1 of 1 priced) value
/live/kalshi_jack/mlb : division strip  1 of 1 priced   (partial: absent -> not flagged partial)
```
Durable full-coverage tests added: `test_accounts_m2::test_mark_coverage_label_shown_at_full_coverage`
(asserts `1 of 1 priced` on `/` and `/account`, `0 positions` for karen, and NO `partial:`), and
`test_live_r3::test_division_strip_coverage_label_shown_at_full_coverage` (division strip `1 of 1 priced`, no
`partial:`). Both green.

## Item 2 -- per-account mark coverage (open tickers with a bid vs total; unpriced named)
Read from `marks.py` read-only (a fresh fetch of Kalshi's public market endpoint -- the exact reader pm_web's
poller uses; no creds) joined to the journal-derived open positions (PM DB `mode=ro`, `subdivision.live_positions`).
Fetch at the 2026-09-02T16:31:20Z run: `ok=True`, `as_of=1788366680`, 335 open MLB markets, no error.

| Account | Category | Open tickers priced | Unpriced tickers |
|---|---|---|---|
| kalshi_jack | mlb | **8 of 8** | none -- fully priced |
| kalshi_karen | mlb | **6 of 6** | none -- fully priced |

Both accounts are fully priced at read time, so there are no unpriced tickers to name. (Coverage is a live,
market-dependent quantity: a ticker goes unpriced only when Kalshi has no resting bid on the held leg -- e.g. a
suspended/late market -- at which point the UI shows the honest `partial: N of M priced` / `no mark`, never a $0.)

## Item 3 -- absent arm row renders "NEVER ARMED" (distinct from DISARMED and STATE UNAVAILABLE)
`read_display()` already distinguishes `absent` (table read cleanly, no row -> cold start) from `disarmed` (a
row that says off) and `unavailable` (an indeterminate mode=ro read). The BADGE macro (`pm_arm_badge.html`) now
renders each as its own label:
- `armed` -> ARMED (green, age chip)   `disarmed` -> DISARMED (grey, age chip)
- `unavailable` -> STATE UNAVAILABLE (amber)   **`absent` -> NEVER ARMED (grey, hollow-dot `badge never`, NO age chip)**

`absent` carries no timestamp, so no age chip is drawn (guarded on `state != 'absent'` as well as `age is not
none`). The gate/read_status() fail-safe semantics are UNCHANGED -- this is display-only; the CLI stays the
authoritative kill path, and the badge remains a plain `<span>` with no control. New unit tests render the real
macro through a minimal Jinja env: `test_badge_absent_renders_never_armed_no_chip` (asserts NEVER ARMED, no
`chip`, `badge never`, and NOT collapsed to DISARMED/UNAVAILABLE) and `test_badge_states_render_distinct_labels`.
The three no-row page-render assertions that previously read "GLOBAL DISARMED" were corrected to "GLOBAL NEVER
ARMED" (test_accounts_m2 x2, test_live_r3 x1). `test_pm_arm_view_m5::test_page_renders_disarmed` was left as
"DISARMED": it seeds an explicit `armed:False` row (a real disarm), which is correctly still DISARMED.

## Item 4 -- box <-> branch drift check (box files hashed CR-stripped; compared to `9c2eeb3`)
The 15 shipped files, box CR-stripped sha256 (first 16) vs the deployed commit `9c2eeb3` (both sides CR-stripped,
Measurement Rule). **14 identical; app.py differs ONLY by the known M5 hunk. No other drift -> no STOP.**

| File | box sha16 | branch `9c2eeb3` sha16 | match |
|---|---|---|---|
| arm.py | 60f447207d52694a | 60f447207d52694a | yes |
| web/app.py | **8b7d35ca88432603** | 2a1c341d2e855ee2 | **grafted (M5 hunk only)** |
| web/feed_mlb.py | 7f05607bb887bb51 | 7f05607bb887bb51 | yes |
| web/marks.py | 46ca99a80f7d2827 | 46ca99a80f7d2827 | yes |
| web/ui_cache.py | e116ee8ae07e8112 | e116ee8ae07e8112 | yes |
| web/poller.py | 44a6b51da0ad36dd | 44a6b51da0ad36dd | yes |
| web/live_view.py | ec9ef0fb791537e7 | ec9ef0fb791537e7 | yes |
| static/pm_desk.css | abb6affb3ca4987e | abb6affb3ca4987e | yes |
| static/pm_live.js | b4c557fcf4e341e8 | b4c557fcf4e341e8 | yes |
| partials/pm_arm_badge.html | 1f743caebca30541 | 1f743caebca30541 | yes |
| partials/pm_trade_drawer.html | 118e54bca0255682 | 118e54bca0255682 | yes |
| pm_shell.html | c3ddce77a5fb4f9c | c3ddce77a5fb4f9c | yes |
| pm_accounts.html | e5d99d0c4f9c80b8 | e5d99d0c4f9c80b8 | yes |
| pm_account.html | b3fba25a18245d1e | b3fba25a18245d1e | yes |
| pm_live_subdivision.html | 4c15633d805dc52d | 4c15633d805dc52d | yes |

**app.py difference characterized:** box `web/app.py` == the GRAFTED M4 target deployed on 2026-09-02
(`8b7d35ca88432603`); `9c2eeb3`'s app.py is the M5 HEAD (`2a1c341d2e855ee2`). Concrete markers confirm the diff
is exactly the M5 hunk and nothing else: box app.py has `is_admin` x10 and `/pm/arm` x0 (the M4 state), where
M5 HEAD has `is_admin` x12 and `/pm/arm` x1. This is the intended, documented graft (M5 must not leak to prod
before its window), not drift.

**File-count clarification:** this is **15 UNIQUE shipped files** (14 identical + app.py). The DEPLOY section's
"16" counted WRITE OPERATIONS -- three templates (`pm_account.html`, `pm_live_subdivision.html`,
`pm_trade_drawer.html`) were written twice, once in the main graft and once in the entity-fix. The drift check
is over the 15 unique files; the box carries the entity-fixed versions of those three (shas above).

## Verification -- full suite unchanged; the "18" was approximate, the true baseline is 19
`tests/prediction_markets/` (offline, `.venv-webtest`, `-p no:pytest_ethereum`):
- **Clean baseline (my edits stashed): 19 failed**, 1 skipped, ~777 passed.
- **This branch (edits restored): the SAME 19 failed** (byte-identical set), +4 net tests, ALL of the new/updated
  tests green. Zero regressions introduced by any UI change.

The 19 are all pre-existing env-gap / schema-drift, none touching a UI file:
- **17x** `ModuleNotFoundError: No module named 'pykalshi'` (the engine driver dep, absent locally): 2 in
  test_idempotency_r7h, 4 in test_kill_switch_r7d, 7 in test_live_driver_r7c, 4 in test_shard_gate_r2.
- **1x** `test_search_r1::test_schema_head_is_15` -- asserts head 15 but the box/local schema is now **17**
  (migrations 016 + 017 landed in prior deploys). Schema drift in a stale assertion, unrelated to the UI.
- **1x** `test_web_healthz::test_pm_web_imports_no_engine` -- the import-closure guard spawns `python -c "import
  trading_corp..."` in a bare subprocess that has no package on `sys.path` locally (`No module named
  'trading_corp'`); it passes on the box, where the package is importable. Env-gap, not a real leak.

The brief expected "18"; the measured baseline is 19 and my edits leave it exactly 19 (the delta is a
baseline-count approximation, not a new failure -- proven by stashing the edits and re-running: same 19).

## Files that WOULD need to ship for this pass (pm_web-only; NO app.py, NO main.py this time)
Six pm_web files changed (working-tree sha16 -> was, at `9c2eeb3`). **None is app.py** -- so unlike the initial
deploy, this pass needs NO app.py graft; and main.py is untouched as always.
```
df57a85732fbd9f6  (was ec9ef0fb791537e7)  web/live_view.py
8ffe129198a5eed5  (was abb6affb3ca4987e)  web/static/pm_desk.css
6bb019840c5e2b2a  (was 1f743caebca30541)  web/templates/partials/pm_arm_badge.html
014c03bafe3005e5  (was e5d99d0c4f9c80b8)  web/templates/pm_accounts.html
a5f39df0b53dedb4  (was b3fba25a18245d1e)  web/templates/pm_account.html
e1edc02f54aaa369  (was 4c15633d805dc52d)  web/templates/pm_live_subdivision.html
```
Test-only (not shipped to the box): `test_accounts_m2.py`, `test_live_r3.py`, `test_arm_display_fixpass.py`.
These edits are on the branch WORKING TREE (uncommitted); nothing was committed, deployed, or restarted in this
pass. A deploy, if the Board authorises one, would be pm_web-only (these 6 files), one pm_web restart, engine
untouched -- the app.py M4/M5 graft hazard does NOT apply to this set (app.py is unchanged).

> The post-deploy pass was subsequently COMMITTED as `f55bca6` (from `b8fdf30`) before the pre-deploy additions
> below; it was still never deployed or restarted.

---

# PRE-DEPLOY ADDITIONS -- 2026-09-02 (build + test ONLY; NOTHING deployed, NOTHING restarted)

Board follow-up on Jack's live screenshot (four card-level fixes). Built + tested on branch
`pm-ui-rewrite-2026-09-02`, committed **`2c7077b`** (parent `f55bca6`). **No restart, no deploy -- Board
decisions. The engine (trading-corp PID 163519, ARMED, trading two accounts unattended) was not touched; no box
action was taken in this pass at all (pure local build + test).**

## Item 1 -- game DATE and TIME on every card
A scheduled-first-pitch line now sits under the matchup on every game card: `Wed Sep 2 · 6:40 PM ET`, compact,
same mono/dim styling as the header chips. It is **sourced from the Kalshi ticker's ET date + HHMM**
(`live_view._fmt_et_datetime`, fed by `card.start_display`), so it renders even when the sports feed is down --
and it is ALWAYS present (em-dash if a ticker carries no time), so card height is uniform across cards.
- Mismatch, never silent: when the JOINED StatsAPI game carries a DIFFERENT scheduled time, the card shows the
  FEED's time and `card.time_mismatch` records both. That flag rides each affected trade row into the drawer:
  a `†` marker on the always-visible main row plus a "Scheduled start -- feed vs ticker" detail note giving both
  times and the source. `_trade_rows` now joins via `match_in_slate` (tolerant), so the matchup and the flag
  resolve even under the skew.

Regenerated render (one /live page carrying a card per state) -- the start line is present on ALL cards,
including the feed-unavailable one:
```
Wed Sep 2 · 1:10 PM ET   (SEA@BOS suspended)      Wed Sep 2 · 7:05 PM ET   (ATL@WSH in-progress)
Wed Sep 2 · 4:10 PM ET   (ATH@TEX final)          Wed Sep 2 · 8:10 PM ET   (HOU@SEA, feed 20:10 vs ticker 20:07 -> †)
Wed Sep 2 · 6:10 PM ET   (TOR@CLE delayed)        Wed Sep 2 · 9:40 PM ET   (PHI@AZ postponed)
Wed Sep 2 · 6:40 PM ET   (SD@CIN preview)         Wed Sep 2 · 10:10 PM ET  (NYY@LAA FEED N/A -- still shows the ticker time)
```

## Item 2 -- pre-game state is now honest (not "game over", no phantom score)
The screenshot's PREVIEW cards read "no count / game over" and showed "0 0". A game that has not started is not
over and is not 0-0. Fixed:
- `_feed_block` gains `started` (true only once the game has produced play: status in in_progress/final/suspended,
  or any linescore cell present). `linerow` renders a score digit ONLY when `feed.started` -- a pre-game 0-0 (or
  a feed that reports 0) renders BLANK, not "0". One honest rendering applied to every pre-game card.
- `metacol` count area is now state-specific: `preview` -> "not started"; `final` -> "no count / game over";
  `postponed` / `suspended` / `delayed` -> their own labels; feed-down -> "count unavailable". The status chip
  and inning slot label Postponed/Suspended/Delayed as their own states (amber), never as pre-game and never as
  over. Mapping is StatsAPI `detailedState`/`abstractGameState` (already normalized in `feed_mlb._map_status`).

Regenerated render (count-area label per state):
```
preview -> "not started"      final -> "no count / game over"      postponed -> "postponed"
suspended -> "suspended"      delayed -> "delayed"                 feed N/A -> "count unavailable"
```
A pre-game card seeded with a feed 0-0 renders NO score digit (`<span class="r">0</span>` absent from the HTML).

## Item 3 -- bet-slot shorthand carries direction
`_short_label` rewritten (strike = N - 0.5, the Kalshi convention):
- TOTAL: over/under as a sign on the strike -- **`+8.5` (Over = the YES leg) / `-8.5` (Under = the NO leg)**.
- SPREAD: sign + the team backed -- **`-1.5 ATL` (YES = the anchor team lays the spread) / `+1.5 SD` (NO = the
  other team gets it)** (`_spread_other` resolves the opponent from the ticker stem).
- A SETTLED slot (held leg unknown) shows the line/anchor WITHOUT a fabricated direction (`8.5`, `-1.5 SD`).
- MONEYLINE unchanged (the YES club abbr).

Tested four cases: `+8.5` / `-8.5` / `-1.5 ATL` / `+1.5 SD`. Live render confirms the TOT slots show `+8.5`.

## Item 4 -- the toggle buttons no longer run together
Root cause: the Active/Complete toggles are `<a>` anchors, but the padding/divider CSS targeted `<button>` only
(`.toggle button` / `button.mini`), so the anchors rendered as bare inline text -- "Active (6)Complete (11)".
Fixed by giving the toggle its own anchor class `.tgl` with padding + a `+`-divider and the pressed state
(`.toggle .tgl`), scoped so nothing else (the "Poll now" `.mini` link) is affected. Render confirms two separate
segmented anchors (`class="tgl"` x2).

## Verification
- Full `tests/prediction_markets/` (offline, `.venv-webtest`): **19 failed = the SAME env-gap/schema baseline**
  (17× pykalshi `ModuleNotFoundError`, 1× `test_schema_head_is_15` [schema is 17], 1× `test_pm_web_imports_no_engine`
  [bare-subprocess import; passes on the box]). **+12 new tests, all green; zero regressions** -- the existing
  live-page / read-only-guard / nav tests still pass with the template changes.
- New `test_live_card_states_predeploy.py` (12): directional short-labels (4 cases), datetime shapes,
  `_feed_block` per state incl. the pre-game-0-0 case, `build_live_context` start_display / time_mismatch /
  slot direction, and a TestClient render of pre-game/final/feed-down + the mismatch drawer flag.
- Regenerated the live render across all states -- pre-game, in-progress, final-unsettled, postponed, suspended,
  delayed, feed-unavailable, and time-mismatch -- with the date/time line present on every card.

## Shippable file list (pm_web-only; NO app.py, NO main.py)
`git diff --name-only 9c2eeb3 HEAD` over `web/` and `arm.py` -> **app.py and main.py are NOT in the diff**, so the
app.py M4/M5 graft hazard does NOT apply to any deploy of this branch tip. A deploy would ship the 7 pm_web files
below (the union of the post-deploy pass + these additions), CR-stripped sha16 -> (box `9c2eeb3`):
```
3144f4267243a55f  (box ec9ef0fb791537e7)  web/live_view.py                          [both passes]
2681180ca6a423b8  (box abb6affb3ca4987e)  web/static/pm_desk.css                    [both passes]
6bb019840c5e2b2a  (box 1f743caebca30541)  web/templates/partials/pm_arm_badge.html  [post-deploy pass]
48d579db5c2deb65  (box 118e54bca0255682)  web/templates/partials/pm_trade_drawer.html [pre-deploy]
a5f39df0b53dedb4  (box b3fba25a18245d1e)  web/templates/pm_account.html             [post-deploy pass]
014c03bafe3005e5  (box e5d99d0c4f9c80b8)  web/templates/pm_accounts.html            [post-deploy pass]
769044d17363e73c  (box 4c15633d805dc52d)  web/templates/pm_live_subdivision.html    [both passes]
```
The four files THIS pass changed: `live_view.py`, `static/pm_desk.css`, `templates/partials/pm_trade_drawer.html`,
`templates/pm_live_subdivision.html`. Test-only (not shipped): `test_live_card_states_predeploy.py`.

## Nothing was deployed or restarted
Pure local build + test. No box action was taken in this pass. The engine (trading-corp PID 163519, ARMED) was
not touched, no pm_web restart, no schema/venv/systemd change. Committed on the branch at `2c7077b`; prod-live and
main-wip are NOT advanced (box-is-truth). Commits this session: `f55bca6` (post-deploy pass), `2c7077b`
(pre-deploy additions).

---

# DEPLOY 2 -- 2026-09-02 (DEPLOYED LIVE; pm_web-only + ONE pm_web restart; engine never touched)

Board-authorized deploy of the post-deploy pass + pre-deploy additions. **pm_web-ONLY, wholesale copy (no graft),
one pm_web restart. The engine (trading-corp PID 163519, ARMED, trading two accounts unattended) was never
restarted, reloaded, or touched -- verified PID 163519 / NRestarts 0 at every step.** Per-step board-authorized.

## Step 0 -- settled bet slots now carry the same directional shorthand (deploy target `cafb132`)
A settled TOT/SPR slot dropped the sign ("TOT 8.5" not "TOT +8.5"). Fix: `_settled_leg` derives the held side
from the filled ENTRY rows' `outcome_leg` (the ticker leg we bought -- the SAME yes/no a live slot uses), and
`_build_slot`'s settled branch labels via it (`short` + `desc`). Genuinely-unrecorded/ambiguous leg (entries on
both legs, or no filled entry with a leg) -> the line WITHOUT a sign, never a guess. Tests added (`_settled_leg`
derivation; settled TOT `+8.5`/`-8.5`; settled SPR `-1.5 SD`/`+1.5 CIN`; ambiguous -> no sign). Full suite: 19
env-gap baseline unchanged, zero regressions. Commit **`cafb132`** (`cafb132523b26e9df00e8379dd21b11dba542bce`),
parent `8ae3edf`. LIVE render (check 11) shows every held/settled TOT/SPR slot carrying a sign, 0 missing.

## Source & scope
Ship exactly the pm_web files that differ from the deployed `9c2eeb3`. `git diff --name-only 9c2eeb3 cafb132`
over `web/` = 7 files; **app.py and main.py do NOT differ vs `9c2eeb3`** -> the graft rule does not apply
(wholesale copy). Engine files on the branch (main.py, driver_roster.py, venue_exposure.py, engine tests) were
NOT shipped.

## Pre-deploy checks (all PASS -- `pm_deploy2_checks.sh`, 2026-09-02T17:29:14Z)
1. Engine PID **163519**, NRestarts **0**, active; pm_web PID **166025**, active; PM schema head **17**. Arm rows
   (agent_state / pm_live) all armed=True with ts: `arm:global` 2026-08-31T02:35:38Z, `arm:kalshi_jack:mlb`
   2026-08-31T21:49:39Z, `arm:kalshi_karen:mlb` 2026-09-02T12:53:23Z.
2. Each of the 7 box files, CR-stripped, EQUALS its `9c2eeb3` version -- **box == last deploy, zero drift**
   (all 7 `[OK]`).
3. Box app.py M4 markers: `is_admin` **=10**, `/pm/arm` **=0**.
4. Backup of all 7 files -> **`/home/azureuser/pm_deploy2_backup_20260902T172914Z`** (7 files). Journal order
   counts (dry_run=0): kalshi_jack **93**, kalshi_karen **6**.

## Deploy (`pm_deploy2_apply.sh`, 17:30:29Z) -- 7 files, wholesale, sha-verified on the box
Each file base64-embedded (LF, CR-stripped), written, and its CR-stripped sha16 re-checked == the `cafb132`
target. Result: **ALL 7 OK**.
```
6d3b7b3300782d3e  web/live_view.py                          (box was ec9ef0fb791537e7)
2681180ca6a423b8  web/static/pm_desk.css                    (box was abb6affb3ca4987e)
6bb019840c5e2b2a  web/templates/partials/pm_arm_badge.html  (box was 1f743caebca30541)
48d579db5c2deb65  web/templates/partials/pm_trade_drawer.html (box was 118e54bca0255682)
014c03bafe3005e5  web/templates/pm_accounts.html            (box was e5d99d0c4f9c80b8)
a5f39df0b53dedb4  web/templates/pm_account.html             (box was b3fba25a18245d1e)
769044d17363e73c  web/templates/pm_live_subdivision.html    (box was 4c15633d805dc52d)
```

## Restart (pm_web ONLY, 17:30–17:31Z)
The ssh path could not restart (systemctl needs root; `sudo` has no TTY in a streamed session -- it failed SAFE,
changing nothing, pm_web PID stayed 166025). Restart was done via the proven pm_web path -- `az vm run-command`
(runs as root): `systemctl restart prediction-markets-web`. pm_web **166025 -> 167940** (active, running);
**engine trading-corp PID 163519 UNCHANGED, NRestarts 0** immediately after. No package/venv/unit-file change.

## Post-deploy verification (`pm_deploy2_verify.sh`, after a 90s poll cycle, 17:35Z) -- checks 8–17 all PASS
- **[8]** 7 pages 200: `/`, `/account/kalshi_jack`, `/account/kalshi_karen`, `/live/kalshi_jack/mlb`,
  `/live/kalshi_karen/mlb`, `/farm`, `/farm/mlb`.
- **[9]** date/time line on EVERY card: jack 6/6, karen 6/6. Sample: `Wed Sep 2 · 12:40 PM ET`, `· 1:05 PM ET`,
  `· 2:35 PM ET` (real `·`, not a double-escaped entity).
- **[10]** no pre-game card reads "game over" and none shows a score digit: jack 4 PREVIEW cards / karen 5, both
  `game over`=0 and `score`=0; count-label = `not started`. No postponed/suspended/delayed cards on today's slate
  (their labels are proven by test + the pre-deploy render harness).
- **[11]** held TOT/SPR slots missing a direction sign: **0** on both live pages. LIVE samples: `-9.5`, `+7.5`,
  `-8.5` (TOT), `+1.5 WSH` (SPR), `AZ` / `CLE` (ML) -- settled slots included (step 0 live).
- **[12]** coverage label present on all five surfaces (`/`, both `/account`, both `/live`). marks fetch ok,
  336 markets. **kalshi_jack 8 of 8 priced, kalshi_karen 6 of 6 priced -- no unpriced tickers.** Samples:
  `8 of 8 priced`, `6 of 6 priced`, `0 positions`.
- **[13]** arm badge = `class="badge armed"` -> `GLOBAL ARMED` on `/`; on BOTH live pages the GLOBAL badge AND
  the effective badge read `ARMED`; **`DISARMED` / `STATE UNAVAILABLE` on live pages = 0; `NEVER ARMED` anywhere
  = 0**. (Age chips render from the rows' ts.)
- **[14]** Active/Complete toggles = two separate `.tgl` controls per live page (2 each).
- **[15]** double-escaped entities: `&amp;middot;`=0, `&amp;mdash;`=0, `&amp;dagger;`=0, `&amp;ndash;`=0.
- **[16]** engine: PID **163519** unchanged, NRestarts **0**, ERROR entries since the restart **0** (counted
  actual lines, not the "No entries" message). Journal order counts **UNCHANGED** vs pre-deploy: kalshi_jack 93,
  kalshi_karen 6 -- pm_web placed nothing (credential-free, imports no broker).
- **[17]** Farm League unchanged: `/farm/mlb` Analyze occurrences **30**, loss-omission markers **87**.

## Rollback
Not needed -- checks 8–16 all passed. Backup retained at `/home/azureuser/pm_deploy2_backup_20260902T172914Z`
(restore + pm_web-only restart via `pm_deploy2_rollback.sh` if ever required; DANGEROUS if restored later -- it
reverts DEPLOY 2).

## What was NOT done / notes
- Engine NEVER restarted or touched (PID 163519 / NRestarts 0 at every step). No schema/venv/systemd change.
- The direction sign uses ASCII `+`/`-` (e.g. `-8.5`), the trading convention, not the typographic minus in
  Jack's example -- a one-character change if the board prefers `−`.
- prod-live / main-wip pointers NOT advanced and the branch is NOT pushed (box-is-truth; the box is the deployed
  truth, the branch is the record). Deploy target = branch `pm-ui-rewrite-2026-09-02` @ **`cafb132`**.
- Runners in `cc/`: `pm_deploy2_checks`, `pm_deploy2_apply` (+ `gen_deploy2.py`), `pm_deploy2_restart_az.ps1`
  (az/root -- the ssh `pm_deploy2_restart.*` variant failed-safe on the sudo-TTY limit), `pm_deploy2_verify`,
  `pm_deploy2_evidence`, `pm_deploy2_rollback.sh`.

---

# CARD POLISH -- 2026-09-03 (build + test ONLY; NOTHING deployed, NOTHING restarted)

Board follow-up on Jack's live view (deployed code `cafb132`). Five card-level fixes (items 1-4 from the brief +
item 5 from Jack's live feedback). Built + tested on branch `pm-ui-rewrite-2026-09-02`, committed **`431ec76`**.
**No deploy, no restart -- the engine (trading-corp PID 163519, ARMED) was not touched. All box access this pass
was READ-ONLY** (fetched the live page + served CSS to diagnose item 1; headless-rendered PNGs locally).
**No app.py or main.py change** -- `git diff cafb132 HEAD` over app.py/main.py is empty, so the M4/M5 graft rule
does NOT apply to this pass.

## Item 1 -- ACTIVE/COMPLETE toggle: ROOT CAUSE = stale static-asset cache (not a CSS/markup/specificity bug)
I fetched the live page and served CSS from the box (read-only) and rendered the deployed CSS headless. Findings:
- The served `/static/pm_desk.css` **contains** the `.toggle .tgl` rules (CR-stripped sha16 `2681180c` = the
  DEPLOY 2 target); the live page **has** `class="tgl"` on both anchors; the `<link>` order is `pm.css` then
  `pm_desk.css` (desk wins ties); and `.toggle .tgl` (specificity 0,2,0) outranks `.desk a` (0,1,1) and pm.css's
  `a{...}` (0,0,1). So markup, class, rule, and specificity are all correct on the server.
- **Rendering the deployed CSS headless produces the exact prototype**: a bordered segmented control, ACTIVE
  filled, COMPLETE dimmed, a divider between, padding, ~16px before the caveat (`renders/before_toggle.png`).
- Therefore the run-together blue text on Jack's screen is **not** a server bug -- it is the browser applying an
  OLD cached `pm_desk.css` (the file was updated in DEPLOY 2 but the static server sends no cache-busting, so a
  browser that cached the pre-DEPLOY-2 copy -- which had no `.toggle .tgl` -- never re-fetched, and the anchors
  fell back to the plain `a`: no padding, blue). None of the three candidate causes (specificity / missing class
  / never shipped) applies.

**Fix (pm_web-only, no app.py):** version the shell's three static assets with their own content sha8 --
`pm.css?v=204d9051`, `pm_desk.css?v=1b3b8ccc`, `htmx.min.js?v=491955cd` -- so a changed file gets a new URL and
is always re-fetched. `test_asset_cache_bust_hashes_match_files` recomputes each file's CR-stripped sha8 and
asserts the shell's baked `?v=` matches, so any future CSS/JS change that forgets to bump the hash fails CI rather
than shipping a stale asset. The toggle CSS itself was already correct and is unchanged.
(Evidence: `renders/before_toggle.png`, `renders/after_toggle.png` -- identical proper segmented control.)

## Item 2 -- game-state full card border
Three GAME states get a full card border (`renders/after_active_1600.png`, `..._1280.png`):
- LIVE (in_progress, incl. inning breaks) -> `border-color:var(--live)` (blue).
- NOT STARTED (preview/scheduled) -> `border-color:var(--line2)` (subtle). Postponed / suspended / delayed reuse
  this border AND keep their amber chips.
- COMPLETE (final) -> `border-color:var(--off)` (grey).
- Feed-unavailable -> NO state border (amber treatment kept).
The base `.g` border is `1px solid transparent` so no card shifts by state. The **MIXED green top accent
(position state) coexists with the border (game state)** -- verified on SEA@BOS (`1 SETTLED · 1 LIVE`): grey
COMPLETE border + green top accent, both visible, not fighting. The legend gains `card border: live game / not
started / complete`. Existing palette only. (Test: `test_card_state_border_classes` -- st-live/st-pre(preview &
postponed)/st-complete, feed-unavailable none.)

## Item 3 -- count pips clear at an inning break
When StatsAPI `inningState` is Middle or End, `feed_mlb` (both the StatsAPI and ESPN parsers) now emits no
balls/strikes/outs and empty bases -- the count resets between half-innings. The inning label is kept and
MIDDLE -> **`MID`** (short, consistent with TOP/BOT). Rendered: `MID 4` with all pips hollow and no base runners
(`renders/after_ATH.png`; before it showed `MIDDLE 4` with the last PA's lit pips). Tests:
`test_feed_parse_inning_break_clears_count` (parser: Middle/End -> half MID/END, count None, bases (), inning
kept; Top keeps the count) and `test_template_inning_break_renders_empty_pips_and_label` (no lit pips, no base
`on`, `MID 4` shown).

## Item 4 -- date/time chip shortened
Weekday dropped: `_fmt_et_datetime` now returns `Sep 2 · 12:40 PM ET`. Right-aligned on its own line under the
header so it never crowds the feed chip; verified fitting at 1280px (`renders/after_active_1280.png`). Datetime
test updated (`Sep 2 · …`).

## Item 5 -- diamond lines + scoreboard font readability (Jack's live feedback)
Jack reported the diamond lines and scoreboard font were hard to see on the dark panel. Fixed:
- Diamond outline `.sq` **2px** (was 1px) and **#45597a** (brighter than the old `--line2` #2b3a4e); the base
  diamonds and home plate lifted to the same #45597a; the feed-dead dashed square lifted to #2f3d4f.
- Scoreboard digits lifted a full step: leading row stays brightest (`--text`); leading per-inning digits
  #c3ccd8, trailing team's abbr/runs #aeb9c7 and per-inning digits `--dim` (so the lead still reads at a glance);
  unplayed cells #3a4a5f (recessed but no longer near-invisible).
Jack confirmed on the re-render: "Much better." (`renders/after_PHI.png`, `renders/after_ATH.png`.)

## Verification
- Full `tests/prediction_markets/`: **19 failed = the SAME env-gap/schema baseline** (17× pykalshi, 1×
  `test_schema_head_is_15` [schema 17], 1× `test_pm_web_imports_no_engine` [bare-subprocess import; passes on the
  box]); the card-polish tests are green; **zero regressions**.
- Regenerated + VIEWED the live render in every state at 1600px and 1280px: pre-game, in-progress with runners +
  count, inning break with empty pips, final-unsettled, complete, mixed, postponed, feed-unavailable. The toggle
  is a segmented control in the PNG.

## Shippable file list (pm_web-only; NO app.py, NO main.py -> no graft)
`git diff --name-only cafb132 HEAD` over `web/` = 5 files (HEAD CR-stripped sha16 -> deployed `cafb132` sha16):
```
467d528460421a31  (cafb132 7f05607bb887bb51)  web/feed_mlb.py                          [item 3; NEW to the shipped set]
2c7c8875cd80e768  (cafb132 6d3b7b3300782d3e)  web/live_view.py                         [item 4]
1b3b8ccc6dff50cc  (cafb132 2681180ca6a423b8)  web/static/pm_desk.css                   [items 2,4,5]
90e7357b62e6d87c  (cafb132 769044d17363e73c)  web/templates/pm_live_subdivision.html   [item 2]
d5a29a20d5407781  (cafb132 c3ddce77a5fb4f9c)  web/templates/pm_shell.html              [item 1; NEW to the shipped set]
```
`git diff cafb132 HEAD` over app.py/main.py is EMPTY -> app.py did NOT change, so the M4/M5 graft rule does NOT
apply. Test-only (not shipped): `test_live_card_states_predeploy.py`. Note pm_desk.css's cache-bust `?v=1b3b8ccc`
is the first 8 of its own sha16 `1b3b8ccc…` -- self-consistent.

## Nothing was deployed or restarted
Build + test only; all box access this pass was read-only (diagnosis fetch + local headless renders). The engine
(trading-corp PID 163519, ARMED) was not touched; no pm_web restart; no schema/venv/systemd change. Committed on
the branch at `431ec76`; prod-live/main-wip NOT advanced, branch NOT pushed (box-is-truth). Render PNGs in
`cc/renders/` (before_*/after_* + per-card crops); render harness `cc/pm_render.py`, toggle diag
`cc/pm_toggle_diag.sh` (read-only).

---

# DEPLOY 3 -- 2026-09-02/03 (DEPLOYED LIVE; pm_web-only + ONE pm_web restart; engine never touched)

Board-authorized deploy of the CARD POLISH pass. **pm_web-ONLY, wholesale copy of 5 files (no graft:
app.py/main.py unchanged vs `cafb132`), one pm_web restart via `az vm run-command`. The engine (trading-corp PID
163519, ARMED, trading two accounts unattended) was never restarted, reloaded, or touched -- PID 163519 /
NRestarts 0 at every step.** Deploy target = branch `pm-ui-rewrite-2026-09-02` @ **`431ec76`** (report doc commit
on top, not deployed).

## Source & scope
`git diff --name-only cafb132 431ec76` over `web/` = exactly the 5 expected files (feed_mlb.py, live_view.py,
pm_desk.css, pm_live_subdivision.html, pm_shell.html); **app.py and main.py are unchanged vs `cafb132`** (empty
diff) -> no graft. Engine files on the branch were NOT shipped.

## Pre-deploy checks (all PASS -- `pm_deploy3_checks.sh`, 2026-09-02T23:01:37Z)
1. Engine PID **163519**, NRestarts **0**, active; pm_web **167940**, active; schema **17**. Arm rows all
   armed=True with ts (`arm:global` 08-31T02:35:38Z, `arm:kalshi_jack:mlb` 08-31T21:49:39Z, `arm:kalshi_karen:mlb`
   09-02T12:53:23Z).
2. Each of the 5 box files, CR-stripped, EQUALS its `cafb132` sha -- **box == last deploy, zero drift** (5/5 OK).
3. Box app.py M4 markers: `is_admin` **=10**, `/pm/arm` **=0**.
4. Backup -> **`/home/azureuser/pm_deploy3_backup_20260902T230137Z`** (5 files). Journal order counts (dry_run=0):
   kalshi_jack **104**, kalshi_karen **11** (grew from DEPLOY 2's 93/6 -- the engine's normal trading over the day).
5. BEFORE served `/static/pm_desk.css` CR-stripped sha16 = **`2681180ca6a423b8`** (the `cafb132` file); the shell
   link carried **no `?v=`** yet.

## Deploy (`pm_deploy3_apply.sh`, 23:02:04Z) -- 5 files wholesale, sha-verified on the box == `431ec76`
```
467d528460421a31  web/feed_mlb.py                          (box was 7f05607bb887bb51)
2c7c8875cd80e768  web/live_view.py                         (box was 6d3b7b3300782d3e)
1b3b8ccc6dff50cc  web/static/pm_desk.css                   (box was 2681180ca6a423b8)
90e7357b62e6d87c  web/templates/pm_live_subdivision.html   (box was 769044d17363e73c)
d5a29a20d5407781  web/templates/pm_shell.html              (box was c3ddce77a5fb4f9c)
```
Result: **ALL 5 OK**.

## Restart (pm_web ONLY, via az run-command, 23:03Z)
`systemctl restart prediction-markets-web` (root, via `az vm run-command` -- ssh+sudo has no TTY on this box).
pm_web **167940 -> 170400** (active, running); **engine trading-corp PID 163519 UNCHANGED, NRestarts 0**
immediately after. No package/venv/unit-file change.

## Post-deploy verification (`pm_deploy3_verify.sh`, after a 90s poll cycle, 23:03Z) -- checks 9-19 all PASS
- **[9]** the 7 listed pages 200; and **all 15 `/farm/{category}` that exist return 200** (atp, cs2, epl, fed,
  golf, mlb, nba, nfl, nhl, soccer, tennis, ucl, ufc, wnba, wta -- non-200 count 0).
- **[10]** every static asset link carries `?v=` (or is an unversioned page-level script) and each resolves
  **200**: `pm.css?v=204d9051`, `pm_desk.css?v=1b3b8ccc`, `htmx.min.js?v=491955cd`, `pm_live.js`, `pm_sort.js`.
  **No 404** -> no rollback condition.
- **[11]** the versioned CSS served at `/static/pm_desk.css?v=1b3b8ccc` = **`1b3b8ccc6dff50cc`** (== target,
  DIFFERS from step 5's `2681180ca6a423b8`); the live page's pm_desk link now carries `?v=1b3b8ccc`; `.tgl`
  anchors inside `.toggle` = **2**. The cache-bust is live -- a fresh browser now fetches the new CSS.
- **[12]** card borders by state (live pages): st-live and st-pre cards both render their border; **0 cards with
  no border** (every current game has a feed). The live slate had no final/mixed/feed-unavailable card at check
  time -- those states' borders are proven by test + the pre-deploy render; the live+preview borders are
  confirmed on the actual prod render (`cc/renders/prod_live_jack_1600.png`).
- **[13]** cards at an inning break (MID/END) during the window: **0** -> NOT claimed verified here (the behavior
  is proven by `test_feed_parse_inning_break_clears_count` + the pre-deploy render `after_ATH.png`).
- **[14]** date chip short (no weekday) on every card, both live pages: 3 chips each, **0 with a weekday**,
  sample `Sep 2 · 6:40 PM ET`; confirmed fitting at **1280px** on the actual prod render
  (`cc/renders/prod_live_jack_1280.png`) as well as 1600px.
- **[15]** coverage label present on all three surfaces (root/account/live); marks ok (320 markets);
  **kalshi_jack 3 of 3 priced, kalshi_karen 3 of 3 priced -- no unpriced tickers** (open counts dropped from
  8/6 as the day's games settled).
- **[16]** `GLOBAL ARMED` on `/`; effective `ARMED` on both live pages; **0 DISARMED/STATE UNAVAILABLE; 0 NEVER
  ARMED anywhere**.
- **[17]** Farm League: `/farm/mlb` Analyze occurrences **30**, loss-omission markers **87**, pm.css linked; and
  the shared shell change is confirmed **visually not just by 200** -- the actual prod `/farm/mlb` renders fully
  styled (`cc/renders/prod_farm_mlb_1600.png`): nav, tables, colored win%, Analyze controls, loss-omission%.
- **[18]** engine PID **163519** unchanged, NRestarts **0**, ERROR entries since restart **0**; journal counts
  UNCHANGED (kalshi_jack 104, kalshi_karen 11) -- pm_web placed nothing.
- **[19]** double-escaped entities `&amp;middot;`/`&amp;mdash;`/`&amp;dagger;`/`&amp;ndash;` = **0** each.

## Visual confirmation (actual prod bytes)
The served prod HTML (`/live/kalshi_jack/mlb`, `/farm/mlb`) + served CSS were fetched read-only and rendered
headless at 1600px and 1280px (`cc/renders/prod_live_jack_*`, `prod_farm_mlb_*`): the toggle is a bordered
segmented control (item 1 cache-bust live), LIVE cards carry the blue border and PREVIEW the subtle not-started
border (item 2), the date reads `Sep 2 · …` right-aligned (item 4), the diamond lines and scoreboard digits are
clearly legible (item 5), and Farm is fully styled (item 17).

## Rollback
Not needed -- all of 9-18 passed and no step-10 404. Backup retained at
`/home/azureuser/pm_deploy3_backup_20260902T230137Z` (`pm_deploy3_rollback.sh` restores + an az pm_web restart;
DANGEROUS if restored later -- reverts DEPLOY 3).

## What was NOT done / notes
- Engine NEVER restarted or touched (163519 / NRestarts 0 at every step). No schema/venv/systemd change; no new
  box dependency. app.py unchanged (no graft).
- Item 1's fix is the static-asset cache-bust (`?v=` content hash in pm_shell.html) -- a browser that had the old
  `pm_desk.css` cached will now re-fetch because the URL changed. Existing tabs open before the deploy may still
  show the old CSS until reloaded; a normal navigation/refresh picks up the versioned URL.
- prod-live / main-wip NOT advanced; branch NOT pushed (box-is-truth). Deployed code = `431ec76`.
- Runners in `cc/`: `pm_deploy3_checks.sh`, `pm_deploy3_apply.sh` (+ `gen_deploy3.py`), `pm_deploy3_restart_az.ps1`,
  `pm_deploy3_verify.sh`, `pm_deploy3_rollback.sh`; prod render `cc/pm_prodshot.py` from `cc/prod/` (fetched bytes).

---

# MULTI-CATEGORY FIX -- 2026-09-04 (build + test + verify; NOTHING DEPLOYED, NOTHING RESTARTED)

Board-authorized 2026-09-04. Eight live sub-divisions exist (Jack & Karen x ATP/MLB/UFC/WTA). The `/live/{acct}/{cat}`
page rendered a **headline that contradicted its own drawer** on every non-MLB sub-division: the summary strip said
0 games / $0.00 at-cost / 0 positions while the trade drawer listed a real open trade (Jack's ATP KXATPMATCH... 5
contracts, fill $0.10). This pass makes the totals category-agnostic, gives non-MLB categories an honest positions
view, covers every held series in the mark poller, and keeps MLB byte-for-byte identical. Branch
`pm-ui-rewrite-2026-09-02` (worktree `cc-pm-ui-rewrite-wt`); base = deployed `431ec76`.

## Root cause (Jack's hypothesis -- CONFIRMED with box evidence)
`live_view.build_live_context` grouped every held ticker by MLB **game** via `game_key_from_ticker(tk)`, which parses
`KXMLB{GAME,TOTAL,SPREAD}-<date><hhmm><AWAYHOME>-...` against `MLB_TEAMS` and returns **None** for any non-MLB ticker
(`KXATPMATCH`, `KXUFCFIGHT`, `KXWTAMATCH`). The summary strip (at-cost / count / value / realized) was then computed
**from the game cards**, so a non-MLB sub-division produced **no cards -> all-zero strip**. But `_trade_rows` iterates
**every** order regardless of ticker, so the drawer still showed the trade. Net: the sport parser silently gated the
headline totals. The account pages were already correct because they read the journal directly
(`subdivision.live_positions` / `account_pnl`) -- which is why, in the prod probe below, `kalshi_jack`'s **account**
page counts the ATP position ($3.65 = atp $0.50 + mlb $3.15) while its **atp live page** shows $0.00. That split is the
fingerprint of the defect: journal path correct, sport-parser path broken.

## Prod discrepancy table (READ-ONLY box probe `cc/pm_multicat_prod_defect_ro.{ps1,sh}`, deployed code `431ec76`)
Page strip (rendered by the **currently deployed** pm_web, PID 182842) vs mode=ro journal truth
(`subdivision.live_positions` run on the box). All page GETs 200; Remote-User=jack; nothing written.

| account | cat | JOURNAL (mode=ro) | PAGE strip (deployed) | verdict |
|---|---|---|---|---|
| kalshi_jack | atp | open=1 cost=$0.50 | Games=0 cost=$0.00 postbl=N | **DEFECT -- page hides the position** |
| kalshi_jack | mlb | open=1 cost=$3.15 | Games=1 cost=$3.15 postbl=N | agrees (MLB path works) |
| kalshi_jack | ufc | open=0 cost=$0.00 | Games=0 cost=$0.00 | no open pos now (would defect if held) |
| kalshi_jack | wta | open=0 cost=$0.00 | Games=0 cost=$0.00 | no open pos now |
| kalshi_karen | atp | open=0 cost=$0.00 | Games=0 cost=$0.00 | no open pos now |
| kalshi_karen | mlb | open=1 cost=$3.15 | Games=1 cost=$3.15 | agrees (MLB path works) |
| kalshi_karen | ufc | open=0 cost=$0.00 | Games=0 cost=$0.00 | no open pos now |
| kalshi_karen | wta | open=0 cost=$0.00 | Games=0 cost=$0.00 | no open pos now |

Account pages (deployed): `kalshi_jack` journal n_open=2 open_cost=$3.65, page links 4/4 cats; `kalshi_karen` n_open=1
open_cost=$3.15, page links 4/4 cats. **The one live non-MLB position anywhere on the box (jack/atp) is exactly the
one the page hid** -- the reported defect, reproduced from the box.

## The fix, item by item (all in the worktree; evidence = the tests + the worktree render harness)

**1. Totals never depend on the sport parser.** `build_live_context(..., category=None)` now branches on category.
For a **non-MLB** category the summary comes from `_journal_summary(open_positions, orders, marks, now_ts)`: at-cost =
sum of `cost_basis_usd` over `live_positions`; unsettled count/value/coverage via the existing category-agnostic
`value_positions`; realized-today + settled-today from settlement-close journal rows keyed on the close timestamp. The
"games held" cell reads the honest alternative ("No game feed for <CAT> -- N open positions") from `has_game_feed`
False. Evidence: `test_non_mlb_totals_from_journal_not_parser` (n_open=1, at-cost $0.50, value 5x0.12, realized $3.00);
render harness shows all 8 sub-divisions strip==journal.

**2. Non-MLB pages get an honest positions view.** New `_positions_view(...)` builds an Active (open) / Complete
(settled/exit/opposed) table; each row carries ticker, market description (the Kalshi **title** from the mark when
present, else `market_describe`'s `<type>:<ticker>` fallback), side, contracts, cost basis, current value at
contracts x BID or "no mark", status, and the copied whale(s). Template renders `postbl` table for `mode=='positions'`,
no court/octagon cards (later Jack decision). Evidence: `test_non_mlb_positions_view_rows`,
`test_non_mlb_desc_falls_back_to_market_describe_without_title`; PNG `cc/renders/multicat_atp.png` (Zverev vs Halys /
YES / 5 / $0.50 / $0.60 / OPEN / 0xw).

**3. Mark poller covers every held series.** `marks.series_from_tickers(tickers)` + `subdivision.traded_series(conn)`
(distinct Kalshi series prefixes from every **held** ticker across all active sub-divisions, both accounts) feed a new
`poller.refresh_once(..., series_provider=)` / `poll_loop(..., series_provider=)`; `app._held_series_provider` wires it
in (fail-safe: any DB blip or empty result -> the MLB default so a cold start still primes the slate). `marks.Mark`
gained a `title` field (populated in `parse_markets` from the Kalshi `title`), which the positions view uses.
Kalshi public market-data endpoint returns ATP/UFC/WTA identically to MLB -- **confirmed live from the box**: the same
`api.elections.kalshi.com/.../markets?series_ticker=KXATPMATCH` path already backs the tennis matcher and the first
ATP fill (order 160) verification. Evidence: `test_series_from_tickers_distinct_prefixes`,
`test_parse_markets_carries_title`, `test_traded_series_covers_all_held_categories` (mlb+atp+ufc across 2 accounts ->
`(KXATPMATCH, KXMLBGAME, KXUFCFIGHT)`), `test_poller_threads_series_provider_to_fetch_marks` + the two fail-safe tests.
Coverage label "N of M priced" now renders on every category (visible on the ATP PNG: "1 of 1 priced").

**4. Feed-unavailable never means "game over" on a category with no feed.** The non-MLB branch renders no diamond, no
inning/count, no "no count / game over", no baseball legend -- only the positions table + a positions legend. Guarded by
`test_atp_page_renders_positions_view_not_empty_cards` (asserts none of "game over", "runner on base", "no count",
"FEED<br>UNAVAILABLE" appear on the ATP page) and visible in the PNG.

**5. Account page + accounts overview.** Verified already category-agnostic: `account_pnl` iterates every active
category; `_account_open_value` sums `live_positions` across all sub-divisions; sub-division rows list all four
categories. The only prior gap was non-MLB positions had no marks -> now fixed by item 3. No app.py change needed here
beyond item 3's poller wiring. Evidence: render harness ACCOUNT rows (n_open=4, all-4-cats-linked=True) + the prod
probe (deployed account page already linked 4/4 and summed atp+mlb).

**6. MLB unchanged.** The MLB branch keeps the **exact** original card-based summary computation (restored verbatim,
incl. `realized_today`/`settled_today` keyed on the card's game date, so a cross-midnight settlement behaves as before
-- deliberately NOT switched to the journal keying used for non-MLB). Two additive keys (`has_game_feed`,
`n_open_positions`) are appended for the shared template; they do not change any displayed MLB value. Locked by
`test_mlb_summary_strip_values_locked` (exact strip numbers) and `test_mlb_context_byte_identical_with_and_without_category`
(the whole context is identical apart from the echoed `category` field). PNG `cc/renders/multicat_mlb.png` shows the
unchanged game-card view (diamond, ML/TOT/SPR slots, full baseball legend).

## Verification
- **Full `tests/prediction_markets/`** (`.venv-webtest`, `-p no:pytest_ethereum`): **16 failed, all pre-existing
  env-gap** (15 pykalshi `ModuleNotFoundError` in engine-driver tests: `test_kill_switch_r7d` x4, `test_live_driver_r7c`
  x7, `test_shard_gate_r2` x4; + `test_search_r1::test_schema_head_is_15`). **Baseline established authoritatively by
  running the committed base with my changes stashed -> 16 failed, byte-identical failure set (`diff` empty).** So my
  delta = **0 new failures**. (The handoff quoted "19"; the true baseline in this worktree/venv is 16 -- this number has
  been measurement-unstable across sessions per the memory, so I measured it here rather than trusting the quote.)
- **New tests: 16 passed** -- `test_live_view_multicategory.py` (12) + `test_live_multicategory_render.py` (4, incl. the
  `test_asset_cache_bust` guard, which passes -> the pm_desk.css hash bump is correct: `1b3b8ccc` -> `825861cd`).
- **Worktree render harness** `cc/pm_multicat_render.py`: all 8 sub-divisions strip==journal (open=1 / at-cost $0.50 /
  value $0.60 each), both account pages sum all 4 categories -> **ALL PASS**. Screens `cc/renders/multicat_atp.png`
  (non-MLB positions view) and `cc/renders/multicat_mlb.png` (MLB cards unchanged).
- **Box read-only** `cc/pm_multicat_prod_defect_ro.{ps1,sh}`: the prod discrepancy table above (documents the deployed
  defect); worktree render agrees with the same journal truth.
- **Nothing deployed / restarted** (`cc/pm_untouched_check_ro.sh`): engine `trading-corp` MainPID **186179**
  NRestarts=0 (the same PID the fill watcher saw at session start); pm_web MainPID **182842** NRestarts=0. The box still
  serves pre-fix code -- which is precisely why the defect probe reproduces the old behavior.

## Shippable file list vs deployed `431ec76` (NOT deployed -- for the eventual Board deploy)
8 files, +342/-62: `web/live_view.py` (category branch + positions view + journal summary),
`web/marks.py` (`title` field + `series_from_tickers`), `web/poller.py` (`series_provider`),
`prediction_markets/subdivision.py` (`held_tickers` + `traded_series`, additive), `web/app.py`
(**changed -- graft rule applies**), `web/static/pm_desk.css` (positions-table styles), `web/templates/pm_live_subdivision.html`
(cards-vs-table branch), `web/templates/pm_shell.html` (cache-bust hash bump only). Plus 2 new test files.
**app.py CHANGED**: three purely-additive hunks (a `_held_series_provider` fn, the `series_provider=` poller wiring, the
`category=category` pass) -- none touch the M5 is_admin/pm-arm hunk. **Graft rule STANDS**: at deploy time app.py is
GRAFTED onto the box M4 file (box `8b7d35ca` is_admin=10/pm-arm=0), never shipped wholesale; main.py is not shipped from
this branch. Other 7 files are wholesale-safe (no app.py/main.py collision).

## Runners (cc/)
`pm_multicat_render.py` (worktree render harness + strip==journal + PNGs), `pm_multicat_prod_defect_ro.{ps1,sh}` (box
prod-defect probe), `pm_untouched_check_ro.sh` (engine/pm_web untouched check).

---

# DEPLOY 4 -- 2026-09-04 (multi-category fix -> prod; pm_web-only, ONE pm_web restart, engine untouched)

Board-authorized 2026-09-04. Shipped the multi-category fix to prod: the 7 non-app pm_web files wholesale + app.py
GRAFTED (box M4 + 3 additive hunks), one pm_web restart via `az vm run-command`. Deploy target = branch commit
`86744ac`. The engine (`trading-corp`, PID 186179, 8 armed sub-divisions across two accounts) was NOT touched,
restarted, or reloaded at any step. All STOP gates passed; no rollback. **DEPLOYED.**

## Source + measurement
- `git diff --name-only 431ec76 86744ac -- .../main.py` empty -> **main.py UNCHANGED** (no STOP). 8 shippable pm_web
  files (7 non-app + app.py); tests/reports do not ship; engine files not on the shipping list.
- All box-vs-git hashes CR-stripped **both** sides (`tr -d '\r' | sha256sum | cut -c1-16`).

## Pre-deploy checks (`cc/pm_deploy4_precheck_ro.sh`, `cc/pm_deploy4_arm_ro.sh`)
1. **Services/schema/arm recorded.** Engine `trading-corp` MainPID **186179** NRestarts=0 active (since 2026-09-04
   03:57:20Z). pm_web MainPID **182842** NRestarts=0 active. pm_db schema **19**. Arm (authoritative via
   `arm.read_status`): GLOBAL armed=True (ts 2026-08-31T02:35:38); all 8 sub-divisions armed=True latched=False
   (jack/atp 09-04T04:31:42, jack/mlb 08-31T21:49:39, jack/ufc 09-04T04:29:59, jack/wta 09-04T04:31:43, karen/atp
   09-04T04:31:43, karen/mlb 09-02T12:53:23, karen/ufc 09-04T04:29:59, karen/wta 09-04T04:31:43).
2. **Every overwrite target matched its baseline (no third-party box change).** 7 non-app files CR-stripped ==
   431ec76 (subdivision `dbc710c7`, live_view `2c7c8875`, marks `46ca99a8`, poller `44a6b51d`, pm_desk.css `1b3b8ccc`,
   pm_live_subdivision `90e7357b`, pm_shell `d5a29a20`). Box app.py CR-stripped16 = **`8b7d35ca88432603`** == M4 target
   `8b7d35ca`, **is_admin=10, /pm/arm=0** (i.e. the box is M4, NOT the branch M5 `2a1c341d` which carries is_admin=12
   -- confirming the branch app.py must NOT be shipped wholesale).
3. **Backup** = `/home/azureuser/pm_deploy4_backup_20260904T112138Z` (all 8 files, "before" shas recorded there).
   **Journal baseline** (mode=ro): jack/mlb open=1 $3.15, karen/mlb open=1 $3.15, other six sub-divisions open=0 $0.00;
   total open=2 $6.30. (The jack/atp Halys position that reproduced the defect earlier had settled at 06:28Z, order 162,
   so no non-MLB open positions remained at deploy time.)
4. **Cache-bust before:** served `/static/pm_desk.css` CR-stripped16 = `1b3b8ccc6dff50cc`, shell `?v=1b3b8ccc`.

## Deploy (`cc/gen_deploy4.py` -> `cc/pm_deploy4_apply.sh`; app graft `cc/deploy4_box_app.py`)
5. **7 non-app files** written (base64 -> temp -> CR-strip -> sha16 gate -> mv), each CR-stripped16 == 86744ac:
   subdivision `863af1d1522fb364`, live_view `d3cfbeb9549a36b5`, marks `8cace4e71d8140a0`, poller `d9f9f4f518b29869`,
   pm_desk.css `825861cd1f6c6b0d`, pm_live_subdivision `b2cf33e2a7289dc1`, pm_shell `9253801d48466156`. APPLY FAIL=0.
6. **app.py GRAFTED, never wholesale.** Fetched the box M4 app.py (verified `8b7d35ca88432603`), applied the 3 additive
   hunks locally (`_held_series_provider` fn; `poll_loop(..., series_provider=_held_series_provider)`; `build_from_cache(
   ..., category=category)`). Cross-check: `diff` grafted vs branch app.py shows the ONLY delta is the M5 is_admin/pm-arm
   change (a comment + `"is_admin": is_admin_flag,`) -- i.e. the graft = branch minus M5 = box M4 + my 3 hunks, exactly.
   Grafted written to box; new box app.py reference CR-stripped16 = **`c2e4ddef85b4460b`**. Post-write invariants (all
   four shown): **sha `c2e4ddef85b4460b`, is_admin=10, /pm/arm=0, py_compile OK (all 8), app imports with ZERO engine
   imports** (`pykalshi`/`live_driver`/`execution`/`brokers` absent from sys.modules; `_held_series_provider` present).
7. No package/venv/unit changes.

## Restart (`cc/pm_deploy4_restart_az.ps1`)
8. `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript` restarting
   `prediction-markets-web` ONLY. pm_web **182842 -> 190041** active/running. **Engine 186179 -> 186179 (unchanged),
   NRestarts=0** immediately after. Enable succeeded, exit 0.

## Post-deploy verification (`cc/pm_deploy4_postcheck_ro.sh`; ran after a full poll cycle)
9.  All 8 live pages + both account pages + `/` + `/farm` + every `/farm/{category}` return **200** (`/farm/cs` 404 =
    pre-existing, my deploy touched zero farm code -- 'cs' is a non-tile tag, not a live-copyable category).
10. **strip == journal (at-cost + count), all 8:**

    | account | cat | http | mode | JOURNAL open/cost | PAGE hdr/count / at-cost / coverage | verdict |
    |---|---|---|---|---|---|---|
    | kalshi_jack | atp | 200 | positions | 0 / $0.00 | Positions=0 / $0.00 / n/a | OK |
    | kalshi_jack | mlb | 200 | cards | 1 / $3.15 | Games=1 / $3.15 / 1 of 1 priced | OK |
    | kalshi_jack | ufc | 200 | positions | 0 / $0.00 | Positions=0 / $0.00 / n/a | OK |
    | kalshi_jack | wta | 200 | positions | 0 / $0.00 | Positions=0 / $0.00 / n/a | OK |
    | kalshi_karen | atp | 200 | positions | 0 / $0.00 | Positions=0 / $0.00 / n/a | OK |
    | kalshi_karen | mlb | 200 | cards | 1 / $3.15 | Games=1 / $3.15 / 1 of 1 priced | OK |
    | kalshi_karen | ufc | 200 | positions | 0 / $0.00 | Positions=0 / $0.00 / n/a | OK |
    | kalshi_karen | wta | 200 | positions | 0 / $0.00 | Positions=0 / $0.00 / n/a | OK |

    No open positions had a delta vs the step-3 baseline (nothing filled in the window), so no order-id attribution
    needed.
11. **MLB unchanged:** both MLB pages render the card grid (diamond, bet slots, ML/TOT/SPR, state border, date chip,
    coverage label, baseball legend) -- "card grid intact".
12. **Non-MLB:** all 6 render the positions view, "No game feed for <CAT>", and NONE of `class="dia"` / "runner on
    base" / "no count" / "game over" / "FEED UNAVAILABLE". With 0 open non-MLB positions the Active tab honestly reads
    "No open <CAT> positions" (the value-never-$0 rule is vacuously satisfied). **12b -- real non-MLB data on prod:**
    `/live/kalshi_jack/atp?tab=complete` renders the settled Halys position in the `postbl` table with a **LOST** badge
    -- the new positions view proven end-to-end on live prod with a real settled position.
13. **Poller polls the held series:** `traded_series` = `('KXMLBGAME',)` (only KXMLBGAME is currently held; both MLB
    positions are moneyline). Coverage: both MLB "1 of 1 priced"; non-MLB "n/a" (0 open). No held ticker is unpriced
    after the poll cycle. (This is the intended item-3 behavior: the series list is derived from held tickers, so it
    will include KXATPMATCH/KXUFCFIGHT/KXWTAMATCH the moment such a position opens -- and never a hardcoded MLB list.)
14. **Account pages:** both link 4/4 categories; aggregate (n_open=1, $3.15 each) equals the sum of the sub-division
    rows.
15. **Arm:** GLOBAL ARMED; all 8 sub-divisions `is_armed=True`, page shows ARMED, "NEVER ARMED" nowhere (arm state is
    read each cycle from the legacy DB -- untouched by the pm_web restart).
16. **Cache-bust:** served `/static/pm_desk.css` CR-stripped16 = **`825861cd1f6c6b0d`** (changed from `1b3b8ccc`,
    == 86744ac), shell `?v=825861cd`; pm.css / pm_desk.css / htmx.min.js all 200.
17. **Farm** pages unchanged and styled (all real categories 200 with the stylesheet linked).
18. **Engine untouched:** MainPID **186179**, NRestarts **0**; **`journalctl -u trading-corp -p err --since <restart>` =
    "No entries"** (zero engine error entries since the restart). Order counts unchanged (no fills in the window).
19. **Zero double-escaped entities** across `/`, the account page, an MLB page and an ATP page.

## File list -- before/after CR-stripped sha16
| file | box BEFORE | AFTER (on box) |
|---|---|---|
| prediction_markets/subdivision.py | dbc710c79eee1b7c | 863af1d1522fb364 |
| web/app.py (GRAFTED, not wholesale) | 8b7d35ca88432603 (M4) | **c2e4ddef85b4460b** (M4 + 3 hunks) |
| web/live_view.py | 2c7c8875cd80e768 | d3cfbeb9549a36b5 |
| web/marks.py | 46ca99a80f7d2827 | 8cace4e71d8140a0 |
| web/poller.py | 44a6b51da0ad36dd | d9f9f4f518b29869 |
| web/static/pm_desk.css | 1b3b8ccc6dff50cc | 825861cd1f6c6b0d |
| web/templates/pm_live_subdivision.html | 90e7357b62e6d87c | b2cf33e2a7289dc1 |
| web/templates/pm_shell.html | d5a29a20d5407781 | 9253801d48466156 |

- **Grafted app.py sha `c2e4ddef85b4460b` is the NEW box app.py reference** (supersedes M4 `8b7d35ca` for the next deploy;
  it is `8b7d35ca` + the 3 additive multi-category hunks, still is_admin=10 / /pm/arm=0).
- **pm_web PID: 182842 (before) -> 190041 (after).** Engine PID **186179 never changed; NRestarts 0** throughout.
- **Backup:** `/home/azureuser/pm_deploy4_backup_20260904T112138Z` (rollback = restore + `systemctl restart
  prediction-markets-web` only; engine never touched).

## Skipped / notes
- Ship of the branch app.py wholesale: deliberately NOT done (would regress the box M4 authz to branch M5). Grafted
  instead, per the graft rule.
- No prod PNG captured this pass: the deployed code is byte-identical to the fix-task render harness (`cc/renders/
  multicat_atp.png` / `multicat_mlb.png`), and check 12b confirms the real settled position renders on prod.
- The multi-category defect is not currently *visible* on prod as a live open non-MLB position (none open right now);
  the fix is proven by the header/view change on all 6 non-MLB pages + the settled Halys position on the Complete tab.
- `/farm/cs` 404 is pre-existing (no farm code shipped).

## Runners (cc/)
`pm_deploy4_precheck_ro.sh`, `pm_deploy4_arm_ro.sh`, `pm_deploy4_backup.sh`, `gen_deploy4.py` -> `pm_deploy4_apply.sh`,
`deploy4_box_app.py` (grafted), `pm_deploy4_prerestart_ro.sh`, `pm_deploy4_restart_az.ps1`, `pm_deploy4_postcheck_ro.sh`,
`pm_verify_close162_ro.sh`. Rollback: restore the backup dir + restart pm_web only.

---

# BET-SLOT PASS -- 2026-09-04 (widened slots + copied-from whale on each held slot; BUILT, NOT DEPLOYED)

Board-authorized 2026-09-04. Two UI items on the MLB card + the non-MLB positions table, then wrap. Branch
`pm-ui-rewrite-2026-09-02` from `430e8f7` (deployed code `86744ac`, box app.py ref `c2e4ddef85b4460b`). No deploy, no
restart -- the engine (8 armed sub-divisions) was not touched. **app.py NOT changed this pass.**

## 1. Widen the bet slots (item 1)
The three fixed slots (ML/TOT/SPR) were a 190px block, narrower than the space below the diamond. Widened to **208px**
(`.bets width:190px->208px`) so they fill the home-plate area (the diawrap footprint, ~212px) while staying seated over
home plate -- they overflow the 192px diamond symmetrically within the 212px diawrap, so they never collide with the
meta (inning/count) column to the right. All three slots keep identical shape on every card; unheld slots stay dimmed.
The diamond's bottom margin was raised `78px->108px` to make room for the now-two-row slots (below), applied uniformly
so **card height is constant across cards** (verified at 1600px and 1280px).

## 2. Copied-from whale on each held slot (item 2 -- Jack's reversal of the drawer-only ruling)
Each HELD slot now shows the whale it was copied from, on a reserved second row inside the slot:
- **Data (journal-sourced, never inferred):** `_build_slot` takes the ticker's net-open holders from
  `open_positions_by_whale` (== `subdivision.live_positions_by_whale`, the same journal the drawer reads) via
  `whales_by_ticker`; `_whale_tag(whales)` returns `{first, extra, all}` -- the FIRST label (display name, else
  wallet) + the count of ADDITIONAL whales. Settled/unheld slots get `whale_tag=None` (no whale). The `whale_tag` also
  rides on the non-MLB positions-view rows.
- **Render (CSS truncation, so the slot never changes shape):** the template `whaletag` macro emits
  `<span class="wtag"><span class="wn">{first}</span><span class="wx">+N</span></span>`; `.wn` right-truncates with an
  ellipsis at a fixed `max-width:150px` (bet slot) / `180px` (table cell -- a `td`'s max-width is ignored under
  auto table-layout, so the cap lives on the tag), while `.wx` (`+N`) is `flex:none` and stays visible. Every slot
  reserves the whale row (`&nbsp;` when empty) so held/unheld slots are the same height. The full untruncated list is
  the tag's hover `title` and remains in the trade drawer.
- **Scope:** the SAME tag/truncation is applied to the non-MLB positions table's whale column (was `whales|join`).

## Evidence
- **Render harness** `cc/pm_betslot_render.py` -> `cc/renders/betslot_v3_{mlb,atp}_{1600,1280}.png` (VIEWED). MLB card
  cases all present and correct: 1 held slot (Game A: `FROM SuperLongWhaleHandle2026 +1` -- long name + extra both
  shown), 2 held slots (Game B: ML `FROM domer +1`; TOT wallet-only `FROM 0x64e93f87d8a0c1b2cde6f20d71f2113...`
  truncated with ellipsis), 3 held slots (Game C: ML/TOT/SPR each `FROM <whale>`), a settled slot (Game D PHI won ->
  NO whale), unheld slots (reserved empty whale row, equal height, card height constant). ATP positions table: whale
  column shows `Kingfish +1` and the truncated wallet. Both widths render 3-cards-per-row with consistent height.
- **Unit tests** `tests/prediction_markets/test_bet_slot_whales.py` (14): `_whale_tag` single / two `+1` / three `+2`
  / wallet-only / empty->None; `_build_slot` open-single / open-two / open-wallet-only / unheld->None /
  settled->no-whale; end-to-end `build_live_context` slot gets the whale from `open_positions_by_whale` (named + multi
  + wallet-only); non-MLB positions row carries the tag.
- **Cache-bust:** pm_desk.css changed -> shell `?v=825861cd -> a40ab798`; `test_asset_cache_bust_hashes_match_files`
  green (in the 4-file pass below).
- **Full `tests/prediction_markets/`** (`.venv-webtest`, `-p no:pytest_ethereum`): **16 failed -- byte-identical to
  the DEPLOY-4 env-gap baseline (0 delta)**; all new tests + the card/cache-bust regression suites pass.

## Shippable file list vs deployed `86744ac` (BUILT, awaits a Board deploy)
4 files, +69/-21; **app.py NOT changed (no graft needed for this pass)**; all wholesale-safe:

| file | box (86744ac) | after (this pass) |
|---|---|---|
| web/live_view.py | d3cfbeb9549a36b5 | e3779ffb8d5838c1 |
| web/static/pm_desk.css | 825861cd1f6c6b0d | a40ab798dfd0468f |
| web/templates/pm_live_subdivision.html | b2cf33e2a7289dc1 | db9cb08c3b831b35 |
| web/templates/pm_shell.html | 9253801d48466156 | 3ff3178d9f1d3956 |

Plus `tests/prediction_markets/test_bet_slot_whales.py` (new; does not ship). Nothing deployed, nothing restarted;
engine `trading-corp` PID 186179 untouched throughout (read-only session -- no box writes at all this pass).

## Runners (cc/)
`pm_betslot_render.py` (render harness + the six whale cases, both widths).
