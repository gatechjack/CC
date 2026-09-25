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


════════════════════════════════════════════════════════════════════════════════════════════════════
SESSION 2026-09-13 — FOUR PM DEPLOYS AFTER DEPLOY 11 (for a git-truth reconciler)
════════════════════════════════════════════════════════════════════════════════════════════════════
prod-live walked:  489a9ddb -> 68af1b94 -> e02d73ea -> 614313bb -> 5e03b7a2.
ALL FOUR ARE CLEAN FAST-FORWARDS (git merge-base --is-ancestor confirmed each consecutive pair).
SHARED TRIO UNTOUCHED the whole span: git diff --name-only 489a9ddb 5e03b7a2 -- trading_corp/main.py
  trading_corp/agents/data_exec.py trading_corp/brokers/robinhood.py  == EMPTY. Every runtime file below is
  under trading_corp/prediction_markets/ (the PM division).
★ ENGINE PID CHANGED THIS SESSION. The standing fact at the top ("MainPID 351422 ... NEVER restarted") held
  THROUGH Deploy 11. The FIRST deploy tonight (heartbeat b1+stagger) INTENTIONALLY restarted the engine:
  351422 -> 370246. The three deploys AFTER it are pm_web-only and left 370246 untouched (PID + NRestarts=0
  checked before/after each).

[1] HEARTBEAT-STALL FIX — b1 (_SETTLED_LOOKBACK_SEC 160d->2d) + cross-account refresh stagger (2026-09-12)
  prod-live 489a9ddb -> 68af1b94.  FAST-FORWARD: clean.  TAG: none.
  WHAT: closes a ~189s recurring heartbeat/trading gap (Kalshi rate-limit backoff on the shared IP, not a
        disconnect). Shrinks the settled-lookback and staggers the per-account refresh so they don't collide.
  FILES (ENGINE, division-scoped): trading_corp/prediction_markets/live_driver.py  (the ONLY runtime file)
        + tests/prediction_markets/test_refresh_stagger_and_lookback.py + 2 report docs.  NOT the shared trio.
  SERVICES: ENGINE restart — the only deploy this session that BOUNCED EVERY DIVISION.  engine 351422 -> 370246.

[2] MILESTONE START-TIMES REACH CFB — Item A / A3 consumer routing (2026-09-12)
  prod-live 68af1b94 -> e02d73ea.  FAST-FORWARD: clean.  TAG: none.
  WHAT: milestone start-times weren't reaching cfb; milestone-eligible is now derived as
        LIVE_CAPABLE minus _HHMM_AUTHORITATIVE({mlb,cs2}).
  FILES (pm_web, division-scoped): trading_corp/prediction_markets/web/live_view.py  (the ONLY runtime file)
        + test_milestone_structural_fallback.py + test_milestones.py + 2 report docs.  NOT the shared trio.
  SERVICES: pm_web-only restart -> PID 372688.  Engine 370246 untouched.

[3] ANALYZE UPGRADE — deterministic promotion-judge + Sonnet narrator + MIGRATION 023 (2026-09-13)
  prod-live e02d73ea -> 614313bb.  FAST-FORWARD: clean.  TAG: pm-analyze-upgrade-deploy-2026-09-13.
  WHAT: Haiku->Sonnet one-sentence narration OVER a deterministic, stored, sortable score (scoring.py); a
        loss-grounding Phase-A honest windowed ROI.
  FILES (division-scoped): scoring.py (NEW), analyze.py, db.py, loss_grounding.py, web/app.py
        + tests (test_scoring, test_analyze_score, test_rung3_observability) + 2 report docs.  NOT the shared
        trio.  db.py is engine-shared ONLY as the PM DB schema module (migration 023 = additive CREATE TABLE);
        it is not main/data_exec/robinhood.
  MIGRATION: 023 = pm_whale_score (the stored promotion-judge score).  head 22 -> 23.  Pure additive CREATE
        TABLE + index; pm_web-owned (engine never reads it).  ★ APPLIED BY THE 01:30Z paper-poll CRON before my
        manual step (see the STANDING DEPLOY FACT below); the manual step then read head==23 and correctly
        STOPPED.  Verified clean vs the head-22 backup snapshot (config byte-identical, table empty, engine
        untouched).  On-box backup: /home/azureuser/pm_analyze_backup_20260913T011517Z/.
  SERVICES: pm_web-only restart -> PID 376953.  Engine 370246 untouched (it runs its old db.py in memory
        against a head-23 DB = a non-event; it never reads pm_whale_score).
  LIVE PROOF: first Analyze on 0x684baa57c3/mlb returned model=claude-sonnet-4-6 read from the API response
        (NOT the config), cost $0.00275 — the Sonnet swap is live.  It exposed that whale (the most-copied,
        222 fills) as a loss-omission MIRAGE: closed-table 196W/17L looks 92%, but 80% of its losses were
        dropped at 36% coverage -> tier INSUFFICIENT_DATA.
  TESTS: 27 new; 0 new regressions vs the pre-existing UI-test baseline.

[4] PROSPECTS DISPLAY — the stored score on the whale lists (2026-09-13)
  prod-live 614313bb -> 5e03b7a2.  FAST-FORWARD: clean.  TAG: pm-prospects-display-deploy-2026-09-13.
  WHAT: the tier + sort number (tier-capped, trust-flagged) now shows and sorts on Prospects (a sortable JUDGE
        column), the Watchlist, and the /live roster.  Un-analyzed reads "not analyzed", never a 0.
  FILES (pm_web, division-scoped): scoring.py, web/app.py, web/static/pm_desk.css (?v= 422c45ec -> 2a290250),
        web/templates/pm_macros.html, web/templates/pm_shell.html, and 4 partials (pm_prospects_rows,
        pm_watchlist_rows, pm_whale_roster, pm_analyze_result) + tests + 1 manifest doc.  NOT the shared trio.
  MIGRATION: NONE — reads the already-live pm_whale_score.
  SERVICES: pm_web-only, TWO restarts — the initial deploy (PID 379568) then a same-session redeploy for a
        Watchlist live-update fix (PID 381803).  Engine 370246 untouched both times.
  BOX == PROD-LIVE: 9/9 runtime files at 5e03b7a2 (sha256, CR-stripped).  On-box backups:
        /home/azureuser/pm_pd_backup_20260913T032138Z/ (9 files) and pm_pd_backup2_20260913T063200Z/ (4 files).
  ACCEPTANCE (proven on the deployed page): 0x684baa57c3/mlb renders INSUF DATA + flagged on the /live roster
        and sorts BELOW a PROMOTE — the list cannot make the loss-omission mirage look good.
  TESTS: 40 new; 0 new regressions vs the pre-existing UI-test baseline.


★ STANDING DEPLOY FACT — PM MIGRATIONS SELF-APPLY VIA THE pm_cli CRONS (read before you graft a db.py)
------------------------------------------------------------------------------------------------------
Extends the Deploy-11 migration-mechanism note into a warning.  PM migrations apply through db.init_db, which
the pm_cli crons call (paper-poll every 30 min; refresh/adjudicate/rollup daily).  So the MOMENT you graft a new
db.py, THE NEXT CRON TICK APPLIES THE MIGRATION ON ITS OWN — you do not control the timing.  Tonight the 01:30Z
paper-poll applied grafted migration 023 BEFORE my manual apply; my manual step read head==23 and correctly
STOPPED (drift-check) instead of double-applying.  EXPECT THE CRON TO WIN THE RACE.  The self-renumber-at-apply
drift-check (assert box head == expected; renumber to box-head+1 on collision) is what makes that safe, and it
is now the DEFAULT for every future PM migration.  After a cron-applied migration, verify against the pre-graft
DB snapshot (config byte-identical, table created + empty, engine untouched), not the manual apply's own
before/after.
MIGRATION NUMBERS: 023 landed this session; head is now 23; the NEXT is 024.  ★ ITEM B (an engine workstream)
also has a candidate claim on 023 — the collision is real; whoever lands second renumbers to 024 (contiguous).


★ TWO CODE TRAPS (a reconciler or a future agent will want to "fix" these back — DO NOT)
----------------------------------------------------------------------------------------
1. web/static/pm_sort.js: its FIRST click sorts ASCENDING, even though its own comment says "first click =
   descending" — the COMMENT IS WRONG (the code is `asc = dir !== "asc"`, true on the first click).  That is why
   the Prospects JUDGE column emits a NEGATED data-sort-value ({{ -r.score.sort_value }}): with an
   ascending-first sorter, negating puts the best tier (PROMOTE) at the top and un-analyzed at the bottom.
   Remove the minus and the list silently INVERTS — the INSUFFICIENT_DATA mirages float to the top — and it
   LOOKS like it works.  Only a descending-first fix to the shared pm_sort.js should retire the minus.
2. The score_badge OOB id is pm-scoreb-{wallet} — WALLET-ONLY, on purpose.  A category-bearing id put the
   lowercase slug in an attribute and regressed the F-3 casing guard test_category_page_knows_its_category
   (every lowercase category must be a URL path segment).  A /farm/{cat} or /live/{acct}/{cat} page is
   single-category, so the wallet alone is a unique OOB anchor — the category is redundant.


SELF-CORRECTIONS THIS SESSION (distrust the instrument — the habit that keeps this honest)
------------------------------------------------------------------------------------------
  - An unbounded traceback grep over a huge append log read 28,855; the bounded/correct count was 3.  The
    instrument over-counted, not the system.
  - narrate() was going to report model = PM_ANALYZE_MODEL (what we REQUESTED); a silent Haiku fallback would
    pass that identically and the model check would prove nothing.  Fixed to read response_metadata.model (what
    the API RETURNED) + a test that a Haiku answer surfaces as Haiku.
  - Tonight's post-check FALSE-FAILED the mirage acceptance: it sliced "to the next data-whale", but the roster
    renders TWO data-whale attrs per whale (the <div> and the name <button>), so it stopped inside the same
    whale, before the badge.  The PAGE was right; the CHECK was wrong.  Re-sliced on `class="wr" data-whale=`
    before drawing any conclusion.


OPEN AFTER THIS SESSION
-----------------------
  - The b1+stagger RE-MEASUREMENT (deploy [1]) is DUE AND UNTAKEN — confirm the ~189s gap actually shrank.
  - Engine Items B and C (Item B carries the competing 023 migration claim above).
  - Small-rungs cleanup.
  - Pre-existing nits surfaced (not regressions): legacy kalshi_copy_trader "Apify FEED DOWN" (engine legacy
    halted-but-enabled loop); db.py:connect() sets WAL before a busy_timeout, so a transient "database is locked"
    can hit init_db (one old traceback in pm_poll.log).


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
1. Find out what SHOULD be there. prod-live is truth; the current tip is 5e03b7a2 (tag
   pm-prospects-display-deploy-2026-09-13). Through Deploy 11 the code was ce52ef3c (tag
   pm-sizing-deploy11-2026-09-12); the FOUR deploys after it are in the SESSION 2026-09-13 block above, each
   with its prod-live before/after sha, its tag (analyze/prospects; the heartbeat + milestone ones are
   untagged), and its on-box backup dir. Compare, CR-stripped:
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


2026-09-13/14 — LEGACY PM RETIREMENT, PHASE 6: ENGINE-SIDE CODE REMOVAL (★ this one DID restart the engine)
----------------------------------------------------------------------------------------------------------
★ DIFFERENT WORKSTREAM from the pm_web deploys above. This is the retirement of the RETIRED legacy PM
divisions (poly_kalshi arb/copy, kalshi tail/tb/llm/weather/crypto/scout/observer/copy, resolvers) from the
trading engine's own code — NOT a pm_web change. So unlike every entry above, the ENGINE WAS RESTARTED. The
"engine never touched / MainPID 351422" standing fact at the top of this log does NOT apply to this entry.
The full record is reports/platform/LEGACY_PM_RETIREMENT_2026-09-12.md §18 (branch legacy-pm-retire-2026-09-13).
WHAT: removed the legacy strategy LOOP WIRING + loop/helper defs from trading_corp/main.py. NO file deletions,
      NO web changes — those are a deferred "transitive closure" problem (see traps below). Jack ruled this
      "Option 1": deploy the proven graft only.
