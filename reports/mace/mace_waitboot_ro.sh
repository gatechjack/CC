OLD=534581
echo "waiting for restart to take + MACE boot (old PID=$OLD)..."
RESULT=TIMEOUT
for i in $(seq 1 34); do
  PID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
  SUB=$(systemctl show -p SubState --value trading-corp 2>/dev/null)
  AES=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null)
  if [ -n "$PID" ] && [ "$PID" != "0" ] && [ "$PID" != "$OLD" ]; then
    W=$(timeout 25 journalctl -u trading-corp --since "6 min ago" -o cat 2>/dev/null | grep -c "Robinhood MACE wired")
    echo "iter $i: NEW PID=$PID SubState=$SUB ActiveEnter=$AES  MACE_wired_lines=$W"
    if [ "$W" -ge 1 ] && [ "$SUB" = "running" ]; then RESULT=MACE_ONLINE; break; fi
  else
    echo "iter $i: PID=$PID (old=$OLD) SubState=$SUB -- not restarted yet"
  fi
  sleep 8
done
echo "WAIT_RESULT=$RESULT"
echo "DONE_WAITBOOT"
