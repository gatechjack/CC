JACK'S LOG — prediction_markets / pm_web
========================================

Plain-language record of what landed on prod-live and how, written for you (Jack), not for an agent.
If prod-live ever disagrees with the box again, read this top-to-bottom: it tells you what SHOULD be
there, the commit it came from, and the order to put it back. Newest events are at the BOTTOM.

Standing facts that hold for EVERY entry below unless it says otherwise:
  - The trading engine (systemd unit `trading-corp`, MainPID 351422 since 2026-09-12 01:33:35 UTC) was
    NEVER restarted, reloaded, or touched by any of these deploys. It is armed and trading real money.
    Its PID and NRestarts=0 were checked before and after every step and never changed.
  - pm_web is its own systemd unit `prediction-markets-web`, a separate process, loopback-only, credential-
    free, reads only prediction_markets.db. Only pm_web is ever restarted, and only via
    `az vm run-command ... systemctl restart prediction-markets-web` (root via the Azure agent).
  - Box layout: repo root `/home/azureuser/trading_corp`; the package is
    `/home/azureuser/trading_corp/trading_corp/prediction_markets/...`; the pm DB is
    `/home/azureuser/trading_corp/data/prediction_markets.db`.
  - "box == prod-live" always means CR-stripped: compare `git show <ref>:<path> | tr -d '\r' | sha256sum`
    against the box file with `\r` stripped. A raw compare shows phantom drift (git checks out CRLF, the
    box is LF) — this has caused false alarms; always strip CR on BOTH sides.
  - Test baseline throughout this workstream is 22 pre-existing UI-test failures (not ours). Exact command:
    `venv/bin/python -m pytest tests/prediction_markets -q -p no:pytest_ethereum -p no:cacheprovider
    --continue-on-collection-errors` (the `-p no:pytest_ethereum` is mandatory on the box venv — a broken
    web3 plugin crashes collection otherwise). The named set: test_live_r3 x14, test_accounts_m2 x3,
    test_stage2_nav x2, test_stage2_phase3 x2, test_ctx_pagination_fix x1.


BEFORE THE RECONCILE (Deploys 1–7) — pointer only
-------------------------------------------------
The pm_web UI rewrite, the multi-account/multi-category work, the structural matcher (cfb/nfl/…), the
tiles redesign, driver liveness, leg-independence, and the Farm live-whale funnel were built and deployed
across Deploys 1–7. Those are recorded in their own reports under reports/prediction_markets/ (the
PM_UI_HANDOFF_2026-09-03.md file is the running architecture handoff and links the rest). This log does
not restate them. The last of that run was:

  DEPLOY 8 — Farm live-whale badge + promote/demote 409 guards.
    Landed prod-live at commit b1c552b1 ("PM Farm live-whale badge + promote/demote 409 guards
    (Deploy 8 -> prod-live)"). This is the tip the git-truth reconcile (next entry) started from.


2026-09-12 — GIT-TRUTH RECONCILE (git-only; the box was never touched)
---------------------------------------------------------------------
WHAT: A history-only cleanup. The PM deploy line and the MACE deploy line had diverged in git; the box
      was already running the correct code, but `origin/prod-live` in GitHub no longer matched a clean
      linear history. This squashed them into one linear ledger so prod-live is a truthful record again.
PROD-LIVE: b1c552b1  ->  93b5e908  (fast-forward).
HOW: a squashed LINEAR ledger (your 0-merge convention) of the PM line d19c0ab5 + the MACE line ce5b748c
      over base 6a5ed04 (their files are disjoint, so it was conflict-free), plus the third-line
      subdivision.py = f11d755e (a pm-tiles Deploy-7 residue a divergence report had missed; the full-tree
      walk caught it). `main` (61de372) was left untouched.
BOX / ENGINE / pm_web: NOT TOUCHED. This was purely git. A do-no-harm check before and after was IDENTICAL:
      trading-corp PID 351422, 30+ subs armed, 0 latch/trigger, pm schema head 21.
NOT EXERCISED: n/a (no behaviour change).


2026-09-12 — MILESTONE START-TIMES (first cross-category /live feed)
-------------------------------------------------------------------
WHAT: /live can now tell tennis/UFC/soccer games are underway, using Kalshi's public (unauth) `milestones`
      START-TIME endpoint + a clock compare. Start times only; no scores. MLB keeps its own feed as the
      authority (milestone never overrides MLB).
PROD-LIVE: 93b5e908  ->  dfbb140a  (fast-forward).   TAG: pm-milestone-starts-deploy-2026-09-12.
FILES: pm_web only, 5 files, stdlib. NO engine file, NO schema change, NO migration.
SERVICES: engine 351422 untouched. pm_web restarted, PID 332976 -> 360799 (one restart).
TESTS: 155 tests, 22 baseline failures, 0 new.
NOTE: first observed LIVE flip was pending a held game's kickoff at deploy time (the resolution path was
      proven; a real flip just needed a game to start).
