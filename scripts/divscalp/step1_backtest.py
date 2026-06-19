"""Step 1: operator-model backtest of the divergence scalp — net-per-fire, walk-forward.

Entry at the signal bar (close) OR next-bar OPEN (realistic live fill on a once-per-bar-close
alert). STOP just beyond the local extreme (min low / max high over last K bars incl signal,
minus/plus a buffer). TP = fixed R off that tight stop. CORRECTED fees (entry 0.0243%,
maker TP 0.0140%, taker SL 0.0400%, slip 0.005%/leg), drag-in-R = round_trip% / stop%.
Sweep K x buffer x R; report TRAIN + VALIDATE net-per-fire. LOCKBOX (>=Jun 1) reserved
(separate step). Look for a ROBUST positive region, not a single peak.
"""
from __future__ import annotations
import argparse, json, sqlite3, statistics
from datetime import datetime, timezone
from pathlib import Path

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY_FEE, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
MAXBARS = 40  # 2h scalp cap
KS = [1, 3, 5, 10]
BUFS = [0.0003, 0.0005, 0.0010]   # 0.03 / 0.05 / 0.10 %
RS = [1.0, 1.5, 2.0, 3.0]
SIGS = [("bull_divergence", "buy"), ("bear_divergence", "sell")]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=r"C:\Users\AA Incorporado\cc-otter-divscalp-wt\data\divscalp\step1.json")
    a = p.parse_args()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close", "bull_divergence", "bear_divergence"]
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(cols)}
    O, H, L, C = ci["open"], ci["high"], ci["low"], ci["close"]

    def trade(i, side, k, buf, R, entry_mode):
        # local extreme over [i-k+1 .. i]
        seg = rows[max(0, i - k + 1): i + 1]
        lo_k = min(float(b[L]) for b in seg); hi_k = max(float(b[H]) for b in seg)
        if entry_mode == "close":
            ei = i; entry = float(rows[i][C])
        else:  # next_open
            ei = i + 1
            if ei >= len(rows):
                return None
            entry = float(rows[ei][O])
        if side == "buy":
            stop = lo_k - buf * entry; risk = entry - stop
            if risk <= 0:
                return None
            tp = entry + R * risk
        else:
            stop = hi_k + buf * entry; risk = stop - entry
            if risk <= 0:
                return None
            tp = entry - R * risk
        stop_pct = risk / entry
        # walk from ei+1
        outcome, grossR = "open", None
        for j in range(ei + 1, min(len(rows), ei + 1 + MAXBARS)):
            hi, lo = float(rows[j][H]), float(rows[j][L])
            if side == "buy":
                hit_sl = lo <= stop; hit_tp = hi >= tp
            else:
                hit_sl = hi >= stop; hit_tp = lo <= tp
            if hit_sl:           # SL-first on tie (conservative)
                outcome, grossR = "loss", -1.0; break
            if hit_tp:
                outcome, grossR = "win", R; break
        if grossR is None:       # timeout -> mark to last close
            last = float(rows[min(len(rows) - 1, ei + MAXBARS)][C])
            grossR = ((last - entry) if side == "buy" else (entry - last)) / risk
        exitf = MK if outcome == "win" else TK
        net = grossR - (ENTRY_FEE + exitf + SLIP2) / stop_pct
        return outcome, net, stop_pct

    def evalc(sig, side, k, buf, R, em, lo, hi):
        col = ci[sig]; nets, win, sl, n = [], 0, 0, 0
        for i, r in enumerate(rows):
            if r[ci["ts"]] < lo or r[ci["ts"]] >= hi:
                continue
            if not r[col] or float(r[col]) == 0.0:
                continue
            t = trade(i, side, k, buf, R, em)
            if t is None:
                continue
            outcome, net, sp = t
            nets.append(net); n += 1
            win += int(outcome == "win"); sl += int(outcome == "loss")
        if not nets:
            return None
        return {"n": n, "win%": round(100 * win / n, 1), "sl%": round(100 * sl / n, 1),
                "net": round(statistics.fmean(nets), 4)}

    results = {}
    for sig, side in SIGS:
        for em in ("close", "next_open"):
            print(f"\n===== {sig} ({side}) entry={em} =====")
            print(f"{'K':<3}{'buf%':<6}{'R':<5}{'TRAIN n/win%/sl%/NET':<30}{'VALIDATE n/win%/sl%/NET'}")
            for k in KS:
                for buf in BUFS:
                    for R in RS:
                        tr = evalc(sig, side, k, buf, R, em, 0, TRAIN_END)
                        va = evalc(sig, side, k, buf, R, em, TRAIN_END, VAL_END)
                        key = f"{sig}|{em}|K{k}|b{buf}|R{R}"
                        results[key] = {"train": tr, "validate": va}
                        f = lambda d: f"n={d['n']:<4} w={d['win%']:<5} sl={d['sl%']:<5} N={d['net']:+.3f}" if d else "(none)"
                        flag = " *" if (tr and va and tr["net"] > 0 and va["net"] > 0 and tr["n"] >= 15 and va["n"] >= 6) else ""
                        print(f"{k:<3}{buf*100:<6}{R:<5}{f(tr):<30}{f(va)}{flag}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    robust = [k for k, v in results.items() if v["train"] and v["validate"]
              and v["train"]["net"] > 0 and v["validate"]["net"] > 0
              and v["train"]["n"] >= 15 and v["validate"]["n"] >= 6]
    print(f"\n=== configs positive on BOTH train+validate (N-gated): {len(robust)} ===")
    for k in robust:
        print(f"  {k}  train {results[k]['train']['net']:+.3f} / validate {results[k]['validate']['net']:+.3f}")


if __name__ == "__main__":
    main()
