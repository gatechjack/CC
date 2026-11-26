#!/usr/bin/env bash
# secrev_batch2c1_deploy.sh -- BATCH 2c-1 (aiohttp + fastapi group). JACK RUNS (az vm run-command, root).
# Installs EXACTLY these, hash-verified, no transitive pulls:
#   aiohttp 3.13.5->3.14.5  yarl 1.23.0->1.25.1  fastapi 0.136.1->0.165.0
#   starlette 1.0.1->1.10.0  opentelemetry-api (new) 1.45.2
# starlette 1.10.0 >= 1.0.1 => BadHost KEV CVE-2026-48710 stays fixed.
# Gates: base=prod-live 110d735c main.py md5; staged-pins sha256; venv baseline; quiet-60s;
#        install --require-hashes --no-deps; freeze-diff (ONLY the expected names may change) + pip-check diff;
#        ANY deviation -> restore from pre-freeze + abort. Restarts all 4 shared-venv services once.
set -u
exec > >(tee /home/azureuser/secrev_batch2c1_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
BK=/home/azureuser/secrev_batch2c1_backup_$TS
PIP="$ROOT/venv/bin/pip"
PINS=/home/azureuser/secrev_batch2c1_pins.txt
EXPECT_MAIN=6e52c9bd1a36eb1bc801c5772f4178c0                                   # prod-live 110d735c main.py
EXPECT_PINS=172a636846196873e55cd167eab49b2bacd1e79dc6ed12dcf38da3ad3a1596a4  # sha256 of the staged pins file
EXPECT_CHANGED="aiohttp fastapi opentelemetry-api starlette yarl"             # the ONLY pkg names allowed to differ
RESTART="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder"
PROTECTED="$RESTART caddy authelia"
mkdir -p "$BK"
echo "=== BATCH2C-1 DEPLOY START $TS host=$(hostname) ==="
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done

# --- gate: base = prod-live 110d735c ---
BOXMAIN=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)
echo "[base] box main.py md5=$BOXMAIN expect=$EXPECT_MAIN"
[ "$BOXMAIN" = "$EXPECT_MAIN" ] || { echo "[ABORT] box not on prod-live 110d735c base"; exit 2; }

# --- gate: staged pins present + sha256 matches what we generated ---
[ -r "$PINS" ] || { echo "[ABORT] pins file $PINS missing (stage it first)"; exit 2; }
GOTPINS=$(sha256sum "$PINS" | cut -d' ' -f1)
echo "[pins] sha256=$GOTPINS expect=$EXPECT_PINS"
[ "$GOTPINS" = "$EXPECT_PINS" ] || { echo "[ABORT] staged pins sha256 mismatch"; exit 2; }

# --- gate: venv at pre-2c1 baseline ---
g(){ "$PIP" show "$1" 2>/dev/null | awk '/^Version:/{print $2}'; }
BA=$(g aiohttp); BY=$(g yarl); BF=$(g fastapi); BS=$(g starlette); BO=$(g opentelemetry-api)
echo "[base] venv now: aiohttp=$BA yarl=$BY fastapi=$BF starlette=$BS opentelemetry-api=${BO:-absent}"
if [ "$BA" != "3.13.5" ] || [ "$BY" != "1.23.0" ] || [ "$BF" != "0.136.1" ] || [ "$BS" != "1.0.1" ] || [ -n "$BO" ]; then
  echo "[ABORT] venv not at expected 2c-1 baseline"; exit 2; fi

# --- gate: quiet-60s ---
PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
LASTORD=$(sqlite3 "$PMDB" "SELECT CAST((strftime('%s','now') - MAX(submitted_ts)) AS INT) FROM pm_subdivision_order WHERE dry_run=0;" 2>/dev/null)
echo "[quiet] seconds since last real PM order = ${LASTORD:-unknown}"
if [ -n "$LASTORD" ] && [ "$LASTORD" -ge 0 ] 2>/dev/null && [ "$LASTORD" -lt 60 ] 2>/dev/null; then echo "[ABORT] order <60s ago"; exit 3; fi

