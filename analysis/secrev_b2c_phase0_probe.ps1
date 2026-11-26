# secrev_b2c_phase0_probe.ps1 -- Batch 2c PHASE 0a+0b. READ-ONLY (no root, no writes to box).
#   0a: pm-web/sfp/mcr ExecStart venv + MainPID start vs 2b install (00:26Z) + loaded-lib evidence
#       (/proc/pid/maps .so for pillow; dist-info mtime vs start for pure-python pyjwt/urllib3/starlette)
#   0b: diff 2b backup pip_freeze.pre.txt vs current box freeze (expect only pyjwt/urllib3/pillow)
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding=$enc; $OutputEncoding=$enc
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
ROOT=/home/azureuser/trading_corp
VENV="$ROOT/venv/bin"
SP=$("$VENV/python" -c "import site;print(site.getsitepackages()[0])" 2>/dev/null)
echo "=====0a SERVICES (loaded-lib state)====="
echo "site-packages=$SP"
echo "-- current on-disk dist-info mtimes (when each CURRENT version was installed) --"
for p in pyjwt urllib3 pillow starlette aiohttp yarl fastapi cryptography msal; do
  di=$(ls -d "$SP"/${p}-*.dist-info "$SP"/$(echo $p|sed 's/./\U&/')-*.dist-info 2>/dev/null | head -1)
  ver=$(basename "$di" 2>/dev/null | sed -E 's/^[^-]+-([^-]+)\.dist-info/\1/')
  mt=$(stat -c '%y' "$di" 2>/dev/null | cut -d. -f1)
  echo "   $p==$ver installed_at=$mt"
done
for u in prediction-markets-web sfp-card-watcher market-context-recorder trading-corp; do
  pid=$(systemctl show $u -p MainPID --value 2>/dev/null)
  start=$(systemctl show $u -p ExecMainStartTimestamp --value 2>/dev/null)
  nrr=$(systemctl show $u -p NRestarts --value 2>/dev/null)
  venvp=$(systemctl show $u -p ExecStart --value 2>/dev/null | grep -oE '/home/azureuser/trading_corp/venv/bin/python[0-9.]*' | head -1)
  echo "### $u pid=$pid NRestarts=$nrr start=$start"
  echo "    venv=$venvp"
  if [ -r /proc/$pid/maps ]; then
    echo "    maps: readable; compiled libs loaded (pillow/crypto/aiohttp/yarl .so):"
    grep -oE '/home/azureuser/trading_corp/venv/[^ ]+\.(so[.0-9]*)( \(deleted\))?' /proc/$pid/maps 2>/dev/null \
      | grep -iE '_imaging|pil|cryptograph|rust|aiohttp|_http_parser|yarl|_quoting|multidict|frozenlist' | sort -u | sed 's/^/      /'
    dc=$(grep -cE '/home/azureuser/trading_corp/venv/.*\(deleted\)' /proc/$pid/maps 2>/dev/null)
    echo "      venv-files-mapped-but-deleted-on-disk: ${dc:-0}"
  else
    echo "    maps: NOT readable as azureuser (process not owned by azureuser) -> use start-vs-install inference"
  fi
done
echo "2b_install_ref: pyjwt/urllib3/pillow replaced 2026-11-26 ~00:26Z. A service whose start < its dist-info mtime runs the OLD version in memory."
echo "=====0b FREEZE DIFF (2b backup pre vs now)====="
BK=$(ls -d /home/azureuser/secrev_batch2b_backup_*/ 2>/dev/null | tail -1)
echo "2b_backup_dir=$BK"
if [ -r "${BK}pip_freeze.pre.txt" ]; then
  "$VENV/pip" freeze 2>/dev/null | sort > /tmp/b2c_nowfreeze.txt
  echo "pre_count=$(wc -l < "${BK}pip_freeze.pre.txt")  now_count=$(wc -l < /tmp/b2c_nowfreeze.txt)"
  echo "--- diff (< = 2b-pre value, > = now) ---"
  diff <(sort "${BK}pip_freeze.pre.txt") /tmp/b2c_nowfreeze.txt | grep -E '^[<>]' | sed 's/^/   /'
  rm -f /tmp/b2c_nowfreeze.txt
else
  echo "   NO readable 2b backup pip_freeze.pre.txt"
fi
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '33_b2c_phase0_probe.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 33_b2c_phase0_probe.txt ---"
