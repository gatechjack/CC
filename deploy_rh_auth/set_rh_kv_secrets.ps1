# set_rh_kv_secrets.ps1 -- put RH creds into Key Vault (ITEM 1). Operator paste (ONE line):
#   powershell -ep bypass -f .\deploy_rh_auth\set_rh_kv_secrets.ps1
# Prompts for the 3 RH creds (masked), writes them to kv-tc-vtwbowt3wtkpy as
# ROBINHOOD-USERNAME / ROBINHOOD-PASSWORD / ROBINHOOD-MFA-SECRET (the hyphen form
# secrets.py maps ROBINHOOD_* env names to). Values never touch disk or this file.
# MFA secret = the TOTP base32 seed (what pickle_refresh.py feeds pyotp), NOT a 6-digit code.
$ErrorActionPreference = 'Stop'
$vault = 'kv-tc-vtwbowt3wtkpy'

# 0. Confirm az is logged in and can see the vault.
try { az account show 1>$null 2>$null } catch { Write-Host 'ERROR: run  az login  first.'; exit 1 }
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: not logged in to az. Run  az login  first.'; exit 1 }

function Set-Secret([string]$name, [string]$prompt) {
  $sec = Read-Host -AsSecureString $prompt
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  $val  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  if ([string]::IsNullOrWhiteSpace($val)) { Write-Host "SKIP $name (empty)"; return }
  az keyvault secret set --vault-name $vault --name $name --value $val 1>$null 2>$null
  if ($LASTEXITCODE -eq 0) { Write-Host "OK  set $name" } else { Write-Host "FAIL $name (exit $LASTEXITCODE)" }
  $val = $null
}

Write-Host "Vault: $vault  -- enter RH creds (input is masked)"
Set-Secret 'ROBINHOOD-USERNAME'   'Robinhood username (email)'
Set-Secret 'ROBINHOOD-PASSWORD'   'Robinhood password'
Set-Secret 'ROBINHOOD-MFA-SECRET' 'Robinhood MFA/TOTP base32 seed'
Write-Host 'Done. Verify:  az keyvault secret list --vault-name kv-tc-vtwbowt3wtkpy --query "[?starts_with(name,''ROBINHOOD'')].name" -o tsv'
