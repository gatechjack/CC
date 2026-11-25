# secrev_housekeeping_verify.ps1 -- post-run READ-ONLY verify. No writes.
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
echo "[journald] cap=$(grep -h SystemMaxUse /etc/systemd/journald.conf.d/00-secrev-maxuse.conf 2>/dev/null) active=$(systemctl is-active systemd-journald) usage=$(journalctl --disk-usage 2>&1)"
echo "[pm-delete] Tier A present-count (expect 0 of 12):"
N=0
for f in \
 /home/azureuser/pm_stage0_rung3_dbbackup_20260827T130737Z.db \
 /home/azureuser/pm_stage1_rung1_dbbackup_20260827T162523Z.db \
 /home/azureuser/pm_stage1_rung3_adj_dbbackup_20260827T173915Z.db \
 /home/azureuser/pm_stage1_rung3_poll_dbbackup_20260827T172646Z.db \
 /home/azureuser/pm_t1_rung2_poll_dbbackup_20260827T185228Z.db \
 /home/azureuser/pm_rollup2_dbbackup_20260827T190346Z.db \
 /home/azureuser/pm_caps_set_backup_20260830T180415Z.db \
 /home/azureuser/pm_caps_write_backup_20260831T022238Z.db \
 /home/azureuser/pm_mig014_backup_20260831T020430Z.db \
 /home/azureuser/pm_reattach_backup_20260831T022934Z.db \
 /home/azureuser/pm_rd_deploy_backup_20260831T160923Z.db \
 /home/azureuser/pm_r5_caps_backup_20260831T214753Z.db ; do
  if [ -e "$f" ] || [ -e "$f-shm" ] || [ -e "$f-wal" ]; then N=$((N+1)); echo "    STILL PRESENT: $f"; fi
done
echo "    present=$N (0 = all deleted)"
echo "[pm-delete] kept rollback points still present:"
for r in /home/azureuser/pm_recovery_backup_20261007T032558Z.db /home/azureuser/pm_legaudit_clear_backup_20261008T000933Z.db /home/azureuser/pm_rollupfix_backup_20261011T002054Z.tar; do echo "    $([ -e "$r" ] && echo PRESENT || echo MISSING): $r"; done
echo "[xrdp] xrdp active=$(systemctl is-active xrdp 2>&1) enabled=$(systemctl is-enabled xrdp 2>&1); sesman active=$(systemctl is-active xrdp-sesman 2>&1) enabled=$(systemctl is-enabled xrdp-sesman 2>&1); failed-units=$(systemctl --failed --no-legend 2>/dev/null | grep -c xrdp)"
echo "[xrdp] 3389/3350 listeners (expect 0): $(ss -tlnH 2>/dev/null | grep -cE ':3389|:3350')"
echo "[protected services] (compare to before):"
for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia; do echo "    $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done
echo "[authelia health]=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:9091/api/health 2>/dev/null)"
echo "[df /]=$(df -h / | tail -1)"
'@
$res=$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $hostt "tr -d '\r\357\273\277'|bash"
foreach($line in ($res -split "`n")){ if($line.Trim()){ L ("  "+$line.Trim()) } }
[IO.File]::WriteAllText((Join-Path $OutDir '24_housekeeping_verify.txt'),(($log -join "`n")+"`n"),$enc)
