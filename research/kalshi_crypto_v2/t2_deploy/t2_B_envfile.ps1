# T2 Appendix B (FALLBACK - only if step 1 cred pre-flight failed, or you want explicit creds).
# Fetches KALSHI-KAREN-* from Key Vault and installs them as an EnvironmentFile the observer reads
# via the env-override branch of load_creds. Secrets are never printed and never in shell history:
# az output is captured into variables, then streamed to the VM over ssh STDIN only.
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
$vault = 'kv-tc-vtwbowt3wtkpy'
Write-Host "Fetching secrets from Key Vault (values are NOT displayed) ..."
$kid = az keyvault secret show --vault-name $vault --name KALSHI-KAREN-API-KEY-ID --query value -o tsv
$pem = az keyvault secret show --vault-name $vault --name KALSHI-KAREN-PRIVATE-KEY-PEM --query value -o tsv
if (-not $kid -or -not $pem) { Write-Host "STOP: empty secret returned from Key Vault."; exit 1 }
if ($pem -match "`n") { Write-Host "STOP: PEM has real newlines; an EnvironmentFile value must be one line. Use managed identity instead."; exit 1 }
# env file in azureuser's home (600, no sudo). Secret flows to the VM only via ssh STDIN.
$body = "KALSHI_KAREN_API_KEY_ID=$kid`nKALSHI_KAREN_PRIVATE_KEY_PEM=$pem`n"
$body | ssh $h "install -d -m 700 /home/azureuser/.config; cat > /home/azureuser/.config/kcv2-kalshi.env; chmod 600 /home/azureuser/.config/kcv2-kalshi.env"
Write-Host "Env file written 600 in ~/.config. Line count (want 2) + perms:"
ssh $h "wc -l /home/azureuser/.config/kcv2-kalshi.env; stat -c '%a %U' /home/azureuser/.config/kcv2-kalshi.env"
Write-Host "Installing the drop-in (no secret) via sudo ..."
ssh -t $h "sudo install -d -m 755 /etc/systemd/system/trading-corp-kcv2-observer.service.d && printf '[Service]\nEnvironmentFile=/home/azureuser/.config/kcv2-kalshi.env\n' | sudo tee /etc/systemd/system/trading-corp-kcv2-observer.service.d/10-creds.conf && sudo systemctl daemon-reload && echo DROPIN_DONE"
