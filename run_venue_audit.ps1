# Read-only Bitunix venue reconciliation runner (OPERATOR-executed).
# Copies the vetted GET-only audit script to prod, auto-resolves KEY_VAULT_URI
# from the engine, then runs the reconciliation. No writes to the venue.
# If KEY_VAULT_URI cannot be auto-found it prints KEY_VAULT_URI_NOT_FOUND and
# does nothing else -- then set $KV_OVERRIDE below to the vault URL and re-run.
$ErrorActionPreference = "Stop"
$h = "azureuser@trading.jacksumner.com"
$py = Join-Path $PSScriptRoot "venue_audit_ro.py"
$KV_OVERRIDE = ""   # optional: paste https://<name>.vault.azure.net/ here if auto-detect fails
Write-Host "[1/2] copying read-only audit script to prod..."
scp $py "${h}:venue_audit_ro.py"
Write-Host "[2/2] running GET-only venue reconciliation (may take ~1-2 min)..."
$cmd = @'
cd ~/trading_corp || exit 2; KV="__OVERRIDE__"; if [ -z "$KV" ]; then for S in trading-corp trading_corp ceo; do V=$(systemctl show "$S" -p Environment 2>/dev/null | tr ' ' '\n' | sed -n 's/^KEY_VAULT_URI=//p' | head -1); [ -n "$V" ] && KV="$V" && break; done; fi; if [ -z "$KV" ]; then P=$(pgrep -f 'venv/bin/python' | head -1); [ -n "$P" ] && KV=$(tr '\0' '\n' < /proc/$P/environ 2>/dev/null | sed -n 's/^KEY_VAULT_URI=//p' | head -1); fi; if [ -z "$KV" ]; then echo KEY_VAULT_URI_NOT_FOUND; else echo "KV_OK len=${#KV}"; KEY_VAULT_URI="$KV" venv/bin/python ~/venue_audit_ro.py; fi
'@
$cmd = $cmd -replace '__OVERRIDE__', $KV_OVERRIDE
$cmd | ssh $h "tr -d '\r' | bash"
