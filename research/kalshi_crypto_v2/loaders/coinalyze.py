"""S3.3 Coinalyze loader -> lab_coinalyze (long: asset, ts_ms, interval, metric).

Aggregated perp (SYMBOL '<BASE>USDT_PERP.A') per asset. Pulls, per interval:
  ohlcv-history            -> price_o/h/l/c, vol, buy_vol, trades, buy_trades
                              (CVD derived at analysis time: sell=vol-buy_vol)
  open-interest-history    -> oi_o/h/l/c
  funding-rate-history     -> funding_o/h/l/c
  long-short-ratio-history -> ls_<field> (dynamic; empty at fine intervals)
  liquidation-history      -> liq_long, liq_short

RETENTION (probed 2026-08-02): Coinalyze retains 1min ~2d, 5min ~8d, 15min ~22d,
1hour = full period. So we pull ALL of {1min,5min,15min,1hour}; fine intervals
land only a recent tail (surfaced as coverage gaps). Raw responses are cached so
re-runs don't re-pull (pass --refresh to bypass). Key in-memory from KV, redacted.

Usage: python research/kalshi_crypto_v2/loaders/coinalyze.py [--refresh]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
BASE = "https://api.coinalyze.net/v1"
SYMBOL = {a: f"{a}USDT_PERP.A" for a in common.ASSETS}
INTERVALS = ["1min", "5min", "15min", "1hour"]
THROTTLE = 1.6  # ~37 req/min, under Coinalyze's cap

OHLCV_MAP = {"o": "price_o", "h": "price_h", "l": "price_l", "c": "price_c",
             "v": "vol", "bv": "buy_vol", "tx": "trades", "btx": "buy_trades"}
OI_MAP = {"o": "oi_o", "h": "oi_h", "l": "oi_l", "c": "oi_c"}
FUND_MAP = {"o": "funding_o", "h": "funding_h", "l": "funding_l", "c": "funding_c"}
LIQ_MAP = {"l": "liq_long", "s": "liq_short"}
ENDPOINTS = [("ohlcv-history", OHLCV_MAP), ("open-interest-history", OI_MAP),
             ("funding-rate-history", FUND_MAP), ("long-short-ratio-history", None),
             ("liquidation-history", LIQ_MAP)]


def get_key() -> str:
    v = os.getenv("COINALYZE_API_KEY")
    if v:
        return v
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    c = SecretClient(vault_url=os.environ["KEY_VAULT_URI"], credential=DefaultAzureCredential())
    return c.get_secret("coinalyze-api-key").value


def fetch_history(key: str, endpoint: str, symbol: str, interval: str,
                  frm: int, to: int, refresh: bool) -> list:
    ckey = f"coinalyze_{endpoint}_{symbol}_{interval}_{frm}_{to}"
    if not refresh:
        cached = common.cache_get(ckey)
        if cached is not None:
            return cached
    data = common.http_get(f"{BASE}/{endpoint}", headers={"api_key": key}, throttle=THROTTLE,
                           params={"symbols": symbol, "interval": interval, "from": frm, "to": to})
    hist = (data[0].get("history") if data else []) or []
    common.cache_put(ckey, hist)
    return hist


def load(conn, key: str, asset: str, interval: str, frm: int, to: int, refresh: bool) -> dict:
    sym = SYMBOL[asset]
    per_metric = {}
    for endpoint, fmap in ENDPOINTS:
        try:
            hist = fetch_history(key, endpoint, sym, interval, frm, to, refresh)
        except common.GetError as e:
            print(f"    {asset} {interval} {endpoint}: ERR {str(e)[:70]}", flush=True)
            continue
        recs = []
        for pt in hist:
            ts_ms = int(pt["t"]) * 1000
            if fmap is None:  # long-short-ratio: dynamic numeric fields
                for k, v in pt.items():
                    if k == "t" or not isinstance(v, (int, float)):
                        continue
                    recs.append((asset, ts_ms, interval, f"ls_{k}", float(v)))
                    per_metric[f"ls_{k}"] = per_metric.get(f"ls_{k}", 0) + 1
            else:
                for k, metric in fmap.items():
                    if k in pt and pt[k] is not None:
                        recs.append((asset, ts_ms, interval, metric, float(pt[k])))
                        per_metric[metric] = per_metric.get(metric, 0) + 1
        if recs:
            conn.executemany(
                "INSERT OR REPLACE INTO lab_coinalyze(asset,ts_ms,interval,metric,value)"
                " VALUES(?,?,?,?,?)", recs)
            conn.commit()
    return per_metric


def main() -> int:
    refresh = "--refresh" in sys.argv
    key = get_key()
    frm = common.PERIOD_START_MS // 1000
    to = common.now_ms() // 1000
    conn = common.connect()
    print(f"Coinalyze  {common.iso(frm*1000)} -> {common.iso(to*1000)}  refresh={refresh}")
    try:
        for asset in common.ASSETS:
            for interval in INTERVALS:
                pm = load(conn, key, asset, interval, frm, to, refresh)
                # coverage on the interval grid using price_c presence
                ts = [r[0] for r in conn.execute(
                    "SELECT ts_ms FROM lab_coinalyze WHERE asset=? AND interval=? AND metric='price_c'",
                    (asset, interval))]
                step = common.INTERVAL_MS[interval]
                cov = common.minute_coverage(ts, common.PERIOD_START_MS, common.now_ms(), step)
                common.write_coverage(conn, f"coinalyze_{interval}", asset, cov["rows"],
                                      cov["min_ts"], cov["max_ts"], len(cov["gaps"]),
                                      f"missing={cov['missing']} gap_frac={cov['gap_frac']:.2%}"
                                      f" metrics={sorted(pm)}")
                print(f"  {asset} {interval:6}: price pts={cov['rows']:>5}"
                      f" span={common.iso(cov['min_ts'])}..{common.iso(cov['max_ts'])}"
                      f" gap={cov['gap_frac']:.1%} metrics={len(pm)}", flush=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
