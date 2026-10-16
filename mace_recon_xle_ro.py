#!/usr/bin/env python3
# READ-ONLY MACE XLE stuck-rung trace + "2-spreads" split evidence + redesign-health
# since 2026-10-07 (2026-10-15). NO writes / NO broker. sqlite mode=ro + query_only;
# journalctl only for the 'empty response' trend. Companion to mace_recon_health_ro.py.
import sqlite3, subprocess, json
from datetime import datetime, timezone, timedelta, date

ROOT = "/home/azureuser/trading_corp"
DB = ROOT + "/data/trading_corp.db"
SINCE_RUNG = "2026-10-01 00:00:00"    # full lifecycle of the stuck rung
SINCE_DEPLOY = "2026-10-07 00:00:00"  # redesign-health window (exit-redesign 10/07 + hotfix 10/08)
TARGET_EXPIRY = "2026-10-30"
TARGET_STRIKES = {59.0, 60.0, 70.0, 71.0}


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


def dump(c, sql, params=(), limit=300):
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


def strikes_of(legs_json):
    try:
        legs = json.loads(legs_json or "[]")
        return sorted(float(l.get("strike")) for l in legs)
    except Exception:
        return []


now = datetime.now(timezone.utc)
et = now - timedelta(hours=4)
print("MACE XLE TRACE RO :: utc=%s | ET=%s %s" % (
    now.strftime("%Y-%m-%d %H:%M:%S"), et.strftime("%Y-%m-%d %H:%M:%S"), et.strftime("%a")))

c = conn_ro()

hdr("1 ALL XLE RUNGS (identify the stuck 60/59/70/71 10-30 rung; no hardcoded id)")
xle = list(c.execute("SELECT rung_id,status,expiry,entry_ts,exit_ts,exit_reason,exit_debit,"
                     "realized_pnl,credit_actual,contracts,legs_json FROM mace_rung "
                     "WHERE symbol='XLE' ORDER BY expiry,entry_ts"))
print("  %-46s %-9s %-11s %-6s %-9s %-9s %-8s %s" % (
    "rung_id", "status", "expiry", "x", "reason", "exit_deb", "realized", "strikes"))
targets = []
for r in xle:
    d = dict(r)
    st = strikes_of(d["legs_json"])
    row = "  %-46s %-9s %-11s %-6s %-9s %-9s %-8s %s" % (
        d["rung_id"], d["status"], str(d["expiry"])[:10], d["contracts"],
        d["exit_reason"] or "-", d["exit_debit"] if d["exit_debit"] is not None else "-",
        d["realized_pnl"] if d["realized_pnl"] is not None else "-", st)
    print(row)
    if str(d["expiry"])[:10] == TARGET_EXPIRY and set(st) == TARGET_STRIKES:
        targets.append(d["rung_id"])
print("\n  >>> MATCHED stuck-rung candidate(s) (expiry %s, strikes %s): %s" % (
    TARGET_EXPIRY, sorted(TARGET_STRIKES), targets))

if not targets:
    print("  *** NO exact match -- widening to any XLE 10-30 rung ***")
    targets = [dict(r)["rung_id"] for r in xle if str(dict(r)["expiry"])[:10] == TARGET_EXPIRY]
    print("  XLE 10-30 rungs:", targets)

for RID in targets:
    hdr("2 STUCK RUNG FULL ROW + live mark -- %s" % RID)
    dump(c, "SELECT * FROM mace_rung WHERE rung_id=?", (RID,))
    print("-- mace_rung_live (mark frozen if CLOSING; ts = last tick):")
    dump(c, "SELECT rung_id,mark,spot,ts FROM mace_rung_live WHERE rung_id=?", (RID,))

    hdr("3 FULL EXIT/CLOSE LIFECYCLE for %s (every mace_* audit since %s, time-ordered)" % (RID, SINCE_RUNG))
    dump(c, "SELECT ts,kind,payload_json FROM audit_event WHERE ts >= ? AND kind LIKE 'mace_%' "
            "AND payload_json LIKE ? ORDER BY ts", (SINCE_RUNG, "%" + RID + "%"), limit=400)

    hdr("4 SPLIT / PARTIAL evidence for %s (Q2 -- expect ZERO engine partials/splits)" % RID)
    print("-- partial/drain/half-close audits for THIS rung (any = engine acted on a leg subset):")
    dump(c, "SELECT ts,kind,payload_json FROM audit_event WHERE payload_json LIKE ? AND kind IN "
            "('mace_exit_partial','mace_entry_partial','mace_drain_promote','mace_drain_abandon',"
            "'mace_exit_fill','mace_pt_fill') ORDER BY ts", ("%" + RID + "%",))
    print("-- distinct exit combo_ids referenced for this rung (all should be '%s-x*', one condor):" % RID)
    dump(c, "SELECT kind, COUNT(*) n FROM audit_event WHERE payload_json LIKE ? AND kind IN "
            "('mace_exit_start','mace_exit_rejected','mace_exit_error','mace_exit_unpriceable',"
            "'mace_exit_unconfirmed','mace_exit_exhausted','mace_exit_deferred') GROUP BY kind ORDER BY n DESC",
         ("%" + RID + "%",))

