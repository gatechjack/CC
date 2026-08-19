# pk_item1_rollback.ps1 -- ATOMIC rollback: restore BOTH files from the newest
# .bak_item1_<ts> pair + restart. BOTH-OR-NEITHER: aborts if either backup is
# missing (never leaves a mixed pair). No cutover/roster change.
# Run: powershell -ep bypass -f .\pk_item1_rollback.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
set -e
FE=/home/azureuser/trading_corp/trading_corp/agents/strategies/poly_kalshi_executor.py
FM=/home/azureuser/trading_corp/trading_corp/data/mlb_poly_kalshi_match.py
BKE=$(ls -t ${FE}.bak_item1_* 2>/dev/null | head -1)
BKM=$(ls -t ${FM}.bak_item1_* 2>/dev/null | head -1)
[ -n "$BKE" ] && [ -n "$BKM" ] || { echo NO_BACKUP_PAIR_FOUND; exit 2; }
echo "restoring executor from $BKE"
echo "restoring matcher  from $BKM"
cp "$BKE" "$FE"; cp "$BKM" "$FM"
echo "restored_exec_LFMD5  $(tr -d '\r' < $FE | md5sum | cut -d' ' -f1)  (pre-fix d1f871f9c3e83530dc6fba3bd58c2eae)"
echo "restored_match_LFMD5 $(tr -d '\r' < $FM | md5sum | cut -d' ' -f1)  (pre-fix 4b2a5c49fb737d54d5a964868a4cd9fa)"
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
Write-Host "== ITEM 1 ATOMIC ROLLBACK (restore BOTH .bak_item1_* + restart) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
