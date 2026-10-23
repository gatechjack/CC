# BOX-SCRATCH test run (no prod touch): overlay changed files onto a /tmp copy of the live package,
# py_compile all, verify config_hash preserved, run the MACE pytest suite. scp+tar = binary-exact
# (source has non-ASCII em-dashes; must NOT be streamed). Read-only vs prod tree/engine/DB.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$wt = 'C:\Users\AA Incorporado\cc-mace-durability-fix-2026-10-23-wt'
$tgz = "$env:TEMP\mace_durfix.tgz"
$files = @('trading_corp/mace/config.py','trading_corp/mace/execution.py','trading_corp/mace/manager.py',
           'trading_corp/mace/loops.py','trading_corp/main.py','trading_corp/tests/test_mace_durability.py',
           'trading_corp/tests/test_pt_alert_dedupe.py')
if (Test-Path $tgz) { Remove-Item $tgz -Force }
tar.exe -czf $tgz -C $wt $files
scp -o BatchMode=yes -o ConnectTimeout=20 $tgz "${h}:/tmp/mace_durfix.tgz"
$sh = @'
set -u
V=/home/azureuser/trading_corp/venv/bin/python
rm -rf /tmp/mace_durfix; mkdir -p /tmp/mace_durfix
cp -r /home/azureuser/trading_corp/trading_corp /tmp/mace_durfix/trading_corp
tar -xzf /tmp/mace_durfix.tgz -C /tmp/mace_durfix
echo "=== overlaid file CR-stripped md5s (vs worktree) ==="
for f in trading_corp/mace/config.py trading_corp/mace/execution.py trading_corp/mace/manager.py trading_corp/mace/loops.py trading_corp/main.py trading_corp/tests/test_mace_durability.py; do
  printf "%s  %s\n" "$(tr -d '\r' < /tmp/mace_durfix/$f | md5sum | cut -c1-8)" "$f"
done
echo "=== py_compile (syntax gate) ==="
$V -m py_compile /tmp/mace_durfix/trading_corp/mace/config.py /tmp/mace_durfix/trading_corp/mace/execution.py /tmp/mace_durfix/trading_corp/mace/manager.py /tmp/mace_durfix/trading_corp/mace/loops.py /tmp/mace_durfix/trading_corp/main.py /tmp/mace_durfix/trading_corp/tests/test_mace_durability.py && echo PY_COMPILE_OK || echo PY_COMPILE_FAIL
echo "=== config loads + config_hash (must stay 931a8214be50; new fields default) ==="
cd /tmp/mace_durfix && $V -c "from trading_corp.mace.config import load_mace_config as L; c=L('/home/azureuser/trading_corp/config/mace.yaml', exdiv_calendar_path='/home/azureuser/trading_corp/config/ex_dividend_calendar.yaml'); print('config_hash', c.config_hash[:12], '| mtt', c.management.manage_tick_timeout_sec, '| lqt', c.management.leg_quote_timeout_sec, '| wd', c.management.watchdog)"
echo "=== pytest MACE suite (durability + pt-dedupe) ==="
cd /tmp/mace_durfix && $V -m pytest trading_corp/tests/test_mace_durability.py trading_corp/tests/test_pt_alert_dedupe.py -p no:pytest_ethereum -q -o cache_dir=/tmp/mace_durfix/.pc 2>&1 | tail -45
echo "=== cleanup ==="
rm -rf /tmp/mace_durfix /tmp/mace_durfix.tgz
echo DONE
'@
$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
