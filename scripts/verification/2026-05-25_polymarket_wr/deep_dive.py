"""
Deep dive into the counting bugs found in verify_wr.py
"""
import json, time, urllib.request

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def fetch_activity(wallet, max_pages=4):
    rows = []
    offset = 0
    limit = 500
    for _ in range(max_pages):
        url = f"https://data-api.polymarket.com/activity?user={wallet}&limit={limit}&offset={offset}"
        page = http_get(url)
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return rows

# ============================================================
# MOSLEY1 INVESTIGATION
# Bug: 5 losses not counted, prod shows 100% instead of 95%
# ============================================================
print("="*60)
print("MOSLEY1 DEEP DIVE")
print("="*60)
wallet_m = "0x5bec79df9add70a3892041ab1a5516b60f53b215"
activity_m = fetch_activity(wallet_m)

buy_rows_m = [r for r in activity_m if r.get("type") == "TRADE" and r.get("side") == "BUY"]
print(f"Total activity: {len(activity_m)}, BUY rows: {len(buy_rows_m)}")

# Unique condition_ids
from collections import Counter
cid_counter = Counter()
for r in buy_rows_m:
    cid = r.get("conditionId") or r.get("condition_id", "")
    cid_counter[cid] += 1

print(f"Unique condition_ids: {len(cid_counter)}")
print(f"Top 5 by trade count:")
for cid, cnt in cid_counter.most_common(5):
    print(f"  {cid[:30]}... x{cnt}")

# Find the Fucsovics condition_id
fucsovics_cid = None
for r in buy_rows_m:
    cid = r.get("conditionId") or r.get("condition_id", "")
    # We know outcome_idx=0, Fucsovics, price~0.43
    if r.get("price") and abs(float(r.get("price", 0) or 0) - 0.43) < 0.01:
        fucsovics_cid = cid
        break

print(f"\nFucsovics condition_id (by price ~0.43): {fucsovics_cid}")

# Check what gamma returns for this market
if fucsovics_cid:
    url_open = f"https://gamma-api.polymarket.com/markets?condition_ids={fucsovics_cid}&limit=50"
    url_closed = f"https://gamma-api.polymarket.com/markets?condition_ids={fucsovics_cid}&limit=50&closed=true"
    r_open = http_get(url_open)
    time.sleep(0.2)
    r_closed = http_get(url_closed)

    print(f"\nGamma open query returns {len(r_open)} markets")
    print(f"Gamma closed query returns {len(r_closed)} markets")

    if r_closed:
        m = r_closed[0]
        print(f"  closed={m.get('closed')}")
        print(f"  outcomePrices={m.get('outcomePrices')}")
        print(f"  outcomes={m.get('outcomes')}")
        print(f"  question={m.get('question','')[:60]}")

# KEY QUESTION: How many unique condition_ids are resolved?
# Pull all condition_ids from activity
all_cids_m = list(set(r.get("conditionId") or r.get("condition_id","") for r in buy_rows_m if r.get("conditionId") or r.get("condition_id")))
print(f"\nAll unique cids from BUY activity: {len(all_cids_m)}")
print(f"CIDs: {all_cids_m[:5]}")

# Check what ACTIVITY field names look like for the first few rows
print(f"\nFirst BUY row keys: {list(buy_rows_m[0].keys())}")
print(f"First BUY row sample:")
first = buy_rows_m[0]
for k in ["type","side","conditionId","condition_id","outcomeIndex","outcome_index","price","avgPrice","size","timestamp"]:
    if k in first:
        print(f"  {k}: {first[k]}")

# ============================================================
# RUNAROUND: All 100 trades in ~same few markets?
# This is suspicious — is the "window" really just repeated trades
# on the same markets, or is the filter wrong?
# ============================================================
print("\n" + "="*60)
print("RUNAROUND DEEP DIVE")
print("="*60)
wallet_r = "0xc0ff6a9ac424210cf218fda5c5753324c34a9953"
activity_r = fetch_activity(wallet_r, max_pages=4)

buy_rows_r = [r for r in activity_r if r.get("type") == "TRADE" and r.get("side") == "BUY"]
print(f"Total activity: {len(activity_r)}, BUY rows: {len(buy_rows_r)}")

cid_counter_r = Counter()
for r in buy_rows_r:
    cid = r.get("conditionId") or r.get("condition_id", "")
    cid_counter_r[cid] += 1

print(f"Unique condition_ids: {len(cid_counter_r)}")
print(f"Top 10 by trade count:")
for cid, cnt in cid_counter_r.most_common(10):
    print(f"  {cid[:30]}... x{cnt}")

# Show the unique market titles for top cids
top_cids_r = [cid for cid, _ in cid_counter_r.most_common(20)]
# Quick batch fetch
from urllib.parse import urlencode
params = "&".join(f"condition_ids={c}" for c in top_cids_r[:20])
url = f"https://gamma-api.polymarket.com/markets?{params}&limit=50&closed=true"
try:
    mkts = http_get(url)
    print(f"\nGamma returned {len(mkts)} markets for top 20 cids")
    for m in mkts[:10]:
        cid = m.get("conditionId","")
        cnt = cid_counter_r.get(cid, 0)
        closed = m.get("closed")
        prices = m.get("outcomePrices","[]")
        q = m.get("question","")[:60]
        print(f"  x{cnt} closed={closed} prices={prices[:30]} '{q}'")
