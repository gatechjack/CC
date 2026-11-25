# secrev_b2_recon.ps1 -- READ-ONLY recon: item 0 checks + Batch 2 pre-checks + drift. No sudo/write/restart.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$OutputEncoding=New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false
$enc=New-Object System.Text.UTF8Encoding $false
$hostt='azureuser@trading.jacksumner.com'
function Redact-All([string]$t){
  $simple=@(@{t='anthropic_key';re='sk-ant-[A-Za-z0-9_\-]{12,}'},@{t='openai_key';re='sk-(proj-)?[A-Za-z0-9]{32,}'},@{t='xai_key';re='xai-[A-Za-z0-9]{16,}'},@{t='jwt';re='eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}'},@{t='bearer';re='(?i)bearer\s+[A-Za-z0-9._\-]{20,}'},@{t='account';re='\b(116637293063|680725082|934310442|461391328)\b'},@{t='wallet';re='0x[a-fA-F0-9]{40}\b'})
  foreach($p in $simple){ $t=[regex]::Replace($t,$p.re,('[REDACTED:'+$p.t+']')) }; return $t
}
$shBody=@'
set -u
ROOT=/home/azureuser/trading_corp
TCDB="file:$ROOT/data/trading_corp.db?mode=ro"
PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
echo "===CONNECTIVITY==="
echo "[DNS]"; for h in api.github.com robinhood.com fapi.bitunix.com api.elections.kalshi.com; do printf "  %s -> " "$h"; getent hosts "$h" 2>/dev/null | head -1 | awk '{print $1}' || echo "FAIL"; done
echo "[HTTPS outbound status]"; for u in https://api.github.com https://files.pythonhosted.org; do printf "  %s -> " "$u"; curl -sS -o /dev/null -w '%{http_code}\n' --max-time 8 "$u" 2>&1; done
echo "===TRADING_CORP_JOURNAL_SINCE_BATCH3==="
SINCE="2026-11-25 00:46:00"
echo "[window since $SINCE]"
echo "  total lines: $(journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null | wc -l)"
echo "  connection/websocket/reconnect/timeout/error counts:"
for pat in 'connection' 'websocket' 'reconnect' 'timeout' 'ERROR' 'Traceback' 'refused' 'unreachable'; do
  c=$(journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null | grep -icE "$pat"); echo "    $pat = $c"
done
echo "  sample error/exception lines (up to 6):"
journalctl -u trading-corp --since "$SINCE" --no-pager 2>/dev/null | grep -iE 'ERROR|Traceback|connection refused|unreachable|websocket.*clos' | tail -6 | sed 's/^/    /'
echo "===JOURNAL_RETENTION==="
echo "  usage: $(journalctl --disk-usage 2>&1)"
echo "  oldest: $(journalctl -o short-iso --no-pager 2>/dev/null | head -1 | awk '{print $1" "$2}')"
echo "  newest: $(journalctl -o short-iso --no-pager 2>/dev/null | tail -1 | awk '{print $1" "$2}')"
echo "  boots: $(journalctl --list-boots --no-pager 2>/dev/null | wc -l)"
echo "===MAIN_PY_DRIFT==="
echo "  box main.py md5(CR-stripped): $(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)  (expect f486de42afc540b3a0cbdebe344fb13d == prod-live 50aea132)"
echo "===STARLETTE_VENV==="
"$ROOT/venv/bin/pip" show starlette 2>/dev/null | grep -iE '^(Name|Version):' | sed 's/^/  /'
"$ROOT/venv/bin/pip" show fastapi 2>/dev/null | grep -iE '^Version:' | sed 's/^/  fastapi /'
echo "  require-hashes in deploy? (grep runbook/unit):"
grep -rhiE 'require-hashes|requirements\.(lock|txt)' "$ROOT/runbooks" /etc/systemd/system/trading-corp.service 2>/dev/null | grep -iE 'require-hashes|pip install' | head -4 | sed 's/^/    /' || echo "    (no explicit hint found)"
echo "===NONLOCAL_8000==="
echo "[crontab :8000]"; crontab -l 2>/dev/null | grep -n '8000' | sed 's/^/  /' || echo "  (none)"
echo "[systemd units :8000]"; for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder; do systemctl cat "$u" 2>/dev/null | grep -niE ':8000|0\.0\.0\.0' | sed "s/^/  $u: /"; done
echo "[non-repo scripts/home :8000 non-localhost refs]"; grep -rnoE '([0-9]{1,3}(\.[0-9]{1,3}){3}|[a-z.]+):8000' /home/azureuser --include=*.sh --include=*.py --include=*.conf 2>/dev/null | grep -vE '127\.0\.0\.1:8000|localhost:8000' | grep -v "$ROOT/" | head -10 | sed 's/^/  /' || echo "  (none non-localhost outside repo)"
echo "===POSITIONS_ACTIVITY_BASELINE==="
echo "  mace open rungs: $(sqlite3 "$TCDB" "SELECT COUNT(*) FROM mace_rung WHERE status='open';" 2>&1)"
echo "  pm open positions: $(sqlite3 "$PMDB" "SELECT COUNT(*) FROM pm_open_position;" 2>&1)"
echo "  pm active subdivisions: $(sqlite3 "$PMDB" "SELECT COUNT(*) FROM pm_subdivision WHERE active=1;" 2>&1)"
echo "  agent_state arm rows by agent (live divisions):"
sqlite3 "$TCDB" "SELECT '    '||agent||'='||COUNT(*) FROM agent_state GROUP BY agent HAVING agent IN ('robinhood_mace','robinhood_pead','pmcc_robinhood','bitunix_sfp','bitunix_futures','kalshi_copy_trader','pm_live');" 2>&1
echo "  newest audit_event ts: $(sqlite3 "$TCDB" "SELECT COALESCE(MAX(created_ts),MAX(ts),'?') FROM audit_event;" 2>&1 | head -1)"
echo "  newest audit_event age: $(sqlite3 "$TCDB" "SELECT CAST((julianday('now')-julianday(MAX(created_ts)))*86400 AS INT)||'s ago' FROM audit_event;" 2>&1 | head -1)"
echo "===CADDY_UPSTREAM==="
CF=""
for f in /etc/caddy/Caddyfile $(ls -t /home/azureuser/secrev_batch1_backup_*/Caddyfile.bak 2>/dev/null | head -1); do
  if [ -r "$f" ]; then CF="$f"; break; fi
done
if [ -n "$CF" ]; then
  echo "  [readable: $CF] -- lines mentioning 8000 / reverse_proxy to the engine:"
  grep -niE 'reverse_proxy.*8000|:8000|localhost:8000|127\.0\.0\.1:8000|10\.0\.0\.[0-9]+:8000' "$CF" 2>/dev/null | head -8 | sed 's/^/    /'
else
  echo "  (Caddyfile not readable as azureuser; post-restart dashboard-302 via Caddy is the functional test)"
fi
echo "===END==="
'@
$sh=Join-Path $env:TEMP 'secrev_b2_recon.sh'
[IO.File]::WriteAllText($sh,$shBody,$enc)
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Output "Running READ-ONLY Batch 2 recon ..."
$res=Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
Remove-Item $sh -ErrorAction SilentlyContinue
if(-not $res){ Write-Output "ERROR: no output."; exit 1 }
$clean=Redact-All (($res -join "`n"))
$out=Join-Path $OutDir '20_b2_recon.txt'
[IO.File]::WriteAllText($out,$clean,$enc)
Write-Output ("Wrote: "+$out+"  end="+$(if($clean -match '===END==='){'ok'}else{'TRUNC'}))
