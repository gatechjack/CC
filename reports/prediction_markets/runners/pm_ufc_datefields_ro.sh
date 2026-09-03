set -u
ROOT=/home/azureuser/trading_corp
V=$ROOT/venv/bin/python
date -u +"### UFC TICKER-DATE vs EVENT-DATE PROBE (READ-ONLY, public GET) %Y-%m-%dT%H:%M:%SZ ###"
echo "Q: is the Kalshi ticker date the EVENT date (=> Poly slug date matches, join sound, close_time is a late admin close), or something else?"
PYTHONPATH="$ROOT" "$V" - <<'PY'
import json, urllib.request, urllib.error, re
BASE="https://api.elections.kalshi.com/trade-api/v2"
def get(url):
    req=urllib.request.Request(url, headers={"User-Agent":"curl/8.4.0"})
    try:
        r=urllib.request.urlopen(req,timeout=25); return getattr(r,"status",200), json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, {}
    except Exception as e: return "ERR:%r"%e, {}
MON=("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC")
def tdate(tk):
    m=re.match(r"^KXUFC(?:FIGHT|DISTANCE)-(\d{2})([A-Z]{3})(\d{2})",tk or "")
    if not m: return None
    try: mo=MON.index(m.group(2))+1
    except ValueError: return None
    return "20%s-%02d-%02d"%(m.group(1),mo,int(m.group(3)))
for series in ("KXUFCFIGHT","KXUFCDISTANCE"):
    code,data=get(BASE+"/markets?series_ticker=%s&status=open&limit=80"%series)
    mk=(data.get("markets") if isinstance(data,dict) else None) or []
    print("=== %s open=%d (one market per event_ticker) ==="%(series,len(mk)))
    seen=set()
    for s in mk:
        ev=s.get("event_ticker")
        if ev in seen: continue
        seen.add(ev)
        tk=s.get("ticker")
        print("  ticker=%s  parsed_ticker_date=%s"%(tk, tdate(tk)))
        for k in ("occurrence_datetime","open_time","close_time","expected_expiration_time","latest_expiration_time","expiration_time","status"):
            print("     %-26s = %r"%(k, s.get(k)))
        if len(seen)>=8: break
print()
print("=== /events for KXUFCFIGHT (event-level scheduled date: strike_date / sub_title) ===")
code,data=get(BASE+"/events?series_ticker=KXUFCFIGHT&status=open&limit=25&with_nested_markets=false")
evs=(data.get("events") if isinstance(data,dict) else None) or []
print("  http=%s events=%d"%(code,len(evs)))
for e in evs[:10]:
    print("   event_ticker=%s keys=%s"%(e.get("event_ticker"), sorted(e.keys())))
    for k in ("event_ticker","title","sub_title","strike_date","strike_period","expected_expiration_time","occurrence_datetime"):
        if k in e: print("      %-24s = %r"%(k, e.get(k)))
PY
echo "### DONE ###"
