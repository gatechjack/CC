"""Feasibility probe for the kalshi_weather REAL-PRICE gate.

Confirms (before any large pull): can we get historical settled weather-market
prices at a day-before decision time, and how far back does coverage go?

SECRETS: loads KALSHI creds from .env into memory and uses them; NEVER prints any
credential value. Output contains only market data (counts, dates, prices).
"""
from __future__ import annotations

import os
import stat
import tempfile
import time
from datetime import datetime, timezone

import pykalshi


def load_kalshi_client():
    """Parse .env for KALSHI creds (dotenv not installed), materialize PEM to a
    chmod-600 tempfile, return (client, pem_path). Never prints secret values."""
    import re
    raw = open(".env", "r", encoding="utf-8").read()

    def get(key):
        # anchor to a real line-start assignment, skipping commented examples (# ...)
        m = re.search(r"(?m)^" + re.escape(key) + r"=", raw)
        if not m:
            return None
        after = raw[m.end():]
        if after.startswith('"'):
            end = after.find('"', 1)
            return after[1:end]
        return after.split("\n", 1)[0].strip().strip("'")

    api_key_id = get("KALSHI_API_KEY_ID")
    pem = get("KALSHI_PRIVATE_KEY_PEM")
    if not api_key_id or not pem:
        raise SystemExit("KALSHI creds not found in .env")
    if "\\n" in pem and "\n" not in pem:
        pem = pem.replace("\\n", "\n")
    if not pem.endswith("\n"):
        pem += "\n"
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w") as f:
        f.write(pem)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    client = pykalshi.KalshiClient(api_key_id=api_key_id, private_key_path=path)
    return client, path


def main():
    client, pem_path = load_kalshi_client()
    try:
        # 1) auth sanity via a tiny public call
        ex = client.exchange.get_status() if hasattr(client, "exchange") else None
        print("[auth] client constructed; exchange status:", ex)

        # 2) settled markets for one weather series
        series = "KXHIGHNY"
        print(f"\n[markets] fetching settled markets for {series} (fetch_all) ...")
        mk = client.get_markets(status=pykalshi.MarketStatus.SETTLED,
                                series_ticker=series, fetch_all=True)
        rows = list(mk)
        print(f"[markets] {series}: {len(rows)} settled markets")
        # date coverage via close_time
        def ct(m):
            v = getattr(m, "close_time", None) or getattr(m, "close_ts", None)
            return v
        sample = rows[0]
        print("[market.fields]", [a for a in dir(sample) if not a.startswith("_")][:40])
        # find min/max close
        closes = []
        for m in rows:
            v = getattr(m, "close_time", None)
            if v is not None:
                closes.append(v)
        if closes:
            closes_sorted = sorted(str(c) for c in closes)
            print(f"[coverage] earliest close: {closes_sorted[0]}  latest: {closes_sorted[-1]}")

        # 3) one settled market: structure + settlement + candlesticks
        m0 = rows[len(rows) // 2]  # a mid-history one
        tk = getattr(m0, "ticker", None)
        print(f"\n[one-market] ticker={tk}")
        for fld in ("ticker", "result", "close_time", "open_time", "floor_strike",
                    "cap_strike", "yes_sub_title", "last_price", "settlement_value"):
            print(f"    {fld} = {getattr(m0, fld, '<none>')!r}")

        # candlesticks over the 48h before close
        close = getattr(m0, "close_time", None)
        if isinstance(close, str):
            close_dt = datetime.fromisoformat(close.replace("Z", "+00:00"))
        elif isinstance(close, (int, float)):
            close_dt = datetime.fromtimestamp(close, tz=timezone.utc)
        else:
            close_dt = close
        end_ts = int(close_dt.timestamp())
        start_ts = end_ts - 48 * 3600
        print(f"\n[candles] {tk} window {start_ts}..{end_ts} (ONE_HOUR)")
        cs = client.get_candlesticks_batch([tk], start_ts, end_ts,
                                           period=pykalshi.CandlestickPeriod.ONE_HOUR)
        resp = cs.get(tk)
        print("[candles] response type:", type(resp).__name__)
        items = getattr(resp, "candlesticks", None) or getattr(resp, "candles", None) or resp
        try:
            n = len(items)
        except Exception:
            n = "?"
        print("[candles] n =", n)
        if items:
            c0 = items[0]
            print("[candle.fields]", [a for a in dir(c0) if not a.startswith("_")][:40])
            for c in list(items)[:3] + list(items)[-2:]:
                ts = getattr(c, "end_period_ts", None) or getattr(c, "ts", None) or getattr(c, "timestamp", None)
                print("    ", ts, "->", {k: getattr(c, k, None) for k in
                      ("yes_bid", "yes_ask", "price", "open", "close", "mean", "volume")})
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            os.remove(pem_path)
        except Exception:
            pass


if __name__ == "__main__":
    main()
