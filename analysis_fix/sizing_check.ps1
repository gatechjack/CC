# RO: sizing-basis staleness + will-the-15:40-snapshot-self-correct check. Read-only.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$py = @'
import sqlite3, datetime
con=sqlite3.connect("file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro",uri=True)
con.row_factory=sqlite3.Row
now=datetime.datetime.utcnow()
print("NOW_UTC", now.isoformat(), "| ET(approx UTC-4)", (now-datetime.timedelta(hours=4)).strftime("%H:%M:%S"))
print("=== mace_equity_snapshot (last 5) ===")
for r in con.execute("select snap_date,equity,available_buying_power,ts from mace_equity_snapshot order by snap_date desc limit 5").fetchall():
    print("  %s eq=%s abp=%s ts=%s" % (r["snap_date"],r["equity"],r["available_buying_power"],r["ts"]))
row=con.execute("select snap_date,available_buying_power,ts from mace_equity_snapshot order by snap_date desc limit 1").fetchone()
print("LATEST (the sizing basis the eval/UI reads): snap_date=%s abp=%s ts=%s" % (row["snap_date"],row["available_buying_power"],row["ts"]))
has_today=con.execute("select count(*) from mace_equity_snapshot where snap_date='2026-10-23'").fetchone()[0]
print("2026-10-23 snapshot row exists? %s" % bool(has_today))
print("=== TODAY (2026-10-23) robinhood_mace audit: liveness since restart ===")
print("  mace audit max_ts today:", con.execute("select max(ts) from audit_event where actor='robinhood_mace' and ts>='2026-10-23'").fetchone()[0])
for r in con.execute("select kind,count(*) n,max(ts) mx from audit_event where actor='robinhood_mace' and ts>='2026-10-23T14:00' group by kind order by mx desc limit 15").fetchall():
    print("  %-30s n=%-4d last=%s" % (r["kind"],r["n"],r["mx"]))
'@
$py | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | python3 -"
