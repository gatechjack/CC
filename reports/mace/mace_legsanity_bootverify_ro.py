#!/usr/bin/env python3
# READ-ONLY post-restart boot verify for the MACE leg-sanity guard deploy (2026-10-02).
# CODE-ONLY deploy: config_hash is UNCHANGED (931a8214be50). Proof-of-deploy = the 3 grafted .py
# md5s == new targets + a NEW PID + boot-green + 0 tracebacks. NO writes / NO broker.
import sqlite3, subprocess
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"
MACE = ROOT + "/trading_corp/mace"
DB = ROOT + "/data/trading_corp.db"
OLD_PID = "534581"            # pre-deploy PID (2026-09-22 21:11:47Z boot); expect a NEW pid
HASH = "931a8214be50"         # UNCHANGED (code-only deploy; config.py + mace.yaml untouched)
SINCE = "2026-10-02 00:00:00"
# NEW targets (changed): strategy/execution/manager. config.py UNCHANGED.
TARGET = {"config.py": "01117cca", "strategy.py": "50dd6984",
          "execution.py": "5b2307bc", "manager.py": "b0e9e879"}
STUCK = "mace-XLE-2026-10-30-60-59-70-71-20260917"


def sh(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "").rstrip()
        if p.stderr and p.stderr.strip():
            out += "\n[stderr] " + p.stderr.strip()[:300]
        return out
    except Exception as e:
        return "[cmd-error] %r" % (e,)


def hdr(t):
    print("\n===== " + t + " =====")


hdr("0 ENGINE RESTARTED?")
print("server UTC:", datetime.now(timezone.utc).isoformat(timespec="seconds"))
print(sh("systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp,Result 2>&1"))
mp = sh("systemctl show -p MainPID --value trading-corp 2>/dev/null").strip()
print("MainPID:", mp, "-> RESTARTED" if mp != OLD_PID else "-> UNCHANGED (not restarted yet?)")

hdr("1 config_hash UNCHANGED (code-only deploy MUST keep %s)" % HASH)
wired = sh("journalctl -u trading-corp --since '%s' -o cat -g 'Robinhood MACE wired' 2>/dev/null | tail -1" % SINCE)
print(wired)
if HASH in wired:
    print("VERDICT: config_hash %s loaded (unchanged) -> config NOT disturbed by the code graft" % HASH)
else:
    print("VERDICT: config_hash %s not found since restart -- check restart timing / boot line" % HASH)
print("on-disk config sha256[:12]:", sh("sha256sum %s/config/mace.yaml | cut -c1-12" % ROOT), "(expect %s)" % HASH)

hdr("2 GRAFTED BLOBS on box == NEW target (strategy/execution/manager changed; config unchanged)")
for f, exp in TARGET.items():
    got = sh("md5sum '%s/%s' 2>/dev/null | cut -c1-8" % (MACE, f)).strip()
    print("  mace/%-14s md5=%s expect=%s %s" % (f, got, exp, "OK" if got == exp else "*** MISMATCH ***"))

hdr("3 MACE loops online + 0 import errors/tracebacks since restart")
print(sh("journalctl -u trading-corp --since '%s' -o cat -g 'scheduler online' 2>/dev/null | grep -i mace | tail -6" % SINCE))
for pat in ["Traceback", "MACE wiring FAILED", "ImportError", "leg_mids", "leg_inversion"]:
    print("  %-20s %s" % (pat, sh("journalctl -u trading-corp --since '%s' -o cat -g '%s' 2>/dev/null | wc -l" % (SINCE, pat))))

hdr("4 /mace + rung census + stuck XLE rung (guard is INERT until a PT fires)")
print("/mace:", sh("curl -s -m 15 -o /dev/null -w 'HTTP %{http_code}' http://127.0.0.1:8000/mace 2>&1"))
c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; c.execute("PRAGMA query_only=ON;")
try:
    print("  status census:")
    for r in c.execute("SELECT status, COUNT(*) n, GROUP_CONCAT(DISTINCT symbol) syms FROM mace_rung GROUP BY status"):
        print("   ", {k: r[k] for k in r.keys()})
    print("  stuck XLE rung (EXPECTED still 'closing' -- guard fix does NOT un-latch it; see report Task 3):")
    for r in c.execute("SELECT rung_id,status,exit_ts,realized_pnl FROM mace_rung WHERE rung_id=?", (STUCK,)):
        print("   ", {k: r[k] for k in r.keys()})
except Exception as e:
    print("[q-err]", e)
c.close()
print("\n===== END BOOT VERIFY (guard live + INERT; orphan adopt [Task 2] is the next Jack step) =====")
