set -e
STAGE=/tmp/mace_pf_stage.tgz
SCR=/tmp/mace_pf_scratch
SRC=/home/azureuser/trading_corp
VENV=/home/azureuser/trading_corp/venv/bin/python
echo "=== build scratch tree (exclude data/venv/.git) ==="
command -v rsync >/dev/null || { echo "NO rsync -- abort"; exit 9; }
rm -rf "$SCR"; mkdir -p "$SCR"
rsync -a --exclude '/data' --exclude '/venv' --exclude '.git' --exclude __pycache__ --exclude '*.pyc' --exclude .pytest_cache "$SRC"/ "$SCR"/
echo "=== overlay staged 6 files ==="
tar xzf "$STAGE" -C "$SCR"
echo "=== STAGE GATE: CR-stripped md5 == target (proves scratch has the fix) ==="
for pair in "trading_corp/brokers/robinhood.py:1e6bbc1e" "trading_corp/mace/rh_broker.py:bac4411a" "trading_corp/mace/execution.py:e380c2f6"; do
  f="${pair%%:*}"; want="${pair##*:}"; got=$(tr -d '\r' < "$SCR/$f" | md5sum | cut -c1-8)
  if [ "$got" = "$want" ]; then echo "OK  $f $got"; else echo "MISMATCH $f got=$got want=$want"; exit 3; fi
done
echo "=== markers present ==="
grep -q "class RobinhoodComboPartial" "$SCR/trading_corp/brokers/robinhood.py" && echo "robinhood RobinhoodComboPartial OK"
grep -q "_combo_live_at_broker" "$SCR/trading_corp/mace/execution.py" && echo "execution _combo_live_at_broker OK"
grep -q "RobinhoodComboPartial" "$SCR/trading_corp/mace/rh_broker.py" && echo "rh_broker handles RobinhoodComboPartial OK"
echo "=== py_compile the 3 src ==="
"$VENV" -m py_compile "$SCR/trading_corp/brokers/robinhood.py" "$SCR/trading_corp/mace/rh_broker.py" "$SCR/trading_corp/mace/execution.py" && echo "py_compile OK"
echo "=== confirm trading_corp resolves to the SCRATCH (not the live tree) ==="
cd "$SCR"
PYTHONPATH="$SCR" "$VENV" -c "import trading_corp,trading_corp.brokers.robinhood as r; print('trading_corp:',trading_corp.__file__); print('partial_in_terminal_set:', 'partially_filled' in r.RobinhoodBroker._TERMINAL_OPTION_STATES); print('has RobinhoodComboPartial:', hasattr(r,'RobinhoodComboPartial'))"
echo "=== pytest: full MACE suite + cross-division shared-file (robinhood/data_exec/pmcc-combo) ==="
PYTHONPATH="$SCR" "$VENV" -m pytest -p no:pytest_ethereum -q \
  tests/test_mace_*.py trading_corp/tests/test_mace_durability.py \
  tests/test_robinhood_multi_leg.py tests/test_robinhood_order_reconcile.py \
  tests/test_data_exec_safety_handlers.py tests/test_place_combo.py \
  tests/test_combo_approval.py tests/test_pmcc_combo_dispatch.py \
  tests/test_pmcc_execute_dispatch.py 2>&1 | tail -45
echo "DONE"
