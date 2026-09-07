PM TILES PHASE-2 BUILD -- 2026-09-07
====================================

Built the Live Sub-divisions TILE page (Dashboard > Live sub-divisions, GET /live). BUILT + TESTED +
COMMITTED + PUSHED. NOTHING DEPLOYED, NOTHING RESTARTED -- deploy/restart are Jack's. pm_web-only; the engine,
order path, arm state, and prod DB were never touched. Worktree cc-pm-tiles-wt, branch pm-tiles-2026-09-07 (off
pm-tiles-phase1-inventory-2026-09-07). Two accounts traded throughout, untouched.

Commits: efa6b29 (box capture) -> 7e13cf3 (implementation) -> 68798d6 (tests) -> this report.


1. WHAT CHANGED (the design)
============================
GET /live was a sparse tile (account.CATEGORY + a trade-count hint). It is now a rich, per-account tile grid
keyed off DATABASE state (R1): all 43 sub-divisions, segmented by account, sorted armed -> attached-disarmed ->
unattached. Each attached tile carries arm state, driver liveness, attached whales, lifetime + last-24h realized
P&L, and open count/at-cost/current-value+coverage. Unattached subs render a compact "no whales attached" tile.
An ARMED sub whose driver reads STALE/NEVER rides a page-top red ALARM STRIP -- the exact 28h divergence (R1).

Files changed (all pm_web / display; NO engine, NO order path, standalone invariant preserved):
  READERS   subdivision.py     +tiles_all / subdivision_pnl_all / realized_24h_all (batched, read-only)
            arm.py             +read_display_all (batched 4-state, ONE mode=ro open for all tiles)
  ASSEMBLY  web/live_view.py   +build_tiles_context (PURE: no DB/network; reuses _whale_tag + value_positions)
  LOADER    web/app.py         _load_live_list body only (purely additive; graft onto box-current 069d7a25)
  VIEW      web/templates/pm_live_list.html   rebuilt (reuses arm_badge + pm_liveness macros)
            web/static/pm_desk.css            +tile styles (dark, auto-fill grid 1600+1280); cache-bust bumped
            web/templates/pm_shell.html       pm_desk.css ?v= 18454d56 -> 8121e8e0
  CAPTURED  heartbeat.py, partials/pm_liveness.html  (box-only base, imported/used; see section 2)
  TESTS     tests/prediction_markets/test_tiles_readers.py, test_tiles_view.py (+14), test_live_r3.py (5 updated)


2. GIT BASE + BOX-CAPTURE (diffs against PROD truth, not aad4dea)
================================================================
Because the pm-ui-rewrite branch (aad4dea) is a stale subset of the box, every file I edit or import from was
captured/verified against box-current FIRST (2026-09-07T16:48Z read-only fetch, cc/pm_tiles_boxfetch_ro.*):

  file                                   box sha16 (CR-stripped)   status vs branch base
  -------------------------------------- ------------------------- --------------------------------------
  heartbeat.py                           57afdcc6c61055e2          CAPTURED (box-only; == cc-pm-liveness-wt) commit efa6b29
  web/templates/partials/pm_liveness.html 9c87b8309271d773         CAPTURED (box-only; == cc-pm-liveness-wt) commit efa6b29
  web/app.py                             069d7a255a9f4ebc          GRAFT BASE (is_admin=14, /pm/arm=0)
  subdivision.py                         863af1d1522fb364          box == aad4dea (my base) -> wholesale-safe
  arm.py                                 60f447207d52694a          box == aad4dea -> wholesale-safe
  web/live_view.py                       8fb7db158e4a5af8          box == aad4dea -> wholesale-safe
  web/templates/pm_live_list.html        6fd8aad7a1b25d71          box == aad4dea -> wholesale-safe
  web/static/pm_desk.css                 18454d5690a316ed          box == aad4dea -> wholesale-safe
  web/templates/pm_shell.html            934c258ce953b18d          box == aad4dea -> wholesale-safe
  web/templates/partials/pm_arm_badge.html 6bb019840c5e2b2a        box == branch (reused as-is, unchanged)
  web/marks.py / poller.py / ui_cache.py 8cace4e7 / d9f9f4f5 / e116ee8a  box == aad4dea (imported by the loader, unchanged)
  stats.py                               e0334a96f75ba821          reference only (DEFAULT_MIN_RESOLVED=10; not imported)

