PY=/home/azureuser/trading_corp/venv/bin/python
J=/tmp/hc5_$$.txt
journalctl -u trading-corp --since "2026-10-07 04:18:00" --no-pager -o short-iso 2>&1 | grep -E "pm_live_driver cycle " > "$J" 2>&1
echo "cycle-summary lines:"; wc -l "$J"
"$PY" - "$J" <<'PY'
import sys, re, ast
agg={}
nz_shard=[]; nz_reject=[]; nz_err=[]; nz_disarm=[]
ncyc=0
for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    i=line.find("{'account_id'")
    if i<0: continue
    try: d=ast.literal_eval(line[i:].strip())
    except Exception: continue
    ncyc+=1
    a=d.get('account_id');
    s=agg.setdefault(a, dict(sig=0,would=0,placed=0,posts=0,skip=0,reject=0,shard=0,disarm=0,err=0,cyc=0))
    s['sig']+=d.get('n_signals',0) or 0; s['would']+=d.get('n_would_place',0) or 0
    s['placed']+=d.get('placed',0) or 0; s['posts']+=d.get('posts_sent',0) or 0
    s['skip']+=d.get('n_skip',0) or 0; s['reject']+=d.get('n_reject',0) or 0
    s['shard']+=d.get('n_shard_underfunded',0) or 0; s['disarm']+=d.get('n_disarm_blocked',0) or 0
    s['err']+=d.get('errors',0) or 0; s['cyc']+=1
    cat=d.get('category')
    if (d.get('n_shard_underfunded') or 0)>0: nz_shard.append((a,cat,d.get('n_shard_underfunded')))
    if (d.get('n_reject') or 0)>0: nz_reject.append((a,cat,d.get('n_reject')))
    if (d.get('errors') or 0)>0: nz_err.append((a,cat,d.get('errors')))
    if (d.get('n_disarm_blocked') or 0)>0: nz_disarm.append((a,cat,d.get('n_disarm_blocked')))
print("total cycles parsed:", ncyc)
print("%-14s %7s %7s %7s %7s %7s %7s %7s %7s %6s"%("account","cycles","sig","would","placed","posts","skip","reject","shard_uf","disarm"))
for a in sorted(agg):
    s=agg[a]
    print("%-14s %7s %7s %7s %7s %7s %7s %7s %7s %6s"%(a,s['cyc'],s['sig'],s['would'],s['placed'],s['posts'],s['skip'],s['reject'],s['shard'],s['disarm']))
print("NON-ZERO n_shard_underfunded cycles:", len(nz_shard), nz_shard[:10])
print("NON-ZERO n_reject cycles:", len(nz_reject), nz_reject[:10])
print("NON-ZERO errors cycles:", len(nz_err), nz_err[:10])
print("NON-ZERO n_disarm_blocked cycles:", len(nz_disarm), nz_disarm[:10])
PY
rm -f "$J"
echo "=== DONE hc5 ==="
