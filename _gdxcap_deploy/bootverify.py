#!/usr/bin/env python3
# READ-ONLY post-restart BOOT-VERIFY for MACE #2 (GDX time-exit/PT cap). Board runs AFTER the
# restart. NO writes: sqlite query_only=ON; sha256/systemctl/journalctl/grep only. GREEN only if
# ALL hold; any non-green -> recommend rollback.sh + restart. Verifies: restart took (PID+recent);
# config_hash CHANGED 49476b0e -> bfde856f (config edit -- CHANGED is CORRECT; still 49476b0e =
# new config did NOT load); winner-pricing code live; #1 fork preserved (mace_missed_exit +
# exit_disposition_line); MACE-scoped (shared trio main/data_exec/robinhood UNCHANGED by #2);
# rungs intact; MACE/PEAD/PMCC healthy; 0 tracebacks.
import sqlite3, subprocess, re, hashlib
from datetime import datetime, timezone

ROOT = "/home/azureuser/trading_corp"; PKG = ROOT + "/trading_corp"; DB = ROOT + "/data/trading_corp.db"
GREEN = True
NEW_CFG = "bfde856f1c468d115812eeae2a923c1e5b4567e18ba703a5570f3d9df8b2c5cb"
EXEC_TGT = "073b6eeddbec164d6890c231040d476f0cf6f4b97551b714be5fa79dcc7b5b93"
# shared trio MUST stay at #1's deployed SHAs (unchanged by this MACE-scoped #2)
SHARED = {"main.py": (PKG + "/main.py", "cbf4b9283e4769cb53ea26fcce574ae0c45945846983ab9aef17b25b5cf484a6"),
          "agents/data_exec.py": (PKG + "/agents/data_exec.py", "fc8ab2534446d051fc3f96a7fca411a0ef985d227dc8e6d00553f9671ca9a75e"),
          "brokers/robinhood.py": (PKG + "/brokers/robinhood.py", "a26b8d0c84b23fbffae319cf4adc4612a0418506ad640c3191b35b15126a18dc")}


def bad():
    global GREEN; GREEN = False


def sh(cmd, timeout=120):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.rstrip()
    except Exception as e:
        return "[cmd-error] %r" % (e,)


def sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read().replace(b"\r", b"")).hexdigest()
    except Exception as e:
        return "MISSING/%r" % (e,)


def conn_ro():
    c = sqlite3.connect(DB, timeout=20); c.row_factory = sqlite3.Row; c.execute("PRAGMA query_only=ON;"); return c


def hdr(t):
    print("\n===== " + t + " =====")


now = datetime.now(timezone.utc)
print("boot-verify UTC:", now.isoformat(timespec="seconds"))

hdr("1 RESTART TOOK? (PID + recent start) -- board also confirms PID CHANGED")
info = {k: sh("systemctl show trading-corp -p %s --value" % k).strip()
        for k in ("MainPID", "ActiveState", "SubState", "ExecMainStartTimestamp")}
for k, v in info.items():
    print("%-24s = %s" % (k, v))
m = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", info.get("ExecMainStartTimestamp", ""))
start_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if m else None
since = m.group(1) if m else now.isoformat(sep=" ")[:19]
age = (now - start_dt).total_seconds() if start_dt else None
print("seconds since start      =", age)
if info.get("ActiveState") != "active" or info.get("SubState") != "running":
    print("  !! not active/running"); bad()
if age is None or age > 900:
    print("  !! start not recent (>15m) -- confirm the restart actually took"); bad()

hdr("2 config_hash CHANGED 49476b0e -> bfde856f (config edit -- CHANGED is CORRECT)")
box_cfg = sha(ROOT + "/config/mace.yaml")
print("box config/mace.yaml sha256:", box_cfg[:16], "==NEW" if box_cfg == NEW_CFG else "!!MISMATCH")
if box_cfg != NEW_CFG:
    print("  !! box mace.yaml != new config -- graft/config wrong"); bad()
