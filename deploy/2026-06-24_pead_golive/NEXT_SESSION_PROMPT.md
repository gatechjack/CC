New PEAD session, cold start. **PEAD went LIVE on prod 2026-06-25 01:02 UTC** (fractional + flag-2 deferred-fill reconcile, all 4 go-live flips). Its first autonomous scan + the **first-ever LIVE flag-2 reconcile** happen at the **2026-06-25 9:30 ET open**. Your job this session: **VERIFY that first live run worked end-to-end and surface anything wrong.** Do NOT place any real order without explicit operator go.

═══ STEP 0 — GROUND (observe, don't trust notes; state drifts) ═══
- Read memory in full: `pead-live-lifecycle-roundtrip` (the go-live record), `prod-sudo-constraint-no-password` (operator has NO sudo password — root edits via Azure Portal Run Command, SHORT lines only; NOPASSWD = systemctl/journalctl/sqlite3), `command-paste-rule`, `working-discipline`. Full deploy record: `deploy/2026-06-24_pead_golive/GOLIVE_RESULT.md` (also Desktop\bitunix_reports).
- Verify against the RUNNING prod engine (read-only SSH `azureuser@trading.jacksumner.com`): engine healthy (PID was 3511979; `:8000/healthz` LIVE); PEAD live (ExecStart `--live-divisions … robinhood_pead`, divisions.yaml standby:false, strategies.yaml auto_execute:true); Bitunix preserved (paper=False, fee-coupled taker 0.00019 / tp1 3.75 / maker 0.00014 intact, flat?).
- Branch `robinhood-pead-2026-06-20` (pushed to origin, UNMERGED). Worktree: `C:\Users\AA Incorporado\CC\.claude\worktrees\robinhood-pead-2026-06-20`.

═══ TASK 1 — VERIFY THE FIRST LIVE PEAD RUN (read-only) ═══
This is the production pre-open→open flow that was NEVER offline-proven (it went live without the gate34 proof harness). Confirm it actually worked. Use prod sqlite3 (NOPASSWD) on `/home/azureuser/trading_corp/data/trading_corp.db` + `audit_event` + `journalctl -u trading-corp`:
1. **SCAN** — did PEAD scan in the 8:30–9:25 ET window? `audit_event kind='pead_scan_done'` (entered N) / `'pead_scan_error'`.
2. **PENDING** — were `pending_order` rows written pre-open? Are ANY still `state='pending'` now? A stuck pending = un-reconciled = BUG. `SELECT * FROM pending_order`.
3. **RECONCILE** — at the 9:30 open, did the reconcile loop promote fills? `audit kind IN ('pead_entry' reconciled=true, 'pead_reconcile_promoted', 'pead_pending_collar_miss', 'pead_pending_dropped')`. Cross-check each pending → promoted/dropped (none left hanging).
4. **POSITIONS** — does acct 680725082 actually hold the fractional shares the records claim? RH MCP `get_equity_positions` 680725082; reconcile broker truth vs the `paper_trade_record` book (result IS NULL = open).
5. **ERRORS** — any tracebacks / reconcile errors / RH-auth failures since the open in journalctl?
6. **BITUNIX** — unaffected: still live, fee-coupled intact, no regression (reconciler match_count sane).

**THE key risk to hunt:** a fill that filled at the broker but did NOT promote to a record → an untracked/unmanaged position (real money, ~$7.50/name; the manage loop won't exit it). If found, surface immediately with diagnostics.

═══ SAFETY / CONSTRAINTS ═══
- Kill switch (hot, no sudo, no restart): `cc\golive_kill_pead.ps1` flips robinhood_pead `auto_execute:false` (mtime hot-reload; Bitunix untouched).
- Deploy/rollback: operator has NO sudo password — unit edits via **Azure Run Command** (short lines); `sudo cp/sed` will NOT work for the operator. Restart/daemon-reload are NOPASSWD. Rollback recipe in GOLIVE_RESULT.md.
- Stop-and-report at forks; surface anomalies with diagnostic detail; don't expand scope; tighter-than-normal commits. Delegate read-heavy discovery to Sonnet when sufficient. NO real orders without explicit operator go.

Report: Step 0 verification + Task 1 findings (did the first live pre-open-scan → open-reconcile path work? any stuck pendings, untracked positions, collar misses, or errors? Bitunix clean?).
