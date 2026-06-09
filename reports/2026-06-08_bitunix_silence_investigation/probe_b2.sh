#!/usr/bin/env bash
# Thread B call 2 (round 2): B3 Robinhood pickle state + B4-lite service-health anomaly scan.
# B1 already ruled out a service restart (MainPID=2043009, NRestarts=0). This confirms the
# pickle "reset" was a normal IN-PROCESS re-auth, and that no in-process anomalies marred the
# observation window. READ-ONLY (ls / find / journalctl reads only). Timestamps forced to UTC.
set -uo pipefail

echo "=== B3: Robinhood pickle file(s) — location + mtime + size ==="
echo "-- /home/azureuser/trading_corp/data/*.pickle --"
ls -la --time-style=full-iso /home/azureuser/trading_corp/data/*.pickle 2>/dev/null || echo "  (none)"
echo "-- /home/azureuser/.tokens/*.pickle --"
ls -la --time-style=full-iso /home/azureuser/.tokens/*.pickle 2>/dev/null || echo "  (none)"
echo "-- any *.pickle under trading_corp (bounded, with mtime) --"
find /home/azureuser/trading_corp -maxdepth 3 -iname "*.pickle" -printf "%TY-%Tm-%TdT%TH:%TM:%TSZ  %10s bytes  %p\n" 2>/dev/null | head -20
echo

echo "=== B3: Robinhood pickle/auth/login log activity (last 72h, UTC) ==="
journalctl -u trading-corp --since "72 hours ago" --utc --no-pager 2>/dev/null \
  | grep -iE "robinhood.*(pickle|login|auth|token|mfa|challenge|relog|session|expir)|(pickle|login|auth|token|mfa|relog|expir).*robinhood" | tail -25
echo "  ^ empty = no robinhood auth/pickle log lines in 72h"
echo

echo "=== B4-lite: service-health anomaly markers since deploy (2026-06-02 01:39, UTC) ==="
journalctl -u trading-corp --since "2026-06-02 01:39:00" --utc --no-pager 2>/dev/null \
  | grep -iE "Started trading|Stopped|Stopping|Failed|main process exited|Traceback|hold-off|segfault|OOM|Killed" | tail -25
echo "  ^ empty = no stop/restart/crash markers since deploy"
echo "=== END call B2 (read-only) ==="
