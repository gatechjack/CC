#!/usr/bin/env bash
# secrev_housekeeping.sh -- resume-note no-trading-impact items 1-3 (runs as ROOT via az vm run-command).
# (1) journald SystemMaxUse 1G->2G + restart. (2) pm_* Tier A delete (EXACT list, no wildcards, abort-on-newer).
# (3) reset-failed xrdp-sesman. Records protected-service PIDs/NRestarts before/after (must be unchanged).
# NO engine restart, NO reboot, NO snapshot deletion.
set -u
exec > >(tee /home/azureuser/secrev_housekeeping_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
echo "=== HOUSEKEEPING START $TS host=$(hostname) ==="
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"

echo "[before] protected services:"
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done
echo "[before] df /: $(df -h / | tail -1)"

# ---------- ITEM 1: journald SystemMaxUse 1G -> 2G ----------
CONF=/etc/systemd/journald.conf.d/00-secrev-maxuse.conf
echo "[journald] current: $(grep -h SystemMaxUse "$CONF" 2>/dev/null) ; usage=$(journalctl --disk-usage 2>&1)"
cat > "$CONF" <<'EOF'
# secrev: bound journald. 1G -> 2G (2026-11-25, Jack ruling 22:37) stopgap (~2 days at ~0.9 GB/day);
# 14-day target needs the PM log-volume cut first, then ~4G.
[Journal]
SystemMaxUse=2G
SystemKeepFree=2G
SystemMaxFileSize=100M
EOF
echo "[journald] new drop-in:"; sed 's/^/    /' "$CONF"
systemctl restart systemd-journald
sleep 2
echo "[journald] active=$(systemctl is-active systemd-journald) cap=$(grep -h SystemMaxUse "$CONF") usage=$(journalctl --disk-usage 2>&1)"

# ---------- ITEM 2: pm_* Tier A delete (exact list; abort if any newer than a kept rollback point) ----------
ROLLBACKS="/home/azureuser/pm_recovery_backup_20261007T032558Z.db /home/azureuser/pm_legaudit_clear_backup_20261008T000933Z.db /home/azureuser/pm_rollupfix_backup_20261011T002054Z.tar"
REF=""
for r in $ROLLBACKS; do if [ -e "$r" ]; then m=$(stat -c %Y "$r"); if [ -z "$REF" ] || [ "$m" -lt "$REF" ]; then REF=$m; fi; fi; done
if [ -n "$REF" ]; then echo "[pm-delete] oldest kept rollback REF epoch=$REF ($(date -u -d @"$REF" 2>/dev/null))"; else echo "[pm-delete] no rollback point found"; fi
TIER_A="/home/azureuser/pm_stage0_rung3_dbbackup_20260827T130737Z.db /home/azureuser/pm_stage1_rung1_dbbackup_20260827T162523Z.db /home/azureuser/pm_stage1_rung3_adj_dbbackup_20260827T173915Z.db /home/azureuser/pm_stage1_rung3_poll_dbbackup_20260827T172646Z.db /home/azureuser/pm_t1_rung2_poll_dbbackup_20260827T185228Z.db /home/azureuser/pm_rollup2_dbbackup_20260827T190346Z.db /home/azureuser/pm_caps_set_backup_20260830T180415Z.db /home/azureuser/pm_caps_write_backup_20260831T022238Z.db /home/azureuser/pm_mig014_backup_20260831T020430Z.db /home/azureuser/pm_reattach_backup_20260831T022934Z.db /home/azureuser/pm_rd_deploy_backup_20260831T160923Z.db /home/azureuser/pm_r5_caps_backup_20260831T214753Z.db"
ABORT=0; ELIGIBLE=""
echo "[pm-delete] PASS 1 validate (no deletion):"
if [ -z "$REF" ]; then echo "    ABORT: no rollback REF -- cannot safety-check"; ABORT=1; fi
for f in $TIER_A; do
  if [ ! -e "$f" ]; then echo "    MISSING (skip): $f"; continue; fi
  m=$(stat -c %Y "$f")
  if [ -n "$REF" ] && [ "$m" -ge "$REF" ]; then echo "    NEWER-THAN-ROLLBACK (ABORT ALL): $f ($(date -u -d @"$m" 2>/dev/null))"; ABORT=1; continue; fi
  echo "    eligible: $f ($(date -u -d @"$m" 2>/dev/null), $(stat -c %s "$f") B)"
  ELIGIBLE="$ELIGIBLE $f"
done
if [ "$ABORT" = "1" ]; then
  echo "[pm-delete] ABORTED -- deleting NOTHING (a listed item is newer than a rollback point, or no REF)."
else
  echo "[pm-delete] PASS 2 delete (eligible .db + sibling -shm/-wal only if present):"
  FREED=0
  for f in $ELIGIBLE; do
    for x in "$f" "$f-shm" "$f-wal"; do
      if [ -e "$x" ]; then s=$(stat -c %s "$x"); if rm -f "$x"; then FREED=$((FREED + s)); echo "    rm $x ($s B)"; fi; fi
    done
  done
  echo "[pm-delete] bytes freed = $FREED ($(awk "BEGIN{printf \"%.1f\", $FREED/1048576}") MB)"
fi

# ---------- ITEM 3: reset-failed xrdp-sesman ----------
echo "[xrdp] reset-failed xrdp-sesman ..."
systemctl reset-failed xrdp-sesman 2>&1 | sed 's/^/    /'
echo "[xrdp] xrdp active=$(systemctl is-active xrdp 2>&1) enabled=$(systemctl is-enabled xrdp 2>&1); sesman active=$(systemctl is-active xrdp-sesman 2>&1) enabled=$(systemctl is-enabled xrdp-sesman 2>&1)"
echo "[xrdp] 3389/3350 listener count (expect 0): $(ss -tlnH 2>/dev/null | grep -cE ':3389|:3350')"

# ---------- after ----------
echo "[after] protected services (MUST match before):"
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done
echo "[after] df /: $(df -h / | tail -1)"
echo "=== HOUSEKEEPING DONE $TS ==="
