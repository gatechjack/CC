echo "=== MACE EXIT-REDESIGN BOX-SCRATCH (live tree UNTOUCHED; RO throwaway) ==="
S=/tmp/mace_exitredesign_scratch
echo "--- df-guard on / before extract ---"
df -h / | tail -1
rm -rf "$S"; mkdir -p "$S"
tar xzf /tmp/mace_exitredesign_scratch.tar.gz -C "$S" || { echo "tar-extract FAILED"; rm -rf "$S" /tmp/mace_exitredesign_scratch.tar.gz; exit 2; }
PY=$(systemctl show -p ExecStart --value trading-corp 2>/dev/null | grep -oE '/[^ ;]*python[0-9.]*' | head -1)
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  for c in /home/azureuser/trading_corp/.venv/bin/python /home/azureuser/trading_corp/venv/bin/python /home/azureuser/.venv/bin/python; do
    [ -x "$c" ] && PY="$c" && break
  done
fi
echo "PY=$PY"; "$PY" --version 2>&1
cd "$S" || { echo "cd scratch FAILED"; rm -rf "$S" /tmp/mace_exitredesign_scratch.tar.gz; exit 2; }

echo "=== 1) config loads + closeability defaults + config_hash (LF deploy hash = 931a8214be50) ==="
PYTHONPATH="$S" "$PY" -c "from trading_corp.mace.config import load_mace_config as L; c=L('config/mace.yaml', exdiv_calendar_path='config/ex_dividend_calendar.yaml'); print('scratch config_hash[:12]=', c.config_hash[:12]); print('closeability=', c.management.closeability)" 2>&1 | tail -4

echo "=== 2) FULL MACE suite + new redesign files + dedupe (-p no:pytest_ethereum, continue-on-collect-error) ==="
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_*.py trading_corp/tests/test_pt_alert_dedupe.py -p no:pytest_ethereum -q --continue-on-collection-errors > /tmp/mxr_pytest.out 2>&1
RC=$?
echo "--- summary line ---"; tail -1 /tmp/mxr_pytest.out
echo "--- ERROR / FAILED lines (expect ONLY pre-existing stale: config max_contracts / sizing / strategy_entry / web UI; + any box dep-gap collection errors) ---"
grep -E "^(FAILED|ERROR)" /tmp/mxr_pytest.out | sed -E 's/ - .*//' || echo "(none)"
echo "--- exdiv collection-error detail (investigate any NEW box error) ---"
grep -B2 -A30 "test_mace_exdiv" /tmp/mxr_pytest.out | head -40

echo "=== 3) the 4 NEW redesign test files must be ALL GREEN (the deploy gate) ==="
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_exit_unlatch.py tests/test_mace_closeability.py tests/test_mace_ride_expiry.py tests/test_mace_exit_redesign_replays.py -p no:pytest_ethereum -q > /tmp/mxr_new.out 2>&1
RCN=$?
tail -1 /tmp/mxr_new.out
echo "=== new-files exit code: $RCN (0 = all redesign tests pass) ; full-suite exit code: $RC ==="

rm -rf "$S" /tmp/mace_exitredesign_scratch.tar.gz /tmp/mxr_pytest.out /tmp/mxr_new.out
echo "=== END BOX-SCRATCH (live tree untouched; scratch removed) ==="
