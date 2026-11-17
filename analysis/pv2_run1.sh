echo "=== PV2 BOX-SCRATCH RUN: stage + sha-gate + A/B pytest with FULL-LIST DIFF (no live-tree writes) ==="
date -u +%FT%TZ
set -u
ROOT=/home/azureuser/trading_corp
S=/home/azureuser/pv2_scratch
TGZ=/home/azureuser/pv2_stage.tgz

echo "--- [0] df pre-guard ---"
df -h / | tail -1
AVAIL=$(df --output=avail -k / | tail -1 | tr -d ' ')
if [ "$AVAIL" -lt 2000000 ]; then echo "ABORT: <2G free"; exit 2; fi

echo "--- [1] unpack scratch ---"
rm -rf "$S"; mkdir -p "$S"
tar xzf "$TGZ" -C "$S" || { echo "ABORT: untar failed"; exit 3; }

echo "--- [2] sha-gate: staged files == TARGET (CR-stripped sha256-16) ---"
fail=0
check() { a=$(tr -d '\r' < "$S/$1" | sha256sum | cut -c1-16); if [ "$a" != "$2" ]; then echo "SHA MISMATCH $1 got=$a want=$2"; fail=1; else echo "ok $1 $a"; fi; }
check trading_corp/data/polymarket_data_api_client.py c88cfce6a16cc4a0
check trading_corp/prediction_markets/loss_grounding.py 42d166575b2f39bd
check tests/test_polymarket_data_api_v2_migration.py 2e0c480564089773
check tests/prediction_markets/test_v2_matcher_equivalence.py f9df2c447cbc448a
if [ "$fail" != "0" ]; then echo "ABORT: stage gate failed"; exit 4; fi

echo "--- [3] BASE copies from the LIVE tree (read-only source) + gate them ---"
mkdir -p "$S/_base" "$S/_target"
cp "$ROOT/trading_corp/data/polymarket_data_api_client.py" "$S/_base/client.py"
cp "$ROOT/trading_corp/prediction_markets/loss_grounding.py" "$S/_base/loss_grounding.py"
b1=$(tr -d '\r' < "$S/_base/client.py" | sha256sum | cut -c1-16)
b2=$(tr -d '\r' < "$S/_base/loss_grounding.py" | sha256sum | cut -c1-16)
echo "base client=$b1 (want ad9a31922becfbe1) loss_grounding=$b2 (want ce22b4d26de6faf4)"
if [ "$b1" != "ad9a31922becfbe1" ] || [ "$b2" != "ce22b4d26de6faf4" ]; then echo "ABORT: live tree != BASE"; exit 5; fi
cp "$S/trading_corp/data/polymarket_data_api_client.py" "$S/_target/client.py"
cp "$S/trading_corp/prediction_markets/loss_grounding.py" "$S/_target/loss_grounding.py"

PY=$ROOT/venv/bin/python
# NOTE: tests/test_backtest_polymarket_arbitrage.py is EXCLUDED: it loads
# scripts/backtest_polymarket_arbitrage.py by path, which does NOT exist on the
# prod-live lineage (verified) -- it can never collect here, identically in A and B.
SUITE="tests/prediction_markets tests/test_polymarket_data_api_client_retry.py tests/test_polymarket_whale_audit.py tests/test_polymarket_metrics_epoch.py tests/test_polymarket_per_division_wallets.py tests/test_polymarket_watch_only_sort.py"

echo "--- [4] RUN A (BASE files; existing suite only) ---"
cp "$S/_base/client.py" "$S/trading_corp/data/polymarket_data_api_client.py"
cp "$S/_base/loss_grounding.py" "$S/trading_corp/prediction_markets/loss_grounding.py"
cd "$S"
PYTHONPATH=. "$PY" -m pytest -p no:pytest_ethereum --continue-on-collection-errors -q $SUITE --ignore=tests/prediction_markets/test_v2_matcher_equivalence.py > "$S/runA.txt" 2>&1
echo "run A exit=$? counts: $(grep -E '^[0-9]+ (passed|failed)|failed,|passed,' "$S/runA.txt" | tail -1)"
tail -2 "$S/runA.txt"

echo "--- [5] RUN B (TARGET files; existing suite + both new test files) ---"
cp "$S/_target/client.py" "$S/trading_corp/data/polymarket_data_api_client.py"
cp "$S/_target/loss_grounding.py" "$S/trading_corp/prediction_markets/loss_grounding.py"
PYTHONPATH=. "$PY" -m pytest -p no:pytest_ethereum --continue-on-collection-errors -q $SUITE tests/test_polymarket_data_api_v2_migration.py > "$S/runB.txt" 2>&1
echo "run B exit=$? counts: $(grep -E '^[0-9]+ (passed|failed)|failed,|passed,' "$S/runB.txt" | tail -1)"
tail -2 "$S/runB.txt"

echo "--- [6] A/B DELTA (FULL lists; B-only failures below are the regressions to explain) ---"
grep "^FAILED" "$S/runA.txt" | sort > "$S/failA.txt"
grep "^FAILED" "$S/runB.txt" | sort > "$S/failB.txt"
echo "A failures: $(wc -l < "$S/failA.txt") ; B failures: $(wc -l < "$S/failB.txt")"
echo "-- B-only (regressions OR new-test failures) --"
comm -13 "$S/failA.txt" "$S/failB.txt"
echo "-- A-only (fixed by TARGET, unexpected) --"
comm -23 "$S/failA.txt" "$S/failB.txt"

echo "--- [7] new-test results detail ---"
grep -E "test_polymarket_data_api_v2_migration|test_v2_matcher_equivalence" "$S/runB.txt" | grep -E "FAILED|ERROR" || echo "ALL NEW TESTS PASSED"

echo "--- [8] py_compile + import closure on TARGET ---"
"$PY" -m py_compile "$S/trading_corp/data/polymarket_data_api_client.py" "$S/trading_corp/prediction_markets/loss_grounding.py" && echo "py_compile OK"
cd "$S" && PYTHONPATH=. "$PY" -c "import trading_corp.data.polymarket_data_api_client as m; import trading_corp.prediction_markets.loss_grounding; import trading_corp.prediction_markets.live_driver; print('import closure OK;', m._DATA_API_V2)"
echo "=== END RUN (scratch kept at $S) ==="
