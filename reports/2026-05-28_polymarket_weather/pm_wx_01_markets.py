"""Stage 1: pull the full Daily-Temperature market universe (settled + open).

tag_id=103040 (daily-temperature). Captures settlement + Q2 efficiency fields.
Writes data/markets_closed.json and data/markets_open.json.
"""
from __future__ import annotations
import json, re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import datetime
from _pmwx import GAMMA, get_json, fetch_all, save

TAG = 103040
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

US_CITIES = {
    "nyc", "dallas", "atlanta", "miami", "chicago", "austin", "denver",
    "houston", "los-angeles", "san-francisco", "seattle",
}

# Celsius-quoted (international) vs Fahrenheit (US) is inferable from threshold
# label suffix ("19c" vs "75f"); but city is the cleaner signal. We tag both.


def parse_slug(slug: str):
    """-> (city, date_token, threshold_label). date like 'april-15-2026'."""
    m = re.match(r"highest-temperature-in-(.+?)-on-([a-z]+-\d+(?:-\d{4})?)-(.+)$", slug or "")
    if not m:
        m2 = re.match(r"highest-temperature-in-(.+?)-on-([a-z]+-\d+(?:-\d{4})?)$", slug or "")
        if m2:
            return m2.group(1), m2.group(2), ""
        return "", "", ""
    return m.group(1), m.group(2), m.group(3)


def jload(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def curate(m: dict) -> dict:
    slug = m.get("slug") or ""
    city, date_tok, thr = parse_slug(slug)
    prices = jload(m.get("outcomePrices")) or []
    outcomes = jload(m.get("outcomes")) or []
    tokens = jload(m.get("clobTokenIds")) or []
    try:
        prices_f = [float(p) for p in prices]
    except Exception:
        prices_f = []
    win_idx = None
    for i, p in enumerate(prices_f):
        if p >= 0.9:
            win_idx = i
            break
    return {
        "conditionId": m.get("conditionId"),
        "slug": slug,
        "eventSlug": (m.get("events") or [{}])[0].get("slug") if m.get("events") else m.get("eventSlug"),
        "question": m.get("question"),
        "city": city,
        "date_tok": date_tok,
        "threshold": thr,
        "is_us": city in US_CITIES,
        "volumeNum": float(m.get("volumeNum") or 0.0),
        "liquidityNum": float(m.get("liquidityNum") or m.get("liquidity") or 0.0) if (m.get("liquidityNum") or m.get("liquidity")) else 0.0,
        "startDate": m.get("startDate"),
        "endDate": m.get("endDate"),
        "createdAt": m.get("createdAt"),
        "closedTime": m.get("closedTime"),
        "closed": bool(m.get("closed")),
        "outcomes": outcomes,
        "outcomePrices": prices_f,
        "win_idx": win_idx,
        "clobTokenIds": tokens,
        "spread": m.get("spread"),
        "bestBid": m.get("bestBid"),
        "bestAsk": m.get("bestAsk"),
        "lastTradePrice": m.get("lastTradePrice"),
    }


def pull_window(dmin, dmax):
    """Offset-paginate one end-date window (stays under gamma's 10100 cap)."""
    rows = []
    off = 0
    while True:
        r = get_json(f"{GAMMA}/markets", params={
            "tag_id": TAG, "closed": "true",
            "end_date_min": dmin, "end_date_max": dmax,
            "limit": 100, "offset": off})
        if not r:
            break
        rows.extend(r)
        if len(r) < 100:
            break
        off += 100
        if off > 10000:
            print(f"  WARN window {dmin[:10]} hit offset cap", flush=True)
            break
    return rows


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    # ---- settled: partition by weekly end-date windows (offset cap workaround)
    print(f"=== pulling SETTLED markets tag={TAG} by weekly windows ===", flush=True)
    start = datetime.date(2025, 11, 1)
    end = datetime.date(2026, 6, 8)
    seen = {}
    d = start
    while d < end:
        nd = d + datetime.timedelta(days=7)
        dmin = d.isoformat() + "T00:00:00Z"
        dmax = nd.isoformat() + "T00:00:00Z"
        wr = pull_window(dmin, dmax)
        for m in wr:
            cid = m.get("conditionId")
            if cid:
                seen[cid] = m
        if wr:
            print(f"  {d.isoformat()}: +{len(wr)} (total {len(seen)})", flush=True)
        d = nd
    cur = [curate(m) for m in seen.values()]
    save(os.path.join(DATA_DIR, "markets_closed.json"), cur)
    n_settle = sum(1 for c in cur if c["win_idx"] is not None)
    cities = len({c["city"] for c in cur if c["city"]})
    vol = sum(c["volumeNum"] for c in cur)
    print(f"  markets_closed.json: {len(cur)} markets, {n_settle} settled, "
          f"{cities} cities, ${vol:,.0f} volume", flush=True)

    # ---- open: small enough for a single offset pull
    print(f"=== pulling OPEN markets tag={TAG} ===", flush=True)
    rows = fetch_all(f"{GAMMA}/markets",
                     params={"tag_id": TAG, "closed": "false"},
                     page=100, max_pages=120)
    curo = [curate(m) for m in rows]
    save(os.path.join(DATA_DIR, "markets_open.json"), curo)
    print(f"  markets_open.json: {len(curo)} markets, "
          f"{len({c['city'] for c in curo if c['city']})} cities", flush=True)


if __name__ == "__main__":
    main()
