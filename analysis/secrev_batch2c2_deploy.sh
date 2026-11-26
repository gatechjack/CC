#!/usr/bin/env bash
# secrev_batch2c2_deploy.sh -- BATCH 2c-2 (cryptography auth-path group). JACK RUNS (az vm run-command, root).
# Run ONLY after 2c-1 is green 30+ min AND Jack confirmed dashboard click-through.
# Installs EXACTLY these, hash-verified, no transitive pulls:
#   cryptography 47.0.0->50.0.2   msal 1.36.0->1.39.0  (msal 1.36.0 pins cryptography<49 -> required companion)
# azure-identity/azure-core/azure-keyvault-secrets must stay UNCHANGED.
# Base = post-2c-1 box (git base = 2c-1 deploy_log commit; proven here by 2c-1 versions being present).
set -u
exec > >(tee /home/azureuser/secrev_batch2c2_lastrun.log) 2>&1
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
BK=/home/azureuser/secrev_batch2c2_backup_$TS
PIP="$ROOT/venv/bin/pip"
PINS=/home/azureuser/secrev_batch2c2_pins.txt
EXPECT_MAIN=6e52c9bd1a36eb1bc801c5772f4178c0
EXPECT_PINS=ca13eaf20f6fa44c4cd5c993c8d159b55ea2cca1aec633a0f0775ee0eda06d26
EXPECT_CHANGED="cryptography msal"
RESTART="trading-corp prediction-markets-web sfp-card-watcher market-context-recorder"
PROTECTED="$RESTART caddy authelia"
mkdir -p "$BK"
echo "=== BATCH2C-2 DEPLOY START $TS host=$(hostname) ==="
for u in $PROTECTED; do echo "  $u MainPID=$(systemctl show $u -p MainPID --value 2>/dev/null) NRestarts=$(systemctl show $u -p NRestarts --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"; done

BOXMAIN=$(tr -d '\r' < "$ROOT/trading_corp/main.py" | md5sum | cut -c1-32)
echo "[base] box main.py md5=$BOXMAIN expect=$EXPECT_MAIN"
[ "$BOXMAIN" = "$EXPECT_MAIN" ] || { echo "[ABORT] box main.py md5 mismatch"; exit 2; }

[ -r "$PINS" ] || { echo "[ABORT] pins file $PINS missing"; exit 2; }
GOTPINS=$(sha256sum "$PINS" | cut -d' ' -f1)
echo "[pins] sha256=$GOTPINS expect=$EXPECT_PINS"
[ "$GOTPINS" = "$EXPECT_PINS" ] || { echo "[ABORT] staged pins sha256 mismatch"; exit 2; }

g(){ "$PIP" show "$1" 2>/dev/null | awk '/^Version:/{print $2}'; }
# gate: 2c-1 MUST already be applied (proves ordering) AND crypto/msal still baseline
BA=$(g aiohttp); BF=$(g fastapi); BS=$(g starlette); BO=$(g opentelemetry-api); BC=$(g cryptography); BM=$(g msal)
echo "[base] venv: aiohttp=$BA fastapi=$BF starlette=$BS otel=$BO | cryptography=$BC msal=$BM"
if [ "$BA" != "3.14.5" ] || [ "$BF" != "0.165.0" ] || [ "$BS" != "1.10.0" ] || [ "$BO" != "1.45.2" ]; then
  echo "[ABORT] 2c-1 not applied (base must be post-2c-1)"; exit 2; fi
if [ "$BC" != "47.0.0" ] || [ "$BM" != "1.36.0" ]; then echo "[ABORT] cryptography/msal not at 2c-2 baseline"; exit 2; fi

PMDB="file:$ROOT/data/prediction_markets.db?mode=ro"
LASTORD=$(sqlite3 "$PMDB" "SELECT CAST((strftime('%s','now') - MAX(submitted_ts)) AS INT) FROM pm_subdivision_order WHERE dry_run=0;" 2>/dev/null)
echo "[quiet] seconds since last real PM order = ${LASTORD:-unknown}"
if [ -n "$LASTORD" ] && [ "$LASTORD" -ge 0 ] 2>/dev/null && [ "$LASTORD" -lt 60 ] 2>/dev/null; then echo "[ABORT] order <60s ago"; exit 3; fi

"$PIP" freeze 2>/dev/null | sort > "$BK/freeze.pre.txt"
"$PIP" check > "$BK/pipcheck.pre.txt" 2>&1 || true
echo "[backup] freeze.pre ($(wc -l < "$BK/freeze.pre.txt") pkgs) -> $BK"

restore_abort(){ echo "[RESTORE] $1"; "$PIP" install --no-deps -r "$BK/freeze.pre.txt" 2>&1 | tail -4; echo "[ABORT] restored; nothing restarted"; exit 4; }

echo "[install] pip install --require-hashes --no-deps -r $PINS ..."
"$PIP" install --require-hashes --no-deps -r "$PINS" 2>&1 | tail -12 || restore_abort "pip install failed"

NC=$(g cryptography); NM=$(g msal); NAI=$(g azure-identity); NACO=$(g azure-core); NKV=$(g azure-keyvault-secrets)
echo "[verify] cryptography=$NC msal=$NM | UNCHANGED azure-identity=$NAI azure-core=$NACO azure-keyvault-secrets=$NKV"
if [ "$NC" != "50.0.2" ] || [ "$NM" != "1.39.0" ] || [ "$NAI" != "1.25.3" ] || [ "$NACO" != "1.39.0" ] || [ "$NKV" != "4.11.0" ]; then
  restore_abort "versions off / collateral azure change"; fi

"$PIP" freeze 2>/dev/null | sort > "$BK/freeze.post.txt"
CHG=$(diff "$BK/freeze.pre.txt" "$BK/freeze.post.txt" | grep -E '^[<>]' | sed -E 's/^[<>] //; s/==.*//; s/ .*//' | tr 'A-Z' 'a-z' | sed 's/_/-/g' | sort -u)
EXTRA=$(comm -23 <(printf '%s\n' $CHG) <(printf '%s\n' $EXPECT_CHANGED | tr ' ' '\n' | sort -u))
echo "[freeze-diff] changed names: $(printf '%s' "$CHG" | tr '\n' ' ')"
[ -z "$EXTRA" ] || restore_abort "unexpected package(s) changed: $(printf '%s' "$EXTRA" | tr '\n' ' ')"

"$PIP" check > "$BK/pipcheck.post.txt" 2>&1 || true
NEWC=$(comm -13 <(sort "$BK/pipcheck.pre.txt") <(sort "$BK/pipcheck.post.txt") | grep -viE '^No broken' || true)
[ -z "$NEWC" ] || { echo "[NEW pip-check conflict]"; echo "$NEWC" | sed 's/^/    /'; restore_abort "new pip-check conflict"; }
echo "[verify] versions correct; only expected names changed; no NEW pip-check conflict"

for u in $RESTART; do
  OLD=$(systemctl show $u -p MainPID --value 2>/dev/null)
  echo "[restart] $u old PID=$OLD ..."; systemctl restart $u; sleep 6
  echo "[restart] $u new PID=$(systemctl show $u -p MainPID --value 2>/dev/null) active=$(systemctl is-active $u 2>/dev/null)"
done
echo "=== BATCH2C-2 DEPLOY DONE $TS -- backups in $BK ==="
echo "RUN secrev_batch2c2_verify.ps1 next (Key Vault fetch burst / 0 msal+azure errors / broker auth RH+Bitunix+Kalshi x4)."
