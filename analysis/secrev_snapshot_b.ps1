# secrev_snapshot_b.ps1 -- PRE-STEP (item 1): fresh Azure OS-disk snapshot tc-prod-os-20261124b.
# az control-plane only (snapshot create). Does NOT touch the box OS / no run-command / no restart.
param([string]$Rg='rg-shared-prod',[string]$Vm='tc-prod-vm',[string]$SnapName='tc-prod-os-20261124b',
  [string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$diskId=(az vm show -g $Rg -n $Vm --query "storageProfile.osDisk.managedDisk.id" -o tsv --only-show-errors 2>$null)
if(-not $diskId){ Write-Output "ERROR: no OS disk id. Abort."; exit 1 }
Write-Output ("osDisk: " + $diskId)
$exists=(az snapshot show -g $Rg -n $SnapName --query "name" -o tsv --only-show-errors 2>$null)
if($exists){ Write-Output ("snapshot '" + $SnapName + "' already exists -- not recreating.") }
else { Write-Output ("creating snapshot '" + $SnapName + "' ..."); az snapshot create -g $Rg -n $SnapName --source $diskId --incremental false -o none --only-show-errors 2>$null }
$snap=az snapshot show -g $Rg -n $SnapName --query "{name:name,state:provisioningState,created:timeCreated,src:creationData.sourceResourceId}" -o json --only-show-errors 2>$null | Out-String
[IO.File]::WriteAllText((Join-Path $OutDir '15_snapshot_b.json'),$snap,$enc)
Write-Output $snap.Trim()
Write-Output ("SNAPSHOT: " + $(if($snap -match '"state"\s*:\s*"Succeeded"'){'READY (Succeeded)'}else{'CHECK state above'}))
