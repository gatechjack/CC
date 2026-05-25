"""
Trace exactly what happens with Mosley1's 150 target_buy_rows limit.

The seed script stops fetching activity when buy_count >= 150.
With 1980 BUYs in the first 2000 rows, it's definitely hitting 150 early.
But which 150? And do those 150 contain the Fucsovics trades at positions 32-36?

YES they do (positions 32-36 in buy_rows, which is within first 150).

So the Fucsovics market IS resolved, IS in the window... but prod says win.

Hypothesis: The resolutions dict from gamma is somehow returning the
Fucsovics market as a WIN for outcome_index=0.

Let me check: does the current gamma closed=true response for Fucsovics
have outcomePrices=["0","1"] (Berrettini wins, i.e. index 1)?
Or could there be a different market with this conditionId?

Also check: the SORTING of outcomes — does gamma always return outcomes
in the same order as the activity API's outcomeIndex?
"""
import json, time, urllib.request

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

# The Fucsovics conditionId
fucsovics_cid = "0xa2a8b2f7341e95f955f4e801d2dd9764834f48ccbf25d825adedbf72d169c8d7"

# Fetch both open and closed
r_open = http_get(f"https://gamma-api.polymarket.com/markets?condition_ids={fucsovics_cid}&limit=50")
time.sleep(0.3)
r_closed = http_get(f"https://gamma-api.polymarket.com/markets?condition_ids={fucsovics_cid}&limit=50&closed=true")

print("OPEN query result count:", len(r_open))
print("CLOSED query result count:", len(r_closed))

if r_closed:
    m = r_closed[0]
    print("\nFull market record (closed query):")
    for k, v in m.items():
        print(f"  {k}: {v}")

# Now let's check: Mosley1 activity says outcomeIndex=0 = Marton Fucsovics
# gamma closed says outcomePrices=["0","1"] → index 1 wins (Berrettini)
# So this IS a loss. Our compute correctly gives LOSS.
# But prod gives WIN.

# The only way this resolves as WIN on prod is if either:
# (a) The gamme API returned DIFFERENT data when prod ran (stale data at resolution time)
# (b) The `_decode_resolution` receives different data
# (c) The market_resolutions dict lookup uses wrong key

# Let's check: what IS in the market_resolutions dict for Fucsovics on prod?
# We can check by querying the polymarket_whale_stats audit table or any cached data.

# Actually - look at this more carefully.
# Mosley1 has 20 unique condition_ids total.
# The Fucsovics market is ONE of those 20.
# But Mosley1 traded it 5 times (buy_rows 32-36).
# The windowed 100 resolved BUYs includes these 5 (they're in positions 1-5 of resolved window).

# Wait -- let me re-examine. From verify_wr.py:
# STORED: wins=100, losses=0, WR=1.0
# COMPUTED: wins=95, losses=5, WR=0.9500
# The 5 losses are Fucsovics.

# The key question: when did prod run the seed script?
# If it ran BEFORE the Fucsovics match resolved, the market would have been "pending"
# (not "resolved"), and those 5 BUYs would have been SKIPPED (win=None → continue).
# Then the window of 100 would be filled by the next 100 resolved BUYs - all wins.

# Let's check the Fucsovics match timing.
# The activity timestamp for Fucsovics trades:
wallet_m = "0x5bec79df9add70a3892041ab1a5516b60f53b215"
r = http_get(f"https://data-api.polymarket.com/activity?user={wallet_m}&limit=100&offset=0")
fucsovics_rows = [x for x in r if abs(float(x.get("price",0) or 0) - 0.43) < 0.01 and x.get("outcomeIndex") == 0]
print(f"\nFucsovics trade timestamps: {[x.get('timestamp') for x in fucsovics_rows]}")

# Convert to human dates
import datetime
for x in fucsovics_rows[:3]:
    ts = x.get("timestamp")
    if ts:
        dt = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc)
        print(f"  timestamp {ts} = {dt.isoformat()}")

# The watchlist was written to agent_state. When was it last updated?
# We can check from prod.
print("\nThe key insight:")
print("If the seed ran BEFORE Fucsovics resolved, those 5 trades would be 'pending'")
print("and SKIPPED in the window → not counted in denominator → 100 real wins from other markets.")
print("Now that Fucsovics HAS resolved (closed=True, prices=[0,1]), our local compute")
print("correctly identifies them as losses. But the stored stats reflect the state at seed time.")
print()
print("This is a STALE DATA bug: the stored win_rate is frozen at seed time.")
print("It doesn't reflect resolution events that occurred AFTER the seed ran.")

# Let's also verify: what are the 100 winning condition_ids Mosley1 actually has?
# From deep_dive we know 20 unique cids, Fucsovics was 1 of them.
# The other 19 all resolved as wins? Let's check.
r2 = http_get(f"https://data-api.polymarket.com/activity?user={wallet_m}&limit=500&offset=0")
time.sleep(0.3)
buy_rows = [x for x in r2 if x.get("type") == "TRADE" and x.get("side") == "BUY"]

# Get all cids and their trade counts
from collections import Counter
cid_counts = Counter(x.get("conditionId","") for x in buy_rows)
print(f"Mosley1 unique condition_ids: {len(cid_counts)}")
print(f"Top CIDs by trade count:")
for cid, cnt in cid_counts.most_common(10):
    print(f"  {cnt}x {cid[:40]}")

# Fetch all resolutions for these
all_cids = [c for c in cid_counts if c]
params = "&".join(f"condition_ids={c}" for c in all_cids)
r_open2 = http_get(f"https://gamma-api.polymarket.com/markets?{params}&limit=50")
time.sleep(0.3)
r_closed2 = http_get(f"https://gamma-api.polymarket.com/markets?{params}&limit=50&closed=true")

print(f"\nGamma open: {len(r_open2)} markets, closed: {len(r_closed2)} markets")

# What are the resolutions for each cid?
resolutions = {}
for m in r_open2:
    cid = m.get("conditionId","")
    resolutions[cid] = m
for m in r_closed2:
    cid = m.get("conditionId","")
    resolutions[cid] = m  # closed wins

print("\nAll 20 markets for Mosley1:")
for cid, m in resolutions.items():
    closed = m.get("closed", False)
    prices = m.get("outcomePrices","[]")
    outcomes = m.get("outcomes","[]")
    q = m.get("question","")[:50]
    cnt = cid_counts.get(cid, 0)

    try:
        pp = json.loads(prices) if isinstance(prices,str) else prices
        pp = [float(p) for p in pp]
    except:
        pp = []

    winner_idx = next((i for i,p in enumerate(pp) if p >= 0.9), None)

    # What did Mosley1 bet?
    mosley_bets = [x for x in buy_rows if x.get("conditionId","") == cid]
    bet_idxs = list(set(int(x.get("outcomeIndex",-1)) for x in mosley_bets))

    status = "pending"
    if closed:
        status = "resolved" if winner_idx is not None else "void"

    correct = (winner_idx in bet_idxs) if winner_idx is not None else None
    print(f"  x{cnt} [{status}] correct={correct} bet={bet_idxs} winner={winner_idx} prices={pp}")
    print(f"    {q}")
    print(f"    outcomes={outcomes}")
