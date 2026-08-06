# T2 Appendix B (FALLBACK - only if step 1's KV read failed, or you want explicit creds). NO sudo.
# Secret path: az (your identity) fetches -> streamed to the VM over ssh STDIN only (never printed,
# never in history) -> written to an azureuser-owned 600 file in ~/.config. The /etc drop-in (no secret)
# is installed via az run-command (root), passed with --scripts "@shortpath" (see step 1 note).
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
$rg = 'RG-SHARED-PROD'; $vm = 'tc-prod-vm'; $vault = 'kv-tc-vtwbowt3wtkpy'
Write-Host "Fetching secrets from Key Vault (values are NOT displayed) ..."
$kid = az keyvault secret show --vault-name $vault --name KALSHI-KAREN-API-KEY-ID --query value -o tsv
$pem = az keyvault secret show --vault-name $vault --name KALSHI-KAREN-PRIVATE-KEY-PEM --query value -o tsv
if (-not $kid -or -not $pem) { Write-Host "STOP: empty secret returned from Key Vault."; exit 1 }
if ($pem -match "`n") { Write-Host "STOP: PEM has real newlines; an EnvironmentFile value must be one line. Use managed identity instead."; exit 1 }
$body = "KALSHI_KAREN_API_KEY_ID=$kid`nKALSHI_KAREN_PRIVATE_KEY_PEM=$pem`n"
$body | ssh $h "install -d -m 700 /home/azureuser/.config; cat > /home/azureuser/.config/kcv2-kalshi.env; chmod 600 /home/azureuser/.config/kcv2-kalshi.env"
Write-Host "Env file written 600 in ~/.config. Line count (want 2) + perms:"
ssh $h "wc -l /home/azureuser/.config/kcv2-kalshi.env; stat -c '%a %U' /home/azureuser/.config/kcv2-kalshi.env"
Write-Host "Installing the drop-in (no secret) via az run-command (root) ..."
$drop = @'
install -d -m 755 /etc/systemd/system/trading-corp-kcv2-observer.service.d
printf '[Service]\nEnvironmentFile=/home/azureuser/.config/kcv2-kalshi.env\n' > /etc/systemd/system/trading-corp-kcv2-observer.service.d/10-creds.conf
systemctl daemon-reload
echo DROPIN_DONE
'@
$sh = Join-Path $env:TEMP 't2_kcv2_B.sh'
[IO.File]::WriteAllText($sh, ($drop -replace "`r", ""), (New-Object System.Text.UTF8Encoding($false)))
$short = (New-Object -ComObject Scripting.FileSystemObject).GetFile($sh).ShortPath
$raw = az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts "@$short"
try { ($raw | ConvertFrom-Json).value[0].message } catch { $raw }
Remove-Item $sh -ErrorAction SilentlyContinue
