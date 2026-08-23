#!/usr/bin/env bash
# CP2 Phase-1 ROOT DEPLOY (Option A). Runs as ROOT via `az vm run-command` (sanctioned root channel; azureuser has
# no sudo). POSIX-safe (RunShellScript may exec /bin/sh): no [[ ]], no process substitution, no `local`.
# SCOPE, EXACTLY: place web/ + pm_web.py; chown azureuser + modes TARGETED to the PM-only paths + the two PM
# script files; install the unit; gate (2 stated exclusions); prove; GOTCHA-1 no-residue; read-only threat scan.
# NEVER: -R above the PM paths, any engine file, the shared scripts/ dir, Caddy/DNS/Authelia.
echo "=== PM P2 CP2 PHASE-1 ROOT DEPLOY (start) ==="
date -u
echo "whoami=$(whoami)   (expect root)"
ROOT=/home/azureuser/trading_corp
PKG=$ROOT/trading_corp/prediction_markets
SCR=$ROOT/trading_corp/scripts
VP=$ROOT/venv/bin/python
STAGE=/home/azureuser/pm_p2_stage.tgz
STG=/tmp/pm_p2_deploy_stg
UNIT=/etc/systemd/system/prediction-markets-web.service
DEPLOY_OK=1
EXP_WEBAPP=1ed5ba8bfb2246f408867ccc531a7d8f75038cf49d1073ef987762975b41c283
EXP_WEBINIT=047476866c3bb2874eeecb2e21af17099bc8816cb5c96adf0ee1cbad91757e03
EXP_PMWEB=cb49b841c7a790a182750b0c1f7de1e56b0055e209b0a3ea9b9a2bcba2a36090
EXP_SVC=17de410bc7e6334a04c74432cd90a8634d5578224da51aa322e47cbd661fc1f9
EXP_DB=ef20a508d8f327ec7dc98822ec5c82a8e91bb964af6cdebfe93f7bcd580ed194
EXP_STATS=6d6e66049d8f3bbb56783ab9455d0812c3f35173ee75c47ed4d40d8a21cd7c20
EXP_CAT=b2b85b8eb12f154855c42e8edc52ca029b9d70506431bcfa822e282af9acca4a
EXP_CLI=d1e49e99bd0472a28d635a83e187580bfc5190c3b474cc43d87bc1383cede812

echo ""
echo "=== [0] ENGINE BEFORE (baseline) ==="
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
AS0=$(systemctl show -p ActiveState --value trading-corp.service 2>/dev/null)
echo "trading-corp MainPID=$PID0 ActiveState=$AS0"
"$VP" -c "import sqlite3;c=sqlite3.connect('file:$ROOT/data/prediction_markets.db?mode=ro',uri=True);print('live_schema_version',c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]);c.close()" 2>&1

echo ""
echo "=== [1] EXTRACT stage (root) ==="
sha256sum "$STAGE" 2>&1
rm -rf "$STG"; mkdir -p "$STG"; tar -xzf "$STAGE" -C "$STG" && echo "extracted to $STG"

echo ""
echo "=== [2] DEPLOY new PM files (web/ + pm_web.py) ==="
mkdir -p "$PKG/web"
cp "$STG/trading_corp/prediction_markets/web/__init__.py" "$PKG/web/__init__.py"
cp "$STG/trading_corp/prediction_markets/web/app.py" "$PKG/web/app.py"
cp "$STG/trading_corp/scripts/pm_web.py" "$SCR/pm_web.py"
echo "placed: $PKG/web/{__init__,app}.py + $SCR/pm_web.py"

echo ""
echo "=== [3] CHOWN + CHMOD -- TARGETED (PM-only paths + the 2 PM files; NEVER the scripts/ dir or engine) ==="
chown -R azureuser:azureuser "$PKG"
find "$PKG" -type d -exec chmod 755 {} +
find "$PKG" -type f -exec chmod 644 {} +
chown azureuser:azureuser "$SCR/pm_cli.py" "$SCR/pm_web.py"
chmod 644 "$SCR/pm_cli.py" "$SCR/pm_web.py"
echo "applied to: $PKG (recursive), $SCR/pm_cli.py, $SCR/pm_web.py"
echo "NOT touched: the scripts/ dir ($SCR) itself, any engine file, anything above the PM paths."

