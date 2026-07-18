#!/usr/bin/env bash
# Installs the standalone RH daily re-login timer (ITEM 1). Run AS ROOT (Azure Run Command).
# Prereq: /home/azureuser/rh_daily_relogin.py already scp'd (azureuser). secrets.py on disk
# (ROBINHOOD_* in expected_env_vars) from the batch, KV has ROBINHOOD-USERNAME/PASSWORD.
set -e
test -f /home/azureuser/rh_daily_relogin.py || { echo "MISSING /home/azureuser/rh_daily_relogin.py"; exit 1; }
chown azureuser:azureuser /home/azureuser/rh_daily_relogin.py 2>/dev/null || true

cat > /etc/systemd/system/rh-relogin.service <<'EOF'
[Unit]
Description=RH daily re-login (refresh robin_stocks pickle; ITEM 1, standalone)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=azureuser
WorkingDirectory=/home/azureuser/trading_corp
Environment=PYTHONPATH=/home/azureuser/trading_corp
Environment=KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/
StandardInput=null
TimeoutStartSec=180
ExecStart=/home/azureuser/trading_corp/venv/bin/python /home/azureuser/rh_daily_relogin.py

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/rh-relogin.timer <<'EOF'
[Unit]
Description=Daily RH re-login timer (ITEM 1) -- keeps ~/.tokens/robinhood.pickle non-stale

[Timer]
OnCalendar=*-*-* 13:00:00 UTC
Persistent=true
AccuracySec=1min
Unit=rh-relogin.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now rh-relogin.timer
echo "=== TIMER ENABLED ==="
systemctl is-enabled rh-relogin.timer
systemctl list-timers rh-relogin.timer --no-pager || true

echo "=== VERIFY: run the service ONCE now (pickle valid -> gentle fast-path, NO push expected) ==="
systemctl start rh-relogin.service
sleep 4
echo "--- service result ---"
systemctl show rh-relogin.service -p Result,ExecMainStatus,ActiveState,SubState | sed 's/^/  /'
echo "--- service journal (last run) ---"
journalctl -u rh-relogin.service -n 12 --no-pager --output=cat || true
echo "=== INSTALL DONE ==="
