"""Kalshi public-catalog harvest from THIS machine's IP (the box IP is shared with the live engine's own
Kalshi reads and 429s -- HEARTBEAT_STALL class; local-IP harvest is the pm-backtest + pv2fh precedent).

Input: pairs JSON from the box recorder. Output: {SERIES}_{status}_p{n}.json files (raw /markets pages),
settled pre-filtered with min_close_ts = now - 2d (the sim's PubKalshi expects the filter pre-applied).
Budget 400 GETs declared, 0.45s throttle, HARD STOP on the first 429 (exit 3)."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

PAIRS_FILE = sys.argv[1]
OUT = sys.argv[2]
BUDGET = 400
THROTTLE = 0.45
LOOKBACK = 2 * 86400
BASE = "https://api.elections.kalshi.com/trade-api/v2/markets"

pairs = json.load(open(PAIRS_FILE))["pairs"]
os.makedirs(OUT, exist_ok=True)
calls = 0
now = int(time.time())
for series, status in pairs:
    if not series:
        continue
    cursor = None
    page = 0
    while True:
        if calls >= BUDGET:
            print("BUDGET EXHAUSTED at %d calls" % calls)
            sys.exit(2)
        q = {"series_ticker": series, "status": status, "limit": "1000"}
        if status == "settled":
            q["min_close_ts"] = str(now - LOOKBACK)
        if cursor:
            q["cursor"] = cursor
        url = BASE + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={"User-Agent": "tc-research/1.0"})
        calls += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("HTTP 429 at call %d (%s %s) -- HARD STOP per rules" % (calls, series, status))
                sys.exit(3)
            print("HTTP %s at call %d (%s %s) -- recorded as empty page" % (e.code, calls, series, status))
            d = {"markets": [], "cursor": ""}
        time.sleep(THROTTLE)
        with open(os.path.join(OUT, "%s_%s_p%d.json" % (series, status, page)), "w") as f:
            json.dump({"markets": d.get("markets") or []}, f)
        page += 1
        cursor = d.get("cursor")
        if not cursor or not (d.get("markets") or []):
            break
print("HARVEST_OK calls=%d pairs=%d out=%s" % (calls, len(pairs), OUT))
