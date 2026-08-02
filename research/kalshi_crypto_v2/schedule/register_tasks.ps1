<#
Register (or remove) the two KCV2 standing-accrual Scheduled Tasks on THIS machine.

  \TradingCorp\kcv2-ladder-snap   daily  08:00 local        -> run_accrual.ps1 -Job ladder
  \TradingCorp\kcv2-fine-flow     every 12h from 07:00 local -> run_accrual.ps1 -Job fineflow

Design notes:
- LogonType = Interactive (runs only when this user is logged on, inside their
  profile). This is DELIBERATE and load-bearing: the loaders authenticate to Key
  Vault via DefaultAzureCredential, which on this machine has no managed identity
  and falls back to the Azure CLI token cache (~/.azure) of the logged-on user.
  A "run whether logged on or not" task in a different security context would not
  see that cache and would fail the cred fetch. See SCHEDULING_RUNBOOK.md.
- -Force makes registration idempotent (re-run to update).
- StartWhenAvailable: a missed trigger (machine asleep/off) runs on next wake.
- ExecutionTimeLimit 1h + MultipleInstances IgnoreNew: no runaways, no overlap.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File register_tasks.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File register_tasks.ps1 -Remove
#>
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$ScheduleDir = $PSScriptRoot
$Wrapper = Join-Path $ScheduleDir 'run_accrual.ps1'
$TaskPath = '\TradingCorp\'
$Ladder = 'kcv2-ladder-snap'
$Fine   = 'kcv2-fine-flow'

if ($Remove) {
    foreach ($n in @($Ladder, $Fine)) {
        try { Unregister-ScheduledTask -TaskName $n -TaskPath $TaskPath -Confirm:$false -ErrorAction Stop; Write-Output "removed $TaskPath$n" }
        catch { Write-Output "not present: $TaskPath$n" }
    }
    return
}

if (-not (Test-Path $Wrapper)) { throw "wrapper missing: $Wrapper" }

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 10)

function Register-AccrualTask([string]$name, [string]$job, $trigger) {
    $arg = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Job {1}' -f $Wrapper, $job)
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $ScheduleDir
    Register-ScheduledTask -TaskName $name -TaskPath $TaskPath -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description "KCV2 standing accrual: $job (see research/kalshi_crypto_v2/schedule/SCHEDULING_RUNBOOK.md)" -Force | Out-Null
    Write-Output "registered $TaskPath$name  ($job)"
}

# Ladder: once daily at 08:00 local.
$ladderTrigger = New-ScheduledTaskTrigger -Daily -At '08:00'

# Fine-flow: every 12h. -Once + -RepetitionInterval (no duration) repeats indefinitely.
$fineStart = (Get-Date).Date.AddHours(7)     # anchor 07:00 local -> fires 07:00 / 19:00
$fineTrigger = New-ScheduledTaskTrigger -Once -At $fineStart -RepetitionInterval (New-TimeSpan -Hours 12)

Register-AccrualTask $Ladder 'ladder'   $ladderTrigger
Register-AccrualTask $Fine   'fineflow' $fineTrigger

Write-Output ''
Write-Output 'Registered tasks:'
Get-ScheduledTask -TaskPath $TaskPath | Select-Object TaskName, State | Format-Table -AutoSize
