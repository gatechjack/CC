"""Stage 6 (decisive copyability test): out-of-sample (split-half) persistence.

The 71% net-positive rate among top-active wallets is survivorship-contaminated
(losing active traders blow up and leave the active set). The copy-bot's core
assumption is that a PAST winner keeps winning. Test it directly:

For each top wallet, pull full /closed-positions (settled, with resolution
timestamp + realizedPnl + totalBought), split the wallet's weather positions
at its time-median into EARLY and LATE halves, and ask:
  - does EARLY hold-to-settlement ROI predict LATE ROI? (correlation)
  - among wallets net-positive EARLY, what is mean LATE ROI + % still positive?

If early winning predicts late winning -> persistent skill (copyable).
If late ROI regresses to ~0 regardless of early -> variance/survivorship
(NOT copyable). Hold-to-settlement ROI is exactly what a copy-bot would earn.

Writes data/persistence.json.
"""
from __future__ import annotations
import sys, os, statistics
sys.path.insert(0, os.path.dirname(__file__))
from _pmwx import DATA, get_json, map_concurrent, load, save

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TOP_N = int(os.environ.get("TOP_N", "400"))


def is_weather(slug):
    s = (slug or "").lower()
    return "temperature" in s or s.startswith("highest-temperature")


def fetch_closed_detail(wallet):
    rows, off = [], 0
    while True:
        try:
            d = get_json(f"{DATA}/closed-positions",
                         params={"user": wallet, "limit": 50, "offset": off},
                         max_retries=4)
        except Exception:
            break
        if not isinstance(d, list) or not d:
            break
        rows.extend(d)
        if len(d) < 50:
            break
        off += 50
        if off > 3000:
            break
    out = []
    for p in rows:
        if not is_weather(p.get("slug")):
            continue
        out.append((int(p.get("timestamp") or 0),
                    float(p.get("realizedPnl") or 0.0),
                    float(p.get("totalBought") or 0.0),
                    float(p.get("curPrice") or 0.0)))
    return out


def roi(positions):
    inv = sum(b for _, _, b, _ in positions)
    pnl = sum(p for _, p, _, _ in positions)
    return (pnl / inv if inv > 0 else None), pnl, inv


def main():
    pnl = load(os.path.join(DATA_DIR, "wallet_pnl.json"), [])
    elig = [r for r in pnl if r["n_weather_positions"] >= 30 and r["n_event_days"] >= 15]
    elig.sort(key=lambda r: r["total_realized_pnl"], reverse=True)
    top = elig[:TOP_N]
    print(f"split-half persistence on top {len(top)} eligible wallets", flush=True)

    res = map_concurrent(lambda r: fetch_closed_detail(r["wallet"]), top,
                         workers=6, label="closed-detail")

    rows = []
    for r, positions in res:
        if not positions or len(positions) < 20:
            continue
        positions = [p for p in positions if p[0] > 0]
        positions.sort(key=lambda x: x[0])
        mid = len(positions) // 2
        early, late = positions[:mid], positions[mid:]
        e_roi, e_pnl, e_inv = roi(early)
        l_roi, l_pnl, l_inv = roi(late)
        if e_roi is None or l_roi is None:
            continue
        rows.append({"wallet": r["wallet"], "name": r.get("name", ""),
                     "n": len(positions),
                     "early_roi": e_roi, "late_roi": l_roi,
                     "early_pnl": e_pnl, "late_pnl": l_pnl})
    save(os.path.join(DATA_DIR, "persistence.json"), rows)

    e = [x["early_roi"] for x in rows]
    l = [x["late_roi"] for x in rows]
    n = len(rows)
    print(f"\nwallets with valid split: {n}", flush=True)
    # correlation
    if n > 2:
        me, ml = statistics.mean(e), statistics.mean(l)
        cov = sum((x["early_roi"] - me) * (x["late_roi"] - ml) for x in rows) / n
        se, sl = statistics.pstdev(e), statistics.pstdev(l)
        pear = cov / (se * sl) if se > 0 and sl > 0 else None
        print(f"  mean early_roi={me:.3f}  mean late_roi={ml:.3f}", flush=True)
        print(f"  Pearson corr(early_roi, late_roi) = {pear:.3f}" if pear is not None else "  corr undefined", flush=True)
    # conditioning: among early-positive, late behavior
    for lbl, pred in [("early ROI > 0", lambda x: x["early_roi"] > 0),
                      ("early ROI > +10%", lambda x: x["early_roi"] > 0.10),
                      ("early ROI top quartile", None)]:
        if pred is None:
            thr = sorted(e)[int(0.75 * n)]
            sub = [x for x in rows if x["early_roi"] >= thr]
        else:
            sub = [x for x in rows if pred(x)]
        if not sub:
            continue
        lr = [x["late_roi"] for x in sub]
        frac_pos = 100 * sum(1 for x in sub if x["late_roi"] > 0) / len(sub)
        print(f"  {lbl}: n={len(sub)}  mean late_roi={statistics.mean(lr):.3f}  "
              f"median late_roi={statistics.median(lr):.3f}  %late-positive={frac_pos:.0f}%", flush=True)
    # late-positive base rate for reference
    print(f"  [reference] overall %late-positive = {100*sum(1 for x in rows if x['late_roi']>0)/n:.0f}%", flush=True)


if __name__ == "__main__":
    main()
