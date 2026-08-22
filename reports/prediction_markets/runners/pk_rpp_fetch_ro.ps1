# pk_rpp_fetch_ro.ps1 -- retrieve the already-generated /tmp/pm_rpp_out.txt (last probe run, ~315 lines)
# from the box in sub-cap chunks. NO re-run, READ-ONLY, cleans up /tmp at the end.
# Run: powershell -ep bypass -f .\pk_rpp_fetch_ro.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_rppf_chunk.sh'
[IO.File]::WriteAllText($tf, "test -f /tmp/pm_rpp_out.txt && wc -l < /tmp/pm_rpp_out.txt || echo NO_FILE`n", $enc)
Write-Host "== RETRIEVE PROBE OUTPUT (chunked, no re-run) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
$per = 30
for ($n = 0; $n -lt 12; $n++) {
    $a = $n * $per + 1
    $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_rpp_out.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
[IO.File]::WriteAllText($tf, "rm -f /tmp/pm_probe.b64 /tmp/pm_rpp_out.txt`n", $enc)
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
Remove-Item $tf -ErrorAction SilentlyContinue
