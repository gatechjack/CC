# T2 step 2: copy the 3 files to the VM, verify the 2 python md5s (LF), migrate ONLY if they match.
# Run from local PowerShell.
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
$src = 'C:\Users\AA Incorporado\cc-2026-08-02-wt'
Write-Host "--- scp 3 files to VM home ---"
scp "$src\trading_corp\agents\strategies\kalshi_crypto_v2_observer.py" "$src\scripts\migrate_kcv2_tables.py" "$src\infra\systemd\trading-corp-kcv2-observer.service" "${h}:~/"
$bash = @'
cd /home/azureuser/trading_corp
cp ~/kalshi_crypto_v2_observer.py trading_corp/agents/strategies/kalshi_crypto_v2_observer.py
cp ~/migrate_kcv2_tables.py scripts/migrate_kcv2_tables.py
ok=1
check() { got=$(md5sum "$2" | awk '{print $1}'); if [ "$got" = "$1" ]; then echo "MATCH     $2"; else echo "MISMATCH  $2 got=$got want=$1"; ok=0; fi; }
check dba46374b23a74fe9eaa333be61744cd trading_corp/agents/strategies/kalshi_crypto_v2_observer.py
check 7a2dd43e46be0c57382a838f6b223b64 scripts/migrate_kcv2_tables.py
if [ "$ok" != "1" ]; then echo "STOP: md5 mismatch - NOT migrating. Report to agent."; exit 1; fi
echo "--- migrate (creates 4 kcv2_* tables) ---"
venv/bin/python -X utf8 scripts/migrate_kcv2_tables.py data/trading_corp.db
echo "--- tables present (expect 4) ---"
sqlite3 -readonly data/trading_corp.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kcv2_%' ORDER BY name;"
'@
$bash | ssh $h "tr -d '\r' | bash"
