cd /home/azureuser/trading_corp
echo "=== DRIFT-GATE md5 (expect base b11af9b) ==="
for f in trading_corp/mace/manager.py trading_corp/mace/execution.py trading_corp/mace/loops.py trading_corp/web/mace_view.py trading_corp/web/templates/mace_live.html config/mace.yaml config/ex_dividend_calendar.yaml; do
  md5sum "$f"
done
if [ -e trading_corp/web/templates/partials/mace_halt.html ]; then echo "HALT_HTML EXISTS (unexpected)"; else echo "HALT_HTML ABSENT (expected)"; fi
echo "=== engine state ==="
systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState
