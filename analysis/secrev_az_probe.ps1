# secrev_az_probe.ps1 -- PHASE 1 READ-ONLY Azure control-plane probe for tc-prod-vm.
# READ-ONLY az calls ONLY: vm list/show/get-instance-view, nic show, nsg show, subnet show, public-ip show.
# NO az vm run-command, NO assess-patches (that one is listed as a Jack-approved Phase-2 step), NO writes.
# Writes JSON + a summary into the worktree analysis dir. Pure ASCII.
param(
  [string]$OutDir = 'C:\Users\AA Incorporado\cc-secrev-2026-11-23-wt\analysis',
  [string]$Vm = 'tc-prod-vm'
)
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$enc = New-Object System.Text.UTF8Encoding $false
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log = New-Object System.Collections.Generic.List[string]
function L([string]$s){ $log.Add($s); Write-Output $s }
function Save([string]$name,$obj){ if($null -ne $obj){ [IO.File]::WriteAllText((Join-Path $OutDir $name), (($obj | ConvertTo-Json -Depth 40)), $enc) } }
function AzJson([string[]]$a){
  $raw = & az @a -o json --only-show-errors 2>$null | Out-String
  if($raw.Trim().Length -gt 0){ try { return ($raw | ConvertFrom-Json) } catch { return $null } }
  return $null
}

L "=== AZ ACCOUNT ==="
$acct = AzJson @('account','show')
if($acct){ L ("subscription: " + $acct.name + "  id=" + $acct.id + "  state=" + $acct.state) } else { L "az account show: no session. Aborting."; exit 1 }

L "=== VM LOCATE (-d) ==="
$vms = AzJson @('vm','list','-d')
$v = $vms | Where-Object { $_.name -eq $Vm } | Select-Object -First 1
if(-not $v){ L ("VM " + $Vm + " not found in subscription. Aborting."); exit 1 }
$rg = $v.resourceGroup
Save "az_vm_list_detail.json" $v
L ("vm=" + $v.name + "  rg=" + $rg + "  size=" + $v.hardwareProfile.vmSize + "  location=" + $v.location + "  power=" + $v.powerState)
L ("publicIps=" + ($v.publicIps) + "  privateIps=" + ($v.privateIps) + "  fqdns=" + ($v.fqdns))

L "=== VM SHOW (image / osdisk / patch settings) ==="
$vd = AzJson @('vm','show','-g',$rg,'-n',$Vm)
Save "az_vm_show.json" $vd
if($vd){
  $img = $vd.storageProfile.imageReference
  L ("image: publisher=" + $img.publisher + " offer=" + $img.offer + " sku=" + $img.sku + " version=" + $img.version + " exactVersion=" + $img.exactVersion)
  L ("osDisk: name=" + $vd.storageProfile.osDisk.name + " createOption=" + $vd.storageProfile.osDisk.createOption + " sizeGb=" + $vd.storageProfile.osDisk.diskSizeGb + " osType=" + $vd.storageProfile.osDisk.osType)
  L ("osDisk.managedDisk.id=" + $vd.storageProfile.osDisk.managedDisk.id + " storageAccountType=" + $vd.storageProfile.osDisk.managedDisk.storageAccountType)
  $lx = $vd.osProfile.linuxConfiguration
  L ("linux: disablePasswordAuth=" + $lx.disablePasswordAuthentication + " provisionVMAgent=" + $lx.provisionVMAgent)
  L ("patchSettings: patchMode=" + $lx.patchSettings.patchMode + " assessmentMode=" + $lx.patchSettings.assessmentMode)
  L ("securityProfile: securityType=" + $vd.securityProfile.securityType + " encryptionAtHost=" + $vd.securityProfile.encryptionAtHost)
}

