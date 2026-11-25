#!/usr/bin/env bash
# secrev_batch4_deploy.sh -- BATCH 4 (runs as ROOT via az vm run-command). Jack-approved. ONLY after Batch 2 green.
# Upgrade kernel (6.8.0-1074) + activate pending libc6, then REBOOT. Backgrounds the reboot so output is captured.
set -u
exec > >(tee /home/azureuser/secrev_batch4_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
echo "=== BATCH4 START $TS host=$(hostname) ==="

# pre-reboot QUIET gate (no real PM order in last 60s)
PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
LASTORD=$(sqlite3 "$PMDB" "SELECT CAST((strftime('%s','now') - MAX(submitted_ts)) AS INT) FROM pm_subdivision_order WHERE dry_run=0;" 2>/dev/null)
echo "[quiet] seconds since last real PM order = ${LASTORD:-unknown}"
if [ -n "$LASTORD" ] && [ "$LASTORD" -ge 0 ] 2>/dev/null && [ "$LASTORD" -lt 60 ] 2>/dev/null; then echo "[ABORT] order <60s ago -- not quiet; re-run in ~1 min (nothing upgraded)"; exit 3; fi

# pre-reboot state snapshot (for the after-boot verify to compare)
echo "[pre] running kernel: $(uname -r)"
echo "[pre] installed linux-image-azure: $(dpkg-query -W -f='${Version}' linux-image-azure 2>/dev/null)"
echo "[pre] active units:"; for u in trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia chrony; do echo "    $u=$(systemctl is-active $u 2>/dev/null)"; done
echo "[pre] crontab lines: $(crontab -l 2>/dev/null | grep -cE '^[^#[:space:]]')"
echo "[pre] reboot-required: $([ -f /var/run/reboot-required ] && echo yes || echo no)"

# upgrade kernel meta + images/tools + libc6 (libc6 likely already installed -> activated by the reboot)
echo "[apt] upgrading kernel (1074) + libc6 (NEEDRESTART_MODE=l) ..."
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y --only-upgrade --no-install-recommends -o Dpkg::Options::=--force-confold linux-azure linux-image-azure linux-headers-azure linux-tools-azure linux-cloud-tools-azure libc6 libc-bin 2>&1 | tail -20
RC=${PIPESTATUS[0]}
echo "[apt] exit=$RC; installed linux-image-azure now: $(dpkg-query -W -f='${Version}' linux-image-azure 2>/dev/null); libc6: $(dpkg-query -W -f='${Version}' libc6 2>/dev/null)"
echo "[boot] installed kernels on disk:"; ls -1 /boot/vmlinuz-* 2>/dev/null | sed 's/^/    /'
if [ "$RC" != "0" ]; then echo "[ABORT] apt failed -- NOT rebooting"; exit 4; fi

echo "[reboot] scheduling reboot via systemd transient timer (survives run-command exit) ..."
systemctl stop secrev-batch4-reboot.timer secrev-batch4-reboot.service 2>/dev/null || true
systemd-run --on-active=20 --unit=secrev-batch4-reboot --timer-property=AccuracySec=1s systemctl reboot 2>&1 | sed 's/^/    /'
sleep 1
echo "[reboot] timer active=$(systemctl is-active secrev-batch4-reboot.timer 2>/dev/null) (fires ~20s from now)"
echo "=== BATCH4 UPGRADE DONE $TS -- box reboots in ~20s via systemd timer; VERIFY after boot (uname 1074, units active, reboot-required cleared) ==="
