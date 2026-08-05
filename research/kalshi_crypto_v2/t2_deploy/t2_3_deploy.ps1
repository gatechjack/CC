# T2 step 3: place files + md5-gate + migrate + install unit + md5-gate + enable, via az run-command
# (root, NO sudo). Self-gated: an md5 mismatch aborts BEFORE migrating / before enabling. DB write is
# done as azureuser (runuser) so the live DB + wal/shm stay azureuser-owned.
$ErrorActionPreference = 'Stop'
$rg = 'RG-SHARED-PROD'; $vm = 'tc-prod-vm'
$bash = @'
cd /home/azureuser/trading_corp
install -o azureuser -g azureuser -m 644 /home/azureuser/kalshi_crypto_v2_observer.py trading_corp/agents/strategies/kalshi_crypto_v2_observer.py
install -o azureuser -g azureuser -m 644 /home/azureuser/migrate_kcv2_tables.py scripts/migrate_kcv2_tables.py
ok=1
for pair in "dba46374b23a74fe9eaa333be61744cd trading_corp/agents/strategies/kalshi_crypto_v2_observer.py" "7a2dd43e46be0c57382a838f6b223b64 scripts/migrate_kcv2_tables.py"; do
  set -- $pair; g=$(md5sum "$2" | awk '{print $1}')
  if [ "$g" = "$1" ]; then echo "MATCH     $2"; else echo "MISMATCH  $2 got=$g"; ok=0; fi
done
if [ "$ok" != "1" ]; then echo "STOP: python md5 mismatch - NOT migrating"; exit 1; fi
echo "--- migrate (as azureuser) ---"
runuser -u azureuser -- /home/azureuser/trading_corp/venv/bin/python -X utf8 /home/azureuser/trading_corp/scripts/migrate_kcv2_tables.py /home/azureuser/trading_corp/data/trading_corp.db
install -m 644 /home/azureuser/trading-corp-kcv2-observer.service /etc/systemd/system/trading-corp-kcv2-observer.service
ug=$(md5sum /etc/systemd/system/trading-corp-kcv2-observer.service | awk '{print $1}')
if [ "$ug" != "bf0014618895921790c6423f4fbd2255" ]; then echo "UNIT_MD5_MISMATCH_STOP got=$ug"; exit 1; fi
echo "UNIT_MD5_MATCH"
systemctl daemon-reload
systemctl enable --now trading-corp-kcv2-observer
systemctl status trading-corp-kcv2-observer --no-pager | head -5
echo "=== DEPLOY_OK ==="
'@
$bash = $bash -replace "`r", ""
(az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $bash | ConvertFrom-Json).value[0].message
