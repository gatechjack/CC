PM TILES REDESIGN -- BUILD -- 2026-09-10
========================================

Board-authorized build of the Claude-Design "Live sub-divisions" redesign (GET /live). pm_web-only:
no engine import, no order path, no migration, no restart, no deploy, no push. Worktree
cc-pm-tiles-redesign-wt, branch pm-tiles-redesign-2026-09-10 (off pm-tiles-redesign-inventory-2026-09-09).
Graft base for app.py = box eeac337d (captured read-only, commit 4d6bf15). All other pm_web files were
confirmed box-identical in the 2026-09-09 inventory.

Design source: the 5 handoff bundles (jack/karen/alarm/events/phone) -> project/subdivisions-jack.html (the
primary; the other four differ only by the VIEW constant + one datum: karen=viewer_role account, alarm=MLS
liveness STALE, events=the animation page, phone=iframe at 374px). Notes: project/subdivisions-notes.md.


1. FIELD MAP -- every MOCK key -> its source
=============================================
Legend: EXISTING = a deployed reader/const already returns it. NEW(reader) = a new pm_web reader (named).
COMPUTED = derived in the assembler/template from other fields. ASSET = a static file. Ages are passed as
`*_age_seconds` and tick client-side (as the design does), so the server sends age-at-render.

  MOCK path                         Source
  --------------------------------  -------------------------------------------------------------------
  meta.global_arm                   EXISTING  arm.read_display() global_state (via _arm/read_display_all)
  meta.global_arm_age_seconds       EXISTING  live_view._ts_age(global ts, now)
  meta.poll_interval_seconds        EXISTING  live_view.POLL_INTERVAL_SECONDS (60)
  meta.generated_age_seconds        COMPUTED  0 at render (now_ts); JS ticks "updated Ns ago / next poll"
  meta.viewer_role                  NEW-graft authz.is_admin(request) -> "admin" | "account" (R6)
  meta.viewer_account               NEW-graft the single visible account for a non-admin, else null (R6)
  meta.tz_note                      COMPUTED  static string (US Eastern calendar day/week/month)
  meta.thin_threshold               EXISTING  search.DEFAULT_MIN_RESOLVED_FLOOR (50)
  accounts[]{id,name,venue,slug}    EXISTING  subdivision.accounts_overview / active_accounts, SCOPED by
                                              authz.visible_account_ids (R6). name=label, venue=venue.
  sports{CODE:{family,logo}}        NEW-const live_view.SPORTS (category -> family) + logo existence in
                                              static/logos/<CODE>.png. Display metadata (design requires it).
  live_capable[]                    NEW-const live_view.LIVE_CAPABLE = MLB,CS2,NFL,NBA,NHL,WNBA,CFB
                                              (MLB feed + the 6 whose ticker carries an HHMM start; item 10)
  subdivisions[].code               EXISTING  tiles_all category (upper-cased)
  subdivisions[].account            EXISTING  tiles_all account_id
  subdivisions[].activity           NEW       classify_activity() -> LIVE|UPCOMING|SETTLED|INACTIVE|UNATTACHED
  subdivisions[].arm{state,age}     EXISTING  arm.read_display_all + _ts_age
  subdivisions[].liveness{...}      EXISTING  heartbeat.read_liveness SubLiveness (state, age_sec, n_signals,
                                              placed, errors) -> {state, heartbeat_age_seconds, signals, orders, errors}
  subdivisions[].whales             EXISTING  tiles_all n_whales (count only -- identities stay on detail page)
  subdivisions[].realized.today     NEW(reader) realized_windows_all -> ET-calendar today (booked closes)
  subdivisions[].realized.week      NEW(reader) realized_windows_all -> ET-calendar week (booked closes)
  subdivisions[].realized.month     NEW(reader) realized_windows_all -> ET-calendar month (booked closes)
  subdivisions[].realized.all_time  EXISTING  subdivision_pnl_all.realized (== the account page; ties out)
  subdivisions[].realized.booked_closes/wins/losses/unbooked  EXISTING  subdivision_pnl_all
  subdivisions[].open{count,cost,value,priced,of}  EXISTING  live_positions + value_positions
  subdivisions[].open.mark_age_seconds  EXISTING  _cache_marks() refreshed_ts age
  subdivisions[].event{...}         NEW       LIVE only. MLB: feed_mlb scoreboard (game_key_from_ticker +
                                              match_in_slate + _feed_block) + positions named via R2; non-MLB
                                              live-capable: label + positions, no scoreboard (degrades honestly)
  subdivisions[].next_event{label,starts_in_seconds}  NEW  UPCOMING: soonest held event, label via R2;
                                              starts_in only for LIVE_CAPABLE (ticker HHMM); else null
  subdivisions[].last_trade         NEW(reader) journal max submitted_ts (in context; NOT rendered -- notes 74)
  subdivisions[].last_close{age,result,realized}  NEW(reader) journal newest booked is_exit close
  subdivisions[].orphan             NEW       category in the retired set of live_view.SPORTS (soccer). R7.

  Naming (R2), one helper name_market(ticker, leg, mark, feed_game, category), priority:
    feed team names (MLB) -> Mark.title (cached) -> market_describe.describe_market (MLB) ->
    "<CATEGORY> <market type>" (never the raw ticker; the exception is logged + reported).


