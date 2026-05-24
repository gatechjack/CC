echo "=== sample kalshi_sports_arb_unmapped rows (what teams did Kalshi see?) ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT ts, json_extract(payload_json,'\$.ticker') AS ticker, json_extract(payload_json,'\$.team_a_name') AS ka, json_extract(payload_json,'\$.team_b_name') AS kb FROM audit_event WHERE kind='kalshi_sports_arb_unmapped' ORDER BY ts DESC LIMIT 10;"
echo ""
echo "=== what teams is the-odds-api actually returning for baseball_mlb right now ==="
cd /home/azureuser/trading_corp && KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ /home/azureuser/trading_corp/venv/bin/python3 - <<'PY'
import sys, urllib.request, urllib.parse, json
sys.path.insert(0, '.')
from trading_corp.utils.secrets import load_secrets
secrets = load_secrets()
key = secrets.odds_api_key
u = 'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?' + urllib.parse.urlencode({
    'apiKey': key, 'regions':'us', 'markets':'h2h',
    'bookmakers':'pinnacle,draftkings,fanduel,betmgm',
    'oddsFormat':'american', 'dateFormat':'iso',
})
r = urllib.request.urlopen(u, timeout=15)
data = json.loads(r.read())
print(f'odds-api returned {len(data)} games')
for g in data[:10]:
    print(f'  home="{g.get("home_team")}" away="{g.get("away_team")}" start={g.get("commence_time")}')
PY
