"""Stage 2: discover weather-trading wallets from a broad market sample.

Loads markets_closed.json, picks a breadth sample (top-by-volume + temporal
strata across the whole horizon), pulls /trades per market, accumulates a
per-wallet weather-activity index. closed-positions (Stage 3) then computes
each candidate's TRUE full weather P&L, so discovery only needs breadth.

Writes data/wallet_activity.json.
"""
from __future__ import annotations
import sys, os, collections
sys.path.insert(0, os.path.dirname(__file__))
from _pmwx import DATA, get_json, map_concurrent, load, save

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def pick_sample(markets):
    """Breadth sample: top-1500 by volume + top-6 per date_tok for temporal
    coverage. Union, dedup by conditionId."""
    by_vol = sorted([m for m in markets if m.get("conditionId")],
                    key=lambda m: m.get("volumeNum") or 0, reverse=True)
    sample = {m["conditionId"]: m for m in by_vol[:1500]}
    by_date = collections.defaultdict(list)
    for m in by_vol:
        by_date[m.get("date_tok")].append(m)
    for dt, ms in by_date.items():
        for m in ms[:6]:
            sample[m["conditionId"]] = m
    return list(sample.values())


def fetch_trades(cid):
    rows = []
    off = 0
    while True:
        d = get_json(f"{DATA}/trades", params={"market": cid, "limit": 500, "offset": off})
        if not isinstance(d, list) or not d:
            break
        rows.extend(d)
        if len(d) < 500:
            break
        off += 500
        if off > 30000:
            break
    return rows


def main():
    markets = load(os.path.join(DATA_DIR, "markets_closed.json"), [])
    print(f"loaded {len(markets)} closed markets", flush=True)
    cid2meta = {m["conditionId"]: m for m in markets if m.get("conditionId")}
    sample = pick_sample(markets)
    print(f"discovery sample: {len(sample)} markets", flush=True)

    results = map_concurrent(lambda m: fetch_trades(m["conditionId"]), sample,
                             workers=6, label="trades")

    wallets = {}
    tapes = {}  # cid -> compact tape [(ts, price, outcomeIndex, size)] for Q2 reuse
    for m, trades in results:
        if not trades:
            continue
        cid = m["conditionId"]
        tapes[cid] = [
            [int(t.get("timestamp") or 0), float(t.get("price") or 0),
             int(t.get("outcomeIndex") or 0), float(t.get("size") or 0)]
            for t in trades
        ]
        city = m.get("city") or ""
        ed = (city, m.get("date_tok") or "")
        for t in trades:
            w = (t.get("proxyWallet") or "").lower()
            if not w:
                continue
            rec = wallets.setdefault(w, {
                "name": t.get("name") or "", "markets": set(), "event_days": set(),
                "n_trades": 0, "usdc_vol": 0.0,
            })
            rec["markets"].add(cid)
            rec["event_days"].add(ed)
            rec["n_trades"] += 1
            try:
                rec["usdc_vol"] += float(t.get("size") or 0) * float(t.get("price") or 0)
            except Exception:
                pass

    # serialize sets to counts
    out = {}
    for w, rec in wallets.items():
        out[w] = {
            "name": rec["name"],
            "n_markets": len(rec["markets"]),
            "n_event_days": len(rec["event_days"]),
            "n_trades": rec["n_trades"],
            "usdc_vol": round(rec["usdc_vol"], 2),
        }
    save(os.path.join(DATA_DIR, "wallet_activity.json"), out)
    save(os.path.join(DATA_DIR, "tapes.json"), tapes)
    # distribution summary
    n = len(out)
    for bar in (3, 5, 10, 20, 50):
        c = sum(1 for r in out.values() if r["n_markets"] >= bar)
        print(f"  wallets in >= {bar} discovery markets: {c}", flush=True)
    print(f"  total distinct wallets discovered: {n}", flush=True)


if __name__ == "__main__":
    main()
