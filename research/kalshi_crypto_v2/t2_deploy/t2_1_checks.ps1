# T2 step 1: prod-state precheck + managed-identity cred pre-flight, via az run-command (root, NO sudo).
# Requires: you are az-logged-in with run-command rights on the VM (az account show).
$ErrorActionPreference = 'Stop'
$rg = 'RG-SHARED-PROD'; $vm = 'tc-prod-vm'
$bash = @'
cd /home/azureuser/trading_corp
echo "--- kcv2 tables (expect NONE) ---"
sqlite3 -readonly data/trading_corp.db "SELECT name FROM sqlite_master WHERE name LIKE 'kcv2_%';"
echo "--- service (expect not-found) ---"
systemctl status trading-corp-kcv2-observer 2>/dev/null | head -1 || echo "not found"
echo "--- managed-identity KV read (expect: KV_OK True True) ---"
/home/azureuser/trading_corp/venv/bin/python -c "from azure.identity import DefaultAzureCredential as D; from azure.keyvault.secrets import SecretClient as S; c=S('https://kv-tc-vtwbowt3wtkpy.vault.azure.net/', D()); print('KV_OK', bool(c.get_secret('KALSHI-KAREN-API-KEY-ID').value), bool(c.get_secret('KALSHI-KAREN-PRIVATE-KEY-PEM').value))"
'@
$bash = $bash -replace "`r", ""
(az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $bash | ConvertFrom-Json).value[0].message
