<#
  deploy_gate_battery.ps1  -  MACE deploy pre-flight gate battery (2026-08-21).

  ONE dry-run-able runnable that bundles the gates a MACE file-overwrite deploy
  must pass BEFORE the operator swaps files + restarts. Read-only by default
  (dry-run); -Apply additionally writes the on-box backup + rollback.sh. It NEVER
  overwrites/deploys and NEVER restarts - it only GATES.

  Gates:
    1. changed-file discovery + STAGED == CHANGED assertion
         (git-changed runtime files must equal the intended deploy set -
          this is the gate that would have caught the config.py omission).
    2. py_compile     - every staged .py compiles in the box venv.
    3. drift-gate     - on-box CURRENT content == the deploy BASE content,
                        LF-normalized on BOTH sides (never local git md5, which
                        false-drifts on Windows CRLF), and the branch actually
                        changes it.
    4. backup + rollback.sh  - snapshot the current on-box files + emit a restore
                        script (only with -Apply; dry-run prints the plan).
    5. boot-verify checklist - printed for the post-restart step (Phase 5).

  KNOWN ISSUE (deferred, 2026-08-22): the -Apply path's Gate-4 backup writer pipes a here-doc + this
    script down a single ssh stdin, which HANGS on the here-doc terminator. DRY-RUN (default) is
    unaffected. Until fixed, create the -Apply backup out-of-band (printf-based writer, no
    here-doc-over-stdin), or apply the 1-line fix: sanitize stdin with tr -d '\r\357\273\277' and
    stop nesting the here-doc inside the piped script.

  Usage (dry-run against the P1.5 branch):
    powershell -ep bypass -f .\scripts\deploy_gate_battery.ps1 `
      -Branch mace-p15-offhours-fix-2026-08-21 -Base e298ea8 `
      -Worktree "C:\Users\AA Incorporado\cc-mace-phase1-wt" `
      -DeployFiles trading_corp/mace/execution.py,trading_corp/mace/loops.py,trading_corp/mace/manager.py
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Branch,
  [string]$Base = "prod-live",
  [Parameter(Mandatory=$true)][string]$Worktree,
  [string[]]$DeployFiles = @(),
  [string]$BoxHost = "azureuser@172.171.189.116",
  [string]$BoxRoot = "/home/azureuser/trading_corp",
  [switch]$Apply
)

# `powershell -File` passes a comma-list as ONE string; split + trim so both the
# runner form (-DeployFiles a,b,c) and a real PS array work.
$DeployFiles = @($DeployFiles | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
$ErrorActionPreference = "Continue"   # native git/ssh stderr must not halt the gates
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$SSH = @("-o","BatchMode=yes","-o","ConnectTimeout=20","-o","StrictHostKeyChecking=accept-new")
$results = [ordered]@{}   # gate -> $true/$false
function Line($c){ Write-Host ("=" * 72); Write-Host $c; Write-Host ("=" * 72) }
function Md5Lf([string]$text){
  if ($null -eq $text) { $text = "" }
  # LF-normalize + trim trailing whitespace so ssh-cat vs git-show capture the
  # final newline identically (a trailing-newline diff is not meaningful drift).
  $lf = ($text -replace "`r","").TrimEnd()
  $md5 = [System.Security.Cryptography.MD5]::Create()
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($lf)
  ($md5.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join ""
}
function GitShow([string]$ref,[string]$file){
  # raw file content at a ref; $null if the path does not exist there
  $out = & git -C $Worktree show "${ref}:${file}" 2>$null
  if ($LASTEXITCODE -ne 0) { return $null }
  ($out -join "`n")
}
function SshCat([string]$file){
  $out = & ssh @SSH $BoxHost "cat '$BoxRoot/$file' 2>/dev/null; echo GATE_RC=`$?" 2>$null
  $joined = ($out -join "`n")
  if ($joined -match "GATE_RC=(\d+)") { $rc = [int]$Matches[1] } else { $rc = 1 }
  $content = ($joined -replace "GATE_RC=\d+\s*$","")
  return @{ rc = $rc; content = $content }
}

Line "MACE DEPLOY GATE BATTERY  branch=$Branch  base=$Base  mode=$(if($Apply){'APPLY'}else{'DRY-RUN'})"
Write-Host "worktree=$Worktree  box=${BoxHost}:$BoxRoot"

