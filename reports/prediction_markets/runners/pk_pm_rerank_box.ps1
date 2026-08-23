# pk_pm_rerank_box.ps1 -- READ-ONLY. Task 0 (first-cron verify) + Task 1/2 data (corrected scoreboard,
# two-sided share, category mix) for the 12 rostered whales, straight from the deployed PM DB.
# No mutation, no roster/agent_state write, no legacy DB contact. Run: powershell -ep bypass -f .\pk_pm_rerank_box.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_rerank_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_rerank.txt
{
echo "===== TASK 0: FIRST CRON FIRE VERIFICATION ====="
echo "-- now (UTC) --"; date -u
echo "-- engine MainPID (must be 850993) --"; systemctl show -p MainPID -p ActiveState trading-corp.service 2>/dev/null
echo "-- legacy DB (must be untouched by PM) --"; stat -c '%n size=%s mtime=%y' data/trading_corp.db 2>/dev/null
echo "-- pm_refresh.log (the cron target) --"
if [ -f /home/azureuser/pm_refresh.log ]; then
  stat -c 'log size=%s mtime=%y' /home/azureuser/pm_refresh.log
  echo "---- pm_refresh.log FULL (verdicts/429s) ----"; cat /home/azureuser/pm_refresh.log
  echo "---- (end log) ----"
else
  echo "pm_refresh.log ABSENT -> cron has NOT fired (or failed before writing)."
fi
echo "-- current azureuser crontab pm line --"; crontab -u azureuser -l 2>/dev/null | grep -F 'pm_cli.py refresh' || echo "(pm cron line missing!)"
venv/bin/python - <<'PY'
import sqlite3, datetime
def ts(v):
    try: return datetime.datetime.utcfromtimestamp(int(v)).strftime('%Y-%m-%d %H:%M:%S') if v else '-'
    except Exception: return str(v)
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
print("== pm_whale integrity (pulled==stored?, complete?, last backfill/refresh) ==")
tot=0; bad=0; incomplete=0
for r in c.execute("SELECT wallet,user_name,backfill_complete,last_pulled,last_stored,last_backfill_ts,last_refresh_ts FROM pm_whale ORDER BY user_name"):
    integ = "OK" if (r["last_pulled"]==r["last_stored"]) else "MISMATCH!"
    if integ!="OK": bad+=1
    if not r["backfill_complete"]: incomplete+=1
    print("  %-14s %-13s complete=%s pulled=%s stored=%s [%s] bf=%s rf=%s" % (
        r["wallet"][:14], (r["user_name"] or "?")[:13], r["backfill_complete"], r["last_pulled"], r["last_stored"], integ,
        ts(r["last_backfill_ts"]), ts(r["last_refresh_ts"])))
    tot+=1
print("  SUMMARY whales=%d pulled!=stored=%d incomplete=%d" % (tot,bad,incomplete))
print("== row counts ==")
print("  pm_closed_position TOTAL =", c.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0])
print("  distinct wallets in closed =", c.execute("SELECT COUNT(DISTINCT wallet) FROM pm_closed_position").fetchone()[0])
print("  per-wallet stored rows:")
for r in c.execute("SELECT p.wallet,w.user_name,COUNT(*) n FROM pm_closed_position p LEFT JOIN pm_whale w ON p.wallet=w.wallet GROUP BY p.wallet ORDER BY n DESC"):
    print("    %-14s %-13s %d" % (r["wallet"][:14],(r["user_name"] or "?")[:13], r["n"]))
c.close()
PY
echo ""
echo "===== TASK 1: CORRECTED SCOREBOARD (FULL pm_category_stats + both scores; NO min_resolved filter) ====="
venv/bin/python - <<'PY'
import sqlite3
ROSTER={
 "0x16bb9951a36fce71e2ef57890b786145e0ba8492":("SDTrading","mlb","LIVE"),
 "0x2dc13c6bda81b202281e796953a7323de675b33c":("xifutloong3","mlb","LIVE"),
 "0x52f454c43b23504d2dc39e034bf19469fd592b15":("Kh4mz4t","ufc","PIN"),
 "0x99b1b05948d6e58a51fcd366b7e4b183b198196a":("STC14","ufc","PIN"),
 "0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4":("000why000","ufc","PIN"),
 "0x6dd6314d1670f9f1ccccbd6746b0bf2f2fa0f5f4":("4751346","ufc","PIN"),
 "0xc3e550fae1c90b71675f3355e5864c240bea519d":("kutsumiakia","ufc","PIN"),
 "0x75e091ca3f8e5481c2166c82fba0669e3a65fe50":("FordBronco","nfl","PIN"),
 "0x2fb0f88ef5ba40e799b996e6f07b590d92b4abf8":("AIisTheNewWD","nfl","PIN"),
 "0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009":("BetMechanic","nba","PIN"),
 "0xd1acd3925d895de9aec98ff95f3a30c5279d08d5":("Kickstand7","fed","PIN"),
 "0x71edffd0d70a1da823ff07a3c6fc81457294d338":("pako","fed","PIN"),
}
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
def sc(w,cat,routine):
    r=c.execute("SELECT score FROM pm_score_snapshot WHERE wallet=? AND category=? AND routine=?",(w,cat,routine)).fetchone()
    return ("%.3f"%r["score"]) if r and r["score"] is not None else "-"
def f(v,p="%.1f"):
    return (p%v) if isinstance(v,(int,float)) else "-"
