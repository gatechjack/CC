#!/usr/bin/env bash
# Prod Robinhood session refresh — forces a fresh rs.login() with push approval.
#
# Why: trading-corp.service restart triggers an MFA loop because the cached
# session pickle (~/.tokens/robinhood.pickle) is stale. Manually refreshing
# the pickle while you tap "approve" on your phone fixes the next restart.
#
# Usage (from your local machine, az CLI authenticated):
#   az vm run-command invoke -n tc-prod-vm -g rg-shared-prod \
#     --command-id RunShellScript --scripts @scripts/rh_mfa_refresh_prod.sh \
#     --query "value[0].message" -o tsv
#
# Timing: rs.login() polls Robinhood's sheriff-workflow API for 2 minutes
# after sending the push. Approve within that window.
set -e
echo "==> Stopping trading-corp"
sudo systemctl stop trading-corp
sleep 2

echo "==> Backing up existing pickle"
sudo cp /home/azureuser/.tokens/robinhood.pickle \
       /home/azureuser/.tokens/robinhood.pickle.bak.$(date +%s) 2>&1 || true

echo "==> Clearing pickle (forces fresh login)"
sudo rm -f /home/azureuser/.tokens/robinhood.pickle

echo "==> Triggering Robinhood login -- PUSH CHALLENGE WILL ARRIVE ON PHONE"
cd /home/azureuser/trading_corp
sudo -u azureuser \
    KEY_VAULT_URI="https://kv-tc-vtwbowt3wtkpy.vault.azure.net/" \
    venv/bin/python -c "
import sys
from trading_corp.utils.secrets import load_secrets
import robin_stocks.robinhood as rs
s = load_secrets()
if not s.robinhood_username or not s.robinhood_password:
    print('FAIL: robinhood creds not loaded from KV'); sys.exit(2)
print(f'Logging in as {s.robinhood_username[:3]}***')
try:
    rs.login(s.robinhood_username, s.robinhood_password, store_session=True)
    print('LOGIN OK')
except Exception as e:
    print(f'LOGIN FAILED: {type(e).__name__}: {e}'); sys.exit(1)
"

echo "==> Verifying new pickle"
sudo ls -la /home/azureuser/.tokens/robinhood.pickle

echo "==> Starting trading-corp"
sudo systemctl start trading-corp
sleep 5

echo "==> Service status"
sudo systemctl is-active trading-corp
echo "==> Recent service logs (last 20 lines)"
sudo journalctl -u trading-corp -n 20 --no-pager | tail -20
