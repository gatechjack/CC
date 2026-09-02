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