# --- backup: full freeze + pip-check BEFORE ---
"$PIP" freeze 2>/dev/null | sort > "$BK/freeze.pre.txt"
"$PIP" check > "$BK/pipcheck.pre.txt" 2>&1 || true
echo "[backup] freeze.pre ($(wc -l < "$BK/freeze.pre.txt") pkgs) + pip-check captured -> $BK"

restore_abort(){ echo "[RESTORE] $1"; "$PIP" install --no-deps -r "$BK/freeze.pre.txt" 2>&1 | tail -4; echo "[ABORT] restored from pre-freeze; nothing restarted"; exit 4; }

# --- install: hash-verified, no transitive pulls ---
echo "[install] pip install --require-hashes --no-deps -r $PINS ..."
"$PIP" install --require-hashes --no-deps -r "$PINS" 2>&1 | tail -12 || restore_abort "pip install failed"

# --- verify: target versions + nothing else touched ---
NA=$(g aiohttp); NY=$(g yarl); NF=$(g fastapi); NS=$(g starlette); NO=$(g opentelemetry-api)
NC=$(g cryptography); NM=$(g msal); NJ=$(g pyjwt); NU=$(g urllib3); NPI=$(g pillow)
echo "[verify] aiohttp=$NA yarl=$NY fastapi=$NF starlette=$NS otel=$NO | UNCHANGED crypto=$NC msal=$NM pyjwt=$NJ urllib3=$NU pillow=$NPI"
if [ "$NA" != "3.14.5" ] || [ "$NY" != "1.25.1" ] || [ "$NF" != "0.165.0" ] || [ "$NS" != "1.10.0" ] || [ "$NO" != "1.45.2" ] \
   || [ "$NC" != "47.0.0" ] || [ "$NM" != "1.36.0" ] || [ "$NJ" != "2.15.1" ] || [ "$NU" != "2.8.0" ] || [ "$NPI" != "12.3.0" ]; then
  restore_abort "versions off / collateral change"; fi

# --- freeze-diff gate: ONLY the expected package names may differ ---
"$PIP" freeze 2>/dev/null | sort > "$BK/freeze.post.txt"
CHG=$(diff "$BK/freeze.pre.txt" "$BK/freeze.post.txt" | grep -E '^[<>]' | sed -E 's/^[<>] //; s/==.*//; s/ .*//' | tr 'A-Z' 'a-z' | sed 's/_/-/g' | sort -u)
EXTRA=$(comm -23 <(printf '%s\n' $CHG) <(printf '%s\n' $EXPECT_CHANGED | tr ' ' '\n' | sort -u))
echo "[freeze-diff] changed names: $(printf '%s' "$CHG" | tr '\n' ' ')"
[ -z "$EXTRA" ] || restore_abort "unexpected package(s) changed: $(printf '%s' "$EXTRA" | tr '\n' ' ')"

# --- pip-check diff: no NEW conflict vs before ---
"$PIP" check > "$BK/pipcheck.post.txt" 2>&1 || true
NEWC=$(comm -13 <(sort "$BK/pipcheck.pre.txt") <(sort "$BK/pipcheck.post.txt") | grep -viE '^No broken' || true)
[ -z "$NEWC" ] || { echo "[NEW pip-check conflict]"; echo "$NEWC" | sed 's/^/    /'; restore_abort "new pip-check conflict"; }
echo "[verify] versions correct; only expected names changed; no NEW pip-check conflict"

# --- restart the 4 shared-venv services once each ---
for u in $RESTART; do
  OLD=$(systemctl show $u -p MainPID --value 2>/dev/null)
  echo "[restart] $u old PID=$OLD ..."; systemctl restart $u; sleep 6
  echo "[restart] $u new PID=$(systemctl show $u -p MainPID --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"
done
echo "=== BATCH2C-1 DEPLOY DONE $TS -- backups in $BK ==="
echo "RUN secrev_batch2c1_verify.ps1 next (new PIDs x4 / versions loaded / healthz / tracebacks / URL click-list)."
