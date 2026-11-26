# secrev_batch7_verify.ps1 -- BATCH 7 post-deploy verify (same depth as 2c-1/0g). READ-ONLY (ssh + PC curl).
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
$hostt='azureuser@trading.jacksumner.com'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]; function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
declare -A OLD=( [trading-corp]=17947 [prediction-markets-web]=17975 [sfp-card-watcher]=18009 [market-context-recorder]=18024 )
echo "=====services: new PID + NRestarts + tracebacks(5min) + missing-lib====="
for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder; do
  pid=$(systemctl show $u -p MainPID --value); nr=$(systemctl show $u -p NRestarts --value); act=$(systemctl is-active $u)
  S=$(systemctl show $u -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //')
  tb=$(journalctl -u $u --since "$S" --no-pager 2>/dev/null | grep -c Traceback)
  ml=$(journalctl -u $u --since "$S" --no-pager 2>/dev/null | grep -icE 'cannot open shared object|error while loading shared|ImportError|undefined symbol|No module named')
  chg=$([ "$pid" != "${OLD[$u]}" ] && echo NEW || echo 'SAME(!)')
  echo "  $u active=$act pid=$pid(was ${OLD[$u]}=$chg) NRestarts=$nr tb_5min=$tb missing_lib=$ml"
done
echo "  caddy pid=$(systemctl show caddy -p MainPID --value) authelia pid=$(systemctl show authelia -p MainPID --value) (expect UNCHANGED 651/649)"
echo "=====Xvfb :99 + bind + reboot====="
echo "  Xvfb :99 pid=$(pgrep -f 'Xvfb :99' | head -1)"
echo "  ss 8000 (expect 127.0.0.1 only):"; ss -tlnH 2>/dev/null | grep ':8000' | sed 's/^/    /'
echo "  reboot-required=$([ -f /var/run/reboot-required ] && echo YES || echo no)"
echo "=====health + peers====="
echo "  healthz:8000=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/healthz) :8081=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8081/healthz)"
echo "  caddy=$(systemctl is-active caddy) authelia=$(systemctl is-active authelia) authelia_health=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:9091/api/health)"
TCS=$(systemctl show trading-corp -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //'); J(){ journalctl -u trading-corp --since "$TCS" --no-pager 2>/dev/null; }
echo "=====Key Vault / managed-identity (0 errors expected)====="
echo "  trading-corp azure_200=$(J | grep -cE 'http_logging_policy: Response status: 200') kv_refs=$(J | grep -ciE 'keyvault|key vault|SecretClient') azure/msal/kv_errors=$(J | grep -iE 'msal|azure-identity|keyvault|credential' | grep -iE 'error|exception|fail|unauthoriz|expired' | grep -vE 'http_logging_policy' | grep -ivE '[0-9]{2}:[0-9]{2}:[0-9]{2},(401|403)' | grep -c .)"
PMS=$(systemctl show prediction-markets-web -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //')
echo "  pm-web KV: $(journalctl -u prediction-markets-web --since "$PMS" --no-pager 2>/dev/null | grep -icE 'keyvault|loaded from Key Vault|SecretClient') refs, errors=$(journalctl -u prediction-markets-web --since "$PMS" --no-pager 2>/dev/null | grep -iE 'keyvault|credential|azure' | grep -icE 'error|exception|fail')"
echo "=====brokers + divisions====="
echo "  RH startup live_brokers: $(J | grep -oE 'live_brokers\": \[[^]]*\]' | head -1)"
echo "  Bitunix ws feed=$(J | grep -icE 'bitunix ws feed') reconciler=$(J | grep -icE 'bitunix_position_reconciler')"
echo "  Kalshi pm_live_driver lines=$(J | grep -cE 'pm_live_driver'); shard-snaps: $(for a in kalshi_jack kalshi_karen kalshi_marc kalshi_trey; do echo -n "$a=$(J | grep -cE "shard.snapshot $a") "; done)"
echo "  divisions active: $(for d in bitunix mace pead robinhood_pmcc kalshi polymarket; do echo -n "$d=$(J | grep -icE "$d") "; done)"
echo "  broker auth errors (excl benign): $(J | grep -iE 'auth.*fail|unauthoriz|\b403\b|attestation.*(fail|expir)|session.*expir' | grep -ivE 'polymarket|data-api|/positions|429|[0-9]{2}:[0-9]{2}:[0-9]{2},(401|403)' | grep -c .)"
echo "=====box-wide missing-lib scan since trading-corp restart====="
echo "  cannot-open-shared-object / ImportError / undefined-symbol (all units): $(journalctl --since "$TCS" --no-pager 2>/dev/null | grep -icE 'cannot open shared object|error while loading shared|undefined symbol|ImportError')"
journalctl --since "$TCS" --no-pager 2>/dev/null | grep -iE 'cannot open shared object|error while loading shared|undefined symbol' | tail -5 | sed 's/^/    /'
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ($line.TrimEnd()) } }
$curl=(Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if($curl){ L "[PC curl via Caddy -- expect 302 / 405 / 400]"
  L ("   / -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1)))
  L ("   webhook GET -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/webhook/tradingview/lord-otter 2>&1 | Select-Object -First 1)))
  L ("   malformed-Host -> " + ((& $curl -sS -o NUL -D - --max-time 12 -H "Host: trading.jacksumner.com/x" https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1))) }
[IO.File]::WriteAllText((Join-Path $OutDir '43_batch7_verify.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 43_batch7_verify.txt ---"
