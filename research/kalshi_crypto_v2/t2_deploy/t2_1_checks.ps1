# T2 step 1: prod-state precheck + managed-identity cred pre-flight (READ-ONLY).
# Run from local PowerShell. Prints remote output; you apply OK / STOP-if from the sheet.
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
$bash = @'
cd /home/azureuser/trading_corp
echo "--- kcv2 tables (expect NONE) ---"
sqlite3 -readonly data/trading_corp.db "SELECT name FROM sqlite_master WHERE name LIKE 'kcv2_%';"
echo "--- service (expect not-found) ---"
systemctl status trading-corp-kcv2-observer 2>/dev/null | head -1 || echo "not found"
echo "--- managed-identity KV read (expect: KV_OK True True) ---"
venv/bin/python -c "from azure.identity import DefaultAzureCredential as D; from azure.keyvault.secrets import SecretClient as S; c=S('https://kv-tc-vtwbowt3wtkpy.vault.azure.net/', D()); print('KV_OK', bool(c.get_secret('KALSHI-KAREN-API-KEY-ID').value), bool(c.get_secret('KALSHI-KAREN-PRIVATE-KEY-PEM').value))"
'@
$bash | ssh $h "tr -d '\r' | bash"
