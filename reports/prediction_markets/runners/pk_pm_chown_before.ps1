# pk_pm_chown_before.ps1 -- ITEM 1a: chown the PM DB to azureuser:azureuser (Board-authorized), verify
# ownership, and snapshot the BEFORE state. ONLY the PM DB is touched; legacy trading_corp.db is NOT.
# The chown runs as root (az run-command control-plane); the subsequent refresh (runner B) runs AS AZUREUSER.
# Run: powershell -ep bypass -f .\pk_pm_chown_before.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_chown_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
echo "=== MainPID before (850993) ==="; systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "=== ownership BEFORE ==="; ls -l data/prediction_markets.db data/prediction_markets.db-wal data/prediction_markets.db-shm 2>&1
echo "=== CHOWN PM DB (+wal/+shm; missing sidecars are expected, not an error) ==="
chown azureuser:azureuser data/prediction_markets.db && echo "chowned .db" || echo "CHOWN .db FAILED"
for s in -wal -shm; do
  if [ -e "data/prediction_markets.db$s" ]; then chown azureuser:azureuser "data/prediction_markets.db$s" && echo "chowned db$s"; else echo "no db$s (expected)"; fi
done
echo "=== ownership AFTER ==="; ls -l data/prediction_markets.db data/prediction_markets.db-wal data/prediction_markets.db-shm 2>&1
echo "=== azureuser can write now? (runuser as the cron user) ==="
runuser -u azureuser -- test -w data/prediction_markets.db && echo "azureuser CAN write db" || echo "azureuser CANNOT write db"
echo "=== legacy DB UNTOUCHED (context; must stay azureuser-owned, mtime = live engine) ==="; ls -l data/trading_corp.db
echo "=== BEFORE snapshot ==="
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("file:data/prediction_markets.db?mode=ro",uri=True); c.row_factory=sqlite3.Row
print("closed_total =", c.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0])
print("category_stats_rows =", c.execute("SELECT COUNT(*) FROM pm_category_stats").fetchone()[0])
print("category_stats_MAX_updated_ts =", c.execute("SELECT MAX(updated_ts) FROM pm_category_stats").fetchone()[0])
print("whale_MAX_last_refresh_ts =", c.execute("SELECT MAX(last_refresh_ts) FROM pm_whale").fetchone()[0])
print("per-wallet rows (BEFORE):")
for r in c.execute("SELECT wallet,COUNT(*) n FROM pm_closed_position GROUP BY wallet ORDER BY n DESC"):
    print("  ",r["wallet"][:14],r["n"])
c.close()
PY
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== ITEM 1a: chown PM DB + BEFORE snapshot =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
