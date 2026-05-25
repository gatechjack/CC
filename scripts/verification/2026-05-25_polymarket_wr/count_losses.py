"""
Count actual losses from the resolved market dump above and understand why they're excluded from window.
Also nail down the Mosley1 bug precisely.
"""
import json, time, urllib.request
from collections import Counter

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

def fetch_resolutions(cids):
    results = {}
    for i in range(0, len(cids), 50):
        chunk = cids[i:i+50]
        params = "&".join(f"condition_ids={c}" for c in chunk)
        for closed_flag in [False, True]:
            url = f"https://gamma-api.polymarket.com/markets?{params}&limit=50"
            if closed_flag:
                url += "&closed=true"
            try:
                data = http_get(url)
                for m in data:
                    cid = m.get("conditionId") or m.get("condition_id")
                    if cid:
                        if closed_flag:
                            results[cid] = dict(m)
                        elif cid not in results:
                            results[cid] = dict(m)
            except Exception as e:
                print(f"  error: {e}")
            time.sleep(0.2)
    return results

# ============================================================
# RUNAROUND: Why are losses excluded from the 100-trade window?
# From deep_dive.py we see: many resolved markets where bet_idx != winner_idx
# But my windowing computed wins=100, losses=0
# The window only hits 13 unique markets -- the earliest-resolved ones
# Key question: Are the LOSING resolved markets NOT in the first 100 kept BUYs?
# ============================================================
print("="*60)
print("RUNAROUND: Understanding why losses aren't in window")
print("="*60)

wallet_r = "0xc0ff6a9ac424210cf218fda5c5753324c34a9953"
activity_r = fetch_activity(wallet_r, max_pages=4)
buy_rows_r = [r for r in activity_r if r.get("type") == "TRADE" and r.get("side") == "BUY"]

print(f"Total BUY rows: {len(buy_rows_r)}")
print(f"Timestamps: first={buy_rows_r[0].get('timestamp')}, last={buy_rows_r[-1].get('timestamp')}")

all_cids_r = list(set(r.get("conditionId","") for r in buy_rows_r if r.get("conditionId")))
resolutions_r = fetch_resolutions(all_cids_r)

# Categorize all markets
winning_for_runaround = []  # markets where Runaround bet correctly
losing_for_runaround = []   # markets where Runaround bet wrong
unresolved = []

for cid, m in resolutions_r.items():
    closed = m.get("closed", False)
    try:
        prices = json.loads(m.get("outcomePrices","[]")) if isinstance(m.get("outcomePrices"), str) else m.get("outcomePrices",[])
        prices = [float(p) for p in prices]
    except:
        prices = []
    has_winner = any(p >= 0.9 for p in prices)

    if not (closed and has_winner):
        unresolved.append(cid)
        continue

    winner_idx = next((i for i, p in enumerate(prices) if p >= 0.9), None)
    bets = [r for r in buy_rows_r if r.get("conditionId","") == cid]
    bet_outcome_idxs = set(int(r.get("outcomeIndex", -1)) for r in bets)

    if winner_idx in bet_outcome_idxs:
        winning_for_runaround.append((cid, m, bets, winner_idx, prices))
    else:
        losing_for_runaround.append((cid, m, bets, winner_idx, prices))

print(f"\nWinning markets: {len(winning_for_runaround)}")
print(f"Losing markets: {len(losing_for_runaround)}")
print(f"Unresolved: {len(unresolved)}")

print(f"\nLOSING MARKETS for Runaround:")
for cid, m, bets, winner_idx, prices in losing_for_runaround:
    bet_idxs = [int(r.get("outcomeIndex",-1)) for r in bets]
    q = m.get("question","")[:60]
    outcomes = m.get("outcomes","[]")
    # Find position of these bets in the buy_rows array (i.e., how far down the feed)
    first_pos = next((i for i, r in enumerate(buy_rows_r) if r.get("conditionId","") == cid), -1)
    # Count how many resolved BUYs appear BEFORE this in the feed
    resolved_before = 0
    for r in buy_rows_r[:first_pos]:
        rcid = r.get("conditionId","")
        rres = resolutions_r.get(rcid, {})
        rclosed = rres.get("closed", False)
        try:
            rprices = json.loads(rres.get("outcomePrices","[]")) if isinstance(rres.get("outcomePrices"), str) else rres.get("outcomePrices",[])
            rprices = [float(p) for p in rprices]
        except:
            rprices = []
        rhas_winner = any(p >= 0.9 for p in rprices)
        if rclosed and rhas_winner:
            resolved_before += 1

    print(f"  first_pos_in_feed={first_pos}, resolved_before={resolved_before}")
    print(f"  bet_idxs={bet_idxs[:3]} winner_idx={winner_idx} prices={prices}")
    print(f"  '{q}' outcomes={outcomes}")
    print(f"  -> Runaround bet on {[json.loads(outcomes)[bi] if isinstance(outcomes,str) else outcomes[bi] for bi in bet_idxs[:1] if bi >= 0]}")
    print()

