set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
DB=$ROOT/data/prediction_markets.db
NEW=/tmp/leg_audit_new.py
echo "### CLASSIFIER PARITY (old deployed vs new) over LIVE DB verdicts -- READ-ONLY $(date -u +%Y-%m-%dT%H:%M:%SZ) ###"
echo "engine PID (UNTOUCHED): $(systemctl show -p MainPID --value trading-corp 2>/dev/null) ; pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
[ -f "$NEW" ] || { echo "  ** new leg_audit.py MISSING -- abort"; exit 2; }
cd "$ROOT" && PM_DB="$DB" NEWFILE="$NEW" PYTHONPATH="$ROOT" "$V" - <<'PY'
import os, sqlite3, importlib.util
from trading_corp.prediction_markets.leg_audit import classify_leg_audit as OLD   # box-deployed (old)
spec = importlib.util.spec_from_file_location("leg_audit_new", os.environ["NEWFILE"])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
NEW = mod.classify_leg_audit                                                       # my new version
c = sqlite3.connect("file:%s?mode=ro" % os.environ["PM_DB"], uri=True)
rows = c.execute("SELECT leg_audit, COUNT(*) n FROM pm_subdivision_order WHERE dry_run=0 "
                 "GROUP BY leg_audit ORDER BY n DESC").fetchall()
print("DISTINCT live leg_audit verdicts (dry_run=0), old-vs-new classification:")
allmatch = True; alias_present = 0
for v, n in rows:
    o = OLD(v); w = NEW(v); same = (o == w)
    if not same: allmatch = False
    if v == "ok:code_alias": alias_present = n
    print("  %-58r n=%-6d old=%-11s new=%-11s %s" % (v, n, o, w, "MATCH" if same else "*** DIFFER ***"))
print()
print("  ALL existing verdicts classify IDENTICALLY (old==new):", allmatch)
print("  'ok:code_alias' rows currently in live DB:", alias_present, "(expect 0 -- old engine never writes it)")
print("  the ONLY delta: old('ok:code_alias')=%s  ->  new('ok:code_alias')=%s" % (OLD("ok:code_alias"), NEW("ok:code_alias")))
c.close()
PY
echo
echo "REVERSE-DEP: which box files IMPORT the leg_audit module (must be pm_web only; NOT live_driver.py):"
grep -rlE "import leg_audit" --include=*.py "$ROOT/trading_corp" | sed "s#$ROOT/##"
echo "  live_driver.py imports leg_audit? -> $(grep -lE 'import leg_audit' "$ROOT/trading_corp/prediction_markets/live_driver.py" 2>/dev/null || echo NO)"
echo "### DONE (engine PID unchanged: $(systemctl show -p MainPID --value trading-corp 2>/dev/null)) ###"