L "=== NICS / NSG / SUBNET / PUBLIC-IP ==="
$nsgSeen = @{}
foreach($nicRef in $vd.networkProfile.networkInterfaces){
  $nic = AzJson @('network','nic','show','--ids',$nicRef.id)
  if(-not $nic){ L ("nic fetch failed: " + $nicRef.id); continue }
  Save ("az_nic_" + $nic.name + ".json") $nic
  $ip0 = $nic.ipConfigurations[0]
  L ("nic=" + $nic.name + " privateIp=" + $ip0.privateIpAddress + " ipForwarding=" + $nic.enableIPForwarding)
  if($ip0.publicIpAddress.id){
    $pip = AzJson @('network','public-ip','show','--ids',$ip0.publicIpAddress.id)
    if($pip){ L ("publicIP=" + $pip.ipAddress + " fqdn=" + $pip.dnsSettings.fqdn + " sku=" + $pip.sku.name + " alloc=" + $pip.publicIPAllocationMethod) }
  } else { L "publicIP: (none directly on NIC ipconfig)" }
  $nicNsgId = $nic.networkSecurityGroup.id
  L ("nic-level NSG: " + $(if($nicNsgId){$nicNsgId}else{'(none)'}))
  $subnetId = $ip0.subnet.id
  $subnet = AzJson @('network','vnet','subnet','show','--ids',$subnetId)
  $subNsgId = $subnet.networkSecurityGroup.id
  L ("subnet=" + $(if($subnet){$subnet.name}else{'?'}) + " subnet-level NSG: " + $(if($subNsgId){$subNsgId}else{'(none)'}))
  foreach($nsgId in @($nicNsgId,$subNsgId)){
    if(-not $nsgId){ continue }
    if($nsgSeen.ContainsKey($nsgId)){ continue }
    $nsgSeen[$nsgId] = $true
    $nsg = AzJson @('network','nsg','show','--ids',$nsgId)
    if(-not $nsg){ L ("nsg fetch failed: " + $nsgId); continue }
    Save ("az_nsg_" + $nsg.name + ".json") $nsg
    L ("--- NSG " + $nsg.name + " : INBOUND custom rules (by priority) ---")
    $inb = @($nsg.securityRules | Where-Object { $_.direction -eq 'Inbound' } | Sort-Object priority)
    if($inb.Count -eq 0){ L "  (no custom inbound rules; only Azure defaults)" }
    foreach($r in $inb){
      $dp = if($r.destinationPortRange){ $r.destinationPortRange } else { ($r.destinationPortRanges -join ',') }
      $sp = if($r.sourceAddressPrefix){ $r.sourceAddressPrefix } else { ($r.sourceAddressPrefixes -join ',') }
      L ("  [" + $r.priority + "] " + $r.name + "  " + $r.access + " " + $r.protocol + "  src=" + $sp + "  dport=" + $dp)
    }
    L ("--- NSG " + $nsg.name + " : OUTBOUND custom rules (by priority) ---")
    $outb = @($nsg.securityRules | Where-Object { $_.direction -eq 'Outbound' } | Sort-Object priority)
    if($outb.Count -eq 0){ L "  (no custom outbound rules; only Azure defaults)" }
    foreach($r in $outb){
      $dp = if($r.destinationPortRange){ $r.destinationPortRange } else { ($r.destinationPortRanges -join ',') }
      $sp = if($r.sourceAddressPrefix){ $r.sourceAddressPrefix } else { ($r.sourceAddressPrefixes -join ',') }
      L ("  [" + $r.priority + "] " + $r.name + "  " + $r.access + " " + $r.protocol + "  src=" + $sp + "  dport=" + $dp)
    }
  }
}

L "=== INSTANCE VIEW (agent + any existing patch assessment; read-only) ==="
$iv = AzJson @('vm','get-instance-view','-g',$rg,'-n',$Vm)
Save "az_instance_view.json" $iv
if($iv -and $iv.instanceView){
  $iview = $iv.instanceView
  $agentStatus = if($iview.vmAgent.statuses){ $iview.vmAgent.statuses[0].displayStatus } else { '?' }
  L ("vmAgent version=" + $iview.vmAgent.vmAgentVersion + "  status=" + $agentStatus)
  if($iview.patchStatus -and $iview.patchStatus.availablePatchSummary){
    $aps = $iview.patchStatus.availablePatchSummary
    L ("patch assessment: status=" + $aps.status + " critical=" + $aps.criticalAndSecurityPatchCount + " other=" + $aps.otherPatchCount + " lastAssessed=" + $aps.lastModifiedTime)
  } else {
    L "patchStatus: none cached in instance view. (To populate: 'az vm assess-patches -g RG -n VM' = read-only ASSESSMENT, installs nothing -- Phase-2 Jack-approved step.)"
  }
}

[IO.File]::WriteAllText((Join-Path $OutDir '03_azure_summary.txt'), (($log -join "`n") + "`n"), $enc)
Write-Output ("`nWrote: " + (Join-Path $OutDir '03_azure_summary.txt'))
