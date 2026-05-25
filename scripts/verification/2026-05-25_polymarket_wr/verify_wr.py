"""
Polymarket WR Investigation
Empirically replicates compute_polymarket_stats for Mosley1 and Runaround
to verify the 100% WR anomaly.
"""

import json
import time
import urllib.request
import urllib.parse
from collections import defaultdict

WHALES = [
    {"user_name": "Mosley1",  "proxy_wallet": "0x5bec79df9add70a3892041ab1a5516b60f53b215"},
    {"user_name": "Runaround", "proxy_wallet": "0xc0ff6a9ac424210cf218fda5c5753324c34a9953"},
]

def http_get(url, label=""):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def fetch_activity(wallet, max_rows=500):
    """Fetch activity pages until we have enough BUY rows or exhaust."""
    rows = []
    offset = 0
    limit = 500
    while True:
        url = f"https://data-api.polymarket.com/activity?user={wallet}&limit={limit}&offset={offset}"
        page = http_get(url, "activity")
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
        if offset >= 2000:  # safety cap
            break
        time.sleep(0.3)
    return rows

def fetch_gamma_markets(condition_ids, closed=False):
    """Fetch markets from gamma API, replicating fetch_market_resolutions exactly."""
    results = {}
    chunk_size = 50
    ids = list(condition_ids)
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i:i+chunk_size]
        params = "&".join(f"condition_ids={cid}" for cid in chunk)
        url = f"https://gamma-api.polymarket.com/markets?{params}&limit=50"
        if closed:
            url += "&closed=true"
        try:
            data = http_get(url)
            for m in data:
                cid = m.get("conditionId") or m.get("condition_id")
                if cid:
                    results[cid] = m
        except Exception as e:
            print(f"  gamma fetch error: {e}")
        time.sleep(0.2)
    return results

def decode_resolution(market):
    """
    Replicate _decode_resolution:
    resolved = closed=True AND any outcomePrices >= 0.9
    winner_idx = first index where price >= 0.9
    Returns (is_resolved, winner_idx, outcomes, outcome_prices)
    """
    closed = market.get("closed", False)
    raw_prices = market.get("outcomePrices", "[]")
    raw_outcomes = market.get("outcomes", "[]")

    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        prices = [float(p) for p in prices]
    except Exception:
        prices = []

    try:
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
    except Exception:
        outcomes = []

    if not closed:
        return False, None, outcomes, prices

    winner_idx = None
    for idx, p in enumerate(prices):
        if p >= 0.9:
            winner_idx = idx
            break

    is_resolved = winner_idx is not None
    return is_resolved, winner_idx, outcomes, prices

