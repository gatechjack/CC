set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
SCRATCH=/home/azureuser/pm_farmsearch_scratch_$TS
V=$ROOT/venv/bin/python
TAR=/home/azureuser/pm_farmsearch_overlay.tar
echo "### FARM-SEARCH BOX-SCRATCH (READ-ONLY; isolated scratch tree; the LIVE tree is never touched) $TS ###"
echo "engine PID (must be UNTOUCHED): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
[ -f "$TAR" ] || { echo "  ** overlay tar MISSING -- abort"; exit 2; }
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
tar xf "$TAR" -C "$SCRATCH"
echo "  scratch tree extracted: $(du -sh "$SCRATCH" 2>/dev/null | cut -f1)"

echo
echo "### [1] PYTEST -- my change surface, on the BOX venv (pykalshi/httpx/fastapi present; -p no:pytest_ethereum) ###"
TESTS="test_search_lock test_cli_search test_search_web test_search_r1 test_search_r3 test_search_run_r2 test_cli test_m4_gates test_web_healthz test_farm"
FILES=""
for t in $TESTS; do f="$SCRATCH/tests/prediction_markets/$t.py"; [ -f "$f" ] && FILES="$FILES tests/prediction_markets/$t.py"; done
cd "$SCRATCH" && PYTHONPATH="$SCRATCH" "$V" -m pytest $FILES -p no:pytest_ethereum -q -p no:cacheprovider 2>&1 | tail -35

echo
echo "### [2] INVOKABILITY -- transitive imports resolve in the box service env (the Gate-A lesson) ###"
cd "$SCRATCH"
NHELP=$(PYTHONPATH="$SCRATCH" "$V" trading_corp/scripts/pm_cli.py --help 2>&1 | grep -c 'search')
echo "  pm_cli --help mentions 'search' on $NHELP line(s) (expect >= 1)"
PYTHONPATH="$SCRATCH" "$V" trading_corp/scripts/pm_cli.py search --help >/dev/null 2>&1; echo "  pm_cli search --help exit=$? (expect 0 -- import graph resolves)"
echo "  bucket-reject smoke (pm_cli search --category mlb must FAIL LOUD, exit 2, not a clean run):"
PYTHONPATH="$SCRATCH" "$V" trading_corp/scripts/pm_cli.py --db /tmp/pm_fs_smoke_$TS.db search --category mlb >/tmp/pm_fs_out_$TS 2>&1; RC=$?
head -3 /tmp/pm_fs_out_$TS | sed 's/^/    /'
echo "    exit=$RC (expect 2)"
rm -f /tmp/pm_fs_smoke_$TS.db /tmp/pm_fs_out_$TS

echo
echo "### [3] CLEANUP ###"
rm -rf "$SCRATCH" "$TAR"
echo "  engine PID (UNTOUCHED throughout): $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "### DONE ###"
