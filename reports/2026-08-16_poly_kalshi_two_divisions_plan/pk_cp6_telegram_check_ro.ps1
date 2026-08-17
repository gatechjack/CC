# pk_cp6_telegram_check_ro.ps1 -- READ-ONLY: confirm the restart-verify's "PAPER_TELEGRAM_CARDS 1" is
# the benign boot log line "Polymarket copy trader scanner online" (a verify-grep artifact), NOT an
# actual paper copy card. Shows the exact matching journal lines + confirms the paper scan is a no-op
# (selected_whales empty). No writes. Run:
#   powershell -ep bypass -f .\pk_cp6_telegram_check_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "== EXACT lines matching 'Polymarket copy' since restart 04:38:48 (was counted as 1) =="
journalctl -u trading-corp --since "2026-08-17 04:38:48" --no-pager 2>/dev/null | grep "Polymarket copy"
echo "== actual copy CARD signature 'Polymarket copy (ENTRY|EXIT)' (expect NONE) =="
journalctl -u trading-corp --since "2026-08-17 04:38:48" --no-pager 2>/dev/null | grep -E "Polymarket copy (ENTRY|EXIT)" | head -5
echo "-- end card grep (empty above = no paper card fired) --"
echo "== paper sim scan outcome (expect no-op: selected_whales empty) =="
journalctl -u trading-corp --since "2026-08-17 04:38:48" --no-pager 2>/dev/null | grep -iE "polymarket_copy_trader.*no selected|no selected whales|copy ProposedOrder" | head -5
echo "-- end --"
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== CP6 paper-telegram false-positive check (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
