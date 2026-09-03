set -u
ROOT=/home/azureuser/trading_corp
PM=$ROOT/data/prediction_markets.db
V=$ROOT/venv/bin/python
date -u +"### OPPOSED-GUARD HISTORY SCAN (READ-ONLY) %Y-%m-%dT%H:%M:%SZ ###"
echo "Q (R2 urgency): how often was a contest DECIDED but generated NO close on a cid we HELD a side of -> rode to settlement UN-flattened?"
PM="$PM" "$V" - <<'PY'
import subprocess, re, sqlite3, os
from collections import defaultdict
# ---- 1) pull retained journal guard warnings ----
try:
    out = subprocess.run(["journalctl","-u","trading-corp","--since","2026-08-30","--no-pager"],
                         capture_output=True, text=True, timeout=180).stdout
except Exception as e:
    out=""; print("journalctl err:", e)
lines = out.splitlines()
print("journal lines pulled:", len(lines))
if lines:
    print("  earliest:", lines[0][:58]); print("  latest  :", lines[-1][:58])
warn_re = re.compile(r"OPPOSING-PAIR guard (\S+) -- (\d+) NEWLY-contested.*opposed_closes=(\d+) cids=\[(.*?)\]")
cid_re  = re.compile(r"0x[0-9a-fA-F]{8,}")
per_cid = defaultdict(lambda: {"warns":0,"max_oc":0,"acct":set(),"first":None,"last":None})
total_warns=0
for ln in lines:
    m = warn_re.search(ln)
    if not m: continue
    total_warns += 1
    acctcat, ncont, oc, cidstr = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    stamp = ln[:15]
    for c in cid_re.findall(cidstr):
        d=per_cid[c]; d["warns"]+=1; d["max_oc"]=max(d["max_oc"],oc); d["acct"].add(acctcat)
        if d["first"] is None: d["first"]=stamp
        d["last"]=stamp
print("total OPPOSING-PAIR warnings:", total_warns, " | distinct contested cids:", len(per_cid))

# ---- 2) DB classify each distinct contested cid ----
pm=sqlite3.connect("file:%s?mode=ro"%os.path.abspath(os.environ["PM"]),uri=True); pm.row_factory=sqlite3.Row
def counts(cid):
    entered = pm.execute("SELECT COALESCE(SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE 0 END),0) FROM pm_subdivision_order WHERE condition_id=? AND dry_run=0 AND outcome_status='filled'",(cid,)).fetchone()[0]
    opp = pm.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE condition_id=? AND close_source='opposed'",(cid,)).fetchone()[0]
    stl = pm.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE condition_id=? AND close_source LIKE 'settlement%'",(cid,)).fetchone()[0]
    othx = pm.execute("SELECT COUNT(*) FROM pm_subdivision_order WHERE condition_id=? AND is_exit=1 AND (close_source IS NULL OR close_source NOT IN ('opposed') AND close_source NOT LIKE 'settlement%')",(cid,)).fetchone()[0]
    net = pm.execute("SELECT COALESCE(SUM(CASE WHEN is_exit=0 THEN COALESCE(fill_count,0) ELSE -COALESCE(fill_count,0) END),0) FROM pm_subdivision_order WHERE condition_id=? AND dry_run=0 AND outcome_status='filled'",(cid,)).fetchone()[0]
    return float(entered or 0), opp, stl, othx, float(net or 0)

flattened=[]; never_held=[]; issue2=[]; other=[]
for cid,d in per_cid.items():
    entered,opp,stl,othx,net = counts(cid)
    row=dict(cid=cid, warns=d["warns"], max_oc=d["max_oc"], entered=entered, opp=opp, stl=stl, othx=othx, net=net,
             acct=sorted(d["acct"]), first=d["first"], last=d["last"])
    if opp>0:
        flattened.append(row)                       # guard DID flatten it at least once (working)
    elif entered<=0:
        never_held.append(row)                      # contested but we held nothing -> nothing to flatten (benign)
    elif stl>0 and othx==0:
        issue2.append(row)                          # HELD a side, NEVER opposed-closed, rode to SETTLEMENT -> Issue-2 harm
    else:
        other.append(row)                           # held + closed by a non-opposed exit (whale-exit) OR still open

print()
print("=== classification of the %d distinct contested cids ==="%len(per_cid))
print("  FLATTENED (opposed-closed >=1; guard worked):", len(flattened))
print("  NEVER-HELD (contested, held nothing; benign):", len(never_held))
print("  OTHER (held + closed by non-opposed exit, or still open):", len(other))
print("  >>> ISSUE-2 (held a side, NEVER opposed-closed, rode to SETTLEMENT un-flattened):", len(issue2))
for r in issue2:
    print("     cid=%s acct=%s warns=%d entered=%.1f settled_rows=%d net_now=%.1f first=%s last=%s"%(
        r["cid"], r["acct"], r["warns"], r["entered"], r["stl"], r["net"], r["first"], r["last"]))
print()
print("=== FLATTENED cids (working; note re-log noise via warns) ===")
for r in sorted(flattened, key=lambda x:-x["warns"])[:12]:
    print("     cid=%s acct=%s warns=%d opp_rows=%d entered=%.1f net_now=%.1f"%(r["cid"],r["acct"],r["warns"],r["opp"],r["entered"],r["net"]))
print()
print("=== OTHER (held + non-opposed exit or still open) ===")
for r in other[:12]:
    print("     cid=%s acct=%s warns=%d opp=%d stl=%d othx=%d entered=%.1f net_now=%.1f"%(r["cid"],r["acct"],r["warns"],r["opp"],r["stl"],r["othx"],r["entered"],r["net"]))
PY
echo "### DONE ###"
