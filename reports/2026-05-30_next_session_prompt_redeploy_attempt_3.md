# Next-session prompt — Stage-1 prod redeploy attempt #3

**Prerequisites — must all be true before this session starts:**

1. Branch `stage1-blockers-items3-4-5-2026-05-30` is merged to `origin/main`. (HEAD at session-open time = the merge commit.)
2. Operator has reviewed the Item 5 sweep findings at `reports/2026-05-30_prod_vs_main_file_level_sweep.md` + `reports/2026-05-30_prod_vs_main_file_level_sweep_findings_analysis.md` and accepted the 65-file transfer-set baseline.
3. Prod state is post-rollback stable: `MainPID=1874494 NRestarts=0 ActiveState=active` (or whatever current stable state is — verify before starting).
4. The 03:57 UTC sed-overlay on `config/strategies.yaml` (bitunix paper-sizing) is either (a) merged from branch `bitunix-risk-tier-pre-live` into origin/main, OR (b) on the to-re-apply list for post-transfer.

**Session goal:** execute Plan A whole-file deploy to prod using the 65-file transfer-set baseline from Item 5's empirical sweep, NOT a `git diff`-derived manifest. Verify post-deploy stability via systemd PID change + healthz=200 + audit-row landing. Roll back byte-identically on any startup crash signature.

---

## Read first (in order)

1. `[[stage1-blockers-items3-4-5-resolved-2026-05-30]]` — what this session inherits.
2. `[[file-level-prod-vs-main-sweep-as-standing-discipline]]` — the new pre-deploy gate this session uses.
3. `[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]` — the gap this session's deploy mechanism closes.
4. `[[pre-deploy-filesystem-audit-discipline]]` — the audit-not-stale gate (still applies).
5. `[[stage1-redeploy-rolled-back-2026-05-30]]` — the rollback this session retries. **Note the CORRECTION header** — original diagnostic was misattributed; this session uses the corrected understanding.
6. `[[verify-premises-against-ground-truth]]` — the discipline that caught the prior misattribution.
7. `runbooks/deploy_log.md` — most recent entries cover the 17:22 + 22:43 rollbacks + this session's branch.

---

## Pre-deploy gate checklist (run sequentially, STOP on any failure)

1. **Full test gate:** `python -m pytest --continue-on-collection-errors --tb=no -q`. Expected: 2083 passed / 28 failed / 3 collection errors (pre-existing). Any new failure → STOP.

2. **Gate (a) REST resilience:** already LANDED on origin/main `eae5080`. No action.

3. **Gate (b) bitunix operational runbooks:** already LANDED via `f20a7bc`. No action.

4. **Gate (c) bitunix prod-surface md5diff:** `python scripts/bitunix_prod_surface_md5diff.py`. Should report DRIFT (bitunix files are in the DIFFER-STALE list) — that's expected because the bitunix surface IS what this deploy transfers. Confirm the drift signature matches the sweep's findings; cross-check md5s.

5. **Finding #9 filesystem audit (audit-not-stale re-probe):** run the per-file md5 cross-check against the most recent `reports/<date>_uncommitted_prod_surgical_edits_audit.md`. Expected: no new prod-only edits since the audit. Verify before proceeding.

