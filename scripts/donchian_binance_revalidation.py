"""Donchian re-validation on the Binance 4Y corpus — PHASE 1 (data + cross-venue).

Read-only research harness. Reuses the byte-identical decision module
`trading_corp.agents.strategies.donchian_btc.evaluate_donchian` so signals
match prod exactly.

Phase 1 does:
  1. Load Binance USD-M perp BTCUSDT 1h monthly CSVs, derive 6h bars, validate.
  2. Load Coinbase BTC-USD 6h (cached from backtest_donchian) for the overlap.
  3. Cross-venue disagreement on closes, 20-bar highs, 6-bar lows, and the raw
     Donchian triggers (up-break / down-break / trend-ok) + compounded position.
"""
from __future__ import annotations

import csv
import glob
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"C:\Users\AA Incorporado\cc")
sys.path.insert(0, str(REPO))

from trading_corp.agents.strategies.donchian_btc import (  # noqa: E402
    Decision, DonchianConfig, State, evaluate_donchian,
)
from scripts.backtest_donchian import fetch_ohlcv  # noqa: E402

BINANCE_1H_DIR = Path(r"C:\Users\AA Incorporado\Desktop\binance_corpus\1h")
BUCKET_MS = 6 * 3600 * 1000


def load_binance_1h() -> list[tuple]:
    rows: list[tuple] = []
    files = sorted(glob.glob(str(BINANCE_1H_DIR / "BTCUSDT-1h-*.csv")))
    for f in files:
        with open(f, newline="") as fh:
            r = csv.reader(fh)
            next(r)  # header present in ALL files (per INTEGRITY_REPORT)
            for line in r:
                rows.append((int(line[0]), float(line[1]), float(line[2]),
                             float(line[3]), float(line[4]), float(line[5])))
    rows.sort(key=lambda x: x[0])
    seen, ded = set(), []
    for x in rows:
        if x[0] in seen:
            continue
        seen.add(x[0]); ded.append(x)
    return ded, len(files)


