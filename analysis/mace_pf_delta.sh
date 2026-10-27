set -e
VENV=/home/azureuser/trading_corp/venv/bin/python
# The 4 files that FAILED in the fixed run -- all UNEDITED by the fix, so base==fixed versions.
# (They do NOT import RobinhoodComboPartial, so they collect on base src.)
FILES="tests/test_mace_config.py tests/test_mace_loops.py tests/test_mace_sizing.py tests/test_mace_strategy_entry.py"
echo "########## BASE src (/tmp/mace_pf_baseline_scratch) ##########"
cd /tmp/mace_pf_baseline_scratch
PYTHONPATH=/tmp/mace_pf_baseline_scratch "$VENV" -m pytest -p no:pytest_ethereum -q --tb=no -rf $FILES 2>&1 | tail -14
echo ""
echo "########## FIXED src (/tmp/mace_pf_scratch) ##########"
cd /tmp/mace_pf_scratch
PYTHONPATH=/tmp/mace_pf_scratch "$VENV" -m pytest -p no:pytest_ethereum -q --tb=no -rf $FILES 2>&1 | tail -14
echo "DELTA DONE"
