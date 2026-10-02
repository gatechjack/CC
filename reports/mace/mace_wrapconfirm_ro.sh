cd /home/azureuser/trading_corp || exit 2
echo "== ENGINE =="
systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p ActiveEnterTimestamp -p NRestarts 2>&1
echo -n "healthz: "; curl -s --max-time 8 http://localhost:8000/healthz 2>/dev/null; echo
echo -n "live config_hash (/mace): "; curl -s --max-time 8 http://localhost:8000/mace 2>/dev/null | grep -oE "931a8214[a-f0-9]*" | head -1; echo
echo "== on-box md5s =="
for f in config strategy execution manager; do echo "$f md5=$(md5sum trading_corp/mace/$f.py|cut -c1-8)"; done
echo "== XLE 10-30 rungs =="
sqlite3 -readonly -header data/trading_corp.db "SELECT rung_id,status,contracts c,round(credit_actual,2) cr,round(max_risk_usd,1) mr,substr(expiry,1,10) exp FROM mace_rung WHERE symbol='XLE' AND expiry='2026-10-30' ORDER BY entry_ts"
echo "== census =="
sqlite3 -readonly -header data/trading_corp.db "SELECT status,count(*) n FROM mace_rung GROUP BY status ORDER BY n DESC"
echo "== tracebacks since restart 20:08:06Z =="
timeout 40 journalctl -u trading-corp --since "2026-10-02 20:08:06" --no-pager 2>/dev/null | grep -c -iE traceback
echo "ET_now: $(TZ=America/New_York date '+%F %T %Z %a')"
echo "DONE_WRAPCONFIRM"
