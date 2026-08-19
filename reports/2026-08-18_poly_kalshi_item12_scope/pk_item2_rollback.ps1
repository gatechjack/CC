# pk_item2_rollback.ps1 -- restore brokers/kalshi.py from the newest .bak_item2_* and
# restart. Single file, no cutover/roster change -> clean rollback.
# Run: powershell -ep bypass -f .\pk_item2_rollback.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
set -e
F=/home/azureuser/trading_corp/trading_corp/brokers/kalshi.py
BK=$(ls -t ${F}.bak_item2_* 2>/dev/null | head -1)
[ -n "$BK" ] || { echo NO_BACKUP_FOUND; exit 2; }
echo "restoring from $BK"
cp "$BK" "$F"
echo "restored_LFMD5 $(tr -d '\r' < $F | md5sum | cut -d' ' -f1)  (pre-fix expected 18626cf0ddcdf6c3663be7d9602abbba)"
RS=$(date -u +"%Y-%m-%d %H:%M:%S")
systemctl restart trading-corp
for i in $(seq 1 66); do
  sleep 5
  if journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -q "Poly->Kalshi MLB copy WIRED"; then
    echo "WIRED_SECONDS $((i*5))"; break; fi
done
echo "PID $(systemctl show trading-corp -p MainPID --value)"
journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -E "Poly->Kalshi MLB copy WIRED|roster invariant"
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== ITEM 2 ROLLBACK (restore .bak_item2_* + restart) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
