# pk_pm_box_sha.ps1 -- READ-ONLY chain-of-custody: sha256 the ACTUAL deployed PM package files on the box,
# to compare against the local worktree before advancing prod-live (prod-live must reflect production truth).
# No mutation. Run: powershell -ep bypass -f .\pk_pm_box_sha.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_boxsha_box.sh'
$bash = @'
OUT=/tmp/pm_boxsha.txt
R=/home/azureuser/trading_corp
{
echo "=== deployed PM package sha256 (leaf name for comparison) ==="
for f in \
  "$R/trading_corp/prediction_markets/__init__.py" \
  "$R/trading_corp/prediction_markets/category.py" \
  "$R/trading_corp/prediction_markets/db.py" \
  "$R/trading_corp/prediction_markets/ingest.py" \
  "$R/trading_corp/prediction_markets/rosters.py" \
  "$R/trading_corp/prediction_markets/stats.py" \
  "$R/trading_corp/scripts/pm_cli.py" ; do
  if [ -f "$f" ]; then sha256sum "$f" | awk -v n="$(basename "$f")" '{print $1"  "n}'; else echo "MISSING  $(basename "$f")"; fi
done
echo "=== config location probe (both candidates) ==="
for c in "$R/config/pm_seed_wallets.yaml" "$R/trading_corp/config/pm_seed_wallets.yaml"; do
  if [ -f "$c" ]; then sha256sum "$c" | awk -v n="$c" '{print $1"  "n}'; else echo "absent: $c"; fi
done
} > "$OUT" 2>&1
echo "BOXSHA_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== BOX deployed PM package sha256 (read-only) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
[IO.File]::WriteAllText($tf, "sed -n '1,40p' /tmp/pm_boxsha.txt`n", $enc)
Write-Host "---- OUTPUT ----"
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