def derive_6h(rows_1h: list[tuple]):
    b, order = {}, []
    for ot, o, h, l, c, v in rows_1h:
        key = (ot // BUCKET_MS) * BUCKET_MS
        if key not in b:
            b[key] = {"open": o, "high": h, "low": l, "close": c, "vol": v, "n": 1}
            order.append(key)
        else:
            d = b[key]
            d["high"] = max(d["high"], h); d["low"] = min(d["low"], l)
            d["close"] = c; d["vol"] += v; d["n"] += 1
    bars, incomplete = [], 0
    for key in order:
        d = b[key]
        if d["n"] != 6:
            incomplete += 1
            continue
        bars.append({"ts": datetime.fromtimestamp(key / 1000, tz=timezone.utc),
                     "open": d["open"], "high": d["high"], "low": d["low"],
                     "close": d["close"], "volume": d["vol"]})
    return bars, incomplete, len(order)


def contiguity_gaps(bars: list[dict]) -> int:
    gaps = 0
    for i in range(1, len(bars)):
        dt = (bars[i]["ts"] - bars[i - 1]["ts"]).total_seconds()
        if abs(dt - 21600) > 1:
            gaps += 1
    return gaps


def triggers(bars: list[dict], entry=20, exit=6, trend=168) -> dict:
    """Path-independent raw Donchian conditions per bar (channels exclude current,
    SMA is last `trend` incl current — matches evaluate_donchian)."""
    need = max(entry, exit, trend)
    out = {}
    for i in range(need, len(bars)):
        cur = bars[i]
        ehi = max(x["high"] for x in bars[i - entry:i])
        elo = min(x["low"] for x in bars[i - exit:i])
        sma = sum(x["close"] for x in bars[i - trend + 1:i + 1]) / trend
        out[cur["ts"]] = {
            "up": cur["close"] > ehi, "dn": cur["close"] < elo,
            "tok": cur["close"] > sma, "close": cur["close"], "ehi": ehi, "elo": elo,
        }
    return out


def position_series(bars: list[dict], cfg: DonchianConfig) -> dict:
    state, pos = State.CASH, {}
    fires = {"buy": 0, "sell": 0}
    for i in range(len(bars)):
        v = evaluate_donchian(state=state, bars_window=bars[:i + 1], config=cfg, now=bars[i]["ts"])
        if v.decision == Decision.BUY:
            state = State.BTC; fires["buy"] += 1
        elif v.decision == Decision.SELL:
            state = State.CASH; fires["sell"] += 1
        pos[bars[i]["ts"]] = state.value
    return pos, fires


def pct_stats(vals: list[float]) -> dict:
    vals = sorted(vals)
    n = len(vals)
    return {
        "n": n,
        "median": statistics.median(vals) if n else 0,
        "mean": statistics.fmean(vals) if n else 0,
        "p95": vals[int(0.95 * n)] if n else 0,
        "max": vals[-1] if n else 0,
    }


def main():
    print("=== BINANCE LOAD ===")
    rows_1h, nfiles = load_binance_1h()
    print(f"1h files={nfiles} rows_1h={len(rows_1h)}")
    print(f"1h first={datetime.fromtimestamp(rows_1h[0][0]/1000, tz=timezone.utc)} "
          f"last={datetime.fromtimestamp(rows_1h[-1][0]/1000, tz=timezone.utc)}")
    bars6, incomplete, nbuckets = derive_6h(rows_1h)
    print(f"6h buckets total={nbuckets} complete={len(bars6)} incomplete_dropped={incomplete}")
    print(f"6h first={bars6[0]['ts']} last={bars6[-1]['ts']} gaps={contiguity_gaps(bars6)}")

    # HODL over full window (context)
    hodl = (bars6[-1]["close"] - bars6[0]["close"]) / bars6[0]["close"] * 100
    print(f"BINANCE 6h span HODL={hodl:+.2f}%  first_close={bars6[0]['close']:.2f} last_close={bars6[-1]['close']:.2f}")

    # === Coinbase for cross-venue ===
    print("\n=== COINBASE LOAD (for cross-venue) ===")
    cb_start = datetime(2024, 5, 9, tzinfo=timezone.utc)
    cb_end = datetime(2026, 5, 9, tzinfo=timezone.utc)
    try:
        cb = fetch_ohlcv(cb_start, cb_end, 21600, refresh=False)
    except Exception as e:
        print("coinbase fetch failed:", e); cb = []
    print(f"coinbase 6h bars={len(cb)} first={cb[0]['ts'] if cb else None} last={cb[-1]['ts'] if cb else None}")

    # Align on ts
    binm = {b["ts"]: b for b in bars6}
    cbm = {b["ts"]: b for b in cb}
    common = sorted(set(binm) & set(cbm))
    print(f"overlap 6h bars (ts-aligned) = {len(common)}  "
          f"{common[0] if common else None} .. {common[-1] if common else None}")

    # Close / level diffs on overlap
    close_abs, close_signed = [], []
    for ts in common:
        ba, ca = binm[ts]["close"], cbm[ts]["close"]
        close_abs.append(abs(ba - ca) / ca * 100)
        close_signed.append((ba - ca) / ca * 100)
    print("\n=== CROSS-VENUE CLOSE DIFF (Binance vs Coinbase, % of Coinbase) ===")
    print("abs%:", {k: round(v, 4) for k, v in pct_stats(close_abs).items()})
    print(f"signed median%: {statistics.median(close_signed):+.4f} (perp basis/peg offset)")

    # Trigger comparison (need full-history triggers per venue, compared on overlap)
    tb = triggers(bars6)
    tc = triggers(cb)
    tcommon = sorted(set(tb) & set(tc))
    ehi_abs = [abs(tb[ts]["ehi"] - tc[ts]["ehi"]) / tc[ts]["ehi"] * 100 for ts in tcommon]
    elo_abs = [abs(tb[ts]["elo"] - tc[ts]["elo"]) / tc[ts]["elo"] * 100 for ts in tcommon]
    up_dis = sum(1 for ts in tcommon if tb[ts]["up"] != tc[ts]["up"])
    dn_dis = sum(1 for ts in tcommon if tb[ts]["dn"] != tc[ts]["dn"])
    tok_dis = sum(1 for ts in tcommon if tb[ts]["tok"] != tc[ts]["tok"])
    ent_dis = sum(1 for ts in tcommon if (tb[ts]["up"] and tb[ts]["tok"]) != (tc[ts]["up"] and tc[ts]["tok"]))
    up_b = sum(1 for ts in tcommon if tb[ts]["up"]); up_c = sum(1 for ts in tcommon if tc[ts]["up"])
    dn_b = sum(1 for ts in tcommon if tb[ts]["dn"]); dn_c = sum(1 for ts in tcommon if tc[ts]["dn"])
    N = len(tcommon)
    print("\n=== CROSS-VENUE DONCHIAN EXTREMES + TRIGGERS (overlap, N=%d) ===" % N)
    print("20-bar-high abs%:", {k: round(v, 4) for k, v in pct_stats(ehi_abs).items()})
    print("6-bar-low  abs%:", {k: round(v, 4) for k, v in pct_stats(elo_abs).items()})
    print(f"up-break events: Binance={up_b} Coinbase={up_c}  DISAGREE={up_dis} ({up_dis/N*100:.3f}% of bars)")
    print(f"dn-break events: Binance={dn_b} Coinbase={dn_c}  DISAGREE={dn_dis} ({dn_dis/N*100:.3f}% of bars)")
    print(f"trend-ok DISAGREE={tok_dis} ({tok_dis/N*100:.3f}% of bars)")
    print(f"ENTRY-signal (up&tok) DISAGREE={ent_dis} ({ent_dis/N*100:.3f}% of bars)")

    # Compounded position-state disagreement (full stateful run per venue, compared on overlap)
    cfg = DonchianConfig(entry_lookback=20, exit_lookback=6, trend_filter_lookback=168, granularity_seconds=21600)
    pb, fb = position_series(bars6, cfg)
    pc, fc = position_series(cb, cfg)
    pcommon = [ts for ts in common if ts in pb and ts in pc]
    pos_dis = sum(1 for ts in pcommon if pb[ts] != pc[ts])
    print("\n=== COMPOUNDED POSITION-STATE DISAGREEMENT (20/168/6, overlap) ===")
    print(f"Binance fires buy={fb['buy']} sell={fb['sell']} | Coinbase fires buy={fc['buy']} sell={fc['sell']}")
    print(f"position differs on {pos_dis}/{len(pcommon)} overlap bars ({pos_dis/len(pcommon)*100:.3f}%)")


if __name__ == "__main__":
    main()
