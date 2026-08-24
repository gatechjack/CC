$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# READ-ONLY. Roots-reads /etc/caddy/Caddyfile (typically not azureuser-readable) via az run-command. No edits, no reload.
$sh = "C:\Users\AA Incorporado\cc\_p2_caddy_read.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING $sh - STOP"; exit 1 }
$lf  = ((Get-Content -Raw $sh) -replace "`r","")
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($lf))
$remote = "echo $b64 | base64 -d | bash"
Write-Host "[remote] READ-ONLY Caddyfile dump (root via az; no edits, no reload)..."
$msg = & az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $remote --query "value[0].message" -o tsv
Write-Host ("[az exit] " + $LASTEXITCODE)
Write-Host "----- BEGIN box output -----"
Write-Host $msg
Write-Host "----- END box output -----"
