# secrev_batch0_az.ps1 -- BATCH 0 (Jack-approved): Azure OS-disk snapshot (rollback point) + patch ASSESSMENT.
# az control-plane only. Snapshot CREATE is a write (new Azure resource); assess-patches is an ASSESSMENT
# (reads pending patches, installs NOTHING). Does NOT touch the box OS, does NOT restart, does NOT run-command.
param(
  [string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis',
  [string]$Rg = 'rg-shared-prod',
  [string]$Vm = 'tc-prod-vm',
  [string]$SnapName = 'tc-prod-os-20261124'
)
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Output "=== resolve OS managed-disk id ==="
$diskId = (az vm show -g $Rg -n $Vm --query "storageProfile.osDisk.managedDisk.id" -o tsv --only-show-errors 2>$null)
if(-not $diskId){ Write-Output "ERROR: could not resolve OS disk id. Aborting (nothing created)."; exit 1 }
Write-Output ("osDisk: " + $diskId)

Write-Output "=== snapshot: create (idempotent) ==="
$exists = (az snapshot show -g $Rg -n $SnapName --query "name" -o tsv --only-show-errors 2>$null)
if($exists){
  Write-Output ("snapshot '" + $SnapName + "' ALREADY EXISTS -- not recreating.")
} else {
  Write-Output ("creating snapshot '" + $SnapName + "' from OS disk (full, incremental=false) ...")
  $create = az snapshot create -g $Rg -n $SnapName --source $diskId --incremental false -o json --only-show-errors 2>$null | Out-String
  if($create.Trim()){ [IO.File]::WriteAllText((Join-Path $OutDir '08_batch0_snapshot.json'), $create, $enc) }
}

Write-Output "=== snapshot: verify exists ==="
$snap = az snapshot show -g $Rg -n $SnapName --query "{name:name, state:provisioningState, sizeGb:diskSizeGb, created:timeCreated, src:creationData.sourceResourceId}" -o json --only-show-errors 2>$null | Out-String
if($snap.Trim()){
  [IO.File]::WriteAllText((Join-Path $OutDir '08_batch0_snapshot_show.json'), $snap, $enc)
  Write-Output $snap.Trim()
  $ok = ($snap -match '"state"\s*:\s*"Succeeded"')
  Write-Output ("SNAPSHOT ROLLBACK POINT: " + $(if($ok){'READY (provisioning Succeeded)'}else{'CHECK state above'}))
} else { Write-Output "ERROR: snapshot show returned nothing -- snapshot may not exist." }

Write-Output "=== assess-patches (ASSESSMENT ONLY -- installs nothing) ==="
$assess = az vm assess-patches -g $Rg -n $Vm -o json --only-show-errors 2>$null | Out-String
if($assess.Trim()){
  [IO.File]::WriteAllText((Join-Path $OutDir '09_assess_patches.json'), $assess, $enc)
  try {
    $a = $assess | ConvertFrom-Json
    Write-Output ("assessment: status=" + $a.status + " critical+security=" + $a.criticalAndSecurityPatchCount + " other=" + $a.otherPatchCount + " rebootPending=" + $a.rebootPending + " started=" + $a.startDateTime)
  } catch { Write-Output "assessment saved (could not parse summary; see 09_assess_patches.json)" }
} else { Write-Output "assess-patches returned nothing (may need a few minutes; or permission). See classifier note." }

Write-Output "`nWrote snapshot+assessment evidence to $OutDir"
