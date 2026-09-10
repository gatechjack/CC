# PM UI — HANDOFF for the next UI code agent

**STATUS: CURRENT — last updated 2026-09-10 (folds in DEPLOY 7: the Live Sub-divisions REDESIGN — Claude Design port
of GET /live). Prior: DEPLOY 6 (the tile page), DEPLOY 5 (settled-slot whale + bet-slot pass). This is the current
handoff; the filename keeps its original date so existing references resolve. ★ The pm-ui-rewrite branch is now a
STALE SUBSET of the box — the box has advanced via the
driver-liveness (heartbeat + account-page liveness panel), farm-search, remaining-categories, and DEPLOY-6 tile
deploys; repo reconciliation is DEFERRED, so graft every deploy onto BOX-CURRENT (drift map:
PM_TILES_PHASE1_INVENTORY_2026-09-07.md).**

Workstream: the Prediction Markets `pm_web` UI rewrite. This doc is the starting point for the next UI pass.
Branch `pm-ui-rewrite-2026-09-02` (pushed to origin), worktree `C:\Users\AA Incorporado\cc-pm-ui-rewrite-wt`.
Deployed code (live-copy UI) = **`8978a2c`** (DEPLOY 5, tag `pm-ui-deploy5-2026-09-04`). Full narrative:
`PM_UI_REWRITE_REPORT_2026-09-02.md` (PLAN / FIX PASS / DEPLOY 1-3 / CARD POLISH / MULTI-CATEGORY FIX / DEPLOY 4 /
BET-SLOT PASS / DEPLOY 5).
★ **DEPLOY 6 (2026-09-07): the Live Sub-divisions TILE page** (GET /live rebuilt) — branch `pm-tiles-2026-09-07`
(tag `pm-tiles-deploy6-2026-09-07`), reports `PM_TILES_PHASE2_BUILD_2026-09-07.md` (build+deploy) +
`PM_TILES_PHASE1_INVENTORY_2026-09-07.md` (inventory + box-drift map).

--------------------------------------------------------------------------------
## 1. WHAT IS LIVE ON PROD

Deployed code = **`8978a2c`** (DEPLOY 5, 2026-09-04). pm_web is a STANDALONE FastAPI+Jinja app: it imports only
`trading_corp.data.*` + the PM package + stdlib; NO engine/main/agents/brokers; holds NO Kalshi credentials; can
never place an order. SINGLE uvicorn worker (loopback :8081, behind Authelia which sets Remote-User). Restarts go
through `az vm run-command` (root); the engine `trading-corp` (ARMED, 8 sub-divisions across two accounts) is NEVER
touched.

Deploy history (all pm_web-only, engine never restarted):
  DEPLOY 1 `9c2eeb3` -> 2 `cafb132` -> 3 `431ec76` -> 4 `86744ac` (multi-category fix) -> 5 `8978a2c` (bet-slot +
  settled-slot whale) -> **6 (2026-09-07) the Live Sub-divisions TILE page** (branch pm-tiles-2026-09-07 @ df38514)
  -> **7 (2026-09-10) the Live Sub-divisions REDESIGN** (branch pm-tiles-redesign-2026-09-10 @ 5c047f4, tag
  pm-tiles-deploy7-2026-09-10; 8 pm_web files + 21 logos + app.py graft, ONE pm_web restart 235587->298063, engine
  292771 untouched; report PM_TILES_REDESIGN_BUILD_2026-09-10.md).
★ NOTE: between DEPLOY 5 and 6 the box ALSO took non-UI-rewrite-branch deploys (driver-liveness incl. the heartbeat
tables + the account-page liveness panel; farm-search; remaining-categories), so the box pm_web is AHEAD of the
pm-ui-rewrite branch. Always graft onto BOX-CURRENT; do not wholesale-copy from a branch.

Post-DEPLOY-5 box shipped-file set (the 4 files that changed 86744ac->8978a2c; box == these, CR-stripped sha16 @
`8978a2c`):
    8fb7db158e4a5af8  web/live_view.py
    18454d5690a316ed  web/static/pm_desk.css
    db9cb08c3b831b35  web/templates/pm_live_subdivision.html
    934c258ce953b18d  web/templates/pm_shell.html
