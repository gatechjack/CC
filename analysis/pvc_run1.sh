echo "=== PVC BOX-SCRATCH RUN: chase-rule A/B pytest (A = v2 TARGET tip dbbc24bf, B = v2+chase) ==="
date -u +%FT%TZ
set -u
ROOT=/home/azureuser/trading_corp
S=/home/azureuser/pvc_scratch
TGZ=/home/azureuser/pvc_stage.tgz

echo "--- [0] df pre-guard ---"
df -h / | tail -1
AVAIL=$(df --output=avail -k / | tail -1 | tr -d ' ')
if [ "$AVAIL" -lt 2000000 ]; then echo "ABORT: <2G free"; exit 2; fi

echo "--- [1] unpack scratch ---"
rm -rf "$S"; mkdir -p "$S"
tar xzf "$TGZ" -C "$S" || { echo "ABORT: untar failed"; exit 3; }

echo "--- [2] sha-gate: staged TARGET engine files (CR-stripped sha256-16) ---"
fail=0
check() { a=$(tr -d '\r' < "$S/$1" | sha256sum | cut -c1-16); if [ "$a" != "$2" ]; then echo "SHA MISMATCH $1 got=$a want=$2"; fail=1; else echo "ok $1 $a"; fi; }
check trading_corp/data/polymarket_data_api_client.py c88cfce6a16cc4a0
check trading_corp/prediction_markets/loss_grounding.py 42d166575b2f39bd
check trading_corp/prediction_markets/execution.py b4f75cc64793c3fb
check trading_corp/prediction_markets/live_driver.py 69dc1040abe53bc9
check trading_corp/prediction_markets/heartbeat.py 227eda723301c288
check trading_corp/prediction_markets/db.py a4d8ddfd2254830a
if [ "$fail" != "0" ]; then echo "ABORT: stage gate failed"; exit 4; fi

echo "--- [3] BASE engine copies from the LIVE tree (read-only source) + gate them (== dbbc24bf == box) ---"
mkdir -p "$S/_base"
for f in execution.py live_driver.py heartbeat.py db.py; do
  cp "$ROOT/trading_corp/prediction_markets/$f" "$S/_base/$f" || { echo "ABORT: base copy $f"; exit 5; }
done
bcheck() { a=$(tr -d '\r' < "$S/_base/$1" | sha256sum | cut -c1-16); if [ "$a" != "$2" ]; then echo "BASE DRIFT $1 got=$a want=$2"; fail=1; else echo "base ok $1 $a"; fi; }
bcheck execution.py a8a03b59e422a8a2
bcheck live_driver.py 4bbe3023610aaadc
bcheck heartbeat.py 57afdcc6c61055e2
bcheck db.py 49f4f6d8d53d8fc5
if [ "$fail" != "0" ]; then echo "ABORT: live tree drifted from BASE"; exit 5; fi
mkdir -p "$S/_target"
for f in execution.py live_driver.py heartbeat.py db.py; do
  cp "$S/trading_corp/prediction_markets/$f" "$S/_target/$f"
done
# TARGET copies of the FIVE chase-edited test files (restored for run B)
mkdir -p "$S/_target_tests"
for f in test_itf_match.py test_boxing_match.py test_f1_match.py test_attachment_span_history.py test_rung3_observability.py; do
  cp "$S/tests/prediction_markets/$f" "$S/_target_tests/$f"
done

PY=$ROOT/venv/bin/python
# tests/test_backtest_polymarket_arbitrage.py EXCLUDED (scripts/ file absent on prod-live lineage -- pv2 precedent)
SUITE="tests/prediction_markets tests/test_polymarket_data_api_client_retry.py tests/test_polymarket_whale_audit.py tests/test_polymarket_metrics_epoch.py tests/test_polymarket_per_division_wallets.py tests/test_polymarket_watch_only_sort.py tests/test_polymarket_data_api_v2_migration.py"

echo "--- [4] RUN A (BASE = the v2 tip: live-tree engine chase files + pre-chase test files; chase suite ignored) ---"
for f in execution.py live_driver.py heartbeat.py db.py; do
  cp "$S/_base/$f" "$S/trading_corp/prediction_markets/$f"
done
for f in test_itf_match.py test_boxing_match.py test_f1_match.py test_attachment_span_history.py test_rung3_observability.py; do
  cp "$S/_ab_base_tests/prediction_markets/$f" "$S/tests/prediction_markets/$f"
done
cd "$S"
PYTHONPATH=. "$PY" -m pytest -p no:pytest_ethereum --continue-on-collection-errors -q $SUITE --ignore=tests/prediction_markets/test_chase_gate.py > "$S/runA.txt" 2>&1
echo "run A exit=$? counts: $(grep -E '[0-9]+ (passed|failed)' "$S/runA.txt" | tail -1)"

echo "--- [5] RUN B (TARGET = v2+chase engine files + chase-edited tests + test_chase_gate.py) ---"
for f in execution.py live_driver.py heartbeat.py db.py; do
  cp "$S/_target/$f" "$S/trading_corp/prediction_markets/$f"
done
for f in test_itf_match.py test_boxing_match.py test_f1_match.py test_attachment_span_history.py test_rung3_observability.py; do
  cp "$S/_target_tests/$f" "$S/tests/prediction_markets/$f"
done
PYTHONPATH=. "$PY" -m pytest -p no:pytest_ethereum --continue-on-collection-errors -q $SUITE > "$S/runB.txt" 2>&1
echo "run B exit=$? counts: $(grep -E '[0-9]+ (passed|failed)' "$S/runB.txt" | tail -1)"

echo "--- [6] A/B DELTA (FULL lists; B-only failures are the regressions to explain) ---"
grep "^FAILED" "$S/runA.txt" | sort > "$S/failA.txt"
grep "^FAILED" "$S/runB.txt" | sort > "$S/failB.txt"
echo "A failures: $(wc -l < "$S/failA.txt")   B failures: $(wc -l < "$S/failB.txt")"
echo "-- B-only (REGRESSIONS):"
comm -13 "$S/failA.txt" "$S/failB.txt"
echo "-- A-only (fixed/absent in B):"
comm -23 "$S/failA.txt" "$S/failB.txt"
echo "--- [7] chase-suite verdict in B ---"
grep -E "test_chase_gate" "$S/runB.txt" | grep -E "FAILED|ERROR" || echo "test_chase_gate.py: ALL PASSED in run B"
echo "=== PVC A/B DONE (scratch kept at $S for inspection; clean at session close) ==="
