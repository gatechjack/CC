#!/usr/bin/env python3
# READ-ONLY ACCEPTANCE for the MACE STOP-HOTFIX. Run AFTER the restart + at least one manage tick
# (market window 09:35-15:55 ET). Proves: the spuriously-stopped XLE 57.5/56.5/67/68 rung SELF-HEALS
# (closing -> open via mace_closing_reopen) and NO new spurious stop fires on a healthy OTM condor.
# NO writes / NO broker.
import sqlite3, subprocess
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"; DB = ROOT + "/data/trading_corp.db"
WEDGED = "mace-XLE-2026-11-06-57.5-56.5-67-68-20260929"


def sh(cmd, timeout=90):
    try:
        return (subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout or "").rstrip()
    except Exception as e:
        return "[cmd-error] %r" % (e,)


since = sh("systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null "
           "| sed -E 's/^[A-Za-z]+ //' | awk '{print $1\" \"$2}'") or "1 hour ago"
print("server UTC:", datetime.now(timezone.utc).isoformat(timespec="seconds"),
      "| ET:", sh("TZ=America/New_York date '+%F %T %Z'"), "| boot-since:", since)

print("\n== 1) the spuriously-stopped XLE rung self-healed (closing -> open) ==")
try:
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; c.execute("PRAGMA query_only=ON;")
    for r in c.execute("SELECT rung_id,status,exit_reason,exit_ts,realized_pnl,substr(COALESCE(extra_json,'-'),1,70) e "
                        "FROM mace_rung WHERE rung_id=?", (WEDGED,)):
        print("   ", {k: r[k] for k in r.keys()})
    print("    EXPECT status 'open' (reopened by the stop self-heal); NOT a looping 'closing'.")
    print("  all CLOSING rungs now (spurious-stop blast radius should be empty/clearing):")
    for r in c.execute("SELECT rung_id,exit_reason FROM mace_rung WHERE status='closing'"):
        print("   ", {k: r[k] for k in r.keys()})
    c.close()
except Exception as e:
    print("[q-err]", e)

print("\n== 2) self-heal + no-new-spurious-stop audits since boot ==")
for pat in ["mace_closing_reopen", "mace_exit_rejected", "mace_exit_exhausted", "empty response"]:
    print("  %-22s %s" % (pat, sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -c '%s'" % (since, pat))))
print("  stop exit_starts since boot:",
      sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep mace_exit_start | grep -c stop" % since))
print("  mace_closing_reopen on the wedged rung:",
      sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep mace_closing_reopen | grep -c '57.5'" % since))

print("\n== 3) manage loop live + 0 tracebacks ==")
print("  Traceback:", sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -c Traceback" % since))
print("  /mace:", sh("curl -s -m 15 -o /dev/null -w 'HTTP %%{http_code}' http://127.0.0.1:8000/mace 2>&1"))
print("\n== ACCEPTANCE: PASS iff the XLE 57.5/56.5/67/68 rung is 'open' (reopened) AND the exit_rejected/")
print("   empty-response reject loop has STOPPED (counts flat since the first post-hotfix tick). ==")
