# secrev_recon_batch345.ps1 -- READ-ONLY recon for item 0 (disk reclaim evidence) + Batch 3/5 planning.
# ssh-STDIN read-only probe + read-only az NSG show. No sudo/write/restart. Redacted. -> analysis\14_recon.txt
param([string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
$hostt = 'azureuser@trading.jacksumner.com'
function Redact-All([string]$t){
  $hits=@{}; $simple=@(
    @{t='anthropic_key';re='sk-ant-[A-Za-z0-9_\-]{12,}'},@{t='openai_key';re='sk-(proj-)?[A-Za-z0-9]{32,}'},
    @{t='xai_key';re='xai-[A-Za-z0-9]{16,}'},@{t='jwt';re='eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}'},
    @{t='bearer';re='(?i)bearer\s+[A-Za-z0-9._\-]{20,}'},@{t='conn';re='(?i)(postgres(ql)?|mysql|mongodb|redis|amqp)://[^:/\s]+:[^@/\s]+@'},
    @{t='account';re='\b(116637293063|680725082|934310442|461391328)\b'},@{t='wallet';re='0x[a-fA-F0-9]{40}\b'})
  foreach($p in $simple){ $mm=[regex]::Matches($t,$p.re); if($mm.Count){ $hits[$p.t]=([int]$hits[$p.t])+$mm.Count; $t=[regex]::Replace($t,$p.re,('[REDACTED:'+$p.t+']')) } }
  return [pscustomobject]@{ Text=$t; Hits=$hits }
}
$shBody = @'
set -u
echo "===ITEM0_DISK_RECLAIM_EVIDENCE==="
echo "[df / now]"; df -h / | tail -1
echo "[unattended-upgrades log mtimes]"; ls -la --time-style=long-iso /var/log/unattended-upgrades/ 2>/dev/null | sed 's/^/  /'
echo "[u-u dpkg.log clean/autoclean/remove lines]"; grep -hiE 'clean|remov' /var/log/unattended-upgrades/unattended-upgrades-dpkg.log 2>/dev/null | head -12 | sed 's/^/  /'
echo "[APT Periodic autoclean config]"; grep -rhiE 'AutocleanInterval|Unattended-Upgrade |Download-Upgradeable|Update-Package-Lists' /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/*periodic* 2>/dev/null | sed 's/^/  /'
echo "[apt-daily timers last/next]"; systemctl list-timers 'apt-daily*' --all --no-pager --no-legend 2>/dev/null | sed 's/^/  /'
echo "[apt history clean/autoremove w/ dates]"; zgrep -hE 'Start-Date:|Commandline:.*(clean|autoremove)|Remove:' /var/log/apt/history.log* 2>/dev/null | grep -iE -A1 -B1 'clean|autoremove|remove:' 2>/dev/null | head -14 | sed 's/^/  /'
echo "[apt archive cache now]"; du -sh /var/cache/apt/archives 2>/dev/null | sed 's/^/  /'
echo "[journald usage + cap config]"; journalctl --disk-usage 2>&1 | sed 's/^/  /'; grep -rhiE 'SystemMaxUse|MaxRetentionSec' /etc/systemd/journald.conf /etc/systemd/journald.conf.d/ 2>/dev/null | grep -v '^#' | sed 's/^/  cfg: /' || echo "  (no explicit SystemMaxUse -> default = 10% of fs ~6G)"
echo "[journald vacuum/rotate events since 11-20]"; journalctl -u systemd-journald --since '2026-11-20' --no-pager 2>/dev/null | grep -iE 'vacuum|rotat|time spent|suspend' | tail -6 | sed 's/^/  /'
echo "===BATCH3_UPGRADABLE==="
apt list --upgradable 2>/dev/null
echo "===SECURITY_STATUS==="
pro security-status 2>&1 | grep -iE 'security update|esm-apps|esm-infra|receiving|not attached|packages' | head -8 | sed 's/^/  /'
echo "===NEEDRESTART==="
dpkg -l needrestart 2>/dev/null | awk '/^ii/{print "  "$2" "$3}'
grep -hE 'nrconf\{restart\}|nrconf\{kernelhints\}' /etc/needrestart/needrestart.conf 2>/dev/null | grep -v '^#' | sed 's/^/  /' || echo "  (needrestart default restart mode)"
echo "===SERVICE_PID_BASELINE==="
for u in trading-corp sfp-card-watcher caddy authelia; do
  echo "--- $u ---"; systemctl show "$u" -p Id,ActiveState,SubState,MainPID,NRestarts,ExecMainStartTimestamp 2>/dev/null | sed 's/^/  /'
done
echo "[pm/prediction web service units + what serves 8081/8000]"
systemctl list-units --type=service --all --no-legend --no-pager 2>/dev/null | grep -iE 'predict|pm[-_]?web|market|stream' | sed 's/^/  unit: /' || echo "  (no pm-web unit)"
ss -tlnH 2>/dev/null | grep -E ':8000|:8081' | sed 's/^/  listen: /'
echo "===PM_BACKUPS==="
ls -lda --time-style=long-iso /home/azureuser/pm_*backup* 2>/dev/null | awk '{print "  "$6" "$7"  "$5"B  "$NF}'
echo "[sizes]"; du -sh /home/azureuser/pm_*backup* 2>/dev/null | sort -rh | sed 's/^/  /'
echo "[total]"; du -ch /home/azureuser/pm_*backup* 2>/dev/null | tail -1 | sed 's/^/  /'
echo "===XRDP_STATE==="
echo "  is-active=$(systemctl is-active xrdp 2>&1) is-enabled=$(systemctl is-enabled xrdp 2>&1)"
echo "  sesman is-active=$(systemctl is-active xrdp-sesman 2>&1)"
ss -tlnH 2>/dev/null | grep -E ':3389|:3350' | sed 's/^/  listen: /' || echo "  (nothing on 3389/3350)"
echo "===END==="
'@
$sh = Join-Path $env:TEMP 'secrev_recon_b345.sh'
[IO.File]::WriteAllText($sh, $shBody, $enc)
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Output "Running READ-ONLY recon against $hostt ..."
$res = Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
Remove-Item $sh -ErrorAction SilentlyContinue
if(-not $res){ Write-Output "ERROR: no ssh output."; exit 1 }

# read-only az: the two inbound SSH NSG rules, side by side
$nsgJson = az network nsg show --ids "/subscriptions/6f20f2e1-28ec-4857-857c-457c7f5212ca/resourceGroups/rg-shared-prod/providers/Microsoft.Network/networkSecurityGroups/tc-prod-nsg" --query "securityRules[?direction=='Inbound'].{name:name,prio:priority,access:access,proto:protocol,src:sourceAddressPrefix,dport:destinationPortRange}" -o json --only-show-errors 2>$null | Out-String

$combined = (($res -join "`n") + "`n===NSG_INBOUND_RULES===`n" + $nsgJson)
$r = Redact-All $combined
$out = Join-Path $OutDir '14_recon.txt'
[IO.File]::WriteAllText($out, $r.Text, $enc)
if($r.Hits.Count){ Write-Output ("Redaction: " + (($r.Hits.GetEnumerator()|%{"$($_.Name)=$($_.Value)"}) -join ' ')) } else { Write-Output "Redaction: none" }
Write-Output ("Wrote: " + $out + "  end=" + $(if($r.Text -match '===END==='){'ok'}else{'TRUNC'}))