for w,(name,rcat,tag) in ROSTER.items():
    print("### %s [%s] rostered=%s  wallet=%s" % (name,tag,rcat,w))
    rows=c.execute("SELECT * FROM pm_category_stats WHERE wallet=? ORDER BY (category=?) DESC, n_resolved DESC",(w,rcat)).fetchall()
    if not rows: print("   (no category_stats rows)"); continue
    for s in rows:
        star="*ROSTERED*" if s["category"]==rcat else ""
        awp=s["avg_win_price"]
        chalk="CHALK" if (isinstance(awp,(int,float)) and awp>=0.85) else ("CONTESTED" if (isinstance(awp,(int,float)) and awp<0.70) else "mid")
        print("   %-8s n=%-5s W/L=%s/%s win%%=%s roiC%%=%s roiN%%=%s net=%s cost=%s avgWinPx=%s [%s] nExcl=%s dq$%%=%s dqCnt%%=%s DQ=%s ANOM=%s | net_roi=%s recency=%s %s" % (
            s["category"], s["n_resolved"], s["wins"], s["losses"],
            f(s["win_rate"]*100 if isinstance(s["win_rate"],(int,float)) else None,"%.0f"),
            f(s["roi"]*100 if isinstance(s["roi"],(int,float)) else None,"%+.1f"),
            f(s["roi_notional"]*100 if isinstance(s["roi_notional"],(int,float)) else None,"%+.1f"),
            f(s["net_realized_pnl"],"%+.0f"), f(s["cost_basis"],"%.0f"), f(awp,"%.2f"), chalk,
            s["n_excluded"], f(s["dq_dollar_pct"]*100 if isinstance(s["dq_dollar_pct"],(int,float)) else None,"%.0f"),
            f(s["dq_count_pct"]*100 if isinstance(s["dq_count_pct"],(int,float)) else None,"%.0f"),
            s["data_quality"] or "-", s["n_anomaly"], sc(w,s["category"],"net_roi"), sc(w,s["category"],"recency_weighted"), star))
c.close()
PY
echo ""
echo "===== TASK 2: TWO-SIDED HOLDINGS (hedging != conviction) + CATEGORY MIX (game vs futures) ====="
venv/bin/python - <<'PY'
import sqlite3, re
ROSTER={
 "0x16bb9951a36fce71e2ef57890b786145e0ba8492":("SDTrading","mlb"),
 "0x2dc13c6bda81b202281e796953a7323de675b33c":("xifutloong3","mlb"),
 "0x52f454c43b23504d2dc39e034bf19469fd592b15":("Kh4mz4t","ufc"),
 "0x99b1b05948d6e58a51fcd366b7e4b183b198196a":("STC14","ufc"),
 "0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4":("000why000","ufc"),
 "0x6dd6314d1670f9f1ccccbd6746b0bf2f2fa0f5f4":("4751346","ufc"),
 "0xc3e550fae1c90b71675f3355e5864c240bea519d":("kutsumiakia","ufc"),
 "0x75e091ca3f8e5481c2166c82fba0669e3a65fe50":("FordBronco","nfl"),
 "0x2fb0f88ef5ba40e799b996e6f07b590d92b4abf8":("AIisTheNewWD","nfl"),
 "0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009":("BetMechanic","nba"),
 "0xd1acd3925d895de9aec98ff95f3a30c5279d08d5":("Kickstand7","fed"),
 "0x71edffd0d70a1da823ff07a3c6fc81457294d338":("pako","fed"),
}
FUT=re.compile(r'(champion|championship|mvp|to-win|winner|world-series|super-bowl-champ|finals|division|conference|most-|award|-season|playoff|trophy|bracket|title|-cup\b|-odds\b|make-the|to-reach|to-make)')
DATE=re.compile(r'\d{4}-\d{2}-\d{2}')
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
print("-- two-sided share (same condition_id held on >1 outcome_index) --")
for w,(name,rcat) in ROSTER.items():
    per=c.execute("SELECT condition_id,COUNT(*) legs FROM pm_closed_position WHERE wallet=? GROUP BY condition_id",(w,)).fetchall()
    nm=len(per); ts=sum(1 for r in per if r["legs"]>1)
    perc=c.execute("SELECT condition_id,COUNT(*) legs FROM pm_closed_position WHERE wallet=? AND category=? GROUP BY condition_id",(w,rcat)).fetchall()
    nmc=len(perc); tsc=sum(1 for r in perc if r["legs"]>1)
    print("   %-13s ALL: %d/%d two-sided=%.0f%%   ROSTERED(%s): %d/%d two-sided=%.0f%%" % (
        name, ts, nm, (100.0*ts/nm if nm else 0), rcat, tsc, nmc, (100.0*tsc/nmc if nmc else 0)))
print("-- category MIX in the ROSTERED category (heuristic: slug regex; game=has date, futures=futures-marker) --")
for w,(name,rcat) in ROSTER.items():
    rows=c.execute("SELECT slug,event_slug,title FROM pm_closed_position WHERE wallet=? AND category=?",(w,rcat)).fetchall()
    if not rows: print("   %-13s %s: (0 rows)"%(name,rcat)); continue
    fut=game=other=0; samp_f=set(); samp_g=set()
    for r in rows:
        s=((r["event_slug"] or r["slug"] or "")).lower()
        if FUT.search(s): fut+=1; samp_f.add(s[:46])
        elif DATE.search(s): game+=1; samp_g.add(s[:46])
        else: other+=1
    n=len(rows)
    print("   %-13s %s n=%d  game=%d(%.0f%%) futures=%d(%.0f%%) other/undated=%d(%.0f%%)" % (
        name,rcat,n,game,100.0*game/n,fut,100.0*fut/n,other,100.0*other/n))
    if samp_f: print("       futures-like sample:", " | ".join(list(samp_f)[:3]))
    if samp_g: print("       game-like sample:   ", " | ".join(list(samp_g)[:2]))
c.close()
PY
} > "$OUT" 2>&1
echo "RERANK_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM FARM RE-RANK: Task 0 + Task 1/2 data (READ-ONLY) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 220; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 45; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_rerank.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
