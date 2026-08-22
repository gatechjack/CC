# pk_pm_rollback.ps1 -- MUTATION (Jack runs). Reverses the ADDITIVE P1 deploy. Box is NOT git,
# so rollback = delete the added files: trading_corp/prediction_markets/ (whole pkg),
# trading_corp/scripts/pm_cli.py, config/pm_seed_wallets.yaml. Also removes ONLY the PM refresh
# cron line (preserves every other crontab line) and LEAVES the separate PM DB in place (inert:
# a distinct file no engine reads once the package is gone). NO restart, NO sudo, NO existing-file
# edits. Engine untouched. Run: powershell -ep bypass -f .\pk_pm_rollback.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_pm_rb.sh'
$bash = @'
R=/home/azureuser/trading_corp
echo "== PM P1 ROLLBACK (reverse of additive deploy; NO restart, NO sudo, NO existing-file edits) =="
echo "-- targets --"
for f in trading_corp/prediction_markets trading_corp/scripts/pm_cli.py config/pm_seed_wallets.yaml; do
  if [ -e "$R/$f" ]; then echo "  present -> remove: $f"; else echo "  absent  (skip):   $f"; fi
done
rm -rf "$R/trading_corp/prediction_markets"
rm -f  "$R/trading_corp/scripts/pm_cli.py"
rm -f  "$R/config/pm_seed_wallets.yaml"
echo "-- verify removed --"
FAIL=0
for f in trading_corp/prediction_markets trading_corp/scripts/pm_cli.py config/pm_seed_wallets.yaml; do
  if [ -e "$R/$f" ]; then echo "  STILL PRESENT (FAIL): $f"; FAIL=1; else echo "  removed: $f"; fi
done
echo "-- crontab: remove ONLY the PM refresh line, preserve all others --"
CUR="$(crontab -l 2>/dev/null)"
if printf '%s\n' "$CUR" | grep -q 'pm_cli.py'; then
  printf '%s\n' "$CUR" | grep -v 'pm_cli.py' | crontab -
  echo "  PM cron line removed. Remaining crontab:"; crontab -l 2>/dev/null | sed 's/^/    /'
else
  echo "  no PM cron line present (nothing to remove)."
fi
echo "-- PM DB: LEFT IN PLACE (separate file, inert once package is gone) --"
ls -la "$R"/data/prediction_markets.db* 2>/dev/null || echo "  (no PM DB present)"
echo "  discard manually if desired: rm -f $R/data/prediction_markets.db*"
echo "ROLLBACK_DONE (FAIL=$FAIL)"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM P1 ROLLBACK =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -Force -ErrorAction SilentlyContinue
