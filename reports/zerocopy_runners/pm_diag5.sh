ROOT=/home/azureuser/trading_corp
D=$ROOT/trading_corp/prediction_markets
echo "=== *.py in prediction_markets matching prop/match ==="
ls -1 "$D" | grep -iE 'prop|match' || echo "(none)"
echo "=== count of .py files ==="
ls -1 "$D"/*.py | wc -l
echo "=== execution.py references to prop matcher (import / player_prop / props_match / build_prop_index) ==="
grep -nE 'player_prop|props_match|build_prop_index|import .*prop' "$D/execution.py" | head -20 || echo "(no match)"
echo "=== live_driver.py references ==="
grep -nE 'player_prop|props_match|build_prop_index' "$D/live_driver.py" | head -20 || echo "(no match)"
echo "=== any file in prediction_markets importing a prop matcher module ==="
grep -rnE 'player_props_match|from .* import .*prop' "$D" | head -20 || echo "(none)"
echo "=== DONE diag5 ==="
