#!/usr/bin/env bash
# secrev_batch1.sh -- BATCH 1 (runs as ROOT via az vm run-command). Jack-approved.
# Caddy 2.11.4 -> 2.11.9 (validate-before-install) + Authelia 4.39.19 -> 4.39.28 (sha256-verified binary swap).
# Fail-safe: any failure aborts BEFORE touching the next component; authelia start-failure auto-rolls-back.
# Does NOT restart trading-corp. Prints a clear trail. No secrets printed.
set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/home/azureuser/secrev_batch1_backup_$TS
mkdir -p "$BK"
echo "=== BATCH1 START $TS host=$(hostname) ==="

EXECSTART=$(systemctl show caddy -p ExecStart 2>/dev/null)
CADDYFILE=$(echo "$EXECSTART" | grep -oE '\-\-config [^ ;]+' | awk '{print $2}' | head -1)
ADAPTER=$(echo "$EXECSTART" | grep -oE '\-\-adapter [^ ;]+' | awk '{print $2}' | head -1)
[ -z "$CADDYFILE" ] && CADDYFILE=/etc/caddy/Caddyfile
[ -z "$ADAPTER" ] && ADAPTER=caddyfile
AUCFG=/etc/authelia/configuration.yml
echo "[pre] caddy=$(/usr/bin/caddy version 2>/dev/null | head -1)"
echo "[pre] authelia=$(/usr/local/bin/authelia --version 2>/dev/null | head -1)"
echo "[pre] Caddyfile=$CADDYFILE adapter=$ADAPTER"

# ---------- BACKUPS ----------
cp -a /usr/bin/caddy "$BK/caddy.bin.bak" 2>/dev/null
dpkg-query -W -f='${Version}\n' caddy > "$BK/caddy.deb.version" 2>/dev/null
cp -a "$CADDYFILE" "$BK/Caddyfile.bak" 2>/dev/null
cp -a /usr/local/bin/authelia "$BK/authelia.bin.bak" 2>/dev/null
cp -a "$AUCFG" "$BK/authelia.configuration.yml.bak" 2>/dev/null
echo "[backup] dir=$BK"
ls -l "$BK" | awk '{print "  "$0}'
# Pin the FIRST backup of the night as the ORIGINAL (pre-change 2.11.4/4.39.19) state; never overwrite it.
PIN=/home/azureuser/secrev_batch1_ORIGINAL
if [ ! -f "$PIN" ]; then echo "$BK" > "$PIN"; echo "[backup] PINNED ORIGINAL pre-change state -> $BK"; else echo "[backup] ORIGINAL already pinned -> $(cat "$PIN") (this run's backup $BK is NOT the rollback target)"; fi

# ---------- CADDY ----------
CUR_CADDY=$(/usr/bin/caddy version 2>/dev/null | awk '{print $1}')
if echo "$CUR_CADDY" | grep -q '^v2.11.9'; then
  echo "[caddy] already 2.11.9 -- skip"
else
  echo "[caddy] apt-get update ..."
  apt-get update 2>&1 | tail -3
  echo "[caddy] download-only upgrade ..."
  apt-get install -y --only-upgrade --download-only caddy 2>&1 | tail -4
  DEB=$(ls -t /var/cache/apt/archives/caddy_*.deb 2>/dev/null | head -1)
  echo "[caddy] deb=$DEB"
  if [ -z "$DEB" ]; then echo "[caddy] NO deb downloaded -- ABORT (authelia untouched)"; exit 2; fi
  TMPD=$(mktemp -d)
  dpkg-deb -x "$DEB" "$TMPD"
  # The deb ships TWO files named 'caddy' (usr/bin/caddy binary + etc/bash_completion.d/caddy script).
  # Select the BINARY explicitly, never the completion script.
  NEWCADDY="$TMPD/usr/bin/caddy"
  [ -x "$NEWCADDY" ] || NEWCADDY=$(find "$TMPD" -type f -name caddy -path '*bin*' ! -path '*completion*' 2>/dev/null | head -1)
  if [ -z "$NEWCADDY" ] || [ ! -x "$NEWCADDY" ]; then echo "[caddy] could not locate executable caddy BINARY in deb (got: $NEWCADDY) -- ABORT (authelia untouched)"; exit 2; fi
  NV=$("$NEWCADDY" version 2>/dev/null | head -1)
  if [ -z "$NV" ]; then echo "[caddy] new binary reported no version (wrong file selected?) -- ABORT (authelia untouched)"; exit 2; fi
  echo "[caddy] new-binary version: $NV"
  echo "[caddy] VALIDATE live config with NEW binary (before install) ..."
  if "$NEWCADDY" validate --config "$CADDYFILE" --adapter "$ADAPTER" > "$BK/caddy_validate.out" 2>&1; then
    echo "[caddy] validate OK -> installing deb"
    apt-get install -y --only-upgrade caddy 2>&1 | tail -5
    echo "[caddy] reload (graceful) ..."
    if systemctl reload caddy; then echo "[caddy] reloaded"; else echo "[caddy] reload failed -> restart"; systemctl restart caddy; fi
    sleep 2
    echo "[caddy] service=$(systemctl is-active caddy) now=$(/usr/bin/caddy version 2>/dev/null | head -1)"
  else
    echo "[caddy] VALIDATE FAILED -- NOT installing; caddy stays $CUR_CADDY. First 20 lines:"
    sed -n '1,20p' "$BK/caddy_validate.out" | awk '{print "  "$0}'
    echo "=== BATCH1 ABORTED at caddy validate (authelia untouched) ==="
    exit 3
  fi
fi

