"""Stage 4 (Q1): characterize HOW the top persistent winners trade.

For each top net-positive persistent wallet, pull full /activity, filter to
weather TRADE rows, join to market endDates, and derive signals that separate
a DIRECTIONAL FORECAST edge from MARKET-MAKING / spread-capture / arb:

  - buy/sell mix + round-trip rate (both BUY and SELL on same market before
    settlement) + both-sides rate (traded outcome_index 0 AND 1 on a market)
        -> high  => market-making / liquidity provision / scalping
        -> low (mostly BUY, hold to settlement) => directional
  - entry lead time (hours before endDate) — forecast edge needs lead time
  - entry price distribution — favorite-harvesting (>0.9) vs genuine uncertainty
  - US vs intl concentration, sizing, fillable-size reality

Writes data/winner_characterization.json + prints a table.
"""
from __future__ import annotations
import sys, os, datetime, statistics, collections
sys.path.insert(0, os.path.dirname(__file__))
from _pmwx import DATA, get_json, map_concurrent, load, save

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
NPOS_BAR = int(os.environ.get("NPOS_BAR", "30"))
DAYS_BAR = int(os.environ.get("DAYS_BAR", "15"))
TOP_N = int(os.environ.get("TOP_N", "20"))


def to_unix(iso):
    try:
        return int(datetime.datetime.fromisoformat(
            (iso or "").replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def fetch_activity(wallet, max_rows=4000):
    """Most-recent activity. /activity offset-caps low (~1000-5000) and 400s
    past it, so we stop gracefully on error — a recent sample is enough to
    characterize trading STYLE (sell mix, round-trips, lead time, entry px)."""
    rows, off = [], 0
    while len(rows) < max_rows:
        try:
            d = get_json(f"{DATA}/activity",
                         params={"user": wallet, "limit": 500, "offset": off},
                         max_retries=3)
        except Exception:
            break  # offset cap reached
        if not isinstance(d, list) or not d:
            break
        rows.extend(d)
        if len(d) < 500:
            break
        off += 500
    return rows


def is_weather(slug, title=""):
    s = (slug or "").lower()
    return "temperature" in s or s.startswith("highest-temperature") or "highest-temperature" in (title or "").lower()


def characterize(wallet, rows, cid_end):
    wx = [r for r in rows if (r.get("type") or "").upper() == "TRADE"
          and is_weather(r.get("slug"), r.get("title"))]
    if not wx:
        return None
    n = len(wx)
    n_buy = sum(1 for r in wx if (r.get("side") or "").upper() == "BUY")
    n_sell = n - n_buy
    # per-market aggregation for round-trip / both-sides
    per_mkt = collections.defaultdict(lambda: {"sides": set(), "outs": set()})
    leads, prices, sizes, usdc = [], [], [], []
    us_n = intl_n = 0
    US = {"nyc", "dallas", "atlanta", "miami", "chicago", "austin", "denver",
          "houston", "los-angeles", "san-francisco", "seattle"}
    for r in wx:
        cid = r.get("conditionId")
        per_mkt[cid]["sides"].add((r.get("side") or "").upper())
        per_mkt[cid]["outs"].add(r.get("outcomeIndex"))
        prices.append(float(r.get("price") or 0))
        sizes.append(float(r.get("size") or 0))
        usdc.append(float(r.get("usdcSize") or 0))
        slug = (r.get("slug") or "")
        city = ""
        if "highest-temperature-in-" in slug:
            city = slug.split("highest-temperature-in-")[1].split("-on-")[0]
        if city in US:
            us_n += 1
        elif city:
            intl_n += 1
        end_ts = cid_end.get(cid)
        ts = int(r.get("timestamp") or 0)
        if end_ts and ts and (r.get("side") or "").upper() == "BUY":
            leads.append((end_ts - ts) / 3600.0)
    n_mkts = len(per_mkt)
    roundtrip = sum(1 for v in per_mkt.values() if {"BUY", "SELL"} <= v["sides"])
    bothsides = sum(1 for v in per_mkt.values() if len(v["outs"]) >= 2)
    return {
        "wallet": wallet,
        "n_weather_trades": n,
        "n_markets": n_mkts,
        "sell_frac": round(n_sell / n, 3),
        "roundtrip_market_frac": round(roundtrip / n_mkts, 3) if n_mkts else 0,
        "bothsides_market_frac": round(bothsides / n_mkts, 3) if n_mkts else 0,
        "median_entry_lead_h": round(statistics.median(leads), 1) if leads else None,
        "pct_entries_gt_24h": round(100 * sum(1 for x in leads if x > 24) / len(leads), 1) if leads else None,
        "median_entry_price": round(statistics.median([p for p in prices if p > 0]), 3) if any(prices) else None,
        "pct_entries_price_gt_0_9": round(100 * sum(1 for p in prices if p > 0.9) / len(prices), 1) if prices else None,
        "median_usdc_size": round(statistics.median([u for u in usdc if u > 0]), 1) if any(usdc) else None,
        "max_usdc_size": round(max(usdc), 1) if usdc else None,
        "us_trades": us_n, "intl_trades": intl_n,
    }


def classify(c):
    """Heuristic label from the signals."""
    if c["sell_frac"] >= 0.3 or c["roundtrip_market_frac"] >= 0.3 or c["bothsides_market_frac"] >= 0.3:
        return "market-making/scalping (sells, round-trips, or both-sides)"
    if (c.get("pct_entries_price_gt_0_9") or 0) >= 60:
        return "favorite-harvesting (buys near-certain >0.9)"
    if (c.get("pct_entries_gt_24h") or 0) >= 40:
        return "directional-forecast (buy+hold, lead time)"
    return "buy-and-hold (short lead; directional or late-certainty)"


def main():
    pnl = load(os.path.join(DATA_DIR, "wallet_pnl.json"), [])
    markets = load(os.path.join(DATA_DIR, "markets_closed.json"), [])
    cid_end = {m["conditionId"]: to_unix(m.get("endDate"))
               for m in markets if m.get("conditionId")}
    winners = [r for r in pnl
               if r["n_weather_positions"] >= NPOS_BAR
               and r["n_event_days"] >= DAYS_BAR
               and r["total_realized_pnl"] > 0]
    winners = winners[:TOP_N]
    print(f"characterizing top {len(winners)} persistent winners "
          f"(npos>={NPOS_BAR}, days>={DAYS_BAR})", flush=True)

    res = map_concurrent(lambda w: fetch_activity(w["wallet"]), winners,
                         workers=5, label="activity")
    act_by_wallet = {w["wallet"]: rows for w, rows in res}

    out = []
    for w in winners:
        rows = act_by_wallet.get(w["wallet"]) or []
        c = characterize(w["wallet"], rows, cid_end)
        if not c:
            continue
        c["name"] = w.get("name", "")
        c["total_realized_pnl"] = w["total_realized_pnl"]
        c["n_settled_positions"] = w["n_weather_positions"]
        c["n_event_days"] = w["n_event_days"]
        c["roi_pct"] = w.get("roi_pct")
        c["win_rate_pct"] = w.get("win_rate_pct")
        c["label"] = classify(c)
        out.append(c)
    save(os.path.join(DATA_DIR, "winner_characterization.json"), out)

    print("\n=== TOP PERSISTENT WINNER CHARACTERIZATION ===", flush=True)
    for c in out:
        print(f"\n  {c['name'][:18] or c['wallet'][:12]} | PnL ${c['total_realized_pnl']:,.0f} | "
              f"{c['n_settled_positions']} pos / {c['n_event_days']} days | "
              f"ROI {c['roi_pct']}% | WR {c['win_rate_pct']}%", flush=True)
        print(f"     sell_frac={c['sell_frac']} roundtrip={c['roundtrip_market_frac']} "
              f"bothsides={c['bothsides_market_frac']} | lead_med={c['median_entry_lead_h']}h "
              f">24h={c['pct_entries_gt_24h']}% | entry_px_med={c['median_entry_price']} "
              f">0.9={c['pct_entries_price_gt_0_9']}%", flush=True)
        print(f"     size_med=${c['median_usdc_size']} max=${c['max_usdc_size']} | "
              f"US/intl trades={c['us_trades']}/{c['intl_trades']} | => {c['label']}", flush=True)


if __name__ == "__main__":
    main()
