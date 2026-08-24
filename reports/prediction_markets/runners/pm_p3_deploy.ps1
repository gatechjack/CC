$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# PM P3 DEPLOY (local orchestrator). Operator pastes ONE line AFTER MARKET CLOSE on Jack's go:
#   powershell -ep bypass -f .\pm_p3_deploy.ps1
# Steps: [1-2] archive ONLY the 11 PM files + build a sha256 reference; [scp] tarball+ref to box (azureuser);
# [box] run the FAIL-CLOSED deploy as root (az run-command) -- manifest-assert, chain-of-custody, GOTCHA-3
# path proof, backup-before-overwrite, GOTCHA-2 gate, restart pm_web ONLY if gate passes, healthz, sync-names
# (azureuser), VERIFY RENDER; [5] advance prod-live for the deployed artifacts ONLY if DEPLOY_VERDICT=OK and
# the prod-live worktree is clean/on-anchor (else prints the manual command). Read the box output to judge.
$h = "azureuser@trading.jacksumner.com"
$wt = "C:\Users\AA Incorporado\cc-pm-p3-wt"
$cwd = "C:\Users\AA Incorporado\cc"
$stage = "$cwd\pm_p3_deploy.tgz"
$ref = "$cwd\pm_p3_deploy.sha256"
$boxsh = "$cwd\pm_p3_deploy_box.sh"
$rg = "RG-SHARED-PROD"; $vm = "tc-prod-vm"
$branch = "prediction-markets-p3-2026-08-24"
$plwt = "C:\Users\AA Incorporado\cc-prodlive-cp7-wt"
$files = @(
  "trading_corp/prediction_markets/positions.py",
  "trading_corp/prediction_markets/names.py",
  "trading_corp/prediction_markets/stats.py",
  "trading_corp/prediction_markets/web/app.py",
  "trading_corp/prediction_markets/web/static/pm.css",
  "trading_corp/prediction_markets/web/templates/pm_macros.html",
  "trading_corp/prediction_markets/web/templates/pm_whale.html",
  "trading_corp/prediction_markets/web/templates/pm_whale_overview.html",
  "trading_corp/prediction_markets/web/templates/partials/pm_position_rows.html",
  "trading_corp/prediction_markets/web/templates/partials/pm_scoreboard_table.html",
  "trading_corp/scripts/pm_cli.py"
)
if (-not (Test-Path $boxsh)) { Write-Host "MISSING box script: $boxsh - STOP"; exit 1 }

Write-Host "[local] git archive 11 PM files from $branch HEAD"
git -C "$wt" archive --format=tar.gz -o "$stage" HEAD @files
if ($LASTEXITCODE -ne 0) { Write-Host "GIT ARCHIVE FAILED - STOP"; exit 1 }
Write-Host ("[local] HEAD: " + (git -C "$wt" rev-parse --short HEAD) + "  stage sha256: " + (Get-FileHash -Algorithm SHA256 "$stage").Hash.ToLower())

# build the LF sha256 reference from the SAME tarball (box compares box-extracted sha == this)
$tmp = Join-Path $env:TEMP ("pm_p3_ref_" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
tar -xzf "$stage" -C "$tmp"
if ($LASTEXITCODE -ne 0) { Write-Host "LOCAL EXTRACT FAILED - STOP"; Remove-Item "$tmp" -Recurse -Force; exit 1 }
$lines = @(); $ok = $true
foreach ($f in $files) {
  $p = Join-Path $tmp ($f -replace '/','\')
  if (-not (Test-Path $p)) { Write-Host "MISSING in archive: $f"; $ok = $false; continue }
  $lines += ((Get-FileHash -Algorithm SHA256 $p).Hash.ToLower() + " " + $f)
}
if (-not $ok) { Remove-Item "$tmp" -Recurse -Force; exit 1 }
[IO.File]::WriteAllText($ref, ([string]::Join("`n", $lines) + "`n"), (New-Object Text.UTF8Encoding $false))
Remove-Item "$tmp" -Recurse -Force
Write-Host ("[local] wrote reference manifest (" + $files.Count + " files, LF)")

Write-Host "[local] scp stage + reference + box script to the box..."
scp -o StrictHostKeyChecking=accept-new "$stage" ($h + ":pm_p3_deploy.tgz")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP tgz FAILED - STOP"; exit 1 }
scp -o StrictHostKeyChecking=accept-new "$ref" ($h + ":pm_p3_deploy.sha256")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP ref FAILED - STOP"; exit 1 }
scp -o StrictHostKeyChecking=accept-new "$boxsh" ($h + ":pm_p3_deploy_box.sh")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP box.sh FAILED - STOP"; exit 1 }

Write-Host "[box] running the FAIL-CLOSED deploy as root (az runs the scp'd box script)..."
# Pass a SHORT command that runs the scp'd file. Inline b64 blew the Windows command-line length cap;
# bash reading the file has no length/quoting/splitting limit.
$out = az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts "bash /home/azureuser/pm_p3_deploy_box.sh" --query "value[0].message" -o tsv 2>&1 | Out-String
Write-Host $out
$deployOk = ($out -match 'DEPLOY_VERDICT=OK')
Remove-Item "$stage" -ErrorAction SilentlyContinue
Remove-Item "$ref" -ErrorAction SilentlyContinue

# prod-live advance is DECOUPLED (Jack's ruling): the ledger is written by a SEPARATE runner, after a
# human reads this output and agrees the box is right. This runner does NOT touch prod-live.
Write-Host ""
Write-Host "=== prod-live advance = a SEPARATE, deliberate step (decoupled from the deploy) ==="
Write-Host "prod-live is the LEDGER of what is on the box; write it only AFTER you have READ the output"
Write-Host "above and agree the deploy is right. 'The script said OK' and 'Jack read it and agrees' are"
Write-Host "different standards, and the ledger deserves the second. Deploy = the risky part; ledger"
Write-Host "advance = bookkeeping. Not coupled."
Write-Host ""
Write-Host "If the box output shows DEPLOY_VERDICT=OK + GATE_PASS + HEALTHZ_OK + ENGINE_PID_UNCHANGED=GOOD,"
Write-Host ("advance prod-live (records the 11 PM artifacts from " + $branch + " into worktree")
Write-Host "cc-prodlive-cp7-wt, byte-verified vs origin/prod-live, then pushes) by pasting THIS ONE LINE:"
Write-Host ""
Write-Host "  powershell -ep bypass -f .\pm_p3_prodlive_advance.ps1"
Write-Host ""
Write-Host ("=== DEPLOY DONE. deployOk=" + $deployOk + ". Box output to read: BACKUP_KEPT_AT (early), GATE_PASS, RESTART_OK, HEALTHZ_OK, DEPLOY_VERDICT=OK, SYNC_NAMES_*, RENDER_*, ENGINE_PID_UNCHANGED=GOOD. ===")