# ---------- AUTHELIA ----------
CUR_AU=$(/usr/local/bin/authelia --version 2>/dev/null | awk '{print $NF}')
if echo "$CUR_AU" | grep -q '4.39.28'; then
  echo "[authelia] already 4.39.28 -- skip"
else
  cd /tmp || exit 9
  V=4.39.28
  BASE=authelia-v$V-linux-amd64.tar.gz
  URL=https://github.com/authelia/authelia/releases/download/v$V/$BASE
  SUMURL=https://github.com/authelia/authelia/releases/download/v$V/checksums.sha256
  echo "[authelia] download $URL"
  if ! curl -fsSL -o "$BASE" "$URL"; then echo "[authelia] tarball download FAILED -- authelia untouched (caddy stands)"; exit 4; fi
  echo "[authelia] download $SUMURL (combined checksums)"
  if ! curl -fsSL -o checksums.sha256 "$SUMURL"; then echo "[authelia] checksums.sha256 download FAILED -- authelia untouched"; exit 5; fi
  EXP=$(grep -E "authelia-v$V-linux-amd64\.tar\.gz$" checksums.sha256 | awk '{print $1}' | head -1)
  ACT=$(sha256sum "$BASE" | awk '{print $1}')
  echo "[authelia] expected=$EXP"
  echo "[authelia] actual  =$ACT"
  if [ -z "$EXP" ]; then echo "[authelia] tarball line not found in checksums.sha256 -- authelia untouched"; exit 6; fi
  if [ "$EXP" != "$ACT" ]; then echo "[authelia] SHA256 MISMATCH -- authelia untouched"; exit 6; fi
  echo "[authelia] sha256 OK -> extract"
  EXDIR=$(mktemp -d)
  tar xzf "$BASE" -C "$EXDIR"
  echo "[authelia] tarball contents:"
  ( cd "$EXDIR" && find . -maxdepth 3 -type f 2>/dev/null | sed 's/^/    /' )
  # Locate the real binary by name candidates, confirmed by a working --version (tarball is sha256-verified).
  NB=""
  for cand in $(find "$EXDIR" -maxdepth 3 -type f \( -name 'authelia-linux-amd64' -o -name 'authelia' \) 2>/dev/null); do
    if "$cand" --version >/dev/null 2>&1; then NB="$cand"; break; fi
  done
  if [ -z "$NB" ]; then echo "[authelia] could not locate a runnable authelia binary in tarball (see listing above) -- authelia untouched"; exit 6; fi
  echo "[authelia] new-binary: $NB -> $("$NB" --version 2>/dev/null | head -1)"
  # ---- CONFIG-VALIDATION GATE: NEW binary validates the LIVE config BEFORE we stop the running Authelia ----
  EFPATH=$(systemctl show authelia -p EnvironmentFiles --value 2>/dev/null | head -1 | awk '{print $1}')
  if "$NB" validate-config --help >/dev/null 2>&1; then VCCMD="validate-config"
  elif "$NB" config validate --help >/dev/null 2>&1; then VCCMD="config validate"
  else VCCMD=""; fi
  if [ -z "$VCCMD" ]; then
    echo "[authelia] WARNING: new binary exposes no validate-config subcommand -- gate skipped (sha256 already verified)"
  else
    ( set -a; [ -n "$EFPATH" ] && [ -f "$EFPATH" ] && . "$EFPATH" 2>/dev/null; set +a; "$NB" $VCCMD --config "$AUCFG" ) > "$BK/authelia_validate.out" 2>&1
    VCRC=$?
    ERRS=$(grep -ciE 'error|fatal|panic' "$BK/authelia_validate.out" 2>/dev/null)
    echo "[authelia] validate-config ($VCCMD) exit=$VCRC errorlines=$ERRS (full output in $BK, not printed)"
    if [ "$VCRC" != "0" ]; then
      echo "[authelia] CONFIG VALIDATION FAILED with new binary -- NOT swapping; 4.39.19 left RUNNING. (caddy upgrade stands)"
      echo "=== BATCH1 authelia SKIPPED at config-validate (4.39.19 running) ==="
      exit 8
    fi
    echo "[authelia] config validate OK -> proceed to swap"
  fi
  systemctl stop authelia
  cp -a "$NB" /usr/local/bin/authelia
  chmod 755 /usr/local/bin/authelia
  chown root:root /usr/local/bin/authelia
  systemctl start authelia
  sleep 4
  if systemctl is-active --quiet authelia; then
    echo "[authelia] active; now=$(/usr/local/bin/authelia --version 2>/dev/null | head -1)"
  else
    echo "[authelia] FAILED TO START with new binary -> ROLLING BACK to backup binary"
    systemctl stop authelia
    cp -a "$BK/authelia.bin.bak" /usr/local/bin/authelia
    chmod 755 /usr/local/bin/authelia
    systemctl start authelia
    sleep 3
    echo "[authelia] rolled back; service=$(systemctl is-active authelia) ver=$(/usr/local/bin/authelia --version 2>/dev/null | head -1)"
    echo "=== BATCH1 authelia ROLLED BACK (caddy upgrade stands) ==="
    exit 7
  fi
fi

echo "[post] caddy=$(/usr/bin/caddy version 2>/dev/null | head -1)"
echo "[post] authelia=$(/usr/local/bin/authelia --version 2>/dev/null | head -1)"
echo "[post] services: caddy=$(systemctl is-active caddy) authelia=$(systemctl is-active authelia)"
echo "[post] authelia recent errors (count): $(journalctl -u authelia -n 200 --no-pager 2>/dev/null | grep -c 'level=error')"
echo "=== BATCH1 DONE $TS -- backups in $BK ==="