PER-FILE SHAS: see the milestone deploy report/manifest (this log records the commit + counts).


2026-09-12 — DEPLOY 9: PER-WHALE ROSTER + DETACH
------------------------------------------------
WHAT: the "Copies these whales" panel became a per-whale live-copy ROSTER (journal-filtered record,
      dollars-first, on-roster vs formerly-live), with a DETACH control and drawer drill-through.
PROD-LIVE: dfbb140a  ->  afbcfbea (code)  ->  8fdded34 (docs).   TAG: pm-roster-deploy9-2026-09-12 (on the
      code commit afbcfbea).
FILES: pm_web files + ONE engine-shared file, subdivision.py, ADDITIVE-ONLY (112 insertions / 0 deletions
      = 3 new functions; no existing function changed, so the engine's behaviour is unchanged — it loads
      the new subdivision.py on its own next restart).
SERVICES: engine 351422 untouched. pm_web restarted, PID 360799 -> 363574 (one restart).
BOX == PROD-LIVE: 42/42 web files + subdivision.py, verified.
YOUR RULING implemented: Detach is OWNER-OR-ADMIN — Karen may detach from her OWN sub-divisions, Jack from
      all. This introduced `authz.can_act_on_account`, the first use of owner_identity to gate a WRITE
      (Promote/Attach stay admin-only). The engine's per-cycle roster query is attachment-gated, so Detach
      stops new copies within one ~7s cycle with no restart; open positions ride to settlement.
TESTS: 20 new, 0 regressions.
NOT EXERCISED ON PROD: the FIRST real Detach. When you do it, expect "placed 0" for that whale on its next
      heartbeat.


2026-09-12 — DEPLOY 10: LIVE FIXES (Items 3 + 1 + 2)
---------------------------------------------------
WHAT: three /live-page fixes, in the order you ruled to build them.
      Item 3 — mark-cache render: titles persist across a failed poll, marks are MERGED not replaced (a
        stale value shows with its own age, amber past the threshold), a cold cache reads "marks loading"
        (not "no mark"), and no non-MLB label is ever a raw ticker.
      Item 1 — the LIVE tile is a constant 480px: one FEATURED game (closest to settling) + other underway
        games as compact chips + "+N more live".
      Item 2 — every non-MLB position/trade reads as matchup + signed shorthand (e.g. "SPR -6.5 MIZZ")
        through ONE shared formatter, on THREE surfaces (the /live positions table grouped by game with the
        SIDE column dropped, the trade drawer, and the Farm whale paper-trade list). You expanded this
        mid-build to cover the drawer + the Farm paper list — same function, no second implementation,
        fail-closed everywhere.
PROD-LIVE: 8fdded34  ->  c339437b (code)  ->  78d90e54 (docs).   TAG: pm-livefix-deploy10-2026-09-12 (on the
      code commit c339437b).
