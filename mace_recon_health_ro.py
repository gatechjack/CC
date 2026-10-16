#!/usr/bin/env python3
# READ-ONLY MACE reconcile/health check (2026-10-15). NO writes / NO broker placement.
# sqlite mode=ro + query_only; only systemctl/journalctl/curl/sha256sum/md5sum shelled.
# Covers Q6 (engine health / census / code-intact / divisions), Q4 (reconcile-open-orders
# error), Q5 (today's eval sanity). The XLE stuck-rung trace + redesign-health is the
# companion runner mace_recon_xle_ro.py.
import sqlite3, subprocess, json
from datetime import datetime, timezone, timedelta, date, time as dtime

ROOT = "/home/azureuser/trading_corp"
DB = ROOT + "/data/trading_corp.db"
MACE = ROOT + "/trading_corp/mace"
EXPECT_MD5 = {"strategy.py": "b30db63f", "manager.py": "dc04eb60", "domain.py": "8522dc3a",
              "execution.py": "9cc1c165", "config.py": "d441957f"}
EXPECT_CFGHASH = "931a8214be50"
TODAY_ET = "2026-10-15"
SINCE_RECON = "2026-10-13 00:00:00"   # reconcile-error window (covers Jack's 10/13 manual close -> now)


def sh(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = p.stdout or ""
        if p.stderr and p.stderr.strip():
            out += "\n[stderr] " + p.stderr.strip()[:300]
        return out.rstrip()
    except Exception as e:
        return "[cmd-error] %r" % (e,)


def hdr(t):
    print("\n===== " + t + " =====")


def conn_ro():
    c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON;")
    return c


def dump(c, sql, params=(), limit=200):
    try:
        cur = c.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print("cols:", cols, "rows:", len(rows))
        for i, r in enumerate(rows[:limit]):
            print("  [%d] %s" % (i, {k: r[k] for k in cols}))
        return rows
    except Exception as e:
        print("[query-error] %r" % (e,))
        return []


now = datetime.now(timezone.utc)
et = now - timedelta(hours=4)
print("MACE RECON/HEALTH RO :: utc=%s | ET=%s %s" % (
    now.strftime("%Y-%m-%d %H:%M:%S"), et.strftime("%Y-%m-%d %H:%M:%S"), et.strftime("%a")))
in_win = (et.weekday() < 5) and (dtime(9, 35) <= et.time() <= dtime(15, 55))
print("manage window 09:35-15:55 ET weekday -> IN-WINDOW NOW? %s" % in_win)

hdr("Q6a ENGINE / PID / CONFIG / CODE-INTACT")
svc = sh("systemctl show trading-corp -p MainPID,ActiveState,SubState,NRestarts,Result,"
         "ExecMainStartTimestamp,ActiveEnterTimestamp 2>&1")
print(svc)
boot_ts = ""
for line in svc.splitlines():
    if line.startswith("ExecMainStartTimestamp="):
        parts = line.split("=", 1)[1].strip().split()
        if len(parts) >= 3:
            boot_ts = parts[1] + " " + parts[2]
print("since_boot=%r" % boot_ts)
SB = boot_ts or "2 days ago"
print("/mace HTTP   :", sh("curl -s -m 15 -o /dev/null -w 'HTTP %{http_code}' http://127.0.0.1:8000/mace 2>&1"))
print("/healthz HTTP:", sh("curl -s -m 15 -o /dev/null -w 'HTTP %{http_code}' http://127.0.0.1:8000/healthz 2>&1"))
print("-- code-intact (CR-stripped md5 vs DEPLOYED targets):")
all_ok = True
for rel, exp in EXPECT_MD5.items():
    got = sh("tr -d '\\r' < %s/%s | md5sum | cut -c1-8" % (MACE, rel))
    ok = got == exp
    all_ok = all_ok and ok
    print("   %-14s got=%s expect=%s %s" % (rel, got, exp, "OK" if ok else "*** MISMATCH ***"))
ch = sh("sha256sum %s/config/mace.yaml | cut -c1-12" % ROOT)
cfg_ok = ch == EXPECT_CFGHASH
print("   config_hash    got=%s expect=%s %s" % (ch, EXPECT_CFGHASH, "OK" if cfg_ok else "*** MISMATCH ***"))
print("   >>> CODE-INTACT:", "ALL OK (box == deployed redesign+hotfix bytes)" if (all_ok and cfg_ok) else "*** DRIFT ***")

hdr("Q6b LIVE RH (not paper) + wiring + /mace in-memory hash")
print("-- 'Robinhood MACE wired' (mode + hash per boot, last 2d):")
print(sh("journalctl -u trading-corp --since '2 days ago' -o cat 2>/dev/null | grep -F 'Robinhood MACE wired' | tail -6"))
print("-- PaperExecutionBroker since boot (expect 0 for LIVE):",
      sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -c 'PaperExecutionBroker'" % SB))
print("-- MACE wiring FAILED since boot (expect 0):",
      sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -cF 'MACE wiring FAILED'" % SB))
print("-- /mace in-memory config_hash:",
      sh("curl -s -m 10 http://127.0.0.1:8000/mace 2>/dev/null | grep -oiE 'config[_ -]?hash[^0-9a-f]{0,12}[0-9a-f]{8,16}' | head -1"))

c = conn_ro()

hdr("Q6c RUNG CENSUS + ALL STUCK/WEDGED + ARM/HALT")
dump(c, "SELECT status, COUNT(*) n, GROUP_CONCAT(DISTINCT symbol) syms FROM mace_rung GROUP BY status")
print("-- ALL closing/submitting rungs (any symbol) -- the 'stuck' set:")
dump(c, "SELECT rung_id,symbol,status,expiry,contracts,exit_reason,entry_ts FROM mace_rung "
        "WHERE status IN ('closing','submitting') ORDER BY symbol,expiry")
print("-- OPEN rungs with a ride disposition (riding to expiry) -- extra_json carries ride:")
for r in c.execute("SELECT rung_id,symbol,status,expiry,contracts,extra_json FROM mace_rung "
                   "WHERE status='open' AND extra_json LIKE '%ride%' ORDER BY symbol,expiry"):
    print("  ", r["rung_id"], r["symbol"], r["status"], "exp", r["expiry"], "x", r["contracts"])
    print("     extra=", r["extra_json"])
print("-- arm/halt state (agent_state robinhood_mace):")
dump(c, "SELECT agent,key,value_json,updated_ts FROM agent_state WHERE agent='robinhood_mace'")

hdr("Q6d MARK FEED freshness (open+closing rungs)")
for r in c.execute("""SELECT l.rung_id,l.mark,l.spot,l.ts,rr.symbol,rr.status
                      FROM mace_rung_live l JOIN mace_rung rr ON rr.rung_id=l.rung_id
                      WHERE rr.status IN ('open','closing') ORDER BY rr.symbol,rr.expiry"""):
    d = dict(r)
    try:
        ts = str(d["ts"]).replace("T", " ")[:19]
        age = (now - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds() / 60.0
    except Exception:
        age = -1.0
    print("  %-5s %-8s mark=%-8s spot=%-9s age_min=%.1f rung=%s" % (
        d["symbol"], d["status"], d["mark"], d["spot"], age, d["rung_id"]))

hdr("Q6e TRACEBACKS / ERRORS since boot (expect ~0)")
for pat in ["Traceback", "mace.*[Ee]rror", "mace.*Exception", "ImportError|ModuleNotFoundError"]:
    print("  %-26s %s" % (pat, sh("journalctl -u trading-corp --since '%s' -o cat -g '%s' 2>/dev/null | wc -l" % (SB, pat))))

hdr("Q4 RECONCILE-OPEN-ORDERS ERROR (x12 @ 10/15 14:23 ET) -- cause")
print("-- ALL reconcile/sweep/open-order audit kinds since %s (grouped):" % SINCE_RECON)
dump(c, "SELECT kind, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM audit_event "
        "WHERE ts >= ? AND (kind LIKE '%reconcile%' OR kind LIKE '%open_orders%' "
        "OR kind LIKE '%sweep%' OR kind LIKE '%expiry%' OR kind LIKE '%drain%') "
        "GROUP BY kind ORDER BY n DESC", (SINCE_RECON,))
print("-- distinct error strings for mace_reconcile_open_orders_error (payload) since %s:" % SINCE_RECON)
dump(c, "SELECT payload_json, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM audit_event "
        "WHERE kind='mace_reconcile_open_orders_error' AND ts >= ? GROUP BY payload_json", (SINCE_RECON,))
print("-- the 10/15 reconcile-open-orders rows (each, time-ordered) -- confirm the x12 burst + exact ET:")
dump(c, "SELECT ts,kind,payload_json FROM audit_event WHERE kind='mace_reconcile_open_orders_error' "
        "AND ts >= '2026-10-15 00:00:00' ORDER BY ts", limit=60)
print("-- journal around 14:23 ET (18:23 UTC) 10/15 for reconcile / open orders / RH endpoint errors:")
print(sh("journalctl -u trading-corp --since '2026-10-15 18:00:00' --until '2026-10-15 18:40:00' -o cat 2>/dev/null | "
         "grep -iE 'reconcile|open.?order|robinhood|429|401|timeout|empty response' | tail -40"))

hdr("Q5 TODAY'S EVAL (10/15) -- entry evals + skip reasons + trouble kinds")
print("-- mace_entry_eval today (symbol / entered / skip_reason / reason):")
for a in c.execute("SELECT ts,payload_json FROM audit_event WHERE kind='mace_entry_eval' "
                   "AND ts >= '2026-10-15 00:00:00' ORDER BY ts"):
    try:
        p = json.loads(a["payload_json"])
        print("  %s %-5s entered=%s skip=%s reason=%s" % (
            a["ts"][:19], p.get("symbol"), p.get("entered"), p.get("skip_reason"), p.get("reason")))
    except Exception:
        print("  %s %s" % (a["ts"][:19], a["payload_json"]))
print("-- entry trouble kinds today (unpriceable / standdown / credit_floor / error):")
dump(c, "SELECT ts,kind,payload_json FROM audit_event WHERE ts >= '2026-10-15 00:00:00' AND kind IN "
        "('mace_entry_unpriceable','mace_entry_standdown','mace_standdown','mace_entry_error',"
        "'mace_entry_skip','mace_entry_credit_floor') ORDER BY ts", limit=60)
print("-- any entries actually TAKEN today (entry_ts >= 10/15):")
dump(c, "SELECT rung_id,symbol,status,entry_ts,credit_actual,contracts FROM mace_rung "
        "WHERE entry_ts >= '2026-10-15 00:00:00' ORDER BY entry_ts")

hdr("Q6f OTHER DIVISIONS health (error-ish counts since boot)")
for pat in ("pmcc", "pead", "bitunix", "prediction|kalshi|polymarket|pm_"):
    print("  %-30s errors=%s" % (pat, sh(
        "journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -iE '%s' | grep -icE 'error|traceback|exception|fail'" % (SB, pat))))

print("\n-- DB quick_check(3):", end=" ")
try:
    print(c.execute("PRAGMA quick_check(3)").fetchall())
except Exception as e:
    print("[qc-err]", e)
c.close()
print("\n===== END mace_recon_health_ro =====")
