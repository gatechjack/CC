#!/usr/bin/env python3
# READ-ONLY post-restart boot verify for the MACE STOP-HOTFIX (2026-10-08). Code-only: config_hash
# UNCHANGED (931a8214be50). Proof = the 2 grafted .py md5s == targets (+ 3 unchanged) + NEW PID/boot
# + 0 tracebacks + LIVE RH. NO writes / NO broker.
import subprocess
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"; MACE = ROOT + "/trading_corp/mace"
OLD_PID = "675073"            # PID before the hotfix restart (2026-10-08 08:13 boot). Expect NEW.
HASH = "931a8214be50"
# strategy + manager CHANGE; domain/execution/config UNCHANGED (== deployed exit-redesign).
TARGET = {"strategy.py": "b30db63f", "manager.py": "dc04eb60",
          "domain.py": "8522dc3a", "execution.py": "9cc1c165", "config.py": "d441957f"}


def sh(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "").rstrip() + (("\n[stderr] " + p.stderr.strip()[:200]) if p.stderr.strip() else "")
    except Exception as e:
        return "[cmd-error] %r" % (e,)


boot = sh("systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null")
# journal window = since the current boot (robust whatever time the restart happened).
since = sh("systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null "
           "| sed -E 's/^[A-Za-z]+ //' | awk '{print $1\" \"$2}'") or "1 hour ago"

print("server UTC:", datetime.now(timezone.utc).isoformat(timespec="seconds"), "| boot:", boot)
mp = sh("systemctl show -p MainPID --value trading-corp 2>/dev/null").strip()
print("MainPID:", mp, "-> RESTARTED" if mp != OLD_PID else "-> UNCHANGED (not restarted yet?)")
print("ActiveState/SubState:", sh("systemctl show -p ActiveState,SubState --value trading-corp 2>/dev/null").replace("\n", " "))

print("\n== config_hash UNCHANGED + RH LIVE (running process, since boot) ==")
wired = sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -i 'MACE wired' | tail -1" % since)
print(wired or "(MACE wired line not found yet -- boot may still be in progress)")
print("on-disk mace.yaml sha256[:12]:", sh("sha256sum %s/config/mace.yaml | cut -c1-12" % ROOT), "(expect %s)" % HASH)

print("\n== grafted blobs on box == target (CR-stripped md5) ==")
for f, exp in TARGET.items():
    got = sh("tr -d '\\r' < '%s/%s' 2>/dev/null | md5sum | cut -c1-8" % (MACE, f)).strip()
    print("  mace/%-13s md5=%s expect=%s %s" % (f, got, exp, "OK" if got == exp else "*** MISMATCH ***"))

print("\n== 0 tracebacks / import errors since boot ==")
for pat in ["Traceback", "ImportError", "MACE wiring FAILED", "stop_triggered"]:
    print("  %-20s %s" % (pat, sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -c '%s'" % (since, pat))))
print("  /mace:", sh("curl -s -m 15 -o /dev/null -w 'HTTP %%{http_code}' http://127.0.0.1:8000/mace 2>&1"))
print("\n== END STRUCTURAL BOOT VERIFY -- behavioral acceptance = mace_stophotfix_accept_ro.py ==")
