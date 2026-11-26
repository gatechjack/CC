# secrev_b6_item23_probe.ps1 -- Batch 6 items 2+3. READ-ONLY, no root. Livepatch + ESM update scope.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
echo "=====ITEM2. canonical-livepatch status====="
if command -v canonical-livepatch >/dev/null 2>&1; then canonical-livepatch status 2>&1 | sed 's/^/  /'; else echo "  canonical-livepatch not on PATH"; fi
echo "  -- pro livepatch (from status):"
pro status 2>/dev/null | grep -iE 'livepatch|kernel' | sed 's/^/  /'
echo "  -- running kernel: $(uname -r)"
echo "=====ITEM3a. pro security-status (ESM coverage)====="
pro security-status 2>/dev/null | sed 's/^/  /' | head -25
echo "=====ITEM3b. apt list --upgradable (current lists; origin via suite)====="
apt list --upgradable 2>/dev/null | grep -vE '^Listing' | sed 's/^/  /'
echo "  -- count: $(apt list --upgradable 2>/dev/null | grep -vcE '^Listing')"
echo "=====ITEM3c. origin per upgradable pkg (esm-apps / esm-infra / standard)====="
for p in $(apt list --upgradable 2>/dev/null | grep -vE '^Listing' | cut -d/ -f1); do
  o=$(apt-cache policy "$p" 2>/dev/null | awk '/\*\*\*/{getline; print}' | grep -oE 'o=[^ ,]+|a=[^ ,]+' | tr '\n' ' ')
  cand=$(apt-cache policy "$p" 2>/dev/null | awk '/Candidate:/{print $2}')
  echo "  $p -> $cand  [$o]"
done
echo "=====ITEM3d. flagged-sensitive upgradable (python3.12/openssl/libssl/libc/kernel/caddy/authelia)====="
apt list --upgradable 2>/dev/null | grep -iE 'python3\.12|openssl|libssl|libc6|linux-image|linux-azure|linux-headers|caddy|authelia' | sed 's/^/  FLAG: /' || echo "  (none of the sensitive set upgradable)"
echo "=====ITEM3e. which running services load an upgradable lib (-> would need restart)====="
UPLIBS=$(apt list --upgradable 2>/dev/null | grep -vE '^Listing' | cut -d/ -f1 | grep -iE '^lib|^python3' | tr '\n' '|' | sed 's/|$//')
echo "  upgradable lib-ish pkgs: ${UPLIBS:-none}"
for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia; do
  pid=$(systemctl show $u -p MainPID --value 2>/dev/null)
  del=$(grep -cE '\(deleted\)' /proc/$pid/maps 2>/dev/null)
  echo "  $u pid=$pid deleted-mapped-now=$del start=$(systemctl show $u -p ExecMainStartTimestamp --value 2>/dev/null)"
done
echo "  -- needrestart batch list (list-only; what IT thinks needs restart now):"
needrestart -b 2>/dev/null | grep -iE 'NEEDRESTART-SVC|NEEDRESTART-KSTA|NEEDRESTART-KCUR|NEEDRESTART-KEXP' | sed 's/^/    /' | head -20 || echo "    (needrestart -b needs root or none)"
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=30 -o StrictHostKeyChecking=accept-new azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '38_b6_item23_esm_scope.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 38_b6_item23_esm_scope.txt ---"
