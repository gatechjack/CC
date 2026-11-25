#!/usr/bin/env bash
# secrev_batch2_deploy.sh -- BATCH 2 deploy (runs as ROOT via az vm run-command). Jack-approved.
# Bind dashboard to 127.0.0.1 (main.py) + starlette 1.0.1 + held python3.12 debs, ONE engine restart.
# Fail-safe: drift-gate, quiet-60s gate, hash-gated graft w/ py_compile + auto-restore. NEEDRESTART_MODE=l.
set -u
exec > >(tee /home/azureuser/secrev_batch2_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
BK=/home/azureuser/secrev_batch2_backup_$TS
STAGED=/home/azureuser/secrev_batch2_main.py
EXPECT_BASE=f486de42afc540b3a0cbdebe344fb13d   # prod-live 50aea132 main.py (box must match before graft)
EXPECT_NEW=6e52c9bd1a36eb1bc801c5772f4178c0    # the bind-to-127.0.0.1 main.py
mkdir -p "$BK"
echo "=== BATCH2 DEPLOY START $TS host=$(hostname) ==="

# 0) drift gate
BOXMD5=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)
echo "[drift] box main.py md5=$BOXMD5 expect-base=$EXPECT_BASE"
if [ "$BOXMD5" != "$EXPECT_BASE" ]; then echo "[ABORT] box main.py != prod-live base -- drift, nothing changed"; exit 2; fi

# 1) staged file present + is the exact bind change
if [ ! -f "$STAGED" ]; then echo "[ABORT] staged main.py missing"; exit 2; fi
STMD5=$(tr -d '\r' < "$STAGED" | md5sum | cut -c1-32)
echo "[staged] md5=$STMD5 expect-new=$EXPECT_NEW"
if [ "$STMD5" != "$EXPECT_NEW" ]; then echo "[ABORT] staged main.py hash mismatch"; exit 2; fi
DIFFN=$(diff <(tr -d '\r' < "$ROOT/trading_corp/main.py") <(tr -d '\r' < "$STAGED") | grep -cE '^[<>]')
echo "[staged] changed lines vs box = $DIFFN (expect 2)"
if [ "$DIFFN" != "2" ]; then echo "[ABORT] staged file differs from box by more than the host line"; exit 2; fi

# 2) pre-restart QUIET gate (no real order in last 60s) + record positions
PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
TCDB="file:$ROOT/data/trading_corp.db?mode=ro"
LASTORD=$(sqlite3 "$PMDB" "SELECT CAST((strftime('%s','now') - MAX(submitted_ts)) AS INT) FROM pm_subdivision_order WHERE dry_run=0;" 2>/dev/null)
echo "[quiet] seconds since last real PM order = ${LASTORD:-unknown}"
if [ -n "$LASTORD" ] && [ "$LASTORD" -ge 0 ] 2>/dev/null && [ "$LASTORD" -lt 60 ] 2>/dev/null; then
  echo "[ABORT] a real order placed <60s ago -- NOT quiet; re-run in ~1 min"; exit 3
fi
echo "[positions] mace_open=$(sqlite3 "$TCDB" "SELECT COUNT(*) FROM mace_rung WHERE status='open';" 2>&1) pm_active_subdiv=$(sqlite3 "$PMDB" "SELECT COUNT(*) FROM pm_subdivision WHERE active=1;" 2>&1)"
echo "[positions] arm_state by agent:"
sqlite3 "$TCDB" "SELECT '    '||agent||'='||COUNT(*) FROM agent_state GROUP BY agent;" 2>&1 | head -30

# 3) backups
cp -a "$ROOT/trading_corp/main.py" "$BK/main.py.bak"
"$ROOT/venv/bin/pip" freeze > "$BK/pip_freeze.pre.txt" 2>/dev/null
echo "[backup] -> $BK"

# 4) held python3.12 deadsnakes debs (NEEDRESTART_MODE=l; takes effect at the restart below)
echo "[py3.12] venv before: $("$ROOT/venv/bin/python" -V 2>&1); system deb: $(dpkg-query -W -f='${Version}' python3.12 2>/dev/null)"
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y --only-upgrade --no-install-recommends -o Dpkg::Options::=--force-confold python3.12 python3.12-dev python3.12-venv libpython3.12 libpython3.12-dev libpython3.12-stdlib 2>&1 | tail -12
echo "[py3.12] system deb after: $(dpkg-query -W -f='${Version}' python3.12 2>/dev/null)"

# 5) starlette into the prod venv
echo "[pip] starlette before: $("$ROOT/venv/bin/pip" show starlette 2>/dev/null | awk '/^Version:/{print $2}')"
"$ROOT/venv/bin/pip" install "starlette==1.0.1" 2>&1 | tail -6
SV=$("$ROOT/venv/bin/pip" show starlette 2>/dev/null | awk '/^Version:/{print $2}')
echo "[pip] starlette after: $SV"
if [ "$SV" != "1.0.1" ]; then echo "[ABORT] starlette != 1.0.1 after install; main.py NOT grafted, NOT restarting"; exit 4; fi

# 6) graft main.py (force LF) + verify + py_compile, auto-restore on failure
tr -d '\r' < "$STAGED" > "$ROOT/trading_corp/main.py"
NEWMD5=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)
echo "[graft] box main.py now md5=$NEWMD5 expect=$EXPECT_NEW"
if [ "$NEWMD5" != "$EXPECT_NEW" ]; then echo "[ABORT] post-graft mismatch -- restoring"; cp -a "$BK/main.py.bak" "$ROOT/trading_corp/main.py"; exit 4; fi
if ! "$ROOT/venv/bin/python" -m py_compile "$ROOT/trading_corp/main.py"; then echo "[ABORT] py_compile failed -- restoring"; cp -a "$BK/main.py.bak" "$ROOT/trading_corp/main.py"; exit 4; fi
echo "[graft] py_compile OK"

# 7) restart trading-corp ONCE
OLDPID=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
echo "[restart] old PID=$OLDPID -- restarting trading-corp ONCE ..."
systemctl restart trading-corp
sleep 10
NEWPID=$(systemctl show trading-corp -p MainPID --value 2>/dev/null)
echo "[restart] new PID=$NEWPID active=$(systemctl is-active trading-corp) NRestarts=$(systemctl show trading-corp -p NRestarts --value 2>/dev/null)"
echo "[restart] bind check (early): $(ss -tlnH 2>/dev/null | grep ':8000' | sed 's/^/    /')"
echo "=== BATCH2 DEPLOY DONE $TS -- backups in $BK. Verify: 5-min traceback watch + divisions + ss 127.0.0.1:8000 + 302/405/400 ==="
