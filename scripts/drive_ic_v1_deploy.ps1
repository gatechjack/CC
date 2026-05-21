# Driver: chunked IC v1 prod deploy.
#
# Prereqs (already done by the prior session):
#   - xz tarball at "$env:TEMP\ic_v1_full.tar.xz"  (~155 KB)
#   - 11 base64 chunks at "$env:TEMP\chunk.aa" through "$env:TEMP\chunk.ak"
#     (10 x 20000 bytes + 1 x ~6600 bytes)
#   - Finalize script at scripts/ic_v1_deploy_finalize.sh
#
# Run from PowerShell at the repo root:
#   .\scripts\drive_ic_v1_deploy.ps1
#
# Wall time ~5 min (11 az invocations at ~20-30s each, then ~30s for the
# final assembly + restart + verify). Each az call returns synchronously.
$ErrorActionPreference = "Stop"
$VM = "tc-prod-vm"
$RG = "rg-shared-prod"
$suffixes = @("aa","ab","ac","ad","ae","af","ag","ah","ai","aj","ak")

Write-Host ""
Write-Host "==================================================================="
Write-Host " IC v1 prod deploy - chunked transport ($($suffixes.Count) chunks)"
Write-Host "==================================================================="

# Phase 1: reset prod chunks dir
Write-Host ""
Write-Host "==> Resetting /tmp/ic_v1_chunks on prod"
$resetScript = "$env:TEMP\ic_reset.sh"
[System.IO.File]::WriteAllText($resetScript, "sudo rm -rf /tmp/ic_v1_chunks`nmkdir -p /tmp/ic_v1_chunks`necho ready`n")
az vm run-command invoke -n $VM -g $RG --command-id RunShellScript --scripts "@$resetScript" --query "value[0].message" -o tsv | Out-Host

# Phase 2: upload each chunk
for ($i = 0; $i -lt $suffixes.Count; $i++) {
    $suffix = $suffixes[$i]
    $n = "{0:00}" -f ($i + 1)
    $chunkFile  = "$env:TEMP\chunk.$suffix"
    $scriptFile = "$env:TEMP\ic_upload_$n.sh"

    if (-not (Test-Path $chunkFile)) {
        throw "Missing chunk file: $chunkFile (did the prior tar+split step run?)"
    }
    $chunkBytes = [System.IO.File]::ReadAllBytes($chunkFile)
    $chunkText  = [System.Text.Encoding]::ASCII.GetString($chunkBytes)

    # Build the upload script with strict LF line endings.
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("set -e`n")
    [void]$sb.Append("mkdir -p /tmp/ic_v1_chunks`n")
    [void]$sb.Append("cat > /tmp/ic_v1_chunks/chunk_$n.b64 <<'CHUNK_EOF'`n")
    [void]$sb.Append($chunkText)
    [void]$sb.Append("`nCHUNK_EOF`n")
    [void]$sb.Append("echo `"chunk_$n.b64: `$(wc -c < /tmp/ic_v1_chunks/chunk_$n.b64) bytes`"`n")
    [System.IO.File]::WriteAllText($scriptFile, $sb.ToString())

    $sz = (Get-Item $scriptFile).Length
    Write-Host ""
    Write-Host "==> Uploading chunk $n/$($suffixes.Count) (script=$sz bytes)"
    az vm run-command invoke -n $VM -g $RG --command-id RunShellScript --scripts "@$scriptFile" --query "value[0].message" -o tsv | Out-Host
}

# Phase 3: final assembly + restart + verify
Write-Host ""
Write-Host "==================================================================="
Write-Host " Final stage: assemble + extract + restart + verify"
Write-Host "==================================================================="
$finalizeScript = "C:\Users\AA Incorporado\CC\scripts\ic_v1_deploy_finalize.sh"
if (-not (Test-Path $finalizeScript)) {
    throw "Missing finalize script: $finalizeScript"
}
# Normalize line endings on the finalize script to LF before shipping
$finalizeContent = [System.IO.File]::ReadAllText($finalizeScript) -replace "`r`n","`n"
$finalizeTmp = "$env:TEMP\ic_v1_deploy_finalize.sh"
[System.IO.File]::WriteAllText($finalizeTmp, $finalizeContent)
az vm run-command invoke -n $VM -g $RG --command-id RunShellScript --scripts "@$finalizeTmp" --query "value[0].message" -o tsv | Out-Host

Write-Host ""
Write-Host "==================================================================="
Write-Host " Deploy driver complete."
Write-Host "==================================================================="
