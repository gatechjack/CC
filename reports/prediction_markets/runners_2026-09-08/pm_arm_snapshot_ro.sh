set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
DB=$ROOT/data/prediction_markets.db
LEGACY=$ROOT/data/trading_corp.db
echo "### PM ARM-SNAPSHOT (READ-ONLY) -- cs2 / wnba / mex on kalshi_jack + kalshi_karen -- $(date -u +%Y-%m-%dT%H:%M:%SZ) ###"
echo "### verifies attachment + whale book/category + roster spawn + liveness(n_signals) + arm state + sizing PRE. NO writes. ###"
echo
echo "## engine service (ActiveEnterTimestamp = last boot; a post-boot attach is DORMANT until restart) ##"
systemctl show trading-corp -p MainPID -p NRestarts -p ActiveEnterTimestamp -p SubState 2>/dev/null | sed 's/^/   /'
echo
cd "$ROOT" && PM_DB="$DB" LEGACY_DB="$LEGACY" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os,sqlite3,hashlib
from trading_corp.prediction_markets import execution as EX
from trading_corp.prediction_markets import driver_roster as DR
from trading_corp.prediction_markets import heartbeat as HB
from trading_corp.prediction_markets import arm as ARM
PM=os.environ["PM_DB"]; LEG=os.environ["LEGACY_DB"]
CATS=("cs2","wnba","mex"); ACCTS=("kalshi_jack","kalshi_karen")
def sha(lst): return hashlib.sha256("\n".join(lst).encode()).hexdigest()[:16]
c=sqlite3.connect("file:%s?mode=ro"%PM,uri=True); c.row_factory=sqlite3.Row

print("== SECTION 1/2: ATTACHMENTS + WHALE BOOK (the FIRST question: is it attached, which wallet, does the book hold the category) ==")
for cat in CATS:
  for acct in ACCTS:
    at=c.execute("SELECT wallet,active,source,added_ts FROM pm_subdivision_attachment WHERE account_id=? AND category=? ORDER BY active DESC,added_ts DESC",(acct,cat)).fetchall()
    act=[r for r in at if r["active"]==1]
    print("  -- %s / %s : %d ACTIVE attachment(s) (%d rows total) --"%(acct,cat,len(act),len(at)))
    if not at: print("       (no attachment row at all)")
    for r in at:
        w=r["wallet"]
        comp=c.execute("SELECT category cat,COUNT(*) n FROM pm_open_position WHERE wallet=? GROUP BY category ORDER BY n DESC",(w,)).fetchall()
        comps=", ".join("%s=%d"%((x["cat"] or "?"),x["n"]) for x in comp) or "(0 open positions in book)"
        op=c.execute("SELECT COUNT(*) FROM pm_open_position WHERE wallet=? AND category=?",(w,cat)).fetchone()[0]
        cl=c.execute("SELECT COUNT(*) FROM pm_closed_position WHERE wallet=? AND category=?",(w,cat)).fetchone()[0]
        if op>0: v="HOLDS %d LIVE %s position(s) -> category CONFIRMED + conclusive dry-run FEASIBLE"%(op,cat)
        elif cl>0: v="0 live now, %d CLOSED %s -> trades the category (history) but nothing live to dry-run today"%(cl,cat)
        else: v="** 0 open AND 0 closed in %s -- this whale's book is SOMETHING ELSE; DO NOT ARM on this attachment **"%cat
        print("     wallet=%s active=%s source=%s"%(w,r["active"],r["source"]))
        print("       book-by-cat: %s"%comps)
        print("       verdict   : %s"%v)
        for s in c.execute("SELECT title,outcome,refreshed_ts FROM pm_open_position WHERE wallet=? AND category=? ORDER BY refreshed_ts DESC LIMIT 4",(w,cat)).fetchall():
            print("       LIVE-POS  : %r  outcome=%s  refreshed=%s"%((s["title"] or "")[:64],s["outcome"],s["refreshed_ts"]))

print("")
print("== SECTION 3: DRIVER ROSTER (a sub is SPAWNED iff it was active+attached at the last engine boot) ==")
ros=DR.active_driver_subdivisions(c); rs={(r["account_id"],r["category"]) for r in ros}
print("  roster size=%d :"%len(ros))
for r in ros: print("     %s / %s"%(r["account_id"],r["category"]))
print("  -- target spawn state --")
for cat in CATS:
  for acct in ACCTS:
    print("     %s / %s : %s"%(acct,cat,"SPAWNED (has a task)" if (acct,cat) in rs else "DORMANT (attached post-boot -> ENGINE RESTART REQUIRED to spawn a task)"))

print("")
print("== SECTION 4: LIVENESS PANEL (n_signals = the pre-arm signal count; re-run this AFTER the restart to read it) ==")
lv=HB.read_liveness(c)
alarm=[x for x in lv if x.state in ("STALE","NEVER","CATEGORY_STARVED")]
print("  panel size=%d ; alarms (STALE/NEVER/CATEGORY_STARVED)=%d"%(len(lv),len(alarm)))
for x in lv:
    tgt="  <== TARGET" if x.category in CATS else ""
    print("     %-13s/%-5s %-16s n_signals=%s placed=%s age=%ss%s"%(x.account_id,x.category,x.state,x.n_signals,x.placed,x.age_sec,tgt))

print("")
print("== SECTION 5: ARM STATE (expect disarmed / absent, no latch; arm() refuses a latched scope) ==")
g=ARM.current_row(global_=True,legacy_db_path=LEG)
print("  GLOBAL armed=%s latched=%s"%(bool(g and g.get("armed")),bool(g and g.get("latched"))))
for cat in CATS:
  for acct in ACCTS:
    row=ARM.current_row(acct,cat,legacy_db_path=LEG)
    vv=ARM.read_arm_verdict(acct,cat,legacy_db_path=LEG)
    print("     %s / %s effective_armed=%s latched=%s row=%s"%(acct,cat,vv.armed,bool(row and row.get("latched")),"present" if row else "ABSENT (never armed)"))

print("")
print("== SECTION 6: SIZING PRE (resolved via sub_config_from_row, ALL subs; targets marked) ==")
rows=c.execute("SELECT * FROM pm_subdivision ORDER BY account_id,category").fetchall()
blob=[]; rawall=[]; rawoth=[]; tgt=[]
for row in rows:
    sc=EX.sub_config_from_row(row)
    ln="%-13s|%-4s|mode=%-9s|contracts=%s|fixed=%s|types=%s"%(sc.account_id,sc.category,sc.sizing_mode,sc.contracts,sc.fixed_stake_usd,",".join(sc.market_types))
    blob.append(ln)
    rr="%s|%s|%s|%s|%s"%(row["account_id"],row["category"],row["sizing_mode"],row["fixed_stake_usd"],row["contracts"])
    rawall.append(rr)
    if row["category"] in CATS: tgt.append(ln); print("     %s  <== TARGET"%ln)
    else: rawoth.append(rr)
print("  subs total=%d ; target rows=%d ; other rows=%d"%(len(rows),len(tgt),len(rawoth)))
print("  RESOLVED-config sha(all subs)          = %s"%sha(blob))
print("  RAW sizing sha(all: acct|cat|mode|fixed|contracts) = %s"%sha(rawall))
print("  ** RAW sizing sha(NON-TARGET rows only) = %s  <== MUST be byte-identical after the sizing write (POST-vs-PRE) **"%sha(rawoth))
c.close()
print("")
print("### SNAPSHOT DONE ###")
PY
echo "### PM ARM-SNAPSHOT exit ###"
