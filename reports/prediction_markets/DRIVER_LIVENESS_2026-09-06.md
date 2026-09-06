# DRIVER LIVENESS — build handoff (2026-09-06). KEEP CURRENT AS RUNGS LAND.

## Context
2026-09-04 the PM driver was silently deleted from `main.py` (a co-tenant wholesale deploy) and **PM did not trade
for ~28 hours, undetected** — the nine arm rows were all correctly armed the whole time. **Arm state answers "is
this supposed to trade?"; it CANNOT answer "is the driver actually running right now?".** This builds that missing
signal: an engine-written per-sub-division heartbeat pm_web reads and bands by age. Plan of record:
`.claude/plans/lovely-puzzling-wind.md`. Branch `pm-driver-liveness-2026-09-06` (worktree `cc-pm-liveness-wt`),
base `3f498d4` (multicat, == box engine). Standing: 2 accounts, 8 armed subs on mlb/ufc/atp/wta.
STOP: `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.

## ★ THE MIGRATION-NUMBER GATE — settled: 020 (contiguous), collision resolved AT DEPLOY
Checked empirically (not reasoned): the highest migration claimed IN CODE across **every** branch is **019**
(multicat); the **box live schema head is 19**; no branch has 020 in code. pm-ui-rewrite carries 017 and the
migration-019 banner reserves **020+** for it ("close to deploying").
- **Why 020 and not a higher 'safe' number:** `db.py` migrations are **CONTIGUOUS by a tested invariant**
  (`test_schema_head_tracks_migrations`: `[v...] == range(1, HEAD+1)`). A gap (021 with 020 empty) breaks it, and
  weakening the invariant would also mask ACCIDENTAL gaps. So the next migration is necessarily 020.
- **init_db is a SINGLE-MAX counter** (`db.py`: `if version <= current: skip`) — a same-number collision **silently
  skips** the loser's DDL → a monitor that lies all-NEVER on a box that thinks it migrated. This is the same class
  of hazard that hid the 28-hour outage.
- **★ Resolution is at DEPLOY, not by the number:** the L1/L2 deploy GATE drift-checks the **live box schema head
  == 19** immediately before `init_db`. If it moved (pm-ui-rewrite shipped their 020 first → box 20), the deploy
  ABORTS and I renumber this to **021** (still contiguous, after the box's 20) + graft `db.py` file-by-file. Either
  way the heartbeat table is applied, never silently skipped.
- **★ Jack-note / coordination:** the contiguous-schema model means two workstreams can't independently pick numbers
  — they serialize (whoever deploys first takes 020; the other rebases to 021). Coordinate the pm-ui-rewrite agent,
  or let deploy-order + the drift-check handle it. If you'd rather cede 020 up front, the only mechanism is a gap,
  which needs the contiguity test relaxed — NOT recommended.

## Deploy shape (Jack's question answered)
- **L1 + L2 ship on ONE engine bounce** (Jack prefers one bounce). Order within it: **migration 020 LEADS the code**
  (apply via `init_db` — a live DB write, Jack authorizes — BEFORE the restart that runs the writer), mirroring the
  M3 precedent ("the deploy applies the migration BEFORE this restart; the writer also fail-softs if the table is
  absent"). Files: `db.py` (migration 020) + `heartbeat.py` (new) + `live_driver.py` (the writer). Engine restart
  bounces every division — warn co-tenants first and re-run the driver-restore-style post-check.
- **L3 is a separate pm_web restart** (no engine touch) — `web/app.py` (GRAFT) + `live_view.py` + templates.

## Rung status
- **L1 — migration 020 + `heartbeat.py` + offline tests.** DONE. `pm_driver_task_heartbeat` (per account) +
  `pm_driver_heartbeat` (per account,category) [migration 020, loud banner, contiguous]. `heartbeat.py` (pm_web-safe,
  no engine import): `upsert_task_alive/reached/evaluated/mark_skipped` (commit each), `liveness_band`
  (**both-directions** — a future ts reads STALE, never fresh-forever), `table_present` (absent-vs-empty),
  `read_liveness` (starts from the EXPECTED attachment-gated set so a NEVER-spawned sub is caught; 6 states
  RUNNING/IDLE/CATEGORY_STARVED/STALE/NEVER/PENDING; attach-grace → PENDING; ceiling_latched → IDLE not a fault),
  `any_alarm`. `test_heartbeat.py` (9 tests) green incl the **incident-shape unit acceptance** (8 expected subs,
  no heartbeats, past grace → all NEVER → alarm), both-directions band, expected-set-driven NEVER detection. Full
  `test_db` + `test_heartbeat` = 22 green.
- **L2 — engine writer in `live_driver.py` (3 grains, fail-soft, no-lie proof).** DONE. `live_driver` imports
  `heartbeat`; writes wired: **task_alive** per-account at the TOP of the while-loop (OUTSIDE the cycle try, own
  short-lived connection, own fail-soft); **reached** as the FIRST statement in `for c in cats` (before the
  no-builder/no-ctx continues) + **mark_skipped** on those two continues; **evaluated**+summary after
  `run_live_arm_gated_cycle`. Every write via `heartbeat.safe_beat` (swallows any error -> a liveness write NEVER
  kills a trading cycle). Tests appended to `test_live_driver_r7c.py` (reuses its offline harness) + a `safe_beat`
  unit in `test_heartbeat.py`: **(1) no-lie** (`_max_cycles=0` -> the loop body never runs -> ZERO heartbeat rows),
  **(2) three grains on a cycle** (task_alive + reached + evaluated=='evaluated', read_liveness -> RUNNING/IDLE, no
  alarm; ctx builder injected so the evaluated path runs offline without pykalshi), **(3) fail-soft** (a raising
  `upsert_evaluated` does NOT propagate; the cycle completes, task_alive still writes). r7c: **7 failed/25 passed
  (BASE) -> 7 failed/28 passed (L2)** = +3 passing, 0 new failures (the 7 are pre-existing pykalshi box-only tests,
  green in L4 box-scratch). ★ Trading path otherwise byte-unchanged (only additive heartbeat calls; the arm gate,
  place path, journal, opposed guard, settlement all untouched -- to be re-confirmed by L4 adversarial review).
- **L3 — pm_web liveness panel (6 states) + incident acceptance test.** DONE (commit `d347d0d`).
  READ-ONLY display of `heartbeat.read_liveness` over the EXPECTED (attachment-gated) set, banded by age.
  `web/app.py` (GRAFT-shaped, additive): `heartbeat` import; `_load_accounts_overview` attaches per-account
  liveness + a visible-scoped flat list + `any_liveness_alarm`; `_load_account` attaches per-sub `liveness_rows`
  + `liveness_alarm`; `_load_live_subdivision` attaches the single-sub row. ★ **ABSENT-vs-EMPTY gate:** every
  alarm bool is `liveness_present and any_alarm(...)` -- a NOT-deployed monitor (table absent) reads NEUTRAL,
  never red (don't cry wolf about the monitor's own absence -- the silently-skipped-migration hazard). New
  `web/templates/partials/pm_liveness.html`: SELF-CONTAINED `pm-lv-*` **classes** (NO `pm.css`/`pm_desk.css`
  edit -- owned by pm-ui-rewrite); ★ style hooks are CLASSES, the `data-liveness-*` attributes are
  inspection-only (never in a selector) so a test's substring check counts rendered elements, not CSS.
  `pm_accounts.html`/`pm_account.html`/`pm_live_subdivision.html` render the panel/badge. Six states:
  RUNNING/IDLE healthy; PENDING (fresh attach) + CATEGORY_STARVED informational; STALE + NEVER = red alarm;
  ceiling_latched -> IDLE (not a fault). `test_liveness_web.py` (9, all green): **★ THE INCIDENT** (8 subs, no
  heartbeats, past grace -> all NEVER, `data-liveness-alarm="1"`, "driver NOT running", no green ok) + the
  **28h-stale** shape (STALE, age reads "28h ago") + all-RUNNING green + **PENDING on fresh attach** (not
  alarm) + **ceiling_latched -> IDLE** (not a fault) + **table-absent -> neutral** + account-page all-NEVER
  alarms + **CATEGORY_STARVED is NOT an alarm** + the live-sub badge. ★ **Load-bearing property proven:** an
  alive account TASK means no sub can be NEVER/STALE (those are task-level) -> RUNNING and a hard alarm never
  co-occur on one account; per-category degradation is the softer CATEGORY_STARVED. `test_web_healthz`
  (pm_web-imports-no-engine) still passes -- `heartbeat` imports only `time`+`dataclasses`. Full
  web+heartbeat+db+m4 suites green locally (p2venv).
  - ★ **BASE-MISMATCH NOTE for the L3 deploy (like farm-search):** this worktree's web lineage (multicat
    `3f498d4`) is OLDER than the box's DEPLOYED web (DEPLOY 5, pm-ui-rewrite lineage -- box has `live_view.py`,
    this base does NOT; the loaders live in `app.py` here). So the L3 web files GRAFT onto the box's newer web
    file-by-file at deploy; verify the anchors (`_load_accounts_overview`, `_load_account`,
    `_load_live_subdivision`, the account/overview/live-sub templates) still exist on the box web before
    grafting. `partials/pm_liveness.html` is NEW (drops in clean). This is an L5 reconcile step.
- **L4 — box-scratch + adversarial review.** DONE.
  - **BOX-SCRATCH GREEN** (runner `cc/pm_liveness_scratch.{ps1,sh}`, git-archive HEAD -> isolated box scratch tree,
    box venv `-p no:pytest_ethereum`; the LIVE tree + engine untouched). **114 tests pass** across
    test_heartbeat + test_live_driver_r7c (incl. the pykalshi box-only tests that can't run locally) +
    test_liveness_web + test_web_healthz + test_db + test_web_r4/r6 + test_accounts_m2 + test_m4_gates. Invokability:
    `pm_web app + heartbeat import OK` (the isolation invariant holds in the box service env), `live_driver +
    heartbeat import OK`, `SCHEMA_HEAD=20 contiguous=True has020=True`. Engine PID 206872 UNTOUCHED before + after.
  - **ADVERSARIAL REVIEW** (independent audit + my own order-path read). ★ **ORDER-PATH SAFETY: CLEAN** — the one
    real risk (the reached/evaluated heartbeat writes call `conn.commit()` mid-cycle on the order path's OWN
    connection) is a NON-issue because the PM DB connection is opened **`isolation_level=None` (SQLite AUTOCOMMIT,
    `db.py:74`)** — there is no multi-statement transaction to split; the order path already self-commits per write
    (`_record_order`/`_finalize_order`); and there is **NO `conn.rollback()` anywhere in `live_driver.py`** (the
    cycle `except` only logs). So a heartbeat commit cannot early/partial-commit order/Journal/opposed-guard/
    settlement writes or defeat a rollback. Verified, not assumed. Fail-soft completeness, the cannot-lie property
    (no heartbeat write outside the loop body; `main.py` has zero heartbeat refs), template autoescape (no `|safe`;
    styling is class-based, `data-liveness-*` inspection-only), both-directions age (a future ts -> STALE/dead,
    never fresh), and migration contiguity: ALL substantiated clean.
  - **★ FLAG FOR JACK (finding #4, NOT fixed — a documented design decision, your call):** the GET route
    `/live/{account}/{category}` (`app.py:651`) has **no authz scoping** — unlike `/` and `/account` it never
    resolves identity/checks `visible_account_ids`, so within the authenticated family a non-owner can view any
    sub's page. This is **PRE-EXISTING and INTENTIONAL** — the R3/R6 author's own comment (`test_live_r3.py:66`):
    *"the /live pages themselves are not scoped; this header is inert there."* It already openly shows orders /
    positions / copied-whale identities; the liveness feature adds only a small RUNNING/STALE badge + detail to
    that surface. I did NOT change it: reversing a documented decision belongs to you, and a naive
    `active_accounts`-based gate carries real regression risk (the schema-9 honest-empty path + tile-on-create
    without a `pm_account` row would flip 200->404). **Minimal fix if you want it closed** (own test pass, not a
    freebie): mirror `account_page`/`_load_account` — resolve `identity, is_admin` in `live_subdivision_page`, pass
    into `_load_live_subdivision`, return `_FORBIDDEN` (403) when `account_id not in visible_account_ids(...)`,
    guarding the schema-9/no-account cases. All current `/live` tests run as admin, so admins are unaffected.
- **L5 — stage deploy (manifest, migration ordering, post-check, stop conditions). BUILT + STAGED. HALTED for Jack.**
  Branch `pm-driver-liveness-2026-09-06` PUSHED (commits `e4773a0` L1, `a892dfe` L2, `d347d0d` L3, `48a02fb` L4).
  Read-only recon RAN (`cc/pm_liveness_deploy_recon.{ps1,sh}`, engine+pm_web PIDs untouched) — results below.

## ★★★ STAGE 1 APPLIED 2026-09-06 ~03:01Z (migration 020 + engine graft). AWAITING JACK RESTART of trading-corp.
Board-authorized atomic Stage 1 executed via `cc/pm_liveness_stage1b_apply.{ps1,sh}`. ★ DRIFT FOUND + RECONCILED:
the box engine `db.py` had **drifted from my multicat base** (comment-only — a 4-line migration-017 back-port note +
one trailing comment near line 811/889, FAR from my insertion points). The FIRST runner (`pm_liveness_stage1_apply`,
box==base identity model) correctly **FAIL-CLOSED** on that drift rather than blind-overwrite. Reconciled file-by-file:
grafted my two additive MIGRATION_020 hunks onto the **box's** db.py (`box_db.py` sha d43e941f -> grafted 39b5ac8e,
**0 box lines removed**), keeping the box version as truth (box-is-truth). `live_driver.py` was byte-identical to my
base (da62db40) so HEAD placed cleanly (sha 732fcaab). `heartbeat.py` new (0dcc1114). Sequence run: preflight (import
origin) -> gate re-run (head **19** GREEN) -> Gate-1 DB backup + `integrity_check=ok` -> graft db.py -> **init_db ->
head 20, both heartbeat tables created (0 rows, writer not live yet)** -> graft live_driver.py + drop heartbeat.py ->
Gate-A (pm_web+heartbeat import OK / live_driver+heartbeat OK / SCHEMA_HEAD=20 contiguous has020) -> HALT. Engine PID
206872 + pm_web 205353 **UNCHANGED** (no restart). Backup+rollback: `~/pm_liveness_stage1b_backup_20260906T030140Z/`
(db.py.orig, live_driver.py.orig, prediction_markets.db). ROLLBACK = restore the two .orig + `rm heartbeat.py` (020 is
additive+fail-soft; tables may stay). Post-check runner staged: `cc/pm_liveness_stage1_postcheck.{ps1,sh}` (read-only)
— run AFTER Jack's restart; headline = ALL EIGHT SUBS WRITE A HEARTBEAT with all THREE grains populating.

## ★★ THE STAGED DEPLOY (manifest / ordering / post-check / stop). NOTHING MUTATING RUN. HALTED.

**Global STOP (never depends on any web surface, works with both down):**
`cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`

**Recon result (2026-09-06 ~02:29Z, READ-ONLY):**
- **[A] MIGRATION GATE = GREEN:** box live PM DB `/home/azureuser/trading_corp/data/prediction_markets.db`
  schema head = **19**, applied versions contiguous 1..19, both heartbeat tables ABSENT. **-> PROCEED with 020**
  (pm-ui-rewrite has not taken it). ★ RE-RUN THIS GATE immediately before Stage 1; if head moved to 20, ABORT +
  renumber 020->021 (still contiguous) + re-graft db.py, then proceed.
- **[B] ENGINE files graftable:** `heartbeat.py` ABSENT (new-file drop); `live_driver.py` anchors present at the
  SAME line numbers as my base (`cycles += 1`@703, `for c in cats`@735, `run_live_arm_gated_cycle`@848) with ZERO
  `heartbeat.` refs -> additive hunks apply cleanly; `db.py` carries `(19, MIGRATION_019)`, no 020. Confirm by a
  CR-stripped compare at Stage-1 build.
- **[C] PM_WEB base-mismatch CONFIRMED (DEPLOY-5 lineage):** the box web HAS `live_view.py` (my multicat base does
  NOT); the loaders sit at different lines; **`_load_live_subdivision` has a DIFFERENT signature**
  (`(account_id, category, now_ts)` + a `**ctx` merge from live_view); the templates are the larger pm-ui-rewrite
  versions (`pm_live_subdivision.html` 211+ lines vs my ~60). So **L3 must be RE-GRAFTED onto the box's DEPLOY-5
  web at deploy time, not file-copied.** `_load_accounts_overview`/`_load_account` signatures + the
  `shard_snapshot.read_latest`/return anchors DO match -> those two grafts map directly. `partials/` exists;
  `pm_liveness.html` drops in new.

### STAGE 1 — L1 + L2 (ONE engine bounce; migration LEADS the code). HALT: needs a live DB write + engine restart.
Files: `db.py` (+`MIGRATION_020` block after 019, +`(20, MIGRATION_020)` in `MIGRATIONS`; `SCHEMA_HEAD` auto-follows
the `max(...)`), `heartbeat.py` (NEW), `live_driver.py` (+`heartbeat` import, +3 grains, +2 mark_skipped). GRAFT
`db.py`+`live_driver.py` file-by-file (CR-stripped compare to prove only my additive hunks differ); drop `heartbeat.py`.
Ordering (mirrors the M3 shard-snapshot precedent — migration BEFORE the writer restart):
  1. Backup: `cp -r` the three engine files + a `sqlite3 .backup` of the PM DB to `~/pm_liveness_stage1_backup_<TS>/`.
  2. Re-run the [A] drift-check gate (head==19 or ABORT+renumber).
  3. **Apply migration 020 (LIVE DB WRITE — Jack authorizes):** `PYTHONPATH=. venv/bin/python -c "from
     trading_corp.prediction_markets import db; db.init_db()"` -> creates the two heartbeat tables, writes
     schema_version row 20. Idempotent; the writer also fail-softs if the table is absent.
  4. Place the grafted `db.py`/`live_driver.py` + new `heartbeat.py` into the live tree.
  5. **Restart `trading-corp` (ENGINE BOUNCE — Jack authorizes; warn co-tenants FIRST — it bounces EVERY division,
     mace + all 8 PM subs).**