heartbeat.py imports NO engine (stdlib only) -> the standalone invariant holds; the branch imports it cleanly.
The thin floor = search.DEFAULT_MIN_RESOLVED_FLOOR = 50 (search is top-imported by app.py on branch AND box).


3. EVIDENCE PER SPEC ITEM
=========================
R1 -- keys off DB state; shows the arm-vs-engine DIVERGENCE. build_tiles_context reads the sub/attachment
   tables + persisted agent_state arm rows + the engine heartbeat; a tile is ARMED-in-DB from arm.read_display_all
   (effective = global AND sub), LIVE from heartbeat.read_liveness. is_alarm = effective-armed AND liveness in
   (STALE, NEVER). A DB-live sub the engine hasn't picked up is NEVER hidden. Proven: test_alarm_strip_is_armed_
   and_stale_only (an armed+STALE and armed+NEVER alarm; a DISARMED+STALE does NOT alarm; armed+RUNNING does not).
   The alarm strip is rendered page-top (not just a tile colour) -- template `tl-alarm` block + per-tile
   `tl-tile-alarm` border. Observed in the render: "DRIVER NOT RUNNING -- 1 ARMED sub-division not cycling ...
   jack . wta . STALE 32m ago".
R2 -- ALL 43, segmented by account, unattached compact + "no whales attached". subdivision.tiles_all drops the
   >=1-attachment gate; the builder segments by account and marks attached vs compact. Proven:
   test_tiles_all_includes_unattached (ufc, 0 whales, present), test_unattached_is_compact_no_rich_fields,
   test_segments_sort_and_counts (armed -> attached-disarmed -> unattached). Box-scratch (live): 43 total / 28
   attached / 15 unattached.
R3 -- realized = SUM(realized_pnl) over BOOKED terminal closes, labelled "realized net of fees"; count = "N booked
   closes . W-L from settlements"; unbooked counted separately; never open value. realized_pnl is NET of fees
   (entry fees are in the cost basis, settlement.py:129/150; settlement fee=0). subdivision_pnl_all splits booked
   (realized_pnl NOT NULL) vs unbooked (NULL); W-L = won=1/0 (settlements only). Proven: test_subdivision_pnl_all_
   splits_booked_unbooked_and_wl_from_settlements. Box-scratch (live) jack/mlb: realized $10.34, booked 70,
   W-L 38-32, unbooked 6 (70 booked + 6 unbooked = 76 total closes; W-L 38-32 = the 70 booked). Open value is a
   separate figure, never added to realized (builder keeps them distinct).
   LAST-24H = settlements with settled_ts in the last 24h (realized_24h_all, close_source LIKE 'settlement%' AND
   settled_ts >= now-86400); label states the window ("last 24h settled"). Proven: test_realized_24h_all_window_
   edge (exactly-86400 included; 86401 excluded; a whale-exit is NOT a settlement -> excluded). Box-scratch (live)
   jack/mlb: $17.73 / 8 settlements.
R4 -- open = three figures, never conflated: count, at-cost, current value + "N of M priced". count + at-cost from
   live_positions; value + coverage from live_view.value_positions (contracts x held-leg BID over the cached
   marks); no-mark is honest ("no mark" / "0 of M priced"), never $0. Proven: test_open_three_figures_and_coverage
   (2 open, $5.05 cost DISTINCT from $3.00 value, 1 of 2 priced, complete=False). Render: a full "2 of 2 priced"
   AND a no-mark "0 of 1 priced" tile.
R5 -- grafts onto BOX-CURRENT. app.py hunk developed against box 069d7a25 (section 6); the 6 wholesale files have
   box==aad4dea base so they copy clean; heartbeat.py/pm_liveness.html were captured from the box. No repo-wide
   reconciliation attempted.

