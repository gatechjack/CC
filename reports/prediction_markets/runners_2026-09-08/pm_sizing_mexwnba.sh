set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
DB=$ROOT/data/prediction_markets.db
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
echo "### SIZING NORMALIZE (WRITE) -- mex+wnba x jack/karen fixed->contracts=5 ; cs2 ALREADY contracts (NOT touched) -- $STAMP ###"
echo "### backup ~/pm_sizing_backup_$STAMP.json ; strict 4-row guard ; PRE + POST-vs-PRE byte-unchanged proof ; DB read per cycle, NO restart ###"
echo
cd "$ROOT" && PM_DB="$DB" BK="$HOME/pm_sizing_backup_$STAMP.json" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os,sqlite3,time,json,hashlib
from trading_corp.prediction_markets import execution as EX
DB=os.environ["PM_DB"]; BK=os.environ["BK"]
TARGET_CATS=("mex","wnba"); ACCTS=("kalshi_jack","kalshi_karen"); ORIG=("mlb","atp","wta","ufc")
IGNORE_TARGET={"sizing_mode","contracts","updated_ts"}   # the only cols a TARGET row may differ on
c=sqlite3.connect(DB,timeout=10); c.execute("PRAGMA busy_timeout=8000"); c.row_factory=sqlite3.Row
def snap(): return {(r["account_id"],r["category"]):dict(r) for r in c.execute("SELECT * FROM pm_subdivision")}
def rawrow(d): return "%s|%s|%s|%s|%s"%(d["account_id"],d["category"],d["sizing_mode"],d["fixed_stake_usd"],d["contracts"])
def sha(lst): return hashlib.sha256("\n".join(sorted(lst)).encode()).hexdigest()[:16]

pre=snap()
with open(BK,"w") as f: json.dump([pre[k] for k in sorted(pre)], f)
print("BACKUP: %s (%d rows)"%(BK,len(pre)))
targets=[k for k in pre if k[1] in TARGET_CATS]
print("TARGET rows (mex+wnba x jack/karen = %d): sizing_mode->'contracts', contracts->5"%len(targets))
# cross-check to the snapshot's PRE non-target sha (rows except cs2/wnba/mex) -> proves nothing drifted since the snapshot
snap_nontarget=[rawrow(pre[k]) for k in pre if k[1] not in ("cs2","wnba","mex")]
print("PRE snapshot-nontarget sha (except cs2/wnba/mex, %d rows)=%s  (expect 1ef99e94df451fc2)"%(len(snap_nontarget),sha(snap_nontarget)))

print("\n=== PRE: resolved sizing for EVERY sub (sub_config_from_row) ===")
def cls(cat): return "TARGET" if cat in TARGET_CATS else ("cs2(already-contracts)" if cat=="cs2" else ("original" if cat in ORIG else "other"))
for (a,cat) in sorted(pre):
    sc=EX.sub_config_from_row(c.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?",(a,cat)).fetchone())
    if cat in TARGET_CATS or cat in ("cs2",)+ORIG:
        print("   %-13s/%-4s sizing_mode=%-9s contracts=%-2s fixed_stake=%-5s  [%s]"%(a,cat,sc.sizing_mode,sc.contracts,pre[(a,cat)]["fixed_stake_usd"],cls(cat)))

now=int(time.time())
cur=c.execute("UPDATE pm_subdivision SET sizing_mode='contracts', contracts=5, updated_ts=? "
              "WHERE category IN ('mex','wnba') AND account_id IN ('kalshi_jack','kalshi_karen') AND sizing_mode='fixed'",(now,))
n=cur.rowcount
print("\n=== WRITE: affected = %d (EXPECT 4) ==="%n)
if n!=4:
    c.rollback(); c.close()
    print("*** ABORTED: rowcount != 4 -> ROLLED BACK, NO change. Investigate. ***"); raise SystemExit(1)
c.commit()

post=snap()
bad=[]
for k in targets:
    sc=EX.sub_config_from_row(c.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?",k).fetchone())
    if not (sc.sizing_mode=="contracts" and int(sc.contracts)==5): bad.append("%s/%s->%s/%s"%(k[0],k[1],sc.sizing_mode,sc.contracts))
tdrift=[k+(col,) for k in targets for col in post[k] if col not in IGNORE_TARGET and post[k][col]!=pre[k][col]]
ndrift=[]; cs2_ok=True; orig_ok=True
for k in post:
    if k[1] in TARGET_CATS: continue
    for col in post[k]:
        if post[k][col]!=pre[k][col]:
            ndrift.append("%s/%s.%s %r->%r"%(k[0],k[1],col,pre[k][col],post[k][col]))
            if k[1]=="cs2": cs2_ok=False
            if k[1] in ORIG: orig_ok=False
print("=== POST-VERIFY (POST-vs-PRE) ===")
print("  [1] all %d targets resolve sizing_mode=contracts/contracts=5 : %s"%(len(targets),"OK" if not bad else "*** FAIL %s ***"%bad))
print("  [2] target rows -- every OTHER column byte-unchanged          : %s"%("ALL UNCHANGED" if not tdrift else "*** DRIFT %s ***"%tdrift))
print("  [3] EVERY non-target row FULLY byte-unchanged                 : %s"%("ALL UNCHANGED" if not ndrift else "*** CHANGED %s ***"%ndrift))
print("      cs2 (already contracts) untouched: %s ; originals mlb/atp/wta/ufc untouched: %s"%(cs2_ok,orig_ok))
print("  POST snapshot-nontarget sha=%s -> %s"%(sha([rawrow(post[k]) for k in post if k[1] not in ("cs2","wnba","mex")]),"unchanged" if sha([rawrow(post[k]) for k in post if k[1] not in ("cs2","wnba","mex")])==sha(snap_nontarget) else "*** CHANGED ***"))
print("  target rows now:")
for k in sorted(targets):
    sc=EX.sub_config_from_row(c.execute("SELECT * FROM pm_subdivision WHERE account_id=? AND category=?",k).fetchone())
    print("     %-13s/%-4s sizing_mode=%s contracts=%d per-order=$%.2f daily=$%.2f open=$%.2f"%(k[0],k[1],sc.sizing_mode,sc.contracts,sc.per_order_usd_cap,sc.daily_usd_cap,sc.max_open_usd))
ok=(not bad and not tdrift and not ndrift)
print("\nRESULT: %s"%("OK -- mex+wnba flipped to FLAT 5 CONTRACTS; cs2 + originals + all others byte-identical" if ok else "*** CHECK FAILED -- inspect flags ***"))
print("ROLLBACK: UPDATE pm_subdivision SET sizing_mode='fixed' WHERE category IN ('mex','wnba') AND account_id IN ('kalshi_jack','kalshi_karen');  (backup %s)"%BK)
c.close()
print("\n### SIZING NORMALIZE DONE ###")
PY
echo "### exit ###"