FILES: 9 pm_web files (the union of the three items). app.py IS in the set (the Farm-paper enrich).
      subdivision.py is NOT (no engine-shared file this time). No migration. CR-sha16 before -> after:
        web/ui_cache.py                              dee87281 -> 1919ca19
        web/app.py                                   ef1dec75 -> e3962488
        web/live_view.py                             51f91696 -> ff6c3c03
        web/templates/pm_live_subdivision.html       cea30f34 -> 98ffb7a2
        web/templates/partials/pm_subs_event.html    3dd74d8e -> 51ebf8ce
        web/templates/partials/pm_trade_drawer.html  fc251c6d -> 36edbd52
        web/templates/partials/pm_paper_trade_rows.html 2c6f136b -> 7d194817
        web/static/pm_desk.css                       c5efb60a -> 92a1ef2e   (cache-bust ?v=92a1ef2e)
        web/templates/pm_shell.html                  7e73fe61 -> c134af36
SERVICES: engine 351422 untouched. pm_web restarted TWICE, PID 363574 -> 365664 -> 365841. The second
      restart was deliberate: to catch the cold-cache "marks loading" window with a tight fetch loop
      (the first fetch had landed just after the first poll completed). Both restarts were pm_web only.
BOX == PROD-LIVE: 63/63 tracked web files, verified BEFORE and AFTER.
TESTS: 200 tests, 22 baseline failures, 0 new.
PRE-DEPLOY VALIDATION (your ruling to confirm the away/home order): a board-authorized read-only run pulled
      15 real held cfb/nfl/mlb tickers off the box and cross-checked my decode against Kalshi's own event
      sub-title order AND the Polymarket slug — 15/15 matched Kalshi, 5/5 (the ones with a Poly slug)
      matched {away}-{home}. No label changes needed.
HONEST NOTE: raw "KX" text still appears in the trade drawer's Ticker provenance field + the drawer's
      describe_market "Market" line for non-structural categories (atp/ufc/…). Total-KX is IDENTICAL
      before/after (deploy is neutral); the positions-table LABELS are clean. Extending the label rule to
      the non-structural drawer is filed as an off-box backlog item.
PROCESS CORRECTION #1: during this deploy I ran several box steps with ad-hoc `ssh`/`az`/`scp` instead of a
      written .ps1 runner. You corrected me ("reread command-paste-rule"). See the standing rule at the
      bottom.


2026-09-12 — DEPLOY 11: CONTRACT SIZING FROM THE UI + MIGRATION 022
------------------------------------------------------------------
WHAT: you can now change a sub-division's contracts-per-copy from its /live/{account}/{category} page (e.g.
      5 -> 1) with NO restart. The engine already reads pm_subdivision.contracts every ~7s cycle; this makes
      the page's standing sentence true. A server-rendered confirm (works with JS off) states the change and
      a per-copy cost estimate from recent fills, then a POST writes the value and an audit row in one
      transaction. The header shows "N contracts / copy · set by <who> · <age>"; the drawer footer lists the
      last 5 changes.
PROD-LIVE: 78d90e54  ->  ce52ef3c (code)  ->  489a9ddb (docs).   TAG: pm-sizing-deploy11-2026-09-12 (on the
      code commit ce52ef3c).
FILES: pm_web files + a NEW pm-side module sizing.py (imported only by web/app.py — NOT engine-shared) + the
      engine-shared db.py, ADDITIVE-ONLY (22 insertions / 0 deletions — only the new migration was appended;
      no existing migration/function changed). CR-sha16 before -> after:
        db.py (engine-shared, additive)              3b5ae50d -> 8dc457d2
        sizing.py (new pm-side)                       (new)    -> 248a1800
        web/app.py                                   e3962488 -> 53106001
        web/templates/pm_live_subdivision.html       98ffb7a2 -> 7d686aeb
        web/templates/partials/pm_sizing_control.html (new)    -> e96a94fc
        web/templates/partials/pm_sizing_confirm.html (new)    -> d284b09f
        web/templates/partials/pm_trade_drawer.html  36edbd52 -> 57b3ab9c
        web/static/pm_desk.css                       92a1ef2e -> 422c45ec   (cache-bust ?v=422c45ec)
        web/templates/pm_shell.html                  c134af36 -> f729755a
