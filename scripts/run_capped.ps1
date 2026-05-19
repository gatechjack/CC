# Wrap any command with a 25 GB virtual-commit cap via procgov (Windows Job Object).
# H7 Mitigation 2. See docs/runbooks/session_workload_defaults.md.
# Usage: .\scripts\run_capped.ps1 python scripts/backtest_kalshi_structure_arb.py
if ($args.Count -eq 0) { Write-Error "Usage: run_capped.ps1 <command> [args...]"; exit 1 }
& procgov -r --maxjobmem 25G --terminate-job-on-exit -- @args
exit $LASTEXITCODE
