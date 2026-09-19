#!/usr/bin/env python3
# READ-ONLY post-restart boot verify for the PT mark-guard deploy (2026-09-18).
# NO writes / NO broker: sqlite query_only + systemctl/journalctl/curl/md5sum/sha256sum.
import sqlite3, subprocess
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"
MACE = ROOT + "/trading_corp/mace"
DB = ROOT + "/data/trading_corp.db"
OLD_PID = "474141"           # pre-deploy PID (18:17Z boot); expect a NEW pid
NEW_HASH = "931a8214be50"
OLD_HASH = "bfde856f1c46"
SINCE = "2026-09-18 00:00:00"
TARGET = {"config.py": "01117cca", "strategy.py": "69eef994",
          "execution.py": "66dba55a", "manager.py": "1d4334d7"}


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
now = datetime.now(timezone.utc)
print("server UTC:", now.isoformat(timespec="seconds"))
print(sh("systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp,Result 2>&1"))
mp = sh("systemctl show -p MainPID --value trading-corp 2>/dev/null").strip()
print("MainPID:", mp, "-> RESTARTED" if mp != OLD_PID else "-> UNCHANGED (not restarted yet?)")

hdr("1 config_hash POSITIVE PROOF (MUST be %s, config edit MOVED it off %s)" % (NEW_HASH, OLD_HASH))
wired = sh("journalctl -u trading-corp --since '%s' -o cat -g 'Robinhood MACE wired' 2>/dev/null | tail -1" % SINCE)
print(wired)
if NEW_HASH in wired:
    print("VERDICT: NEW config_hash %s LOADED -> mark-guard config took effect" % NEW_HASH)
elif OLD_HASH in wired:
    print("VERDICT: OLD config_hash %s still loaded -> FAILED deploy (config edit did not take)" % OLD_HASH)
else:
    print("VERDICT: config_hash not found since %s -- check restart timing" % SINCE)
print("on-disk config sha256[:12]:", sh("sha256sum %s/config/mace.yaml | cut -c1-12" % ROOT))

hdr("2 GRAFTED BLOBS on box == target")
for f, exp in TARGET.items():
    got = sh("md5sum '%s/%s' 2>/dev/null | cut -c1-8" % (MACE, f)).strip()
    print("  mace/%-14s md5=%s expect=%s %s" % (f, got, exp, "OK" if got == exp else "*** MISMATCH ***"))

hdr("3 4 MACE loops online + 0 import errors/tracebacks since restart")
print(sh("journalctl -u trading-corp --since '%s' -o cat -g 'scheduler online' 2>/dev/null | grep -i mace | tail -6" % SINCE))
for pat in ["Traceback", "MACE wiring FAILED", "mace.*[Ee]rror", "ImportError", "mark_guard"]:
    print("  %-20s %s" % (pat, sh("journalctl -u trading-corp --since '%s' -o cat -g '%s' 2>/dev/null | wc -l" % (SINCE, pat))))

hdr("4 /mace + arm state + rung census (guard is INERT until a PT fires)")
print("/mace:", sh("curl -s -m 15 -o /dev/null -w 'HTTP %{http_code}' http://127.0.0.1:8000/mace 2>&1"))
c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; c.execute("PRAGMA query_only=ON;")
try:
    for r in c.execute("SELECT agent,key,value_json FROM agent_state WHERE agent='robinhood_mace' AND key='entry_halt'"):
        print("  entry_halt:", {k: r[k] for k in r.keys()})
    print("  status census:")
    for r in c.execute("SELECT status, COUNT(*) n, GROUP_CONCAT(DISTINCT symbol) syms FROM mace_rung GROUP BY status"):
        print("   ", {k: r[k] for k in r.keys()})
    print("  XLE 2026-10-30 stuck rung (still closing, reset pending):")
    for r in c.execute("SELECT rung_id,status,exit_ts,realized_pnl FROM mace_rung WHERE rung_id='mace-XLE-2026-10-30-60-59-70-71-20260917'"):
        print("   ", {k: r[k] for k in r.keys()})
except Exception as e:
    print("[q-err]", e)
c.close()
print("\n===== END BOOT VERIFY (guard live + INERT; DB reset is the next Jack step) =====")
