PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
BK=/home/azureuser/pm_recovery_backup_$(date -u +%Y%m%dT%H%M%SZ).db
echo "=== df / (pre-backup guard) ==="; df -h / | tail -1
echo "=== engine PID/NRestarts BEFORE ==="; systemctl show trading-corp -p MainPID -p NRestarts -p ActiveEnterTimestamp 2>&1
cd "$ROOT" || exit 2
PYTHONPATH=. "$PY" - "$PMDB" "$BK" <<'PY'
import sys, os, sqlite3, time
from trading_corp.prediction_markets.settlement import _HELD_SQL, _iso_to_unix, _EPS
PMDB, BK = sys.argv[1], sys.argv[2]
TICKER="KXUFCFIGHT-26SEP29BULVIS-VIS"
CAT="ufc"; CLOSE_SOURCE="settlement_scalar"; SETTLED_VALUE=0.45
SETTLED_ISO="2026-09-29T23:49:46.459185Z"
ACCTS=["kalshi_jack","kalshi_karen","kalshi_marc","kalshi_trey"]
EXPECT={"kalshi_jack":5.0,"kalshi_karen":5.0,"kalshi_marc":1.0,"kalshi_trey":1.0}
now_ts=int(time.time()); settled_ts=_iso_to_unix(SETTLED_ISO) or now_ts
print("now_ts",now_ts,"settled_ts",settled_ts,"settled_value",SETTLED_VALUE,"close_source",CLOSE_SOURCE)

print("=== STEP A: online backup (RO source handle) ===")
src=sqlite3.connect("file:%s?mode=ro"%PMDB, uri=True)
dst=sqlite3.connect(BK)
try:
    src.backup(dst, pages=2000, sleep=0.05)
finally:
    dst.close(); src.close()
print("BACKUP:", BK, "size_bytes", os.path.getsize(BK))
b=sqlite3.connect("file:%s?mode=ro"%BK, uri=True)
print("backup quick_check:", b.execute("pragma quick_check").fetchone()[0]); b.close()

print("=== STEP B: before-snapshot (RO) ===")
ro=sqlite3.connect("file:%s?mode=ro"%PMDB, uri=True); ro.row_factory=sqlite3.Row
print("main quick_check(before):", ro.execute("pragma quick_check").fetchone()[0])
before_count=ro.execute("select count(*) from pm_subdivision_order").fetchone()[0]
before_max=ro.execute("select coalesce(max(id),0) from pm_subdivision_order").fetchone()[0]
ex=ro.execute("select count(*) from pm_subdivision_order where ticker=? and is_exit=1",(TICKER,)).fetchone()[0]
print("before count",before_count,"max_id",before_max,"existing is_exit=1 for ticker",ex)
ro.close()
if ex!=0:
    print("ABORT: ticker already has is_exit=1 close row(s) -> refuse to double-book. NO WRITE."); sys.exit(3)

print("=== STEP C: compute planned rows (mirror book_settlements), scoped to ticker ===")
rw=sqlite3.connect(PMDB, timeout=30); rw.row_factory=sqlite3.Row
rw.execute("pragma busy_timeout=30000")
planned=[]
for aid in ACCTS:
    for row in rw.execute(_HELD_SQL, (aid, CAT)):
        if row["ticker"]!=TICKER: continue
        entered=float(row["entered"] or 0.0); exited=float(row["exited"] or 0.0)
        entry_cost=float(row["entry_cost"] or 0.0); net_open=round(entered-exited,6)
        if net_open<=_EPS:
            print("skip(flat):",aid,row["wallet"]); continue
        avg_cost=(entry_cost/entered) if entered>_EPS else 0.0
        realized=round(net_open*SETTLED_VALUE - net_open*avg_cost, 6)
        planned.append(dict(account_id=aid, wallet=row["wallet"], condition_id=row["condition_id"],
            outcome_index=row["outcome_index"], ticker=row["ticker"], outcome_leg=row["outcome_leg"],
            net_open=net_open, avg_cost=round(avg_cost,6), fill_price=round(SETTLED_VALUE,4), realized=realized))
