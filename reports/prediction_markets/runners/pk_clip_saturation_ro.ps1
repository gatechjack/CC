# pk_clip_saturation_ro.ps1 -- READ-ONLY (Item 2): run pm_clip_saturation_probe.py (cost-ROI distribution
# + _edge_factor clip saturation across the 12 roster whales) in an isolated ~/ scratch. Reads legacy DB
# mode=ro for the roster; no writes; scratch removed. Run: powershell -ep bypass -f .\pk_clip_saturation_ro.ps1
$ErrorActionPreference = 'Stop'
$W = "C:\Users\AA Incorporado\cc-prediction-markets-wt"
$tar = Join-Path $env:TEMP 'pm_cs.tgz'
if (Test-Path $tar) { Remove-Item $tar -Force }
tar.exe -czf $tar -C $W trading_corp/prediction_markets trading_corp/data trading_corp/__init__.py reports/prediction_markets/runners/pm_clip_saturation_probe.py
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_cs_chunk.sh'
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($tar))
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_cs.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
$bash = @'
S="${HOME:-/home/azureuser}/pm_cs_scratch"; OUT=/tmp/pm_cs_out.txt
{
rm -rf "$S"; mkdir -p "$S"; base64 -d /tmp/pm_cs.b64 | tar xzf - -C "$S"
cd "$S" && PYTHONPATH=. /home/azureuser/trading_corp/venv/bin/python reports/prediction_markets/runners/pm_clip_saturation_probe.py
rm -rf "$S"
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
$b2 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$first2 = $true
for ($i = 0; $i -lt $b2.Length; $i += $size) {
    $chunk = $b2.Substring($i, [Math]::Min($size, $b2.Length - $i))
    $op = if ($first2) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pm_cs.sh.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first2 = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pm_cs.sh.b64 > /tmp/pm_cs.sh && bash /tmp/pm_cs.sh`n", $enc)
Write-Host "== CLIP SATURATION PROBE (READ-ONLY) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 120; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_cs_out.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
[IO.File]::WriteAllText($tf, "rm -f /tmp/pm_cs.b64 /tmp/pm_cs.sh.b64 /tmp/pm_cs.sh /tmp/pm_cs_out.txt`n", $enc)
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
Remove-Item $tf, $tar -Force -ErrorAction SilentlyContinue
