echo "=== MACE PT mark-guard BOX-SCRATCH (live tree UNTOUCHED) ==="
S=/tmp/mace_guard_scratch
rm -rf "$S"; mkdir -p "$S"
tar xzf /tmp/mace_guard_scratch.tar.gz -C "$S" || { echo "tar-extract FAILED"; exit 2; }
PY=$(systemctl show -p ExecStart --value trading-corp 2>/dev/null | grep -oE '/[^ ;]*python[0-9.]*' | head -1)
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  for c in /home/azureuser/trading_corp/.venv/bin/python /home/azureuser/trading_corp/venv/bin/python /home/azureuser/.venv/bin/python; do
    [ -x "$c" ] && PY="$c" && break
  done
fi
echo "PY=$PY"; "$PY" --version 2>&1
cd "$S" || { echo "cd scratch FAILED"; exit 2; }
echo "=== 1) config parse of the NEW management.mark_guard block ==="
PYTHONPATH="$S" "$PY" -c "from trading_corp.mace.config import load_mace_config as L; c=L('config/mace.yaml', exdiv_calendar_path='config/ex_dividend_calendar.yaml'); print('scratch config_hash[:12]=', c.config_hash[:12], '(deploy LF hash = 931a8214be50)'); print('mark_guard=', c.management.mark_guard)" 2>&1 | tail -6
echo "=== 2) MACE test suite (-p no:pytest_ethereum) ==="
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_*.py -p no:pytest_ethereum -q > /tmp/mace_guard_pytest.out 2>&1
RC=$?
tail -45 /tmp/mace_guard_pytest.out
echo "=== pytest exit code: $RC (0 = all pass) ==="
rm -rf "$S" /tmp/mace_guard_scratch.tar.gz /tmp/mace_guard_pytest.out
echo "=== END BOX-SCRATCH ==="
