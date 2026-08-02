<#
KCV2 standing-accrual job wrapper.

Runs one resumable lab loader under the 25 GB memory cap (house discipline,
scripts\run_capped.ps1 -> procgov), measures how many rows landed, and appends
ONE append-only heartbeat line so a dead timer is VISIBLE at a glance.

Jobs (see SCHEDULING_RUNBOOK.md):
  -Job ladder    Kalshi hourly-ladder snapshot  (daily)   -> lab_kalshi_ladder_snap
  -Job fineflow  Coinalyze fine-flow / Rider A  (every 12h)-> lab_coinalyze (1/5/15min)

Both loaders are idempotent (ladder skips already-snapped events; fine-flow is
INSERT OR REPLACE, no DELETE), so re-running after any failure is always safe.

Failure behaviour: loud. On any non-zero loader exit or wrapper exception a
heartbeat line with status=ERROR is written and the wrapper exits non-zero, so
Task Scheduler's Last Run Result also flags it.

SECRETS: none are printed. The loaders fetch creds from Key Vault in-memory and
redact; this wrapper only relays their (already-redacted) stdout. Do not add any
line that echoes an environment variable value here.

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File run_accrual.ps1 -Job ladder
  powershell -NoProfile -ExecutionPolicy Bypass -File run_accrual.ps1 -Job fineflow
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('ladder', 'fineflow')]
    [string]$Job,

    # Concrete CPython 3.14 (NOT the WindowsApps store alias, which is unreliable
    # under Task Scheduler). Override only if the interpreter moves.
    [string]$Python = 'C:\Users\AA Incorporado\AppData\Local\Python\pythoncore-3.14-64\python.exe'
)

$ErrorActionPreference = 'Stop'

# --- resolve the co-located subsystem paths (robust to where we are invoked) ---
$ScheduleDir = $PSScriptRoot
$Kcv2Dir     = Split-Path -Parent $ScheduleDir            # research\kalshi_crypto_v2
$ResearchDir = Split-Path -Parent $Kcv2Dir                # research
$Worktree    = Split-Path -Parent $ResearchDir            # worktree root
$Capped      = Join-Path $Worktree 'scripts\run_capped.ps1'
$LabDb       = Join-Path $Kcv2Dir  'lab\kcv2_lab.db'
$LoadersDir  = Join-Path $Kcv2Dir  'loaders'
$LogDir      = Join-Path $ScheduleDir 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# --- per-job config ------------------------------------------------------------
switch ($Job) {
    'ladder' {
        $script    = Join-Path $LoadersDir 'kalshi_ladder_snap.py'
        $scriptArg = @('--every-hours', '24')
        $countSql  = "SELECT COUNT(*) FROM lab_kalshi_ladder_snap"
    }
    'fineflow' {
        $script    = Join-Path $LoadersDir 'coinalyze.py'
        $scriptArg = @('--fine-only')
        $countSql  = "SELECT COUNT(*) FROM lab_coinalyze WHERE interval IN ('1min','5min','15min')"
    }
}

$Heartbeat = Join-Path $LogDir ("{0}.heartbeat.log" -f $Job)
$LastLog   = Join-Path $LogDir ("{0}.last.log"      -f $Job)
$env:KEY_VAULT_URI = 'https://kv-tc-vtwbowt3wtkpy.vault.azure.net/'

function Now-Utc { [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') }

# Read-only row count of the accrual table. Returns [int] or $null on failure.
function Get-RowCount {
    $q = "import sqlite3,sys`n" +
         "c=sqlite3.connect('file:'+r'''$LabDb'''+'?mode=ro',uri=True)`n" +
         "print(c.execute(r'''$countSql''').fetchone()[0]); c.close()"
    try {
        $out = & $Python -c $q 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -match '^\d+$') { return [int]$out }
    } catch { }
    return $null
}

function Write-Heartbeat([string]$status, [int]$exitCode, $before, $after, [int]$dur, [string]$note) {
    $b = if ($null -ne $before) { $before } else { 'NA' }
    $a = if ($null -ne $after)  { $after }  else { 'NA' }
    $d = if (($null -ne $before) -and ($null -ne $after)) { $after - $before } else { 'NA' }
    $noteClean = ($note -replace '"', "'" -replace '\s+', ' ').Trim()
    if ($noteClean.Length -gt 220) { $noteClean = $noteClean.Substring(0, 220) }
    $line = ('{0}  job={1}  status={2}  exit={3}  rows_before={4} rows_after={5} rows_new={6}  dur={7}s  note="{8}"' `
             -f (Now-Utc), $Job, $status, $exitCode, $b, $a, $d, $dur, $noteClean)
    Add-Content -Path $Heartbeat -Value $line -Encoding utf8
    Write-Output $line
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$before = Get-RowCount
try {
    if (-not (Test-Path $script))  { throw "loader not found: $script" }
    if (-not (Test-Path $Capped))  { throw "run_capped.ps1 not found: $Capped" }
    if (-not (Test-Path $Python))  { throw "python not found: $Python" }

    # Run the loader under the memory cap; capture all output to the per-run log.
    $raw = & $Capped $Python $script @scriptArg *>&1
    $code = $LASTEXITCODE
    $raw | Out-File -FilePath $LastLog -Encoding utf8

    $sw.Stop()
    $after = Get-RowCount

    # note = last meaningful loader line (drop the procgov banner + blanks)
    $lines = @($raw | ForEach-Object { "$_" } |
        Where-Object { $_ -notmatch '^(Process Governor|Copyright|Maximum job|All configured|Press Ctrl-C)' -and $_.Trim() -ne '' })
    $note = if ($lines.Count -gt 0) { $lines[-1] } else { '' }

    if ($code -eq 0) {
        Write-Heartbeat 'OK' $code $before $after ([int]$sw.Elapsed.TotalSeconds) $note
        exit 0
    } else {
        Write-Heartbeat 'ERROR' $code $before $after ([int]$sw.Elapsed.TotalSeconds) "loader exit $code; last: $note"
        exit $code
    }
} catch {
    $sw.Stop()
    $after = Get-RowCount
    Write-Heartbeat 'ERROR' 99 $before $after ([int]$sw.Elapsed.TotalSeconds) "wrapper exception: $($_.Exception.Message)"
    exit 99
}
