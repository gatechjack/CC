# T2 step 1: prod-state precheck + managed-identity cred pre-flight, via az run-command (root, NO sudo).
# The bash is written to a temp .sh (LF, no BOM) and passed with --scripts "@file" (a multi-line
# --scripts STRING gets mangled by az.cmd on Windows); the 8.3 short path avoids the space in %TEMP%.
# Requires: az-logged-in with run-command rights (az account show).
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
$sh = Join-Path $env:TEMP 't2_kcv2_1.sh'
[IO.File]::WriteAllText($sh, ($bash -replace "`r", ""), (New-Object System.Text.UTF8Encoding($false)))
$short = (New-Object -ComObject Scripting.FileSystemObject).GetFile($sh).ShortPath
$raw = az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts "@$short"
try { ($raw | ConvertFrom-Json).value[0].message } catch { $raw }
Remove-Item $sh -ErrorAction SilentlyContinue
