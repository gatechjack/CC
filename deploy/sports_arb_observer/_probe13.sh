echo "=== date distribution of the 50 unmapped Kalshi MLB tickers ==="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db "SELECT SUBSTR(json_extract(payload_json,'\$.ticker'), 11, 7) AS date_blob, COUNT(*) FROM audit_event WHERE kind='kalshi_sports_arb_unmapped' AND ts > '2026-05-24 04:00:00' GROUP BY date_blob ORDER BY date_blob;"
echo ""
echo "=== odds-api default window for baseball_mlb (no date filter) ==="
cd /home/azureuser/trading_corp && KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ /home/azureuser/trading_corp/venv/bin/python3 - <<'PY'
import sys, urllib.request, urllib.parse, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '.')
from trading_corp.utils.secrets import load_secrets
secrets = load_secrets()
key = secrets.odds_api_key
# Try with commenceTimeTo widened to +72h
end = (datetime.now(timezone.utc) + timedelta(hours=72)).strftime('%Y-%m-%dT%H:%M:%SZ')
u = 'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?' + urllib.parse.urlencode({
    'apiKey': key, 'regions':'us', 'markets':'h2h',
    'bookmakers':'pinnacle,draftkings,fanduel,betmgm',
    'oddsFormat':'american', 'dateFormat':'iso',
    'commenceTimeTo': end,
})
r = urllib.request.urlopen(u, timeout=15)
data = json.loads(r.read())
print(f'WITH commenceTimeTo=+72h: {len(data)} games')
dates = {}
for g in data:
    d = (g.get('commence_time') or '')[:10]
    dates[d] = dates.get(d, 0) + 1
for d in sorted(dates):
    print(f'  {d}: {dates[d]} games')
print(f'quota: remaining={r.headers.get("x-requests-remaining")} used={r.headers.get("x-requests-used")}')
PY
