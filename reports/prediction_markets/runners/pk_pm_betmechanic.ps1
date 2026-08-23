# pk_pm_betmechanic.ps1 -- MUTATION (autonomous). Complete the PARTIAL BetMechanic wallet (hit cap=8000 =
# >8000 positions) by re-backfilling with --cap 50000. Re-runs rollup+compute_scores over the whole DB.
# If it still hits 50000 -> stays PARTIAL (mega-whale), reported + excluded. Additive; no restart/sudo/legacy.
# Run: powershell -ep bypass -f .\pk_pm_betmechanic.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_betm_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_betm.txt
{
echo "=== PID before (must be 850993) ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value) since=$(systemctl show trading-corp.service -p ActiveEnterTimestamp --value)"
echo "=== RE-BACKFILL BetMechanic --cap 50000 (mega-whale completion; 429 backoff) ==="
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py backfill --only-wallets 0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009 --cap 50000 2>&1
echo "BACKFILL_EXIT=$?"
echo "=== BetMechanic verdict (pm_whale) ==="
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
r=c.execute("SELECT backfill_complete bc, last_pulled lp, last_stored ls FROM pm_whale WHERE wallet='0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009'").fetchone()
print("BetMechanic: complete=%s pulled=%s stored=%s -> %s"%(r["bc"],r["lp"],r["ls"],"COMPLETE (now RANKED)" if r["bc"]==1 else "STILL PARTIAL (mega-whale >50000; NOT ranked)"))
print("whales complete:", c.execute("SELECT COUNT(1) FROM pm_whale WHERE backfill_complete=1").fetchone()[0], "/ 12")
print("scored wallets:", c.execute("SELECT COUNT(DISTINCT wallet) FROM pm_score_snapshot").fetchone()[0])
print("closed rows total:", c.execute("SELECT COUNT(1) FROM pm_closed_position").fetchone()[0])
c.close()
PY
echo "=== PID after (must still be 850993) ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value)"
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM P1 -- COMPLETE BetMechanic (--cap 50000) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 40; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_betm.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
