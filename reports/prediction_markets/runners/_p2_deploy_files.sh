#!/usr/bin/env bash
# PM P2 CP2-Ph2 FILE DEPLOY v2 -- targets the REAL package at the DOUBLE path
# ($ROOT/trading_corp/prediction_markets, where `import trading_corp.*` resolves; PYTHONPATH=$ROOT).
# The v1 run wrongly extracted to $ROOT/prediction_markets (single); this cleans that stray tree (guarded),
# deploys the 8 files to the real path as azureuser (born azureuser-owned), verifies sha + GOTCHA-2 gate over
# ONLY the deployed artifacts, and clears __pycache__ so the restart recompiles. exit 2 on any failure => the
# .ps1 ABORTS before the restart. Backs up the 2 overwritten files first.
set -u
echo "=== PM P2 CP2-Ph2 FILE DEPLOY v2 (DOUBLE path) start ==="; date -u; echo "whoami=$(whoami)"
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"           # repo root on the box (== CWD + PYTHONPATH of pm_web)
PKG="$ROOT/trading_corp"         # the real package dir
WEB="$PKG/prediction_markets/web"
STATS="$PKG/prediction_markets/stats.py"
STAGE="$H/pm_p2_stage.tgz"
BAK="$H/pm_p2_ph2_bak_$(date +%Y%m%d_%H%M%S)"
[ -f "$STAGE" ] || { echo "MISSING tarball $STAGE"; exit 2; }
[ -f "$PKG/prediction_markets/__init__.py" ] || { echo "REAL package not found at $PKG/prediction_markets -- ABORT"; exit 2; }

echo "--- [0] guarded cleanup of the STRAY single-path tree left by the v1 botched deploy ---"
STRAY="$ROOT/prediction_markets"   # WRONG path; real pkg is $PKG/prediction_markets
if [ -d "$STRAY" ] && [ ! -f "$STRAY/ingest.py" ] && [ -f "$STATS" ]; then
  echo "  removing $STRAY (guards ok: no ingest.py in stray + real stats.py present)"; rm -rf "$STRAY"
  [ -e "$STRAY" ] && echo "  STRAY_STILL_THERE=BAD" || echo "  STRAY_REMOVED=OK"
else
  echo "  SKIP (stray_dir=$([ -d "$STRAY" ]&&echo y||echo n) stray_has_ingest=$([ -f "$STRAY/ingest.py" ]&&echo y||echo n) real_stats=$([ -f "$STATS" ]&&echo y||echo n))"
fi

echo "--- [1] tarball sha256 (must equal LOCAL f640c2bf...) ---"; sha256sum "$STAGE"

echo "--- [2] backup the 2 REAL files being OVERWRITTEN (double path) ---"
mkdir -p "$BAK/prediction_markets/web"
cp -p "$STATS" "$BAK/prediction_markets/stats.py" && echo "  backed up stats.py ($(wc -c <"$STATS") bytes)"
cp -p "$WEB/app.py" "$BAK/prediction_markets/web/app.py" && echo "  backed up app.py ($(wc -c <"$WEB/app.py") bytes)"
echo "  BACKUP_DIR=$BAK"

echo "--- [3] extract Phase-2 subset into the REAL tree (-C \$ROOT => \$ROOT/trading_corp/... = double; --no-same-owner) ---"
tar -xzf "$STAGE" -C "$ROOT" --no-same-owner \
  trading_corp/prediction_markets/stats.py \
  trading_corp/prediction_markets/web/app.py \
  trading_corp/prediction_markets/web/templates \
  trading_corp/prediction_markets/web/static && echo "  extract OK"

echo "--- [4] modes (dirs 755, files 644) + clear __pycache__ so restart recompiles ---"
find "$WEB/templates" "$WEB/static" -type d -exec chmod 755 {} +
find "$WEB/templates" "$WEB/static" -type f -exec chmod 644 {} +
chmod 644 "$STATS" "$WEB/app.py"
find "$PKG/prediction_markets" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; echo "  __pycache__ cleared"

echo "--- [5] per-file sha256(12) vs LOCAL refs (mismatch => exit 2) ---"
SHAFAIL=0
check(){ local want="$1" f="$2" got; got=$(cd "$ROOT" && sha256sum "$f" 2>/dev/null | cut -c1-12); if [ "$got" = "$want" ]; then echo "  OK   $got  $f"; else echo "  FAIL got=$got want=$want  $f"; SHAFAIL=1; fi; }
check e8cd1f979094 trading_corp/prediction_markets/stats.py
check 2b07add5342c trading_corp/prediction_markets/web/app.py
check 7e69610cbf90 trading_corp/prediction_markets/web/templates/pm_base.html
check f8b74472875e trading_corp/prediction_markets/web/templates/pm_macros.html
check 2e806a512fe9 trading_corp/prediction_markets/web/templates/pm_scoreboard.html
check bb332eb7c070 trading_corp/prediction_markets/web/templates/partials/pm_scoreboard_table.html
check aa2dbfa449f8 trading_corp/prediction_markets/web/static/pm.css
check 491955cd1810 trading_corp/prediction_markets/web/static/htmx.min.js

echo "--- [6] GOTCHA-2 gate over ONLY the deployed artifacts (templates/, static/, app.py, stats.py) ---"
BAD_OWNER=$(find "$WEB/templates" "$WEB/static" "$WEB/app.py" "$STATS" ! -user azureuser 2>/dev/null | wc -l)
WORLD_W=$(find "$WEB/templates" "$WEB/static" "$WEB/app.py" "$STATS" -perm -o+w 2>/dev/null | wc -l)
NON755_DIR=$(find "$WEB/templates" "$WEB/static" -type d ! -perm 755 2>/dev/null | wc -l)
NON644_FILE=$(find "$WEB/templates" "$WEB/static" "$WEB/app.py" "$STATS" -type f ! -perm 644 2>/dev/null | wc -l)
echo "  bad_owner=$BAD_OWNER world_writable=$WORLD_W non755_dirs=$NON755_DIR non644_files=$NON644_FILE"
echo "  (context) deployed tree:"; find "$WEB/templates" "$WEB/static" "$WEB/app.py" "$STATS" -printf '    %M %u:%g %p\n' 2>/dev/null | sort -k3

echo "--- [7] VERDICT ---"
if [ "$SHAFAIL" = 0 ] && [ "$BAD_OWNER" = 0 ] && [ "$WORLD_W" = 0 ] && [ "$NON755_DIR" = 0 ] && [ "$NON644_FILE" = 0 ]; then
  rm -f "$STAGE"; echo "DEPLOY_FILES_OK=1 (staged tarball removed; backup at $BAK)"; echo "=== FILE DEPLOY v2 (done, clean) ==="; exit 0
else
  echo "DEPLOY_FILES_OK=0 (sha=$SHAFAIL owner=$BAD_OWNER ww=$WORLD_W dir=$NON755_DIR file=$NON644_FILE) -- ABORT before restart"
  echo "=== FILE DEPLOY v2 (done, FAILED) ==="; exit 2
fi
