set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
BASE=/home/azureuser/pm_legaudit_base_$TS
STG=/home/azureuser/pm_legaudit_stg_$TS
TAR=/tmp/legaudit_overlay.tgz
echo "### LEG-AUDIT CODE-ALIAS BOX-SCRATCH DIFFERENTIAL (box venv; LIVE tree+engine+DB UNTOUCHED) $TS ###"
echo "engine PID (UNTOUCHED): $(systemctl show -p MainPID --value trading-corp 2>/dev/null) ; pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
[ -f "$TAR" ] || { echo "  ** overlay tar MISSING -- abort"; exit 2; }
rm -rf "$BASE" "$STG"; mkdir -p "$BASE"
cp -r "$ROOT/trading_corp" "$BASE/trading_corp"
[ -d "$ROOT/tests" ] && cp -r "$ROOT/tests" "$BASE/tests" || mkdir -p "$BASE/tests/prediction_markets"
[ -f "$ROOT/pyproject.toml" ] && cp "$ROOT/pyproject.toml" "$BASE/"
[ -f "$ROOT/conftest.py" ] && cp "$ROOT/conftest.py" "$BASE/"
cp -r "$BASE" "$STG"
tar xzf "$TAR" -C "$STG"    # overlay ONLY my changed files onto the STG copy
echo
echo "### [0] files that differ (baseline vs overlaid) -- must be exactly my 3 ###"
for f in trading_corp/prediction_markets/live_driver.py trading_corp/prediction_markets/leg_audit.py tests/prediction_markets/test_leg_audit_surface.py; do
  if diff -q "$BASE/$f" "$STG/$f" >/dev/null 2>&1; then echo "    SAME  $f"; else echo "    DIFF  $f"; fi
done
echo
echo "### [1] box-venv IMPORT of edited files + real-data smoke ###"
cd "$STG" && PYTHONPATH="$STG" "$V" - <<'PY'
from trading_corp.prediction_markets.live_driver import _audit_leg_independent, _LEG_AUDIT_CODE_ALIASES
from trading_corp.prediction_markets import leg_audit as LA
print("  import OK")
print("  alias table:", _LEG_AUDIT_CODE_ALIASES)
v = _audit_leg_independent("cs2", "Luminosity", "KXCS2GAME-26SEP171100NIPLG-LG", "yes")
print("  LG/Luminosity  verdict=%r -> state=%s" % (v, LA.classify_leg_audit(v)))
va = _audit_leg_independent("cs2", "NIP", "KXCS2GAME-26SEP171100NIPLG-LG", "yes")
print("  ANTI LG/NIP    verdict=%r -> state=%s" % (va, LA.classify_leg_audit(va)))
vn = _audit_leg_independent("cs2", "NIP", "KXCS2GAME-26SEP171100NIPLG-NIP", "yes")
print("  NIP/NIP        verdict=%r -> state=%s" % (vn, LA.classify_leg_audit(vn)))
vf = _audit_leg_independent("cs2", "magic", "KXCS2GAME-26SEP082300FAZEMGC-FAZE", "yes")
print("  FAZE/magic     verdict=%r -> state=%s (regression: still soft)" % (vf, LA.classify_leg_audit(vf)))
print("  classify(ok:code_alias)=%s ; classify(ok)=%s ; classify(unchecked)=%s" % (
      LA.classify_leg_audit("ok:code_alias"), LA.classify_leg_audit("ok"), LA.classify_leg_audit("unchecked")))
PY
echo
echo "### [2a] BASELINE pytest -- full tests/prediction_markets (pristine box tree) ###"
echo "  baseline has test_leg_audit_surface.py: $( [ -f "$BASE/tests/prediction_markets/test_leg_audit_surface.py" ] && echo YES || echo NO )"
cd "$BASE" && PYTHONPATH="$BASE" "$V" -m pytest tests/prediction_markets -p no:pytest_ethereum -p no:cacheprovider -q 2>&1 > /tmp/base_pt.txt; echo "  SUMMARY: $(tail -1 /tmp/base_pt.txt)"; echo "  FAILED-count: $(grep -c '^FAILED' /tmp/base_pt.txt)"
echo
echo "### [2b] OVERLAID pytest -- full tests/prediction_markets (my change) ###"
cd "$STG" && PYTHONPATH="$STG" "$V" -m pytest tests/prediction_markets -p no:pytest_ethereum -p no:cacheprovider -q 2>&1 > /tmp/stg_pt.txt; echo "  SUMMARY: $(tail -1 /tmp/stg_pt.txt)"; echo "  FAILED-count: $(grep -c '^FAILED' /tmp/stg_pt.txt)"
echo "  NEW failures in overlaid not in baseline (must be empty):"
comm -13 <(grep '^FAILED' /tmp/base_pt.txt | sort) <(grep '^FAILED' /tmp/stg_pt.txt | sort) | sed 's/^/    +/'
rm -f /tmp/base_pt.txt /tmp/stg_pt.txt
echo
echo "### [2c] OVERLAID pytest -- test_leg_audit_surface.py VERBOSE (my new + extended cases by name) ###"
cd "$STG" && PYTHONPATH="$STG" "$V" -m pytest tests/prediction_markets/test_leg_audit_surface.py -p no:pytest_ethereum -p no:cacheprovider -v 2>&1 | grep -E "PASSED|FAILED|ERROR|passed|failed|error" | tail -20
echo
echo "### [3] CLEANUP (scratch + tar removed; live tree/engine untouched) ###"
rm -rf "$BASE" "$STG" "$TAR"
echo "  engine PID (UNTOUCHED): $(systemctl show -p MainPID --value trading-corp 2>/dev/null) ; pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
echo "### LEG-AUDIT CODE-ALIAS BOX-SCRATCH DONE ###"
