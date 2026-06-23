# Wrap any command with a 25 GB virtual-commit cap via procgov (Windows Job Object).
# H7 Mitigation 2. See docs/runbooks/session_workload_defaults.md.
# Usage: .\scripts\run_capped.ps1 python scripts/backtest_kalshi_structure_arb.py
if ($args.Count -eq 0) { Write-Error "Usage: run_capped.ps1 <command> [args...]"; exit 1 }
$cmd = $args[0]
$rest = @(); if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
# Resolve a bare 'python' to the REAL interpreter (2026-06-21). The default
# 'python' on PATH is the 0-byte WindowsApps App Execution Alias, and procgov's
# CreateProcess on that reparse stub fails with ACCESS_DENIED. Pick the first
# non-WindowsApps python.exe; fall back to the py launcher's sys.executable.
if ($cmd -eq 'python') {
    $cmd = (Get-Command python -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' -and $_.Source -like '*.exe' } |
        Select-Object -First 1).Source
    if (-not $cmd) { $cmd = (& py -c "import sys; print(sys.executable)" 2>$null) }
    # Hardening: fail LOUD if resolution produced nothing usable, so a future
    # Python move surfaces visibly instead of silently proceeding.
    if (-not $cmd -or -not (Test-Path -LiteralPath $cmd -PathType Leaf)) {
        Write-Error "run_capped.ps1: could not resolve a usable python interpreter (resolved='$cmd'). Aborting - fix the Python path / PATH."
        exit 3
    }
}
& procgov -r --maxjobmem 25G --terminate-job-on-exit -- $cmd @rest
exit $LASTEXITCODE
