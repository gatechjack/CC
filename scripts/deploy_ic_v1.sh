#!/usr/bin/env bash
# IC v1 deploy — applies the wiring commit (65c8cdd) to prod.
#
# Stages:
#   1. Decode embedded gzip+base64 patch.
#   2. Dry-run `patch -p1 --fuzz=5` to catch drift collisions before touching files.
#   3. Back up the 4 target files with a timestamp.
#   4. Apply the patch.
#   5. Import-test trading_corp.main + trading_corp.web.routes (syntax check).
#   6. systemctl restart trading-corp.
#   7. Show service status + recent logs.
#
# Usage (from your local machine, az CLI authenticated):
#   1. Generate the patch + embed:
#        cd "C:/Users/AA Incorporado/CC"
#        git format-patch -1 HEAD --stdout > /tmp/ic_v1.patch
#        B64=$(gzip -c /tmp/ic_v1.patch | base64 -w0)
#        sed -i "s|__PATCH_B64__|$B64|" scripts/deploy_ic_v1.sh
#   2. Ship + run:
#        az vm run-command invoke -n tc-prod-vm -g rg-shared-prod \
#          --command-id RunShellScript --scripts @scripts/deploy_ic_v1.sh \
#          --query "value[0].message" -o tsv
set -e
B64='__PATCH_B64__'
mkdir -p /tmp/ic-deploy
echo "$B64" | base64 -d | gunzip > /tmp/ic-deploy/ic_v1.patch
echo "==> Patch size:"
ls -la /tmp/ic-deploy/ic_v1.patch
echo "==> Dry-run patch -p1 --fuzz=5"
cd /home/azureuser/trading_corp
sudo -u azureuser patch -p1 --fuzz=5 --dry-run < /tmp/ic-deploy/ic_v1.patch
echo "==> Dry-run OK — backing up + applying"
STAMP=$(date +%Y%m%d-%H%M%S)
sudo -u azureuser cp config/divisions.yaml config/divisions.yaml.pre-ic-v1-$STAMP
sudo -u azureuser cp config/strategies.yaml config/strategies.yaml.pre-ic-v1-$STAMP
sudo -u azureuser cp trading_corp/main.py trading_corp/main.py.pre-ic-v1-$STAMP
sudo -u azureuser cp trading_corp/web/routes.py trading_corp/web/routes.py.pre-ic-v1-$STAMP
echo "==> Applying patch"
sudo -u azureuser patch -p1 --fuzz=5 < /tmp/ic-deploy/ic_v1.patch
echo "==> Post-apply md5"
md5sum config/divisions.yaml config/strategies.yaml trading_corp/main.py trading_corp/web/routes.py
echo "==> Import test (catch syntax errors before restart)"
sudo -u azureuser venv/bin/python -c "import trading_corp.main; import trading_corp.web.routes; print('IMPORT OK')"
echo "==> Restarting trading-corp"
sudo systemctl restart trading-corp
sleep 6
echo "==> Service status"
sudo systemctl is-active trading-corp
echo "==> Recent logs (last 70 lines)"
sudo journalctl -u trading-corp -n 80 --no-pager | tail -70
