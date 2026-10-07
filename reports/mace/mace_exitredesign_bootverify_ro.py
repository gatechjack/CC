#!/usr/bin/env python3
# READ-ONLY post-restart boot verify for the MACE EXIT-REDESIGN deploy (2026-10-09).
# CODE-ONLY deploy: config_hash UNCHANGED (931a8214be50). Proof-of-deploy = the 5 grafted .py md5s
# == NEW targets + a NEW PID/boot + boot-green + 0 tracebacks. NO writes / NO broker.
# NOTE: deployed Friday POST-CLOSE -> the manage loop (09:35-15:55 ET, Mon-Fri) is IDLE until Monday
# 09:35, so the wedged XLE rung stays 'closing' tonight (EXPECTED); the SELF-HEAL (reopen -> ride) is
# the Monday-open ACCEPTANCE check (mace_exitredesign_accept_ro.py), not this structural verify.
import sqlite3, subprocess
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"
MACE = ROOT + "/trading_corp/mace"
DB = ROOT + "/data/trading_corp.db"
OLD_PID = "642873"            # PID before the deploy restart (2026-10-06 02:56:24Z dedupe boot);
                              # deployed 2026-10-07 23:30Z -> new PID 664274. RE-PEG if restarted since.
HASH = "931a8214be50"         # UNCHANGED (code-only deploy; config/mace.yaml data untouched)
SINCE = "2026-10-07 23:29:00"  # since the Wed 2026-10-07 deploy restart (was Friday 10-09 target)
# NEW targets -- ALL 5 change this deploy (config.py the MODULE changes; config/mace.yaml data does not).
TARGET = {"domain.py": "8522dc3a", "execution.py": "9cc1c165", "strategy.py": "b147e59d",
          "manager.py": "ccd02002", "config.py": "d441957f"}
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
print("MainPID:", mp, "-> RESTARTED" if mp != OLD_PID else "-> UNCHANGED (not restarted yet? re-peg OLD_PID)")

hdr("1 config_hash UNCHANGED (code-only deploy MUST keep %s)" % HASH)
wired = sh("journalctl -u trading-corp --since '%s' -o cat -g 'Robinhood MACE wired' 2>/dev/null | tail -1" % SINCE)
print(wired)
print("VERDICT:", ("config_hash %s loaded (unchanged)" % HASH) if HASH in wired
      else "config_hash %s not found since restart -- check restart timing/boot line" % HASH)
print("on-disk config/mace.yaml sha256[:12]:",
      sh("sha256sum %s/config/mace.yaml | cut -c1-12" % ROOT), "(expect %s)" % HASH)

hdr("2 GRAFTED BLOBS on box == NEW target (ALL 5 changed; CR-stripped md5)")
for f, exp in TARGET.items():
    got = sh("tr -d '\\r' < '%s/%s' 2>/dev/null | md5sum | cut -c1-8" % (MACE, f)).strip()
    print("  mace/%-14s md5=%s expect=%s %s" % (f, got, exp, "OK" if got == exp else "*** MISMATCH ***"))

hdr("3 MACE loops online + 0 import errors/tracebacks since restart")
print(sh("journalctl -u trading-corp --since '%s' -o cat -g 'scheduler online' 2>/dev/null | grep -i mace | tail -6" % SINCE))
for pat in ["Traceback", "MACE wiring FAILED", "ImportError", "assess_closeability", "QuoteSnapshot"]:
    print("  %-22s %s" % (pat, sh("journalctl -u trading-corp --since '%s' -o cat -g '%s' 2>/dev/null | wc -l" % (SINCE, pat))))

hdr("4 /mace + rung census + wedged XLE rung (self-heal is the MONDAY-OPEN acceptance, not tonight)")
print("/mace:", sh("curl -s -m 15 -o /dev/null -w 'HTTP %{http_code}' http://127.0.0.1:8000/mace 2>&1"))
try:
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; c.execute("PRAGMA query_only=ON;")
    print("  status census:")
    for r in c.execute("SELECT status, COUNT(*) n, GROUP_CONCAT(DISTINCT symbol) syms FROM mace_rung GROUP BY status"):
        print("   ", {k: r[k] for k in r.keys()})
    print("  wedged XLE rung (EXPECTED still 'closing' off-hours; self-heals at Monday 09:35 first manage tick):")
    for r in c.execute("SELECT rung_id,status,exit_reason,exit_ts,realized_pnl,extra_json FROM mace_rung WHERE rung_id=?", (STUCK,)):
        print("   ", {k: r[k] for k in r.keys()})
    c.close()
except Exception as e:
    print("[q-err]", e)
print("\n===== END STRUCTURAL BOOT VERIFY -- acceptance = mace_exitredesign_accept_ro.py at Monday open =====")