POST-CHECK (Stage 1): engine MainPID changed + NRestarts sane + start clean; `schema_version` MAX=20 + both
heartbeat tables exist; within ~15s the driver writes rows — `SELECT account_id,category,state FROM
pm_driver_heartbeat` shows the 8 expected subs RUNNING/IDLE (not NEVER); `pm_driver_task_heartbeat` has 2 fresh
account rows; boot-reconcile clean; arm rows BYTE-UNCHANGED + still armed (global + jack/karen × mlb/ufc/atp/wta);
0 Traceback / 0 exposure_unknown; the 3 file hashes match the grafted versions. STOP CONDITIONS: any of — migration
did not reach 20, heartbeat tables absent after init_db, arm rows changed, a driver Traceback, or subs read NEVER
after ~1 min -> `live-disarm --global`, restore from the Stage-1 backup, restart, investigate.

### STAGE 2 — L3 (pm_web restart ONLY; no engine touch). HALT: needs a pm_web restart + prod file writes.
Because the box web is DEPLOY-5, this is a fresh graft built AT DEPLOY TIME against the then-current box web (NOT a
copy of my base files — those are the older multicat templates). Steps:
  1. Fetch the box's live `web/app.py` + `web/templates/{pm_accounts,pm_account,pm_live_subdivision}.html` +
     `web/live_view.py`; build the graft: (a) add `heartbeat` to the app.py `from .. import ...` line; (b) add the
     liveness read to `_load_accounts_overview` (anchor `shard_snapshot.read_latest`@~560 — direct) + `_load_account`
     (return anchor@~607 — direct) + `_load_live_subdivision` (ADAPT to its `(…, now_ts)` + `**ctx` shape); (c) gate
     every alarm bool on `liveness_present`; (d) re-apply the template edits (import `lv`, `liveness_assets()`, the
     panel/badge) to the DEPLOY-5 templates at their real structure; (e) drop in `partials/pm_liveness.html` (new).
  2. Box-scratch the grafted pm_web on the box venv (`test_liveness_web` + `test_web_healthz` + `test_web_r4/r6` +
     `test_accounts_m2` must pass; the pm_web-imports-no-engine invariant MUST hold).
  3. Backup the box web files; place the grafted files; **restart `prediction-markets-web` (Jack authorizes).**
