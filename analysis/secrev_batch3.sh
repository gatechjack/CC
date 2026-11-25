#!/usr/bin/env bash
# secrev_batch3.sh -- BATCH 3 (runs as ROOT via az vm run-command). Jack-approved.
# OS security debs, NO reboot, NO engine restart. EXCLUDES kernel, libc6, python3.12* (deadsnakes), xrdp.
# needrestart forced LIST-ONLY so nothing auto-restarts; only chrony/snapd may be restarted (safe allowlist).
set -u
exec > >(tee /home/azureuser/secrev_batch3_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
echo "=== BATCH3 START $TS host=$(hostname) ==="

# Explicit allowlist from the box upgradable/security list (recon 2026-11-24). NO kernel/libc6/python3.12*/xrdp.
PKGS="chrony distro-info iproute2 libaudit-common libaudit1 libglib2.0-0 libglib2.0-bin libglib2.0-data libgssapi-krb5-2 libk5crypto3 libkrb5-3 libkrb5support0 libnetplan0 libopeniscsiusr nano netplan-generator netplan.io open-iscsi open-vm-tools python3-distro-info python3-distupgrade python3-netplan snapd tmux ubuntu-release-upgrader-core xfsprogs"
SAFE_RESTART="chrony snapd"   # the ONLY services this script is allowed to restart
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"

# Defensive guard: refuse if any excluded class slipped into PKGS.
if echo "$PKGS" | tr ' ' '\n' | grep -qE '^(linux-|libc6$|python3\.12|libpython3\.12|xrdp)'; then
  echo "[abort] excluded package class present in PKGS"; exit 2
fi

echo "[before] PROTECTED services (MUST be unchanged after):"
for u in $PROTECTED; do
  echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"
done
echo "[before] target versions:"
for p in $PKGS; do echo "  $p=$(dpkg-query -W -f='${Version}' $p 2>/dev/null)"; done

echo "[apt] install --only-upgrade (DEBIAN_FRONTEND=noninteractive, NEEDRESTART_MODE=l, confold) ..."
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y --only-upgrade --no-install-recommends -o Dpkg::Options::=--force-confold $PKGS 2>&1 | tail -50
RC=${PIPESTATUS[0]}
echo "[apt] exit=$RC"

echo "[needrestart] services that WANT a restart (LIST-ONLY, restarts nothing):"
NEEDRESTART_MODE=l needrestart -b 2>/dev/null | grep -E 'NEEDRESTART-(SVC|KSTA|KCUR|KEXP)' | sed 's/^/  /'
WOULD=$(NEEDRESTART_MODE=l needrestart -b 2>/dev/null | awk -F': ' '/NEEDRESTART-SVC/{print $2}')

echo "[restart] safe allowlist only = $SAFE_RESTART"
for s in $SAFE_RESTART; do
  if echo "$WOULD" | grep -qE "^${s}(\.service)?$"; then
    echo "  restarting $s (flagged + on safe allowlist) ..."; systemctl restart "$s" && echo "    $s active=$(systemctl is-active $s)"
  else
    echo "  $s not flagged by needrestart -- not restarting"
  fi
done
echo "[restart] NEVER touched: $PROTECTED (needrestart was list-only; their lib deps load at next restart)"

echo "[after] PROTECTED services (compare MainPID + NRestarts to [before]):"
for u in $PROTECTED; do
  echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"
done
echo "[after] target versions:"
for p in $PKGS; do echo "  $p=$(dpkg-query -W -f='${Version}' $p 2>/dev/null)"; done
echo "[after] remaining upgradable count: $(apt list --upgradable 2>/dev/null | grep -vc '^Listing')"
echo "=== BATCH3 DONE $TS ==="
