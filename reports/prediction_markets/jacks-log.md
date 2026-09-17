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
