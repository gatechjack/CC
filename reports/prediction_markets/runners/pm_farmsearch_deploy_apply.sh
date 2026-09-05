set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
PKG=$ROOT/trading_corp
STAGE=/home/azureuser/pm_farmsearch_stage
BK=/home/azureuser/pm_farmsearch_deploy_backup_$TS
V=$ROOT/venv/bin/python
PART=prediction_markets/web/templates/partials/pm_search_status.html
echo "### FARM-SEARCH DEPLOY -- APPLY (pm_web-only; NO migration; NO engine touch) $TS ###"
echo "engine PID (must be UNTOUCHED): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"

# ---- PRE-FLIGHT DRIFT CHECK: the box must match what the graft was built against ----
echo "## pre-flight drift check (CR-stripped sha256):"
DRIFT=0
chk(){ h=$(tr -d '\r' < "$PKG/$1" | sha256sum | cut -c1-16); if [ "$h" = "$2" ]; then echo "  OK    $1 = $h"; else echo "  DRIFT $1 = $h (expected $2)"; DRIFT=1; fi; }
chk prediction_markets/search_run.py 311beb68fa68ef7b
chk scripts/pm_cli.py 7ae2f219a1b3d358
chk prediction_markets/web/templates/pm_farm_league.html 92af020168a88ed0
chk prediction_markets/web/app.py c2e4ddef85b4460b
if [ -f "$PKG/$PART" ]; then echo "  DRIFT $PART already PRESENT"; DRIFT=1; else echo "  OK    pm_search_status.html absent"; fi
for s in trading_corp/prediction_markets/search_run.py trading_corp/scripts/pm_cli.py trading_corp/prediction_markets/web/templates/pm_farm_league.html trading_corp/prediction_markets/web/templates/partials/pm_search_status.html app_grafted.py; do
  [ -f "$STAGE/$s" ] || { echo "  ** staged file MISSING: $STAGE/$s"; DRIFT=1; }
done
[ "$DRIFT" = 0 ] || { echo "## ** DRIFT / MISSING STAGE -- ABORT. Nothing changed. Re-graft against the current box app.py."; exit 3; }

# ---- BACKUP (the 4 existing files; the new partial has no prior) ----
mkdir -p "$BK/prediction_markets/web/templates" "$BK/scripts"
cp -a "$PKG/prediction_markets/search_run.py" "$BK/prediction_markets/search_run.py"
cp -a "$PKG/scripts/pm_cli.py" "$BK/scripts/pm_cli.py"
cp -a "$PKG/prediction_markets/web/templates/pm_farm_league.html" "$BK/prediction_markets/web/templates/pm_farm_league.html"
cp -a "$PKG/prediction_markets/web/app.py" "$BK/prediction_markets/web/app.py"
echo "## backup -> $BK (DANGEROUS to restore blindly -- it reverts this deploy)"

restore(){ echo "  ...RESTORING from $BK";
  cp -a "$BK/prediction_markets/search_run.py" "$PKG/prediction_markets/search_run.py"
  cp -a "$BK/scripts/pm_cli.py" "$PKG/scripts/pm_cli.py"
  cp -a "$BK/prediction_markets/web/templates/pm_farm_league.html" "$PKG/prediction_markets/web/templates/pm_farm_league.html"
  cp -a "$BK/prediction_markets/web/app.py" "$PKG/prediction_markets/web/app.py"
  rm -f "$PKG/$PART"; }

# ---- APPLY (staged -> place, forced 644) ----
mkdir -p "$PKG/prediction_markets/web/templates/partials"
cp "$STAGE/trading_corp/prediction_markets/search_run.py" "$PKG/prediction_markets/search_run.py"
cp "$STAGE/trading_corp/scripts/pm_cli.py" "$PKG/scripts/pm_cli.py"
cp "$STAGE/trading_corp/prediction_markets/web/templates/pm_farm_league.html" "$PKG/prediction_markets/web/templates/pm_farm_league.html"
cp "$STAGE/trading_corp/prediction_markets/web/templates/partials/pm_search_status.html" "$PKG/$PART"
cp "$STAGE/app_grafted.py" "$PKG/prediction_markets/web/app.py"
chmod 644 "$PKG/prediction_markets/search_run.py" "$PKG/scripts/pm_cli.py" "$PKG/prediction_markets/web/templates/pm_farm_league.html" "$PKG/$PART" "$PKG/prediction_markets/web/app.py"

