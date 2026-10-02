cd /home/azureuser/trading_corp || exit 2
echo "== RUNNING ENGINE (must be UNCHANGED by the graft -- no restart) =="
systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p ActiveEnterTimestamp -p NRestarts 2>&1
echo -n "healthz: "; curl -s --max-time 8 http://localhost:8000/healthz 2>/dev/null; echo
echo -n "live config_hash (/mace, from RUNNING process): "; curl -s --max-time 8 http://localhost:8000/mace 2>/dev/null | grep -oE "931a8214[a-f0-9]*" | head -1; echo
echo "== ON-DISK md5s (grafted = staged for next restart) =="
for f in config strategy execution manager; do echo "$f md5=$(md5sum trading_corp/mace/$f.py|cut -c1-8)"; done
echo "on-disk config_hash: $(sha256sum config/mace.yaml | cut -c1-12)"
echo "ET_now: $(TZ=America/New_York date '+%F %T %Z')"
echo "backup present:"; ls -d /home/azureuser/mace_legsanity_graft_backup_* 2>/dev/null | tail -1
echo "DONE_POSTGRAFT"