6. **Item 5 sweep (NEW gate, this session's mainline gate):** `python scripts/prod_vs_main_file_level_md5_sweep.py --report reports/<deploy-date>_prod_vs_main_file_level_sweep.md`. Compare against `reports/2026-05-30_prod_vs_main_file_level_sweep.md` (the baseline). Expected:
   - MATCH count should be ≥ 185 (more if anything has been deployed since the baseline run; less is a HARD STOP).
   - DIFFER-STALE-ON-PROD list should be a SUBSET of the baseline's 51 entries (operator may have already transferred some).
   - MISSING_ON_PROD list should be a SUBSET of the baseline's 14 entries.
   - PROD_ONLY list: any NEW entries since baseline = HARD STOP (investigate).
   - Any net-new finding not in baseline = HARD STOP, surface to operator.

7. **Import-sanity check:** `python -c "from trading_corp.main import run; print('import_ok', run)"`. Catches module-import failures (necessary but not sufficient — does not execute `run()`).

8. **Construct the transfer manifest:** UNION of:
   - The current Item 5 sweep's DIFFER-STALE-ON-PROD + MISSING_ON_PROD (filesystem-derived baseline).
   - `git diff <last-deploy-pointer>..origin/main --name-only -- trading_corp/ config/` (diff-derived set).
   - Subtract the 1 known overlay (`config/strategies.yaml`) — that gets sed-reapplied post-transfer OR is already merged via overlay branch.
   - Result should be ~65 files (give or take a few based on what's happened since baseline).

---

## Deploy phase (only after all gates green)

Plan A pattern, refined for Item 5 transfer-set construction:

1. **Backup tag:** generate `pre-stage1-redeploy3-<YYYYMMDD>-<HHMM>`. The deploy script must back up every file in the transfer manifest before overwriting.

2. **Chunked transfer:** per the 18-file pattern from the 22:43 session, but with ~65 files. ~3x the transfer volume; budget ~25 minutes for the transfer phase (vs ~8 minutes for 18 files). Per-file md5-verify on prod after each chunk.

3. **HARD STOP before restart:** confirm transfer manifest covers all of:
   - 51 DIFFER-STALE-ON-PROD entries from baseline
   - 14 MISSING_ON_PROD entries from baseline
   - Any net-new entries surfaced by the current Item 5 sweep
   - `trading_corp/agents/divisions/tasty_options.py` (main.py:1234 import)
   - `trading_corp/agents/strategies/tasty_options_iron_condor.py` (main.py:1235 import)
   - `trading_corp/brokers/tastytrade.py` (main.py:1867 import)
   - `trading_corp/brokers/bitunix_exceptions.py` (transitive via bitunix.py)
   - `trading_corp/brokers/bitunix_symbols.py` (transitive via bitunix.py)

4. **Re-apply 03:57 UTC sed-overlay on `config/strategies.yaml`** AFTER transfer if the overlay branch isn't merged. Verify the resulting md5 matches the prod md5 from baseline (`61dd355082f936016810337058d30cd0`).

5. **RH-pickle-coordinated restart** per the standing pattern. Phone-in-hand for password-prompts.

6. **Healthz monitoring:** systemctl status, journalctl tail, healthz=200 expected at T+~5min30s. Crash signature in journalctl = ROLLBACK trigger.

---

## Rollback recipe

If startup crash signature appears within T+15min:

```bash
TAG=pre-stage1-redeploy3-<YYYYMMDD>-<HHMM>
BASE=/home/azureuser/trading_corp
ssh azureuser@trading.jacksumner.com "
for f in <transferred-files-list>; do
  if [ -f \$BASE/\$f.\$TAG ]; then mv \$BASE/\$f.\$TAG \$BASE/\$f; fi
done
rm -rf <net-new-files-list>
systemctl restart trading-corp
"
```

Net-new files in this transfer = the 14 MISSING_ON_PROD entries from baseline (these have no backup tag because they don't exist on prod yet — `rm -rf` them on rollback).

---

## Post-deploy verification

- Healthz=200 at T+5min30s (matches historical lazy-bind).
- New MainPID (different from pre-deploy PID).
- NRestarts=0 after the explicit restart.
- `journalctl -u trading-corp.service --since "<deploy-start>"` shows no AttributeError/TypeError/ImportError.
- Audit row landing: query `audit_event` for a `startup_complete` (or analogous) row with timestamp matching the new MainPID's start time.

If all green, append a deploy_log.md entry per the template at the top of the file.

If any crash signature appears, execute rollback recipe; preserve backup-tag files (do-not-delete); record forensics; STOP-and-report to operator.

---

## Out of scope for this session

- Execution_mode flip on bitunix (still paper-default).
- `auto_execute: true` on tasty_options (still paper).
- N+2 Phase 3 broker-write/safety/entry-path implementation.
- Investigation of the 14 MISSING_ON_PROD files' deploy history (separate P1 BACKLOG item filed by Items 3+4+5 session).
- Cleanup of PROD_ONLY anomalies (`config/Lets`, `main.py.orig`, `_observer_test.py` — separate P3 items).

---

## Hard stops

- Item 5 sweep surfaces NEW findings vs baseline (anything not in the 51+14+18 lists from `reports/2026-05-30_prod_vs_main_file_level_sweep.md`) → STOP, investigate.
- Any pre-deploy gate fails → STOP.
- Crash signature within T+15min after restart → ROLLBACK.
- More than 3 systemd restart iterations → ROLLBACK regardless of NRestarts.
- Operator-curated overlay (`config/strategies.yaml`) post-transfer md5 doesn't match expected → STOP before restart.

---

## Memory references this session may need

- `[[stage1-blockers-items3-4-5-resolved-2026-05-30]]`
- `[[file-level-prod-vs-main-sweep-as-standing-discipline]]`
- `[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]`
- `[[pre-deploy-filesystem-audit-discipline]]`
- `[[gate-c-md5diff-landed-2026-05-30]]`
- `[[stage1-redeploy-rolled-back-2026-05-30]]` (read the CORRECTION header)
- `[[bitunix-risk-tier-and-leverage-pre-live]]` (the 03:57 overlay)
- `[[verify-premises-against-ground-truth]]`
- `[[reference-az-run-command-stdout-cap]]`
- `[[deploy-mechanics-crlf-config-patch]]` (gate (c) tool's CRLF discipline)
