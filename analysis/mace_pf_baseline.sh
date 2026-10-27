set -e
BASE=/tmp/mace_pf_baseline.tgz
BSCR=/tmp/mace_pf_baseline_scratch
FSCR=/tmp/mace_pf_scratch
SRC=/home/azureuser/trading_corp
VENV=/home/azureuser/trading_corp/venv/bin/python
PYSET="tests/test_mace_*.py trading_corp/tests/test_mace_durability.py tests/test_robinhood_multi_leg.py tests/test_robinhood_order_reconcile.py tests/test_data_exec_safety_handlers.py tests/test_place_combo.py tests/test_combo_approval.py tests/test_pmcc_combo_dispatch.py tests/test_pmcc_execute_dispatch.py"
echo "=== build BASELINE scratch (base src + current tests, NO fix) ==="
rm -rf "$BSCR"; mkdir -p "$BSCR"
rsync -a --exclude '/data' --exclude '/venv' --exclude '.git' --exclude __pycache__ --exclude '*.pyc' --exclude .pytest_cache "$SRC"/ "$BSCR"/
tar xzf "$BASE" -C "$BSCR"
if grep -q "class RobinhoodComboPartial" "$BSCR/trading_corp/brokers/robinhood.py"; then echo "UNEXPECTED fix in baseline"; exit 4; fi
echo "baseline src = BASE (no RobinhoodComboPartial) confirmed"
echo ""
echo "########## FIXED RUN (/tmp/mace_pf_scratch, has the 3-file fix) ##########"
cd "$FSCR"
PYTHONPATH="$FSCR" "$VENV" -m pytest -p no:pytest_ethereum -q --tb=no -rN $PYSET 2>&1 | tail -16
echo ""
echo "########## BASELINE RUN (base src, same current tests) ##########"
cd "$BSCR"
PYTHONPATH="$BSCR" "$VENV" -m pytest -p no:pytest_ethereum -q --tb=no -rN $PYSET 2>&1 | tail -16
echo "COMPARE DONE"