# --- Gate 1: changed-file discovery + STAGED == CHANGED ----------------------
Line "GATE 1  changed-file discovery + staged==changed"
$allChanged = (& git -C $Worktree diff --name-only $Base $Branch 2>$null) | Where-Object { $_ -ne "" }
Write-Host "git-changed ($Base..$Branch): $($allChanged.Count) file(s)"
$allChanged | ForEach-Object { Write-Host "    $_" }
# runtime = deployable (trading_corp/ or config/), excluding tests + docs
$runtimeChanged = $allChanged | Where-Object {
  ($_ -like "trading_corp/*" -or $_ -like "config/*") -and ($_ -notlike "tests/*") -and ($_ -notlike "*.md")
}
Write-Host ""
Write-Host "runtime-changed (deployable): $($runtimeChanged.Count)"
$runtimeChanged | ForEach-Object { Write-Host "    $_" }

if ($DeployFiles.Count -eq 0) {
  $staged = @($runtimeChanged)
  Write-Host ""
  Write-Host "NOTE: -DeployFiles omitted -> staged auto-derived from git-changed runtime set."
  Write-Host "      staged==changed is self-consistent; operator should still confirm the set."
  $results["staged==changed"] = $true
} else {
  $staged = @($DeployFiles)
  $missing = @($runtimeChanged | Where-Object { $staged -notcontains $_ })   # changed but NOT staged (config.py case)
  $extra   = @($staged | Where-Object { $runtimeChanged -notcontains $_ })   # staged but NOT changed
  Write-Host ""
  Write-Host "staged (intended deploy set): $($staged.Count)"
  $staged | ForEach-Object { Write-Host "    $_" }
  if ($missing.Count -gt 0) { Write-Host ""; Write-Host "  !! CHANGED-BUT-NOT-STAGED (would silently ship stale):"; $missing | ForEach-Object { Write-Host "       $_" } }
  if ($extra.Count   -gt 0) { Write-Host ""; Write-Host "  !! STAGED-BUT-NOT-CHANGED (spurious / wrong base):";     $extra   | ForEach-Object { Write-Host "       $_" } }
  $results["staged==changed"] = ($missing.Count -eq 0 -and $extra.Count -eq 0)
}
Write-Host ""
Write-Host ("GATE 1: " + $(if($results["staged==changed"]){"PASS"}else{"FAIL"}))

# --- Gate 2: py_compile in the box venv --------------------------------------
Line "GATE 2  py_compile (box venv)"
$pyFiles = @($staged | Where-Object { $_ -like "*.py" })
$pyOk = $true
if ($pyFiles.Count -eq 0) {
  Write-Host "no .py in staged set - skipping"
} else {
  & ssh @SSH $BoxHost "rm -rf /tmp/gate_pyc; mkdir -p /tmp/gate_pyc" 2>$null | Out-Null
  foreach ($f in $pyFiles) {
    $leaf = ($f -replace "/","__")
    & scp @SSH (Join-Path $Worktree $f) "${BoxHost}:/tmp/gate_pyc/$leaf" 2>$null | Out-Null
    $r = & ssh @SSH $BoxHost "$BoxRoot/venv/bin/python -m py_compile /tmp/gate_pyc/$leaf && echo OK || echo FAIL" 2>$null
    $ok = ($r -join "") -match "OK"
    if (-not $ok) { $pyOk = $false }
    Write-Host ("    {0,-45} {1}" -f $f, $(if($ok){"OK"}else{"FAIL"}))
  }
  & ssh @SSH $BoxHost "rm -rf /tmp/gate_pyc" 2>$null | Out-Null
}
$results["py_compile"] = $pyOk
Write-Host ""; Write-Host ("GATE 2: " + $(if($pyOk){"PASS"}else{"FAIL"}))

