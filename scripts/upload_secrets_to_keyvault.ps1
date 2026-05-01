<#
.SYNOPSIS
    Upload secrets from local .env to Azure Key Vault.

.DESCRIPTION
    One-time bootstrap (or incremental update) of secrets in the production
    Azure Key Vault. Reads each KEY=VALUE line from .env and creates/updates
    a corresponding secret in the vault. KV doesn't allow underscores in
    secret names, so KEY_NAME becomes KEY-NAME.

    Re-running is safe — `az keyvault secret set` is idempotent.

.PARAMETER VaultName
    Azure Key Vault name. Defaults to the one created by infra/main.bicep.
    Override if you have multiple vaults.

.PARAMETER EnvFile
    Path to .env. Defaults to repo-root .env.

.PARAMETER DryRun
    Just print what would be uploaded — don't touch the vault.

.EXAMPLE
    # First-time upload
    .\scripts\upload_secrets_to_keyvault.ps1

    # Just preview
    .\scripts\upload_secrets_to_keyvault.ps1 -DryRun

    # Different vault
    .\scripts\upload_secrets_to_keyvault.ps1 -VaultName kv-tc-staging-xxxxx
#>

param(
    [string]$VaultName = "kv-tc-vtwbowt3wtkpy",
    [string]$EnvFile = "",
    [switch]$DryRun
)

# Default .env path — repo root, parent of scripts/
if ([string]::IsNullOrEmpty($EnvFile)) {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $EnvFile = Join-Path $repoRoot ".env"
}

if (-not (Test-Path $EnvFile)) {
    Write-Error ".env not found at $EnvFile"
    exit 1
}

Write-Host "Vault: $VaultName"
Write-Host "Source: $EnvFile"
Write-Host "Mode:  $(if ($DryRun) { 'DRY RUN' } else { 'LIVE' })"
Write-Host ""

# Sanity check we can talk to the vault
$vaultExists = az keyvault show --name $VaultName --query name -o tsv 2>$null
if (-not $vaultExists) {
    Write-Error "Cannot reach Key Vault '$VaultName'. Check name + your az login."
    exit 1
}

# Count for summary
$uploaded = 0
$skipped = 0
$failed = 0

# Stream .env line by line
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()

    # Skip comments + blank lines
    if ($line -eq "" -or $line.StartsWith("#")) {
        return
    }

    # Match KEY=VALUE
    if ($line -notmatch "^([A-Z][A-Z0-9_]*)=(.*)$") {
        Write-Warning "Skipping unrecognized line: $line"
        return
    }

    $key = $matches[1]
    $value = $matches[2]

    # Strip surrounding quotes if present (some .env conventions use them)
    if ($value.Length -ge 2) {
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }

    # Skip empty values (e.g. RH MFA secret often blank)
    if ($value -eq "") {
        Write-Host "  - $key (empty, skipped)" -ForegroundColor DarkGray
        $script:skipped++
        return
    }

    # KV secret naming: replace underscores with hyphens
    $kvName = $key -replace "_", "-"

    # For values that contain literal \n (like Coinbase EC keys stored
    # single-line in .env), expand them to actual newlines so the secret
    # in KV has the proper PEM format. Code on the VM will read it back
    # already-multi-line.
    if ($value -match "\\n") {
        $value = $value -replace "\\n", "`n"
    }

    if ($DryRun) {
        $preview = if ($value.Length -gt 30) { $value.Substring(0, 27) + "..." } else { $value }
        Write-Host "  [dry] $key -> $kvName = $preview"
        $script:uploaded++
        return
    }

    try {
        # `az keyvault secret set` accepts multi-line values via --value;
        # PowerShell passes them through.
        $null = az keyvault secret set `
            --vault-name $VaultName `
            --name $kvName `
            --value "$value" `
            --output none 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK]   $key -> $kvName" -ForegroundColor Green
            $script:uploaded++
        } else {
            Write-Host "  [FAIL] $key -> $kvName  (exit $LASTEXITCODE)" -ForegroundColor Red
            $script:failed++
        }
    }
    catch {
        Write-Host "  [FAIL] $key -> $kvName : $_" -ForegroundColor Red
        $script:failed++
    }
}

Write-Host ""
Write-Host "Done. Uploaded: $uploaded  Skipped (empty): $skipped  Failed: $failed"
if ($failed -gt 0) {
    exit 1
}