2. WHAT WAS BUILT (per step; committed after each)
==================================================
Commits: 6e30cdb field-map -> 4d6bf15 box-capture app.py (eeac337d) -> 57c9c2c readers+assembler+scoping ->
520dd1c template+CSS+events+logos -> 6d94f40 tests+search_run-capture -> this report.

- READERS (subdivision.py, stdlib-only, standalone): realized_windows_all (ET-calendar today/week/month over BOOKED
  closes anchored COALESCE(settled_ts,response_ts); all_time has no lower bound so it EQUALS subdivision_pnl_all --
  ties out to the account page); last_events_all (last trade + last booked close per sub); events_since (the pulse
  reader: real orders with id>since, split placed/closed).
- ASSEMBLER (web/live_view.py): build_subdivisions_context -- PURE, no DB/network. SPORTS family map + LIVE_CAPABLE +
  RETIRED_CATEGORIES consts; et_window_cutoffs (Monday week, DST-correct); parse_ticker_start (item 10, ~15 lines);
  name_market (R2); _event_underway (the per-category feed hook); _live_event (MLB scoreboard, degrades); _next_event;
  the classifier; the alarm-first + |today| sort (R5); the summary rollup + tabs. Removed the superseded
  build_tiles_context.
- APP (web/app.py, GRAFT onto box eeac337d): _load_live_list rebuilt + SCOPED; /live tabs via ?account=;
  /live/{account}/{category} 403s a non-admin; new /live/events pulse endpoint; logo/JS version helpers. No engine
  import (standalone invariant holds -- app imports with zero engine/broker modules), no migration.
- TEMPLATE (web/templates/pm_live_list.html + partials/pm_subs_event.html + pm_subs_eventline.html): server-rendered
  Claude-Design port; the LIVE event block + NEXT/LAST line are partials (R1 -- one include to drop).
- CSS (web/static/pm_desk.css): the design's tile system, every rule scoped under .subs (no collision with pm.css or
  the shared shell), reusing the existing palette + header + .badge. Shell pm_desk.css ?v= bumped 8121e8e0->80c88cc2.
- EVENTS JS (web/static/pm_live_subs.js): age ticker + 60s fetch-swap + the diff-since pulse (placed = cyan glow,
  silent; closed = green/red wash + a two-note tone if sound on; "settled Nm ago", never instant). Sound off by
  default, header toggle.
- LOGOS: 21 league logos downscaled to <=128px PNG -> static/logos/<CODE>.png; monogram fallback (SOCCER). Versioned
  by runtime CR-stripped sha8 (_LOGO_VERSIONS / _SUBS_JS_V).