hdr("5 REDESIGN HEALTH since %s -- audit-kind vocabulary (DISCOVER, not assume)" % SINCE_DEPLOY)
dump(c, "SELECT kind, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM audit_event "
        "WHERE ts >= ? AND kind LIKE 'mace_%' GROUP BY kind ORDER BY n DESC", (SINCE_DEPLOY,), limit=120)

hdr("6 REDESIGN HEALTH -- new-path engagements (counts since deploy)")
for k in ("mace_ride_enter", "mace_ride_escape", "mace_ride_cleared", "mace_closing_reopen",
          "mace_closing_reopen_refused", "mace_expired_booked", "mace_expiry_legs_remain",
          "mace_expiry_sweep_error", "mace_exit_fill", "mace_pt_fill", "mace_exit_deferred",
          "mace_exit_rejected", "mace_exit_error", "mace_exit_exhausted", "mace_exit_unconfirmed",
          "mace_exit_unpriceable", "mace_exit_abort_pt_unconfirmed", "mace_exit_abort_prior_unconfirmed",
          "mace_pt_mark_reject", "mace_mark_unavailable", "mace_exit_risk_reject"):
    n = c.execute("SELECT COUNT(*) FROM audit_event WHERE kind=? AND ts >= ?", (k, SINCE_DEPLOY)).fetchone()[0]
    if n:
        print("  %-34s %d" % (k, n))
print("  (0-count kinds omitted)")

hdr("7 REAL CLOSES since deploy (exit_ts >= %s) -- clean PT/time/stop/expiry fills" % SINCE_DEPLOY)
dump(c, "SELECT rung_id,symbol,expiry,exit_ts,exit_reason,exit_debit,realized_pnl,credit_actual "
        "FROM mace_rung WHERE exit_ts >= ? ORDER BY exit_ts", (SINCE_DEPLOY,), limit=120)

hdr("8 THE OTHER HOTFIX-TARGET RUNG (XLE 57.5/56.5/67/68) -- did the self-heal fire?")
for r in c.execute("SELECT rung_id,status,expiry,exit_ts,exit_reason,realized_pnl,legs_json "
                   "FROM mace_rung WHERE symbol='XLE' ORDER BY entry_ts"):
    st = strikes_of(r["legs_json"])
    if set(st) & {57.5, 56.5, 67.0, 68.0}:
        print("  %s status=%s exp=%s reason=%s realized=%s strikes=%s" % (
            r["rung_id"], r["status"], str(r["expiry"])[:10], r["exit_reason"], r["realized_pnl"], st))
        dump(c, "SELECT ts,kind FROM audit_event WHERE payload_json LIKE ? AND kind IN "
                "('mace_closing_reopen','mace_ride_enter','mace_exit_rejected','mace_exit_fill') ORDER BY ts",
             ("%" + r["rung_id"] + "%",), limit=40)

hdr("9 'empty response' TREND since deploy (loop supposedly dead -> should be ~0 recently)")
print(sh("journalctl -u trading-corp --since '%s' -o cat 2>/dev/null | grep -ic 'empty response'" % SINCE_DEPLOY))
print("-- last 15 'empty response' lines (with dates -- confirm they stopped):")
print(sh("journalctl -u trading-corp --since '%s' -o short-iso 2>/dev/null | grep -i 'empty response' | tail -15" % SINCE_DEPLOY))

c.close()
print("\n===== END mace_recon_xle_ro =====")