except Exception as e:
    print(f"Error: {e}")

# Now check if the 100 kept trades represent UNIQUE markets or repeat-trades
# Walk the windowing exactly
print("\nSimulating windowing for Runaround:")
all_cids_r = list(set(r.get("conditionId") or r.get("condition_id","") for r in buy_rows_r if r.get("conditionId") or r.get("condition_id")))

# Fetch resolutions for all
def fetch_resolutions(cids):
    results = {}
    for i in range(0, len(cids), 50):
        chunk = cids[i:i+50]
        params = "&".join(f"condition_ids={c}" for c in chunk)
        for closed in [False, True]:
            url = f"https://gamma-api.polymarket.com/markets?{params}&limit=50"
            if closed:
                url += "&closed=true"
            try:
                data = http_get(url)
                for m in data:
                    cid = m.get("conditionId") or m.get("condition_id")
                    if cid:
                        if closed and cid in results:
                            results[cid].update(m)  # closed overwrites
                        elif cid not in results:
                            results[cid] = dict(m)
                        elif closed:
                            results[cid] = dict(m)
            except Exception as e:
                print(f"  error: {e}")
            time.sleep(0.2)
    return results

resolutions_r = fetch_resolutions(all_cids_r)
print(f"Resolutions fetched: {len(resolutions_r)}")

resolved_count = 0
unresolved_count = 0
for cid, m in resolutions_r.items():
    closed = m.get("closed", False)
    try:
        prices = json.loads(m.get("outcomePrices","[]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices",[])
        prices = [float(p) for p in prices]
    except:
        prices = []
    has_winner = any(p >= 0.9 for p in prices)
    if closed and has_winner:
        resolved_count += 1
    else:
        unresolved_count += 1

print(f"Resolved markets: {resolved_count}, Unresolved: {unresolved_count}")

# Walk windowing
kept_r = []
unique_cids_in_window = set()
for row in buy_rows_r:
    cid = row.get("conditionId") or row.get("condition_id","")
    if not cid:
        continue
    res = resolutions_r.get(cid)
    if not res:
        continue
    closed = res.get("closed", False)
    try:
        prices = json.loads(res.get("outcomePrices","[]")) if isinstance(res.get("outcomePrices"), str) else res.get("outcomePrices",[])
        prices = [float(p) for p in prices]
    except:
        prices = []
    has_winner = any(p >= 0.9 for p in prices)
    if closed and has_winner:
        kept_r.append((row, res, prices))
        unique_cids_in_window.add(cid)
    if len(kept_r) >= 100:
        break

print(f"Kept (resolved BUYs up to 100): {len(kept_r)}")
print(f"Unique condition_ids in kept window: {len(unique_cids_in_window)}")

# Show distribution
window_cid_counts = Counter(row.get("conditionId") or row.get("condition_id","") for row, _, _ in kept_r)
print(f"Top 10 cids in window by trade count:")
for cid, cnt in window_cid_counts.most_common(10):
    m = resolutions_r.get(cid, {})
    print(f"  x{cnt} '{m.get('question','')[:50]}'")

# Score
wins_r = 0
losses_r = 0
for row, res, prices in kept_r:
    oi = row.get("outcomeIndex")
    try:
        oi = int(oi)
    except:
        oi = None
    winner_idx = None
    for i, p in enumerate(prices):
        if p >= 0.9:
            winner_idx = i
            break
    is_win = (oi is not None and oi == winner_idx)
    if is_win:
        wins_r += 1
    else:
        losses_r += 1

print(f"Runaround computed: wins={wins_r}, losses={losses_r}, WR={wins_r/len(kept_r):.4f}")

# CRITICAL: Check if there are any resolved-LOSING markets for Runaround
print("\nAll resolved markets for Runaround:")
for cid, m in resolutions_r.items():
    closed = m.get("closed", False)
    try:
        prices = json.loads(m.get("outcomePrices","[]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices",[])
        prices = [float(p) for p in prices]
    except:
        prices = []
    has_winner = any(p >= 0.9 for p in prices)
    if closed and has_winner:
        winner_idx = next((i for i, p in enumerate(prices) if p >= 0.9), None)
        # Find what outcome_idx Runaround bet on for this market
        bets = [r for r in buy_rows_r if (r.get("conditionId") or r.get("condition_id","")) == cid]
        bet_outcome_idxs = list(set(r.get("outcomeIndex") for r in bets))
        outcomes = m.get("outcomes", "[]")
        print(f"  cid={cid[:20]}... winner_idx={winner_idx} bet_idxs={bet_outcome_idxs} "
              f"prices={prices} outcomes={outcomes[:60]}")
        print(f"    question: {m.get('question','')[:60]}")
