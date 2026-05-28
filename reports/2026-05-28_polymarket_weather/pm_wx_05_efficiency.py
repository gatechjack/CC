"""Stage 5 (Q2): Polymarket weather price-efficiency vs the Kalshi benchmark.

Kalshi benchmark (prior study, real-price holdout):
  market Brier = 0.1607 on n=7,403 interior markets; best model 0.1780.

Apples-to-apples metric here: market-implied Brier of Polymarket weather
markets — P(YES) from the last trade at/before a horizon vs realized outcome.
Primary horizon = "evening before" (endDate - 18h) to match Kalshi's leak-safe
snapshot. Also 24h/6h/1h/last for the convergence curve. Split US vs intl.

City-stratified sample (balanced across the 50 cities, spanning volume range)
so the US-vs-intl efficiency test isn't volume-biased. Reuses discovery tapes
where available; pulls the rest. Spread from live open-market snapshots.
"""
from __future__ import annotations
import sys, os, datetime, statistics, collections, random
sys.path.insert(0, os.path.dirname(__file__))
from _pmwx import DATA, get_json, map_concurrent, load, save

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PER_CITY = int(os.environ.get("PER_CITY", "40"))   # settled markets per city
random.seed(7)


def to_unix(iso):
    try:
        return int(datetime.datetime.fromisoformat(
            (iso or "").replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def fetch_trades(cid):
    rows, off = [], 0
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
    return [[int(t.get("timestamp") or 0), float(t.get("price") or 0),
             int(t.get("outcomeIndex") or 0), float(t.get("size") or 0)] for t in rows]


def yes_price_at(tape, horizon_ts):
    """Last YES-implied price (oi0=price, oi1=1-price) at/before horizon_ts."""
    best = None
    for ts, price, oi, size in tape:
        if ts <= horizon_ts and price > 0:
            if best is None or ts > best[0]:
                yp = price if oi == 0 else (1.0 - price)
                best = (ts, yp)
    return best[1] if best else None


def brier(pairs):
    if not pairs:
        return None, 0
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs), len(pairs)


def main():
    markets = load(os.path.join(DATA_DIR, "markets_closed.json"), [])
    tapes = load(os.path.join(DATA_DIR, "tapes.json"), {}) or {}
    settled = [m for m in markets if m.get("win_idx") is not None and m.get("city")
               and m.get("endDate") and len(m.get("outcomes") or []) == 2]
    print(f"settled binary markets: {len(settled)}; reusable tapes: {len(tapes)}", flush=True)

    # city-stratified sample
    by_city = collections.defaultdict(list)
    for m in settled:
        by_city[m["city"]].append(m)
    sample = []
    for city, ms in by_city.items():
        ms_sorted = sorted(ms, key=lambda x: x.get("volumeNum") or 0, reverse=True)
        # take a spread across the volume range, not just the top
        if len(ms_sorted) <= PER_CITY:
            sample.extend(ms_sorted)
        else:
            step = len(ms_sorted) / PER_CITY
            sample.extend([ms_sorted[int(i * step)] for i in range(PER_CITY)])
    print(f"city-stratified sample: {len(sample)} markets across {len(by_city)} cities", flush=True)

    # need tape for each; reuse discovery tapes, pull the rest
    need = [m for m in sample if m["conditionId"] not in tapes]
    print(f"pulling tape for {len(need)} markets not in discovery cache", flush=True)
    pulled = map_concurrent(lambda m: fetch_trades(m["conditionId"]), need,
                            workers=6, label="eff-tape")
    for m, tp in pulled:
        if tp:
            tapes[m["conditionId"]] = tp
    save(os.path.join(DATA_DIR, "tapes.json"), tapes)  # merged cache for reuse

    horizons = {"24h": 24, "18h_eve": 18, "6h": 6, "1h": 1, "last": -1}
    # collect per-market YES price at each horizon + realized
    buckets = {h: {"all": [], "us": [], "intl": [],
                   "int_all": [], "int_us": [], "int_intl": []} for h in horizons}
    detail = []  # per-market record for re-slicing without re-pulling
    for m in sample:
        cid = m["conditionId"]
        tape = tapes.get(cid)
        if not tape:
            continue
        end_ts = to_unix(m["endDate"])
        if not end_ts:
            continue
        realized_yes = 1.0 if m["win_idx"] == 0 else 0.0
        is_us = m["is_us"]
        rec = {"cid": cid, "city": m["city"], "is_us": is_us,
               "vol": m.get("volumeNum") or 0, "realized_yes": realized_yes}
        for h, hrs in horizons.items():
            if hrs < 0:
                p = yes_price_at(tape, 10 ** 11)
            else:
                p = yes_price_at(tape, end_ts - hrs * 3600)
            if p is None:
                continue
            p = min(max(p, 0.0), 1.0)
            rec[f"p_{h}"] = round(p, 4)
            buckets[h]["all"].append((p, realized_yes))
            buckets[h]["us" if is_us else "intl"].append((p, realized_yes))
            if 0.05 < p < 0.95:
                buckets[h]["int_all"].append((p, realized_yes))
                buckets[h]["int_us" if is_us else "int_intl"].append((p, realized_yes))
        detail.append(rec)
    save(os.path.join(DATA_DIR, "efficiency_detail.json"), detail)

    print("\n=== MARKET-IMPLIED BRIER (Polymarket weather) ===", flush=True)
    print("Kalshi benchmark: market Brier 0.1607 (interior, n=7403)\n", flush=True)
    rep = {}
    for h in horizons:
        b_all, n_all = brier(buckets[h]["all"])
        b_us, n_us = brier(buckets[h]["us"])
        b_in, n_in = brier(buckets[h]["intl"])
        b_int, n_int = brier(buckets[h]["int_all"])
        b_ius, n_ius = brier(buckets[h]["int_us"])
        b_iin, n_iin = brier(buckets[h]["int_intl"])
        rep[h] = {"all": (b_all, n_all), "us": (b_us, n_us),
                  "intl": (b_in, n_in), "interior": (b_int, n_int),
                  "interior_us": (b_ius, n_ius), "interior_intl": (b_iin, n_iin)}
        def f(x):
            return f"{x:.4f}" if x is not None else "  -  "
        print(f"  {h:9} all={f(b_all)}(n={n_all})  interior={f(b_int)}(n={n_int})  "
              f"int_US={f(b_ius)}(n={n_ius})  int_INTL={f(b_iin)}(n={n_iin})", flush=True)

    # ---- spread from live open markets ----
    open_m = load(os.path.join(DATA_DIR, "markets_open.json"), [])
    sp_us, sp_intl = [], []
    for m in open_m:
        s = m.get("spread")
        try:
            s = float(s)
        except Exception:
            continue
        if s <= 0 or s > 1:
            continue
        (sp_us if m.get("is_us") else sp_intl).append(s)
    print("\n=== LIVE SPREAD (open markets, gamma 'spread' field, in $) ===", flush=True)
    for lbl, arr in [("US", sp_us), ("INTL", sp_intl)]:
        if arr:
            print(f"  {lbl}: n={len(arr)} median={statistics.median(arr):.4f} "
                  f"mean={statistics.mean(arr):.4f}", flush=True)
    print(f"  Kalshi cost-model spread assumption: 0.02-0.04 (1-2c/side)", flush=True)

    save(os.path.join(DATA_DIR, "efficiency_report.json"),
         {"brier": {h: {k: list(v) for k, v in rep[h].items()} for h in rep},
          "spread": {"us_median": statistics.median(sp_us) if sp_us else None,
                     "intl_median": statistics.median(sp_intl) if sp_intl else None,
                     "us_n": len(sp_us), "intl_n": len(sp_intl)},
          "sample_size": len(sample)})


if __name__ == "__main__":
    main()
