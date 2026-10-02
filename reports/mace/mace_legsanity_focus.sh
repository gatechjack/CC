echo "=== FOCUSED: leg-sanity guard tests (-v) -- live tree UNTOUCHED ==="
S=/tmp/mace_guard_scratch
rm -rf "$S"; mkdir -p "$S"
tar xzf /tmp/mace_guard_scratch.tar.gz -C "$S" || { echo "tar FAILED"; exit 2; }
PY=/home/azureuser/trading_corp/venv/bin/python
cd "$S" || exit 2
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_mark_guard.py tests/test_mace_strategy_manage.py -p no:pytest_ethereum -v 2>&1 | tail -60
rm -rf "$S" /tmp/mace_guard_scratch.tar.gz
echo "=== END FOCUSED ==="
