# secrev_batch4_verify.ps1 -- BATCH 4 after-boot verify. READ-ONLY. Run once the box is back (~3-5 min post-reboot).
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$OutputEncoding=New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false
$enc=New-Object System.Text.UTF8Encoding $false
$hostt='azureuser@trading.jacksumner.com'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
# reachability first
$ping = ssh -o BatchMode=yes -o ConnectTimeout=12 $hostt "echo UP; uptime -p" 2>&1
if($ping -notmatch 'UP'){ L ("BOX NOT REACHABLE YET: " + ($ping -join ' ')); L "Re-run in a minute; if >10 min since reboot, STOP and tell Jack (snapshot tc-prod-os-20261124c is the fallback)."; [IO.File]::WriteAllText((Join-Path $OutDir '23_batch4_verify.txt'),(($log -join "`n")+"`n"),$enc); exit 0 }
$sh=@'
set -u
ROOT=/home/azureuser/trading_corp
echo "[kernel] running uname -r=$(uname -r)  (expect 6.8.0-1074-azure)"
echo "[uptime] $(uptime -p); booted $(uptime -s)"
echo "[reboot-required] $([ -f /var/run/reboot-required ] && echo 'STILL SET' || echo 'cleared')"
echo "[units active?]:"; for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia chrony walinuxagent; do echo "    $u=$(systemctl is-active $u 2>/dev/null)"; done
echo "[engine] PID=$(systemctl show trading-corp -p MainPID --value) NRestarts=$(systemctl show trading-corp -p NRestarts --value) started=$(systemctl show trading-corp -p ExecMainStartTimestamp --value)"
echo "[bind] ss :8000 (expect 127.0.0.1):"; ss -tlnH 2>/dev/null | grep ':8000' | sed 's/^/    /'
echo "[venv] starlette=$("$ROOT/venv/bin/pip" show starlette 2>/dev/null | awk '/^Version:/{print $2}') python=$("$ROOT/venv/bin/python" -V 2>&1)"
echo "[crontab lines] $(crontab -l 2>/dev/null | grep -cE '^[^#[:space:]]')"
START=$(systemctl show trading-corp -p ExecMainStartTimestamp --value 2>/dev/null | sed -E 's/^[A-Za-z]+ //')
echo "[tracebacks since boot] $(journalctl -u trading-corp --since "$START" --no-pager 2>/dev/null | grep -c 'Traceback')"
echo "[authelia health] $(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:9091/api/health 2>/dev/null)"
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ("  "+$line.Trim()) } }
$curl=(Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if($curl){
  L "[PC curl via Caddy -- expect 302 / 405 / 400]"
  L ("    / -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1)))
  L ("    webhook GET -> " + ((& $curl -sS -o NUL -D - --max-time 12 https://trading.jacksumner.com/webhook/tradingview/lord-otter 2>&1 | Select-Object -First 1)))
  L ("    malformed-Host -> " + ((& $curl -sS -o NUL -D - --max-time 12 -H "Host: trading.jacksumner.com/x" https://trading.jacksumner.com/ 2>&1 | Select-Object -First 1)))
}
[IO.File]::WriteAllText((Join-Path $OutDir '23_batch4_verify.txt'),(($log -join "`n")+"`n"),$enc)
