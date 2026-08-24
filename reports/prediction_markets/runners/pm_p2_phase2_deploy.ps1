$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$stage    = "C:\Users\AA Incorporado\cc\pm_p2_stage.tgz"
$deploySh = "C:\Users\AA Incorporado\cc\_p2_deploy_files.sh"
$proveSh  = "C:\Users\AA Incorporado\cc\_p2_restart_prove.sh"
foreach ($f in @($stage,$deploySh,$proveSh)) { if (-not (Test-Path $f)) { Write-Host "MISSING $f - STOP"; exit 1 } }
Write-Host ("[local] stage sha256: " + (Get-FileHash -Algorithm SHA256 $stage).Hash.ToLower())

Write-Host "[1/3] scp tarball to box (azureuser)..."
scp -o StrictHostKeyChecking=accept-new $stage ($h + ":pm_p2_stage.tgz")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED $LASTEXITCODE - STOP"; exit 1 }

Write-Host "[2/3] deploy files as azureuser (extract + modes + per-file sha + GOTCHA-2 gate; ABORTS before restart on failure)..."
Get-Content -Raw $deploySh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
$filesRC = $LASTEXITCODE
Write-Host ("[2/3] file-deploy exit = " + $filesRC)
if ($filesRC -ne 0) { Write-Host "FILE DEPLOY FAILED/GATED (rc=$filesRC) -> NOT restarting pm_web. STOP." ; exit 1 }

Write-Host "[3/3] restart pm_web + prove-live (root via az run-command)..."
# az sends bytes with no CR strip; base64 the LF-normalized prove script so the arg is one clean token (no
# @file/path-with-spaces quirks, no inline-quoting of the script's own quotes/heredocs).
$lf  = ((Get-Content -Raw $proveSh) -replace "`r","")
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($lf))
$remote = "echo $b64 | base64 -d | bash"
$msg = & az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $remote --query "value[0].message" -o tsv
Write-Host ("[3/3] az exit = " + $LASTEXITCODE)
Write-Host "----- BEGIN box output -----"
Write-Host $msg
Write-Host "----- END box output -----"
Write-Host "[done] pm_p2 CP2-Ph2 deploy finished"
