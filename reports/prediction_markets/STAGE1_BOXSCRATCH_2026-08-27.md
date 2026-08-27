# Stage-1 BOX-SCRATCH — RED (2026-08-27), 7 failures, STOPPED per instruction

**Read-only box-scratch** (scratch code + a `.backup` copy of live; **live DB byte-untouched, schema still 8**;
engine PID 676 / pm_web 652 unchanged across the run; scratch removed). Branch tip tested: `7070850`.
**Result: 7 failed / ~160 passed / 1 skipped. RED — no fix applied (Jack: report and STOP, do not iterate).**

## Harness correction (first run was a false 50-fail)
The first run showed 50 failures — a **runner provisioning bug of mine**: the scratch tar archived `trading_corp`
+ `tests/prediction_markets` but **not `pyproject.toml`** (which sets `asyncio_mode = "auto"`, required for the
async tests) **nor `tests/conftest.py`** (shared fixtures). Adding both to the tar and re-running gave the real
result below. (Correcting a mis-provisioned harness, not a code fix.)

## Stage-1 CORE — ALL GREEN
- **Adjudicator gamma re-base** (`test_stage1_adjudicate_gamma.py`): loss-omission fix, win, void, stale, pending,
  subset-assert, return shape — pass.
- **paper_rollup + R1 gate + airtight cleanup** (`test_stage1_paper_rollup.py`): R1 excludes deactivated pairs
  (rows survive), correct win_rate, honest-empty, ROI math — pass.
- **BASIS test** (pinned shows paper 0.40 not legacy 0.89): passes against NEW code; and **run against OLD
  `c77f618` farm.py it FAILS** (`got 0.89`) — confirming it is a real test, not a presence check.
- **Migration 009 proven on the `.backup` copy:** schema 8→9; `pm_paper_category_stats` present, 15 columns
  (correct shape); **grace re-tune = 259200 (72h)**; watchlist 92/22 intact.

## The 7 failures (exact reasons)
### GROUP A — schema-head-pin assertions (6): `assert 9 == 8` (mechanical, expected with any new migration)
These hardcode the schema head as 8; migration 009 makes it 9. **The standard bump 8→9** — the same maintenance
CP3b-2 did for migration 007 (`bump 4 schema-head assertions 6->7`). **My miss: I did not include these bumps in
the Stage-1 commit.**
- `test_caveat_analytics.py::test_migration_004_schema` (:150)
- `test_caveat_analytics.py::test_migration_004_idempotent_on_p1_shaped_db` (:177 — `3==3` passes, `9==8` fails)
- `test_db.py::test_migrations_idempotent` (:53)
- `test_names.py::test_records_last_run_and_schema_unbumped_by_pm_meta` (:123)
- `test_removal_gate.py::test_migration_008_adds_columns_index_head_8` (:81 — the name itself pins head=8)
- `test_web_healthz.py::test_healthz_ok` (:27 — `pm_db_schema_version == 8`)

### GROUP B — the grace re-seed in migration 009 is fragile (1): `no such table: pm_paper_config`
`test_removal_gate.py::test_upgrade_backfills_existing_rows_to_active_1` upgrades a hand-crafted **partial
schema-7 DB (pm_watchlist only, no `pm_paper_config`)** through migration 009; 009's grace
`INSERT OR REPLACE INTO pm_paper_config …` fails (`db.py:619`). **Live is unaffected** — the real DB has
`pm_paper_config` (migration 005 created it), so the live 8→9 path works — but the box-scratch correctly flags
that **bundling a config data-write into migration 009 is fragile on partial-upgrade paths.**

## Proposed fixes (NOT applied — awaiting your go)
- **GROUP A:** bump the 6 assertions `== 8` → `== 9` (one line each). This is completing the migration's expected
  head-pin maintenance.
- **GROUP B — needs your call on the mechanism** (the 72h ruling stands; only *how* to seed it is in question):
  - (i) **Guard it in 009:** prepend `CREATE TABLE IF NOT EXISTS pm_paper_config(key TEXT PRIMARY KEY, value TEXT
    NOT NULL, updated_ts INTEGER)` before the `INSERT OR REPLACE` (idempotent; makes 009 self-sufficient on any
    upgrade path). Minimal, keeps the seed in the migration.
  - (ii) **Move the grace re-tune OUT of the migration:** keep 009 pure DDL; set the live value via a
    Jack-authorized one-shot config `UPDATE` at rung 1/3, with `CONFIG_DEFAULTS=259200` as the code default.
    Cleaner separation (DDL vs data), but the live value then depends on a separate step.
  - My lean: **(i)** — smallest, keeps the ruling self-contained in the migration, and the guard is honest
    idempotent DDL.

**STOPPED. Live untouched. Awaiting authorization to apply the fixes + re-run box-scratch.**
