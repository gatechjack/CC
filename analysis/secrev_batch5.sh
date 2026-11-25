#!/usr/bin/env bash
# secrev_batch5.sh -- BATCH 5 non-Pro parts (runs as ROOT via az vm run-command). Jack-approved.
# (1) xrdp: stop+disable+mask (NOT purge). (2) journald: persistent SystemMaxUse cap + vacuum.
# Touches NO trading service. No reboot, no engine restart. Does NOT delete pm_* backups (Jack rules on those).
set -u
exec > >(tee /home/azureuser/secrev_batch5_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
echo "=== BATCH5 START $TS host=$(hostname) ==="
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"

echo "[before] protected PIDs:"
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done

# ---------- xrdp: stop + disable + mask (do NOT purge) ----------
echo "[xrdp] before: xrdp active=$(systemctl is-active xrdp 2>&1) enabled=$(systemctl is-enabled xrdp 2>&1); sesman active=$(systemctl is-active xrdp-sesman 2>&1)"
systemctl stop xrdp xrdp-sesman 2>&1 | sed 's/^/  stop: /'
systemctl disable xrdp xrdp-sesman 2>&1 | sed 's/^/  disable: /'
systemctl mask xrdp xrdp-sesman 2>&1 | sed 's/^/  mask: /'
echo "[xrdp] after: xrdp active=$(systemctl is-active xrdp 2>&1) enabled=$(systemctl is-enabled xrdp 2>&1); sesman active=$(systemctl is-active xrdp-sesman 2>&1) enabled=$(systemctl is-enabled xrdp-sesman 2>&1)"
echo "[xrdp] listeners on 3389/3350 (expect NONE):"
L=$(ss -tlnH 2>/dev/null | grep -E ':3389|:3350'); if [ -n "$L" ]; then echo "$L" | sed 's/^/  STILL-LISTENING: /'; else echo "  (none -- 3389/3350 closed)"; fi

# ---------- journald: persistent cap + vacuum ----------
echo "[journald] usage before: $(journalctl --disk-usage 2>&1)"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/00-secrev-maxuse.conf <<'EOF'
# secrev 2026-11-24: bound journald so it cannot regrow to multi-GB on this live-trading box.
[Journal]
SystemMaxUse=1G
SystemKeepFree=2G
SystemMaxFileSize=100M
EOF
echo "[journald] drop-in /etc/systemd/journald.conf.d/00-secrev-maxuse.conf:"
sed 's/^/    /' /etc/systemd/journald.conf.d/00-secrev-maxuse.conf
systemctl restart systemd-journald 2>&1 | sed 's/^/  journald-restart: /'
journalctl --vacuum-size=1G 2>&1 | tail -3 | sed 's/^/  vacuum: /'
echo "[journald] usage after: $(journalctl --disk-usage 2>&1)"

echo "[after] protected PIDs (compare to before -- MUST be unchanged):"
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done
echo "[after] df /: $(df -h / | tail -1)"
echo "=== BATCH5 DONE $TS ==="
