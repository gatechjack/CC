#!/usr/bin/env python3
# READ-ONLY: replicate mace_view._realized_pnl_windows EXACTLY and show whether the booked
# XLE rung (+16.00, exit_ts 2026-10-13) is inside each Division P&L bucket. NO writes.
import sqlite3
from datetime import datetime, timezone, timedelta

DB = "/home/azureuser/trading_corp/data/trading_corp.db"
RID = "mace-XLE-2026-10-30-60-59-70-71-20260917"
ET = timezone(timedelta(hours=-4))  # EDT


def et_midnight_utc_iso(days_back):
    d = (datetime.now(ET) - timedelta(days=days_back)).date()
    m = datetime(d.year, d.month, d.day, tzinfo=ET)
    return m.astimezone(timezone.utc).isoformat(timespec="seconds")


c = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=30)
c.execute("PRAGMA query_only=ON;")
today_iso = datetime.now(ET).date().isoformat()
print("now_et_date=%s | 1w_cutoff=%s | 1mo_cutoff=%s" % (today_iso, et_midnight_utc_iso(6), et_midnight_utc_iso(29)))

al = c.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM mace_rung WHERE status='closed' AND realized_pnl IS NOT NULL").fetchone()[0]
d1 = c.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM mace_rung WHERE status='closed' AND substr(exit_ts,1,10)=?", (today_iso,)).fetchone()[0]
w1 = c.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM mace_rung WHERE status='closed' AND realized_pnl IS NOT NULL AND exit_ts >= ?", (et_midnight_utc_iso(6),)).fetchone()[0]
m1 = c.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM mace_rung WHERE status='closed' AND realized_pnl IS NOT NULL AND exit_ts >= ?", (et_midnight_utc_iso(29),)).fetchone()[0]

print("\nDIVISION P&L (live-computed-from-rungs, exactly as the dashboard):")
print("  1D  (today ET session) = %+.2f   [screenshot +$0.00]" % d1)
print("  1W  (last 7 cal days)  = %+.2f   [screenshot +$79.00]" % w1)
print("  1MO (last 30 cal days) = %+.2f   [screenshot +$447.00]" % m1)
print("  ALL (all-time)         = %+.2f   [screenshot +$721.00]" % al)

r = c.execute("SELECT status,exit_ts,realized_pnl FROM mace_rung WHERE rung_id=?", (RID,)).fetchone()
print("\nbooked rung %s: status=%s exit_ts=%s realized=%s" % (RID, r[0], r[1], r[2]))
exit_date = (r[1] or "")[:10]
print("  -> in 1D ? %s (exit_date %s == today %s)" % (exit_date == today_iso, exit_date, today_iso))
print("  -> in 1W ? %s   in 1MO ? %s   in ALL ? %s"
      % ((r[1] or "") >= et_midnight_utc_iso(6), (r[1] or "") >= et_midnight_utc_iso(29), r[0] == "closed"))
print("\n  so pre-write buckets would have been: 1W=%+.2f 1MO=%+.2f ALL=%+.2f (each -16.00)"
      % (w1 - 16.0, m1 - 16.0, al - 16.0))
c.close()
print("\n===== END divpnl check =====")
