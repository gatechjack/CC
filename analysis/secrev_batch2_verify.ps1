# secrev_batch2_verify.ps1 -- BATCH 2 post-verify. READ-ONLY (ssh + PC curl). No writes.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$OutputEncoding=New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false
$enc=New-Object System.Text.UTF8Encoding $false
$hostt='azureuser@trading.jacksumner.com'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
ROOT=/home/azureuser/trading_corp
echo "[engine] PID=$(systemctl show trading-corp -p MainPID --value) active=$(systemctl is-active trading-corp) NRestarts=$(systemctl show trading-corp -p NRestarts --value) started=$(systemctl show trading-corp -p ExecMainStartTimestamp --value)"
echo "[bind] ss :8000 (MUST be 127.0.0.1, NOT 0.0.0.0):"
ss -tlnH 2>/dev/null | grep ':8000' | sed 's/^/    /'
echo "[venv] starlette=$("$ROOT/venv/bin/pip" show starlette 2>/dev/null | awk '/^Version:/{print $2}') fastapi=$("$ROOT/venv/bin/pip" show fastapi 2>/dev/null | awk '/^Version:/{print $2}') python=$("$ROOT/venv/bin/python" -V 2>&1)"
START=$(systemctl show trading-corp -p ExecMainStartTimestamp --value 2>/dev/null | sed -E 's/^[A-Za-z]+ //')
echo "[tracebacks since restart ($START)]: $(journalctl -u trading-corp --since "$START" --no-pager 2>/dev/null | grep -c 'Traceback')"
echo "[live divisions initialized since restart] (expect all 6):"
for d in bitunix_sfp robinhood_pead bitunix_futures kalshi_copy_trading robinhood_pmcc robinhood_mace; do
  n=$(journalctl -u trading-corp --since "$START" --no-pager 2>/dev/null | grep -icE "$d"); echo "    $d: $n log refs"
done
echo "[other services still active]:"
for u in prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia chrony; do echo "    $u=$(systemctl is-active $u 2>/dev/null)"; done
echo "[authelia health]=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:9091/api/health 2>/dev/null)"
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ("  "+$line.Trim()) } }
$curl=(Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if($curl){
  L "[PC curl via Caddy -- expect 302 / 405 / 400]"
  L ("    / -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1)))
  L ("    webhook GET -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/webhook/tradingview/lord-otter 2>&1 | Select-Object -First 1)))
  L ("    malformed-Host -> " + ((& $curl -sS -o NUL -D - --max-time 12 -H "Host: trading.jacksumner.com/x" https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1)))
}
[IO.File]::WriteAllText((Join-Path $OutDir '21_batch2_verify.txt'),(($log -join "`n")+"`n"),$enc)
