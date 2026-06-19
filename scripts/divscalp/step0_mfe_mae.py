"""Step 0 (model-free): MFE/MAE + stop-quality probe for the Otter divergence-scalp setup.

Operator's claim: these signals catch the local extreme, so price barely goes against you
(small MAE) before moving favorably (decent MFE) -> high R:R with a stop just beyond the
local low/high. This probe measures that DIRECTLY, no exit model:
  per signal, per forward horizon h (bars), from entry = signal-bar CLOSE:
    MFE = best favorable excursion (%), MAE = worst adverse excursion (%), sign-adjusted by side.
Also reports the local-extreme stop distance (min low / max high over last K incl signal) and
the implied R:R = MFE / stop_distance. TRAIN+VALIDATE only; LOCKBOX (>=Jun 1) untouched.
Entry timing: k=0 (signal close) AND k=1 (next bar) — first look at the repaint/timing question.
"""
from __future__ import annotations
import sqlite3, statistics
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()   # >= this = LOCKBOX (excluded)
HORIZONS = [1, 3, 5, 10, 20]
KSTOP = 5  # local-extreme lookback for the stop-distance reference

LONGS = ["bull_divergence", "otter_buy", "super_buy_high", "bottom_signal"]
SHORTS = ["bear_divergence", "otter_sell", "super_sell_high", "top_signal"]


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close"] + LONGS + SHORTS
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    H, L, C = ci["high"], ci["low"], ci["close"]

    def probe(sig, side, k):
        col = ci[sig]
        per_h = {h: {"mfe": [], "mae": []} for h in HORIZONS}
        stop_pcts = []
        for i, r in enumerate(rows):
            if r[ci["ts"]] >= VAL_END:        # lockbox excluded
                continue
            if not r[col] or float(r[col]) == 0.0:
                continue
            j = i + k
            if j + max(HORIZONS) >= len(rows):
                continue
            entry = float(rows[j][C])
            # local-extreme stop distance (operator's stop just beyond local low/high)
            lo_k = min(float(rows[m][L]) for m in range(max(0, i - KSTOP + 1), i + 1))
            hi_k = max(float(rows[m][H]) for m in range(max(0, i - KSTOP + 1), i + 1))
            if side == "buy":
                stop_pcts.append((entry - lo_k) / entry * 100)
            else:
                stop_pcts.append((hi_k - entry) / entry * 100)
            for h in HORIZONS:
                seg = rows[j + 1: j + 1 + h]
                if side == "buy":
                    mfe = max((float(b[H]) - entry) / entry for b in seg) * 100
                    mae = min((float(b[L]) - entry) / entry for b in seg) * 100
                else:
                    mfe = max((entry - float(b[L])) / entry for b in seg) * 100
                    mae = min((entry - float(b[H])) / entry for b in seg) * 100  # negative = adverse
                per_h[h]["mfe"].append(mfe)
                per_h[h]["mae"].append(mae)
        n = len(per_h[HORIZONS[0]]["mfe"])
        if n == 0:
            return None
        sp = statistics.median(stop_pcts)
        out = {"n": n, "stop_pct_med": round(sp, 3)}
        for h in HORIZONS:
            mfe = statistics.median(per_h[h]["mfe"]); mae = statistics.median(per_h[h]["mae"])
            out[h] = (round(mfe, 3), round(mae, 3))
        return out

    for k in (0, 1):
        print(f"\n================ ENTRY k={k} ({'signal close' if k==0 else 'next bar'}) ================")
        print(f"(median MFE% / MAE% per horizon; stop_pct = median local-extreme stop dist, K={KSTOP})")
        hdr = "  ".join(f"h{h}:MFE/MAE" for h in HORIZONS)
        print(f"{'signal':<18}{'side':<5}{'n':<5}{'stop%':<7}{hdr}")
        for sig, side in [(s, "buy") for s in LONGS] + [(s, "sell") for s in SHORTS]:
            d = probe(sig, side, k)
            if not d:
                print(f"{sig:<18}{side:<5}(none)"); continue
            cells = "  ".join(f"{d[h][0]:+.2f}/{d[h][1]:+.2f}" for h in HORIZONS)
            # implied R:R at h=10 using local-extreme stop
            rr = d[10][0] / d["stop_pct_med"] if d["stop_pct_med"] > 0 else 0
            print(f"{sig:<18}{side:<5}{d['n']:<5}{d['stop_pct_med']:<7}{cells}   RR@h10={rr:.2f}")


if __name__ == "__main__":
    main()
