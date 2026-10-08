echo "=== MACE STOP-HOTFIX BOX-SCRATCH (live tree UNTOUCHED; RO throwaway) ==="
S=/tmp/mace_stophotfix_scratch
echo "--- df-guard / ---"; df -h / | tail -1
rm -rf "$S"; mkdir -p "$S"
tar xzf /tmp/mace_stophotfix_scratch.tar.gz -C "$S" || { echo "extract FAILED"; rm -rf "$S" /tmp/mace_stophotfix_scratch.tar.gz; exit 2; }
PY=$(systemctl show -p ExecStart --value trading-corp 2>/dev/null | grep -oE '/[^ ;]*python[0-9.]*' | head -1)
[ -x "$PY" ] || PY=/home/azureuser/trading_corp/venv/bin/python
"$PY" --version 2>&1; cd "$S" || exit 2
echo "=== 1) config_hash (LF deploy hash = 931a8214be50) ==="
PYTHONPATH="$S" "$PY" -c "from trading_corp.mace.config import load_mace_config as L; print('config_hash', L('config/mace.yaml', exdiv_calendar_path='config/ex_dividend_calendar.yaml').config_hash[:12])" 2>&1 | tail -2
echo "=== 2) FULL mace suite (-p no:pytest_ethereum, continue-on-collect) ==="
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_*.py trading_corp/tests/test_pt_alert_dedupe.py -p no:pytest_ethereum -q --continue-on-collection-errors > /tmp/sh.out 2>&1
echo "--- summary ---"; tail -1 /tmp/sh.out
echo "--- FAILED/ERROR (expect ONLY pre-existing stale: config/sizing/strategy_entry/migration) ---"
grep -E "^(FAILED|ERROR)" /tmp/sh.out | sed -E 's/ - .*//'
echo "=== 3) the hotfix tests must be GREEN: spurious-stop replay + stop semantics + un-latch ==="
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_closeability.py tests/test_mace_exit_unlatch.py tests/test_mace_exit_redesign_replays.py tests/test_mace_ride_expiry.py -p no:pytest_ethereum -q > /tmp/sh2.out 2>&1
tail -1 /tmp/sh2.out
echo "=== 4) explicit: the spurious-stop replay test ==="
PYTHONPATH="$S" "$PY" -m pytest tests/test_mace_closeability.py -p no:pytest_ethereum -q -k "SPURIOUS_STOP or dead_wing_short or stop_triggered or no_stop_on_healthy" 2>&1 | tail -2
rm -rf "$S" /tmp/mace_stophotfix_scratch.tar.gz /tmp/sh.out /tmp/sh2.out
echo "=== END ==="
