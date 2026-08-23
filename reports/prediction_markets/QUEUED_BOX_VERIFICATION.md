# QUEUED — operator-run-runner / Jack-present verification (P1)

Everything here needs an **operator-run runner** (a box MUTATION) or Jack's presence. Read-only work is done autonomously via direct SSH/az. This list makes Jack's return **one decisive run**, not a discovery process. Appended as the build proceeds.

Status legend: `[ ]` not yet run · `[R]` ready (runner written + validated) · `[done]` completed.

**P1 BUILD STATUS (2026-08-22): package COMPLETE + box-verified — 48 passed / 1 skipped** (db, category tier-1+2, ingest incl §3A invariant+event-group, rosters, stats + both routines + scoreboard, pm_cli incl `--only-wallets`). Chain-of-custody sha256 match; isolation clean; legacy DB untouched. All that remains is on this list (mutations / Jack-present).

## ★ VALIDATION GAP — READ FIRST (honest scope of what "green" proves)
**48/48 green is ON FIXTURES + offline logic. No backfill has ingested a single live row.** The tests
prove the parse/quarantine/rollup/ranking LOGIC is correct against hand-built fixtures; they do NOT
prove anything about the real data. Everything DATA-dependent is still UNKNOWN and only resolves after
the Step-3/Step-4 backfill (DEPLOY_SEQUENCE.md):
- **Real category coverage vs the §12 ≥85%-non-unknown bar** — unmeasured (fixtures are hand-picked).
  Live tier-2 tail-resolution was spot-checked read-only (8% of 504 tier-1-unknowns; EVENTS_TAG_SCHEMA.md
  Task 1e), but coverage over the FULL row set per wallet is unknown until backfill.
- **Real exclusion counts + the >10% `data_quality` contamination flag** — unmeasured. Whether the §3A
  quarantine fires at the right rate on live Fed negRisk data is exactly what Step 3 (Kickstand7) surfaces.
- **Manual net-verify (Kickstand7 Fed + the MLB clean exact-match)** — NOT run; QUEUED (NET_VERIFY_TARGET.md).
- **Idempotency / ≥3,000-row landing / per-wallet isolation on real pulls** — asserted by tests on
  fixtures + mocks, not observed on the live API.
- **Live-API smoke (§4 below)** — never run (opt-in flag).

**Closed read-only this session (no longer gaps):** the 12-wallet seed roster RESOLVES on the box
(2 live + 10 PCT-selected; Kickstand7/pako present) and the deploy `-C` path is correct (nested package
layout; all 3 targets absent = clean first deploy). See §6.

**★★ BLOCKING FINDING (2026-08-22 live reconciliation — `QUARANTINE_RECONCILE_2026-08-22.md`, §13A(f)):**
running the ACTUAL §3A ingest code on 4 whales showed **clause (a) [loss-exceeds-cost] FALSE-POSITIVES on
real single-game MLB losses** (SDTrading 5/462, xifutloong3 17/201) — `/closed-positions total_bought`
understates cost on scale-ins — so the quarantine EXCLUDES REAL LOSSES and biases the scoreboard UP.
Clause (b) [zero-cost phantom] is sound. Fed is NOT clean (Kickstand7 3/83; pako 0). **Ingestion (Steps 0-4)
is safe; the RANKING is not trustworthy until Jack decides the clause-(a) rework** (proposals in the reconcile
doc). This corrects the prior "four live categories are binary/clean" record (REALIZEDPNL_PROBE_RESULT.md).

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
- `[R]` `test_smoke_live.py` WRITTEN (skipif `PM_LIVE_API`!=1; offline suite skips it). **Queued live run:** on the box, `PM_LIVE_API=1 <venv> -m pytest tests/prediction_markets/test_smoke_live.py` (G0 + ordering probe, read-only, no DB writes).

## 5. §12 acceptance items requiring deploy/cron
- **Cron-slot pre-check (§10): `[done read-only 2026-08-22]`.** Findings: azureuser crontab = hourly `replay_audit_event` at :00 (top of EVERY hour incl 03:00; light, writes the LEGACY db not ours) + 08:30 divergence; root crontab = none; systemd timers near 03:00 = `update-notifier-download` 03:02, `e2scrub_all` 03:10, `systemd-tmpfiles-clean` 03:12 (trivial OS); `tc-audit-reality` 06:01 (clear); no MACE/engine timer (in-process, ~19:45-20:58Z entry / ~13:35-19:55Z manage). **RECOMMENDATION: schedule PM refresh at 03:20 UTC** (avoids the top-of-hour cron + the 03:02-03:12 OS-timer cluster; nothing else until 06:00). Separate DB means no lock contention even at 03:00, but 03:20 is cleanest.
- Nightly `refresh` cron install (proposed **03:20 UTC**) — **mutation, queued.**
- `[ ]` install decision + line is Jack's.

## 6. Deploy + prod-live advance  (pre-staged 2026-08-22 — full runbook in DEPLOY_SEQUENCE.md)
- `[R]` **Deploy runner** `cc\pk_pm_deploy.ps1` — additive file copy of the package + `pm_cli.py` +
  `config/pm_seed_wallets.yaml` to `/home/azureuser/trading_corp` (NO restart, NO existing-file edits,
  NO sudo; prints local+box sha256 for chain-of-custody). Parser-validated, 0 non-ASCII, no BOM.
- `[R]` **Rollback runner** `cc\pk_pm_rollback.ps1` — deletes the 3 PM paths + ONLY the PM cron line
  (preserves all others); leaves the separate PM DB inert. Parser-validated, 0 non-ASCII, no BOM.
- **Deploy `-C` path CONFIRMED read-only 2026-08-22:** box is the NESTED layout
  (`/home/azureuser/trading_corp/trading_corp/`); all 3 targets ABSENT = clean first deploy;
  `trading_corp/__init__.py` already on box (not shipped).
- **On-box run order (CORRECTED flags — the CLI has NO `--from-rosters` flag; roster is the default):**
  `g0-validate` -> `backfill --dry-run` -> **`backfill --only-wallets <Kickstand7>` (STOP, inspect)** ->
  (Jack authorizes) -> `backfill` (all 12) -> `report` -> cron `refresh` @03:20 UTC. Single-wallet
  checkpoint = Kickstand7 `0xd1acd3925d895de9aec98ff95f3a30c5279d08d5` (rationale: NET_VERIFY_TARGET.md).
- **Roster RESOLVED on box read-only 2026-08-22:** size 12 (2 live MLB SDTrading/xifutloong3 + 10
  PCT-selected incl Kickstand7/pako). This is the authoritative membership; re-confirm at Step-2 dry-run.
- prod-live advance (commit deployed artifacts) + merge `prediction-markets-p1` -> durable
  `prediction-markets` (NO main merge until cutover) — **git, queued (DEPLOY_SEQUENCE.md Steps 7-8).**
- `[ ]` execution (Jack-gated).

---
*Appended as the autonomous/interim blocks proceed. Anything I cannot verify read-only lands here.*
*2026-08-22 interim: pre-staged deploy+rollback runners + DEPLOY_SEQUENCE.md + NET_VERIFY_TARGET.md;
paid Task-1 reporting debt (EVENTS_TAG_SCHEMA.md a-g index); no mutations.*
