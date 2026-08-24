$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# READ-ONLY Kalshi key auth test: scp the test to the box, stream the box runner (discovers KEY_VAULT_URI
# from the engine service env, runs it as azureuser via the VM Managed Identity). NO orders (read-only
# KalshiBroker has no place_order). Secrets never printed. Engine-PID bracketed. The test file is removed after.
# Operator pastes ONE line:  powershell -ep bypass -f .\pm_kalshi_key_test.ps1
$h = "azureuser@trading.jacksumner.com"
$py = "C:\Users\AA Incorporado\cc\pm_kalshi_key_test.py"
$sh = "C:\Users\AA Incorporado\cc\pm_kalshi_key_test_box.sh"
if (-not (Test-Path $py)) { Write-Host "MISSING $py - STOP"; exit 1 }
if (-not (Test-Path $sh)) { Write-Host "MISSING $sh - STOP"; exit 1 }
Write-Host "[local] scp read-only key-auth test to box..."
scp -o StrictHostKeyChecking=accept-new "$py" ($h + ":pm_kalshi_key_test.py")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED - STOP"; exit 1 }
Write-Host "[box] running read-only key-auth test (KeyVault via VM Managed Identity; NO orders; secrets never printed)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] ssh exit " + $LASTEXITCODE)