echo ""
echo "=== [4] CHAIN OF CUSTODY (deployed PM code == approved refs) ==="
chk() { got=$(sha256sum "$1" 2>/dev/null | cut -d' ' -f1); if [ "$got" = "$2" ]; then echo "  OK   $1"; else echo "  FAIL $1 got=$got exp=$2"; DEPLOY_OK=0; fi; }
chk "$PKG/web/app.py" "$EXP_WEBAPP"
chk "$PKG/web/__init__.py" "$EXP_WEBINIT"
chk "$SCR/pm_web.py" "$EXP_PMWEB"
chk "$PKG/db.py" "$EXP_DB"
chk "$PKG/stats.py" "$EXP_STATS"
chk "$PKG/category.py" "$EXP_CAT"
chk "$SCR/pm_cli.py" "$EXP_CLI"
chk "$STG/reports/prediction_markets/prediction-markets-web.service" "$EXP_SVC"

echo ""
echo "=== [5] GATE over PM-only paths + the 2 PM files ==="
echo "  EXCLUSION 1 (STATED, not silent): shared scripts/ dir $SCR stays 197609:755 -- engine-interleaved, not ours (Option A)."
echo "  EXCLUSION 2 (STATED, not silent): unit $UNIT is root:root by nature -- correct for systemd; excluded from the azureuser gate."
n_owner=$(find "$PKG" \( ! -user azureuser -o ! -group azureuser \) 2>/dev/null | wc -l)
n_ww=$(find "$PKG" -perm -0002 2>/dev/null | wc -l)
n_dir=$(find "$PKG" -type d ! -perm 755 2>/dev/null | wc -l)
n_file=$(find "$PKG" -type f ! -perm 644 2>/dev/null | wc -l)
echo "  prediction_markets/: bad_owner=$n_owner world_writable=$n_ww non755_dirs=$n_dir non644_files=$n_file"
[ "$n_owner" -gt 0 ] && { echo "  offending owners:"; find "$PKG" \( ! -user azureuser -o ! -group azureuser \) 2>/dev/null | head; }
[ "$n_ww" -gt 0 ] && { echo "  world-writable:"; find "$PKG" -perm -0002 2>/dev/null | head; }
pmf_bad=0
for f in "$SCR/pm_cli.py" "$SCR/pm_web.py"; do
  o=$(stat -c '%U:%G' "$f" 2>/dev/null); m=$(stat -c '%a' "$f" 2>/dev/null)
  echo "  $f owner=$o mode=$m"
  [ "$o" = "azureuser:azureuser" ] || pmf_bad=1
  [ "$m" = "644" ] || pmf_bad=1
done
if [ "$n_owner" -eq 0 ] && [ "$n_ww" -eq 0 ] && [ "$n_dir" -eq 0 ] && [ "$n_file" -eq 0 ] && [ "$pmf_bad" -eq 0 ]; then
  echo "  GATE PASS (PM paths azureuser, dirs 755, files 644, none world-writable; 2 PM files azureuser 644)"
else
  echo "  GATE FAIL"; DEPLOY_OK=0
fi

echo ""
echo "=== [6] INSTALL UNIT + enable (only if DEPLOY_OK=1) ==="
if [ "$DEPLOY_OK" = "1" ]; then
  cp "$STG/reports/prediction_markets/prediction-markets-web.service" "$UNIT"
  chmod 644 "$UNIT"
  echo "unit installed: $UNIT (root:root 644 by nature -- gate-excluded, stated)"
  systemctl daemon-reload
  systemctl enable --now prediction-markets-web 2>&1
  echo "daemon-reload + enable --now prediction-markets-web done"
  sleep 2
else
  echo "DEPLOY_OK=0 -> SKIP unit install/enable (chain-of-custody or gate failed above). Nothing enabled."
fi

