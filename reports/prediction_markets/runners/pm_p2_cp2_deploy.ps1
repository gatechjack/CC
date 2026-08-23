$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = "azureuser@trading.jacksumner.com"
$rg = "RG-SHARED-PROD"
$vm = "tc-prod-vm"
$stage = "C:\Users\AA Incorporado\cc\pm_p2_stage.tgz"
$sh = "C:\Users\AA Incorporado\cc\pm_p2_cp2_deploy.sh"
if (-not (Test-Path $stage)) { Write-Host "MISSING stage: $stage - STOP"; exit 1 }
if (-not (Test-Path $sh))    { Write-Host "MISSING deploy.sh: $sh - STOP"; exit 1 }
Write-Host ("[local] stage sha256: " + (Get-FileHash -Algorithm SHA256 $stage).Hash.ToLower())
Write-Host "[local] scp stage tarball to box (as azureuser)..."
scp -o StrictHostKeyChecking=accept-new $stage ($h + ":pm_p2_stage.tgz")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED $LASTEXITCODE - STOP"; exit 1 }
Write-Host "[root] az vm run-command invoke RunShellScript (sanctioned root channel; scoped Option A)..."
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts "@$sh" --query "value[0].message" -o tsv
Write-Host ("[done] az run-command exit " + $LASTEXITCODE)