# ============================================================
# MOSLEY1: The 5 losses (Fucsovics) -- why does prod score them as wins?
# ============================================================
print("="*60)
print("MOSLEY1: Diagnosing the 5 Fucsovics losses")
print("="*60)

wallet_m = "0x5bec79df9add70a3892041ab1a5516b60f53b215"
activity_m = fetch_activity(wallet_m, max_pages=4)
buy_rows_m = [r for r in activity_m if r.get("type") == "TRADE" and r.get("side") == "BUY"]

all_cids_m = list(set(r.get("conditionId","") for r in buy_rows_m if r.get("conditionId")))
resolutions_m = fetch_resolutions(all_cids_m)

# Find the Fucsovics cid
fucsovics_rows = [r for r in buy_rows_m if abs(float(r.get("price",0) or 0) - 0.43) < 0.01 and r.get("outcomeIndex") == 0]
fucsovics_cid = fucsovics_rows[0].get("conditionId") if fucsovics_rows else None
print(f"Fucsovics CID: {fucsovics_cid}")

if fucsovics_cid:
    res = resolutions_m.get(fucsovics_cid, {})
    print(f"  closed={res.get('closed')}")
    print(f"  outcomePrices={res.get('outcomePrices')}")
    print(f"  outcomes={res.get('outcomes')}")
    print(f"  question={res.get('question','')[:80]}")

    # Mosley1 bet outcomeIndex=0 (Fucsovics), but winner_idx=1 (Berrettini)
    # Our compute correctly gives LOSS. But prod gives WIN.
    # The only way prod gives WIN is if outcomeIndex somehow resolves differently
    # or if the resolutions dict doesn't have this cid at all (unresolved → filtered out,
    # meaning it's NOT in the 100-trade window, so losses don't come from this market)

    # Check: is this market in the 100-trade window in our compute?
    # From verify_wr.py run: 5 LOSSES appear in sample, so YES it is in our window
    # But prod gives 100/0. So prod either:
    # (a) doesn't resolve this market (filtered out → not in denominator)
    # (b) resolves it as a win somehow

    # Let's check: does our fetch_resolutions get a different result from gamma?
    # The open query returned 0 markets, closed returned 1 with prices=["0","1"] winner=Berrettini
    # So our code correctly identifies this as a loss.
    # But wait -- what if prod's fetch_market_resolutions call uses a DIFFERENT
    # field name for conditionId? Let me check what the activity row looks like.
    print(f"\nFucsovics activity row fields:")
    for r in fucsovics_rows[:1]:
        for k, v in r.items():
            print(f"  {k}: {v}")

# KEY INSIGHT: Check the actual gamma market field for conditionId
# vs what the activity row uses
print("\nChecking field name alignment: activity conditionId vs gamma conditionId")
sample_cid = all_cids_m[0] if all_cids_m else None
if sample_cid:
    url = f"https://gamma-api.polymarket.com/markets?condition_ids={sample_cid}&limit=1&closed=true"
    data = http_get(url)
    if data:
        print(f"Gamma market keys: {list(data[0].keys())[:20]}")
        print(f"gamma conditionId: {data[0].get('conditionId')}")
        print(f"gamma condition_id: {data[0].get('condition_id')}")

# Check if Mosley1's activity has 'conditionId' field consistently
print(f"\nMosley1 conditionId field check (first 5 BUY rows):")
for r in buy_rows_m[:5]:
    print(f"  conditionId={r.get('conditionId')}, condition_id={r.get('condition_id')}")

# ANOTHER HYPOTHESIS: The activity API might paginate differently on prod
# vs our local pull. If prod fetches fewer pages, different rows land in window.
# But we're seeing stored: 100 wins, 0 losses → all 100 are wins.
# That means prod is EITHER:
# 1. Not resolving Fucsovics market (missing from resolutions dict)
# 2. Resolving it but computing win wrong
# 3. Fucsovics trades are beyond position 100 in the resolved-BUY window ON PROD

# Let's check: where do Fucsovics trades appear in the buy_rows feed?
fucsovics_positions = [i for i, r in enumerate(buy_rows_m) if r.get("conditionId","") == fucsovics_cid]
print(f"\nFucsovics trade positions in buy_rows (0-indexed): {fucsovics_positions}")
print(f"Total buy_rows: {len(buy_rows_m)}")

# Simulate windowing and track position
resolved_count = 0
for i, r in enumerate(buy_rows_m):
    cid = r.get("conditionId","")
    res = resolutions_m.get(cid, {})
    closed = res.get("closed", False)
    try:
        prices = json.loads(res.get("outcomePrices","[]")) if isinstance(res.get("outcomePrices"), str) else res.get("outcomePrices",[])
        prices = [float(p) for p in prices]
    except:
        prices = []
    has_winner = any(p >= 0.9 for p in prices)
    if closed and has_winner:
        resolved_count += 1
        if cid == fucsovics_cid:
            print(f"  Fucsovics at buy_row[{i}] -> resolved_count={resolved_count} (in window if <= 100)")
        if resolved_count >= 100:
            print(f"  Window of 100 exhausted at buy_row[{i}]")
            break
