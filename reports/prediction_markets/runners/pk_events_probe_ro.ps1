# pk_events_probe_ro.ps1 -- READ-ONLY gamma /events tags-schema probe. Ships pm_events_probe.py to the
# box (base64), pipes to venv python3 (stdlib only, no trading_corp import), writes output to a /tmp
# file, chunk-retrieves it (defeats the ~4KB az cap), cleans up. Public no-auth APIs; no box mutation.
# Run: powershell -ep bypass -f .\pk_events_probe_ro.ps1
$ErrorActionPreference = 'Stop'
$py = [IO.File]::ReadAllText("C:\Users\AA Incorporado\cc-prediction-markets-wt\reports\prediction_markets\runners\pm_events_probe.py") -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_ev_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_ev.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pm_ev.b64 | /home/azureuser/trading_corp/venv/bin/python3 - > /tmp/pm_ev_out.txt 2>&1; echo RUN_DONE lines=`$(wc -l < /tmp/pm_ev_out.txt)`n", $enc)
Write-Host "== EVENTS TAG PROBE (READ-ONLY) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 400
$runStr = ($runMsg | Out-String)
if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30
$nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1
    $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_ev_out.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
[IO.File]::WriteAllText($tf, "rm -f /tmp/pm_ev.b64 /tmp/pm_ev_out.txt`n", $enc)
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
Remove-Item $tf -ErrorAction SilentlyContinue
