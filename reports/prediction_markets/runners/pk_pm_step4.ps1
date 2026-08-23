# pk_pm_step4.ps1 -- MUTATION (autonomous, board-authorized). Step 4: FULL 12-wallet backfill into the
# corrected-schema DB (per-wallet isolation; 429 backoff+retry; PARTIAL/cap-hit -> backfill_complete=0 =
# NOT ranked). Then rollup + compute_scores (already inside `backfill`), and print per-wallet verdicts +
# PID. Additive; NO restart/sudo/legacy contact. All box ops inside this file (run via -f).
# Run: powershell -ep bypass -f .\pk_pm_step4.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_step4_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_step4.txt
{
echo "=== PID before (must be 850993) ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value) Active=$(systemctl show trading-corp.service -p ActiveState --value) since=$(systemctl show trading-corp.service -p ActiveEnterTimestamp --value)"
echo "=== FULL 12-WALLET BACKFILL (per-wallet isolation; 429 backoff+retry; then rollup+compute_scores) ==="
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py backfill 2>&1
echo "BACKFILL_EXIT=$?"
echo "=== PER-WALLET VERDICTS (pm_whale) ==="
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
print("   %-44s %-8s %8s %8s %s"%("wallet","complete","pulled","stored","verdict"))
for r in c.execute("SELECT wallet, backfill_complete bc, last_pulled lp, last_stored ls FROM pm_whale ORDER BY bc, wallet"):
    v="COMPLETE" if r["bc"]==1 else "PARTIAL/INCOMPLETE (NOT RANKED)"
    ok="OK" if (r["lp"] is not None and r["lp"]==r["ls"]) else "PULLED!=STORED"
    print("   %-44s %-8s %8s %8s %s [%s]"%(r["wallet"],r["bc"],r["lp"],r["ls"],v,ok))
print("whales total:", c.execute("SELECT COUNT(1) FROM pm_whale").fetchone()[0],
      "| complete:", c.execute("SELECT COUNT(1) FROM pm_whale WHERE backfill_complete=1").fetchone()[0])
print("closed rows total:", c.execute("SELECT COUNT(1) FROM pm_closed_position").fetchone()[0],
      "| distinct wallets w/ rows:", c.execute("SELECT COUNT(DISTINCT wallet) FROM pm_closed_position").fetchone()[0])
print("score snapshots:", c.execute("SELECT COUNT(1) FROM pm_score_snapshot").fetchone()[0],
      "| scored wallets:", c.execute("SELECT COUNT(DISTINCT wallet) FROM pm_score_snapshot").fetchone()[0])
c.close()
PY
echo "=== PID after (must still be 850993) ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value)"
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM P1 STEP 4 -- FULL 12-WALLET BACKFILL =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 160; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_step4.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
