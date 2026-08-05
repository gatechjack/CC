# T2 step 2: copy the 3 files to the VM home (plain scp as azureuser - NO sudo, NO az). Files are LF.
# az-root step 3 then places them from home into the tree + /etc.
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
$src = 'C:\Users\AA Incorporado\cc-2026-08-02-wt'
scp "$src\trading_corp\agents\strategies\kalshi_crypto_v2_observer.py" "$src\scripts\migrate_kcv2_tables.py" "$src\infra\systemd\trading-corp-kcv2-observer.service" "${h}:~/"
Write-Host "scp done (expect three 100% lines above)."
