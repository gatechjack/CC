RID=mace-XLE-2026-10-30-60-59-70-71-20260917
SINCE="2026-10-02 20:17:00"
DBP=/home/azureuser/trading_corp/data/trading_corp.db
echo "waiting for next manage tick to process the reset rung ($RID)..."
RES=TIMEOUT
for i in $(seq 1 45); do
  TS=$(sqlite3 -readonly "$DBP" "SELECT ts FROM mace_rung_live WHERE rung_id='$RID'")
  ST=$(sqlite3 -readonly "$DBP" "SELECT status FROM mace_rung WHERE rung_id='$RID'")
  REJ=$(timeout 20 journalctl -u trading-corp --since "$SINCE" -o cat 2>/dev/null | grep "60-59-70-71" | grep -ciE "pt_mark_reject|leg_inversion")
  EVT=$(timeout 20 journalctl -u trading-corp --since "$SINCE" -o cat 2>/dev/null | grep "60-59-70-71" | grep -ciE "exit_error|exit_start|manage_exit")
  echo "iter $i: status=$ST live_ts=$TS reject=$REJ exit_evt=$EVT"
  if [ "$REJ" -ge 1 ] || [ "$EVT" -ge 1 ] || [ "${TS:0:16}" != "2026-10-02T14:13" ]; then RES=TICKED; break; fi
  sleep 20
done
echo "RESULT=$RES"
echo "== XLE 10-30 rung states =="
sqlite3 -readonly -header "$DBP" "SELECT rung_id,status,contracts FROM mace_rung WHERE symbol='XLE' AND expiry='2026-10-30' ORDER BY entry_ts"
echo "== reset-rung events since reset (expect pt_mark_reject/leg_inversion, NO exit_error) =="
timeout 40 journalctl -u trading-corp --since "$SINCE" -o cat 2>/dev/null | grep "60-59-70-71" | grep -iE "pt_mark_reject|leg_inversion|exit_error|exit_start|manage_exit|PT held" | tail -15
echo "== adopted orphan live mark (managed?) =="
sqlite3 -readonly -header "$DBP" "SELECT rung_id,round(mark,3) mark,round(spot,2) spot,ts FROM mace_rung_live WHERE rung_id LIKE 'mace-XLE-2026-10-30-59.5-58-70-71.5%'"
echo "DONE_ACCEPT"