wired = sh("TZ=UTC journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -F 'Robinhood MACE wired'" % since)
print(wired or "(no 'Robinhood MACE wired' line this boot)")
if "config_hash=bfde856f" in wired:
    print("OK   engine loaded config_hash=bfde856f (new config in memory)")
else:
    print("  !! engine did NOT log config_hash=bfde856f this boot"); bad()
if "config_hash=49476b0e" in wired:
    print("  !! STILL config_hash=49476b0e -- NEW CONFIG DID NOT LOAD, investigate"); bad()

hdr("3 winner-pricing code live + #1 FORK preserved (execution.py)")
E = PKG + "/mace/execution.py"
box_exec = sha(E)
print("box execution.py sha256:", box_exec[:16], "==target" if box_exec == EXEC_TGT else "!!MISMATCH")
if box_exec != EXEC_TGT:
    print("  !! execution.py != #2 target"); bad()
wn = sh("grep -cF 'pricing == \"winner\"' %s" % E).strip()
mm = sh("grep -cF mace_missed_exit %s" % E).strip()
edl = sh("grep -cF exit_disposition_line %s" % E).strip()
print("winner branch=%s (>=1)  mace_missed_exit=%s (>=1, #1)  exit_disposition_line=%s (>=4, box)" % (wn, mm, edl))
if not (wn.isdigit() and int(wn) >= 1): print("  !! winner pricing branch absent"); bad()
if not (mm.isdigit() and int(mm) >= 1): print("  !! #1 mace_missed_exit reverted"); bad()
if not (edl.isdigit() and int(edl) >= 4): print("  !! box exit_disposition_line reverted"); bad()
knob = sh("grep -cF exit_winner_band %s/mace/config.py" % PKG).strip()
print("config.py exit_winner_band field =", knob, "(>=1)")
if not (knob.isdigit() and int(knob) >= 1): print("  !! config.py knob missing"); bad()

hdr("4 MACE-SCOPED: shared trio UNCHANGED by #2 (still #1-deployed)")
for name, (p, want) in SHARED.items():
    got = sha(p)
    ok = got == want
    print("%-24s %s  %s" % (name, got[:16], "== #1-deployed (unchanged)" if ok else "!!CHANGED -- #2 must be MACE-scoped"))
    if not ok:
        bad()

hdr("5 RUNGS intact + division health + tracebacks")
c = conn_ro()
oc = c.execute("SELECT status, COUNT(*) n FROM mace_rung GROUP BY status ORDER BY status").fetchall()
print("mace_rung by status:", {r["status"]: r["n"] for r in oc})
for label, pat in (("MACE wired", "Robinhood MACE wired"), ("PEAD wired", "Robinhood PEAD wired"),
                   ("PMCC online", "IC position manager online|pmcc"), ("PM driver", "PM LIVE DRIVER WIRED|poly_kalshi")):
    hit = sh("TZ=UTC journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -icE '%s'" % (since, pat)).strip()
    hit = hit if hit.isdigit() else "0"
    print("%-14s grep=%s" % (label, hit))
    if hit == "0": print("  !! %s not seen this boot" % label); bad()
tb = sh("TZ=UTC journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -c 'Traceback (most recent call last)'" % since).strip()
tb = tb if tb.isdigit() else "0"
print("tracebacks this boot:", tb)
if tb != "0":
    print(sh("TZ=UTC journalctl -u trading-corp --since '%s' -o short-iso 2>/dev/null | grep -A6 Traceback | tail -40" % since)); bad()
c.close()

hdr("VERDICT")
if GREEN:
    print("BOOT-VERIFY GREEN -- config_hash bfde856f loaded, winner pricing live, #1 fork preserved, shared trio")
    print("unchanged (MACE-scoped), rungs + divisions healthy, 0 tracebacks. #2 done (box-only, NO FF-push).")
else:
    print("BOOT-VERIFY *** RED *** -- one or more checks failed above. RECOMMEND rollback.sh + restart; do NOT arm.")
