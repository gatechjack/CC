#!/usr/bin/env python3
# READ-ONLY ACCEPTANCE for the MACE EXIT-REDESIGN deploy -- run at/after the MONDAY 09:35 ET open
# (first manage tick after the Friday deploy; manage window 09:35-15:55 ET Mon-Fri). Proves the
# redesign's LIVE behaviour: the wedged XLE rung SELF-HEALS (closing -> reopen -> ride) and the
# ~15-min exit_error loop is GONE. NO writes / NO broker.
import sqlite3, subprocess
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"
DB = ROOT + "/data/trading_corp.db"
SINCE = "2026-10-09 00:00:00"      # since the Friday deploy; widen if needed
STUCK = "mace-XLE-2026-10-30-60-59-70-71-20260917"


def sh(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "").rstrip()
    except Exception as e:
        return "[cmd-error] %r" % (e,)


def hdr(t):
    print("\n===== " + t + " =====")


print("server UTC:", datetime.now(timezone.utc).isoformat(timespec="seconds"),
      "| ET:", sh("TZ=America/New_York date '+%F %T %Z'"))

hdr("1 WEDGED XLE RUNG self-healed (closing -> open, then rides the dead wing)")
try:
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; c.execute("PRAGMA query_only=ON;")
    for r in c.execute("SELECT rung_id,status,exit_reason,exit_ts,realized_pnl,extra_json FROM mace_rung WHERE rung_id=?", (STUCK,)):
        print("   ", {k: r[k] for k in r.keys()})
    print("   EXPECT: status 'open' (reopened) with extra_json disposition 'riding' (dead wing) --")
    print("           OR 'closed'/'expired' if it reached 10-30 expiry; NOT a looping 'closing'.")
    c.close()
except Exception as e:
    print("[q-err]", e)

hdr("2 THE LOOP IS GONE: exit_error / close_exhausted counts since deploy (pre-fix was ~13/day)")
# audit events live in the activity/audit log table; grep the journal as the portable fallback.
for pat in ["mace_exit_error", "mace_exit_exhausted", "mace_missed_exit", "empty response"]:
    print("  %-22s %s" % (pat, sh("journalctl -u trading-corp --since '%s' -o cat -g '%s' 2>/dev/null | wc -l" % (SINCE, pat))))

hdr("3 REDESIGN AUDITS fired (self-heal + ride + gate) since deploy")
for pat in ["mace_closing_reopen", "mace_ride_enter", "mace_ride_cleared", "mace_close_blocked", "mace_expired_booked"]:
    n = sh("journalctl -u trading-corp --since '%s' -o cat -g '%s' 2>/dev/null | wc -l" % (SINCE, pat))
    print("  %-22s %s" % (pat, n))

hdr("4 manage loop live + 0 new tracebacks since deploy")
print("  Traceback:", sh("journalctl -u trading-corp --since '%s' -o cat -g 'Traceback' 2>/dev/null | wc -l" % SINCE))
print("  /mace:", sh("curl -s -m 15 -o /dev/null -w 'HTTP %%{http_code}' http://127.0.0.1:8000/mace 2>&1"))

print("\n===== ACCEPTANCE: PASS iff the XLE rung is NO LONGER a looping 'closing' (reopened/riding/")
print("      expired) AND mace_exit_error is ~0 since deploy (was ~13/day). Byte-parity of a liquid")
print("      close is covered by the committed test_mace_exit_redesign_replays.py golden. =====")