print("PLANNED rows:",len(planned))
for p in planned:
    print("  %-14s leg=%s net_open=%s avg_cost=%s fill_price=%s realized=%s wallet=%s cid=%s oidx=%s"%(
        p["account_id"],p["outcome_leg"],p["net_open"],p["avg_cost"],p["fill_price"],p["realized"],
        p["wallet"][:12],(p["condition_id"] or '')[:14],p["outcome_index"]))
ok=(len(planned)==4) and all(abs(p["net_open"]-EXPECT[p["account_id"]])<1e-6 for p in planned) \
   and all(p["outcome_leg"]=="yes" for p in planned) \
   and sorted(p["account_id"] for p in planned)==sorted(ACCTS)
print("PRECONDITION ok:",ok)
if not ok:
    print("ABORT: preconditions not met (want 4 rows, net_open 5/5/1/1, leg=yes, all 4 accts). NO WRITE."); rw.close(); sys.exit(4)

print("=== STEP D: WRITE 4 scalar-close rows (exact book_settlements INSERT shape) ===")
for p in planned:
    rw.execute(
      "INSERT INTO pm_subdivision_order (account_id, category, wallet, condition_id, outcome_index, ticker, "
      " outcome_leg, is_exit, fill_count, fill_price, fee, outcome_status, close_source, realized_pnl, won, "
      " settled_ts, dry_run, submitted_ts, response_ts) VALUES (?,?,?,?,?,?,?,1,?,?,0,'filled',?,?,?,?,0,?,?)",
      (p["account_id"],CAT,p["wallet"],p["condition_id"],p["outcome_index"],p["ticker"],p["outcome_leg"],
       p["net_open"],p["fill_price"],CLOSE_SOURCE,p["realized"],None,settled_ts,now_ts,now_ts))
rw.commit()
print("COMMITTED",len(planned),"rows")

print("=== STEP E: verify ===")
after_count=rw.execute("select count(*) from pm_subdivision_order").fetchone()[0]
print("after count",after_count,"total_delta",after_count-before_count)
newrows=list(rw.execute("select id,account_id,category,wallet,ticker,outcome_leg,is_exit,fill_count,fill_price,fee,outcome_status,close_source,realized_pnl,won,settled_ts,submitted_ts,response_ts,dry_run from pm_subdivision_order where id>? order by id",(before_max,)))
mine=[r for r in newrows if r["close_source"]==CLOSE_SOURCE and r["ticker"]==TICKER]
others=[r for r in newrows if not (r["close_source"]==CLOSE_SOURCE and r["ticker"]==TICKER)]
print("new rows total",len(newrows),"| mine(settlement_scalar,ticker)",len(mine),"| other-concurrent",len(others))
print("-- MY 4 rows field-by-field --")
for r in mine: print("  ",dict(r))
if others:
    print("-- OTHER new rows (concurrent engine writes; itemized) --")
    for r in others: print("  id=%s acct=%s ticker=%s is_exit=%s close_source=%s"%(r["id"],r["account_id"],r["ticker"],r["is_exit"],r["close_source"]))
print("-- signed-net for ticker per account (expect 0) --")
for aid in ACCTS:
    v=rw.execute("select coalesce(sum((case outcome_leg when 'yes' then 1 when 'no' then -1 else 0 end)*(case when is_exit=0 then 1 else -1 end)*coalesce(fill_count,0)),0) from pm_subdivision_order where account_id=? and ticker=? and dry_run=0 and outcome_status='filled'",(aid,TICKER)).fetchone()[0]
    print("   %-14s signed_net=%s"%(aid,v))
print("main quick_check(after):", rw.execute("pragma quick_check").fetchone()[0])
rw.close()
print("realized P&L booked: jack+karen+marc+trey total =", round(sum(p["realized"] for p in planned),6))
PY
RC=$?
echo "python rc=$RC"
echo "=== engine PID/NRestarts AFTER (must be unchanged; no restart performed) ==="; systemctl show trading-corp -p MainPID -p NRestarts -p ActiveEnterTimestamp 2>&1
echo "=== DONE p2 ==="
