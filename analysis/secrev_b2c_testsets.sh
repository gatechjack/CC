#!/usr/bin/env bash
# BATCH 2c item 6 -- run the full suite for each of the 3 library sets vs baseline.
# Install set -> full suite (via secrev_b2c_test.sh) -> revert to baseline -> assert -> next set.
set -u
PY="/c/Users/AA Incorporado/cc/b2c_mirror/Scripts/python.exe"
TEST="/c/Users/AA Incorporado/cc/secrev_b2c_test.sh"
g(){ "$PY" -m pip show "$1" 2>/dev/null | awk '/^Version:/{print $2}'; }
assert_baseline(){
  if [ "$(g cryptography)" = "47.0.0" ] && [ "$(g msal)" = "1.36.0" ] \
  && [ "$(g aiohttp)" = "3.13.5" ] && [ "$(g yarl)" = "1.23.0" ] \
  && [ "$(g fastapi)" = "0.136.1" ] && [ "$(g starlette)" = "1.0.1" ]; then
    echo "[baseline-OK] $1"
  else
    echo "[FATAL] venv not at baseline ($1): crypto=$(g cryptography) msal=$(g msal) aiohttp=$(g aiohttp) yarl=$(g yarl) fastapi=$(g fastapi) starlette=$(g starlette)"; exit 2
  fi
}
echo "### testsets start $(date -u)"
assert_baseline "pre"

echo "### SET A: cryptography 50.0.2 + msal 1.39.0"
"$PY" -m pip install -q "cryptography==50.0.2" "msal==1.39.0" 2>&1 | tail -3
bash "$TEST" set_a
"$PY" -m pip install -q "cryptography==47.0.0" "msal==1.36.0" 2>&1 | tail -3
assert_baseline "post-A"

echo "### SET B: aiohttp 3.14.5 + yarl 1.25.1"
"$PY" -m pip install -q "aiohttp==3.14.5" "yarl==1.25.1" 2>&1 | tail -3
bash "$TEST" set_b
"$PY" -m pip install -q "aiohttp==3.13.5" "yarl==1.23.0" 2>&1 | tail -3
assert_baseline "post-B"

echo "### SET C: fastapi 0.165.0 + starlette 1.10.0 (+opentelemetry-api)"
"$PY" -m pip install -q "fastapi==0.165.0" "starlette==1.10.0" 2>&1 | tail -3
bash "$TEST" set_c
"$PY" -m pip install -q "fastapi==0.136.1" "starlette==1.0.1" 2>&1 | tail -3
"$PY" -m pip uninstall -y -q opentelemetry-api 2>&1 | tail -1
assert_baseline "post-C"
echo "### testsets DONE $(date -u)"

echo "### DIFF vs baseline (new failures introduced by each set) ###"
OUT="/c/Users/AA Incorporado/cc-secrev-2026-11-23-wt/analysis"
for s in set_a set_b set_c; do
  if [ -f "$OUT/31_test_${s}.ids" ]; then
    NEW=$(comm -13 "$OUT/31_test_baseline.ids" "$OUT/31_test_${s}.ids" 2>/dev/null)
    GONE=$(comm -23 "$OUT/31_test_baseline.ids" "$OUT/31_test_${s}.ids" 2>/dev/null)
    echo "[$s] baseline_fails=$(wc -l < "$OUT/31_test_baseline.ids") ${s}_fails=$(wc -l < "$OUT/31_test_${s}.ids") NEW=$(printf '%s' "$NEW" | grep -c .) FIXED=$(printf '%s' "$GONE" | grep -c .)"
    [ -n "$NEW" ] && { echo "   NEW FAILURES:"; printf '%s\n' "$NEW" | sed 's/^/     /'; }
  fi
done