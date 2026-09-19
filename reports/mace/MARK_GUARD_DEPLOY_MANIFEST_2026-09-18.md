# MACE PT mark-trust guard — DEPLOY MANIFEST (2026-09-18)

Branch **mace-pt-mark-guard-2026-09-18** @ commit **67f687ec**, off box-truth **2362db46** (trigmid build).
Built + tested + committed. **Nothing deployed / restarted / DB-written.** DEPLOY + RESTART + DB-write are
Jack-reserved. Box-only graft (NO FF-push — origin/prod-live has diverged to 777e87a5 via PM; reconcile deferred).

## Base verify (STEP 0 — PASSED)
Box running files == trigmid build (per `mace_baseverify_ro.ps1`, box PID 474141, 18:17Z boot):
manager `a83b62e1`, execution `a65955bb`, strategy `5937a4e3`, config.py `30d9d99a`, notify `95abf231`,
config/mace.yaml `bfde856f1c46`. All OK -> safe to branch off 2362db46.

## The change (5 files grafted + 2 test files; MACE-scoped; NO shared-trio)
LF-normalized hashes = the expected box-after-graft values (files land LF via `tr -d '\r'`):

| file | LF md5[:8] | note |
|---|---|---|
| config/mace.yaml | ccfbbc3b (sha256 **931a8214be50**) | + `management.mark_guard` block |
| trading_corp/mace/config.py | 01117cca | `MarkGuardConfig` + parse/validate |
| trading_corp/mace/strategy.py | 69eef994 | pure `assess_pt_mark_trust` + `shorter_dated_same_strike_siblings` |
| trading_corp/mace/execution.py | 66dba55a | `RungStore.get_live_state` (additive read) |
| trading_corp/mace/manager.py | 1d4334d7 | `_pt_mark_guard` wiring + `_pt_mark_trust` state |
| tests/test_mace_strategy_manage.py | (test) | +11 pure guard cases |
| tests/test_mace_mark_guard.py | (test, new) | +2 manager-level cases |

- Gates **ONLY EXIT_PT** (stop/time/exdiv unguarded — risk-reducing, must fire). `close_rung` / deferral /
  hold / CLOSING logic UNCHANGED. NO volume gate. NO shared-trio (data_exec/ceo_graph/risk) file touched.

## config_hash MOVES (Jack-accepted)
**bfde856f1c46 -> 931a8214be50.** A config edit MUST move the hash. Boot-verify criterion FLIPS:
`config_hash != bfde856f AND == 931a8214be50` (== correct). The deploy ledger + memory anchors referencing
`bfde856f` update to `931a8214` in the wrap.

## Tests (box-scratch, /tmp, live tree UNTOUCHED, `-p no:pytest_ethereum`, box venv py3.12)
- Config parse: `mark_guard=MarkGuardConfig(enabled=True, frozen_cycles=2, sane_epsilon_usd=0.01, max_cycle_drop_pct=0.35)`; scratch config_hash `931a8214be50`.
- New guard tests: **31/31 PASS** (test_mace_mark_guard.py 2 + test_mace_strategy_manage.py 29 incl. 11 new).
- Full MACE suite (`tests/test_mace_*.py`): **7 failures, IDENTICAL to the unmodified-2362db46 baseline**
  (config value-drift, sizing 11==12, strategy_entry x2, p14 time-exit, web-reskin POP, migration) ->
  **0 NEW failures**. These pre-existing failures are config-shape / web-asset / env drift, unrelated to the
  guard (base config-parse threw `AttributeError: no attribute 'mark_guard'`, confirming the branch is the
  new working path).

## DEPLOY (RESERVED — Jack runs, after review)
1. **Graft** (staged runner `cc/mace_guard_graft.ps1`): scp+tar the 5 files, drift-gate box==2362db46 for
   ALL 5 (abort on any mismatch), backup to `~/mace_guard_graft_backup_<ts>`, `tr -d '\r'` -> cp, re-verify
   each LF md5 == the table above, py_compile. Partial-graft -> rollback from backup + abort.
2. **Restart** REQUIRED (config read once at boot): canonical `restart_tc.ps1`.
3. **Boot-verify** (RO runner `cc/mace_guard_bootverify_ro.ps1`): new PID, **config_hash 931a8214be50**,
   MACE wired OK (mark_guard block parsed), 0 import errors/tracebacks, 4 loops online, all divisions back,
   5 grafted blobs == table, arm state unchanged.

## DB RESET of the stuck XLE rung (RESERVED — Jack runs, AFTER boot-verify)
Staged runner `cc/mace_xle_reset.ps1` (guarded, gated behind the deploy):
- Backup the row, RO pre-verify (`status='closing'`, exit fields NULL, sibling-mark bad-quote check),
- `UPDATE mace_rung SET status='open' WHERE rung_id='mace-XLE-2026-10-30-60-59-70-71-20260917' AND status='closing'`,
- RO post-verify (`status='open'`, exit fields still NULL).
Sequencing is load-bearing: guard-first means a still-bad Monday 0.13 is REJECTED (rung HOLDS + alerts)
instead of the false PT re-firing and bouncing the rung back to CLOSING.

## Rollback
GIT-revert on the branch + re-graft the base 5 files from the backup + restart (per the standing rollback
rule — never a naive backup restore that leaves prod-live ahead; here box-only so no prod-live divergence).
