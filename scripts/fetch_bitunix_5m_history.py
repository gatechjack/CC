"""Paginated pull of historical 5m Bitunix futures bars.

Used by the v1.1 hostile-regime backtest (Bybit DB dataset) to source
Factor 3 (volatility) inputs. Bybit is geo-blocked from this network,
so Bitunix native REST is the closest-to-prod 5m source available.

Bitunix kline endpoint:
- /api/v1/futures/market/kline?symbol=BTCUSDT&interval=5m&startTime=ms&endTime=ms&limit=200
- Returns up to 200 bars, NEWEST FIRST
- No auth required

Pagination: walk endTime backwards from window-end to window-start. Each
call's oldest bar becomes the next call's endTime - 1ms.

Output: `data/historical_alerts/cache_ohlcv_bitunix_5m_<start>_<end>.json`
Same shape as the existing Coinbase OHLCV caches (list of dicts with
ts, open, high, low, close, volume).
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "historical_alerts"


def fetch_window(
    start_ms: int, end_ms: int,
    symbol: str = "BTCUSDT", interval: str = "5m",
    page_sleep_s: float = 0.25,
) -> list[dict]:
    """Paginate Bitunix 5m bars covering [start_ms, end_ms]."""
    bars_by_ts: dict[int, dict] = {}
    cur_end = end_ms
    page = 0
    with httpx.Client(base_url="https://fapi.bitunix.com", timeout=15.0) as cl:
        while cur_end > start_ms:
            r = cl.get(
                "/api/v1/futures/market/kline",
                params={
                    "symbol": symbol, "interval": interval,
                    "startTime": start_ms, "endTime": cur_end,
                    "limit": 200,
                },
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != 0:
                raise RuntimeError(
                    f"Bitunix code={d.get('code')} msg={d.get('msg')!r}"
                )
            rows = d.get("data") or []
            if not rows:
                break
            for row in rows:
                ts = int(row["time"])
                if ts < start_ms or ts > end_ms:
                    continue
                if ts in bars_by_ts:
                    continue
                bars_by_ts[ts] = {
                    "ts": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("baseVol", row.get("quoteVol", 0)) or 0),
                }
            oldest_ts = min(int(r["time"]) for r in rows)
            if oldest_ts >= cur_end:
                break    # not making progress
            cur_end = oldest_ts - 1
            page += 1
            if page % 10 == 0:
                print(f"  page {page}: oldest so far = "
                      f"{datetime.fromtimestamp(oldest_ts/1000, tz=timezone.utc)}"
                      f"  total bars = {len(bars_by_ts)}")
            time.sleep(page_sleep_s)
    bars = sorted(bars_by_ts.values(), key=lambda b: b["ts"])
    return bars


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True, help="UTC start YYYY-MM-DD")
    p.add_argument("--end", required=True, help="UTC end YYYY-MM-DD")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="5m")
    p.add_argument("--out", default=None)
    args = p.parse_args()
    start_dt = datetime.fromisoformat(args.start + "T00:00:00+00:00")
    end_dt = datetime.fromisoformat(args.end + "T00:00:00+00:00")
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    if args.out is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out_path = (
            CACHE_DIR / f"cache_ohlcv_bitunix_{args.interval}_"
            f"{start_dt.date().isoformat().replace('-','')}_"
            f"{end_dt.date().isoformat().replace('-','')}.json"
        )
    else:
        out_path = Path(args.out)
    print(f"Fetching Bitunix {args.symbol} {args.interval} {args.start} -> {args.end}")
    bars = fetch_window(start_ms, end_ms, symbol=args.symbol, interval=args.interval)
    print(f"Got {len(bars)} bars; writing {out_path}")
    out_path.write_text(json.dumps(bars), encoding="utf-8")


if __name__ == "__main__":
    main()
