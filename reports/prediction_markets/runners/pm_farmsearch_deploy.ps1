$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$wt = "C:\Users\AA Incorporado\cc-pm-farm-search-wt"
$tar = "C:\Users\AA Incorporado\cc\pm_farmsearch_files.tar"
$grafted = "C:\Users\AA Incorporado\cc\_farmsearch_app_grafted.py"
$applysh = "C:\Users\AA Incorporado\cc\pm_farmsearch_deploy_apply.sh"
$postsh = "C:\Users\AA Incorporado\cc\pm_farmsearch_deploy_postcheck.sh"
$cands = @("$env:SystemRoot\System32\OpenSSH", "$env:SystemRoot\Sysnative\OpenSSH",
           "$env:ProgramFiles\Git\usr\bin", "${env:ProgramFiles(x86)}\Git\usr\bin",
           "$env:LOCALAPPDATA\Programs\Git\usr\bin")
function Find-Exe($name) {
  $c = Get-Command $name -ErrorAction SilentlyContinue; if ($c) { return $c.Source }
  foreach ($d in $cands) { $p = Join-Path $d "$name.exe"; if (Test-Path $p) { return $p } }
  throw "$name not found (try a 64-bit shell)."
}
$ssh = Find-Exe "ssh"; $scp = Find-Exe "scp"; $az = Find-Exe "az"
if (Test-Path $tar) { Remove-Item $tar -Force }
# stage: git archive HEAD the 4 wholesale/new files (LF blobs); grafted app.py streamed separately
& git -C $wt archive -o $tar HEAD trading_corp/prediction_markets/search_run.py trading_corp/scripts/pm_cli.py trading_corp/prediction_markets/web/templates/pm_farm_league.html trading_corp/prediction_markets/web/templates/partials/pm_search_status.html
if (-not (Test-Path $tar)) { throw "git archive produced no tar" }
& $ssh -o ConnectTimeout=20 $h "rm -rf /home/azureuser/pm_farmsearch_stage; mkdir -p /home/azureuser/pm_farmsearch_stage"
& $scp -o ConnectTimeout=20 $tar ("{0}:/home/azureuser/pm_farmsearch_files.tar" -f $h)
& $scp -o ConnectTimeout=20 $grafted ("{0}:/home/azureuser/pm_farmsearch_stage/app_grafted.py" -f $h)
& $ssh -o ConnectTimeout=20 $h "tar xf /home/azureuser/pm_farmsearch_files.tar -C /home/azureuser/pm_farmsearch_stage; rm -f /home/azureuser/pm_farmsearch_files.tar"
Remove-Item $tar -Force -ErrorAction SilentlyContinue

Write-Host "==== APPLY (drift-check + backup + apply + Gate-A; auto-restore on failure) ===="
$applyOut = (Get-Content -Raw $applysh | & $ssh -o ConnectTimeout=25 -o ServerAliveInterval=15 $h "tr -d '\r\357\273\277' | bash") 2>&1
$applyOut | ForEach-Object { Write-Host $_ }
if (-not ($applyOut -match "DEPLOY_APPLIED_OK")) {
  Write-Host "==== APPLY DID NOT REPORT SUCCESS -- NOT RESTARTING pm_web (apply auto-restored on any gate failure). STOP. ===="
  exit 1
}

Write-Host "==== RESTART pm_web (prediction-markets-web) via az -- ENGINE NOT touched ===="
& $az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart prediction-markets-web" --query "value[0].message" -o tsv

Write-Host "==== POST-CHECK ===="
Get-Content -Raw $postsh | & $ssh -o ConnectTimeout=25 -o ServerAliveInterval=15 $h "tr -d '\r\357\273\277' | bash"
Write-Host "---- deploy runner exit ----"
