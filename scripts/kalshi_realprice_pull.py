"""Pull REAL historical Kalshi prices for the kalshi_weather real-price gate.

For every VERIFIED weather series (config/weather_stations.yaml `series:` block,
verified=true, non-null settles_at), pull settled markets + hourly candlesticks
in the decision window around each target date. Output one JSONL row per market
with the hourly (yes_bid, yes_ask, price) series so the join pass can align the
price to the NBM decision cycle.

SECRETS: KALSHI creds loaded from .env into memory and used; NEVER printed.
Output (tmp/kalshi_realprice_candles.jsonl) contains only market price data.
Resumable: skips series already present in the output file.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pykalshi

OUT = "tmp/kalshi_realprice_candles.jsonl"
MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def load_client():
    raw = open(".env", "r", encoding="utf-8").read()

    def get(key):
        m = re.search(r"(?m)^" + re.escape(key) + r"=", raw)
        if not m:
            return None
        after = raw[m.end():]
        if after.startswith('"'):
            return after[1:after.find('"', 1)]
        return after.split("\n", 1)[0].strip().strip("'")

    kid, pem = get("KALSHI_API_KEY_ID"), get("KALSHI_PRIVATE_KEY_PEM")
    if not kid or not pem:
        raise SystemExit("KALSHI creds missing")
    if "\\n" in pem and "\n" not in pem:
        pem = pem.replace("\\n", "\n")
    if not pem.endswith("\n"):
        pem += "\n"
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w") as f:
        f.write(pem)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return pykalshi.KalshiClient(api_key_id=kid, private_key_path=path), path


def verified_series():
    raw = open("config/weather_stations.yaml", encoding="utf-8").read()
    lines = raw[raw.find("\nseries:"):].splitlines()
    cur, d = None, {}
    for l in lines:
        m = re.match(r"^  ([A-Z0-9]+):\s*$", l)
        if m:
            cur = m.group(1); d[cur] = {}
        elif cur:
            for fld, key in (("settles_at", "icao"), ("settles_what", "what"),
                             ("verified", "verified")):
                mm = re.match(r"^    " + fld + r":\s*(\S+)", l)
                if mm:
                    d[cur][key] = mm.group(1)
    out = {}
    for k, v in d.items():
        if v.get("verified") == "true" and v.get("icao") and v.get("icao") != "null":
            what = v.get("what", "")
            kind = "daily_max" if "max" in what else ("daily_min" if "min" in what else None)
            if kind:
                out[k] = (v["icao"], kind)
    return out


def parse_date(ticker, series):
    # SERIES-YYMONDD-STRIKE  e.g. KXHIGHNY-26APR23-B72.5
    m = re.match(re.escape(series) + r"-(\d{2})([A-Z]{3})(\d{2})-", ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    return datetime(2000 + int(yy), MONTHS[mon], int(dd), tzinfo=timezone.utc).date()


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ohlc_close(o):
    return f(getattr(o, "close_dollars", None)) if o is not None else None


def main():
    limit_series = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                done.add(json.loads(line)["series"])
            except Exception:
                pass
    series_map = verified_series()
    todo = [s for s in series_map if s not in done]
    if limit_series:
        todo = todo[:limit_series]
    print(f"[pull] {len(series_map)} verified series; {len(done)} already done; "
          f"pulling {len(todo)} now -> {OUT}")

    client, pem_path = load_client()
    fout = open(OUT, "a", encoding="utf-8")
    try:
        for si, series in enumerate(todo):
            icao, kind = series_map[series]
            try:
                mk = list(client.get_markets(status=pykalshi.MarketStatus.SETTLED,
                                             series_ticker=series, fetch_all=True))
            except Exception as e:
                print(f"[skip] {series}: get_markets failed: {e}")
                continue
            # group by event (shared date -> shared candle window)
            events = {}
            for m in mk:
                ev = getattr(m, "event_ticker", None)
                events.setdefault(ev, []).append(m)
            nrows = 0
            for ev, ms in events.items():
                d0 = parse_date(getattr(ms[0], "ticker", ""), series)
                if d0 is None:
                    continue
                start = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc) - timedelta(days=1)
                end = datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc) + timedelta(hours=18)
                tickers = [getattr(m, "ticker", None) for m in ms]
                tickers = [t for t in tickers if t]
                try:
                    cs = client.get_candlesticks_batch(
                        tickers, int(start.timestamp()), int(end.timestamp()),
                        period=pykalshi.CandlestickPeriod.ONE_HOUR)
                except Exception as e:
                    print(f"[warn] {ev}: candles failed: {e}")
                    cs = {}
                by_ticker = {getattr(m, "ticker", None): m for m in ms}
                for tk, m in by_ticker.items():
                    resp = cs.get(tk)
                    items = getattr(resp, "candlesticks", None) if resp is not None else None
                    candles = []
                    for c in (items or []):
                        ts = getattr(c, "end_period_ts", None)
                        yb = ohlc_close(getattr(c, "yes_bid", None))
                        ya = ohlc_close(getattr(c, "yes_ask", None))
                        px = getattr(c, "price", None)
                        pxc = f(getattr(px, "close_dollars", None)) if px is not None else None
                        if ts is not None and (yb is not None or ya is not None):
                            candles.append([int(ts), yb, ya, pxc])
                    row = {
                        "series": series, "icao": icao, "kind": kind,
                        "ticker": tk, "event": ev,
                        "date": str(parse_date(tk, series)),
                        "floor": f(getattr(m, "floor_strike", None)),
                        "cap": f(getattr(m, "cap_strike", None)),
                        "result": getattr(m, "result", None),
                        "candles": candles,
                    }
                    fout.write(json.dumps(row) + "\n")
                    nrows += 1
                time.sleep(0.12)  # gentle inter-event pacing
            fout.flush()
            print(f"[{si+1}/{len(todo)}] {series} ({icao}/{kind}): "
                  f"{len(mk)} markets, {nrows} rows written")
    finally:
        fout.close()
        try:
            client.close()
        except Exception:
            pass
        try:
            os.remove(pem_path)
        except Exception:
            pass
    print("[pull] done")


if __name__ == "__main__":
    main()