Tile spec (all present, verified in the render + tests):
  - Header account . category, link /live/{acct}/{cat}: yes.
  - Arm state ARMED/DISARMED/NEVER ARMED/UNAVAILABLE + age: reuses the pm_arm_badge macro (4 states), arm_ts_age.
  - Liveness RUNNING/IDLE/CATEGORY_STARVED/STALE/NEVER + age, banded: reuses pm_liveness colours; page-top alarm
    strip for armed+STALE/NEVER; CATEGORY_STARVED stays amber (non-alarm).
  - Attached whales count + names/short-wallet, right-truncated fixed field, +N: reuses _whale_tag + .wtag/.wn/.wx
    (render: "copies Kingfish +2 of 3", plus a truncated 0x wallet).
  - Realized (R3), last-24h (new reader), open (R4): present.
  - Small-sample caveat: booked closes < 50 (search.DEFAULT_MIN_RESOLVED_FLOOR) -> "THIN <50" next to the W-L,
    with the same intent/threshold as the account page. Proven: test_realized_split_and_thin_flag (76 not thin;
    14 thin).
  - Compact unattached: account . category + arm + "no whales attached -- cannot trade", visibly distinct (dashed).
  - Segmented + sorted (armed -> attached-disarmed -> unattached): builder _rank; render shows Jack then Karen.
  - Dark, no CDN (vendored assets), 1600 + 1280 legible: section 4.
  - Every feed value carries its age; every caveat sits with its number: arm/liveness age chips; THIN beside W-L.


