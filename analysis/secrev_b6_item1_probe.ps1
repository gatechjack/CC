# secrev_b6_item1_probe.ps1 -- Batch 6 item 1 (URGENT): auto-update exposure. READ-ONLY, no root.
# unattended-upgrades origins (ESM?) + Automatic-Reboot + blacklist; apt timers next run; needrestart unattended mode.
param([string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false; [Console]::OutputEncoding=$enc; $OutputEncoding=$enc
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log=New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
$sh=@'
set -u
echo "=====A. 20auto-upgrades (periodic enabled?)====="
grep -vE '^\s*//' /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null | grep -E '.' || echo "  (file absent)"
echo "=====B. 50unattended-upgrades: Allowed-Origins (uncommented) -- ESM included?====="
awk '/Allowed-Origins|Origins-Pattern/{f=1} f{print} /\};/{if(f)exit}' /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null | grep -vE '^\s*//' | grep -E '.' | sed 's/^/  /'
echo "  -- explicit ESM origin refs (non-comment):"
grep -nE 'ESMApps|ESM:|esm-apps|esm-infra|apps-security|infra-security' /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null | grep -vE ':\s*//' | sed 's/^/    /' || echo "    (none)"
echo "=====C. 50unattended-upgrades: Automatic-Reboot + Blacklist + Minimal-Steps (uncommented)====="
grep -nE 'Automatic-Reboot|Package-Blacklist|Minimal-Steps' /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null | grep -vE ':\s*//' | sed 's/^/  /' || echo "  (all commented/default)"
echo "  -- blacklist block contents (uncommented):"
awk '/Package-Blacklist/{f=1} f{print} /\};/{if(f)exit}' /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null | grep -vE '^\s*//' | grep -E '"' | sed 's/^/    /' || echo "    (empty)"
echo "=====D. conf.d overrides (unattended/esm/pro)====="
ls /etc/apt/apt.conf.d/ 2>/dev/null | grep -iE 'unattended|auto-upgrade|esm|pro|needrestart' | sed 's/^/  /'
echo "=====E. apt timers -- next run====="
systemctl list-timers apt-daily.timer apt-daily-upgrade.timer --all --no-pager 2>/dev/null | sed 's/^/  /'
echo "  now: UTC=$(date -u +%F' '%H:%M:%S)Z  ET=$(TZ=America/New_York date +%F' '%H:%M' '%Z)"
for t in apt-daily apt-daily-upgrade; do
  n=$(systemctl show $t.timer -p NextElapseUSecRealtime --value 2>/dev/null)
  echo "  $t.timer active=$(systemctl is-active $t.timer) NextElapseRealtime=$n"
done
echo "=====F. needrestart unattended mode====="
echo "  /etc/needrestart/needrestart.conf \$nrconf{restart}:"
grep -nE 'nrconf\{restart\}' /etc/needrestart/needrestart.conf 2>/dev/null | sed 's/^/    /' || echo "    (not set -> package default)"
echo "  conf.d overrides:"
for f in /etc/needrestart/conf.d/*; do [ -r "$f" ] && { echo "    --- $f ---"; grep -nE 'nrconf\{restart\}|\$nrconf' "$f" 2>/dev/null | sed 's/^/      /'; }; done
echo "  apt hook /etc/apt/apt.conf.d/99needrestart:"; grep -vE '^\s*//' /etc/apt/apt.conf.d/99needrestart 2>/dev/null | grep -E '.' | sed 's/^/    /' || echo "    (absent)"
echo "  versions: $(dpkg -l needrestart unattended-upgrades 2>/dev/null | awk '/^ii/{print $2"="$3}' | tr '\n' ' ')"
echo "=====G. u-u needrestart integration + last run result====="
grep -nE 'needrestart|_restart|MinimalSteps' /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null | grep -vE ':\s*//' | sed 's/^/  /' || echo "  (no explicit needrestart line in u-u -> uses system needrestart default)"
echo "  last unattended-upgrades runs (headers only):"; grep -hE 'Starting unattended upgrades|Packages that will be upgraded|Allowed origins are' /var/log/unattended-upgrades/unattended-upgrades.log 2>/dev/null | tail -4 | sed 's/^/    /' || echo "    (no log yet)"
echo "=====H. is a reboot currently required?====="
[ -f /var/run/reboot-required ] && echo "  REBOOT-REQUIRED present: $(cat /var/run/reboot-required 2>/dev/null)" || echo "  no reboot-required flag"
[ -f /var/run/reboot-required.pkgs ] && { echo "  pkgs:"; sed 's/^/    /' /var/run/reboot-required.pkgs 2>/dev/null; }
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=accept-new azureuser@trading.jacksumner.com "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ L ($line.TrimEnd()) }
[IO.File]::WriteAllText((Join-Path $OutDir '37_b6_item1_autoupdate.txt'),(($log -join "`n")+"`n"),$enc)
Write-Output "--- wrote 37_b6_item1_autoupdate.txt ---"
