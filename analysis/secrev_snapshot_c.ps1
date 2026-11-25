# secrev_snapshot_c.ps1 -- Batch 4 PRE-STEP: fresh OS-disk snapshot tc-prod-os-20261124c. az control-plane only.
param([string]$Rg='rg-shared-prod',[string]$Vm='tc-prod-vm',[string]$SnapName='tc-prod-os-20261124c',
  [string]$OutDir='C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference='Continue'
$enc=New-Object System.Text.UTF8Encoding $false
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$diskId=(az vm show -g $Rg -n $Vm --query "storageProfile.osDisk.managedDisk.id" -o tsv --only-show-errors 2>$null)
if(-not $diskId){ Write-Output "ERROR: no OS disk id. Abort."; exit 1 }
$exists=(az snapshot show -g $Rg -n $SnapName --query "name" -o tsv --only-show-errors 2>$null)
if($exists){ Write-Output ("snapshot '"+$SnapName+"' already exists -- not recreating.") }
else { Write-Output ("creating snapshot '"+$SnapName+"' ..."); az snapshot create -g $Rg -n $SnapName --source $diskId --incremental false -o none --only-show-errors 2>$null }
$snap=az snapshot show -g $Rg -n $SnapName --query "{name:name,state:provisioningState,created:timeCreated}" -o json --only-show-errors 2>$null | Out-String
[IO.File]::WriteAllText((Join-Path $OutDir '22_snapshot_c.json'),$snap,$enc)
Write-Output $snap.Trim()
Write-Output ("SNAPSHOT: "+$(if($snap -match '"state"\s*:\s*"Succeeded"'){'READY (Succeeded)'}else{'CHECK state above'}))
