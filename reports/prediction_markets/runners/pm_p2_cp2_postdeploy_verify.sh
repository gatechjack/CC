#!/usr/bin/env bash
# CP2 Phase-1 POST-DEPLOY READ-ONLY VERIFY (azureuser) -- the root-deploy output was truncated on paste; re-capture
# the proofs. curl/systemctl/ls/stat/sha256/find/ss/ps/grep ONLY. No writes, no chown, no restart, no engine touch.
echo "=== PM P2 CP2 POST-DEPLOY VERIFY (read-only) ==="
date -u
echo "whoami=$(whoami)"
ROOT=/home/azureuser/trading_corp
PKG=$ROOT/trading_corp/prediction_markets
SCR=$ROOT/trading_corp/scripts

echo ""
echo "=== pm_web service (expect active/running, User=azureuser) ==="
systemctl show prediction-markets-web -p ActiveState -p SubState -p MainPID -p User -p UnitFileState 2>&1
echo "--- /healthz (expect HTTP:200 + pm_db_schema_version 4) ---"
curl -s -m 5 -w ' HTTP:%{http_code}\n' http://127.0.0.1:8081/healthz 2>&1

echo ""
echo "=== engine (expect MainPID 850993, active -- UNCHANGED) ==="
systemctl show trading-corp.service -p MainPID -p ActiveState -p SubState 2>&1

echo ""
echo "=== PM path ownership/modes -- GATE proof + OPEN-A closure (expect azureuser, dirs 755, files 644) ==="
stat -c 'DIR %n %U:%G %a' "$PKG" 2>&1
ls -la "$PKG" 2>&1
ls -la "$PKG/web" 2>&1
ls -l "$SCR/pm_cli.py" "$SCR/pm_web.py" 2>&1
echo "--- gate re-check counts (all expect 0) ---"
echo "bad_owner=$(find "$PKG" \( ! -user azureuser -o ! -group azureuser \) 2>/dev/null | wc -l) world_writable=$(find "$PKG" -perm -0002 2>/dev/null | wc -l) non755_dirs=$(find "$PKG" -type d ! -perm 755 2>/dev/null | wc -l) non644_files=$(find "$PKG" -type f ! -perm 644 2>/dev/null | wc -l)"
echo "--- the shared scripts/ dir (EXCLUDED, stays 197609:755 -- OPEN-A remainder) ---"
stat -c '%n %U:%G %a' "$SCR" 2>&1

echo ""
echo "=== chain of custody (deployed PM hashes; expect web/app 1ed5ba8b, __init__ 04747686, pm_web cb49b841, db ef20a508, stats 6d6e6604, cat b2b85b8e, pm_cli d1e49e99) ==="
sha256sum "$PKG/web/app.py" "$PKG/web/__init__.py" "$SCR/pm_web.py" "$PKG/db.py" "$PKG/stats.py" "$PKG/category.py" "$SCR/pm_cli.py" 2>&1

echo ""
echo "=== unit file (root:root 644 by nature -- gate-excluded) ==="
ls -l /etc/systemd/system/prediction-markets-web.service 2>&1

echo ""
echo "=== threat 9a-9d (azureuser-visible subset; root-only bits were in the truncated root run) ==="
echo "--- [9a] login-shell accounts (/etc/passwd) ---"
grep -E ':/(bin/(ba|z)?sh|usr/bin/(ba|z)?sh)$' /etc/passwd 2>&1 | cut -d: -f1,3,7
echo "--- [9b] authorized_keys (azureuser-readable; key COUNT + owner only) ---"
for akf in /home/*/.ssh/authorized_keys; do [ -r "$akf" ] && echo "  $akf : $(grep -c . "$akf" 2>/dev/null) keys, $(stat -c '%U:%G %a' "$akf" 2>/dev/null)"; done
echo "  (/root/.ssh/authorized_keys needs root -- from the truncated root run [9b])"
echo "--- [9c] distinct process users ---"
ps -eo user:24 --no-headers 2>/dev/null | sort | uniq -c
echo "--- [9d] listening sockets (ss -tln; process names need root -- from root run [9d]) ---"
ss -tln 2>&1
echo "=== VERIFY done ==="
