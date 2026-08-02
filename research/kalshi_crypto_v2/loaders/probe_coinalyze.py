"""Coinalyze API surface probe (READ-ONLY, no DB writes). Determines, before
building the S3.3 loader: (a) which aggregated symbols cover BTC/ETH/SOL/XRP,
(b) whether 1-min history reaches back to the 2026-05-25 backfill start (this is
the flow/positioning LEAD data — a truncated lookback is a hard S3 finding),
(c) the OHLCV field names so CVD (buy vs sell volume) can be constructed.

Key fetched in-memory from KV (redacted). Usage:
  python research/kalshi_crypto_v2/loaders/probe_coinalyze.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

BASE = "https://api.coinalyze.net/v1"
BASES = {"BTC", "ETH", "SOL", "XRP"}


def get_key() -> str:
    v = os.getenv("COINALYZE_API_KEY")
    if v:
        return v
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    vault = os.getenv("KEY_VAULT_URI") or "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
    c = SecretClient(vault_url=vault, credential=DefaultAzureCredential())
    return c.get_secret("coinalyze-api-key").value


def main() -> int:
    key = get_key()
    hdr = {"api_key": key}
    print(f"key: present len={len(key)}")

    # (a) markets: find symbols covering our bases
    fm = common.http_get(f"{BASE}/future-markets", headers=hdr, throttle=1.5)
    print(f"\nfuture-markets: {len(fm)} rows")
    # discover exchange codes + which have aggregated ('.A' style) symbols
    by_base = {}
    for m in fm:
        b = (m.get("base_asset") or "").upper()
        if b in BASES:
            by_base.setdefault(b, []).append(m)
    for b in sorted(BASES):
        rows = by_base.get(b, [])
        perps = [m for m in rows if m.get("is_perpetual")]
        print(f"  {b}: {len(rows)} markets ({len(perps)} perp). sample symbols:")
        for m in rows[:4]:
            print(f"     symbol={m.get('symbol')!r} exch={m.get('exchange')!r}"
                  f" perp={m.get('is_perpetual')} quote={m.get('quote_asset')!r}")
    # show any aggregated symbols (exchange code often 'A' or contains 'aggregate')
    aggs = sorted({m.get("symbol") for m in fm
                   if (m.get("base_asset") or "").upper() in BASES
                   and str(m.get("symbol", "")).endswith(".A")})
    print(f"\n  aggregated (.A) symbols for our bases: {aggs[:12]}")

    # pick the canonical USDT-margined aggregated perp
    def pick(base: str) -> str | None:
        cands = [m.get("symbol") for m in by_base.get(base, [])]
        for pref in (f"{base}USDT_PERP.A", f"{base}USD_PERP.A", f"{base}USDT.A"):
            if pref in cands:
                return pref
        return cands[0] if cands else None

    test_sym = pick("BTC")
    now_s = common.now_ms() // 1000
    windows = {"full_2026-05-25": common.PERIOD_START_MS // 1000,
               "recent_3d": now_s - 3 * 86400}
    for wname, frm in windows.items():
        print(f"\n(b) symbol={test_sym!r} window={wname}")
        for ep in ("ohlcv-history", "open-interest-history", "funding-rate-history",
                   "long-short-ratio-history", "liquidation-history"):
            try:
                data = common.http_get(f"{BASE}/{ep}", headers=hdr, throttle=1.5, params={
                    "symbols": test_sym, "interval": "1min", "from": frm, "to": now_s})
                hist = (data[0].get("history") if data else []) or []
                if hist:
                    t0, t1 = hist[0].get("t"), hist[-1].get("t")
                    print(f"  {ep:26} pts={len(hist):>6} first={common.iso(t0*1000)}"
                          f" last={common.iso(t1*1000)} fields={sorted(hist[0].keys())}")
                    if ep == "ohlcv-history":
                        print(f"     first pt: {hist[0]}")
                else:
                    print(f"  {ep:26} EMPTY (data rows={len(data) if data else 0})")
            except common.GetError as e:
                print(f"  {ep:26} ERR {str(e)[:90]}")
    print(f"\nchosen symbols: " + ", ".join(f"{b}->{pick(b)}" for b in sorted(BASES)))

    # (c) retention/paging test: does a PAST-only 1min window return deep data,
    #     and how deep do coarser intervals reach? Decides chunked-paging viability.
    print("\n(c) retention / paging probe (ohlcv-history, BTCUSDT_PERP.A)")
    tests = [
        ("1min  past 05-25..05-27", "1min", common.PERIOD_START_MS // 1000,
         (common.PERIOD_START_MS // 1000) + 2 * 86400),
        ("5min  full window", "5min", common.PERIOD_START_MS // 1000, now_s),
        ("15min full window", "15min", common.PERIOD_START_MS // 1000, now_s),
        ("1hour full window", "1hour", common.PERIOD_START_MS // 1000, now_s),
    ]
    for label, interval, frm, to in tests:
        try:
            data = common.http_get(f"{BASE}/ohlcv-history", headers=hdr, throttle=1.5, params={
                "symbols": test_sym, "interval": interval, "from": frm, "to": to})
            hist = (data[0].get("history") if data else []) or []
            if hist:
                print(f"  {label:26} pts={len(hist):>6} first={common.iso(hist[0]['t']*1000)}"
                      f" last={common.iso(hist[-1]['t']*1000)}")
            else:
                print(f"  {label:26} EMPTY")
        except common.GetError as e:
            print(f"  {label:26} ERR {str(e)[:80]}")

    # (d) true retention floor per interval: PAST-ONLY window far back, 5-day slice
    print("\n(d) retention floor (past-only 5-day slice starting 2026-05-25)")
    frm = common.PERIOD_START_MS // 1000
    to5 = frm + 5 * 86400
    for interval in ("1min", "5min", "15min", "30min", "1hour"):
        try:
            data = common.http_get(f"{BASE}/ohlcv-history", headers=hdr, throttle=1.5, params={
                "symbols": test_sym, "interval": interval, "from": frm, "to": to5})
            hist = (data[0].get("history") if data else []) or []
            if hist:
                print(f"  {interval:8} pts={len(hist):>5} first={common.iso(hist[0]['t']*1000)}"
                      f" last={common.iso(hist[-1]['t']*1000)}  -> RETAINED this far back")
            else:
                print(f"  {interval:8} EMPTY -> not retained at 05-25")
        except common.GetError as e:
            print(f"  {interval:8} ERR {str(e)[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
