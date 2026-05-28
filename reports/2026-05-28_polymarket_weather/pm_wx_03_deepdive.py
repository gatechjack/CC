"""Stage 3: per-wallet weather realized-P&L deep-dive (settled-only).

Takes the top discovery candidates and pulls each wallet's FULL
/closed-positions, filters to weather (temperature/weather slugs), and
computes authoritative settled-only realized P&L + persistence metrics.

`realizedPnl` is the API's own per-resolved-position realized number (handles
round-trips + settlement). `curPrice>=0.9` => the held side won. This is the
clean settled-only P&L the brief requires — no open-position mark-to-market.

Writes data/wallet_pnl.json.
"""
from __future__ import annotations
import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))
from _pmwx import DATA, get_json, map_concurrent, load, save

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CANDIDATE_BAR = int(os.environ.get("CAND_BAR", "3"))   # min discovery markets
MAX_CANDIDATES = int(os.environ.get("MAX_CAND", "900"))

US_CITIES = {
    "nyc", "dallas", "atlanta", "miami", "chicago", "austin", "denver",
    "houston", "los-angeles", "san-francisco", "seattle",
}


def is_weather(slug: str) -> bool:
    s = (slug or "").lower()
    return "temperature" in s or s.startswith("highest-temperature") or "-weather-" in s


def parse_city_date(slug: str):
    m = re.match(r"highest-temperature-in-(.+?)-on-([a-z]+-\d+(?:-\d{4})?)", slug or "")
    if m:
        return m.group(1), m.group(2)
    return "", ""


def fetch_closed(wallet):
    rows = []
    off = 0
    while True:
        d = get_json(f"{DATA}/closed-positions",
                     params={"user": wallet, "limit": 50, "offset": off})
        if not isinstance(d, list) or not d:
            break
        rows.extend(d)
        if len(d) < 50:
            break
        off += 50
        if off > 3000:
            break
    return rows


def analyze(wallet, rows):
    wx = [p for p in rows if is_weather(p.get("slug"))]
    if not wx:
        return None
    pnl = 0.0
    invested = 0.0
    wins = 0
    event_days = set()
    us_pnl = us_n = intl_pnl = intl_n = 0
    us_pnl = 0.0; intl_pnl = 0.0
    entry_prices = []
    for p in wx:
        rp = float(p.get("realizedPnl") or 0.0)
        tb = float(p.get("totalBought") or 0.0)
        ap = float(p.get("avgPrice") or 0.0)
        cp = float(p.get("curPrice") or 0.0)
        pnl += rp
        invested += tb
        if cp >= 0.9:
            wins += 1
        if ap > 0:
            entry_prices.append(ap)
        city, dt = parse_city_date(p.get("slug"))
        if city:
            event_days.add((city, dt))
            if city in US_CITIES:
                us_n += 1; us_pnl += rp
            else:
                intl_n += 1; intl_pnl += rp
    n = len(wx)
    return {
        "wallet": wallet,
        "n_weather_positions": n,
        "n_event_days": len(event_days),
        "total_realized_pnl": round(pnl, 2),
        "total_invested": round(invested, 2),
        "roi_pct": round(100.0 * pnl / invested, 2) if invested > 0 else None,
        "win_rate_pct": round(100.0 * wins / n, 1) if n else None,
        "us_n": us_n, "us_pnl": round(us_pnl, 2),
        "intl_n": intl_n, "intl_pnl": round(intl_pnl, 2),
        "avg_entry_price": round(sum(entry_prices) / len(entry_prices), 3) if entry_prices else None,
        "n_total_closed": len(rows),
    }


def main():
    act = load(os.path.join(DATA_DIR, "wallet_activity.json"), {})
    cands = [w for w, r in act.items() if r["n_markets"] >= CANDIDATE_BAR]
    cands.sort(key=lambda w: act[w]["n_markets"], reverse=True)
    cands = cands[:MAX_CANDIDATES]
    print(f"deep-diving {len(cands)} candidate wallets (bar>={CANDIDATE_BAR} markets)", flush=True)

    results = map_concurrent(lambda w: fetch_closed(w), cands, workers=6, label="closed-pos")

    out = []
    for w, rows in results:
        if not rows:
            continue
        rec = analyze(w, rows)
        if rec:
            rec["name"] = act.get(w, {}).get("name", "")
            out.append(rec)
    out.sort(key=lambda r: r["total_realized_pnl"], reverse=True)
    save(os.path.join(DATA_DIR, "wallet_pnl.json"), out)

    # persistence-bar summary
    for npos, ndays in [(30, 0), (30, 15), (50, 20)]:
        elig = [r for r in out if r["n_weather_positions"] >= npos and r["n_event_days"] >= ndays]
        winners = [r for r in elig if r["total_realized_pnl"] > 0]
        print(f"  bar npos>={npos},days>={ndays}: {len(elig)} eligible, "
              f"{len(winners)} net-positive", flush=True)
    print("  TOP 15 by realized PnL:", flush=True)
    for r in out[:15]:
        print(f"    {r['wallet'][:12]} {r.get('name','')[:16]:16} "
              f"pnl=${r['total_realized_pnl']:>10,.0f} n={r['n_weather_positions']:>4} "
              f"days={r['n_event_days']:>3} roi={r['roi_pct']}% wr={r['win_rate_pct']}% "
              f"US/intl={r['us_n']}/{r['intl_n']}", flush=True)


if __name__ == "__main__":
    main()
