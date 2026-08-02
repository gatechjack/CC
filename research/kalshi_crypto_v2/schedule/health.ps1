<#
KCV2 accrual health check — one glance tells you if either timer is dead.

For each job it prints the newest heartbeat line, its age, and a verdict:
  OK     last run succeeded and is within the expected cadence window
  STALE  last heartbeat is older than 1.5x the cadence  -> the timer may be dead
  ERROR  last run recorded status=ERROR                 -> re-run + investigate
  NONE   no heartbeat yet (never run)

It also shows the registered Scheduled Task state / last result / next run.

Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File health.ps1
#>
$ScheduleDir = $PSScriptRoot
$LogDir = Join-Path $ScheduleDir 'logs'

# job -> (cadence hours, scheduled-task path + name)
$TaskPath = '\TradingCorp\'
$jobs = @(
    @{ Name = 'ladder';   CadenceH = 24; Task = 'kcv2-ladder-snap' },
    @{ Name = 'fineflow'; CadenceH = 12; Task = 'kcv2-fine-flow'  }
)

$nowUtc = [DateTime]::UtcNow
Write-Output ("KCV2 accrual health  @ {0}" -f $nowUtc.ToString('yyyy-MM-ddTHH:mm:ssZ'))
Write-Output ('-' * 78)

foreach ($j in $jobs) {
    $hb = Join-Path $LogDir ("{0}.heartbeat.log" -f $j.Name)
    $verdict = 'NONE'; $ageStr = '-'; $last = '(no heartbeat yet)'
    if (Test-Path $hb) {
        $last = (Get-Content $hb -Tail 1)
        if ($last -match '^(\S+)\s') {
            try {
                $t = [DateTime]::Parse($Matches[1], $null, [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal)
                $age = $nowUtc - $t
                $ageStr = ('{0:0.0}h' -f $age.TotalHours)
                if ($last -match 'status=ERROR') { $verdict = 'ERROR' }
                elseif ($age.TotalHours -gt ($j.CadenceH * 1.5)) { $verdict = 'STALE' }
                else { $verdict = 'OK' }
            } catch { $verdict = '??' }
        }
    }
    Write-Output ("[{0,-5}] {1,-8}  cadence={2}h  age={3}" -f $verdict, $j.Name, $j.CadenceH, $ageStr)
    Write-Output ("        last: {0}" -f $last)

    $ti = $null
    try { $ti = Get-ScheduledTaskInfo -TaskName $j.Task -TaskPath $TaskPath -ErrorAction Stop } catch { }
    if ($ti) {
        $st = (Get-ScheduledTask -TaskName $j.Task -TaskPath $TaskPath -ErrorAction SilentlyContinue).State
        Write-Output ("        task: state={0} lastResult=0x{1:X} lastRun={2} nextRun={3}" -f `
            $st, $ti.LastTaskResult, $ti.LastRunTime, $ti.NextRunTime)
    } else {
        Write-Output ("        task: NOT REGISTERED ({0}{1})" -f $TaskPath, $j.Task)
    }
    Write-Output ''
}
Write-Output "Cadence windows: ladder STALE > 36h, fineflow STALE > 18h. lastResult 0x0 = success."
