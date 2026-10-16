#!/usr/bin/env python3
# READ-ONLY MACE mark-feed / manage-loop liveness + traceback characterization (2026-10-15).
# Investigates the anomaly: mace_rung_live marks frozen ~10/13 16:40Z while entry/snapshot
# loops ran today. NO writes. sqlite mode=ro + query_only; journalctl TIGHT windows + box
# timeouts to avoid the known since-boot timeout trap.
import sqlite3, subprocess
from datetime import datetime, timezone, timedelta

ROOT = "/home/azureuser/trading_corp"
DB = ROOT + "/data/trading_corp.db"


def sh(cmd, timeout=80):
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


def dump(c, sql, params=(), limit=60):
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
print("MACE MARK/LOOP LIVENESS RO :: utc=%s" % now.strftime("%Y-%m-%d %H:%M:%S"))
c = conn_ro()

hdr("1 mace_rung_live freshness -- exact freeze boundary")
dump(c, "SELECT MAX(ts) max_ts, MIN(ts) min_ts, COUNT(*) n, COUNT(DISTINCT ts) distinct_ts FROM mace_rung_live")
print("-- top 12 freshest rows (any frozen at the same ts = loop-wide freeze):")
dump(c, "SELECT rung_id,mark,spot,ts FROM mace_rung_live ORDER BY ts DESC LIMIT 12")

hdr("2 Manage-loop AUDIT liveness -- last ts per manage/entry/reconcile/snapshot kind")
for k in ("mace_manage_exits", "mace_manage_exit", "mace_snapshot", "mace_equity_snapshot",
          "mace_entry_eval", "mace_entry_round", "mace_daily_summary", "mace_pt_synthetic",
          "mace_mark_unavailable", "mace_reconcile_open_orders_error", "mace_pt_mark_reject"):
    row = c.execute("SELECT COUNT(*) n, MAX(ts) last FROM audit_event WHERE kind=?", (k,)).fetchone()
    print("  %-34s n=%-6s last=%s" % (k, row["n"], row["last"]))

hdr("3 ANY mace audit at all in the last 3 days (is the manage/reconcile loop alive today?)")
dump(c, "SELECT DATE(ts) d, kind, COUNT(*) n FROM audit_event WHERE kind LIKE 'mace_%' "
        "AND ts >= '2026-10-13 00:00:00' GROUP BY DATE(ts), kind ORDER BY d, n DESC", limit=80)

hdr("4 reconcile_open_orders_error distinct strings since deploy (the 820 -- all 'not logged in'?)")
dump(c, "SELECT payload_json, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM audit_event "
        "WHERE kind='mace_reconcile_open_orders_error' AND ts >= '2026-10-07 00:00:00' GROUP BY payload_json", limit=20)

hdr("5 TRACEBACKS since 10/13 (bounded window + box timeout; the 22-since-boot)")
print(sh("timeout 55 journalctl -u trading-corp --since '2026-10-13 00:00:00' --until '2026-10-16 02:00:00' "
         "-o short-iso 2>/dev/null | grep -F 'Traceback' | tail -30"))
print("-- exception TYPES near those tracebacks (mace/robinhood context, bounded):")
print(sh("timeout 55 journalctl -u trading-corp --since '2026-10-13 00:00:00' --until '2026-10-16 02:00:00' "
         "-o cat 2>/dev/null | grep -iE 'Error:|Exception:' | grep -iE 'mace|robinhood|option|quote|login|session' | sort | uniq -c | sort -rn | head -25"))

hdr("6 MANAGE loop around the 10/13 15:52-16:40Z stop boundary (what happened?)")
print(sh("timeout 55 journalctl -u trading-corp --since '2026-10-13 15:45:00' --until '2026-10-13 17:10:00' "
         "-o short-iso 2>/dev/null | grep -iE 'mace' | grep -iE 'manage|mark|quote|error|traceback|exception|login|session|robinhood' | tail -40"))

hdr("7 RH SESSION / login health TODAY (10/15) -- is the session dropping? bounded")
print(sh("timeout 55 journalctl -u trading-corp --since '2026-10-15 00:00:00' -o short-iso 2>/dev/null | "
         "grep -iE 'robinhood|logged in|login|re-?auth|401|session' | grep -ivE 'polymarket|kalshi|pm_live|prediction' | tail -40"))

hdr("8 MACE scheduler loops 'online' (which loops started at the 10/08 boot?)")
print(sh("timeout 50 journalctl -u trading-corp --since '2026-10-08 16:30:00' --until '2026-10-08 16:50:00' "
         "-o cat 2>/dev/null | grep -iE 'mace' | grep -iE 'scheduler|online|loop|wired|interval' | tail -30"))

c.close()
print("\n===== END mace_recon_markloop_ro =====")
