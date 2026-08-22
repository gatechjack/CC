# pk_pm_deploy.ps1 -- MUTATION (Jack runs on return). ADDITIVE file copy ONLY of the P1 artifacts to
# /home/azureuser/trading_corp: trading_corp/prediction_markets/ (new pkg), trading_corp/scripts/pm_cli.py
# (new file), config/pm_seed_wallets.yaml (new file). tar contains ONLY these PM paths -> cannot touch any
# legacy file. NO engine restart, NO existing-file edits, NO sudo. Prints local sha256 for chain-of-custody
# and re-hashes on the box after extract. Verify DEPLOY_DONE + sha256 match, then run the DEPLOY_SEQUENCE.
# Run: powershell -ep bypass -f .\pk_pm_deploy.ps1
$ErrorActionPreference = 'Stop'
$W = "C:\Users\AA Incorporado\cc-prediction-markets-wt"
$mods = @("db", "category", "ingest", "rosters", "stats")
Write-Host "== LOCAL sha256 (compare to box after extract) =="
foreach ($m in $mods) {
    $h = (Get-FileHash (Join-Path $W "trading_corp\prediction_markets\$m.py") -Algorithm SHA256).Hash.ToLower()
    Write-Host ("  {0,-12} {1}" -f "$m.py", $h)
}
Write-Host ("  {0,-12} {1}" -f "pm_cli.py", (Get-FileHash (Join-Path $W "trading_corp\scripts\pm_cli.py") -Algorithm SHA256).Hash.ToLower())
$tar = Join-Path $env:TEMP 'pm_deploy.tgz'
if (Test-Path $tar) { Remove-Item $tar -Force }
tar.exe -czf $tar -C $W trading_corp/prediction_markets trading_corp/scripts/pm_cli.py config/pm_seed_wallets.yaml
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_dep_chunk.sh'
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($tar))
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_deploy.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
$bash = @'
R=/home/azureuser/trading_corp
echo "=== PRE-CHECK (targets should be ABSENT for a clean first deploy) ==="
for f in trading_corp/prediction_markets trading_corp/scripts/pm_cli.py config/pm_seed_wallets.yaml; do
  if [ -e "$R/$f" ]; then echo "  ALREADY PRESENT (re-deploy overwrite of PM-owned file): $f"; else echo "  absent (new): $f"; fi
done
echo "=== EXTRACT (additive; tar holds ONLY PM paths -> no legacy file touched) ==="
base64 -d /tmp/pm_deploy.b64 | tar xzvf - -C "$R"
echo "=== VERIFY package files present ==="
find "$R/trading_corp/prediction_markets" -name '*.py' | sort
ls -la "$R/trading_corp/scripts/pm_cli.py" "$R/config/pm_seed_wallets.yaml"
echo "=== BOX sha256 (compare to LOCAL printed by the runner) ==="
sha256sum "$R/trading_corp/prediction_markets/db.py" "$R/trading_corp/prediction_markets/category.py" "$R/trading_corp/prediction_markets/ingest.py" "$R/trading_corp/prediction_markets/rosters.py" "$R/trading_corp/prediction_markets/stats.py" "$R/trading_corp/scripts/pm_cli.py"
echo "=== NO restart / NO sudo / NO existing-file edits. Engine untouched. ==="
rm -f /tmp/pm_deploy.b64
echo "DEPLOY_DONE"
'@
$bash = $bash -replace "`r", ""
$b2 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$first2 = $true
for ($i = 0; $i -lt $b2.Length; $i += $size) {
    $chunk = $b2.Substring($i, [Math]::Min($size, $b2.Length - $i))
    $op = if ($first2) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_dep.sh.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first2 = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pm_dep.sh.b64 > /tmp/pm_dep.sh && bash /tmp/pm_dep.sh; rm -f /tmp/pm_dep.sh.b64 /tmp/pm_dep.sh`n", $enc)
Write-Host "== PM P1 DEPLOY (additive copy) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf, $tar -Force -ErrorAction SilentlyContinue
