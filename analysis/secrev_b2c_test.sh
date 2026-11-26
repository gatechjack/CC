#!/usr/bin/env bash
# BATCH 2c item 6 -- run the full pytest suite on the prod-mirror venv and emit a
# compact summary + sorted FAILED/ERROR node-id list for baseline-vs-upgraded diffing.
# Usage: secrev_b2c_test.sh <label>
set -u
LABEL="${1:?need label}"
WT="/c/Users/AA Incorporado/cc-b2c-2026-11-25-wt"
PY="/c/Users/AA Incorporado/cc/b2c_mirror/Scripts/python.exe"
OUT="/c/Users/AA Incorporado/cc-secrev-2026-11-23-wt/analysis"
RAW="$OUT/31_test_${LABEL}.txt"
IDS="$OUT/31_test_${LABEL}.ids"

cd "$WT" || exit 2
echo "=== test run [$LABEL] start $(date -u) ===" | tee "$RAW"
echo "--- installed target versions ---" | tee -a "$RAW"
"$PY" -m pip freeze 2>/dev/null | grep -iE '^(cryptography|msal|aiohttp|yarl|fastapi|starlette|opentelemetry-api)==' | tee -a "$RAW"
echo "--- pip check ---" | tee -a "$RAW"
"$PY" -m pip check 2>&1 | tee -a "$RAW"
echo "--- pytest ---" | tee -a "$RAW"
PYTHONPATH="$WT" "$PY" -m pytest tests \
  -p no:pytest_ethereum --continue-on-collection-errors \
  --timeout=90 --timeout-method=thread \
  -q --tb=no -rfE 2>&1 | tee -a "$RAW"
echo "=== test run [$LABEL] end $(date -u) ===" | tee -a "$RAW"
# extract sorted node ids for failures + errors
grep -E '^(FAILED|ERROR) ' "$RAW" | sed -E 's/ +-.*$//' | sort -u > "$IDS"
echo "--- summary line ---"
grep -E '[0-9]+ (passed|failed|error)' "$RAW" | tail -1
echo "FAILED/ERROR count: $(wc -l < "$IDS")"
