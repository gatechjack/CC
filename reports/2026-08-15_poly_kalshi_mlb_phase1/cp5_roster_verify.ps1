# CP5 roster VERIFY (READ-ONLY) - dumps prod selected_whales + pinned_whales.
# Operator run:  powershell -ep bypass -f .\cp5_roster_verify.ps1
$ErrorActionPreference = 'Stop'
$py = @'
import os
try:
    from dotenv import load_dotenv
    load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence.db import load_agent_state
print("DB", DB)
for key in ("selected_whales", "pinned_whales"):
    rec = load_agent_state("polymarket_copy_trader", key, db_url=DB)
    v = rec[0] if rec else []
    names = [x.get("user_name") if isinstance(x, dict) else x for x in (v or [])]
    print(key, "count", len(v or []), names)
'@
$py = $py -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$sh = "cd /home/azureuser/trading_corp && printf %s '$b64' | base64 -d | venv/bin/python3 -"
Write-Host "== READ-ONLY: prod selected_whales / pinned_whales =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
