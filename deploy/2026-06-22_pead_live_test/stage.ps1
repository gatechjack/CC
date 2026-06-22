# PEAD STEP 3 — GATE 1 staging (PowerShell). Transfers the branch checkout to the
# prod VM (~/pead_branch) for the live test. Self-locating (cwd-independent).
# Run from PowerShell:
#   & ".\.claude\worktrees\robinhood-pead-2026-06-20\deploy\2026-06-22_pead_live_test\stage.ps1"
# Read-only on prod's running engine: drops files into ~/pead_branch only; no
# install, no deployed-file change, no restart. Uses your existing ssh/scp auth.
$ErrorActionPreference = "Stop"
$RemoteHost = "azureuser@trading.jacksumner.com"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Tar  = Join-Path $env:TEMP "pead_branch.tgz"
Write-Output "staging from: $Root"
tar -czf $Tar "--exclude=__pycache__" "--exclude=*.pyc" -C $Root trading_corp config deploy/2026-06-22_pead_live_test
if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
Write-Output ("tarball: {0:N1} MB" -f ((Get-Item $Tar).Length / 1MB))
scp $Tar "${RemoteHost}:pead_branch.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed ($LASTEXITCODE)" }
ssh $RemoteHost "rm -rf pead_branch && mkdir pead_branch && tar xzf pead_branch.tgz -C pead_branch && echo STAGED_OK"
if ($LASTEXITCODE -ne 0) { throw "remote extract failed ($LASTEXITCODE)" }
Write-Output "DONE. On prod: cd ~/pead_branch then bash deploy/2026-06-22_pead_live_test/gate1_run.sh"
