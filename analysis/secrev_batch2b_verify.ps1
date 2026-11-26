# secrev_batch2b_verify.ps1 -- BATCH 2b post-deploy verify. READ-ONLY (ssh journal/health + PC curl). No writes.
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
echo "[venv] pyjwt=$("$ROOT/venv/bin/pip" show pyjwt 2>/dev/null|awk '/^Version:/{print $2}') urllib3=$("$ROOT/venv/bin/pip" show urllib3 2>/dev/null|awk '/^Version:/{print $2}') pillow=$("$ROOT/venv/bin/pip" show pillow 2>/dev/null|awk '/^Version:/{print $2}') starlette=$("$ROOT/venv/bin/pip" show starlette 2>/dev/null|awk '/^Version:/{print $2}') fastapi=$("$ROOT/venv/bin/pip" show fastapi 2>/dev/null|awk '/^Version:/{print $2}') python=$("$ROOT/venv/bin/python" -V 2>&1)"
echo "[bind] $(ss -tlnH 2>/dev/null | grep ':8000' | sed 's/^/ /')"
START=$(systemctl show trading-corp -p ExecMainStartTimestamp --value 2>/dev/null | sed -E 's/^[A-Za-z]+ //')
J(){ journalctl -u trading-corp --since "$START" --no-pager 2>/dev/null; }
echo "[tracebacks since restart] $(J | grep -c 'Traceback')"
echo "[other services] pm-web=$(systemctl is-active prediction-markets-web) sfp=$(systemctl is-active sfp-card-watcher) mcr=$(systemctl is-active market-context-recorder) caddy=$(systemctl is-active caddy) authelia=$(systemctl is-active authelia)"
echo "[authelia health] $(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:9091/api/health 2>/dev/null)"
echo "=== BROKER AUTH evidence (since restart; the engine holds the Key Vault creds) ==="
echo "[Robinhood] activity=$(J | grep -icE 'robinhood|robin_stocks|rh_broker|rh_session') ; sample:"
J | grep -iE 'robinhood|rh_session|robin_stocks' | grep -iE 'position|reconcile|account|session (ok|valid|refreshed)|fetched|equity' | tail -1 | sed 's/^/    /'
echo "[Bitunix] activity=$(J | grep -icE 'bitunix') ; sample:"
J | grep -iE 'bitunix' | grep -iE 'primed|bars_cached|position|account|balance' | tail -1 | sed 's/^/    /'
echo "[Kalshi] pm_live_driver cycles per account:"
for a in kalshi_jack kalshi_karen kalshi_marc kalshi_trey; do echo "    $a cycles=$(J | grep -cE "pm_live_driver cycle $a")"; done
echo "[AUTH ERRORS since restart (excl. benign Polymarket data-api 429/incomplete)]:"
AERR=$(J | grep -iE 'auth.*fail|unauthoriz|\b401\b|\b403\b|invalid.*(key|signature|token)|session.*expir|attestation.*(fail|expir|403)|login.*required|signature.*(invalid|mismatch)' | grep -ivE 'polymarket|data-api|/positions|incomplete chunked|429')
echo "    count=$(printf '%s' "$AERR" | grep -c . )"
printf '%s' "$AERR" | tail -5 | sed 's/^/    ERR: /'
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
[IO.File]::WriteAllText((Join-Path $OutDir '27_batch2b_verify.txt'),(($log -join "`n")+"`n"),$enc)
