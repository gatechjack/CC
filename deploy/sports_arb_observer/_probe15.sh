echo "=== current Kalshi MLB date distribution in observer's discovery ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT SUBSTR(json_extract(payload_json,'\$.ticker'), 11, 7) AS date_blob, COUNT(*) FROM audit_event WHERE kind='kalshi_sports_arb_unmapped' AND ts > '2026-05-24 13:00:00' GROUP BY date_blob ORDER BY date_blob;"
echo ""
echo "=== odds-api MLB games available NOW (date breakdown) ==="
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
dates = {}
for g in data:
    d = (g.get('commence_time') or '')[:10]
    dates[d] = dates.get(d, 0) + 1
print(f'odds-api: {len(data)} total games')
for d in sorted(dates):
    print(f'  {d}: {dates[d]}')
print(f'quota: remaining={r.headers.get("x-requests-remaining")} used={r.headers.get("x-requests-used")}')
PY
echo ""
echo "=== sample of odds-api home teams to verify naming ==="
cd /home/azureuser/trading_corp && KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ /home/azureuser/trading_corp/venv/bin/python3 - <<'PY'
import sys, urllib.request, urllib.parse, json
sys.path.insert(0, '.')
from trading_corp.utils.secrets import load_secrets
secrets = load_secrets()
key = secrets.odds_api_key
# Don't re-call odds-api; just use cached game list from prior call by re-fetching since quota allows
u = 'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?' + urllib.parse.urlencode({
    'apiKey': key, 'regions':'us', 'markets':'h2h',
    'bookmakers':'pinnacle,draftkings,fanduel,betmgm',
    'oddsFormat':'american', 'dateFormat':'iso',
})
r = urllib.request.urlopen(u, timeout=15)
data = json.loads(r.read())
for g in data[:5]:
    print(f'  {g.get("commence_time")[:10]} {g.get("home_team")} vs {g.get("away_team")}')
PY