3. R6 SCOPING + the add#5 SECURITY FIX (proof)
==============================================
The existing authz.visible_account_ids is now applied to BOTH /live surfaces (it was on / and /account/{id} only):
  - _load_live_list scopes the account set + tile set to the viewer BEFORE building; admin -> all tabs, a non-admin ->
    only accounts whose owner_identity == their identity (fail-closed), with viewer_role/viewer_account driving the
    single-account note.
  - live_subdivision_page (the detail route, inventory add#5) now returns 403 for an account that is not the viewer's
    (exists) / 404 (absent) -- scoping the tile page while leaving the detail route open would have been theatre.
  - /live/events is scoped identically (a non-admin never sees another account's order flow).
PROVEN (test_subs_scope.py, all green): a Karen (non-admin, PM_ADMIN_IDENTITIES=jack) login sees ONLY kalshi_karen on
/live (no jack tab, no jack tiles, SINGLE-ACCOUNT note) and is REFUSED /live/kalshi_jack/mlb (403) and jack's
/live/events; an admin (jack) sees both. Rendered proof: renders/subs_karen.png.


4. R2 -- MEANINGFUL NAMES, and the exceptions
=============================================
name_market resolves, in order: MLB feed matchup ("NYY @ BAL") -> cached Kalshi Mark.title ("Getafe wins",
"Cleveland wins" -- verified live on the endpoint) -> market_describe (MLB: "Chicago Cubs to win", "Over 8.5 runs") ->
"<CATEGORY> <market type>". It NEVER returns a raw ticker. A held position that reaches the last fallback (no feed, no
cached mark title, non-MLB so describe_market can't parse) is COLLECTED in name_exceptions and logged
(log.warning "named by CATEGORY fallback").
  - R2 EXCEPTIONS on the real data: NONE observed. Every held non-MLB ticker prices through the mark cache, which
    carries Mark.title, so the name is the market title; MLB names come from the feed/describe. Proven zero-ticker:
    test_subs_scope.test_no_raw_ticker_on_live_page + test_subs_view.test_name_market_is_never_a_ticker + the render
    harness (grep of every rendered HTML for the KX...- ticker pattern = 0).
  - NAMING NUANCE (reported, not a defect): for a non-MLB sub the NEXT/event LABEL is the MARKET title ("Getafe wins"),
    not the matchup ("Getafe v X") -- Mark.title is a market name, and there is no matchup string for non-MLB without a
    feed. It reads like a side (satisfies R2); a true matchup for non-MLB needs a per-sport feed (deferred, see R3).


5. R7 -- the SOCCER orphan
==========================
A fully DATA-DRIVEN "no matcher registered" exclusion is NOT available to pm_web: the matcher registry is engine-side
(MATCHER_ADAPTERS + the trading_corp.data matchers), and importing it would break pm_web's standalone invariant (which
a test enforces). So, per R7's fallback, SOCCER is LEFT as the dashed orphan the design shows ("retired code - never
trades"), flagged via the RETIRED_CATEGORIES set in live_view (the curated family map the design already requires). On
the box the orphan is exactly one row: kalshi_jack/soccer (att=0, no matcher; box-scratch [2] confirms 13 unattached
incl it). Reported so the designer knows the page carries permanent dead rows + this one orphan.


6. R3 -- activity classifier + the feed hook
============================================
classify: UNATTACHED (no whales) / else open>0 ? (underway ? LIVE : UPCOMING) : (history ? SETTLED : INACTIVE).
The "underway" test is an explicit per-category hook, _event_underway: MLB via the game feed (in_progress); the
LIVE_CAPABLE sports (CS2/NFL/NBA/NHL/WNBA/CFB) via the ticker HHMM start (parse_ticker_start) + a non-finalized mark;
EVERYTHING ELSE returns unknown -> UPCOMING today. Adding a tennis/UFC/soccer/Fed feed later is ONE function
(_event_underway's else branch), not a rewrite. Proven per category in test_subs_view (cs2 past-start -> LIVE; soccer
date-only -> UPCOMING even with an open position -- honest per inventory item 12). Box-scratch [2] shows the classifier
inputs on real data (lal/mlb open -> UPCOMING/LIVE, history-only -> SETTLED, no-history -> INACTIVE).


7. R4 -- money windows are ET CALENDAR
======================================
realized_windows_all computes today/week/month via et_window_cutoffs (America/New_York; day ends 23:59:59 ET; week
starts MONDAY 00:00 ET -- a one-line change to Sunday, documented) + all_time (no bound). NOT rolling. Realized only,
net of fees, BOOKED closes (realized_pnl NOT NULL); unbooked counted separately; THIN under 50 booked. Current value is
NEVER folded into realized (distinct keys everywhere; the summary bar's "current value" column is cyan/unrealized with
its own coverage + mark age). DST-correct (test_subs_readers proves ET midnight in both EDT and EST). all_time TIES OUT
to subdivision_pnl_all on real prod data (box-scratch [1]: ALL TIE-OUT True, 12/12 subs).


8. VERIFICATION (evidence)
==========================
- TESTS (tests/prediction_markets/, .venv-webtest, -p no:pytest_ethereum): 16 failed / 863 passed / 1 skipped ==
  the env-gap baseline (15 pykalshi ModuleNotFoundError + 1 stale test_schema_head_is_15). ZERO non-baseline failures.
  DELTA explained: replacing build_tiles_context + capturing box app.py briefly exposed 5 farm-test regressions
  (box app.py lazy-imports latest_search_status/refresh_one/acquire_search_lock from a box-current search_run.py the
  stale branch lacked) -> resolved by capturing box search_run.py (a15acc3a) for local parity (NOT a shipped file --
  the box already carries it). New tests (17, all green): test_subs_readers (5: windows+tie-out, ET/DST Monday week,
  ticker-start by category, last_events, events_since), test_subs_view (6: classifier per category, alarm-first+|today|
  sort, name-never-ticker+exception, orphan, viewer passthrough, honest event block), test_subs_scope (6: both routes
  scoped, /live/events scoped, no-raw-ticker, cache-bust-matches-shell).
- RENDERS (cc/pm_subs_render.py -> cc/renders/*.png, VIEWED at 1600/1280/374): subs_jack (summary bar + LIVE MLB 2x2
  scoreboard event block + UPCOMING/SETTLED/INACTIVE 'no history'/UNATTACHED dashed + legend); subs_alarm (page-top red
  strip + ALARM section first + red MLS tile w/ banner); subs_karen (non-admin, only Karen tab + SINGLE-ACCOUNT note);
  subs_events (JUST PLACED cyan + JUST CLOSED WON green banners at rest); subs_phone (374px: one column, summary
  stacked realized-today-first, LIVE drops 2x2, unattached 2-up). All faithful to the prototype.
- BOX-SCRATCH (cc/pm_subs_boxscratch_ro.*, read-only mode=ro, 2026-09-10T06:22Z): all_time realized TIES OUT to the
  account-page reader for all 12 traded subs; counts 43/30/13 match the deployed page; classifier inputs correct;
  events baseline max_order_id=427.
- No engine import (standalone invariant test green + import census = 0 engine/broker modules); no migration; schema
  head unchanged (20).


9. SHIPPABLE FILE LIST (pm_web-only) + app.py hunks + DEPLOY SHAPE
=================================================================
All CR-stripped sha16. BEFORE = box-current (from the 2026-09-09 inventory / box capture); AFTER = this branch.

  file                                     BEFORE (box)        AFTER (branch)      ship mode
  ---------------------------------------- ------------------- ------------------- ------------------------------
  subdivision.py                           752e244af0af9e2a    f11d755e1045068f    wholesale (readers added)
  web/live_view.py                         e514a47ad49fd2f7    c0f44414031194c4    wholesale (assembler; dead fn removed)
  web/templates/pm_live_list.html          7821241509ce44e2    97f0f12b5bdfb1ca    wholesale (redesign)
  web/templates/partials/pm_subs_event.html      (new)         6fcb55b8db032b70    NEW
  web/templates/partials/pm_subs_eventline.html  (new)         164d422b510ed49b    NEW
  web/static/pm_desk.css                   8121e8e010e9efde    80c88cc28abbc1b7    wholesale (+.subs block; ?v bump)
  web/static/pm_live_subs.js                     (new)         85748440ab08ca29    NEW
  web/templates/pm_shell.html              d8076b29874f927f    8a10c80d04f4a126    wholesale (?v= 8121e8e0->80c88cc2 only)
  web/static/logos/<CODE>.png (21)               (new)         (per-file)          NEW (binary; 21 files, <=128px)
  web/app.py                               eeac337d17a84fc7    16caedfe6a193737    GRAFT the hunk (NOT wholesale)

app.py GRAFT (git diff 4d6bf15 HEAD -- app.py = +135/-35, 4 hunks, onto box eeac337d, stays is_admin=14 //pm/arm=0):
  1. +import hashlib (top).
  2. _LOGO_DIR/_STATIC_DIR + _sha8 + _logo_versions + _LOGO_VERSIONS/_SUBS_JS_V + _load_live_list REBUILT (scoped,
     new context) -- replaces the Phase-2 _load_live_list body.
  3. _load_live_subdivision +identity/is_admin_flag params + the visible_account_ids gate (403/404).
  4. _load_live_events loader + @app.get("/live/events") + live_list_page(?account=, scoped) + live_subdivision_page
     (passes identity, handles _FORBIDDEN).
The M5 /pm/arm route is NOT present (graft base is box eeac337d, not the branch M5 app.py) -- it can never leak.
NOT shipped: main.py (untouched); heartbeat.py (already on box); search_run.py (already on box -- captured only for
local test parity).

DEPLOY SHAPE (Jack's to run; pm_web-only, ONE pm_web restart, engine NEVER touched):
  1. PRE: CR-strip-sha the 8 box files == the BEFORE column (drift-check); back up all shipped files (BACKUP IS A
     GATE -- verify the backup dir + every sha BEFORE any copy; fail closed if missing).
  2. Write the 7 wholesale files (subdivision.py, live_view.py, pm_live_list.html, pm_subs_event.html,
     pm_subs_eventline.html, pm_desk.css, pm_live_subs.js, pm_shell.html) + the 21 logos (base64 -> temp -> CR-strip ->
     sha16 gate == AFTER -> mv); GRAFT the app.py hunk (patch --fuzz=0 onto box eeac337d; verify is_admin=14,
     /pm/arm=0, py_compile OK, zero engine imports). NO migration.
  3. RESTART prediction-markets-web ONLY (az vm run-command 'systemctl restart prediction-markets-web'); engine
     trading-corp UNCHANGED.
  4. POST: /live 200 (tabs + tiles); a non-admin identity 403 on another account's /live/{acct}/{cat}; served
     pm_desk.css sha == 80c88cc2 + shell ?v=80c88cc2; each logo 200; /live/events 200 JSON; engine PID + order counts
     unchanged; grep the served /live for the KX...- ticker pattern == 0 (R2).


10. NOT BUILT / NOTES for Jack
==============================
- LIVE event SCOREBOARD is MLB-only (the only category with a game feed). A live-capable non-MLB sub (CS2/NFL/...)
  that goes LIVE renders the event block as a market LABEL + valued positions, no score -- honest, degrades cleanly
  (the partial handles both). A per-sport scoreboard needs a per-sport feed (the same feed R3's hook is built to
  accept later).
- Two supplied logos are extreme-aspect banners (BUN 128x8, MEX 128x16 after downscale) -- they render as a thin strip
  inside the 34px plate. Not a bug (that is the supplied art); flagged so Jack can swap them for square marks.
- search_run.py was box-captured for LOCAL test parity only (box app.py depends on its newer symbols); it is NOT a
  redesign change and is already current on the box. A separate finding: the branch is behind the box on search_run.py
  (and app.py) -- consistent with the standing "branches are behind the box" note; the cross-division reconcile will
  fold it.
- Everything is pm_web-only: no engine import, no order path, no migration, no restart, no push, no deploy. The build
  is committed locally on branch pm-tiles-redesign-2026-09-10; deploy/push/restart are reserved for Jack.


================================================================================================
DEPLOY 7 -- 2026-09-10 (DEPLOYED LIVE; pm_web-only + ONE pm_web restart; engine NEVER touched)
================================================================================================
Board-authorized. Shipped the redesign to prod: 8 pm_web files wholesale + 21 logos + app.py GRAFTED onto box
eeac337d, ONE prediction-markets-web restart via az. Engine trading-corp (PID 292771, ARMED, 30 armed subs across
two accounts) was NOT restarted, reloaded, or touched at any step. All gates passed; no rollback. Deploy target =
branch pm-tiles-redesign-2026-09-10 @ 5c047f4 (the step-0 logo-swap commit). MEASUREMENT RULE (CR-strip both sides)
applied throughout. Runners cc/pm_deploy7_{precheck_ro,apply,verify_ro,restart_az,postcheck_ro,fetchlive_ro}.* +
gen_deploy7.py + pm_deploy7_app.patch.

STEP 0 -- LOGO SWAP (commit 5c047f4): BUN.jpg (428x350) + MEX.png (500x500) -> static/logos/BUN.png (128x105) /
MEX.png (128x128), square marks replacing the banner strips. Logo ?v= is runtime CR-stripped sha8 (auto-versions).
Cache-bust test pins the 21-logo roster. Deploy target sha recorded = 5c047f4.

PRE-CHECK (pm_deploy7_precheck_ro, 2026-09-10T10:36Z) -- ALL GATES PASS, NO DRIFT:
  engine 292771 NRestarts 0 active; pm_web 235587 active; schema head 20. 31 arm rows (global + 30 subs) all
  armed=True. Counts total=43 attached=30 unattached=13 armed=30. Heartbeat 30 rows, 0 STALE/NEVER (alarm strip
  will be empty). Journal orders jack=269 karen=159; events baseline max_order_id=428. DRIFT GATE: box CR-stripped
  sha16 == box-capture BEFORE for all 6 (subdivision 752e244a, live_view e514a47a, pm_live_list 78212415, pm_desk
  8121e8e0, pm_shell d8076b29, search_run a15acc3a) + app.py eeac337d/14/0. NEW files (2 partials, pm_live_subs.js,
  logos/) confirmed ABSENT. Served pm_desk.css 8121e8e0, /live 200.

BACKUP (a GATE) = /home/azureuser/pm_deploy7_backup_20260910T103627Z. Dirs mkdir'd FIRST; the 6 existing files
  (5 text + app.py) copied + VERIFIED (each backup CR-stripped sha == box) before any write. The 3 new text files +
  21 logos have no box copy (rollback for them = remove). Backup complete + verified before [C].

APPLY (gen_deploy7 -> pm_deploy7_apply.sh; base64 -> tmp -> sha16 gate -> mv; app graft via patch --fuzz=0):
  Drift RE-GATE at apply time: all 6 + app.py re-confirmed == BEFORE. 8 text files written (each CR-stripped sha16
  == target: subdivision f11d755e, live_view c0f44414, pm_live_list 97f0f12b, pm_subs_event 6fcb55b8,
  pm_subs_eventline 164d422b, pm_desk 80c88cc2, pm_live_subs.js 85748440, pm_shell 8a10c80d). 21 logos written
  (RAW sha16-gated; binary never CR-stripped). app.py GRAFTED (patch dry-run OK then applied): eeac337d ->
  16caedfe6a193737, /pm/arm=0.
  ** ONE FALSE-STOP (not a defect): the apply's [E] gate asserted is_admin==14 (a stale Deploy-6 assumption) and
     exited 6, because the redesign's SCOPING code legitimately raises is_admin references 14 -> 28. The grafted
     app.py sha16 == 16caedfe6a193737 (byte-identical to the verified local file) and /pm/arm=0 (no M5 leak), so the
     graft is correct. Per the command-paste-rule corollary (verify a gate failure is REAL before aborting), this
     was confirmed a false negative and the skipped post-graft checks were run read-only (pm_deploy7_verify_ro):
     py_compile OK; import OK with engine_imports=[]; routes /live + /live/events + detail all present; loaders +
     21 logos present; all 8 text re-sha == target; 21 logos on box. No rollback.
  No package/venv/unit change.

RESTART (pm_deploy7_restart_az) -- pm_web ONLY: az vm run-command 'systemctl restart prediction-markets-web'.
  pm_web 235587 -> 298063 active/running. Engine trading-corp 292771 -> 292771 UNCHANGED, NRestarts 0 before+after.

POST-CHECK (pm_deploy7_postcheck_ro, after one poll cycle, 2026-09-10T10:41Z) -- checks 9-18 ALL PASS:
  9.  /live 200; 15 tiles (Jack active tab, data-sub); both tabs; sections Live/Upcoming/Settled/Inactive/Unattached;
      22 corner state-tabs; alarm-strip=0 (correct -- 0 STALE/NEVER); GLOBAL ARMED.
  10. Scoping (R6 + add#5): karen -> only kalshi_karen tab + SINGLE-ACCOUNT note + 0 jack tiles; karen ->
      /live/kalshi_jack/mlb = 403; /live/kalshi_karen/mlb = 200; /live/kalshi_nope/mlb = 404; NO identity -> tabs=[]
      (fail-closed).
  11. R2: raw KX...- ticker hits on /live = 0. All 21 logos HTTP 200; BUN 7390 / MEX 9009 bytes (the swapped squares).
  12. Money TIE-OUT: my windows.all_time == subdivision_pnl_all.realized for ALL 12 traded subs (ALL TIE-OUT True).
  13. Activity classified from real data (post-check + served render): a LIVE CS2 sub with the event block (esports
      match underway, ticker-HHMM classifier live); LAL/MLB open -> UPCOMING; history-only -> SETTLED; attached
      no-history -> INACTIVE ("no history"); 13 unattached compact incl. the SOCCER orphan.
  14. /live/events?since=0 -> JSON {max_id:428, placed:[...named "Chicago Cubs vs Cincinnati Reds -- ..."]}; sound
      toggle present + off by default ("Sound off", aria-pressed=false).
  15. Every real page 200 + styled: /, /farm, both account pages, all 43 /live/{acct}/{cat}, all real /farm/{cat}.
      (The lone NON-200 was GET /farm/search=404 -- /farm/search is a POST-only route, category 'search' does not
      exist, honest 404, PRE-EXISTING and untouched by this deploy; a false hit from the post-check's greedy grep of
      the search form action. Not a regression, not a rollback condition.)
  16. Cache-bust: served pm_desk.css sha = 80c88cc2 (== target), shell ?v=80c88cc2, pm_live_subs.js?v=85748440;
      pm.css/pm_desk/subs.js/htmx all 200 (no 404).
  17. Engine 292771 NRestarts 0 UNCHANGED; 0 journalctl -p err since the restart; order counts to compare next
      cycle (jack 269 / karen 159 at pre-check; any change = attributable fills, pm_web places nothing).
  18. Zero double-escaped entities on /live. Served page fetched read-only + rendered at 1280 + 374 (phone) and
      VIEWED (cc/renders/served_1280.png, served_phone.png): faithful, real data, square BUN/MEX logos, LIVE CS2
      2x2 with event block, phone one-column.

FILE LIST -- before(box)/after CR-stripped sha16 (8 text) + 21 logos + app.py graft:
  subdivision.py 752e244a->f11d755e ; web/live_view.py e514a47a->c0f44414 ; pm_live_list.html 78212415->97f0f12b ;
  partials/pm_subs_event.html (new) 6fcb55b8 ; partials/pm_subs_eventline.html (new) 164d422b ;
  static/pm_desk.css 8121e8e0->80c88cc2 ; static/pm_live_subs.js (new) 85748440 ; pm_shell.html d8076b29->8a10c80d ;
  static/logos/*.png x21 (new, RAW-sha gated). web/app.py eeac337d->16caedfe (GRAFT; is_admin 14->28, /pm/arm=0).
  ** NEW BOX app.py REFERENCE = 16caedfe6a193737 (is_admin=28, /pm/arm=0) -- supersedes eeac337d for the next deploy.**
NOT shipped: main.py (untouched); heartbeat.py + partials/pm_liveness.html (already on box); search_run.py
  (already on box; box-captured for local test parity only). No migration; schema head 20.

PIDs: pm_web 235587 -> 298063. Engine 292771 UNCHANGED, NRestarts 0 throughout. Backup:
  /home/azureuser/pm_deploy7_backup_20260910T103627Z (6 files; rollback = restore + pm_web-only restart, new files
  removed). Skipped/notes: the apply [E] is_admin==14 gate false-stop (verified correct read-only, see above); no
  app.py wholesale (grafted); no main.py; no migration; no package/venv change. The engine + both trading accounts
  + the order path were never touched.


================================================================================================
EVENT BLOCK FIX -- 2026-09-11 (Deploy 7.2; BUILT + TESTED + COMMITTED, NOT DEPLOYED)
================================================================================================
Board-authorized fix of the LIVE tile event block. Branch pm-tiles-redesign-2026-09-10 @ f055b7b (worktree
cc-pm-tiles-redesign-wt). pm_web-only; app.py NOT touched; no migration; NOT pushed/deployed/restarted (Jack calls
Deploy 7.2 separately).

ROOT CAUSE (where the grouping went wrong). live_view._live_event picked ONE underway game for the scoreboard
(the first held ticker whose feed game is_live), then built the position rows with `_event_rows(positions, marks)`
over ALL of the sub-division's open positions -- so every open MLB position was attached to that one game. On
Jack's live Jack/MLB tile that rendered "TB 0 ATL 0 ... ML ATL / ML PHI", but PHI is from HOU@PHI, a different
game. Two defects: (a) no grouping by game; (b) ML showed the market's YES club, not the side we hold.

EVIDENCE (real box-held positions, read-only box-scratch cc/pm_eventfix_boxscratch_ro.*, 2026-09-11): both
kalshi_jack/mlb and kalshi_karen/mlb held exactly the two-game set that triggered it --
  KXMLBGAME-26SEP101215TBATL-ATL (TB@ATL, held ATL) + KXMLBGAME-26SEP101305HOUPHI-PHI (HOU@PHI, held PHI).
The old code put BOTH ATL and PHI under the one underway game. Running the NEW _live_event on those exact tickers
(both games stubbed underway) yields TWO rows: TB@ATL -> [ML ATL] (PHI ABSENT), HOU@PHI -> [ML PHI] (ATL ABSENT),
each with the held team marked home. Defect resolved.

THE FIX (live_view.py):
  FIX 1 -- GROUP BY GAME. _live_event now buckets the sub's OPEN positions by the SAME ticker->game join the card
    page uses: game_key_from_ticker for MLB, the match stem (ticker minus the leg suffix) for live-capable non-MLB.
    It emits ONE compact row PER UNDERWAY game, each listing ONLY that game's positions, ordered most-recently-
    started first, capped at 3 with `more` = overflow (the tile links '+N more live' to the detail page). Positions
    on games that are NOT underway are not in the block -- they remain summarised on the OPEN line.
  FIX 2 -- OUR SIDE, EXPLICIT, SAME SHORTHAND. _short_label's ML branch is now leg-aware via a new _held_team_code
    (YES club for a YES/absent leg, the OTHER club for a NO leg) -- ML now carries direction like TOT (+/-) and SPR
    (sign + team) already do, and it is the SAME _short_label the card page renders (not a second labeler; the card
    page's ML compact label becomes leg-aware too, matching its own describe_market desc). The held ML team is also
    marked in the score line (away_ours/home_ours -> a small filled .mine dot beside that team), so the score line
    itself says who we're cheering for. Codes only; kind . label . value; no extra columns, no spelled-out names.
    Settled-during-game rows keep the card-page ✓/✕ rendering (not reachable from the tile's open-positions-only
    feed today, but the partial + CSS carry it).
  FIX 3 -- DEFENSIVE. A position whose ticker joins no game (parser miss / bad ticker) is OMITTED from the block,
    never attached to a game by default; it stays counted on the OPEN line.

FILES (pm_web-only) -- CR-stripped sha16, BEFORE(box, Deploy 7/7.1) -> AFTER(branch):
  web/live_view.py                          c0f44414031194c4 -> a4f233fb18f0cc3c   (_live_event group-by-game,
                                                                                    _held_team_code, _short_label ML)
  web/templates/partials/pm_subs_event.html 6fcb55b8db032b70 -> 3dd74d8ee56d3ea5   (row-per-game stack + .mine + more)
  web/static/pm_desk.css                    80c88cc28abbc1b7 -> 246a3fa9dd20376c   (.mine marker, .evrow-n, .evmore,
                                                                                    .pr.won/.lost)
  web/templates/pm_shell.html               8a10c80d04f4a126 -> 6141979a864e4c04   (pm_desk.css ?v= 80c88cc2->246a3fa9)
  web/app.py                                16caedfe6a193737 (UNCHANGED -- NOT shipped)
  web/static/pm_live_subs.js                fe29f6e59d972a16 (UNCHANGED -- NOT shipped)
No migration; main.py untouched; standalone invariant intact.

VERIFICATION:
  - Full tests/prediction_markets/ (.venv-webtest, -p no:pytest_ethereum): 16 failed / 870 passed / 1 skipped ==
    the env-gap baseline (16), +7 new event tests. New tests (test_subs_event.py): grouping (game A never lists
    game B's position), ML/TOT/SPR labels from the held leg, held-team score-line marker (home + away), unjoinable
    ticker omitted, multi-live-game stack capped at 3 + more==1, non-MLB no-scoreboard row, _short_label ML leg-aware
    (yes->yes club, no->other club). test_subs_view event assertion updated to the row shape. Cache-bust test green
    (shell pm_desk ?v= bumped to 246a3fa9).
  - Rendered + VIEWED: cc/renders/event_fix_live_tile.png (close-up: TB@ATL row with ML ATL / TOT +7.5 / SPR -1.5 ATL
    + the .mine dot on ATL; a separator; SD@CIN row with ML SD + the .mine dot on SD; open shows 9 pos while the
    block shows only the 2 underway games -- FIX 3); subs_jack.png (1600) and subs_phone.png (374, stacked rows).
  - Box-scratch (read-only, real data): see EVIDENCE above -- the fix groups the real held TBATL/HOUPHI positions
    into their own games and marks the held team.

DEPLOY 7.2 SHAPE (Jack's to call; pm_web-only, ONE pm_web restart, engine untouched, backup-is-a-gate): graft the
4 files above wholesale (drift-gate box == the BEFORE column: live_view c0f44414, pm_subs_event 6fcb55b8, pm_desk
8121e8e0... NO -- 80c88cc2 [Deploy-7 value], pm_shell 8a10c80d), NO app.py change, NO logo change, NO migration;
served pm_desk.css must read 246a3fa9 + shell ?v=246a3fa9 after; verify /live event block groups by game + marks
the held team on Jack/MLB. (app.py stays 16caedfe; pm_live_subs.js stays fe29f6e5.)
