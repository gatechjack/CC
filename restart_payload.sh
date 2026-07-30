echo "BEFORE_MainPID=$(systemctl show -p MainPID --value trading-corp)"
systemctl restart trading-corp
sleep 6
echo "is_active=$(systemctl is-active trading-corp)"
systemctl show -p MainPID,NRestarts,ActiveEnterTimestamp,ExecMainStartTimestamp trading-corp
date -u +"RESTART_DONE_UTC=%Y-%m-%dT%H:%M:%SZ"
