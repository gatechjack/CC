$ErrorActionPreference = "Stop"
# READ-ONLY az-root read of Authelia's filesystem-notifier file. Shows ONLY the newest
# enrolment block (user + link + timestamp + expiry). NOTHING written; authelia NOT restarted;
# nothing else on the box touched. Re-runnable -- run it every time someone enrols a device.
$guard = Join-Path $PSScriptRoot "pm_authelia_enrol_link_ro.sh"
if (-not (Test-Path $guard)) { throw "read script not found: $guard" }
$bytes = [IO.File]::ReadAllBytes($guard)
if (($bytes | Where-Object { $_ -gt 127 }).Count -gt 0) { throw "sh has non-ASCII bytes -- refusing" }
Write-Host "invoking az run-command (READ-ONLY: newest Authelia enrolment link; nothing written)..."
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "@$guard" --query "value[0].message" -o tsv
Write-Host "---- pm_authelia_enrol_link_ro exit ($LASTEXITCODE) ----"
