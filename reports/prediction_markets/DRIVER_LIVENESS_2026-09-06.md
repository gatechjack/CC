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
- **L2 — engine writer in `live_driver.py` (3 grains, fail-soft, no-lie proof).** pending.
- **L3 — pm_web liveness panel (6 states) + incident acceptance test.** pending.
- **L4 — box-scratch + adversarial review.** pending.
- **L5 — stage deploy (manifest, migration ordering, post-check, stop conditions). HALT.** pending.

## Open rulings (write here; keep building around them)
- The migration-number contention with pm-ui-rewrite (above) — recommendation given (020 + deploy-drift-check);
  Jack to coordinate or confirm.