MIGRATION: 022 = a new table pm_subdivision_sizing_audit (the who/from/to/when audit). Schema head 21 -> 22.
      pm_web-owned: the engine never reads or writes this table. It is a PURE CREATE (table + index, no ALTER,
      no data change).
  MIGRATION MECHANISM (how the pm DB is migrated on the box — the M1 finding): via db.init_db(), which is
      version-gated (it applies only versions above the current head). db.init_db is invoked by pm_cli — your
      crons (`pm_cli paper-poll` every 30 min, refresh/adjudicate/rollup at 05:00/05:40/05:50) each call it.
      pm_web does NOT migrate on startup (it only starts the poller); the engine connects to the pm DB but
      never calls the pm db.init_db (its own init_db is the legacy trading_corp.db). So the engine cannot
      refuse a head ahead of its code — confirmed live: after the migration it read the head-22 DB with its
      old db.py, zero errors. For this deploy I applied 022 by calling that same db.init_db via a runner
      (not hand-run DDL), after grafting the new db.py, and verified: head 21 -> 22, the audit table exists
      and is empty, pm_subdivision.contracts unchanged for every row (all 5), and the config-table row counts
      (pm_subdivision 44 / pm_account 2 / attachment 60 / watchlist 786) unchanged.
SERVICES: engine 351422 untouched. pm_web restarted, PID 365841 -> 367649 (one restart). journalctl -p err
      for both services since the migration: none.
BACKUP (this is your restore source for the sizing files + the pre-migration DB):
      /home/azureuser/pm_deploy11_backup_20260912T181850Z/
        - the 6 overwritten files (db.py, web/app.py, pm_live_subdivision.html, pm_trade_drawer.html,
          pm_desk.css, pm_shell.html), each verified backup == box == prod-live base at backup time.
        - prediction_markets.db.snapshot (392,851,456 bytes) — a consistent online .backup of the pm DB
          taken BEFORE the migration.
BOX == PROD-LIVE: before = 63/63 web files at 78d90e54; after = 67/67 (65 web files including the 2 new
      partials, + db.py + sizing.py) at ce52ef3c.
TESTS: 33 new sizing tests; 2 head-tracking tests updated for head 22; full suite 22 baseline failures, 0 new.
YOUR RULINGS implemented:
      - owner-or-admin may LOWER the count; only admin may RAISE it (the SECOND use of can_act_on_account).
        Verified live by GET-only: karen lower-own 200 / raise-own 403 / on a jack sub 403; admin 200 both;
        no identity 403; bounds 0 and 51 -> 400, 1 and 50 -> 200.
      - bounds are an integer 1..50, defined as constants in sizing.py — a UI ruling, NOT engine config
        (the engine's per-order/daily USD caps are unrelated and were not touched).
NOT EXERCISED ON PROD: the FIRST real sizing change. No POST was run against prod. When you lower a sub from
      the header, expect the driver's next ~7s cycle to size new copies at the new count, and the audit row
      to appear (and the "set by jack · <age>" header line + the drawer last-5) with your identity. Open
      positions are unchanged.
PROCESS CORRECTION #2: during the migration discovery I pulled one traceback with an ad-hoc `ssh` through
      the shell instead of a .ps1 runner. Read-only and benign, but the same slip as #1. See the standing
      rule below.
FINDING (handed off, not a bug): right after the restart the driver heartbeats looked stale (~189s). I
      stopped and checked before continuing: it was the engine's own KXMLBGAME index refresh (899 games, a
      ~15-minute heavy fetch) — heartbeats pause during it, then burst (they reset to 0s while I watched).
      The engine's cadence is bursty by nature; this was not caused by the deploy. The shard-balance 401
      "timestamp expired" and /positions 429 lines in its log are known pre-existing transient warnings the
      driver handles fail-safe (skip the cycle, never place blind).


