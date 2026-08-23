# pk_pm_cron_install.ps1 -- install the nightly PM refresh cron at 03:20 UTC (RULING B) into azureuser's
# crontab. Idempotent (skips if already present), append-only, backs up the existing crontab first.
# Additive; no restart, no sudo (az run-command control-plane root path, as used for prior deploys),
# no legacy DB contact. Run: powershell -ep bypass -f .\pk_pm_cron_install.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_croninst_box.sh'
$bash = @'
OUT=/tmp/pm_croninst.txt
LINE='20 3 * * * cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py refresh --cap 50000 >> /home/azureuser/pm_refresh.log 2>&1'
{
echo "=== MainPID before (must be 850993) ==="
systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "=== backup existing azureuser crontab ==="
BK="/home/azureuser/pm_cron_bak_$(date +%Y%m%d_%H%M%S).txt"
crontab -u azureuser -l > "$BK" 2>/dev/null && echo "backup -> $BK ($(wc -l < "$BK") lines)" || echo "(no existing crontab; nothing to back up)"
echo "=== idempotent install ==="
if crontab -u azureuser -l 2>/dev/null | grep -qF 'pm_cli.py refresh'; then
  echo "ALREADY PRESENT -- no change made"
else
  ( crontab -u azureuser -l 2>/dev/null; echo "$LINE" ) | crontab -u azureuser -
  echo "INSTALLED: $LINE"
fi
echo "=== azureuser crontab AFTER (verify line present, once) ==="
crontab -u azureuser -l 2>&1
echo "=== count of pm refresh lines (must be exactly 1) ==="
crontab -u azureuser -l 2>/dev/null | grep -cF 'pm_cli.py refresh'
echo "=== MainPID after (must still be 850993) ==="
systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "=== legacy DB untouched? (mtime; not written by cron install) ==="
stat -c '%n mtime=%y size=%s' /home/azureuser/trading_corp/data/trading_corp.db 2>/dev/null || echo "(legacy db stat n/a)"
} > "$OUT" 2>&1
echo "CRONINST_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM cron 03:20 UTC INSTALL (idempotent, append-only) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 40; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_croninst.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
