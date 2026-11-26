#!/usr/bin/env bash
# secrev_batch7_deploy.sh -- BATCH 7 desktop-leftovers purge (381 pkgs) + controlled 4-service restart.
# JACK RUNS (az vm run-command, root). Run only AFTER the snapshot tc-prod-os-20261126 shows Succeeded.
# Removes XFCE desktop + Xorg video drivers + xrdp + ffmpeg/gstreamer/webkit + pulseaudio/printing/perl-www.
# KEEPS: xvfb + X base (xserver-common/libxfont2/xauth/x11-xkb-utils/libgl1/libpixman), libgtk-3-0
# (playwright firefox dlopens it), software-properties-common (deadsnakes python3.12 PPA).
# NOTE: playwright firefox 148 is ALREADY broken pre-purge (system NSS < NSS_3.101); purge touches none of its libs.
set -u
exec > >(tee /home/azureuser/secrev_batch7_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
LIST=/home/azureuser/secrev_batch7_purge_list.txt
EXPECT_LIST=b92b27a4f37b4ebd02b026bd262a1c4b119baaf9edc289a396c4f290e1b5bcae
REINST=/home/azureuser/secrev_batch7_reinstall_$TS.txt
PROTECTED="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder caddy authelia"
RESTART="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder"
KEEP_MUST="xvfb xserver-common libxfont2 xauth x11-xkb-utils libgl1 libpixman-1-0 libgtk-3-0 software-properties-common"
FFDIR=/home/azureuser/.cache/ms-playwright/firefox-1511/firefox
echo "=== BATCH7 START $TS host=$(hostname) ==="

# --- gate: staged list present + sha256 ---
[ -r "$LIST" ] || { echo "[ABORT] purge list missing"; exit 2; }
GOT=$(sha256sum "$LIST" | cut -d' ' -f1); echo "[list] sha256=$GOT expect=$EXPECT_LIST count=$(wc -l < "$LIST")"
[ "$GOT" = "$EXPECT_LIST" ] || { echo "[ABORT] purge-list sha256 mismatch"; exit 2; }
PKGS=$(tr '\n' ' ' < "$LIST")

echo "[before] protected PIDs:"; declare -A OLD
for u in $PROTECTED; do OLD[$u]=$(systemctl show $u -p MainPID --value 2>/dev/null); echo "  $u PID=${OLD[$u]} NRestarts=$(systemctl show $u -p NRestarts --value) active=$(systemctl is-active $u)"; done
XV_PRE=$(pgrep -f 'Xvfb :99' | head -1); echo "[before] Xvfb :99 pid=$XV_PRE"
echo "[before] reboot-required=$([ -f /var/run/reboot-required ] && echo YES || echo no)"
for k in $KEEP_MUST; do dpkg -s "$k" >/dev/null 2>&1 || { echo "[ABORT] KEEP $k not installed pre-purge"; exit 2; }; done
echo "[before] KEEP set all present"
dpkg-query -W -f='${Package}=${Version}\n' $PKGS 2>/dev/null > "$REINST"
echo "[before] saved reinstall list ($(wc -l < "$REINST") pkgs) -> $REINST"
ffnf(){ local n=0; for so in "$FFDIR"/firefox "$FFDIR"/*.so; do [ -e "$so" ] && n=$((n+$(LD_LIBRARY_PATH="$FFDIR" ldd "$so" 2>/dev/null | grep -c 'not found'))); done; echo $n; }
NF_FF_PRE=$(ffnf); NF_XV_PRE=$(ldd "$(command -v Xvfb)" 2>/dev/null | grep -c 'not found')
echo "[before] firefox unresolved-libs=$NF_FF_PRE (pre-existing NSS) ; Xvfb unresolved=$NF_XV_PRE"

# --- DRY-RUN gate: removal == list exactly; no KEEP in removal ---
DRY=$(DEBIAN_FRONTEND=noninteractive apt-get -s purge $PKGS 2>&1)
RSET=$(printf '%s\n' "$DRY" | grep -E '^(Remv|Purg)' | awk '{print $2}' | sort -u)
EXTRA=$(comm -13 <(sort "$LIST") <(printf '%s\n' "$RSET"))
echo "[dry-run] would remove $(printf '%s\n' "$RSET" | grep -c .) ; extra-beyond-list=$(printf '%s' "$EXTRA" | grep -c .)"
[ -z "$EXTRA" ] || { echo "[ABORT] dry-run removes extra: $(printf '%s' "$EXTRA"|tr '\n' ' ')"; exit 3; }
KINR=$(printf '%s\n' "$RSET" | grep -xF -f <(printf '%s\n' $KEEP_MUST) || true)
[ -z "$KINR" ] || { echo "[ABORT] dry-run would remove KEEP: $KINR"; exit 3; }
echo "[dry-run] OK: removal == list, no KEEP touched"

# --- PURGE (explicit list; no --autoremove; needrestart list-only so nothing auto-restarts) ---
echo "[purge] NEEDRESTART_MODE=l apt-get purge -y <381> ..."
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get purge -y $PKGS 2>&1 | tail -12
PURGE_TS=$(date '+%Y-%m-%d %H:%M:%S')

# --- POST-PURGE gates (BEFORE any restart) ---
STILL=$(dpkg -l $PKGS 2>/dev/null | awk '/^ii/{print $2}' | grep -c . || true)
echo "[post-purge] purged-pkgs still installed: ${STILL:-0} (expect 0)"
KFAIL=0; for k in $KEEP_MUST; do dpkg -s "$k" >/dev/null 2>&1 || { echo "  [FAIL] KEEP $k MISSING"; KFAIL=1; }; done
NF_FF_POST=$(ffnf); NF_XV_POST=$(ldd "$(command -v Xvfb)" 2>/dev/null | grep -c 'not found')
echo "[post-purge] firefox unresolved=$NF_FF_POST (pre $NF_FF_PRE) ; Xvfb unresolved=$NF_XV_POST (pre $NF_XV_PRE)"
REG=0; { [ "$NF_FF_POST" -gt "$NF_FF_PRE" ] || [ "$NF_XV_POST" -gt "$NF_XV_PRE" ]; } 2>/dev/null && { echo "  [FAIL] purge added unresolved libs"; REG=1; }
PCHG=0; for u in $PROTECTED; do NEW=$(systemctl show $u -p MainPID --value 2>/dev/null); [ "$NEW" = "${OLD[$u]}" ] || { echo "  [FAIL] $u PID changed during PURGE ($NEW vs ${OLD[$u]})"; PCHG=1; }; done
XV_MID=$(pgrep -f 'Xvfb :99' | head -1)
RRB=$([ -f /var/run/reboot-required ] && echo yes || echo no)
echo "[post-purge] 6 PIDs unchanged=$([ $PCHG = 0 ] && echo yes || echo NO) ; Xvfb :99=${XV_MID:-GONE} ; reboot-required=$RRB"
if [ "${STILL:-0}" != 0 ] || [ "$KFAIL" != 0 ] || [ "$REG" != 0 ] || [ "$PCHG" != 0 ] || [ -z "$XV_MID" ] || [ "$RRB" = yes ]; then
  echo "=== BATCH7 PURGE-GATE FAILURE $TS -- NOT restarting. ROLLBACK: secrev_batch7_rollback.sh $REINST (then restart), snapshot only if that fails ==="; exit 4; fi
echo "[post-purge] ALL GATES GREEN -> proceeding to controlled restart"

# --- CONTROLLED RESTART: one at a time; abort further on any failure ---
RSTART_OK=1
for u in $RESTART; do
  if [ "$RSTART_OK" != 1 ]; then echo "[restart] SKIP $u (a prior restart failed)"; continue; fi
  echo "[restart] $u ..."; systemctl restart "$u"; sleep 8
  act=$(systemctl is-active "$u"); npid=$(systemctl show $u -p MainPID --value)
  echo "  $u active=$act newPID=$npid (was ${OLD[$u]})"
  [ "$act" = active ] || { echo "  [FAIL] $u not active -> ABORT further restarts"; RSTART_OK=0; }
  if [ "$u" = trading-corp ] && [ "$RSTART_OK" = 1 ]; then
    sleep 4; XP=$(pgrep -f 'Xvfb :99' | head -1); echo "  Xvfb :99 after trading-corp restart: pid=${XP:-GONE}"
    [ -n "$XP" ] || { echo "  [FAIL] Xvfb :99 did not respawn -> ABORT"; RSTART_OK=0; }
  fi
done

# --- post-restart in-script checks ---
echo "[verify] healthz: 8000=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/healthz) 8081=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8081/healthz)"
echo "[verify] Xvfb :99 now: pid=$(pgrep -f 'Xvfb :99' | head -1)"
echo "[verify] tracebacks since restart (per service):"
for u in $RESTART; do S=$(systemctl show $u -p ExecMainStartTimestamp --value | sed -E 's/^[A-Za-z]+ //'); echo "    $u tb=$(journalctl -u $u --since "$S" --no-pager 2>/dev/null | grep -c Traceback)"; done
echo "[verify] missing-lib / ImportError lines in journal since purge ($PURGE_TS):"
journalctl --since "$PURGE_TS" --no-pager 2>/dev/null | grep -iE 'cannot open shared object|error while loading shared libraries|ImportError|No module named|undefined symbol' | tail -15 | sed 's/^/    MISSING-LIB: /' || true
MLN=$(journalctl --since "$PURGE_TS" --no-pager 2>/dev/null | grep -icE 'cannot open shared object|error while loading shared libraries|ImportError|undefined symbol')
echo "[verify] missing-lib line count since purge: $MLN"
if [ "$RSTART_OK" = 1 ] && [ "$MLN" = 0 ]; then
  echo "=== BATCH7 DONE GREEN $TS -- 381 purged, KEEP intact, 4 services restarted clean, Xvfb :99 up, 0 missing-lib. Run secrev_batch7_verify.ps1 ==="
else
  echo "=== BATCH7 RESTART/VERIFY FAILURE $TS -- ROLLBACK: secrev_batch7_rollback.sh $REINST then restart services; snapshot only if that fails ==="
fi
