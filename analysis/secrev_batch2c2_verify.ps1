# secrev_batch2c2_verify.ps1 -- BATCH 2c-2 POST-DEPLOY verify (0g). READ-ONLY (ssh + PC curl). No writes.
# Focus: managed-identity + Key Vault succeed in every service that uses them; 0 msal/azure/KV errors; broker auth.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
$hostt='azureuser@trading.jacksumner.com'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
ROOT=/home/azureuser/trading_corp; VENV="$ROOT/venv/bin"
echo "=====versions====="
for p in cryptography msal azure-identity azure-core azure-keyvault-secrets; do echo "   $p=$("$VENV/pip" show $p 2>/dev/null | awk '/^Version:/{print $2}')"; done
echo "=====services new PID + tracebacks(5min)====="
for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder; do
  pid=$(systemctl show $u -p MainPID --value); start=$(systemctl show $u -p ExecMainStartTimestamp --value); act=$(systemctl is-active $u)
  START=$(echo "$start" | sed -E 's/^[A-Za-z]+ //'); tb=$(journalctl -u $u --since "$START" --no-pager 2>/dev/null | grep -c 'Traceback')
  echo "### $u active=$act pid=$pid start=$start tracebacks_5min=$tb"
done
echo "=====Key Vault / managed-identity (per service that uses azure-identity)====="
for u in trading-corp prediction-markets-web; do
  START=$(systemctl show $u -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //')
  J(){ journalctl -u $u --since "$START" --no-pager 2>/dev/null; }
  s200=$(J | grep -cE 'http_logging_policy: Response status: 200')
  kv=$(J | grep -ciE 'keyvault|key vault|SecretClient|vault.azure.net')
  mi=$(J | grep -ciE 'ManagedIdentity|DefaultAzureCredential|IMDS|169\.254\.169\.254|get_token')
  # REAL auth errors (exclude the benign credential-chain 401/404 that precedes the 200 burst, and ,NNN-ms coincidences)
  err=$(J | grep -iE 'msal|azure-identity|azure\.identity|keyvault|credential' | grep -iE 'error|exception|fail|unauthoriz|invalid|expired|denied' | grep -vE 'http_logging_policy' | grep -c .)
  echo "### $u azure_200=$s200 kv_refs=$kv mi_refs=$mi azure/msal/kv_errors=$err"
  J | grep -iE 'msal|azure-identity|keyvault|credential' | grep -iE 'error|exception|fail|unauthoriz|invalid|expired|denied' | grep -vE 'http_logging_policy' | tail -3 | sed 's/^/     ERR: /'
  echo "   azure status-code distribution:"; J | grep -oE 'Response status: [0-9]+' | sort | uniq -c | sed 's/^/     /'
done
echo "=====broker auth (engine holds the KV creds)====="
START=$(systemctl show trading-corp -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //'); J(){ journalctl -u trading-corp --since "$START" --no-pager 2>/dev/null; }
echo "   Robinhood activity=$(J | grep -icE 'robinhood|rh_session|robin_stocks'); sample:"; J | grep -iE 'robinhood|rh_session' | grep -iE 'equity|position|reconcile|session (ok|valid|refreshed)' | tail -1 | sed 's/^/     /'
echo "   Bitunix activity=$(J | grep -icE 'bitunix'); sample:"; J | grep -iE 'bitunix' | grep -iE 'primed|bars_cached|position|balance|reconciler' | tail -1 | sed 's/^/     /'
echo "   Kalshi cycles per account:"; for a in kalshi_jack kalshi_karen kalshi_marc kalshi_trey; do echo "     $a cycles=$(J | grep -cE "pm_live_driver cycle $a")"; done
echo "   broker auth errors (excl benign Polymarket data-api): $(J | grep -iE 'auth.*fail|unauthoriz|\b403\b|invalid.*(key|signature|token)|attestation.*(fail|expir)|session.*expir' | grep -ivE 'polymarket|data-api|/positions|429|[0-9]{2}:[0-9]{2}:[0-9]{2},(401|403)' | grep -c .)"
echo "=====import smoke====="
"$VENV/python" -c "import cryptography,msal,azure.identity,azure.keyvault.secrets as k; print('   import OK: cryptography',cryptography.__version__,'msal',msal.__version__)" 2>&1 | sed 's/^/   /'
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ($line.TrimEnd()) } }
[IO.File]::WriteAllText((Join-Path $OutDir '35_batch2c2_verify.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 35_batch2c2_verify.txt ---"
