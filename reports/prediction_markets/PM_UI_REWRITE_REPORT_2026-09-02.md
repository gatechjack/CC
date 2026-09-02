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