STANDING RULE (from corrections #1 and #2)
------------------------------------------
Every box touch — READ-ONLY included — goes through a written, ASCII-validated .ps1 runner, executed as
`powershell -ep bypass -f .\NAME.ps1`. No ad-hoc `ssh`/`az`/`scp` from the shell, ever. Multi-file grafts
use scp + tar (not base64 heredocs). This is command-paste-rule; both slips above were routing box reads
outside that channel.


OTHER FINDINGS HANDED TO OTHER WORKSTREAMS / THE BACKLOG
-------------------------------------------------------
  - The engine heartbeat "stall" is really the periodic index refresh (above) — a monitoring note, not a bug.
  - The 22 stale UI-test failures are the standing test baseline; they were green on the retired UI branch
    and are not caused by any of these deploys. Fixing/retiring them is filed.
  - Non-MLB LIVE still shows start-times only (no scores). The milestones sweep is the proven half; a real
    scoreboard needs the live_data score line + a scoreboard partial. CFB half-game/quarter markets and a
    couple of team-map gaps (San Diego State, Villanova) are safe-misses, filed.
  - The non-structural drawer label leak (Deploy 10 note) — extend the shared formatter + describe_market to
    atp/ufc/cs2/fed/soccer, off-box.
  - The next schema migration number is 023 (the attach/detach event-log, if built, would take it).


IF PROD-LIVE DIVERGES FROM THE BOX
----------------------------------
1. Find out what SHOULD be there. prod-live is truth; the current tip is 489a9ddb (Deploy 11 docs), the
   code it carries is ce52ef3c (tag pm-sizing-deploy11-2026-09-12). Compare, CR-stripped:
     for the pm_web tree + db.py + sizing.py, on the box compute
       hashlib.sha256(open(path,'rb').read().replace(b'\r',b'')).hexdigest()
     and compare to `git show ce52ef3c:<path> | tr -d '\r' | sha256sum`.
   (This is exactly what the runner cc/pm_deploy11_reverify_ro.ps1 does; it reported 67/67 at deploy.)
   ALWAYS strip CR on both sides — a raw compare shows false drift.

2. Engine-shared files, and why they are safe:
     - db.py       — shipped in Deploys 9 and 11. Changes were ADDITIVE-ONLY (new functions / a new
                     migration appended; nothing existing changed). The engine loads it on its OWN next
                     restart and its behaviour is unchanged.
     - subdivision.py — shipped in Deploy 9. Also additive-only (3 new functions, 0 deletions).
   Because they are additive-only, an engine at an older copy of these files still runs correctly; you do
   NOT need to restart the engine to "match" a db.py/subdivision.py change.

3. Restore ORDER (never the engine):
     a. Restore the files. For the sizing feature's files + the pre-migration DB, the backup is
        /home/azureuser/pm_deploy11_backup_20260912T181850Z/ . For the authoritative content of ANY pm_web
        file, `git show ce52ef3c:<path>` (or the relevant tag) is the source of truth.
     b. Restart pm_web ONLY: `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id
        RunShellScript --scripts "systemctl restart prediction-markets-web"`.
     c. Verify old pages return 200 and are styled, and that the engine PID (351422) and NRestarts are
        unchanged.
   The migration has no down-path. If you ever need to undo it: DROP pm_subdivision_sizing_audit and reset
   the schema head to 21 through db.init_db's mechanism, OR — only if the engine has written nothing since —
   restore prediction_markets.db.snapshot from the Deploy-11 backup. If neither is clean, LEAVE head 22 with
   the empty table in place; it is harmless (nothing reads it but pm_web).

4. NEVER restart or touch the engine (trading-corp / MainPID 351422) to fix a pm_web divergence. pm_web is a
   separate, credential-free, read-only service; nothing about a pm_web file or the pm_web-owned audit table
   requires an engine bounce.
