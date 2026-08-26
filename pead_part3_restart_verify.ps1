$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# STAGED - the ONE privileged restart via az run-command (ROOT, no sudo) + all-division verify.
$rg = "RG-SHARED-PROD"
$vm = "tc-prod-vm"
$h  = "azureuser@trading.jacksumner.com"
$sh = "C:\Users\AA Incorporado\cc\pead_part3_restart_verify.sh"
$out = "pead_part3_restart_out.txt"
if (-not (Test-Path $sh)) { Write-Host "MISSING $sh - STOP"; exit 1 }
Write-Host "[local] scp verify script to box..."
scp -o StrictHostKeyChecking=accept-new "$sh" ($h + ":pead_part3_restart_verify.sh")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED - STOP"; exit 1 }
Write-Host "[box/root via az] single restart + all-division verify (blocks ~60-90s)..."
$raw = az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts "tr -d '\r' < /home/azureuser/pead_part3_restart_verify.sh | bash"
try { ($raw | ConvertFrom-Json).value[0].message | Out-File -Encoding utf8 $out } catch { $raw | Out-File -Encoding utf8 $out }
Write-Host ("[done] az exit " + $LASTEXITCODE)
Get-Content $out