PROD-LIVE: fcbcd4a7  ->  8f35f254  (clean fast-forward, single commit).
MAIN.PY: 6210 -> 3923 LOC, 2,284 lines removed, PURE DELETION (0 non-blank additions; git diff = deletions +
      blank-collapse only). Survivor wiring counts UNCHANGED: bitunix 196, mace 119, pm_live_driver 4,
      scheduled_pm_live_loop 2, shard_snapshot 1, pmcc 2, pead 2, donchian 3. New CR-stripped md5 c15b4de6.
      Also config: poly_kalshi_mlb auto_execute true->false (belt-and-suspenders; the loop wiring is gone).
DEPLOY: box is NOT a git repo -> scp file-graft, drift-gated (live == pre-graft fcee5e81 before write).
      Box backups: /home/azureuser/trading_corp/{trading_corp/main.py,config/strategies.yaml}.bak_legpm_20260913T232311Z.
ENGINE: RESTARTED via the canonical restart_tc.ps1 (az-root systemctl restart trading-corp). MainPID
      370246 -> 397094, boot 2026-09-14 00:25:13Z, NRestarts 0. Post-restart gate PASSED (Jack's split-signal
      rule): PM LIVE DRIVER WIRED for both accounts at 00:25:34 (the exact thing the 2026-09-04 failure had
      deleted) + a real dry_run=0 order (id 820) placed 00:28:53Z. Siblings UNCHANGED: pm_web 381803,
      sfp-card-watcher 656, kcv2 observer 679. Boot clean; NO more kalshi_copy Apify FEED DOWN (loop gone).
TIMERS: the 4 legacy PM timer units (watchlist-stats/-deep, pm-watchlist-deep, pct-pruner) + their 4 companion
      .service files removed (az-root). Backup /root/legpm_timer_unit_backup_20260914T010512Z. Survivor timers
      pead-earnings-watcher / rh-relogin / tc-audit-reality untouched.
EARLIER PHASES (2+3, 2026-09-13): 8 strategies.yaml enabled/auto flags flipped false (hot config disables) and
      reconciled onto prod-live in the Phase-3 reconcile commit (fcbcd4a7 line). Apify billing fully ceased.
REVIVAL: git checkout fcbcd4a7 -- trading_corp/main.py restores the loop wiring.


2026-09-14 — LEGACY PM RETIREMENT, PHASE 7: kcv2 DB DROP — GATES RULED, STOPPED BEFORE THE DROP
----------------------------------------------------------------------------------------------
WHAT: the plan to DROP the four kcv2_* forward-logger tables (~3.15 GB) from data/trading_corp.db (5.31 GB) —
      the SAME DB the live PM division reads arm state from. NOTHING WAS DROPPED. Two gates ruled, then Jack
      stopped the session before any destructive step. Resume guide: reports/platform/PHASE7_RESUME_HANDOFF_2026-09-14.md
      + tracking-doc §19.
ENGINE/BOX: NOT TOUCHED this phase (all read-only + a local archive restore). Engine 397094, pm_web 381803,
      sfp 656 unchanged; kcv2 observer 679 STILL RUNNING + writing (it stops FIRST when the phase resumes).
GATE A (archive is single-copy): integrity PASS (files byte+SHA256 == Phase 5: prod a4eef50f / lab cff8a469)
      AND restorability PASS (a FRESH restore today rebuilt all 4 tables — quotes 12,034,120 etc. — spot rows
      byte-identical). Jack ruled an OFF-DEVICE verified copy is required before the drop. ★ BLOCKED: this
      machine has ONE physical disk, so option (b) "second physical drive" has no target. Next session needs a
      UNC path on another machine (preferred, no hardware) / a USB / or Azure blob (off-site, best long-term).
GATE B: DROP ONLY, NO VACUUM. Measured: engine (397109) + sfp (656) hold the DB open, WAL, freelist_count=0,
      auto_vacuum=0 -> a full VACUUM needs the ENGINE STOPPED, not merely PM-disarmed. ★ THE FILE STAYS ~5.31 GB
      AFTER THE DROP (~3.15 GB free internal pages, reused later). THE DATA IS RETIRED REGARDLESS — do NOT read
      the unchanged file size as a failed drop.
ARM-COUNT TRUTH (a proof obligation that already confused a count): 31 agent_state arm rows in trading_corp.db
      (the DROP TARGET; all armed / 0 latched) vs 44 pm_subdivision rows in prediction_markets.db (UNTOUCHED).
      After the drop, prove THE 31 + the poly_kalshi persist-halt row byte-identical, in trading_corp.db.


★ RETIREMENT TRAPS A GIT-TRUTH RECONCILER WILL WANT (for the 2026-09-13/14 entries above)
-----------------------------------------------------------------------------------------
  - THE TRANSITIVE CLOSURE OF FILE DELETIONS WAS NEVER COMPUTED. main.py imports 0 of the legacy modules at
    module level, but the .py files remain (Phase 6 removed WIRING only). KEEPERS proven by import-graph:
    _weather_math (survivor path_logger/logger.py:31 imports kalshi_quote_dollars from it) and
    kalshi_crypto_v2_observer (while PID 679 runs). Plus 6 blocked shared files with live importers, ~25 legacy
    tests/scripts, and the woven ~2,500 LOC web/data.py dashboard. Do NOT bulk-delete "legacy" files.
  - The poly_kalshi_mlb persist-halt row (agent_state agent='strategy_state') is now REDUNDANT, not protective
    (the loop wiring is gone) — but it is NOT to be removed.
  - The box's NOPASSWD sudo allowlist is INERT (shadowed by a trailing (ALL) ALL) — `sudo -n systemctl` reports
    nothing changed; every root write goes via `az vm run-command` and must verify actual state after.
  - Arm state lives in data/trading_corp.db agent_state — column is `agent` NOT `actor`; read value_json.
    `last_poll_ts` updates only on success, so a failing loop looks dormant — prove liveness from activity, not
    a success timestamp.
  - APIs: Apify was the real paid legacy driver (now stopped); Finnhub dead/free; Anthropic + Kalshi/Polymarket
    keys are SHARED with live PM and MUST NOT be cancelled. The Polymarket USDC drain must GATE key removal.


2026-09-17 — READ-ONLY INVESTIGATION (whale-exit value) — NOTHING DEPLOYED
--------------------------------------------------------------------------
WHAT MOVED ON PROD-LIVE THIS SESSION: nothing. Zero commits, zero branches, no box write, no restart.
prod-live stayed at 1b667406 the whole session. There is NO fold to find dated 2026-09-17 — don't hunt for one.

TWO STALE-STATE CORRECTIONS a reconciler would otherwise trip over:
  1. prod-live had already MOVED before this session, from workstreams this session did NOT run. The chain from
     this log's last recorded tip to now: the ITF tennis category deploy landed 3dd15c10 (tag
     pm-itf-deploy-2026-09-16), then THREE legacy-PM RETIREMENT tranches took it forward —
     tranche 1 (pure deletion of 49 orphaned legacy .py, transitive-closure proven) = ed6d9b83,
     tranche 2a (web/routes.py legacy-route graft, +7 modules) = ae1fef05,
     tranche 2b (shared-file grafts: main.py factory / kalshi.py / web/data.py, +4 modules) = 1b667406.
     ALL CLEAN FAST-FORWARDS. **CURRENT TIP = 1b667406.** Anyone holding 3dd15c10 or older is on a stale base
     and can fast-forward straight to 1b667406.
  2. A local branch literally named `prod-live` existed at 7220e32f (worktree cc-prodlive-cp7-wt), nine-plus
     deploys behind origin's 1b667406 — strictly BEHIND, not diverged (0 commits ahead), NOT tagged, just stale
     lag. It was DELIBERATELY REMOVED this session (worktree + local branch both gone), because a local
     prod-live sitting far behind READS AS DRIFT — and this platform already burned a full session on a
     phantom-drift alarm that was only a CRLF artefact; a real stale ref looks exactly like that false alarm.
     It was cleared on purpose, not lost. There is now NO local prod-live ref; origin/prod-live @ 1b667406 is
     the sole source of truth. local == origin.

