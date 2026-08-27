# Stage-0 Housekeeping — 2026-08-27 (read-only + one deletion)

Five items. Read-only box reads + the single authorized deletion in item 1. No code/deploy/restart/DB-write.

## 1. DB backup hygiene — DONE (one deletion)
- **1a. All 7 PM DB backups on the box** (schema · pm_watchlist active/inactive):
  | file | schema | watchlist | note |
  |---|---|---|---|
  | pm_stage0_rung3_dbbackup_20260827T130737Z.db | **8** | 114 (a1 114 / a0 0) | **CURRENT rollback instrument** |
  | pm_stage0_gate1_dbbackup_20260827T021526Z.db | 7 | (no active col) | rung-1 → **DELETED** |
  | pm_cp3b2_gate1_dbbackup_20260826T032502Z.db | 6 | (no active col) | obsolete (CP3b-2) |
  | pm_cp3a_gate3_db_backup_20260825T154847Z.db | 6 | — | obsolete (CP3a) |
  | pm_cp3a_gate2resume_db_backup_20260825T152544Z.db | 6 | — | obsolete (CP3a) |
  | pm_cp3a_gate2_db_backup_20260825T151528Z.db | 6 | — | obsolete (CP3a) |
  | pm_cp3a_db_backup_20260825T140605Z.db | 4 | (no pm_watchlist) | obsolete (CP3a) |
- **1b. Rung-3 backup verified intact:** sha256 == `9066a392…b9fa78` (recorded), integrity_check **ok**, schema 8,
  114 rows all active=1. Re-verified again immediately before the deletion.
- **1c. Deleted (only, as authorized):** `pm_stage0_gate1_dbbackup_20260827T021526Z.db` (schema-7 rung-1 backup —
  obsolete + a footgun under schema-8 code). Confirmed absent after.
- **1d. README written:** `~/PM_DB_BACKUPS_README.md` — names the rung-3 file as the current instrument, states
  what restoring each does, and flags the schema-mismatch footgun.

## 2. Box scratch sweep — LIST + RECOMMEND (no deletion; awaiting Jack)
- `~/pm_stage0_rung2_bak_20260827T123827Z/` (5 OLD pre-deploy files) — **RECOMMEND REMOVE.** Superseded: the
  deployed code is on `origin` (`c77f618`) and byte-verified `box==c77f618`. Agreed it can go.
- The 5 **obsolete PM DB backups** in item 1a (CP3a schema-4/6 ×4 + CP3b-2 schema-6) — **RECOMMEND REMOVE**
  (restoring any under schema-8 code is a footgun). Not Stage 0's, so not deleted under this authorization.
- Stage-0 sentinels/tars: already cleaned in rung-2. `/tmp/pm_*`: none. My runners are streamed via stdin and
  never persist on the box. Pre-existing prior-session box scripts (`pm_cp3b2_gate2_box.sh`,
  `pm_kalshi_restart_verify_box.sh`, `pm_p3_deploy_box.sh`, `pm_line_measure.py`) are NOT Stage 0's — left as-is.

## 3. Ref + state reconciliation — OBSERVED 2026-08-27 (~13:32Z)
The canonical end-of-Stage-0 snapshot lives in `TRANSITION_STAGE0_COMPLETE_2026-08-27.md §(ii)`. Summary:
`origin/prod-live c77f618` (95e78c4 ancestor ✓) · `origin/main 2c8aa23` · branch `df1300b` (local==origin) ·
**box==c77f618 all 5 PM files** · PM DB schema 8, 92 active / 22 inactive, paper 102 · `/farm` 200 / 182,835 ·
engine 89366 · pm_web 132990.

## 4. Transition doc — DONE
`TRANSITION_STAGE0_TO_NEXT_2026-08-27.md` marked **SUPERSEDED** (its "/farm unchanged is CORRECT" is now false);
new canonical handoff `TRANSITION_STAGE0_COMPLETE_2026-08-27.md` carries Stage 0 complete (rungs+dates+SHAs), the
snapshot, rollback instrument+limits, Stage 1 next+unprepared, migration renumber 009/010/011, R1/R2/R5,
operating rules, box quirks, and the two open items (PK-collision transient/deferred; engine PID 37596→89366).

## 5. Plan close-out — DONE
`PM_REBUILD_PLAN_2026-08-26.md`: STAGE-0-CLOSED banner at top; "live schema 7"→"schema 8", "NOT
deployed"/"NOT yet applied"→deployed. Verified internally consistent: migrations 009/010/011; 5-file deploy set;
no lingering "4 files" (the one mention is the correction note); the R1 rollup gate is durably in
`PM_REQUIREMENTS.md`, not only a migration comment.

**No code change, no deploy, no restart, no DB write. Stage 1 remains unauthorized and unprepared.**
