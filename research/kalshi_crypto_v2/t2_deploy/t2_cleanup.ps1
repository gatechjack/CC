# T2 home-dir cleanup: remove the 3 staged files from ~ on the VM (plain ssh as azureuser, NO sudo).
# Safe any time after step 3 (the files were already placed into the tree + /etc).
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
ssh $h "rm -f ~/kalshi_crypto_v2_observer.py ~/migrate_kcv2_tables.py ~/trading-corp-kcv2-observer.service; echo CLEANUP_DONE"
