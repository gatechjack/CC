KB="https://api.elections.kalshi.com/trade-api/v2"
echo "########## MARK SINGLE-TICKER ENDPOINT PROBE v2 -- READ ONLY ##########"
for S in KXNCAAFGAME KXMLBGAME KXATPMATCH; do
  B=$(curl -s --max-time 20 "$KB/markets?series_ticker=$S&status=open&limit=1")
  echo "=== series $S : list head = [$(printf '%s' "$B" | head -c 160)]"
  TK=$(printf '%s' "$B" | grep -o '"ticker":"[^"]*"' | head -1 | sed 's/.*:"//;s/"$//')
  echo "    extracted ticker = [$TK]"
  if [ -n "$TK" ]; then
    M=$(curl -s --max-time 20 "$KB/markets/$TK")
    echo "    single GET has_market_key = $(printf '%s' "$M" | grep -o '\"market\":{' | wc -l | tr -d ' ')"
    echo "    single GET dollars = $(printf '%s' "$M" | grep -o '\"\(yes_bid\|no_bid\|yes_ask\|no_ask\|last_price\)_dollars\":\"[^\"]*\"' | tr '\n' ' ')"
    echo "    single GET status/title = $(printf '%s' "$M" | grep -o '\"status\":\"[^\"]*\"' | head -1) $(printf '%s' "$M" | grep -o '\"title\":\"[^\"]*\"' | head -1)"
    echo "    single GET head = [$(printf '%s' "$M" | head -c 200)]"
    break
  fi
done
echo "########## PROBE COMPLETE ##########"
