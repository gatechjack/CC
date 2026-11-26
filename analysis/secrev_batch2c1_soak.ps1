# secrev_batch2c1_soak.ps1 -- BATCH 2c-1 soak re-check (READ-ONLY). broker auth + 6 divisions +
# Key Vault + ccxt/Coinbase WS data since 05:11Z + tracebacks/errors. No writes. Gates 2c-2.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis',[string]$Tag='soak1')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
$hostt='azureuser@trading.jacksumner.com'
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
ROOT=/home/azureuser/trading_corp
TCS=$(systemctl show trading-corp -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //')
J(){ journalctl -u trading-corp --since "$TCS" --no-pager 2>/dev/null; }
echo "soak window since=$TCS  now=$(date -u +%F' '%H:%M:%SZ)  (deploy 05:11:27Z)"
echo "=====services (active / tracebacks)====="
for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder; do
  S=$(systemctl show $u -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //')
  tb=$(journalctl -u $u --since "$S" --no-pager 2>/dev/null | grep -c 'Traceback')
  echo "  $u active=$(systemctl is-active $u) tracebacks=$tb"
done
echo "=====trading-corp level=error lines (classify benign vs new)====="
echo "  count=$(J | grep -cE 'level=error| ERROR |CRITICAL')"
J | grep -iE 'level=error| ERROR |CRITICAL' | sed -E 's/0x[0-9a-fA-F]+/0x<R>/g' | tail -6 | sed 's/^/    /'
echo "=====broker auth====="
echo "  Robinhood activity=$(J | grep -icE 'robinhood|rh_session|robin_stocks')"
J | grep -iE 'robinhood|rh_session|robin_stocks' | grep -iE 'equity|position|reconcile|session (ok|valid|refreshed)|snapshot' | tail -1 | sed 's/^/    /'
echo "  Bitunix reconciler activity=$(J | grep -icE 'bitunix_position_reconciler|bitunix.*reconcil')"
J | grep -iE 'bitunix_position_reconciler|bitunix.*reconcil' | tail -1 | sed 's/^/    /'
echo "  Kalshi pm_live_driver cycles/account:"
for a in kalshi_jack kalshi_karen kalshi_marc kalshi_trey; do echo "    $a cycles=$(J | grep -cE "pm_live_driver cycle $a")"; done
echo "=====6 divisions up (distinct division activity)====="
for d in bitunix mace pead robinhood_pmcc tasty_options_iron_condor kalshi polymarket; do
  echo "    $d activity=$(J | grep -icE "$d")"
done
echo "=====Key Vault / managed-identity====="
echo "  trading-corp azure Response-200=$(J | grep -cE 'http_logging_policy: Response status: 200')"
PMS=$(systemctl show prediction-markets-web -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //')
echo "  pm-web azure Response-200=$(journalctl -u prediction-markets-web --since "$PMS" --no-pager 2>/dev/null | grep -cE 'http_logging_policy: Response status: 200')"
echo "  azure/msal/KV ERRORS (excl http-logging + ,401/403 ms coincidences)=$(J | grep -iE 'msal|azure-identity|azure\.identity|keyvault|credential' | grep -iE 'error|exception|fail|unauthoriz|expired|denied' | grep -vE 'http_logging_policy' | grep -ivE '[0-9]{2}:[0-9]{2}:[0-9]{2},(401|403)' | grep -c .)"
J | grep -oE 'Response status: [0-9]+' | sort | uniq -c | sed 's/^/    tc azure /'
echo "=====ccxt / Coinbase WS feed receiving data since 05:11Z====="
MCRS=$(systemctl show market-context-recorder -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //')
MJ(){ journalctl -u market-context-recorder --since "$MCRS" --no-pager 2>/dev/null; }
echo "  market-context-recorder coinbase/ccxt.pro/watch_ticker refs=$(MJ | grep -icE 'coinbase|ccxt\.pro|watch_ticker|COINBASE_SPOT|spot')"
MJ | grep -iE 'coinbase|watch_ticker|COINBASE_SPOT|ws|tick|spot' | tail -3 | sed -E 's/0x[0-9a-fA-F]+/0x<R>/g; s/^/    MCR: /'
echo "  mcr WS gap/disconnect warnings=$(MJ | grep -icE 'gap|disconnect|reconnect|stale|ws.*error|NoneType')"
echo "  trading-corp bitunix ws feed refs=$(J | grep -icE 'bitunix ws feed|bitunix-ws')"
J | grep -iE 'bitunix ws feed' | tail -1 | sed 's/^/    TC-WS: /'
echo "=====END SOAK====="
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ($line.TrimEnd()) } }
[IO.File]::WriteAllText((Join-Path $OutDir ('36_batch2c1_'+$Tag+'.txt')),(($log -join "`n")+"`n"),$enc)
Write-Output ("--- wrote 36_batch2c1_"+$Tag+".txt ---")
