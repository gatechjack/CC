# secrev_az_vnet.ps1 -- PHASE 2 READ-ONLY az probe (B1): who else is on the VNet/subnet with tc-prod-vm.
# READ-ONLY az list/show only. No writes, no run-command. Determines if 0.0.0.0:8000 / xrdp:3389 are
# live intra-VNet exposures (neighbors present) or defense-in-depth only (tc-prod-vm alone).
param([string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis')
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log = New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
function AzJson([string[]]$a){
  $raw = & az @a -o json --only-show-errors 2>$null | Out-String
  if($raw.Trim().Length -gt 0){ try { return ($raw | ConvertFrom-Json) } catch { return $null } }
  return $null
}
$TARGET_SUBNET = 'tc-prod-subnet'

L "=== ALL VMs IN SUBSCRIPTION ==="
$vms = AzJson @('vm','list','-d')
$rows = @()
foreach($v in $vms){
  $rows += [pscustomobject]@{ name=$v.name; rg=$v.resourceGroup; size=$v.hardwareProfile.vmSize; power=$v.powerState; priv=($v.privateIps); pub=($v.publicIps) }
}
L ("VM count in subscription: " + $vms.Count)
foreach($r in ($rows | Sort-Object name)){ L ("  vm=" + $r.name + " rg=" + $r.rg + " size=" + $r.size + " power=" + $r.power + " priv=" + $r.priv + " pub=" + $r.pub) }

L "=== VNETS + SUBNETS + PEERINGS ==="
$vnets = AzJson @('network','vnet','list')
foreach($vn in $vnets){
  L ("vnet=" + $vn.name + " rg=" + $vn.resourceGroup + " space=" + ($vn.addressSpace.addressPrefixes -join ',') )
  foreach($sn in $vn.subnets){
    $nicCount = if($sn.ipConfigurations){ $sn.ipConfigurations.Count } else { 0 }
    L ("   subnet=" + $sn.name + " prefix=" + ($sn.addressPrefix + ($sn.addressPrefixes -join ',')) + " ipConfigs=" + $nicCount + " nsg=" + $(if($sn.networkSecurityGroup){ ($sn.networkSecurityGroup.id -split '/')[-1] }else{'(none)'}))
  }
  if($vn.virtualNetworkPeerings -and $vn.virtualNetworkPeerings.Count){
    foreach($pr in $vn.virtualNetworkPeerings){ L ("   PEERING=" + $pr.name + " state=" + $pr.peeringState + " remote=" + (($pr.remoteVirtualNetwork.id -split '/')[-1])) }
  } else { L "   peerings: (none)" }
}

L "=== NICS ON TARGET SUBNET ($TARGET_SUBNET) ==="
$nics = AzJson @('network','nic','list')
$onSubnet = @()
foreach($nic in $nics){
  foreach($ipc in $nic.ipConfigurations){
    $sid = $ipc.subnet.id
    if($sid -and ($sid -split '/')[-1] -eq $TARGET_SUBNET){
      $vmName = if($nic.virtualMachine){ ($nic.virtualMachine.id -split '/')[-1] } else { '(unattached)' }
      $onSubnet += [pscustomobject]@{ nic=$nic.name; vm=$vmName; priv=$ipc.privateIpAddress; pub=$(if($ipc.publicIpAddress){'yes'}else{'no'}) }
    }
  }
}
L ("NICs on " + $TARGET_SUBNET + ": " + $onSubnet.Count)
foreach($o in ($onSubnet | Sort-Object priv)){ L ("  nic=" + $o.nic + " vm=" + $o.vm + " priv=" + $o.priv + " hasPublicIp=" + $o.pub) }

$neighbors = @($onSubnet | Where-Object { $_.vm -ne 'tc-prod-vm' })
L "=== VERDICT ==="
if($neighbors.Count -eq 0){
  L "tc-prod-vm appears ALONE on $TARGET_SUBNET (no other attached NICs). => 0.0.0.0:8000 and xrdp:3389 stay MEDIUM (defense-in-depth; no co-tenant on subnet)."
} else {
  L ("NEIGHBORS PRESENT on " + $TARGET_SUBNET + " (" + $neighbors.Count + "). => RAISE 0.0.0.0:8000 and xrdp:3389 to HIGH (live intra-subnet exposure):")
  foreach($n in $neighbors){ L ("   neighbor vm=" + $n.vm + " priv=" + $n.priv) }
}
L "NOTE: also weigh VNet peerings above; a peered VNet can reach intra-VNet-allowed ports too."

[IO.File]::WriteAllText((Join-Path $OutDir '07_az_vnet.txt'), (($log -join "`n") + "`n"), $enc)
Write-Output ("`nWrote: " + (Join-Path $OutDir '07_az_vnet.txt'))
