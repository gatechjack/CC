# pk_pm_cron_check.ps1 -- READ-ONLY pre-install slot check for the 03:20 UTC nightly refresh cron.
# Re-verifies the 03:20 slot is clear of cron (azureuser + root + system drop-ins) AND systemd timers
# immediately before install. No mutation. Run: powershell -ep bypass -f .\pk_pm_cron_check.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_cronchk_box.sh'
$bash = @'
OUT=/tmp/pm_cronchk.txt
{
echo "=== MainPID (must be 850993) ==="
systemctl show -p MainPID trading-corp.service 2>/dev/null
echo "=== date UTC ==="
date -u
echo "=== azureuser crontab (target for install) ==="
crontab -u azureuser -l 2>&1 || echo "(no azureuser crontab yet)"
echo "=== root crontab (run-command context) ==="
crontab -l 2>&1 || echo "(no root crontab)"
echo "=== /etc/crontab ==="
cat /etc/crontab 2>/dev/null | grep -vE '^\s*#' | grep -vE '^\s*$' || echo "(none/empty)"
echo "=== /etc/cron.d drop-ins ==="
ls -la /etc/cron.d/ 2>/dev/null
for f in /etc/cron.d/*; do [ -f "$f" ] && echo "--- $f ---" && grep -vE '^\s*#' "$f" | grep -vE '^\s*$'; done
echo "=== systemd timers (all) ==="
systemctl list-timers --all --no-pager 2>/dev/null
echo "=== CONFLICT SCAN: anything scheduled in the 03:00-03:59 UTC hour ==="
echo "-- cron lines starting minute in hour 3 (field2==3) --"
{ crontab -u azureuser -l 2>/dev/null; crontab -l 2>/dev/null; cat /etc/crontab 2>/dev/null; cat /etc/cron.d/* 2>/dev/null; } \
  | grep -vE '^\s*#' | awk 'NF>=6 && $2=="3"{print}' || echo "(none)"
echo "-- timers with 03: next-run --"
systemctl list-timers --all --no-pager 2>/dev/null | grep -E '03:[0-9]{2}:' || echo "(none in 03: hour)"
echo "=== does the pm refresh line already exist? (idempotency check) ==="
crontab -u azureuser -l 2>/dev/null | grep -F 'pm_cli.py refresh' || echo "(not present -- clean install)"
} > "$OUT" 2>&1
echo "CRONCHK_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM cron 03:20 UTC pre-install slot check (READ-ONLY) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 80; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_cronchk.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
