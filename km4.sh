true
BASE=https://api.elections.kalshi.com/trade-api/v2/markets
for T in KXFEDDECISION-26JUL-H0 KXFEDDECISION-26JUL-H25 KXFEDDECISION-26SEP-H0; do
  echo "=== $T ==="
  curl -s --max-time 20 "$BASE/$T" | python3 -c "import sys,json
try:
  m=json.load(sys.stdin).get('market',{})
  print('status =', m.get('status'))
  print('result =', m.get('result'))
  print('close_time =', m.get('close_time'))
  print('expiration_time =', m.get('expiration_time'))
  print('can_close_early =', m.get('can_close_early'))
except Exception as e:
  print('PARSE_ERR', e)"
done
echo "=== DONE km4 (Kalshi public API, $0, read-only) ==="