def run_investigation(whale):
    name = whale["user_name"]
    wallet = whale["proxy_wallet"]
    print(f"\n{'='*60}")
    print(f"INVESTIGATING: {name} ({wallet})")
    print('='*60)

    # Step 1: fetch activity
    print("Fetching activity...")
    activity = fetch_activity(wallet)
    print(f"  Total activity rows: {len(activity)}")

    # Step 2: filter TRADE+BUY rows only
    buy_rows = []
    for row in activity:
        typ = row.get("type", "")
        side = row.get("side", "")
        if typ == "TRADE" and side == "BUY":
            buy_rows.append(row)
    print(f"  TRADE+BUY rows: {len(buy_rows)}")

    # Collect condition_ids
    cids = set()
    for row in buy_rows:
        cid = row.get("conditionId") or row.get("condition_id")
        if cid:
            cids.add(cid)
    print(f"  Unique condition_ids: {len(cids)}")

    # Step 3: fetch gamma markets (open + closed)
    print("Fetching gamma markets (open)...")
    markets_open = fetch_gamma_markets(cids, closed=False)
    print(f"  Returned (open): {len(markets_open)}")

    print("Fetching gamma markets (closed)...")
    markets_closed = fetch_gamma_markets(cids, closed=True)
    print(f"  Returned (closed): {len(markets_closed)}")

    # Merge: closed takes priority (has resolution data)
    markets = {**markets_open, **markets_closed}
    print(f"  Total unique markets: {len(markets)}")

    # Step 4: decode resolutions
    resolutions = {}
    for cid, m in markets.items():
        is_resolved, winner_idx, outcomes, prices = decode_resolution(m)
        resolutions[cid] = {
            "is_resolved": is_resolved,
            "winner_idx": winner_idx,
            "outcomes": outcomes,
            "prices": prices,
            "closed": m.get("closed", False),
            "title": m.get("question", m.get("title", "")),
        }

    # Step 5: windowing — walk buy_rows in order (already most-recent-first from API)
    # Keep BUYs whose market is resolved, up to 100
    kept = []
    for row in buy_rows:
        cid = row.get("conditionId") or row.get("condition_id")
        if not cid:
            continue
        res = resolutions.get(cid)
        if res and res["is_resolved"]:
            kept.append((row, res))
        if len(kept) >= 100:
            break

    print(f"\n  Kept (resolved BUYs, up to 100): {len(kept)}")

    # Step 6: score wins/losses
    wins = 0
    losses = 0
    sample_rows = []

    for row, res in kept:
        outcome_idx = row.get("outcomeIndex")
        if outcome_idx is None:
            # try alternate field names
            outcome_idx = row.get("outcome_index")

        winner_idx = res["winner_idx"]

        try:
            outcome_idx_int = int(outcome_idx)
        except (TypeError, ValueError):
            outcome_idx_int = None

        is_win = (outcome_idx_int is not None and outcome_idx_int == winner_idx)

        if is_win:
            wins += 1
        else:
            losses += 1

        # Collect samples (first 15)
        if len(sample_rows) < 15:
            sample_rows.append({
                "condition_id": (row.get("conditionId") or row.get("condition_id") or "")[:20] + "...",
                "title": res["title"][:60],
                "outcome_idx_from_activity": outcome_idx,
                "outcome_label": (res["outcomes"][outcome_idx_int] if outcome_idx_int is not None and outcome_idx_int < len(res["outcomes"]) else "?"),
                "outcomes": res["outcomes"],
                "prices": res["prices"],
                "winner_idx": winner_idx,
                "winner_label": (res["outcomes"][winner_idx] if winner_idx is not None and winner_idx < len(res["outcomes"]) else "?"),
                "is_win": is_win,
                "price_at_trade": row.get("price", row.get("avgPrice")),
            })

    computed_wr = wins / len(kept) if kept else 0

    print(f"\n  STORED:   wins={whale.get('wins','?')}, losses={whale.get('losses','?')}, WR={whale.get('win_rate','?')}")
    print(f"  COMPUTED: wins={wins}, losses={losses}, WR={computed_wr:.4f}")

    print(f"\n  Sample trades (first {len(sample_rows)}):")
    for s in sample_rows:
        flag = "WIN " if s["is_win"] else "LOSS"
        print(f"  [{flag}] outcome_idx={s['outcome_idx_from_activity']} label='{s['outcome_label']}' "
              f"winner_idx={s['winner_idx']} winner='{s['winner_label']}' "
              f"price={s['price_at_trade']} prices={s['prices']}")
        print(f"         title: {s['title']}")
        print(f"         outcomes: {s['outcomes']}")

    return {
        "user_name": name,
        "wallet": wallet,
        "stored_wins": whale.get("wins"),
        "stored_losses": whale.get("losses"),
        "stored_wr": whale.get("win_rate"),
        "computed_wins": wins,
        "computed_losses": losses,
        "computed_wr": computed_wr,
        "kept_count": len(kept),
        "samples": sample_rows,
    }

# Stored values from agent_state (from prod query)
WHALES = [
    {"user_name": "Mosley1",   "proxy_wallet": "0x5bec79df9add70a3892041ab1a5516b60f53b215",
     "wins": 100, "losses": 0, "win_rate": 1.0, "avg_entry_price": 0.3942, "share_below_70": 0.99},
    {"user_name": "Runaround", "proxy_wallet": "0xc0ff6a9ac424210cf218fda5c5753324c34a9953",
     "wins": 100, "losses": 0, "win_rate": 1.0, "avg_entry_price": 0.6415, "share_below_70": 0.42},
]

results = []
for whale in WHALES:
    r = run_investigation(whale)
    results.append(r)

# Save results
out_path = r"C:\Users\AA Incorporado\CC\tmp\polymarket_wr_investigation\results.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2)
print(f"\n\nResults saved to {out_path}")