echo ""
echo "=== [7] PROVE (only if deployed) ==="
if [ "$DEPLOY_OK" = "1" ]; then
  echo "--- systemctl status prediction-markets-web ---"
  systemctl status prediction-markets-web --no-pager 2>&1 | head -14
  echo "--- curl 127.0.0.1:8081/healthz (retry up to 5x) ---"
  for i in 1 2 3 4 5; do
    out=$(curl -s -m 5 -w ' HTTP:%{http_code}' http://127.0.0.1:8081/healthz 2>&1)
    echo "  attempt $i: $out"
    printf '%s' "$out" | grep -q 'HTTP:200' && break
    sleep 2
  done
  echo "--- ENGINE unchanged across the deploy? ---"
  PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
  AS1=$(systemctl show -p ActiveState --value trading-corp.service 2>/dev/null)
  echo "trading-corp MainPID=$PID1 (was $PID0) ActiveState=$AS1 (was $AS0)"
  if [ "$PID0" = "$PID1" ] && [ "$AS0" = "$AS1" ]; then echo "  ENGINE_UNCHANGED=GOOD"; else echo "  ENGINE_CHANGED=INVESTIGATE"; fi
  echo "--- RESTART pm_web; show trading-corp UNTOUCHED across it (demonstrated) ---"
  systemctl restart prediction-markets-web 2>&1; sleep 2
  PID2=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null)
  AS2=$(systemctl show -p ActiveState --value trading-corp.service 2>/dev/null)
  echo "after pm_web restart: trading-corp MainPID=$PID2 ActiveState=$AS2"
  if [ "$PID0" = "$PID2" ] && [ "$AS0" = "$AS2" ]; then echo "  ENGINE_UNTOUCHED_ACROSS_PMWEB_RESTART=GOOD"; else echo "  ENGINE_TOUCHED=INVESTIGATE"; fi
  echo "--- /healthz after restart ---"
  sleep 1; curl -s -m 5 -w ' HTTP:%{http_code}\n' http://127.0.0.1:8081/healthz 2>&1
fi

echo ""
echo "=== [8] GOTCHA-1 no-residue proof + cleanup ==="
echo "--- post-run ownership/mode of EVERY PM path (expect azureuser; dirs 755; files 644) ---"
ls -la "$PKG" 2>&1
ls -la "$PKG/web" 2>&1
ls -l "$SCR/pm_cli.py" "$SCR/pm_web.py" 2>&1
echo "--- unit file (root:root 644 by nature) ---"
ls -l "$UNIT" 2>&1
rm -rf "$STG"; rm -f "$STAGE"
if [ -e "$STG" ]; then echo "STG_STILL_THERE=BAD"; else echo "STG_GONE"; fi
if [ -e "$STAGE" ]; then echo "STAGE_STILL_THERE=BAD"; else echo "STAGE_GONE"; fi

echo ""
echo "=== [9] THREAT-MODEL (READ-ONLY -- findings for Jack; changes NOTHING) ==="
echo "--- [9a] login-shell accounts (/etc/passwd only; NOT /etc/shadow) ---"
grep -E ':/(bin/(ba|z)?sh|usr/bin/(ba|z)?sh)$' /etc/passwd 2>&1 | cut -d: -f1,3,7
echo "--- [9b] authorized_keys (key-line COUNT + owner ONLY; never key contents) ---"
for akf in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do [ -f "$akf" ] && echo "  $akf : $(grep -c . "$akf" 2>/dev/null) key-lines, owner $(stat -c '%U:%G %a' "$akf" 2>/dev/null)"; done
echo "--- [9c] distinct process users (flag any beyond root/azureuser/system accounts) ---"
ps -eo user:24 --no-headers 2>/dev/null | sort | uniq -c
echo "--- [9d] LISTENING sockets + iface + process (confirm pm_web=127.0.0.1:8081; flag unexpected 0.0.0.0) ---"
ss -tlnp 2>&1
echo "--- [9e] world-readable files under trading_corp matching credential patterns (FILENAMES ONLY, never values) ---"
find "$ROOT" -type f -perm -o=r -not -path '*/venv/*' -not -path '*/__pycache__/*' -not -path '*/.git/*' -not -path '*/data/*' 2>/dev/null | while IFS= read -r f; do
  if LC_ALL=C grep -lIE '(api[_-]?key|secret|token|passwd|password|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|AKIA[0-9A-Z]{16})' "$f" >/dev/null 2>&1; then
    echo "  MATCH (pattern present; VALUE NOT SHOWN): $f  [perm $(stat -c '%a' "$f" 2>/dev/null)]"
  fi
done | head -40
echo "  (world-readable files whose CONTENT matched a credential-ish pattern; values deliberately not printed -- Jack reviews)"

echo ""
echo "=== [10] SUMMARY ==="
echo "DEPLOY_OK=$DEPLOY_OK"
echo "=== PM P2 CP2 PHASE-1 ROOT DEPLOY (done) ==="
