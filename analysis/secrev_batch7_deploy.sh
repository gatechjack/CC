#!/usr/bin/env bash
# secrev_batch7_deploy.sh -- BATCH 7 desktop-leftovers purge (381 pkgs). JACK RUNS (az vm run-command, root).
# STAGED -- run only after a snapshot. Removes the XFCE desktop + Xorg video drivers + xrdp + ffmpeg/gstreamer/webkit
# + pulseaudio/printing/perl-www. KEEPS: xvfb + X base (xserver-common/libxfont2/xauth/x11-xkb-utils/libgl1/libpixman),
# libgtk-3-0 (playwright firefox dlopens it), software-properties-common (deadsnakes python3.12 PPA).
# NOTE: playwright firefox 148 is ALREADY broken pre-purge (system NSS < NSS_3.101); the purge touches none of its libs.
set -u
exec > >(tee /home/azureuser/secrev_batch7_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
LIST=/home/azureuser/secrev_batch7_purge_list.txt
EXPECT_LIST=b92b27a4f37b4ebd02b026bd262a1c4b119baaf9edc289a396c4f290e1b5bcae
REINST=/home/azureuser/secrev_batch7_reinstall_$TS.txt
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"
KEEP_MUST="xvfb xserver-common libxfont2 xauth x11-xkb-utils libgl1 libpixman-1-0 libgtk-3-0 software-properties-common"
FFDIR=/home/azureuser/.cache/ms-playwright/firefox-1511/firefox
echo "=== BATCH7 START $TS host=$(hostname) ==="

# --- gate: staged list present + sha256 matches ---
[ -r "$LIST" ] || { echo "[ABORT] purge list $LIST missing"; exit 2; }
GOT=$(sha256sum "$LIST" | cut -d' ' -f1); echo "[list] sha256=$GOT expect=$EXPECT_LIST count=$(wc -l < "$LIST")"
[ "$GOT" = "$EXPECT_LIST" ] || { echo "[ABORT] purge-list sha256 mismatch"; exit 2; }
PKGS=$(tr '\n' ' ' < "$LIST")

echo "[before] protected PIDs:"; declare -A OLD
for u in $PROTECTED; do OLD[$u]=$(systemctl show $u -p MainPID --value 2>/dev/null); echo "  $u PID=${OLD[$u]} active=$(systemctl is-active $u)"; done
XV_PRE=$(pgrep -f 'Xvfb :99' | head -1); echo "[before] Xvfb :99 pid=$XV_PRE"
echo "[before] reboot-required=$([ -f /var/run/reboot-required ] && echo YES || echo no)"

# --- gate: KEEP packages all installed BEFORE ---
for k in $KEEP_MUST; do dpkg -s "$k" >/dev/null 2>&1 || { echo "[ABORT] KEEP package $k not installed pre-purge"; exit 2; }; done
echo "[before] KEEP set all present"

# --- save exact-version reinstall list (for rollback) ---
dpkg-query -W -f='${Package}=${Version}\n' $PKGS 2>/dev/null > "$REINST"
echo "[before] saved reinstall list ($(wc -l < "$REINST") pkgs) -> $REINST"

# --- firefox + Xvfb lib-resolution baseline (count unresolved; LD_LIBRARY_PATH so bundled libs resolve) ---
ffnf(){ local n=0; for so in "$FFDIR"/firefox "$FFDIR"/*.so; do [ -e "$so" ] && n=$((n+$(LD_LIBRARY_PATH="$FFDIR" ldd "$so" 2>/dev/null | grep -c 'not found'))); done; echo $n; }
xvnf(){ ldd "$(command -v Xvfb)" 2>/dev/null | grep -c 'not found'; }
NF_FF_PRE=$(ffnf); NF_XV_PRE=$(xvnf)
echo "[before] firefox unresolved-libs=$NF_FF_PRE (pre-existing NSS mismatch); Xvfb unresolved=$NF_XV_PRE"

# --- DRY-RUN gate: removal set must EXACTLY equal the list; no KEEP in removal ---
DRY=$(DEBIAN_FRONTEND=noninteractive apt-get -s purge $PKGS 2>&1)
RSET=$(printf '%s\n' "$DRY" | grep -E '^(Remv|Purg)' | awk '{print $2}' | sort -u)
EXTRA=$(comm -13 <(sort "$LIST") <(printf '%s\n' "$RSET"))
MISS=$(comm -23 <(sort "$LIST") <(printf '%s\n' "$RSET"))
echo "[dry-run] would remove $(printf '%s\n' "$RSET" | grep -c .) ; extra-beyond-list=$(printf '%s' "$EXTRA" | grep -c .) ; missing-from-removal=$(printf '%s' "$MISS" | grep -c .)"
[ -z "$EXTRA" ] || { echo "[ABORT] dry-run removes packages NOT in the list: $(printf '%s' "$EXTRA"|tr '\n' ' ')"; exit 3; }
KINR=$(printf '%s\n' "$RSET" | grep -xF -f <(printf '%s\n' $KEEP_MUST) || true)
[ -z "$KINR" ] || { echo "[ABORT] dry-run would remove a KEEP package: $KINR"; exit 3; }
echo "[dry-run] OK: removal == staged list, no KEEP touched"

# --- PURGE (explicit list; no --autoremove; no wildcards; needrestart list-only) ---
echo "[purge] DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get purge -y <381> ..."
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get purge -y $PKGS 2>&1 | tail -15

# --- POST gates ---
STILL=$(dpkg -l $PKGS 2>/dev/null | awk '/^ii/{print $2}' | grep -c . || true)
echo "[post] purged-packages still installed: ${STILL:-0} (expect 0)"
for k in $KEEP_MUST; do dpkg -s "$k" >/dev/null 2>&1 || echo "  [FAIL] KEEP $k MISSING post-purge"; done
echo "[post] KEEP recheck done"
NF_FF_POST=$(ffnf); NF_XV_POST=$(xvnf)
echo "[post] firefox unresolved-libs=$NF_FF_POST (pre=$NF_FF_PRE) ; Xvfb unresolved=$NF_XV_POST (pre=$NF_XV_PRE)"
REG=0; [ "$NF_FF_POST" -gt "$NF_FF_PRE" ] 2>/dev/null && { echo "  [FAIL] purge added $((NF_FF_POST-NF_FF_PRE)) unresolved firefox libs"; REG=1; }
[ "$NF_XV_POST" -gt "$NF_XV_PRE" ] 2>/dev/null && { echo "  [FAIL] purge added unresolved Xvfb libs"; REG=1; }
echo "[post] protected PIDs (must be UNCHANGED):"; FAIL=0
for u in $PROTECTED; do NEW=$(systemctl show $u -p MainPID --value 2>/dev/null); [ "$NEW" = "${OLD[$u]}" ] && S=SAME || { S="CHANGED(!)"; FAIL=1; }; echo "  $u PID=$NEW (was ${OLD[$u]}) $S active=$(systemctl is-active $u)"; done
XV_POST=$(pgrep -f 'Xvfb :99' | head -1); echo "[post] Xvfb :99 pid=$XV_POST (was $XV_PRE) $([ -n "$XV_POST" ] && echo RUNNING || echo 'GONE(!)')"
echo "[post] reboot-required=$([ -f /var/run/reboot-required ] && echo 'YES(!)' || echo no)"
echo "[post] firefox binary execs: $("$FFDIR/firefox" --version 2>&1 | head -1); Xvfb execs: $(Xvfb -help 2>&1 | head -1 | cut -c1-30)"

# --- SOFT browser launch smoke (informational; playwright firefox is pre-broken via NSS) ---
XAUTH=$(ps -o args= -p "${XV_POST:-$XV_PRE}" 2>/dev/null | grep -oE '\-auth [^ ]+' | awk '{print $2}')
echo "[smoke] snap firefox headless about:blank on :99 (as azureuser): "
su - azureuser -c "XAUTHORITY='$XAUTH' DISPLAY=:99 timeout 40 firefox --headless --screenshot=/tmp/b7_snap_ff_$TS.png about:blank >/dev/null 2>&1; echo rc=\$?" 2>/dev/null | sed 's/^/    /'
ls -l /tmp/b7_snap_ff_$TS.png 2>/dev/null | sed 's/^/    screenshot: /' || echo "    (snap firefox did not produce a screenshot -- see rc; informational only)"

if [ "$FAIL" = 0 ] && [ "$REG" = 0 ] && [ "${STILL:-0}" = 0 ] && [ -n "$XV_POST" ]; then
  echo "=== BATCH7 DONE GREEN $TS -- 381 purged, KEEP intact, 6 PIDs unchanged, Xvfb :99 up, no new unresolved libs ==="
else
  echo "=== BATCH7 POST-CHECK FAILURE $TS -- ROLLBACK: restore snapshot OR run secrev_batch7_rollback.sh $REINST ==="
fi
