# pk_pm_rostered_clean.ps1 -- READ-ONLY. Compact one-line-per-whale dump of the ROSTERED-category row
# (avoids wrap ambiguity). No mutation. Run: powershell -ep bypass -f .\pk_pm_rostered_clean.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_rostclean_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_rostclean.txt
venv/bin/python - > "$OUT" 2>&1 <<'PY'
import sqlite3
ROSTER=[
 ("0x16bb9951a36fce71e2ef57890b786145e0ba8492","SDTrading","mlb","LIVE"),
 ("0x2dc13c6bda81b202281e796953a7323de675b33c","xifutloong3","mlb","LIVE"),
 ("0x52f454c43b23504d2dc39e034bf19469fd592b15","Kh4mz4t","ufc","PIN"),
 ("0x99b1b05948d6e58a51fcd366b7e4b183b198196a","STC14","ufc","PIN"),
 ("0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4","000why000","ufc","PIN"),
 ("0x6dd6314d1670f9f1ccccbd6746b0bf2f2fa0f5f4","4751346","ufc","PIN"),
 ("0xc3e550fae1c90b71675f3355e5864c240bea519d","kutsumiakia","ufc","PIN"),
 ("0x75e091ca3f8e5481c2166c82fba0669e3a65fe50","FordBronco","nfl","PIN"),
 ("0x2fb0f88ef5ba40e799b996e6f07b590d92b4abf8","AIisTheNewWD","nfl","PIN"),
 ("0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009","BetMechanic","nba","PIN"),
 ("0xd1acd3925d895de9aec98ff95f3a30c5279d08d5","Kickstand7","fed","PIN"),
 ("0x71edffd0d70a1da823ff07a3c6fc81457294d338","pako","fed","PIN"),
]
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
def sc(w,cat,r):
    x=c.execute("SELECT score FROM pm_score_snapshot WHERE wallet=? AND category=? AND routine=?",(w,cat,r)).fetchone()
    return ("%.3f"%x["score"]) if x and x["score"] is not None else "NR"
print("name          tag  rcat  n     win%  roiC%   roiN%   net_pnl     avgWinPx flag      netroi recency dq")
for w,name,rcat,tag in ROSTER:
    s=c.execute("SELECT * FROM pm_category_stats WHERE wallet=? AND category=?",(w,rcat)).fetchone()
    if not s: print("%-13s %-4s %-5s (NO ROSTERED-CATEGORY ROWS)"%(name,tag,rcat)); continue
    awp=s["avg_win_price"]
    flag="CHALK" if (isinstance(awp,(int,float)) and awp>=0.85) else ("CONTESTED" if (isinstance(awp,(int,float)) and awp<0.70) else "mid")
    unv=" UNVERIFIABLE(n<10)" if (s["n_resolved"] or 0)<10 else ""
    print("%-13s %-4s %-5s %-5s %-5s %-7s %-7s %-11s %-8s %-9s %-6s %-6s %s%s"%(
        name,tag,rcat,s["n_resolved"],
        ("%.0f"%(s["win_rate"]*100)) if isinstance(s["win_rate"],(int,float)) else "-",
        ("%+.1f"%(s["roi"]*100)) if isinstance(s["roi"],(int,float)) else "-",
        ("%+.1f"%(s["roi_notional"]*100)) if isinstance(s["roi_notional"],(int,float)) else "-",
        ("%+.0f"%s["net_realized_pnl"]) if isinstance(s["net_realized_pnl"],(int,float)) else "-",
        ("%.2f"%awp) if isinstance(awp,(int,float)) else "-",
        flag, sc(w,rcat,"net_roi"), sc(w,rcat,"recency_weighted"),
        (s["data_quality"] or "-"), unv))
c.close()
PY
echo "ROSTCLEAN_DONE lines=$(wc -l < "$OUT")"
cat "$OUT"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== ROSTERED-category clean rows (one line per whale) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