POST-CHECK (Stage 2): pm_web MainPID changed + healthz 200; `/`, `/account/{id}`, `/live/{acct}/{cat}` all 200; the
liveness panel renders (RUNNING/IDLE after Stage 1) with `data-liveness-present="1"`; a deliberate read shows a
green panel now (not red); the pm-ui-rewrite DEPLOY-5 content (bet slots, whales, MLB card) is byte-intact (I only
ADDED a panel). STOP CONDITIONS: pm_web 500 / healthz fail / the pm-ui-rewrite content regressed / isolation import
guard fails -> restore the box web backup + restart pm_web. (The engine + arm state are untouched by Stage 2, so a
pm_web failure never stops trading.)

### Deploy-shape answer to Jack's question
L1+L2 = ONE engine bounce (migration 020 leads). L3 = a SEPARATE pm_web restart, no engine touch. Two restarts on
two different services (engine once, pm_web once) — never two engine bounces.

## Open rulings (write here; keep building around them)
- **Migration number:** recon says box head==19 -> **020 is free; PROCEED** (re-check at deploy). No renumber needed
  now. The contiguity invariant still means whoever deploys first takes 020; the drift-check handles the race.
- **★ Finding #4 — the unscoped `/live` GET route (L4 review):** pre-existing + documented-intentional. Ruling
  needed: leave as-is (my liveness badge is marginal on an already-open route) OR close it (own test pass — the
  minimal fix + its schema-9/tile-on-create risk are in the L4 section). Not blocking the liveness deploy either way.
