# pk_pm_directional_slice.ps1 -- ITEM 3 (READ-ONLY): partition each flagged whale's ROSTERED category into
# ONE-SIDED (single outcome_index per condition_id = directional, copyable) vs TWO-SIDED (both legs = spread /
# market-making, NOT copyable by the fail-closed first-side-wins matcher). Per partition: n, wins, win_rate,
# net, cost_basis, cost-ROI, avg_win_price. For FordBronco: avg_price-sum distribution of two-sided pairs as a
# WEAK simultaneous(hedge)-vs-sequential(directional) signal (no entry ts in /closed-positions -> UNDETERMINABLE).
# No mutation. Run: powershell -ep bypass -f .\pk_pm_directional_slice.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_dirslice_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python - <<'PY'
import sqlite3
from collections import defaultdict
WHALES=[  # (wallet, name, rostered_cat, detailed?)
 ("0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009","BetMechanic","nba",True),
 ("0x75e091ca3f8e5481c2166c82fba0669e3a65fe50","FordBronco","nfl",True),
 ("0xd1acd3925d895de9aec98ff95f3a30c5279d08d5","Kickstand7","fed",False),
 ("0x6dd6314d1670f9f1ccccbd6746b0bf2f2fa0f5f4","4751346","ufc",False),
 ("0x52f454c43b23504d2dc39e034bf19469fd592b15","Kh4mz4t","ufc",False),
 ("0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4","000why000","ufc",False),
]
c=sqlite3.connect("file:data/prediction_markets.db?mode=ro",uri=True); c.row_factory=sqlite3.Row
def part(w,cat):
    rows=c.execute("SELECT condition_id,outcome_index,won,realized_pnl,cost_basis,avg_price FROM pm_closed_position WHERE wallet=? AND category=? AND pnl_suspect=0",(w,cat)).fetchall()
    legs=defaultdict(list)
    for r in rows: legs[r["condition_id"]].append(r)
    one=[cid for cid,v in legs.items() if len(v)==1]
    two=[cid for cid,v in legs.items() if len(v)>1]
    def agg(cids):
        rr=[r for cid in cids for r in legs[cid]]
        n=len(rr); wins=sum(1 for r in rr if r["won"]); losses=n-wins
        net=sum((r["realized_pnl"] or 0) for r in rr); cost=sum((r["cost_basis"] or 0) for r in rr)
        awp=[r["avg_price"] for r in rr if r["won"] and r["avg_price"] is not None]
        return dict(ncid=len(cids),nrows=n,wins=wins,losses=losses,net=net,cost=cost,
                    roiC=(net/cost if cost>0 else None),awp=(sum(awp)/len(awp) if awp else None))
    return agg(one),agg(two),legs,two
def pr(tag,a):
    wr=("%.0f"%(100.0*a["wins"]/(a["wins"]+a["losses"]))) if (a["wins"]+a["losses"]) else "-"
    print("   %-10s markets=%-5d rows=%-5d win%%=%-4s net=%-+10.0f cost=%-11.0f cost-ROI=%-8s avgWinPx=%s"%(
        tag,a["ncid"],a["nrows"],wr,a["net"],a["cost"],
        ("%+.1f%%"%(100*a["roiC"]) if a["roiC"] is not None else "-"),
        ("%.2f"%a["awp"] if a["awp"] is not None else "-")))
print("ITEM 3 -- DIRECTIONAL SLICE (one-sided = copyable directional; two-sided = spread/market-making)")
print("="*104)
for w,name,cat,detailed in WHALES:
    one,two,legs,twocids=part(w,cat)
    tot_net=one["net"]+two["net"]
    print("\n### %s (%s)  total scoreable net=%+.0f"%(name,cat,tot_net))
    pr("ONE-SIDED",one); pr("TWO-SIDED",two)
    if tot_net!=0:
        print("   -> net split: one-sided %.0f%% / two-sided %.0f%%"%(100*one["net"]/tot_net,100*two["net"]/tot_net))
    # verdict
    if one["roiC"] is not None and one["roiC"]>0 and one["nrows"]>=10:
        print("   -> COPYABLE SUBSET EXISTS: one-sided n=%d cost-ROI=%+.1f%% (directional signal on this slice)"%(one["nrows"],100*one["roiC"]))
    else:
        print("   -> NO copyable one-sided edge (one-sided too small or non-positive) -> edge is spread/market-making")
    if detailed and twocids:
        # weak simultaneous-vs-sequential: sum of avg_price across the legs of each two-sided market
        import statistics
        sums=[]
        for cid in twocids:
            s=sum((r["avg_price"] or 0) for r in legs[cid]); sums.append(s)
        lo=sum(1 for s in sums if s<0.98); mid=sum(1 for s in sums if 0.98<=s<=1.02); hi=sum(1 for s in sums if s>1.02)
        print("   FordBronco/paired avg_price-sum (weak hedge signal): pairs=%d  <0.98=%d  0.98-1.02=%d  >1.02=%d  median=%.2f"%(
            len(sums),lo,mid,hi,statistics.median(sums) if sums else 0))
        print("   (sum~1.0 => legs price out to a locked spread = simultaneous hedge/market-making; far from 1.0 => possibly sequential/directional.")
        print("    /closed-positions has NO entry timestamps -> simultaneous-vs-sequential is UNDETERMINABLE here; /activity per-fill timestamps would settle it.)")
c.close()
PY
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== ITEM 3: directional-slice study (read-only) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
