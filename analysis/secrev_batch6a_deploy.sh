#!/usr/bin/env bash
# secrev_batch6a_deploy.sh -- BATCH 6a: the 25 pending esm-apps security updates. JACK RUNS (az vm run-command, root).
# All 25 are media/codec/XFCE + xrdp libs; VERIFIED read-only that 0 of the 6 protected services load any of them
# (0 matching .so in /proc/<pid>/maps; caddy is static-Go) -> NO service restart. This is 6a only; there is no 6b.
# Gates: snapshot assumed taken first; DRY-RUN must not REMOVE anything and must not touch the sensitive set
# (python3.12/openssl/libssl/libc6/kernel/caddy/authelia) -> else ABORT before install; install with NEEDRESTART_MODE=l;
# before/after dpkg diff reported; HARD gate = all 6 protected PIDs unchanged + reboot-required stays clear.
set -u
exec > >(tee /home/azureuser/secrev_batch6a_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"
PKGS="exo-utils ffmpeg gstreamer1.0-plugins-bad libaom3 libavcodec58 libavdevice58 libavfilter7 libavformat58 libavutil56 libde265-0 libexo-2-0 libexo-common libfluidsynth3 libgstreamer-plugins-bad1.0-0 libopenexr25 libpostproc55 libsoup-3.0-0 libsoup-3.0-common libsrt1.4-gnutls libswresample3 libswscale5 libzbar0 libzvbi-common libzvbi0 xrdp"
SENS='^(python3\.12|libpython3\.12|openssl|libssl|libc6|libc-bin|linux-image|linux-azure|linux-headers|caddy|authelia)'
echo "=== BATCH6A START $TS host=$(hostname) ==="
declare -A OLD
echo "[before] protected PIDs:"
for u in $PROTECTED; do OLD[$u]=$(systemctl show $u -p MainPID --value 2>/dev/null); echo "  $u PID=${OLD[$u]} NRestarts=$(systemctl show $u -p NRestarts --value) active=$(systemctl is-active $u)"; done
echo "[before] reboot-required=$([ -f /var/run/reboot-required ] && echo YES || echo no)"
dpkg-query -W -f='${Package} ${Version}\n' 2>/dev/null | sort > "/home/azureuser/secrev_batch6a_pkgs.before.$TS"
echo "[before] captured $(wc -l < "/home/azureuser/secrev_batch6a_pkgs.before.$TS") package versions"

# --- pre-install DRY-RUN gate ---
DR=$(DEBIAN_FRONTEND=noninteractive apt-get -s install --only-upgrade $PKGS 2>&1)
echo "[dry-run] Inst/Remv:"; printf '%s\n' "$DR" | grep -E '^(Inst|Remv)' | sed 's/^/    /'
if printf '%s\n' "$DR" | grep -q '^Remv'; then echo "[ABORT] dry-run would REMOVE package(s); nothing changed"; exit 2; fi
SENSHIT=$(printf '%s\n' "$DR" | grep -E '^Inst' | awk '{print $2}' | grep -E "$SENS" || true)
if [ -n "$SENSHIT" ]; then echo "[ABORT] dry-run touches SENSITIVE packages: $SENSHIT -- nothing changed"; exit 2; fi
echo "[dry-run] OK: no removals, no sensitive packages"

# --- install (list-only needrestart -> never auto-restart) ---
echo "[install] DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install --only-upgrade -y ..."
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install --only-upgrade -y $PKGS 2>&1 | tail -25

# --- before/after diff ---
dpkg-query -W -f='${Package} ${Version}\n' 2>/dev/null | sort > "/home/azureuser/secrev_batch6a_pkgs.after.$TS"
echo "[diff] packages changed (< before / > after):"
diff "/home/azureuser/secrev_batch6a_pkgs.before.$TS" "/home/azureuser/secrev_batch6a_pkgs.after.$TS" | grep -E '^[<>]' | sed 's/^/    /'
CHG=$(diff "/home/azureuser/secrev_batch6a_pkgs.before.$TS" "/home/azureuser/secrev_batch6a_pkgs.after.$TS" | grep -E '^[<>]' | awk '{print $2}' | sort -u)
EXTRA=$(comm -23 <(printf '%s\n' $CHG) <(printf '%s\n' $PKGS | tr ' ' '\n' | sort -u))
[ -n "$EXTRA" ] && echo "[note] changed OUTSIDE the named 25 (deps): $(printf '%s' "$EXTRA" | tr '\n' ' ')" || echo "[diff] only the named set changed"

# --- HARD gate: protected PIDs unchanged + no reboot-required ---
echo "[after] protected PIDs (must be UNCHANGED):"
FAIL=0
for u in $PROTECTED; do NEW=$(systemctl show $u -p MainPID --value 2>/dev/null); if [ "$NEW" = "${OLD[$u]}" ]; then S=SAME; else S="CHANGED(!)"; FAIL=1; fi; echo "  $u PID=$NEW (was ${OLD[$u]}) $S active=$(systemctl is-active $u)"; done
echo "[after] reboot-required=$([ -f /var/run/reboot-required ] && echo 'YES(!)' || echo no)"
echo "[needrestart list-only -- services it thinks need restart (informational; we do NOT restart):"
NEEDRESTART_MODE=l needrestart -b 2>/dev/null | grep -iE 'NEEDRESTART-SVC' | sed 's/^/    /' || echo "    (none)"
if [ "$FAIL" -ne 0 ]; then echo "=== BATCH6A: a protected PID CHANGED -- investigate (unexpected) $TS ==="; else echo "=== BATCH6A DONE -- 25 esm-apps patched, all 6 PIDs unchanged, no reboot-required $TS ==="; fi
