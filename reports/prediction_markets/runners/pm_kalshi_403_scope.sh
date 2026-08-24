#!/usr/bin/env bash
# READ-ONLY: scope Kalshi's geo-403 with UNAUTHENTICATED reads + demo reachability + poly_kalshi status.
# NO auth, NO secrets, NO KeyVault, NO orders. Writes only /tmp scratch. Engine-PID bracketed.
echo "=== KALSHI 403 SCOPE (read-only, unauthenticated) $(date -u) ==="
P0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_BEFORE=$P0"

echo ""
echo "=== [LIVE] poly_kalshi_mlb status (journal, last 400 lines) -- still armed + failing on 403? ==="
journalctl -u trading-corp.service -n 400 --no-pager 2>&1 | grep -iE 'poly_kalshi|kalshi.*(403|geo|washington|not[_ ]allowed)|Trading not allowed|WASHINGTON' | tail -18
echo "--- recent timestamps above = the armed retry loop is still firing; none = quiet ---"

echo ""
echo "=== EGRESS IP (what Kalshi geolocates) ==="
curl -sS -m 15 https://api.ipify.org 2>&1; echo "  <- egress IP (expect 168.62.60.79 per record)"

# print http status + a slice of the body (esp. the full 403 geo body) for one URL
probe(){ local u="$1" lbl="$2"; local b="/tmp/kb.$$"; local code
  code=$(curl -sS -m 25 -o "$b" -w "%{http_code}" "$u" 2>/tmp/ke.$$); echo ""; echo "[$code] $lbl"
  echo "   URL: $u"
  echo "   body: $(head -c 600 "$b" 2>/dev/null | tr '\n' ' ')"
  [ -s /tmp/ke.$$ ] && echo "   curl-stderr: $(head -c 200 /tmp/ke.$$)"
  rm -f "$b" /tmp/ke.$$; }

echo ""
echo "=== [1a] UNAUTHENTICATED public reads -- does the geo-403 apply to READS/listings at all? ==="
echo "-- prod TRADING host external-api.kalshi.com (where the ORDER 403 was seen) --"
probe "https://external-api.kalshi.com/trade-api/v2/markets?limit=2" "external markets (general, all categories)"
probe "https://external-api.kalshi.com/trade-api/v2/events?series_ticker=KXMLBGAME&limit=2" "external MLB events (SPORTS)"
echo ""
echo "-- prod READ host api.elections.kalshi.com (the read broker default) --"
probe "https://api.elections.kalshi.com/trade-api/v2/markets?limit=2" "elections markets (general)"
probe "https://api.elections.kalshi.com/trade-api/v2/series?category=Sports&limit=4" "elections SPORTS series listing (reveals MLB ticker + tests sports read)"
probe "https://api.elections.kalshi.com/trade-api/v2/series?category=Economics&limit=2" "elections ECONOMICS series (non-sports contrast)"

echo ""
echo "=== [3] DEMO host reachability (Task 3: is demo geo-blocked from this egress?) ==="
probe "https://external-api.demo.kalshi.co/trade-api/v2/markets?limit=1" "DEMO markets"

echo ""
P1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "ENGINE_AFTER=$P1 BEFORE=$P0"
[ "$P0" = "$P1" ] && [ -n "$P0" ] && echo "ENGINE_UNCHANGED=GOOD" || echo "ENGINE_CHANGED=INVESTIGATE"
echo "=== END KALSHI 403 SCOPE ==="
