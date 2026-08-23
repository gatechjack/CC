# pk_pm_reval.ps1 -- MUTATION (autonomous, board-authorized). Clears the lossy Kickstand7 checkpoint DB by
# RENAME (three EXPLICIT paths -- no glob, no rm; the lossy DB is preserved as .lossy_bak for before/after),
# then re-backfills Kickstand7 ONLY on a fresh DB (migrations 001+002 -> corrected PK) and inspects.
# Additive; NO restart, NO sudo, NO legacy DB contact. All box ops live INSIDE this file and are delivered
# to the box via `az --scripts "@file"` (no base64). Run: powershell -ep bypass -f .\pk_pm_reval.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_reval_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_reval.txt
{
echo "=== PID before (must be 850993) ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value) Active=$(systemctl show trading-corp.service -p ActiveState --value) since=$(systemctl show trading-corp.service -p ActiveEnterTimestamp --value)"
echo "=== RENAME lossy checkpoint DB (explicit paths, no glob, no rm; kept as .lossy_bak) ==="
ls -la data/ | grep prediction_markets
if [ -f data/prediction_markets.db ]; then mv data/prediction_markets.db data/prediction_markets.db.lossy_bak; echo "moved .db -> .lossy_bak"; else echo "  no .db"; fi
if [ -f data/prediction_markets.db-wal ]; then mv data/prediction_markets.db-wal data/prediction_markets.db-wal.lossy_bak; echo "moved -wal"; else echo "  no -wal (expected if clean close)"; fi
if [ -f data/prediction_markets.db-shm ]; then mv data/prediction_markets.db-shm data/prediction_markets.db-shm.lossy_bak; echo "moved -shm"; else echo "  no -shm (expected if clean close)"; fi
echo "after rename:"; ls -la data/ | grep prediction_markets
echo "=== RE-BACKFILL Kickstand7 (fresh DB -> migrations 001+002 -> new PK) ==="
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py backfill --only-wallets 0xd1acd3925d895de9aec98ff95f3a30c5279d08d5 2>&1
echo "BACKFILL_EXIT=$?"
echo "=== INSPECTION (corrected data) ==="
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
print("schema_version:", [r[0] for r in c.execute("SELECT version FROM schema_version ORDER BY version")])
print("cp PK cols:", [r[1] for r in c.execute("PRAGMA table_info(pm_closed_position)") if r[5]>0])
print("total rows stored:", c.execute("SELECT COUNT(1) FROM pm_closed_position").fetchone()[0])
print("distinct condition_ids:", c.execute("SELECT COUNT(DISTINCT condition_id) FROM pm_closed_position").fetchone()[0])
print("-- suspect breakdown --")
for r in c.execute("SELECT COALESCE(suspect_reason,'(scoreable)') s, COUNT(1) n FROM pm_closed_position GROUP BY 1 ORDER BY 2 DESC"):
    print("   %-16s %d"%(r['s'],r['n']))
print("   pnl_anomaly:", c.execute("SELECT COUNT(1) FROM pm_closed_position WHERE pnl_anomaly=1").fetchone()[0])
print("-- category coverage (source) --")
for r in c.execute("SELECT category, category_source, COUNT(1) n FROM pm_closed_position GROUP BY 1,2 ORDER BY 3 DESC"):
    print("   %-9s %-11s %d"%(r['category'],r['category_source'],r['n']))
print("-- pm_category_stats (corrected) --")
print("   %-9s %4s %4s %4s %12s %8s %8s %6s %6s %s"%("cat","nres","nexc","nano","net","roiC%","roiN%","dqC%","dq$%","dq"))
q="SELECT category, n_resolved, n_excluded, n_anomaly, net_realized_pnl, roi, roi_notional, dq_count_pct, dq_dollar_pct, data_quality FROM pm_category_stats ORDER BY n_resolved DESC"
for r in c.execute(q):
    rc=("%.1f"%(r['roi']*100)) if r['roi'] is not None else "n/a"
    rn=("%.1f"%(r['roi_notional']*100)) if r['roi_notional'] is not None else "n/a"
    print("   %-9s %4s %4s %4s %12.2f %8s %8s %6.1f %6.1f %s"%(r['category'],r['n_resolved'],r['n_excluded'],r['n_anomaly'],r['net_realized_pnl'] or 0,rc,rn,(r['dq_count_pct'] or 0)*100,(r['dq_dollar_pct'] or 0)*100,r['data_quality'] or '-'))
print("-- two-sided legs BOTH persist now (were 1 each in lossy run) --")
for slug in ("us-government-shutdown","was-trump-hacked","us-x-iran-ceasefire"):
    rows=c.execute("SELECT outcome o, outcome_index oi, ROUND(total_bought,2) tb, ROUND(realized_pnl,2) rp FROM pm_closed_position WHERE event_slug LIKE ? ORDER BY outcome_index",(slug+"%",)).fetchall()
    print("   %s: %d legs -> %s"%(slug,len(rows),["%s idx%s tb=%s rp=%s"%(x['o'],x['oi'],x['tb'],x['rp']) for x in rows]))
c.close()
PY
echo "=== PID after (must still be 850993) ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value)"
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM P1 STEP-3 RE-VALIDATION (rename + re-backfill Kickstand7) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 120; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_reval.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
