#!/usr/bin/env bash
# secrev_batch1_rollback.sh -- BATCH 1 ROLLBACK (runs as ROOT via az vm run-command). Run only if needed.
# Restores Caddy (previous deb version + Caddyfile) and Authelia (previous binary) from the newest
# secrev_batch1_backup_* dir (or pass a specific dir as $1). Does NOT restart trading-corp.
set -u
# Rollback target: an explicit $1, else the PINNED ORIGINAL (first backup of the night = pre-change
# 2.11.4/4.39.19 state), else the OLDEST backup dir. NEVER "newest" (which could be a partial-upgrade state).
BK=${1:-}
PIN=/home/azureuser/secrev_batch1_ORIGINAL
if [ -z "$BK" ]; then
  if [ -f "$PIN" ] && [ -d "$(cat "$PIN")" ]; then BK=$(cat "$PIN"); echo "[rollback] using PINNED ORIGINAL backup";
  else BK=$(ls -dtr /home/azureuser/secrev_batch1_backup_* 2>/dev/null | head -1); echo "[rollback] no valid pin; using OLDEST backup dir"; fi
fi
echo "=== BATCH1 ROLLBACK from $BK ==="
if [ -z "$BK" ] || [ ! -d "$BK" ]; then echo "no backup dir found -- cannot roll back"; exit 1; fi

PV=$(cat "$BK/caddy.deb.version" 2>/dev/null)
if [ -n "$PV" ]; then
  echo "[caddy] downgrade to $PV"
  apt-get install -y --allow-downgrades "caddy=$PV" 2>&1 | tail -4
fi
if [ -f "$BK/Caddyfile.bak" ]; then cp -a "$BK/Caddyfile.bak" /etc/caddy/Caddyfile; fi
if systemctl reload caddy 2>/dev/null; then echo "[caddy] reloaded"; else systemctl restart caddy; fi

if [ -f "$BK/authelia.bin.bak" ]; then
  echo "[authelia] restore previous binary"
  systemctl stop authelia
  cp -a "$BK/authelia.bin.bak" /usr/local/bin/authelia
  chmod 755 /usr/local/bin/authelia
  systemctl start authelia
  sleep 3
fi
echo "[post] caddy=$(/usr/bin/caddy version 2>/dev/null | head -1) service=$(systemctl is-active caddy)"
echo "[post] authelia=$(/usr/local/bin/authelia --version 2>/dev/null | head -1) service=$(systemctl is-active authelia)"
echo "=== ROLLBACK DONE (authelia config backup available at $BK/authelia.configuration.yml.bak if needed) ==="
