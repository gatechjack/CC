echo "=== MACE PT mark-guard FOCUSED verify (new tests must PASS) ==="
S=/tmp/mace_guard_scratch
rm -rf "$S"; mkdir -p "$S"
tar xzf /tmp/mace_guard_scratch.tar.gz -C "$S" || { echo "tar-extract FAILED"; exit 2; }
PY=$(systemctl show -p ExecStart --value trading-corp 2>/dev/null | grep -oE '/[^ ;]*python[0-9.]*' | head -1)
[ -x "$PY" ] || PY=/home/azureuser/trading_corp/venv/bin/python
cd "$S" || exit 2
echo "=== new guard tests (verbose) ==="
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_mark_guard.py tests/test_mace_strategy_manage.py -p no:pytest_ethereum -v > /tmp/mgv.out 2>&1
RC=$?
grep -E 'guard|sibling|PASSED|FAILED|ERROR|passed|failed|error' /tmp/mgv.out | tail -60
echo "=== focused pytest exit: $RC (0 = all pass) ==="
rm -rf "$S" /tmp/mace_guard_scratch.tar.gz /tmp/mgv.out
echo "=== END FOCUSED VERIFY ==="
