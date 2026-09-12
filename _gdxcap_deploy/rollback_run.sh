#!/bin/bash
# MACE #2 GDX-cap ROLLBACK: run the latest backup's rollback.sh, then re-verify the 4 files == box-truth
# 7683f59b (deployed #1). Board runs on a graft-FAIL or RED boot-verify, then RESTARTS.
set -u
ROOT=/home/azureuser/trading_corp; PKG=$ROOT/trading_corp
RB=$(ls -t "$ROOT"/gdxcap_rollback_*.sh 2>/dev/null | head -1)
if [ -z "$RB" ]; then echo "*** NO rollback script (~/gdxcap_rollback_*.sh) -- run the backup runner first ***"; exit 2; fi
echo "=== running $RB ==="
bash "$RB"
echo "=== RE-VERIFY: restored files == box-truth 7683f59b (deployed #1) ==="
declare -A PA=( [maceyaml]=$ROOT/config/mace.yaml [config]=$PKG/mace/config.py [execution]=$PKG/mace/execution.py [manager]=$PKG/mace/manager.py )
declare -A BASE=( [maceyaml]=49476b0ec0242960207fe2c76384f7533e6e7e758f6364545ef130621fc1a8ad [config]=d5aeb3652a44516e9edf1a28b1bdca8bee25cd50e5306ec0139fa7565a5855e3 [execution]=46464fa3a03f98f7c792325abbc352bb8e997d421fa13f42160d356cce7a06ed [manager]=deb073d368e5ab885863a7a00ee1f46a1a45917f84eb27bf744613fc3d753c31 )
fail=0
for k in maceyaml config execution manager; do
  cur=$(tr -d '\r' < "${PA[$k]}" | sha256sum | cut -d' ' -f1)
  if [ "$cur" = "${BASE[$k]}" ]; then echo "OK base $k"; else echo "FAIL $k cur=$cur"; fail=1; fi
done
[ $fail -eq 0 ] && echo "=== ROLLBACK GREEN: 4 files == 7683f59b. Board RESTART (restart_tc.ps1), then re-run bootverify. ===" || echo "*** ROLLBACK RE-VERIFY FAILED ***"