# ---- VERIFY deployed bytes == targets; else RESTORE ----
echo "## deployed hashes (must match targets):"
vchk(){ h=$(tr -d '\r' < "$PKG/$1" | sha256sum | cut -c1-16); if [ "$h" = "$2" ]; then echo "  OK    $1 = $h"; else echo "  BAD   $1 = $h (expected $2)"; return 1; fi; }
BAD=0
vchk prediction_markets/search_run.py a15acc3a7c28fc30 || BAD=1
vchk scripts/pm_cli.py b5cb0b91fa84683a || BAD=1
vchk prediction_markets/web/templates/pm_farm_league.html 3ccf80ddc42cae40 || BAD=1
vchk "$PART" 59b287dc51b24529 || BAD=1
vchk prediction_markets/web/app.py 34bb61ed92b7cd1b || BAD=1
P=$(stat -c '%a' "$PKG/prediction_markets/web/app.py"); echo "  app.py perms = $P (expect 644)"
[ "$P" = 644 ] || BAD=1
if [ "$BAD" != 0 ]; then echo "## ** DEPLOYED BYTES/PERMS MISMATCH -- RESTORING + ABORT"; restore; exit 4; fi

# ---- app.py M4/M5 marker sanity (graft must NOT have leaked M5) ----
IA=$(grep -cE 'is_admin' "$PKG/prediction_markets/web/app.py"); PA=$(grep -cE '/pm/arm' "$PKG/prediction_markets/web/app.py")
echo "## app.py markers: is_admin=$IA (expect 14 = M4 10 + 4 search) ; /pm/arm=$PA (expect 0 = M5 stays absent)"
if [ "$PA" != 0 ]; then echo "## ** M5 LEAK (/pm/arm present) -- RESTORING + ABORT"; restore; exit 5; fi

# ---- GATE-A: transitive imports resolve in the SERVICE dir (the R7.e lesson) ----
echo "## Gate-A (transitive imports, service dir):"
cd "$ROOT"
PYTHONPATH=. "$V" -c "import trading_corp.prediction_markets.web.app" >/tmp/pm_fs_gA1_$TS 2>&1; RC1=$?
PYTHONPATH=. "$V" trading_corp/scripts/pm_cli.py search --help >/tmp/pm_fs_gA2_$TS 2>&1; RC2=$?
echo "  import pm_web app exit=$RC1 ; pm_cli search --help exit=$RC2 (both expect 0)"
if [ "$RC1" != 0 ] || [ "$RC2" != 0 ]; then
  echo "## ** GATE-A FAILED -- a broken pm_web/CLI is an outage; RESTORING + ABORT"; sed 's/^/    gA1> /' /tmp/pm_fs_gA1_$TS; sed 's/^/    gA2> /' /tmp/pm_fs_gA2_$TS; restore; rm -f /tmp/pm_fs_gA1_$TS /tmp/pm_fs_gA2_$TS; exit 6
fi
rm -f /tmp/pm_fs_gA1_$TS /tmp/pm_fs_gA2_$TS
$V - <<PY 2>/dev/null
import sqlite3
c=sqlite3.connect("file:$ROOT/data/prediction_markets.db?mode=ro", uri=True)
print("  schema head:", c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], "(expect 19 -- NO migration ran)")
PY
echo "engine PID (UNTOUCHED): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
rm -rf "$STAGE"
echo "DEPLOY_APPLIED_OK backup=$BK"
echo "### APPLY DONE -- files in place, Gate-A green, NO restart yet (activation = pm_web restart, next step) ###"