# --- Gate 3: on-box drift-gate (LF both sides) -------------------------------
Line "GATE 3  drift-gate  (on-box CURRENT vs deploy BASE, LF-normalized)"
$driftOk = $true
foreach ($f in $staged) {
  $box   = SshCat $f
  $baseC = GitShow $Base   $f
  $brC   = GitShow $Branch $f
  $boxMd5  = Md5Lf $box.content
  $baseMd5 = Md5Lf $baseC
  $brMd5   = Md5Lf $brC
  $onboxMissing = ($box.rc -ne 0)
  $baseMatch = (-not $onboxMissing) -and ($boxMd5 -eq $baseMd5)
  $changes   = ($baseMd5 -ne $brMd5)
  $status = "OK"
  if ($onboxMissing) { $status = "NEW (not on box yet)" }
  elseif (-not $baseMatch) { $status = "!! DRIFT (box != base)"; $driftOk = $false }
  elseif (-not $changes)   { $status = "!! NO-OP (branch == base)" }
  Write-Host ("    {0,-42} box={1}  base={2}  branch={3}  {4}" -f $f, $boxMd5.Substring(0,8), $(if($baseC){$baseMd5.Substring(0,8)}else{"--------"}), $brMd5.Substring(0,8), $status)
}
$results["drift-gate"] = $driftOk
Write-Host ""; Write-Host ("GATE 3: " + $(if($driftOk){"PASS"}else{"FAIL (investigate on-box drift before deploy)"}))

# --- Gate 4: backup + rollback.sh --------------------------------------------
Line "GATE 4  backup + rollback.sh"
$stamp = (Get-Date -Format "yyyyMMdd_HHmmss")
$bakDir = "/home/azureuser/mace_gate_bak_$stamp"
Write-Host "backup dir: $bakDir"
Write-Host "would snapshot (current on-box copies):"
$staged | ForEach-Object { Write-Host "    $BoxRoot/$_" }
if ($Apply) {
  $mkList = ($staged | ForEach-Object { "mkdir -p `"$bakDir/`$(dirname $_)`"; cp -p `"$BoxRoot/$_`" `"$bakDir/$_`"" }) -join "; "
  $roll = "#!/usr/bin/env bash`nset -euo pipefail`n" + (($staged | ForEach-Object { "cp -p `"$bakDir/$_`" `"$BoxRoot/$_`"" }) -join "`n") + "`necho 'ROLLBACK COMPLETE - restart the engine to load the restored files.'`n"
  $cmd = "mkdir -p '$bakDir'; $mkList; cat > '$bakDir/rollback.sh' <<'ROLL'`n$roll`nROLL`nchmod +x '$bakDir/rollback.sh'; echo BACKUP_OK; ls -la '$bakDir'"
  $out = $cmd | & ssh @SSH $BoxHost "tr -d '\r' | bash" 2>$null
  Write-Host ($out -join "`n")
  $results["backup"] = (($out -join "") -match "BACKUP_OK")
} else {
  Write-Host "(dry-run: no backup written; rollback.sh would 'cp -p' each file back and print a restart reminder)"
  $results["backup"] = $true
}
Write-Host ""; Write-Host ("GATE 4: " + $(if($results["backup"]){"PASS"}else{"FAIL"}))

# --- Gate 5: boot-verify checklist -------------------------------------------
Line "GATE 5  boot-verify checklist  (run AFTER the operator swaps files + restarts)"
@(
  "[ ] config_hash loaded == expected (journalctl 'MACE wired ... config_hash=')",
  "[ ] /mace HTTP 200  (curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:8000/mace)",
  "[ ] open rungs intact + managed (mace_rung status=open count unchanged; mace_rung_live accruing marks in-window)",
  "[ ] halt latch ARM -> HALT -> ARM round-trips",
  "[ ] 0 NEW tracebacks for robinhood_mace since restart (journalctl -u trading-corp | grep mace)",
  "[ ] NO restart after 15:45 ET while P1.5 is UNDEPLOYED (off-hours catch-up hazard - fixed by this very branch)"
) | ForEach-Object { Write-Host "    $_" }
$results["boot-verify-checklist"] = $true

# --- summary -----------------------------------------------------------------
Line "SUMMARY"
$allPass = $true
foreach ($k in $results.Keys) {
  $p = $results[$k]; if (-not $p) { $allPass = $false }
  Write-Host ("    {0,-24} {1}" -f $k, $(if($p){"PASS"}else{"FAIL"}))
}
Write-Host ""
Write-Host ("OVERALL: " + $(if($allPass){"PASS - gates clear (operator still owns the swap + restart)"}else{"FAIL - do NOT deploy; resolve the failing gate"}))
if (-not $allPass) { exit 1 }