WHAT THE INVESTIGATION FOUND (recorded so it isn't re-measured from scratch):
  - Whale-exit copying (Option D) is LIVE, and has been since ~2026-09-04. The backlog's "designed, ruled,
    never built" was WRONG and is corrected at source (prediction-markets-backlog memory). detect_exit_signals
    is wired into the live loop (live_driver.py:1366): a /positions size-reduction confirmed by an /activity
    SELL fires a reduce_only exit.
  - CAUGHT 8 of 450 filled positions (~1.8%), net +$2.00, behaving as INSURANCE (loss-cuts outweigh small
    win-forfeits). MISS-RATE 9 of 390 held-to-settlement (2.3%) at 100% reconstruction coverage (zero
    unknowns); catching all 9 would have been worth ~+$7.40, 7:2 for loss-cuts. Small at current 3-10 contract
    sizing over 2.5 weeks.
  - RULED: LEAVE THE TRIGGER. Revisit condition is SIZING GROWTH, not time (the loss-cut value scales linearly
    with per-copy size). Watch whale 0xb4eea1c8 — 4 of the 9 misses are his.

LESSON (2nd instance this week): THE BACKLOG IS A RECORD OF DECISIONS, NOT A RECORD OF STATE. "Never built"
aged badly and nothing updated it until someone checked against the box. (1st instance: the M5 app.py hazard —
a warning repeated in every deploy prompt for a week that turned out to be a regex artefact.) VERIFY A BACKLOG
CLAIM AGAINST THE RUNNING CODE BEFORE BUILDING ON IT.

OPEN (all Jack's; NO code work pending from this session):
  - enabling ITF: create + attach + arm an ITF sub-division + add the itf_moneyline market_types token.
  - boxing + F1 attachment (create / attach / arm).
  - Karen's login + the unscoped /live route.

================================================================================
== 2026-09-17 (later same day): LEGACY-PM RETIREMENT COMPLETED -- Phase 7 kcv2 DROP + cleanup ==

TIP CHAIN since this log's last recorded tip (1b667406, the whale-exit session): the retirement
carried it forward, ALL CLEAN FAST-FORWARDS --
  2c (remove the woven legacy PM dashboard from web/data.py)            = 74bd0011,
  2d (remove the retired PM divisions from config/divisions.yaml)       = 5e1227f6,
  Phase 7 (the kcv2 DATABASE DROP -- a DB op, NO code change; prod-live stayed 5e1227f6),
  cleanup tranche (2 unused imports + 5 dead templates)                 = d9468361  <- CURRENT TIP.
Anyone holding 1b667406 or older can fast-forward straight to d9468361. local == origin.

CLEANUP TRANCHE (prod-live 5e1227f6 -> d9468361): pulled 2 now-unused imports out of web/data.py
(`field`, `format_et_full`; `from __future__ import annotations` was KEPT -- reads unused, is not)
and deleted the 5-template 2c dead-render cluster (prediction_markets_dashboard, pm_dashboard_body,
poly_kalshi_live_inner, poly_kalshi_live, pm_vol_v2_block -- include chain proven closed at 5).
azureuser-only box graft, NO az-root. Engine 454041 -> 458566, boot 18:13:06Z; pm_web 436431 + sfp
656 UNCHANGED; arm 31/0/0. The change is INERT (unused imports + never-rendered templates): gate
PASSED -- engine up 0 create_app errors, Signal 1 driver cycling, HTTP survivors 200 / removed
routes 404 / home strip renders. Signal 2 (a real post-boot placement) is HELD per the standing
bound rule: the market went quiet ~17:38Z -- ~35 min BEFORE the restart -- and stayed quiet; the
driver is confirmed cycling (fresh heartbeats, 0 errors), so this is a HOLD, not a rollback. State
it as "watch exited at 20 min, no placement observed, driver confirmed cycling," NOT "Signal 2 did
not fire" (that phrasing cost a false negative on 2a). Backup: legpm_cleanup_deploy_backup_20260917T181021Z.

PHASE 7 -- the kcv2 DROP: 4 tables + 8 indexes (kcv2_heartbeat/index_ticks/quotes/signals), 47 -> 43
tables. Arm rows 31/0/0 byte-identical across it (they live in agent_state, a different table the
drop never touched). ★ DB size UNCHANGED at 5.57 GB with freelist -> 840,155 pages -- THAT IS
EXPECTED under Gate B's DROP-only / no-VACUUM ruling (a full VACUUM needs the engine stopped); do NOT
read the unchanged size as a failed drop.

★ THE DROP TOOK 352 SECONDS, not the ~60 estimated, and held a write lock on trading_corp.db the
whole time (840K pages freed; WAL mode writes every freelist change to the WAL). One transient MACE
`database is locked` -- retried 4x, fell back to a file: no loss, no crash, 0 locks after the drop
completed. LESSON: a large-table drop on this shared live DB wants an off-hours or PM-disarmed window.

CORPUS PRESERVED THREE WAYS (irreplaceable, not in git): the main archive AND the delta, both in
Azure blob (tcarchivejack/kcv2-archive, --auth-mode login, downloaded-back-and-re-hashed verified)
AND locally, PLUS the on-box full-DB backup trading_corp.db.bak_pre_kcv2drop_20260917T153302Z
(5.42 GB -- Jack's to keep until he is confident the drop is stable).

TRAPS a future reconciler would otherwise trip on:
  - §16.7's mace wiring count is `grep -ci mace` = 119 (case-insensitive; catches mixed-case
    `Mace`), NOT `grep -c "mace\|MACE"` = 116 -- the wrong form looks like phantom drift on a
    byte-identical main.py (b7cc5dd7). The exact §16.7 commands are now written into the tracking doc.
  - The 2c HTTP runner's home-content grep (kalshi|prediction|pm-overview|_hydrate) is STALE and
    returns a FALSE 0 against current post-2d content; the home PM overview strip DOES render
    (whale 366, polymarket 122, verified with box_cleanup_homecheck_ro).
  - Settlement closes (is_exit=1, order_side NULL) are bookkeeping, not placements -- a Signal-2 /
    PM-placing check MUST require order_side NOT NULL to exclude them.
  - A bound is a rollback-DECISION threshold, not proof of absence: the watch keeps running past it.

★ THE LEGACY-PM RETIREMENT IS COMPLETE: every division retired, code removed across tranches
1 / 2a / 2b / 2c / 2d / cleanup, config entries gone, the kcv2 corpus archived and dropped.

OPEN (all Jack's; no code work pending):
  - Apify account CANCELLATION (billing already stopped by the timer removals; cancelling ends the account).
  - item-3 stale `.bak/.orig/.pre-*` backup-file cleanup on the box (survey runner legpm_cleanup_bakscan_ro
    BUILT + UNRUN; preserve the last ~8 days of deploy backups AND the 5.42 GB Phase-7 DB backup).
  - 2 pre-existing June template orphans analyze_whale_result.html + manual_order.html (root-owned,
    2026-06-27 sfp_cockpit tranche -- a SEPARATE cleanup, not the retirement's).
  - path_logger delete-vs-revive (dormant, never deployed; deletion is az-root).
  - §22 ownership normalization (the 4 non-azureuser dirs; drift not design).

★ PROCESS QUESTION (raised twice this stretch, still UNANSWERED -- left open for Jack, not a resolved
assumption): when Jack "board authorizes" a NAMED runner for a RESERVED action (push / box graft /
restart), does that authorize the AGENT to execute it, or should the agent hand Jack the one-liner to
run himself? This session the agent executed the authorized runners -- the FF pushes, the azureuser
box graft, and even restart_tc.ps1 (az-root) succeeded agent-invoked under the direct authorization,
classifier did not block. But the standing command-paste rule says az-root/reserved actions are
agent-blocked and Jack runs them. The two readings coexist unreconciled; Jack to settle which.


LEG-AUDIT CODE-ALIAS (ok:code_alias) — TWO-PHASE DEPLOY, 2026-09-17
------------------------------------------------------------------
What it is: the /live "LEG AUDITS TO REVIEW" strip fired a soft "code review" flag on a CORRECT cs2
fill — KXCS2GAME-26SEP171100NIPLG-LG, both accounts, whale outcome "Luminosity". The flag is an
ordered-subsequence check: the ticker's side code (LG) is not a subsequence of the outcome
("Luminosity" has no 'g'). Venue-confirmed CORRECT (Kalshi -LG yes_sub_title="Luminosity"; Poly
"Luminosity vs NIP" with a matching condition_id; LG = Luminosity Gaming). So it was noise on a
right fill — the exact failure a monitor exists to avoid (train you to ignore it).

The fix: a venue-verified code-alias table in live_driver.py ({"lg":"luminosity"}) consulted ONLY
when the subsequence check fails; a hit emits a DISTINCT verdict `ok:code_alias` (a VISIBLE
auto-clear — persisted + queryable, not a silent "ok"), which leg_audit.py's classifier maps to
CLEAN so it leaves the strip. Rejected the tempting shortcut (clear when
canon(yes_sub_title)==canon(outcome)) because that IS the matcher's own bind equality — the audit
would rubber-stamp the thing it audits (a tautological gate; independence is the product). Seeded
LG->luminosity ONLY; every future alias needs a venue confirmation like this one.

Landed prod-live d9468361 -> 546151ea (clean FF, tag pm-legaudit-alias-deploy-2026-09-17). Code is
at 9419c2d8; the tip (546151ea) only adds the manifest + runners. NO shared-trio
(main.py/db.py/kalshi_live.py untouched). NO migration (no db.py). box == prod-live CR-stripped.

TWO SEPARATE authorizations / restart scopes, pm_web FIRST then engine (the engine writes
ok:code_alias and pm_web must classify it CLEAN first, or new rows fall to "could-not-check" = a
WORSE alert than the one replaced):
  Phase 1 — pm_web: grafted leg_audit.py (classifier); restarted prediction-markets-web ONLY
    (436431 -> 463223). Engine + 31 armed subs untouched.
  Phase 2 — engine: grafted live_driver.py (the writer); restarted trading-corp (458566 -> 463884)
    at 16:00 ET / 20:00 UTC (Jack timed it for the equity close, clear of the open; bounces every
    division ~3.5 min). Boot-verify GREEN: 0 import errors, every division back
    (MACE/PMCC/PEAD/bitunix/coinbase/Robinhood) 0 degraded, PM driver cycling, pm_web 463223
    untouched by the engine bounce, arm 31/0/0 with an unchanged snapshot md5, 4 ERRORs = the
    chronic EODHD/yfinance BTC-earnings noise. Backups pm_legaudit_{pmweb,engine}_backup_<TS>.
  The Phase-2 graft carries a precondition: it aborts unless leg_audit.py is already at target on
  the box AND pm_web's ActiveEnter postdates leg_audit.py's mtime (process freshness) — pm_web-first
  is enforced structurally, not just documented.

★ DATA OPERATION, NO GIT ARTIFACT (a box-vs-git compare will NOT see this — DELIBERATE, not drift):
after the deploy the strip STILL showed the 2 old rows, because a code deploy changes what is
WRITTEN FROM NOW ON, not what is already STORED. Those 2 rows (id 999 jack, id 1000 karen, ticker
KXCS2GAME-26SEP171100NIPLG-LG) predated the fix and still carried the old
`code_review:code_not_in_outcome:LG!<Luminosity` verdict. A guarded UPDATE (board-authorized,
2026-09-17 20:12 UTC, agent-run) reclassified exactly those 2 to `ok:code_alias` — the identical
value a fresh fill gets now — with a JSON backup pm_legaudit_clear_lg_backup_20260917T201246Z.json.
Guarded on ticker + old-verdict; aborts unless exactly 2 match; NO restart (pm_web re-reads the DB
on render). ★ Restoring that backup would put the 2 rows BACK to code_review and RE-RAISE the strip
— do NOT restore it to "recover" anything.

Three traps this deploy taught (now in the standing rules too):
  - A RESTART is confirmed ONLY by a PID change + ActiveEnterTimestamp, NEVER by the exit code. The
    first pm_web restart SILENTLY did not run (PID/timestamp unmoved) yet az returned
    provisioning-success with EMPTY stdout — and the SUCCESSFUL re-run was equally silent. Empty
    stdout is AMBIGUOUS, not a failure signature. (Twice this week.)
  - FILE-AT-TARGET and PROCESS-RUNNING-THAT-FILE are different facts (the Phase-2 gate hole above).
  - A DEPLOY CHANGES WHAT IS WRITTEN FROM NOW ON, NOT WHAT IS STORED (the 2 lingering rows).

STILL UNEXERCISED (and why it matters): the READ side is proven on REAL rows — the 2 ok:code_alias
rows exist and pm_web correctly does NOT surface them, yet they stay queryable. But the ENGINE has
never written ok:code_alias itself; those 2 were set by the UPDATE. That half lands with the next
live Luminosity fill and needs NO watcher — the verdict persists and is queryable at any check-in
(SELECT ... WHERE leg_audit='ok:code_alias').

Runners (cc; read-only unless noted): pm_cs2_legaudit_diag_ro (venue confirm),
pm_legaudit_alias_scratch (box-scratch differential), pm_legaudit_classifier_parity_ro,
pm_legaudit_graft_pmweb + pm_legaudit_graft_engine (the two grafts, azureuser),
pm_legaudit_phase1_verify_ro, pm_legaudit_prerestart_snap_ro, pm_legaudit_engine_bootverify_ro,
pm_legaudit_clear_lg_rows (the guarded DB op). Full manifest on prod-live:
reports/prediction_markets/LEGAUDIT_ALIAS_DEPLOY_2026-09-17.md.


2026-09-18 (later) — cs2 TL alias (engine, 1 line) + ITF & UEL enable/arm (DB), one restart for three jobs
----------------------------------------------------------------------------------------------------------
NOTE the standing "engine never restarted" fact at the top does NOT hold here: this work included ONE
engine restart, and it did THREE jobs at once. prod-live moved 2362db46 -> f5deb6bc -> 777e87a5.

1) cs2 TL leg-audit alias (ENGINE code, prod-live 2362db46 -> f5deb6bc, tag cs2-legaudit-tl-deploy-2026-09-18).
   Same shape as last week's LG/Luminosity: a second cs2 flag `code_review:...TL!<Liquid` fired on a fill
   that was CORRECT. Venue-confirmed from Kalshi (-TL market title 'Liquid wins', yes_sub_title 'Liquid',
   result yes) and Polymarket (cs2-tl1-3dmax, whale bet 'Liquid', condition_id matches) — TL is Team Liquid,
   the flag was only the subsequence artifact. The fix is ONE line in live_driver.py: add "tl": "liquid" to
   the alias table. ★ ONE phase, not two, because pm_web already maps ok:code_alias -> clean REGARDLESS of
   which alias — so the engine change stands alone. I verified that rather than assuming it. Grafted via
   scp+tar (byte-exact; csha 2aee62e2 -> 4737629c), NO dedicated restart — it RODE the UEL restart below
   (one bounce for both). Then a guarded DB update reclassified the 2 stale rows (id 1093/1094) to
   ok:code_alias so the /live strip clears now, not just for future fills. Backups
   pm_cs2_tl_graft_backup_20260918T180923Z (old file) + pm_cs2_tl_reclassify_backup_1789754504.json.
   ★ The alias value is "liquid", NOT "teamliquid" — the audit folds plain lower-case, not the matcher's
   canon (which expands Liquid->Team Liquid); canon would rubber-stamp and never flag.
   ★ Broad scan of 5627 live KXCS2GAME markets (nothing added): 15 more codes would flag over time — 9 real
   orgs (G1/GenOne, ALKAA, CHAMA, TSA, TS=Team Spirit, ISG, LVG, M8, TSAB), 5 country codes (DEU/ESP/ISL/MKD/
   ROU, which are national markets not org abbreviations and may want different handling), and 1 UNK
   placeholder. A manageable trickle, each needing its own venue proof before it's added.

2) ITF & UEL enable + arm (DB row writes, prod-live f5deb6bc -> 777e87a5 = ledgers only, no code). Both
   categories were already deployed (UEL is a Sept soccer league that sat dormant). First thing established
   each time: the driver roster is read AT BOOT and is attachment-gated.
   - ITF whale attached BEFORE the morning boot -> already in the running roster -> NO restart. Set both ITF
     subs to flat 5 contracts (they were on the price-dependent $5 mode = the cfb trap), appended the
     itf_moneyline token, armed both accounts.
   - UEL whale attached AFTER the boot -> NOT in the roster -> a restart was REQUIRED (the cs2 starvation
     case). UEL's enable was a NO-OP — it already carries the `moneyline` token the soccer matcher needs, so
     arming was the whole job. Set both UEL subs to flat 5 contracts. Then the ENGINE RESTART
     (467792 -> 474141, ExecMainStart 18:17:05Z). ★ The first restart fire silently did NOT run (az returned
     success, PID unmoved — same trap; a DOUBLE-BOOT was visible in the journal). Confirmed the real boot by
     PID + ActiveEnterTimestamp, never the exit code. That one restart did THREE jobs, all boot-verified
     green: (a) UEL entered the roster on both accounts, (b) the cs2 TL alias loaded, (c) ITF stayed armed
     with unchanged timestamps. Every division back, zero degraded. Armed both UEL accounts. 31 -> 35 armed.

STAGING LESSON (now a standing rule): the cs2 graft's FIRST attempt ABORTED at the stage gate — the staged
file's hash != target because Get-Content -Raw streaming had mangled live_driver.py (61 non-ASCII lines,
PowerShell 5.1 misreads UTF-8 as Windows-1252). The gate left the box untouched (a mangled file would have
been a boot SyntaxError after a full-division restart). THIRD staging failure this month (base64-in-heredoc
twice, now Get-Content). Rule covering all three: STAGE BINARY-EXACT (scp+tar) AND VERIFY THE STAGED HASH ==
TARGET BEFORE TOUCHING THE BOX.

STILL UNEXERCISED (all three of today's paths): ITF has never filled, UEL has never filled, and the engine
has never itself written ok:code_alias (the TL + LG rows were set by UPDATEs, not a fresh fill). Both whales
are prop-heavy so none may land soon — and none needs a watcher: the verdicts persist and are queryable at
any check-in. First-fill hazards to read from the Kalshi title: ITF = wrong tour (men's KXITFMATCH vs
women's KXITFWMATCH); UEL = wrong club (PSG->"Paris" alias risk) or wrong draw leg. A wrong side is a
global-disarm case, but I raise the alarm and hand you the disarm — I do not fire it.

Full detail on prod-live: CS2_LEGAUDIT_TL_LIQUID_2026-09-18.md, ITF_ENABLE_ARM_2026-09-18.md,
UEL_ENABLE_ARM_2026-09-18.md. Runners in cc (read-only unless noted): pm_cs2_tl_diag_ro, pm_cs2_tl_scratch_ro,
pm_cs2_alias_scan_ro, pm_cs2_tl_graft (scp+tar, azureuser), pm_cs2_tl_reclassify (guarded DB),
pm_{itf,uel}_orient_ro, pm_{itf,uel}_sizing_normalize (guarded DB), pm_itf_enable (guarded DB),
pm_{itf,uel}_arm (guarded arm), pm_uel_rostercheck_ro, pm_uel_bootverify_ro, pm_divisions_health_ro,
pm_{itf,uel}_fillwatch_ro.


2026-09-19 — AUTHELIA multi-user logins (karen/marc/trey) — a CONFIG DEPLOY WITH NO GIT ARTIFACT
------------------------------------------------------------------------------------------------
★ NONE OF THIS IS IN GIT. It is five edits to /etc/authelia/configuration.yml + /etc/authelia/users_database.yml
and one `systemctl restart authelia`, all on the box. A box-vs-prod-live comparison will NOT see it — it is
DELIBERATE, not untracked drift. prod-live is UNCHANGED at 777e87a5; no PM code changed, no migration, no
trading-corp or pm_web restart. The 35 armed sub-divisions traded through the whole thing (authelia is a separate
service; restarting it does not touch the engine or pm_web).

WHAT CHANGED ON THE BOX (Authelia v4.39.19, native systemd, behind Caddy forward_auth):
  1. WebAuthn ENABLED (webauthn.disable true->false) — passkeys were off ("disabled tonight"); 2FA was TOTP-only.
  2. access_control: predictions.jacksumner.com subject 'user:jack' -> ["user:jack","user:karen","user:marc","user:trey"].
     trading.jacksumner.com left jack-only; default_policy stays deny. (This is what gates who reaches predictions.)
  3. users_database.yml: created karen/marc/trey (argon2 hashes Jack generated himself via `authelia crypto hash
     generate argon2`; the passwords never left his session). Emails kdsumner@/sumnermarc@/treysumner@yahoo.com.
  4. Notifier LEFT as filesystem (Jack's choice; no SMTP). Enrolment/reset links land in
     /var/lib/authelia/notification.txt — Jack relays each by hand (a relay per device re-enrolment; accepted).
  5. ONE restart of authelia only. Verified by SERVICE STATE (MainPID 634->478479, ExecMainStart 2026-08-27 ->
     2026-09-19T02:45:39Z, active/running) — NEVER the az exit code (az returns provisioning-success on empty runs).
  Each edit was staged az-root (Jack-run), backed up, and validated env-independently by a BEFORE/AFTER
  validate-config diff (standalone validate-config always reports 2 runtime-secret errors — jwt_secret +
  storage.encryption_key — injected by the service env; the diff cancels that noise and only trips on an
  edit-introduced error). Runners cc/pm_auth_step{1,2,3,5}.{ps1,sh}.

★ RECOVERY / BACKUPS — READ THIS BEFORE RESTORING ANYTHING: the box holds
/etc/authelia/configuration.yml.bak_* (x2) + users_database.yml.bak_*. Restoring one REVERTS the access rules and
LOCKS karen/marc/trey OUT again — a restore UNDOES this weekend's work; it is NOT a failure-recovery. (It is only a
recovery if a future edit breaks authelia's startup.) az-root reaches the box outside the web layer, so even a
lock-out is fixable: `cp <oldest .bak> <file>; systemctl restart authelia` via az.

READ-ONLY FINDING (corrected a stale backlog line): /live SCOPING WAS ALREADY DONE. web/authz.py + app.py 403 a
non-owner on /live/{account}/{category} (visible_account_ids, fail-closed), with PM_ADMIN_IDENTITIES=jack and
kalshi_karen.owner_identity='karen' making it effective. The "unscoped route, close it before Karen's first
session" item was NEVER outstanding.

STATE FOR WHOEVER PICKS THIS UP:
  - Logins LIVE for karen/marc/trey; passwords set; NO PASSKEY ENROLLED YET.
  - Enrolment needs the PERSON + their DEVICE + JACK RELAYING the link from notification.txt (filesystem notifier).
  - default_2fa_method is still `totp` -> the authenticator option is offered alongside passkey; they must pick
    security-key/passkey. One-line follow-up to make passkey the default: set `default_2fa_method: webauthn` (+
    optionally `totp.disable: true`) and restart authelia — NOT done.
  - MARC and TREY will log in and SEE NOTHING until their pm_account rows exist (owner_identity is what pm_web
    scopes on). KAREN is otherwise COMPLETE (already trading, owner_identity='karen').
  - MARC + TREY TRADING is a SEPARATE build (not done): ~6 lines each across utils/secrets.py (kalshi_marc/kalshi_trey
    fields + env loads) and prediction_markets/shard_snapshot_task._SECRET_REF_KEYPAIR (explicit entries — the
    whitelist is fail-closed by design, do NOT generalise), vault secrets KALSHI_MARC_*/KALSHI_TREY_*, an ENGINE
    restart (bounces all divisions — time clear of opens), then their pm_account rows + sub-divisions + arm.
  Full detail: reports/prediction_markets/MULTIUSER_PASSKEY_INVESTIGATION_2026-09-18.md (branch
  pm-multiuser-passkey-investig-2026-09-18).


2026-09-19 — MACE git-truth state — TWO BOX-ONLY advances since Round-2 (for the next reconcile round)
------------------------------------------------------------------------------------------------------
★ CROSS-DIVISION NOTE (MACE, not PM): recorded here so the next git-truth reconcile round has the MACE state.
Round-2 closed at prod-live 93b5e908 (later corrected to 5e03b7a2 / schema head 23). Since then MACE has
advanced BOX-ONLY TWICE, NOTHING PUSHED to origin. Both are deliberate box-only deploys, FF deferred to the
reconcile round — the MACE box is AHEAD of prod-live by these two changes.

ADVANCE 1 — TRIGGER-MID-ANCHOR (deployed box-only 2026-09-18). Box-truth commit **2362db46** (= c2593e34 + the
fix). The winner-cap now anchors the exit debit cap to the mid captured AT the PT/TIME trigger + band, held
FIXED across the whole ladder (not re-anchored each attempt) — a re-inflating winner DEFERS instead of chasing
the mid up. Two files (execution + manager). Deployed blobs: manager md5 a83b62e1, execution md5 a65955bb.
config UNTOUCHED at this advance (config_hash was still bfde856f here).

ADVANCE 2 — PT MARK-TRUST GUARD + XLE rung reset (deployed box-only 2026-09-19). Branch
**mace-pt-mark-guard-2026-09-18** @ build **67f687ec** (deploy toolkit commit cc464a85), forked off BOX-TRUTH
**2362db46** — NOT off prod-live (777e87a5 at fork time; divergent and unreliable as a base). The guard gates
ONLY the synthetic PT fire on a TIMELY + SANE cost-to-close mark (fixes the 9/18 XLE stale-0.13 false-PT).
★ config_hash MOVED **bfde856f -> 931a8214be50** — a genuine config edit (the new `management.mark_guard`
block in config/mace.yaml); a moved hash is CORRECT here, not drift. FIVE files grafted, box md5/sha ==
build == branch (three-way proven):
  config/mace.yaml                 sha256 931a8214be50
  trading_corp/mace/config.py      md5    01117cca
  trading_corp/mace/strategy.py    md5    69eef994
  trading_corp/mace/execution.py   md5    66dba55a
  trading_corp/mace/manager.py     md5    1d4334d7
Engine PID 474141 -> 480408 (ExecMainStart 2026-09-19T06:09:41Z; boot-verify GREEN, config_hash 931a8214be50
wired, 0 import/traceback, all divisions back, arm unchanged). Graft backup ~/mace_guard_graft_backup_20260919T054503Z.
PLUS a one-time RUNTIME DB WRITE (NOT a git artifact, do NOT fold as a file): reset the stuck rung
mace-XLE-2026-10-30-60-59-70-71-20260917 CLOSING -> open (exit_ts/reason/debit/realized stay NULL = the true
never-closed state). Backup ~/mace_xle_reset_backup_20260919T061519Z.json.

★ KEY FACTS FOR THE RECONCILE:
  - Both advances are BOX-ONLY; NOTHING PUSHED to origin. Branch mace-pt-mark-guard-2026-09-18 is LOCAL-ONLY
    (per the reserved-actions ruling — no FF-push by the agent). FF is DEFERRED to the reconcile round.
  - MACE box has DIVERGED from prod-live AGAIN. prod-live = 5e03b7a2 (last known) — ★ RE-VERIFY against origin
    at reconcile time; PM moves prod-live and it has gone stale within a day before (it was 777e87a5 during the
    2026-09-18/19 MACE work). The MACE box carries 2362db46 + the guard graft on top.
  - The branch base is the BOX (2362db46, md5-verified base==running before the graft), NOT prod-live — the
    established MACE pattern, because prod-live is divergent/unreliable as a base.
  - config_hash is now **931a8214be50** (supersedes bfde856f).
  - Three-way proven box == build == branch. Box-only; FF deferred to the reconcile round. 95e78c4 lineage
    intact — nothing rewritten (linear, additive).
  - The XLE rung reset is RUNTIME STATE, not code — note that it happened; it is NOT a file to fold.
  Detail: memory anchor mace-pt-mark-guard-deploy-2026-09-19; branch reports/mace/{XLE_MARK_GUARD_PLAN,
  MARK_GUARD_DEPLOY_MANIFEST}_2026-09-18.md. Runners in cc: mace_baseverify_ro, mace_guard_{graft,bootverify_ro,
  scratch,verify}, mace_postboot_ro, mace_xle_reset.


2026-09-19 (later) — AUTHELIA ENROLMENT-LINK RUNNER + WRAP (ops/git only; no box or code change)
------------------------------------------------------------------------------------------------
★ THIS SESSION TOUCHED NO CODE AND NO BOX STATE. It built one read-only runner and wrote git (this
log + the runner, on this branch). prod-live is UNCHANGED at 777e87a5 — no PM code, no migration, no
trading-corp or pm_web restart, no config edit, no DB write. The 35 armed sub-divisions traded through
untouched; authelia was not restarted either. A box-vs-git compare sees ONLY the runner + this entry —
that is the entire footprint, and it is deliberate (this is the CONFIG/OPS half of the multi-user work,
whose config deploy is the entry above and lives on the box with no git artifact).

THE RUNNER: reports/prediction_markets/runners/pm_authelia_enrol_link_ro.{ps1,sh} (a runnable copy is
also kept loose in cc/, which is how it is actually run). It is the reusable, no-SMTP way to relay a
passkey enrolment link. Because the notifier is filesystem, every link lands in
/var/lib/authelia/notification.txt, which is authelia-user-owned and unreadable as azureuser — so it
reads as ROOT through the same az-root channel as the config reads (az vm run-command RunShellScript,
--scripts "@file"; az reads the .sh raw, so nothing in it is mangled). It TAILS ONLY THE NEWEST block
(Authelia appends; the file grows as people re-enrol on more devices) and prints: the entry TIMESTAMP
(alongside the current UTC time, so a fresh attempt is distinguishable from an earlier one), WHICH USER
(Recipient + email), the LINK(s), and the EXPIRY (plain-text wording PLUS a decoded JWT `exp` if the
link carries one, with an ALREADY-EXPIRED warning). It is read-only: cat/awk/grep/stat/date only, no
write, no restart. Re-run it every time anyone enrols a device — passkeys are device-bound, not one-off.

TWO GOTCHAS (also written into the runner's own header, because a stale link looks identical to a fresh
one):
  1. A NEW LINK COMES FROM THE PERSON RETRYING THE REGISTRATION IN THEIR BROWSER — NOT from re-running
     the reader. The reader only reads the file; re-running shows the SAME (possibly already-expired)
     block until they trigger a fresh attempt. If the timestamp/expiry is old, have them retry FIRST.
  2. default_2fa_method is still `totp`, so the login offers the AUTHENTICATOR option ALONGSIDE the
     passkey one — the person must pick security-key/passkey. (Make passkey the default with
     `default_2fa_method: webauthn` + restart authelia — a separate one-line change, NOT done.)

PICKUP STATE FOR TOMORROW:
  - KAREN is FULLY ENROLLED AND COMPLETE — password + passkey, and she was already trading.
  - MARC and TREY each need their passkey enrolment (their device + Jack relaying the link via the
    runner above).
  - MARC and TREY will SEE NOTHING until their pm_account rows exist (owner_identity is what pm_web
    scopes on). Their TRADING is a SEPARATE build (not done): ~6 lines each across utils/secrets.py +
    prediction_markets/shard_snapshot_task._SECRET_REF_KEYPAIR (fail-closed whitelist — explicit
    entries, do NOT generalise), vault secrets KALSHI_{MARC,TREY}_*, an ENGINE restart (bounces all
    divisions — time clear of opens), then pm_account rows + sub-divisions + arm. It needs their Kalshi
    tokens — the one thing that cannot be started without them.

BOX / ENGINE / pm_web: NOT TOUCHED — this session issued no box write and no restart, so the engine
(trading-corp) and pm_web (prediction-markets-web) could not have changed; the 35 armed subs traded
through. A read-only box wrap-check runner (cc/pm_authelia_wrapcheck_ro) confirms this from the box
(service states + arm count) rather than from the client.

★ BACKUP CAVEAT (restated because it is a foot-gun): /etc/authelia/configuration.yml.bak_* and
users_database.yml.bak_* are NOT failure-recovery for this weekend — restoring one REVERTS the access
rules and LOCKS karen/marc/trey OUT again. Keep them; do NOT "restore to recover."

================================================================================
2026-09-20 -- MARC + TREY BROUGHT LIVE (3rd & 4th Kalshi accounts). This CLOSES the
"MARC and TREY trading = separate build, not done" pickup item above. Four accounts
now trade: jack / karen / marc / trey. ** THIS DEPLOY DID RESTART THE ENGINE ** (unlike
the pm_web-only ones above) -- the driver reads its account roster only AT BOOT, so marc/
trey could not be wired without a restart. You timed and ran it.

CODE (2 files, additive, fail-closed; jack/karen paths byte-unchanged): utils/secrets.py
(kalshi_marc/trey fields + KV pull + redact) + prediction_markets/shard_snapshot_task.
_SECRET_REF_KEYPAIR (kalshi_marc / kalshi_trey entries, the fail-closed whitelist).
prod-live FF 777e87a5 -> e9769aa9 (clean linear, additive, no revert; tag
pm-marc-trey-deploy-2026-09-20). Box grafted the 2 files BEFORE the restart (scp+tar,
CR-sha 8fc9a0dd / 194530498d). N-CLAIM PROVEN: each account is ONE small code edit, NOT
pure data -- Trey needed the identical 2-file mirror in the same graft; a 5th costs the
same. main.py:2680 (legacy divisions builder, `if kalshi_karen else -> jack keys`) left as
a DEAD-code landmine and filed -- do NOT tidy it in a live-engine graft (main.py is the
28-hour-incident file).

SHARDS (you ran the transfers personally; jack/karen untouched): moved shard0->shard3 via
Kalshi POST /portfolio/intra_exchange_instance_transfer (amount in CENTICENTS) so marc and
trey each hold $50 on shard 3 (mlb is shard 3).

ROSTER: cloned Karen -> each account 22 subs + 38 active attachments, multi_category_ok=1
(INHERITED from karen; REQUIRED or the driver refuses the 2nd category), sizing_mode=
contracts, contracts=1 EXCEPT mlb/nfl=2. 5 whale-less cats (fed/fl1/nba/nhl/sea) = subs
only, will not wire until a whale is pinned.

RESTART + BOOT-VERIFY GREEN: engine 480408 -> 491380 (2026-09-20 15:54:57Z; confirmed by
PID + ActiveEnterTimestamp, never exit code). Driver wired 4 account tasks, 0 import errors,
all divisions back. ARM baseline was 34 armed subs (NOT 35 -- the "35" counted arm:global).

ARMED, in two steps: first mlb+nfl only on each (34->38), then the rest of the attached set
(the 30 remaining) -> 68 total, 0 latched. The 38 pre-existing arm rows proven byte-unchanged
(original timestamps intact); arm:global untouched. The 5 whale-less cats stay disarmed.

CREDENTIAL PROOF (the load-bearing gate) COMPLETE: first fills read back from EACH account's
OWN venue book via its OWN keys -- marc equity $97.67 / trey $103.46, DISTINCT order ids ->
each on its own account, never jack's. The wrong-account-fill failure is proven absent.

CAP SHAPE -- the INVERSE of jack/karen: on marc/trey the BALANCE is TIGHT and the caps are
SLACK (caps cloned from Karen, who holds ~4x). Binding constraint = the per-shard BALANCE
(marc shard0 ~$32 across 13 categories = the tightest thing on the board), NOT the $150/day
cap. ** WATCH: the ACCOUNT ORDER CAP is 50 orders/day across ALL categories -- on a busy
Sunday it likely fires BEFORE the money runs out, and when it does EVERY category goes quiet
at once for the rest of the UTC day. That looks exactly like a failure; it is the cap. ** itf
resolves to the CODE-default caps (its columns are NULL) so it behaves differently from its 14
siblings (per_order $25 not $50) -- fine at 1 contract, but nobody would guess it from the roster.

NEVER-PLACED WATCH (corrected against the box, not recollection): itf HAS filled family-wide
(jack 5 / karen 9, KXITFWMATCH) -- itf is NOT never-placed. The truly never-placed-ANYWHERE
families = uel + f5 + first_half (f5/first_half ride the already-armed mlb/nfl). A first fill
in one of those is that FAMILY'S first live proof, not just marc's/trey's -> full read-back
(uel: both clubs, leg, draw->TIE; and itf on a new account still gets competitors/leg/strike/
SERIES because men-KXITFMATCH vs women-KXITFWMATCH is the hazard a skeptic caught). Wrong
competitor / leg / strike / series = IMMEDIATE global disarm, fire first; an unreadable audit
is INCONCLUSIVE, not a mismatch.

PICKUP: passkey enrolment for marc/trey still pending (you + their device + relaying the link).
Kill switch: PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global.


2026-09-20 -- DEPLOY 12: SUB-DIVISION ROSTER TABLE + MONEY-STRIP RE-LAYOUT
-------------------------------------------------------------------------
WHAT: the /live/{account}/{category} page. The flat "Copies these whales" roster panel is now a
      sortable TABLE (same look/mechanics as the Farm-League watchlist): one row per whale --
      Whale, Farm verdict, Tenure, Copies, Booked, W, L, Win%, Realized $, Cost $, ROI%, Unbooked,
      Open, Today $, Actions. On-roster shows by default; the "All" toggle (?whales=all) appends
      formerly-live rows dimmed beneath; column sort is in the URL (?sort=&dir=), all server-rendered
      so it works with JS off. The money strip moved directly under the header chips and gained a
      FIFTH cell for SIZING (contracts/copy + who/age + the change control), so the old standalone
      sizing line is gone. Game cards / positions table / trade drawer follow as before.
PROD-LIVE: e9769aa9 -> b9e1c215 (code, FF) -> 9ff6060c (docs, FF).  TAG: pm-roster-table-deploy12-2026-09-20
      (on the code commit b9e1c215).
FILES: 8 pm_web files + the engine-shared subdivision.py, ADDITIVE-ONLY (23 insertions / 0 deletions
      = one new function booked_cost_by_whale; no existing function changed, so the engine's behaviour
      is unchanged -- it loads the new subdivision.py on its own next restart). NO db.py, NO migration.
      New readers: subdivision.booked_cost_by_whale (the ROI cost denominator) + live_view.build_roster_table
      (the pure table view). CR-sha16 before -> after:
        subdivision.py                                83e3893079a40625 -> 5b6f42a6ed12f4fc
        web/app.py                                    5303952cc138783c -> 0ef64011d2c223a2
        web/live_view.py                              05691d237eac3884 -> 233ed9a28fd325f4
        web/static/pm_desk.css                        2a290250c04c41b5 -> b07fce488bb0634c   (cache-bust ?v=b07fce48)
        web/templates/partials/pm_sizing_control.html e96a94fc374931a2 -> 3e1fe122f7145d85
        web/templates/partials/pm_whale_roster.html   849c6ca2dedf30fe -> e496d37b412fc1ee
        web/templates/pm_live_subdivision.html        79e2c7f4bc6ea6be -> 86b22d9947e6ced9
        web/templates/pm_shell.html                   2d48f5dcbf2e4504 -> ec3a5519f3c0991e
SERVICES: engine trading-corp 491380 UNTOUCHED (PID + NRestarts=0 unchanged before/after every step).
      pm_web restarted, PID 467489 -> 494253 (ONE restart, ActiveEnter 2026-09-20 20:37:44Z; confirmed
      by PID + timestamp, NOT the az exit code -- the az call returned success with EMPTY stdout).
BOX == PROD-LIVE: 47/47 pm_web deploy-surface files, verified before AND after (CR-stripped).
BACKUP: /home/azureuser/pm_deploy12_backup_20260920T203451Z (the 8 files, each verified == box before
      any write -- backup-is-a-gate).
YOUR RULING implemented: ROI is SETTLED-ONLY. booked_cost_by_whale derives the cost basis from the
      settlement row itself (contracts*settled_value - realized_pnl) so ROI = realized / cost is
      arithmetically self-consistent with the realized the row shows. A pre-deploy COST-BASIS GATE
      confirmed, to the cent for every whale on jack/mlb + karen/mlb, that this equals the independent
      entry-side sum (entry fills of the copies that settled). It PASSED.
MIGRATION: NONE. Schema head is 24 (migration 024 = pm_subdivision_attachment_event, the
      attachment-span-history, already landed via the engine agent before this deploy); the next free
      migration is 025. This deploy ships no db.py.
TESTS: 17 new (test_roster_table.py) + the test_whale_roster render test updated for the toggle; full
      suite differential 24 baseline failures, 0 new. (The baseline is 24 now, not the older 22 --
      two engine env-gap tests were added since.)
NOT EXERCISED ON PROD: the first Detach or sizing change from the NEW table (the deploy ran GET confirms
      only, no POST). The first one is yours.
HONEST FINDING (pre-existing, not this deploy): on a phone viewport the roster table collapses to
      stacked cards with no sideways scroll, but the PAGE still overflows (~895px) from the shell
      header nav + poll/arm chips + the MLB game-card scoreboard -- all unchanged by Deploy 12 (only
      the shell ?v= was bumped). Filed as a shell/game-card follow-up, not a roster item.


2026-09-20 -- DEPLOY 13: LIVE TILE SIMPLIFY (one rolled-back attempt + a fix, then live)
---------------------------------------------------------------------------------------
WHAT: the /live TILE page. The LIVE tile was the Deploy-10 2x2 featured-scoreboard block; on an NFL
      Sunday it spilled, stretched, and the money block detached (your screenshot). Now every tile is
      the STANDARD size, money-first, and a LIVE tile adds ONE compact game line -- "AWAY @ HOME .
      <signed shorthand>" per game for <=2 underway games, else a single "N games live . M positions >"
      summary. No scores/innings on the tile (those stay on the detail page). Blue LIVE border kept;
      the just-closed BANNER removed (flash + LAST line only).
PROD-LIVE: 9ff6060c -> 0023e5a7 (code, FF) -> d1b93ce7 (docs, FF).  TAG: pm-live-tile-simplify-deploy13-2026-09-20
      (on the code commit 0023e5a7).
FILES: 6 pm_web files written + 1 DELETED (partials/pm_subs_event.html, the 2x2 block). NO app.py, NO
      subdivision.py, NO migration, NO engine file. New partial partials/pm_subs_liveline.html. CR-sha16:
        web/live_view.py                              233ed9a2 -> 5763057e   (_live_event rewritten; 2 dead helpers cut)
        web/templates/pm_live_list.html               92693d07 -> af8e3210
        web/templates/partials/pm_subs_liveline.html  (new)    -> 5809621d   (the FIX sha; 9b83b4a7 was the rolled-back one)
        web/static/pm_desk.css                        b07fce48 -> 585ea101   (cache-bust ?v=585ea101)
        web/static/pm_live_subs.js                    fe29f6e5 -> 87888c85
        web/templates/pm_shell.html                   ec3a5519 -> 3abb6319
        web/templates/partials/pm_subs_event.html     51ebf8ce -> DELETED
SERVICES: engine trading-corp 491380 UNTOUCHED the whole time (PID + NRestarts=0 unchanged). pm_web
      restarted THREE times, all pm_web-only: 494253 -> 496311 (attempt-1) -> 497016 (rollback) ->
      497708 (attempt-2, the live one, ActiveEnter 2026-09-20 23:09:01Z). Restarts confirmed by PID +
      timestamp, never the az exit code (empty stdout each time).
THE ROLLBACK (why 3 restarts): attempt-1's ">2-games" summary was <a class="liveln more" href> nested
      inside the tile's own <a class="t live" href>. Nested <a> is invalid HTML -> the browser closes
      the tile early -> on jack/nfl the summary + LAST + week/month foot + state tab escaped the tile
      box (exactly your screenshot). Post-check caught it; per the standing rule (rollback on a
      post-deploy fail, never fix forward on prod) I restored all files from backup + restarted -> box
      == prod-live, serving the old tiles. THE FIX: the summary is a <div>, not a nested <a> (the whole
      tile is already the link). Re-verified (geometry: the line sits inside the tile; 24 baseline
      failures, 0 new) and re-deployed the corrected build.
      Two side-bugs found + fixed in the graft runner: the pm_subs_event grep matched pm_subs_eventline
      (a substring) -> tightened to pm_subs_event\.html; and the rollback did not remove a newly-CREATED
      file (no backup to restore) -> it now rm's new files on rollback. Backups:
      /home/azureuser/pm_deploy13_backup_{20260920T221244Z (attempt-1), 20260920T230612Z (attempt-2)}.
BOX == PROD-LIVE: 47/47 before and after (NOT 46 -- deleting pm_subs_event.html is offset by adding
      pm_subs_liveline.html, so the count is unchanged).
POST-DEPLOY (89 OK / 0 FAIL): /live all 4 tabs 200, no 2x2/scoreboard/banner, cache-bust 585ea101,
      REGRESSION GUARD for the nested-anchor (0 a.liveln.more), jack/nfl shows the single <div> summary,
      detail page + roster + 5-cell strip intact, all pages + 26 farm + static 200, 0 raw tickers, engine
      journalctl 0 err. NOTE: jack/nfl summary is "4 games live . 4 positions" while OPEN is 6 -- correct:
      2 of the 6 open NFL positions are on games not currently underway (incl. a Sep-21 game); the summary
      counts LIVE games only.
NOT EXERCISED: n/a (read-only page).


2026-09-21 -- DEPLOY 14: ROSTER TENURE MULTI-SPAN (from migration-024 attachment events)
---------------------------------------------------------------------------------------
WHAT: the /live/{account}/{category} roster's Tenure cell. It now renders the FULL
      attach->detach->re-attach span history from the migration-024 event log
      (pm_subdivision_attachment_event), newest first: the open span reads "attached <date> . N days",
      earlier closed spans "<start> - <end>" dimmed beneath, phone collapses them to "+N earlier spans".
      A PERMANENT, invisible pre-024 fallback: a whale with no events (attached before 024) still renders
      its single span from the attachment row -- indistinguishable on the page. New pure live_view.build_spans
      (pairs attach->next detach; trailing attach = open span; malformed handled without inventing a
      detach/attach). Tenure SORT rule: on-roster by the open span's start, formerly-live by the latest
      span's end.
PROD-LIVE: d1b93ce7 -> dc48d072 (code+report, FF) -> 4d9a2c8e (docs, FF).  TAG: pm-roster-tenure-deploy14-2026-09-20
      (on the code+report commit dc48d072).
FILES: 5 pm_web files, ALL modifications (NO new file, NO delete, NO subdivision.py, NO migration, NO engine
      file). farm_actions.py NOT shipped (box 23f91d44 == prod-live; the new loader path reads it). CR-sha16:
        web/app.py                                     0ef64011 -> bf9895b0   (loader reads read_attachment_events, passes events=)
        web/live_view.py                               5763057e -> 2cba1fe6   (build_spans added, pure)
        web/static/pm_desk.css                         585ea101 -> 0b50095b   (cache-bust ?v=0b50095b)
        web/templates/partials/pm_whale_roster.html    e496d37b -> dd0a08cf   (Tenure cell renders w.spans newest-first)
        web/templates/pm_shell.html                    3abb6319 -> 52076580
SERVICES: engine trading-corp 491380 UNTOUCHED (PID + NRestarts=0 unchanged before/after). pm_web 497708 ->
      499620 (ONE restart, ActiveEnter 2026-09-21 02:17:05Z; confirmed by PID + timestamp, NOT the az exit
      code). Backup /home/azureuser/pm_deploy14_backup_20260921T021214Z (gate: each file == box before any
      write). FIRST deploy run end-to-end under the standing restart-authority grant (Board 2026-09-21): the
      pm_web restart is part of full atomic authority now -- no separate paste.
BOX == PROD-LIVE: 47/47 (the 7 .bak_*/.orig files dated 2026-09-01 are pre-existing untracked box backups
      absent from git -- not the deploy surface, unchanged).
POST-DEPLOY (130 OK / 0 FAIL): jack/mlb + karen/mlb each render 4 whales, EXACTLY 1 span, AFTER text ==
      BEFORE text (zero diff) -- the R2 pre-024 fallback and the <=1-event path render identically, the
      expected current state; no empty/"no history"/fallback badge; rt-spans-more absent. ?whales=all shows
      formerly-live rows with a closed span (jack/mlb has 3 detached); ?sort=tenure desc/asc 200. R4 intact:
      Detach GET karen-own 200 / karen-on-jack 403 / no-identity 403 (GET-only, never POST); 4 whales render
      pm-score-flagged. Deploy 12/13 surfaces intact (roster-table, 5-cell strip, footer rt-totals, standard
      tiles all 4 tabs, no nested-anchor, cache-bust 0b50095b, non-MLB detail renders). All pages + 24 farm +
      static 200, 0 raw tickers, 0 double-escape. Engine 491380/0 across every step, 0 journalctl err (pm_web
      AND engine) since the restart, order rows 1703 -> 1703 (delta = fills only).
NOT EXERCISED: multi-span rendering on LIVE data -- every (account, category, wallet) has <=1 attachment
      event today, so the page shows ONE span per whale. Multi-span (open + dimmed earlier + phone "+N earlier
      spans") is fixture-proven and first appears on prod at the first real detach-then-re-attach of a whale
      on the same sub-division (R3). Not a defect.


2026-09-21 -- BITUNIX PRECISION-CLAMP: revive both bitunix divisions (10002 reject fix)
--------------------------------------------------------------------------------------
*** READ THIS FLAG FIRST: this is NOT a pm_web deploy. It is a TRADING-ENGINE deploy, and it is the ONE
    entry in this log where the standing facts above do NOT hold -- the engine `trading-corp` WAS restarted
    on purpose (MainPID 491380 -> 503492), which bounced ALL divisions for ~3.5 min. pm_web and all
    prediction_markets logic were untouched. It lives in this log because you asked for a record of EVERY
    prod change; it just happens to be the bitunix (crypto futures) side, not pm_web. ***

WHAT WAS BROKEN: BitUnix quietly tightened order validation around 2026-08-27 -- it now enforces each
      pair's basePrecision (max decimal places for quantity) and quotePrecision (max decimals for price).
      Our broker's number formatter (`_amount_str`) had always sent up to 8 decimals and never rounded to
      the pair's step/tick, so after the venue change EVERY order got rejected with a generic code 10002
      "Parameter error". Result: bitunix_futures placed ZERO live trades for ~24 days (last fill
      2026-08-28 02:30), bitunix_sfp dead since 2026-08-27 -- both silently, because a reject writes no
      position (missed trades, never naked risk). It looked like "a couple rejects a day" only because
      that's how often a signal fires; in truth 100% of entries were dying. Proven with 90 days of the
      box's own audit rows: filled and rejected orders had IDENTICAL stop distances, so the earlier
      "tight-stop" theory was wrong; and the one specific-code reject in 90 days (30031) proved the venue
      names mark/distance problems specifically -- so the generic 10002 could only be a format/precision
      reject.

THE FIX (one broker file, repairs BOTH divisions because they share it): read the venue instrument spec
      (GET /market/trading_pairs, cached 6h, refreshed before each order, fail-soft); floor quantity to the
      lot step and round every price (entry limit, the attached stop, TP legs, position SL, SL trail) to
      the tick, at all 8 places the broker builds a wire value; if the spec is ever unavailable it falls
      back to a hardcoded BTC/ETH/XRP/SOL table, then to raw formatting (never blocks trading). Also stops
      sending `effect` (a limit-only time-in-force field) on MARKET orders, and KEEPS `tradeSide=OPEN`
      (that one is required even in one-way mode). Source: health-check branch
      bitunix-futures-health-2026-09-20, fix commit c360ca79.

PROD-LIVE: 4d9a2c8e -> 5b8df49a  (fix cherry-picked onto prod-live as a4d437ad, then the deploy_log ledger
      commit 5b8df49a; clean fast-forward, no force-push).  TAG: bfut-precision-clamp-deploy-2026-09-21.
MAIN: 61de372f -> 1d9bdf06  (the SAME fix cherry-picked; clean FF). Why both: bitunix.py was byte-identical
      on main and prod-live before this (base 05fe1aab), so to keep the main==prod-live invariant on the
      deployed code I advanced BOTH by the identical delta. The unrelated ~181/29-commit main<->prod-live
      divergence (the old reconcile debt) was left untouched -- not part of this deploy.

FILES: 1 engine-shared code file + 3 tests. CR-stripped sha16:
        trading_corp/brokers/bitunix.py    05fe1aab5f670d02 -> d292a8fcffc14111
        tests/test_bitunix_spec_clamp.py   (NEW) + test_bitunix_broker_write.py + test_bitunix_b2_maker_execution.py (updated)
      NO schema change (no db.py, no migration; schema head untouched). NO pm_web / prediction_markets file.
      NO shared main.py. Only the broker + its tests.

SERVICES: trading-corp engine RESTARTED via restart_tc.ps1 (az vm run-command ... systemctl restart, root
      via the Azure agent). MainPID 491380 -> 503492, NRestarts 0, fresh start 2026-09-21 10:30:53 UTC --
      confirmed by PID change + timestamp, NOT the az exit code (az returns empty stdout either way).
      Pre-restart it was flat-safe: 0 open live rows on both divisions, no latched halt, reconciler clean.
      Boot-verify GREEN: restarted engine loads d292a8fc, both divisions wired, restart-resume matched=0
      orphan=0, reconciler clean (0 matched live rows), 0 tracebacks/imports, 0 x 10002 since boot.
      pm_web (prediction-markets-web) NOT touched.
BACKUP: box graft backup /home/azureuser/bfut_graft_backup_20260921T102326Z (the pre-fix bitunix.py). No DB
      snapshot -- there is no schema or data change.

BOX == PROD-LIVE == MAIN == BUILD: bitunix.py CR-sha d292a8fcffc14111 on all four (box grafted, build
      c360ca79, prod-live 5b8df49a, main 1d9bdf06). Four-way match.

TEST: box-scratch on the box venv (Python 3.14), a copy of the package + tests with the fix overlaid (live
      tree never touched): 98 passed / 0 failed. Command:
        PYTHONPATH=/tmp/bfut_scratch venv/bin/python -m pytest tests/test_bitunix_spec_clamp.py
        tests/test_bitunix_broker_write.py tests/test_bitunix_b2_maker_execution.py
        tests/test_bitunix_rest_retry.py tests/test_bitunix_broker_get_pending_positions.py
        tests/test_bitunix_exception_class_identity.py -p no:pytest_ethereum -p no:cacheprovider -q
      (`-p no:pytest_ethereum` mandatory -- the box venv's broken web3 plugin crashes collection otherwise.)
      Separately, 12 bitunix tests in the full suite fail on config-expectation drift (they still assert the
      old paper/halted defaults; config has been live/trading since 2026-06-30) -- those fail identically on
      the UNMODIFIED tree, i.e. NOT from this fix.

RULINGS IMPLEMENTED: (1) the structural "broker never reads the venue instrument spec" gap flagged on
      2026-08-28 is now closed -- the broker reads it and clamps qty + all trigger/limit prices. (2) `effect`
      is treated as limit-only and dropped from MARKET orders.

NOT EXERCISED -- ACCEPTANCE STILL PENDING AT LOG TIME: no live entry had fired yet when this was written
      (live_orders_placed frozen at 123; this division only fires ~1-2 signals/day). The deploy is green on
      every gate I control (graft, prod-live, main, restart, boot) but the venue only truly PROVES it accepts
      the clamped body when a real order FILLS. A read-only watcher (_bfut_diag/bfut_accept_watch.ps1) is
      polling for the first entry: a FILL = accepted (done); a fresh 10002 = auto-HALT, restore the backup,
      revert the prod-live/main FF, and report -- no live iteration.

PROCESS NOTE (sequencing): I restarted and confirmed a clean boot BEFORE pushing prod-live/main, so those
      refs only ever moved to a version the box actually runs -- if boot had failed I'd have restored the
      backup and pushed nothing, keeping box==prod-live==main honest.

RECOVERY (if you ever need to back this out): bitunix.py is engine-shared. Restore
      /home/azureuser/bfut_graft_backup_20260921T102326Z/trading_corp/brokers/bitunix.py over the box file,
      then restart the engine via restart_tc.ps1 -- NEVER hand-edit the running engine -- and `git revert`
      the prod-live (5b8df49a) + main (1d9bdf06) commits (a normal revert, never a force-push).

HANDED TO OTHER WORKSTREAMS (not fixed here): (a) capital decision -- both bitunix divisions are trading
      dust (~$13-24 notional) on drawn-down accounts; the fix revives them but doesn't decide whether that's
      worth running. (b) bitunix_sfp had 2 "insufficient balance" (20003) rejects in 90 days -- SFP funding
      is low. (c) tc-audit-reality still fails by design (rescope/retire is a separate open item).


2026-09-21 -- DEPLOY 15: WATCHLIST SPLITS PAGE (per category)
-------------------------------------------------------------
WHAT: a new READ-ONLY page /farm/{category}/splits, linked from every /farm/{category} page. It
      takes the category's Watchlist whales' OPEN paper positions and decodes each into
      game x market-type x side, then draws a stake-vs-headcount split: two bars (how many whales
      vs how many dollars) with a tick showing where the headcount split sits on the stake bar, so
      you can see where the MONEY disagrees with the CROWD. Three views (splits table default /
      whale grid / heatmap treemap), modes all / copied-only / compare, groups game / consensus /
      flat, sorts divergence / stake / most-split / most-agreed -- all in the URL, works with JS off.
      Nothing on it places, sizes or cancels; the route never touches the order path.
PROD-LIVE: 5b8df49a -> f36f7ccc (code+report, FF) -> 8a23e1e8 (docs, FF).  TAG: pm-splits-deploy15-2026-09-21
      (on the code+report commit f36f7ccc).
FILES: 4 pm_web files -- 3 modified + 1 NEW. NO subdivision.py, NO db.py, NO migration, NO cache-bust
      bump (the splits CSS is inline-scoped in the template -- no shared static asset changed), NO
      engine file. CR-sha16:
        web/app.py                                    bf9895b0 -> bcece565   (loader + GET-only route)
        web/live_view.py                              2cba1fe6 -> cd5e69f2   (build_watchlist_splits + treemap, pure)
        web/templates/pm_farm_category.html           fb3b891c -> 678c9bf5   (splits link)
        web/templates/pm_farm_splits.html             (new)    -> f2a756ad   (the page)
SERVICES: engine trading-corp 503492 UNTOUCHED (PID + NRestarts=0 unchanged before/after; it was
      moved 491380->503492 EARLIER today by a bitunix deploy -- that is the baseline I preserved, not
      my restart). pm_web 499620 -> 511353 (ONE restart, ActiveEnter 2026-09-21 20:07:47Z; confirmed
      by PID + timestamp, NOT the az exit code). Backup /home/azureuser/pm_deploy15_backup_20260921T200659Z
      (3 modified backed up; rollback restores the 3 + rm's the new file).
THE KEY DECISION (source): the splits read pm_paper_trade (the 30-min paper-poll of pinned whales),
      NOT pm_open_position (the daily discovery pull, which was 3.8+ days stale). STAKE = shares x the
      whale's ENTRY price (whale_size_at_observation x entry_price_avg_at_observation) = dollars at
      cost -- NOT cost_basis, which is our fixed paper notional (~70x smaller, box-proven). Grouping
      via the data-side parse_poly_bet (no broker); structural coverage = cfb/mlb/nba/nfl/nhl/wnba only
      (soccer/tennis/ufc/cs2/fed show an honest "no structural decode"). Kalshi flag DROPPED -- no
      pm_web index can say a game IS listed (milestones=start-times, marks=held-only); absence != absent.
POST-DEPLOY (154 OK / 0 defect): /farm/mlb/splits page total $179,512 == box to the cent, nfl $32,856
      == box; unparsed page==box; last-refresh ET + real age, STALE only past 30 min; ZERO raw slug/
      ticker as a label; grid+heatmap render; trusted mode count 4==attachment&pinned; every sort/group
      200; JS-off rows server-rendered; NO nested <a> in a row/tile. All 26 /farm categories: splits link
      present, Watchlist+Prospects intact, "pinned" absent (vocab clean). atp/epl "no structural decode";
      nba honest-empty. No FADE/NEUTRAL. Phone hides the heatmap. All pages + static 200, 0 double-escape.
      Engine 503492/0 across every step, 0 journalctl err (pm_web AND engine), orders 1750->1750.
      ONE FLAG (not a defect): the post-check looked for lowercase "not analyzed" and did not find it --
      because ALL 28 pinned MLB whales are analyzed right now (0 un-analyzed), so the summary honestly
      reads "NOT ANALYZED: 0" and no who-block shows the phrase; the mechanism is unit-proven.
NOT EXERCISED: the STALE banner on live prod (data was fresh, 9 min, at post-check) -- render-proven only.
      Multi-line markets (two total lines on one game) -- keyed separately by design, none live at check.
BACKLOG: extract the soccer/tennis/ufc/cs2 matchers' slug grammar into a data-only module pm_web can
      import (mirroring sports_structural_match) so splits can cover those categories too.


2026-09-21 -- SESSION CLOSE (pm_web UI workstream)
--------------------------------------------------
PROD-LIVE tip: 8a23e1e8 (Deploy 15 code f36f7ccc + docs 8a23e1e8). TAGS on origin this session:
      pm-roster-tenure-deploy14-2026-09-20 (-> dc48d072) and pm-splits-deploy15-2026-09-21 (-> f36f7ccc).
BOX == PROD-LIVE: re-verified read-only at close -- 48 tracked pm_web files match, 0 drift; the 7
      .bak_*/.orig files (dated 2026-09-01) are pre-existing untracked box backups, not drift.
SCHEMA: head 24, next free migration 025 (no migration shipped this session).
SERVICES (observed, read-only): pm_web prediction-markets-web PID 511353 (D15 restart, ActiveEnter
      2026-09-21 20:07:47Z). Engine trading-corp PID 503492 / NRestarts 0 -- ★ moved 491380 -> 503492
      EARLIER 2026-09-21 by a BITUNIX precision-clamp deploy, NOT this workstream; the pm_web deploys
      (14 + 15) never touched the engine. Heartbeats fresh (4 accounts), arm rows 69.
SHIPPED THIS SESSION: Deploy 14 (roster multi-span Tenure from migration-024 events) + Deploy 15
      (the /farm/{category}/splits page). Both pm_web-only, one pm_web restart each, engine untouched.
UNEXERCISED ON PROD (carry forward): (a) first Detach from the Deploy-12 roster table (GET confirm
      only so far); (b) first sizing change from the roster table; (c) first real detach-then-re-attach
      of a whale -> the FIRST multi-span Tenure row on prod (today every key has <=1 event, single-span);
      (d) the splits STALE banner on live prod (data was fresh at post-check, render-proven only);
      (e) first Analyze/Promote/Demote interplay is unchanged and not re-exercised this session.
OPEN BACKLOG (verbatim from the handoff): extract the soccer / tennis / UFC / CS2 matchers' Poly-slug
      grammar (game/market/side decode) into a DATA-ONLY module pm_web can import without the broker
      (mirroring how sports_structural_match was carved out), then add those categories to the splits
      coverage; until then they honestly show "no structural decode". Also parked: the ITF tile logo
      (file not yet supplied -- wire-in is a one-file static add + cache-bust bump when it arrives).
NOTHING deployed, restarted, or written to the box in this close-out -- housekeeping only (RO box reads
      + git docs on this log's own branch).

----------------------------------------------------------------------------------------------------
2026-09-25 -- DEPLOY 17: TABLES PHASE 1 (Farm watchlist/prospects server-side sort + Analyze states)
----------------------------------------------------------------------------------------------------
WHAT LANDED: the /farm/{category} page's TWO tables (Watchlist + Prospects) now sort SERVER-SIDE from the
      URL (?wsort/?wdir and ?psort/?pdir), so a click reorders via a plain link and it works with JavaScript
      off. The old client-side sorter (pm_sort.js) is no longer used by these two tables (its <script> line
      was removed from pm_farm_category.html); the file itself is left on the box (still served, harmless).
      Also: on Prospects, the Analyze button is now state-aware -- an un-analyzed whale shows "Analyze"; an
      analyzed one shows "View result" (free, cached) + "Re-analyze" (paid re-run) + how old the score is.
COMMIT: prod-live 558fc143 -> 6eb797d0 (the 5 code/template files) -> 507053f6 (this handoff-docs commit).
      Tag pm-tables-deploy17-p1-2026-09-25 -> 6eb797d0. Five files only: web/app.py, web/live_view.py,
      templates/partials/pm_prospects_rows.html, templates/partials/pm_watchlist_rows.html,
      templates/pm_farm_category.html. No new file, no CSS, no migration, no engine file.
HOW: git-archive of 6eb797d0 -> tar -> scp -> a box .sh that drift-gated (box==prod-live BEFORE), backed up
      to /home/azureuser/pm_d17_backup_20260925T201753Z (verified), applied, verified each file's CR-sha ==
      target, py_compile, auto-rollback on any miss. Then ONE `systemctl restart prediction-markets-web`.
SERVICES: pm_web MainPID 523799 -> 569065 (ActiveEnter 2026-09-25 20:21:57 UTC, active/running). Engine
      trading-corp MainPID 534581 / NRestarts 0 -- UNCHANGED before and after every step (never touched).
      box == prod-live 48/48 before and after. journalctl -p err since the restart: pm_web 0, engine 0.
CHECKED ON PROD (read-only): /farm/mlb + nfl + nba + splits + /live + a /live detail + / + all 4 account
      pages all 200 and styled. Watchlist + Prospects DEFAULT order unchanged (34 and 48 rows, byte-for-
      byte vs before). URL sort reorders both tables (and a second category). Analyze states render.
      "pinned"/"candidate" = 0 in the served HTML; raw tickers = 0. Static assets all 200; pm_desk.css
      unchanged (0b50095b). Engine kept trading (order count moved -- pm_web cannot write orders).
STILL TO COME (built + pushed, awaiting their own deploys, stacked on p1): Deploy 18 = Phase 2 (splits
      Whale-Grid header: live-copied highlight + accounts on hover + paper W-L), branch
      pm-tables-p2-2026-09-25; Deploy 19 = Phase 3 (non-MLB /live Active/Complete flat sortable tables +
      trade-drawer series-tag floor), branch pm-tables-p3-2026-09-25. Deploys go 1 -> 2 -> 3, FF each time.

----------------------------------------------------------------------------------------------------
2026-09-25 -- DEPLOY 18: TABLES PHASE 2 (splits Whale-Grid header: live highlight + accounts + paper W-L)
----------------------------------------------------------------------------------------------------
WHAT LANDED: the /farm/{category}/splits "Whale Grid" (?view=grid) column headers now show, per whale: a green
      LIVE highlight if we live-copy that whale (with the account names on hover), and the whale's paper win-loss
      record + win% (same numbers as the Farm Watchlist), with "--" when nothing has closed and a THIN mark under
      50 closed. The grid CELLS (who is on which side) are unchanged -- header-only.
COMMIT: prod-live 507053f6 -> a5db434b (the 3 files) -> 8e450210 (this handoff-docs commit). Tag
      pm-tables-deploy18-p2-2026-09-25 -> a5db434b. Three files: web/app.py, web/live_view.py,
      templates/pm_farm_splits.html. No new file, no CSS, no migration, no engine file.
HOW: same as Deploy 17 -- git-archive of a5db434b -> tar -> scp -> box .sh (drift-gate box==prod-live BEFORE,
      backup to /home/azureuser/pm_d18_backup_20260925T205926Z, apply, verify each CR-sha == target, py_compile,
      auto-rollback on any miss) -> ONE `systemctl restart prediction-markets-web`.
SERVICES: pm_web MainPID 569065 -> 569992 (ActiveEnter 2026-09-25 21:00:09 UTC). Engine trading-corp MainPID
      534581 / NRestarts 0 -- UNCHANGED before/after every step. box == prod-live 48/48 before and after.
      journalctl -p err since restart: pm_web 0, engine 0.
CHECKED ON PROD (read-only): mlb grid -- of our 4 live-copied whales, ONE (0x684baa) currently holds an open mlb
      position so it is the one grid column, and it is highlighted with "LIVE on Jack, Karen, Marc, Trey"; the
      other 3 have no open mlb position so they are not columns (correctly not highlighted). Header W-L matches
      farm_rows (e.g. 65-47 58%). Grid cells: the body template is byte-unchanged (only the header <th> + 3 CSS
      rules changed); the cells' hash moved only because the paper poller updated open positions ~4 min earlier.
      mode=trusted / view=splits / view=heatmap render; nfl grid renders; atp shows "NO STRUCTURAL DECODE".
      "pinned"/"candidate" = 0; raw tickers = 0. Phase 1 still works (?wsort sort + Analyze states). /live both
      tabs, a /live detail, /, the account pages all 200 and styled. Static all 200; pm_desk.css unchanged.
STILL TO COME: Deploy 19 = Phase 3 (non-MLB /live Active/Complete flat sortable tables + trade-drawer series-tag
      floor), branch pm-tables-p3-2026-09-25 @ 60bbe750 (rebased onto the new prod-live). FF prod-live next.

----------------------------------------------------------------------------------------------------
2026-09-25 -- DEPLOY 19: TABLES PHASE 3 (non-MLB /live Active/Complete tables + trade-drawer floor) -- WORKSTREAM CLOSED
----------------------------------------------------------------------------------------------------
WHAT LANDED: every NON-MLB /live sub-division detail page (nfl, atp, wta, nba, nhl, ufc, cfb, ... -- 90 subs across
      the 4 accounts) now shows its Active and Complete positions as real flat SORTABLE tables instead of the old
      group-by-game blocks. Game is a column (matchup + event date); every row has a placement time (ET) + order id.
      Complete defaults to settled newest-first (the "random" order you saw was alphabetical-by-ticker -- fixed);
      Active defaults to event date ascending. Click any column header to sort (it is in the URL, works with JS off).
      Also: the trade drawer no longer leaks the raw Kalshi series tag (KXATPMATCH...) as the bet name for tennis/UFC/
      fed -- those now show a clean "ATP"/"TENNIS" floor, and the raw tag survives ONLY in the drawer's labelled
      "Ticker" provenance field. MLB is untouched (it keeps its game cards).
COMMIT: prod-live 8e450210 -> 60bbe750 (the 3 code files + the build report + render PNGs) -> 30106ccf (this
      handoff-docs commit). Tag pm-tables-deploy19-p3-2026-09-25 -> 60bbe750. Three code files: web/app.py,
      web/live_view.py, templates/pm_live_subdivision.html. No new file, no CSS, no migration, no engine file.
HOW: same as Deploy 17/18 -- git-archive of 60bbe750 -> tar -> scp -> box .sh (drift-gate box==prod-live BEFORE,
      backup to /home/azureuser/pm_d19_backup_20260925T225031Z, apply, verify each CR-sha == target, py_compile,
      auto-rollback on any miss) -> ONE `systemctl restart prediction-markets-web`.
SERVICES: pm_web MainPID 569992 -> 571194 (ActiveEnter 2026-09-25 22:51:19 UTC, active/running). Engine trading-corp
      MainPID 534581 / NRestarts 0 / boot 2026-09-22 21:11:47 UTC -- UNCHANGED before and after every step (never
      touched). box == prod-live re-verified 0 mismatches. journalctl -p err since restart: pm_web 0, engine 0.
CHECKED ON PROD (read-only, waited a poll cycle first): NFL jack Complete = 79 rows (== the journal), all 79 order
      ids present, ordered settled newest-first, OPPOSED rows show "not booked" (12), whale-exit rows show "--" (1);
      the realized column sums to 9.87 which is exactly the journal's per-row-2dp sum (the journal's unrounded total
      is 9.9285 -- the 6-cent look is just each row shown to the cent, proven with a reconcile query). NFL Active = 4
      rows (== journal), event-date ascending, cost $21.50, order ids 2417/2421/2422/2660. URL sort reorders both
      tabs (?psort=cost&pdir=desc, ?psort=realized&pdir=asc). Tennis jack/atp Complete = 30 (== journal, realized
      -6.72 col / -6.7264 unrounded), Active = 0. Drawer floor on atp: 0 raw tags in the Type or Market labels, 33
      floored "ATP" Type cells, raw KXATPMATCH only in the 33 Ticker fields. MLB still shows cards (no table).
      FULL SWEEP: all 90 sub-divisions x 2 tabs = 180 pages returned 200. Phase 1 + Phase 2 still work. "pinned"/
      "candidate" = 0; pm_desk.css unchanged (no CSS shipped); 0 template errors across the whole 180-page sweep.
      Engine kept trading throughout.
CLOSED: this was the last of the three table passes -- Deploys 17 (Farm sort + Analyze states), 18 (splits grid
      header), 19 (live tables + drawer floor) are all live on prod-live. Two small follow-ups filed to backlog:
      (1) a player-code -> real-name map for tennis/UFC/fed so their bet label can be a human matchup instead of the
      "ATP"/"TENNIS" floor; (2) the roster sort and the positions sort share the /live URL but don't compose (sorting
      one resets the other to default) -- minor, both work on their own.
