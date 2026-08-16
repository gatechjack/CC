# CP5 roster WRITE - sets prod selected_whales + pinned_whales = the 4 MLB whales, then verifies.
# Writing pinned too so a pins_only reseed cannot revert the roster.
# Operator run:  powershell -ep bypass -f .\cp5_roster_write.ps1
$ErrorActionPreference = 'Stop'
$py = @'
import os
try:
    from dotenv import load_dotenv
    load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence.db import set_agent_state, load_agent_state
roster = [
 {"wallet": "0x16bb9951a36fce71e2ef57890b786145e0ba8492", "user_name": "SDTrading",                  "category": "mlb", "recency_rank": 1, "recency_matchable_ts": 1786847094, "recency_as_of": "2026-08-16T06:28:52+00:00"},
 {"wallet": "0x9c3ce009c9b039956665cecc4cd14de862b5e8c9", "user_name": "0x0x23kjookhaiuohduoayh8c9", "category": "mlb", "recency_rank": 2, "recency_matchable_ts": 1786835328, "recency_as_of": "2026-08-16T06:28:52+00:00"},
 {"wallet": "0x2dc13c6bda81b202281e796953a7323de675b33c", "user_name": "xifutloong3",                "category": "mlb", "recency_rank": 3, "recency_matchable_ts": 1786834663, "recency_as_of": "2026-08-16T06:28:52+00:00"},
 {"wallet": "0x684baa57c338c2549aec0aa3f034f695d72a8409", "user_name": "monkeymashingkeyboard",      "category": "mlb", "recency_rank": 4, "recency_matchable_ts": 1786833261, "recency_as_of": "2026-08-16T06:28:52+00:00"},
]
pinned = [{"wallet": r["wallet"], "user_name": r["user_name"], "category": "mlb"} for r in roster]
set_agent_state("polymarket_copy_trader", "selected_whales", roster, db_url=DB)
set_agent_state("polymarket_copy_trader", "pinned_whales", pinned, db_url=DB)
sv = (load_agent_state("polymarket_copy_trader", "selected_whales", db_url=DB) or [None])[0]
pv = (load_agent_state("polymarket_copy_trader", "pinned_whales", db_url=DB) or [None])[0]
LEG = set(["Hakei", "CVCM", "ox1star84", "DegenKingBetter", "rollobravado", "Kosherlocks",
           "GreatestTrader", "olddirtyfighter", "llllllII", "digitalnomad85"])
print("DB", DB)
print("selected count", len(sv), [x["user_name"] for x in sv])
print("pinned count", len(pv), [x["user_name"] for x in pv])
print("legacy remaining in selected", [x["user_name"] for x in sv if x["user_name"] in LEG])
print("VERIFY 4-only-no-legacy", (len(sv) == 4 and not [x for x in sv if x["user_name"] in LEG]))
'@
$py = $py -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
$sh = "cd /home/azureuser/trading_corp && printf %s '$b64' | base64 -d | venv/bin/python3 -"
Write-Host "== WRITE prod selected_whales + pinned_whales = the 4, then verify =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