4. SCREENSHOTS
==============
Render harness cc/pm_tiles_render.py (seeds every tile state via build_tiles_context, renders pm_live_list.html
through the app's REAL Jinja env + filters, inlines the CSS for the file:// shot). VIEWED at both widths:
  cc/renders/tiles_v1_1600.png   cc/renders/tiles_v1_1280.png
Shows: red page-top alarm strip (jack/wta STALE); GLOBAL ARMED; Jack (7) + Karen (4) segments; armed-first sort;
liveness chips banded (RUNNING green / IDLE blue / STALE red / CATEGORY STARVED orange); the armed+STALE tile
red-bordered; realized net + booked/W-L + THIN + unbooked; last-24h settled; open 3-figures + "N of M priced"
(incl. a no-mark "0 of 1 priced"); whale "+2 of 3" overflow + a truncated wallet; compact "no whales attached"
tiles with NEVER ARMED / STATE UNAVAILABLE. Both widths 3-5 tiles/row, all values legible.


5. SHIPPABLE FILE LIST (pm_web-only; before = box, after = branch, CR-stripped sha16)
====================================================================================
  file                                   box BEFORE          branch AFTER        ship mode
  -------------------------------------- ------------------- ------------------- ------------------------------
  subdivision.py                         863af1d1522fb364    752e244af0af9e2a    wholesale (base box==aad4dea)
  arm.py                                 60f447207d52694a    b542e9fff3e54e59    wholesale
  web/live_view.py                       8fb7db158e4a5af8    e514a47ad49fd2f7    wholesale
  web/templates/pm_live_list.html        6fd8aad7a1b25d71    7821241509ce44e2    wholesale
  web/static/pm_desk.css                 18454d5690a316ed    8121e8e010e9efde    wholesale (cache-bust ?v=8121e8e0)
  web/templates/pm_shell.html            934c258ce953b18d    d8076b29874f927f    wholesale (?v= bump only)
  web/app.py                             069d7a255a9f4ebc    (branch 70f93d2a)   GRAFT the hunk (section 6) -- NOT wholesale
  heartbeat.py                           57afdcc6c61055e2    (unchanged)         ALREADY ON BOX -- do not ship
  web/templates/partials/pm_liveness.html 9c87b8309271d773   (unchanged)         ALREADY ON BOX -- do not ship
main.py: NOT touched (pm_web-only). No package/venv change; egress unchanged (no new host).
NB: the branch app.py sha (70f93d2a) carries the branch's M5 /pm/arm route (unchanged by me) -- it must NOT ship
wholesale (would leak /pm/arm). Ship the HUNK only, onto box 069d7a25 (which stays is_admin=14, /pm/arm=0).


6. app.py -- THE GRAFT HUNK (onto box 069d7a25; purely additive)
================================================================
ONLY the body of `_load_live_list()` is replaced (the /live route `live_list_page`, the imports, and the top-level
`search` import are UNCHANGED -- byte-identical on branch and box). No import-line edit is needed: the box already
top-imports subdivision/arm/heartbeat/live_view/ui_cache/search; the new body uses a LOCAL `from .. import
heartbeat` so the hunk is additive on both. `git diff aad4dea HEAD -- web/app.py` = 1 file, +28/-4 (only this).

  OLD body (box 069d7a25 lines 926-931, byte-identical to aad4dea):
    with connect() as conn:
        subdivisions = subdivision.list_subdivisions(conn)
    return {"subdivisions": subdivisions}

  NEW body (gathers tiles_all + read_display_all + read_liveness + subdivision_pnl_all + realized_24h_all +
  per-attached-sub attached_whales/live_positions + the mark cache, then live_view.build_tiles_context):
    from .. import heartbeat        # box top-imports it; a LOCAL import keeps this hunk purely additive
    now_ts = int(time.time()); marks, _ = _cache_marks(); floor = search.DEFAULT_MIN_RESOLVED_FLOOR
    with connect() as conn:
        subs = subdivision.tiles_all(conn)
        arm_all = arm.read_display_all([(s["account_id"], s["category"]) for s in subs])
        liveness_present = heartbeat.table_present(conn)
        liveness_by_sub = {(r.account_id, r.category): r for r in heartbeat.read_liveness(conn, now_ts=now_ts)} if liveness_present else {}
        pnl_all = subdivision.subdivision_pnl_all(conn); pnl24_all = subdivision.realized_24h_all(conn, now_ts)
        whales_by_sub, positions_by_sub = {}, {}
        for s in subs:
            if int(s.get("n_whales") or 0) > 0:
                key = (s["account_id"], s["category"])
                whales_by_sub[key] = subdivision.attached_whales(conn, s["account_id"], s["category"])
                positions_by_sub[key] = subdivision.live_positions(conn, s["account_id"], s["category"])
    return live_view.build_tiles_context(subs=subs, arm_all=arm_all, liveness_by_sub=liveness_by_sub,
        liveness_present=liveness_present, whales_by_sub=whales_by_sub, pnl_all=pnl_all, pnl24_all=pnl24_all,
        positions_by_sub=positions_by_sub, marks=marks, now_ts=now_ts, thin_floor=floor)

On the box (schema 20) liveness_present is True (heartbeat tables live). Post-deploy: bump the pm_desk.css served
?v= to 8121e8e0 (already in pm_shell.html); one pm_web restart to load the templates/CSS/app change.


7. MIGRATION
============
NONE. The tile page reads existing tables + the box's already-deployed heartbeat tables (migration 020). No
persisted rollup was added (the 43-tile page is a handful of batched queries, computed per request). Box schema
head = 20 (confirmed). Should a future rollup ever be persisted it MUST be exactly 021 with a head==20 drift-check
first (Phase-1 finding) -- not needed here.


8. TEST BASELINE
================
Full tests/prediction_markets/ (.venv-webtest, -p no:pytest_ethereum): 16 failed = the SAME env-gap baseline
(15x pykalshi engine-driver ModuleNotFoundError + 1x test_search_r1::test_schema_head_is_15 [stale assertion;
local head 19]). 0 non-baseline failures (verified by grep). Delta from my work: +14 new tests (test_tiles_readers
7, test_tiles_view 7) all green; 5 test_live_r3 tests RECONCILED to the redesign (honest-empty wording,
tile-on-create, DB-derived open positions -- not a hardcoded trade count); no safety/honesty check weakened.


9. BOX-SCRATCH -- readers vs LIVE data (cc/pm_tiles_boxscratch_ro.*, mode=ro, 2026-09-07T17:21Z)
==============================================================================================
My reader SQL run against the live prod DB (read-only, no writes): COUNTS total=43 attached=28 unattached=15
armed=8 -> match_phase1 = True (the 43/28/15/8 enumeration). Booked/unbooked split verified against real data
(jack/mlb 70 booked + 6 unbooked = 76; W-L 38-32; realized $10.34; 24h $17.73/8). 20 attached-but-not-armed tiles
render NEVER ARMED/DISARMED. The readers execute cleanly on prod-shaped data and reproduce the tile counts.


10. NOTHING DEPLOYED OR RESTARTED
=================================
Build + test + read-only box reads ONLY. No deploy, no pm_web/engine restart, no arm/halt, no prod-DB write, no
package/venv/systemd change. The engine + both trading accounts + the order path were never touched. Deploy (the
7-file graft above + a pm_web-only restart) is Jack's to authorize.

APPENDIX -- runners / harness (cc/, read-only)
  pm_tiles_boxfetch_ro.*   (box-current app.py region + shas)   pm_tiles_boxscratch_ro.*  (readers vs live data)
  pm_tiles_render.py       (render harness + PNGs at 1600/1280)


================================================================================================================
DEPLOY 6 -- 2026-09-07 (DEPLOYED LIVE; pm_web-only + ONE pm_web restart; engine never touched)
================================================================================================================
Board-authorized 2026-09-07. Shipped the Phase-2 Live Sub-divisions tile page to prod: 6 files wholesale + app.py
GRAFTED (box 069d7a25 + the _load_live_list hunk), ONE pm_web restart via az vm run-command. Engine (trading-corp
PID 232440, ARMED, 8 armed sub-divisions across two accounts) was NOT restarted, reloaded, or touched at any step.
All gates passed; no rollback. DEPLOYED. Deploy target = branch pm-tiles-2026-09-07 @ df38514.

MEASUREMENT RULE applied throughout: box-vs-git shas CR-stripped both sides.

PRE-CHECK (cc/pm_deploy6_precheck_ro.*, 2026-09-07T17:46Z) -- ALL GATES PASS
  1. engine MainPID 232440 NRestarts 0 active; pm_web 232084 active; schema head 20. Arm rows: global +
     8 subs (jack+karen x atp/mlb/ufc/wta) all armed=True latched=False. Heartbeat: 28 rows, all RUNNING/IDLE,
     STALE/NEVER=0. COUNTS total=43 attached=28 unattached=15 armed=8. Journal orders: jack 193, karen 94.
  2. 6 wholesale files CR-stripped16 == box-capture BEFORE (subdivision 863af1d1, arm 60f44720, live_view 8fb7db15,
     pm_live_list 6fd8aad7, pm_desk 18454d56, pm_shell 934c258c). Box app.py = 069d7a255a9f4ebc, is_admin=14,
     /pm/arm=0. NO drift -> no STOP.
  3. Backup = /home/azureuser/pm_deploy6_backup_20260907T175044Z (all 7 files; see the backup NOTE below).
  4. Served pm_desk.css BEFORE = 18454d5690a316ed, shell ?v=18454d56; /live 200, 28 old sparse tiles.

DEPLOY (cc/gen_deploy6.py -> pm_deploy6_apply.sh; app graft via patch --fuzz=0)
  5. Drift RE-GATE at apply time: all 7 box shas re-confirmed == BEFORE (box had not moved). 6 files written
     (base64 -> temp -> CR-strip -> sha16 gate == target -> mv), each verified on the box:
       subdivision.py     863af1d1522fb364 -> 752e244af0af9e2a
       arm.py             60f447207d52694a -> b542e9fff3e54e59
       web/live_view.py   8fb7db158e4a5af8 -> e514a47ad49fd2f7
       web/templates/pm_live_list.html   6fd8aad7a1b25d71 -> 7821241509ce44e2
       web/static/pm_desk.css            18454d5690a316ed -> 8121e8e010e9efde
       web/templates/pm_shell.html       934c258ce953b18d -> d8076b29874f927f
  6. app.py GRAFTED (never wholesale): patch --fuzz=0 applied the _load_live_list hunk (Hunk #1 succeeded at 924,
     offset 128 -- the box's _load_live_list matched the aad4dea context). NEW box app.py reference =
     eeac337d17a84fc7, is_admin=14, /pm/arm=0, py_compile OK, imports with ZERO engine imports (engine_imports=[]),
     _load_live_list present. main.py NOT shipped.
  7. No package/venv/unit changes.
  ** BACKUP NOTE (process defect, remediated -- no impact on the deploy):** the apply backup loop lacked a
     mkdir -p "$BK" before the two ROOT-level files, so subdivision.py + arm.py were overwritten before $BK existed
     (the other 5 backed up fine -- the web/ mkdir created $BK too late for the two). Caught immediately in the
     apply output. REMEDIATED (cc/pm_deploy6_bkfix.*): the pre-deploy originals (== aad4dea == the box-capture
     BEFORE shas) were written into $BK and verified (subdivision.py 863af1d1, arm.py 60f44720). The backup dir is
     now COMPLETE + rollback-ready (7 files). The deploy itself was correct (all target shas verified); the pre-
     deploy state was always recoverable from git aad4dea.

RESTART (cc/pm_deploy6_restart_az.ps1) -- pm_web ONLY
  8. az vm run-command RunShellScript `systemctl restart prediction-markets-web`. pm_web 232084 -> 234853
     active/running. Engine trading-corp 232440 -> 232440 UNCHANGED, NRestarts 0 immediately after. Exit 0.

POST-CHECK (cc/pm_deploy6_postcheck_ro.*, after a poll cycle, 2026-09-07T17:58Z) -- checks 9-16 ALL PASS
  9.  /live 200; new tiles render: tl-tile=43, tl-compact=15, account groups=2 (== the step-1 enumeration
      43/28/15). DB-side counts re-confirmed 43/28/15/8.
  10. Alarm strip ABSENT ("DRIVER NOT RUNNING" occurrences = 0) -- correct, no sub is armed+STALE/NEVER.
  11. All 8 armed subs (jack+karen x atp/mlb/ufc/wta) show ARMED + liveness RUNNING; spot-check of
      /live/kalshi_jack/mlb, /kalshi_jack/atp, /kalshi_karen/mlb sub-pages shows the SAME arm=ARMED +
      pm-lv-st-RUNNING -> tile == sub-page == DB.
  12. jack/mlb realized $10.34, booked 70 + unbooked 6 = 76 == terminal closes 76 (True) -- the R3 split ties out
      to the journal.
  13. 62 pages checked (/, both account pages, /farm + 15 farm categories, all 43 /live/{acct}/{cat}) -> NON-200 = 0
      (/farm/cs excluded, pre-existing). /live styled (pm_desk link present).
  14. Cache-bust: served pm_desk.css = 8121e8e010e9efde (changed from 18454d56, == target); shell ?v=8121e8e0;
      pm.css / pm_desk.css / htmx.min.js all 200 -> no 404, no rollback condition.
  15. Engine MainPID 232440 UNCHANGED, NRestarts 0, ZERO journalctl -p err entries since the restart; order counts
      UNCHANGED (jack 193, karen 94) -- pm_web placed nothing (credential-free, imports no broker).
  16. Zero double-escaped entities on /live (&amp;middot;/&amp;mdash;/&amp;ndash;/&amp;# all 0). Served /live
      rendered at 1600 + 1280 (cc/pm_deploy6_render.py -> renders/deploy6_live_{1600,1280}.png, VIEWED): 43 tiles,
      two account segments, armed-first sort, rich + dashed-compact tiles, no alarm strip, legible at 1280.

FILE LIST -- before(box)/after CR-stripped sha16 (see step 5 for the 6 wholesale; app.py below)
  web/app.py  069d7a255a9f4ebc (M4+farm-search+liveness) -> eeac337d17a84fc7 (+ the _load_live_list tile hunk).
  ** NEW BOX app.py REFERENCE = eeac337d17a84fc7 (is_admin=14, /pm/arm=0) -- supersedes 069d7a25 for the next deploy.**
  heartbeat.py (57afdcc6) + partials/pm_liveness.html (9c87b830): already on box, NOT shipped.

PIDs: pm_web 232084 -> 234853. Engine 232440 UNCHANGED, NRestarts 0 throughout.
Backup: /home/azureuser/pm_deploy6_backup_20260907T175044Z (7 files; rollback = restore + pm_web-only restart).
Skipped/notes: no app.py wholesale (grafted); no main.py; no migration; no package/venv change. The backup-loop
  mkdir defect above was remediated before the restart. Runners: cc/pm_deploy6_{precheck_ro,apply,bkfix,restart_az,
  postcheck_ro,render}.* + gen_deploy6.py + pm_deploy6_app.patch. NOTHING ELSE deployed or restarted; the engine
  and both trading accounts were untouched.
