# QUEUED — operator-run-runner / Jack-present verification (P1)

Everything here needs an **operator-run runner** (a box MUTATION) or Jack's presence. Read-only work is done autonomously via direct SSH/az. This list makes Jack's return **one decisive run**, not a discovery process. Appended as the build proceeds.

Status legend: `[ ]` not yet run · `[R]` ready (runner written + validated) · `[done]` completed.

---

## 1. Prove `trading_corp/__init__.py` is INERT (deliberately, not by accident)
- **Why:** in the 2026-08-22 B+C run, runner bug #1 silently failed the `cp` of `trading_corp/__init__.py`, and Python fell back to namespace-package resolution — so "no engine coupling" was shown *accidentally*. Fixed in the runner (`0eeb3a6`).
- **Needs:** a run that (a) copies `trading_corp/__init__.py` into scratch **successfully** (assert the file is present), AND (b) confirms `pytest tests/prediction_markets/` still passes **with it present**. Deliberate proof.
- `[done read-only 2026-08-22]` `pk_pm_pytest_ro.ps1` run: `trading_corp/__init__.py` was extracted into scratch (present in the file listing) AND `pytest tests/prediction_markets/` passed **with it present** -> init-inert shown deliberately (bug #1 fixed). Optional Jack-run re-confirm at deploy.

## 2. Chain-of-custody: tested bytes == committed bytes
- `git hash-object` per shipped file, **locally and in scratch**, pasted side-by-side, proving the box tested the exact committed bytes (not a drifted copy).
- `[done read-only 2026-08-22]` `pk_pm_pytest_ro.ps1` printed local sha256 and the box `sha256sum` — MATCH for db.py (`f6c900..`) + category.py (`82320a..`): tested bytes == committed bytes. Re-confirm on the final deploy run.

## 3. Legacy-DB integrity (CONTEXT, expected-to-differ) + the DISCRIMINATING proofs
- `md5 + mtime` of `trading_corp.db` (add `-wal`/`-shm`) before/after — **labeled expected-to-differ**: a 3 GB live-written WAL DB cannot be checksummed atomically (2026-08-22 run: md5 differed, size+mtime identical → live-engine mmap, not us).
- **Discriminating proofs (these are the real ones):**
  - no `trading_corp.db` anywhere under scratch after the run (`find $S -name 'trading_corp.db'` empty);
  - the resolved `PM_DB_PATH` printed by the run points to a `/tmp` file;
  - the `db.py` legacy-path guard provably **FIRES** — a test assertion (`test_refuses_legacy_db_path`), not an observation. (Already in `test_db.py`, passed on box.)
- `[done read-only 2026-08-22]` `pk_pm_pytest_ro.ps1`: `*.db` scan under scratch EMPTY; `PM_DB_PATH=/tmp/pm_test_<pid>.db`; `test_refuses_legacy_db_path` PASSED; legacy DB md5 identical before/after this run. Re-confirm on the final deploy run.

## 4. Live-API smoke test (`@pytest.mark.live_api`, opt-in only)
- `test_smoke_live.py` (§11): G0 probe + ordering probe, read-only, no DB writes. Runs only with the opt-in flag.
- `[ ]` written? not yet — build in Task 3; then queue the live run here.

## 5. §12 acceptance items requiring deploy/cron
- **Cron-slot pre-check (§10):** prove **03:00 UTC is clear** against the live box's `crontab -l` (azureuser AND root) + `systemctl list-timers` (known windows: tc-audit-reality ~06:00Z; MACE 15:45-15:58 ET; manage loop 09:35-15:55 ET). Read-only enumeration — CAN be done autonomously; the *decision to install* is queued.
- Nightly `refresh --from-rosters` cron install (03:00 UTC) — **mutation, queued.**
- `[ ]`

## 6. Deploy + prod-live advance
- Additive file copy of the package + `pm_cli.py` + `config/pm_seed_wallets.yaml` to `/home/azureuser/trading_corp` (NO restart, NO existing-file edits, NO sudo) — **mutation, queued.**
- On-box run order: `g0-validate` → `backfill --from-rosters --dry-run` → `backfill --from-rosters` → `report`.
- prod-live advance (commit deployed artifacts) — **mutation, queued.**
- `[ ]`

---
*Appended as the autonomous block proceeds. Anything I cannot verify read-only lands here.*
