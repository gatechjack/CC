# PEAD GO-LIVE — RESULT: ✅ DEPLOYED + VERIFIED LIVE 2026-06-25 01:02 UTC

Operator-executed (Board-auth). Engine restart PID 3418240 → **3511979**, active 01:02:12 UTC.

**All 4 go-live flips ON + verified:**
1. divisions.yaml `robinhood_pead standby: false` ✓
2. ExecStart `--live-divisions bitunix_futures robinhood_pead` ✓
3. strategies.yaml `robinhood_pead auto_execute: true` ✓
4. EODHD via KV — `eodhd_api_key` loads True; engine actively calls EODHD ✓

**Verified:** PEAD execution_mode=live; **RH auth to 680725082** (the 22h-old pickle was valid — `connect()` reused it, no refresh needed); fractional+flag-2 code live; `pending_order` table created; scan + reconcile + manage loops all online; web `/healthz` LIVE + `/telemetry/pead` 200 (web server listens ~90s post-restart). **Bitunix PRESERVED:** paper=False, bar caches primed (3m/1h/4h/1d), fee-coupled intact (GATE 1 passed). No tracebacks. First live flag-2 reconcile run = 2026-06-25 9:30 ET open.

## KEY MECHANISM (operator has NO sudo password)
`--live-divisions` is in the root-owned unit `/etc/systemd/system/trading-corp.service`. Operator NOPASSWD scope = systemctl/journalctl/sqlite3 only — no file edits. So the unit edit was done via **Azure Portal → VM tc-prod-vm → Run command → RunShellScript** (runs as root), with SHORT lines only (the portal box wraps/breaks lines >~100 chars — long `sed`/`python` got newline-corrupted on the first try). Then the restart via NOPASSWD `sudo systemctl daemon-reload + restart`. See `prod-sudo-constraint-no-password` memory.

## Post-deploy script fixes (in this dir)
- `unit_flip_restart.sh` — rewritten NOPASSWD-only (flat-gate + ExecStart-has-robinhood_pead gate + `sudo systemctl` reload/restart); NO `sudo cp/sed` (the original needed a password the operator lacks).
- `bootsmoke.sh` — fixed 2 false-negatives: EODHD check now sets `KEY_VAULT_URI`; healthz retries ~100s (web server starts ~90s after restart).

## SAFETY NETS
- **Hot stop PEAD only (no sudo, no restart):** `cc\golive_kill_pead.ps1` → flips robinhood_pead `auto_execute:false` (mtime hot-reload; PEAD papers within a cycle; Bitunix untouched). Verified scoped to the robinhood_pead block only.
- **Full rollback to inert:** `golive_rollback.ps1` restores the 8 files (azureuser, no sudo) — BUT the ExecStart revert needs root: run via Azure Run Command:
  `sed -i 's/ robinhood_pead//g' /etc/systemd/system/trading-corp.service; systemctl daemon-reload`
  then `ssh … 'sudo systemctl restart trading-corp'` (NOPASSWD). (The packaged rollback's `sudo sed` line will NOT work for the operator — use Run Command for the unit revert.)
