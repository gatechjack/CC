cd /home/azureuser/trading_corp && KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ /home/azureuser/trading_corp/venv/bin/python3 - <<'PY'
import sys, urllib.request, urllib.parse, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '.')
from trading_corp.utils.secrets import load_secrets
key = load_secrets().odds_api_key

def probe(label, params):
    u = 'https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?' + urllib.parse.urlencode({
        'apiKey': key, 'regions':'us', 'markets':'h2h',
        'bookmakers':'pinnacle,draftkings,fanduel,betmgm',
        'oddsFormat':'american', 'dateFormat':'iso',
        **params,
    })
    r = urllib.request.urlopen(u, timeout=15)
    data = json.loads(r.read())
    dates = {}
    last_updates = []
    for g in data:
        d = (g.get('commence_time') or '')[:10]
        dates[d] = dates.get(d, 0) + 1
        for b in g.get('bookmakers') or []:
            lu = b.get('last_update')
            if lu:
                last_updates.append(lu)
    print(f'\n--- {label}: {len(data)} games, quota={r.headers.get("x-requests-remaining")}/{r.headers.get("x-requests-used")} ---')
    for d in sorted(dates):
        print(f'   date={d}: {dates[d]} games')
    if last_updates:
        last_updates.sort()
        print(f'   bookmaker last_update timestamps:')
        print(f'     earliest: {last_updates[0]}')
        print(f'     latest:   {last_updates[-1]}')
        # Stale check
        now = datetime.now(timezone.utc)
        latest = datetime.fromisoformat(last_updates[-1].replace('Z','+00:00'))
        age = (now - latest).total_seconds()
        print(f'     freshness: latest update is {age:.0f}s ago (NOW={now.isoformat()})')

# 1. Default (what observer uses)
probe('DEFAULT (no time filter)', {})

# 2. Explicit commenceTimeTo = +72h
end72 = (datetime.now(timezone.utc) + timedelta(hours=72)).strftime('%Y-%m-%dT%H:%M:%SZ')
probe(f'commenceTimeTo=+72h ({end72})', {'commenceTimeTo': end72})

# 3. Explicit commenceTimeTo = +168h (7 days)
end168 = (datetime.now(timezone.utc) + timedelta(hours=168)).strftime('%Y-%m-%dT%H:%M:%SZ')
probe(f'commenceTimeTo=+168h ({end168})', {'commenceTimeTo': end168})

# 4. List of sports + freshness — see if MLB itself is the slate-limit
u = f'https://api.the-odds-api.com/v4/sports/?apiKey={key}'
r = urllib.request.urlopen(u, timeout=15)
sports = json.loads(r.read())
print(f'\n--- TOTAL SPORTS COVERED: {len(sports)} ---')
mlb = [s for s in sports if 'baseball_mlb' in s.get('key','')]
for s in mlb:
    print(f'   key={s.get("key")} active={s.get("active")} group={s.get("group")} title={s.get("title")}')
PY