DEPLOY 4's other files remain at their `86744ac` shas (subdivision.py 863af1d1, marks.py 8cace4e7, poller.py
d9f9f4f5, ...).
Older UI-rewrite files still live at earlier shas (feed_mlb.py 467d5284, arm.py 60f44720, ui_cache.py e116ee8a,
pm.css 204d9051, pm_live.js b4c557fc, htmx.min.js 491955cd, pm_trade_drawer.html 48d579db, etc.). Reconcile every
deploy the same way: `git show <sha>:file | tr -d '\r' | sha256sum` vs `tr -d '\r' < boxfile | sha256sum`.

**Two standing deploy rules (unchanged):**
  * **app.py is GRAFTED, never wholesale-copied.** ★ **Current box app.py CR-stripped sha16 = `16caedfe6a193737`**
    (DEPLOY 7, 2026-09-10), `grep -c is_admin` = **28** (the redesign's /live scoping raised it from 14),
    `grep -c /pm/arm` = **0** (M5 still never on prod). Lineage: M4 `8b7d35ca` -> DEPLOY-4 `c2e4ddef` (is_admin=10)
    -> farm-search + liveness = `069d7a25` -> DEPLOY-6 tile hunk = `eeac337d` (is_admin=14) -> DEPLOY-7 grafted the
    `_load_live_list` rebuild + `_load_live_subdivision` scoping + `/live/events` = **`16caedfe`**. NEVER ship a
    branch app.py wholesale (it carries M5). Graft your app.py hunks onto the box's current file; verify /pm/arm=0
    + a `16caedfe` base. (NOTE: `is_admin` is no longer a fixed count — DEPLOY-7's scoping legitimately raised it
    14->28; gate a future deploy on /pm/arm=0 + the base sha, NOT on a hardcoded is_admin count.)
  * **main.py NEVER ships from this branch** (it carries the engine's per-account driver wiring). UI deploys are
    pm_web-only. `git diff --name-only <deployed> <target> -- .../main.py` must be empty before any deploy.

--------------------------------------------------------------------------------
## 2. ARCHITECTURE (what was built)

* **Feed adapter** `web/feed_mlb.py` — StatsAPI primary, ESPN fallback (ESPN 403s a browser UA; use `curl/8.4.0`).
  Join key = (ET calendar date, doubleheader#, frozenset of canonical team NAMES) — never a raw abbr. Kalshi
  tickers encode ET; feed reports UTC -> converted to ET before keying. Fetch/parse failure -> ABSENT (card
  degrades to "feed unavailable"), never a wrong game. `match_in_slate` is tolerant (exact key, else lone game,
  else DH by game_no).

* **Mark poller** `web/poller.py` + `web/marks.py` + `web/ui_cache.py` — pm_web OWNS the marks (Kalshi's
  market-data endpoint is PUBLIC/unauth; read with stdlib, no creds/broker). One background task writes an
  in-process cache (`ui_cache`, single worker, no DB/schema) every ~60s; renders read the cache, never block on the
  network. Value = contracts x held-leg **BID**. **★ MULTI-CATEGORY (DEPLOY 4):** the poller no longer hardcodes
  the three MLB series — `poll_loop(..., series_provider=)` calls `app._held_series_provider` ->
  `subdivision.traded_series(conn)`, which derives the distinct Kalshi series from the tickers we CURRENTLY HOLD
  across all sub-divisions (both accounts). Fail-safe: any DB blip / empty result -> the MLB default (so a cold
  start still primes the slate). `marks.Mark` carries a `title` (the Kalshi market title) for the non-MLB rows.

* **live_view assembly** `web/live_view.py` — pure `build_live_context(orders, open_positions,
  open_positions_by_whale, slate, marks_result, now_ts, category=None)` (no DB/network; unit-testable). **★
  MULTI-CATEGORY (DEPLOY 4) — totals NEVER depend on the sport parser:**
    - **MLB** (`category=='mlb'` or detected from tickers) renders GAME CARDS with the EXACT original card-based
      summary (byte-identical; `realized_today`/`settled_today` keyed on the card's game date). Locked by
      `test_mlb_summary_strip_values_locked` + `test_mlb_context_byte_identical_with_and_without_category`.
    - **Non-MLB** (atp/ufc/wta/…) renders a POSITIONS TABLE (`_positions_view`) with a JOURNAL summary
      (`_journal_summary`): at-cost / count / value+coverage / realized-today all from `live_positions` /
      `value_positions`, never `game_key_from_ticker`. The "games held" cell reads "No game feed for <CAT> · N open
      positions". No diamond / inning / count / "game over" / baseball legend on a feed-less category.
  This fixed the DEPLOY-3 defect where a non-MLB sub-division showed 0 games / $0.00 while its drawer held a real
  trade (root cause: `game_key_from_ticker` returns None for non-MLB, so the card-derived strip went 0).

* **Bet slots + whale attribution** `_build_slot` / `_whale_tag` / `_entry_whales` — each MLB card has three fixed
  slots (ML/TOT/SPR). **★ BET-SLOT PASS + SETTLED-SLOT WHALE (DEPLOY 5, LIVE):** slots widened to 208px to fill the
  home-plate area (below the diamond), and each HELD slot -- **live OR settled** -- shows the whale it was copied from
  (first label + `+N` extras, right-truncated by CSS so the slot never changes shape). Source is journal-only:
  net-open holders (`open_positions_by_whale`) for a live slot, the ENTRY-fill copiers (`_entry_whales`) for a settled
  slot (whose net-open set is empty once closed). Every slot reserves the whale row so held/unheld slots keep
  identical height (card height constant). ONLY UNHELD slots show no whale. Same tag in the non-MLB positions table
  (active + settled rows). The full untruncated list stays in the trade drawer (and the tag's hover title).

* **Account pages** — `subdivision.account_pnl` iterates every active category; `_account_open_value` sums
  `live_positions` across all sub-divisions; rows list all four categories. Category-agnostic already; DEPLOY 4
  gave them non-MLB marks via the held-series poller.

* **Arm badge** — read-only from PERSISTED `agent_state` (legacy DB `data/trading_corp.db`, agent `pm_live`, keys
  `arm:global` / `arm:kalshi_<acct>:<cat>`). pm_web only DISPLAYS; the CLI is authoritative. Arming is restart-free
  (read each cycle) — a pm_web restart never changes arm state.

* **Shell + cache-busting** `web/templates/pm_shell.html` — loads `pm.css` then `pm_desk.css`. Static URLs carry
  `?v=<CR-stripped-content-sha8>`; `test_asset_cache_bust_hashes_match_files` fails CI if a CSS/JS change forgets to
  bump the shell hash. Current pm_desk.css shell tag = `?v=a40ab798` (branch; box serves `825861cd` until this pass
  deploys).

--------------------------------------------------------------------------------
## 3. HONESTY RULES (as implemented — preserve these)

No-mark -> `no mark` / `unavailable`, never $0 / never cost-as-value. Coverage label `N of M priced` UNCONDITIONAL
under every current-value figure (every category). Feed age bands (a final never goes stale). Arm states are
FOUR: ARMED / DISARMED / STATE UNAVAILABLE (indeterminate mode=ro read, never shown as disarm) / NEVER ARMED.
Opposed close = "— not booked" (engine books no realized there). Game states: pre-game "not started" (blank, not
0-0); postponed/suspended/delayed are their own amber states; final "game over"; inning break -> empty count pips +
cleared runners. Directional slots: TOTAL `+8.5/-8.5`, SPREAD `-1.5 TEAM/+1.5 OTHER`; settled slots keep the held
direction, never guessed. Retention 24h after game end OR last settlement. **Whale attribution now on the card
slots too** (Jack reversed the drawer-only ruling 2026-09-04) — journal-sourced, never inferred; full list in the
drawer.

--------------------------------------------------------------------------------
## 4. OPERATIONAL LESSONS

* **CR-strip BOTH sides before hashing** any box-vs-git comparison (box is LF; Windows checkout is CRLF). Unstripped
  hashes never match and every file looks drifted.
* **pm_web restarts via `az vm run-command`** (root): `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm
  --command-id RunShellScript --scripts 'systemctl restart prediction-markets-web'`. ssh+sudo has no TTY and
  fails-SAFE. NEVER restart/reload `trading-corp` (the engine).
* **Browser-cached CSS masquerades as a rendering bug** — always bump the `?v=` shell hash on a CSS/JS change and
  verify the served file's sha over HTTP + the page link's new `?v=`.
* **The box is NOT a git repo** — deploys are base64-embedded graft scripts streamed over ssh (`cc/gen_deploy*.py`,
  `cc/pm_deploy*_apply.sh`); write files as azureuser, restart as root via az. DEPLOY 4 pattern:
  `cc/pm_deploy4_precheck_ro.sh`, `cc/gen_deploy4.py` -> `cc/pm_deploy4_apply.sh` (temp -> CR-strip -> sha16 gate ->
  mv), `cc/deploy4_box_app.py` (the graft), `cc/pm_deploy4_restart_az.ps1`, `cc/pm_deploy4_postcheck_ro.sh`.
* **Test baseline: 16 pre-existing env-gap failures** in `tests/prediction_markets/` (offline, `.venv-webtest`,
  `-p no:pytest_ethereum`). All engine-dep, none from UI work; a green UI change leaves them at exactly 16:
  `test_kill_switch_r7d` x4, `test_live_driver_r7c` x7, `test_shard_gate_r2` x4 (`ModuleNotFoundError: pykalshi`) +
  `test_search_r1::test_schema_head_is_15` (stale assertion; schema head is 19). **Measure the baseline yourself by
  stashing your changes and running the committed base** — this number has drifted across sessions (earlier docs
  said 18/19; the authoritative current value on this venv is 16). The pm_web venv is
  `C:\Users\AA Incorporado\CC\.venv-webtest` (fastapi, jinja2, tzdata, playwright); use it for renders.
* **/farm/cs 404 is PRE-EXISTING**, not this branch — 'cs' appears as a non-tile tag on `/farm` but has no
  live-copyable category page. No farm code has shipped from this branch. Do not "fix" it as a regression.

--------------------------------------------------------------------------------
## 5. DELIBERATELY NOT BUILT / DESIGN DECISIONS OPEN

* **"Settled during a live game" note** — needs a settlement-ts <-> game-state-history join that does not exist.
* **Opposed-close realized P&L** — engine work, not UI; the UI shows "not booked".
* **Sport-specific non-MLB cards (court / octagon)** — DEFERRED as a DESIGN DECISION. Non-MLB currently renders an
  honest positions TABLE; whether ATP/UFC/WTA get bespoke scoreboards (and where the data would come from) is Jack's
  call, not assumed. Do not build sport cards without a ruling.
* **Farm League redesign** — the `/farm` + `/farm/{category}` pages are a DESIGN DECISION pending, not part of the
  live-copy UI rewrite. Leave them unless Jack scopes a redesign.

--------------------------------------------------------------------------------
## 6. BACKLOG — gated behind engine work

**Roster / DEMOTE panel** (the next UI pass): replace the flat "Copies these whales" line with an expandable
per-whale panel — per-whale LIVE real-money copy record (dollars first, thin-sample caveats), ON-ROSTER vs
FORMERLY-LIVE with dates, placed/booked/unbooked counts, drill-through to the drawer rows, and a per-sub-division
**DEMOTE** button (confirmation states OPEN copies RUN TO SETTLEMENT — demote stops new copies, does not flatten).
REJECTED by Jack: a paper-vs-live side-by-side per whale.

**SEQUENCING RULE (Jack, hard gate) — ROSTER-SOURCE QUESTION, being reconciled:** the DEMOTE button was gated on the
engine reading the live roster WITHOUT a restart ("today the engine loads the roster at BOOT"). ★ Phase-1 (item 6)
found the code says otherwise: the roster IS the DATABASE, not a yaml — `driver_roster.py:14` ("THE ROSTER IS THE
DATABASE, NOT CONFIG") and `live_driver.py:782` re-read the whale list from `pm_subdivision_attachment WHERE active=1`
EVERY ~7s cycle; only the SPAWN of a NEW (account,category) task is boot-time. So a whale added/removed on an
already-running sub is picked up live, no restart. IF Jack confirms DB-per-cycle with the engine chat, the DEMOTE
hot-reload gate is effectively already met and the item is UNGATED (a detach flips `active=0` and the next cycle
stops copying — `farm_actions.detach_from_live`, already used for the soccer mis-attach). Reconcile before building.

--------------------------------------------------------------------------------
## 7. NOT YET OBSERVED IN PROD

* **Non-MLB mark path with a LIVE non-MLB position.** DEPLOY 4's non-MLB positions view is proven on prod for the
  header/view change (all 6 non-MLB pages) and for a SETTLED position (the Halys loss on `/live/kalshi_jack/atp?tab=
  complete`), but at deploy time no non-MLB sub-division held an OPEN position, so the live current-value/coverage
  path for a non-MLB open position (contracts x bid + "N of M priced" from a KXATPMATCH/KXUFCFIGHT/KXWTAMATCH mark)
  has not been eyeballed on prod. Confirm when a whale next opens a non-MLB position: `traded_series` should include
  that series and the row should price (or read "no mark", never $0). The full value path IS proven off-prod
  (render harness `cc/pm_multicat_render.py` + `cc/pm_betslot_render.py`).
* **Inning-break hollow pips** (DEPLOY 3): proven by unit test + local render only; no game was at an inning break
  during a verification window. Confirm on the next live-baseball check (a MID/END card shows empty pips + cleared
  runners).
* **Multi-whale `+N` and wallet-only truncation on a REAL prod slot.** The settled-slot whale rule IS observed on
  prod (DEPLOY 5 post-check: jack/mlb Complete 7/7 + karen/mlb 5/5 settled slots + the settled ATP row all show a
  whale), but every prod position is currently copied from a SINGLE, short-named whale -- so the `+N` badge and the
  right-truncation ellipsis have not been eyeballed on a live slot. Proven off-prod (render harness
  `cc/pm_betslot_render.py`, `test_bet_slot_whales.py`). Confirm the first time a prod position is stacked by 2+
  whales, or copied from a wallet-only (no display name) whale.

--------------------------------------------------------------------------------
## 8. LIVE SUB-DIVISIONS TILES (DEPLOY 6, 2026-09-07) — cold-start for the next tiles pass

**Prod state.** The tile page (GET /live) is LIVE at branch `pm-tiles-2026-09-07` @ **`3db73c1`** (tag
`pm-tiles-deploy6-2026-09-07`). Box app.py reference = **`eeac337d17a84fc7`** (is_admin=14, /pm/arm=0). PM schema
head = **20**; migrations 018-020 are CLAIMED (018 opposed-marker, 019 multi_category_ok, 020 driver-liveness) — the
**next migration is 021** (db.py migrations are contiguous by a tested invariant; a colliding number silently skips
its DDL, so a deploy must drift-check the live head == 20 first). The tiles added NO migration (all reads are runtime
SQL over existing tables + the box's already-deployed heartbeat tables).

**★ UI BRANCHES ARE A STALE SUBSET OF THE BOX (deferred reconciliation).** The pm-ui-rewrite / pm-tiles branches
predate several deployed non-UI-rewrite workstreams, so these pm_web files exist ON THE BOX but on NO UI branch:
`heartbeat.py`, `web/templates/partials/pm_liveness.html` (driver-liveness), and the `farm.py / farm_actions.py /
analyze.py / search.py / search_run.py / paper.py / positions.py / stats.py / category.py` modules + their templates
(pm_farm_league / pm_farm_category / pm_whale* / pm_watchlist* / pm_macros + the analyze/paper/position/prospect/
search partials). **Procedure Phase 2 used (repeat it):** for EVERY pm_web file you will edit or import from, first
fetch the box's current copy (read-only) and record its CR-stripped sha16 as a "box capture" commit BEFORE editing,
so diffs are against PROD truth, not the stale branch (`cc/pm_tiles_boxfetch_ro.*`). Files whose box sha == your
branch base are wholesale-safe; app.py is ALWAYS a graft onto box-current (§1). Full drift map:
`PM_TILES_PHASE1_INVENTORY_2026-09-07.md`.

**★ DEPLOY DISCIPLINE — BACKUP IS A GATE (hard-won DEPLOY 6).** After backing up, VERIFY the backup dir exists and
every file's sha matches the box BEFORE any copy; a deploy runner must FAIL CLOSED if the backup is missing. DEPLOY
6's apply loop lacked a `mkdir -p "$BK"` before two root-level files, so `subdivision.py` + `arm.py` were overwritten
before the backup dir existed (remediated by backfilling the verified originals). It was recoverable only because the
pre-state == aad4dea; do not rely on that. Gate the backup like every other step.

**Tiles architecture (what is live).** Keyed off DATABASE STATE, not the engine's boot roster:
- Tiles = `subdivision.tiles_all` (ALL active subs, attached + unattached), segmented by account, sorted
  armed -> attached-disarmed -> unattached. Attached = >=1 active attachment (rich tile); unattached = compact
  "no whales attached".
- ARM = `arm.read_display_all` (batched 4-state ARMED/DISARMED/NEVER ARMED/UNAVAILABLE from `agent_state`) with its
  row-ts age. LIVENESS = `heartbeat.read_liveness` (RUNNING/IDLE/CATEGORY_STARVED/STALE/NEVER, banded by age) — shown
  SEPARATELY from arm (arm = should it trade; liveness = is the engine evaluating it).
- **ALARM STRIP (R1)** = page-top red banner listing subs that are effective-ARMED AND liveness STALE or NEVER (the
  28h divergence); CATEGORY_STARVED stays amber non-alarm; empty when nothing qualifies.
- REALIZED (R3) = `subdivision.subdivision_pnl_all` SUM(realized_pnl) over BOOKED terminal closes, labelled "net of
  fees" (entry fees in cost basis, settlement fee=0); count = "N booked closes . W-L from settlements"; UNBOOKED
  closes (opposed/exit, realized_pnl NULL) counted SEPARATELY; NEVER open value. LAST-24H = `realized_24h_all`
  (settlements with settled_ts in the last 24h). OPEN (R4) = three distinct figures (count / at-cost / current value
  + "N of M priced" via `live_view.value_positions`), no-mark honest. Assembler = pure `live_view.build_tiles_context`
  (unit-tested); the ONLY app.py change is the `_load_live_list` body. Thin caveat = booked closes <
  `search.DEFAULT_MIN_RESOLVED_FLOOR` (50).

**Not yet observed on prod.** A REAL armed+STALE/NEVER ALARM STRIP — harness/test-proven only (no armed sub has gone
STALE since deploy; the driver has been healthy). Predicate + render covered by
`test_tiles_view.py::test_alarm_strip_is_armed_and_stale_only` + the render harness (`cc/pm_tiles_render.py` seeds an
armed+STALE sub). Confirm the strip fires the first time an armed sub's heartbeat actually goes STALE/NEVER — that is
a real engine-outage finding for Jack + the engine chat, NOT a UI defect.

**Tiles backlog (Jack: "functional — a more elegant display later").**
- **Mid-size tile for attached-but-never-traded subs**: the ~20 attached-disarmed / never-armed subs with zero
  history render the full rich tile with an empty P&L block, competing visually with the 8 armed tiles. Give a
  zero-history sub (no booked close yet) a MID-SIZE tile — arm, liveness, whales; NO P&L block until a booked close
  exists — so the 8 armed tiles stand out among the 20 zero-history ones.
- **Roster / DEMOTE panel** — see §6; the boot-vs-per-cycle roster-source question is being reconciled (if
  DB-per-cycle is confirmed the DEMOTE gate is already met).
- **Sport-specific ATP/UFC/WTA cards**, **Farm League redesign** — see §5 (design decisions, unchanged).
- **/farm/cs 404** — pre-existing; owned by whoever created the `cs` category, not the tiles workstream.

--------------------------------------------------------------------------------
## 9. LIVE SUB-DIVISIONS REDESIGN (DEPLOY 7, 2026-09-10) — cold-start for the next pass

**Prod state.** GET /live is the Claude-Design redesign, LIVE at branch `pm-tiles-redesign-2026-09-10` @ `5c047f4`
(tag `pm-tiles-deploy7-2026-09-10`). Box app.py = `16caedfe6a193737` (is_admin=28, /pm/arm=0). Schema head 20 (no
migration). Full build+deploy narrative + field map + before/after shas: `PM_TILES_REDESIGN_BUILD_2026-09-10.md`.

**Architecture (what is live).** Server-rendered, pm_web-only, keyed off DB state; JS is enhancement-only.
- **Per-account TABS** via `?account=` in the URL (JS-off works). SCOPED (R6/M4): admin sees all tabs, a non-admin
  only accounts whose `owner_identity` == their identity; no identity -> nothing. The scoping (`authz.visible_account_ids`)
  is now applied to `_load_live_list` AND `live_subdivision_page` (403) AND `/live/events` — the whole /live surface,
  not just the account pages (this closed the add#5 unscoped-detail-route finding).
- **READERS** (`subdivision.py`): `realized_windows_all` (ET-calendar today/week/month + all-time; all-time == the
  account page, ties out), `last_events_all`, `events_since` (the pulse). **ASSEMBLER** (`live_view.build_subdivisions_context`,
  PURE): activity classifier LIVE/UPCOMING/SETTLED/INACTIVE/UNATTACHED via an explicit per-category feed hook
  `_event_underway` (MLB game feed + `parse_ticker_start` HHMM for cs2/nfl/nba/nhl/wnba/cfb; everything else
  unknown->UPCOMING); `name_market` R2 (feed -> Mark.title -> describe_market -> "<CAT> market", never a ticker);
  `SPORTS`/`LIVE_CAPABLE`/`RETIRED_CATEGORIES` consts. **EVENTS**: `GET /live/events?since=<id>` JSON + `pm_live_subs.js`
  (60s poll + placed/closed pulse + two close tones, sound off by default). **LOGOS** `static/logos/<CODE>.png` (21),
  runtime-versioned; monogram fallback (SOCCER).
- **The LIVE event block + the UPCOMING NEXT line are PARTIALS** (`partials/pm_subs_event.html`,
  `pm_subs_eventline.html`) — Jack may strip either with one `{% include %}` (his standing note: "may strip the LIVE
  event block / NEXT line later"). CSS is scoped under `.subs` in `pm_desk.css` (no collision; reuses the palette +
  shell header). Cache-bust: `pm_desk.css?v=80c88cc2`; logos + `pm_live_subs.js` runtime CR-stripped sha8.

**Backlog (redesign follow-ups).**
- **Per-sport feeds for tennis / UFC / soccer / Fed** — today only MLB has a game feed and only MLB + the
  ticker-HHMM sports can read LIVE; the rest stay UPCOMING even once underway. Adding a feed is ONE function
  (`_event_underway`'s else branch) + a scoreboard partial. ★ Candidate source noted: the **Kalshi public
  milestones/market endpoint** (already polled for prices) exposes `status` + `occurrence_datetime` — could drive a
  coarse underway/settled signal per market without a per-sport sports API (see the 2026-09-09 inventory item 12:
  occurrence_datetime is the RESOLUTION time, not the start — treat accordingly).
- **Retire the SOCCER category engine-side** — the `kalshi_jack/soccer` orphan (att=0, no matcher) renders as the
  dashed orphan because pm_web can't do a data-driven "no matcher" check (the matcher registry is engine-side;
  importing it would break the standalone invariant). Retiring `soccer` engine-side (remove the sub-division / mark
  it retired) would drop the dead row cleanly.
- **LIVE event block / NEXT line** — Jack may strip either (each is one include). Non-MLB LIVE tiles show a market
  label + positions, no scoreboard (only MLB has a feed).
