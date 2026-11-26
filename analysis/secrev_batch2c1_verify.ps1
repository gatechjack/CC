# secrev_batch2c1_verify.ps1 -- BATCH 2c-1 POST-DEPLOY verify (0f). READ-ONLY (ssh + PC curl). No writes.
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
# pre-2c1 reference PIDs (from 0a probe 33_): must all CHANGE
declare -A OLD=( [trading-corp]=8931 [prediction-markets-web]=663 [sfp-card-watcher]=672 [market-context-recorder]=660 )
echo "=====versions on disk (==loaded by the fresh processes)====="
for p in aiohttp yarl fastapi starlette opentelemetry-api cryptography msal pyjwt urllib3 pillow; do
  echo "   $p=$("$VENV/pip" show $p 2>/dev/null | awk '/^Version:/{print $2}')"
done
echo "=====services: new PID + 0 tracebacks(5min) + fresh-load evidence====="
for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder; do
  pid=$(systemctl show $u -p MainPID --value); start=$(systemctl show $u -p ExecMainStartTimestamp --value)
  nrr=$(systemctl show $u -p NRestarts --value); act=$(systemctl is-active $u)
  old=${OLD[$u]}; chg=$([ "$pid" != "$old" ] && echo CHANGED || echo "SAME(!)")
  START=$(echo "$start" | sed -E 's/^[A-Za-z]+ //')
  tb=$(journalctl -u $u --since "$START" --no-pager 2>/dev/null | grep -c 'Traceback')
  dc=$(grep -cE '/home/azureuser/trading_corp/venv/.*\(deleted\)' /proc/$pid/maps 2>/dev/null)
  echo "### $u active=$act pid=$pid (was $old -> $chg) NRestarts=$nrr start=$start tracebacks_5min=$tb deleted-mapped=$dc"
  grep -oE '/home/azureuser/trading_corp/venv/[^ ]+(aiohttp|yarl|_http)[^ ]*\.so[^ ]*' /proc/$pid/maps 2>/dev/null | sort -u | sed 's/^/     so: /' | head -4
done
echo "=====health + peers====="
echo "   healthz:8000=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null) healthz:8081=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8081/healthz 2>/dev/null)"
echo "   caddy=$(systemctl is-active caddy) authelia=$(systemctl is-active authelia) authelia_health=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:9091/api/health 2>/dev/null)"
echo "=====engine import smoke (versions the INTERPRETER resolves)====="
"$VENV/python" - <<PY 2>/dev/null
import aiohttp,yarl,fastapi,starlette
try:
    import opentelemetry.version as _v; otel=_v.__version__
except Exception:
    from importlib.metadata import version; otel=version("opentelemetry-api")
print(f"   import: aiohttp={aiohttp.__version__} yarl={yarl.__version__} fastapi={fastapi.__version__} starlette={starlette.__version__} opentelemetry-api={otel}")
PY
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ($line.TrimEnd()) } }
$curl=(Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if($curl){
  L "[PC curl via Caddy -- expect 302 / 405 / 400]"
  L ("   trading / -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1)))
  L ("   webhook GET -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/webhook/tradingview/lord-otter 2>&1 | Select-Object -First 1)))
  L ("   predictions / -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://predictions.jacksumner.com/ 2>&1 | Select-Object -First 1)))
}
L ""
L "===== JACK CLICK-THROUGH (log in via Authelia first) ====="
L "trading.jacksumner.com PAGES (each auto-loads its HTMX partials):"
L "   /  (home: market-ribbon, stat-cards, trade-flow, equity-curve partials)"
L "   /trades   /approvals   /research   /system"
L "   /mace     (mace/partials: prices, candles, rungs, halt)"
L "   /sfp  /sfp/construct  /sfp/llm   (sfp/partials: equity,header,mode-split,near-miss,recon,state-board; construct/partials board+summary; llm/partials/body)"
L "   /telemetry/iron_condor   /telemetry/pead   (pead/partials/live)"
L "   each DIVISION tile on the home page -> /division/<slug> (loads division-equity-curve, donchian-chart, scout, llm-analysis, pair-analysis, pmcc-pricing)"
L "   parametrized (open from a live tile so the id is real): /partials/kalshi-analysis/<event_id>, /partials/polymarket-analysis/<event_id>"
L "predictions.jacksumner.com PAGES:"
L "   /   /live   /live/events   /farm   /farm/<category>   /whale/<wallet>   /account/<account_id>"
L "Confirm: every page renders (no 500), partials fill (no broken HTMX swaps), no console/network errors."
[IO.File]::WriteAllText((Join-Path $OutDir '34_batch2c1_verify.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 34_batch2c1_verify.txt ---"
