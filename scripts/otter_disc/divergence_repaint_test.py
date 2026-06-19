"""PIVOTAL look-ahead / repaint test for the bull_divergence / bear_divergence candidate.

Phase-1 showed divergence entries net +0.3R with 86-91% win — suspiciously high. Divergence
indicators confirm a pivot several bars AFTER the marked bar (right-strength lookahead), so a
column that marks the divergence at the pivot bar is REPAINTING (you couldn't know it in real
time). Test: enter k bars AFTER the marking (k = 0,1,2,3,5,8). If net stays positive through a
realistic confirmation lag (~3-5 bars) the edge is real; if it collapses at k>=1 it's a
look-ahead artifact (and the honest verdict is a null). Corrected fees; LOCKBOX untouched.
"""
from __future__ import annotations
import sqlite3, statistics, sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1])); sys.path.insert(0, str(_HERE.parents[2]))
import backtest_bitunix_confluence as E  # noqa: E402

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
ENTRY, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001
MAXBARS = 480
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()


def cnet(g, win, opn, sp):
    return g - (ENTRY + (MK if (win and not opn) else TK) + SLIP2) / sp


def run():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT ts,open,high,low,close,volume,bull_divergence,bear_divergence "
                       "FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    bars = [{"ts": datetime.fromtimestamp(int(r[0]), tz=timezone.utc), "open": r[1], "high": r[2],
             "low": r[3], "close": r[4], "volume": r[5] or 0.0} for r in rows]
    bo = E._bars_to_objs(bars)

    def ev(col_i, side, k, lo, hi):
        nets, win, res = [], 0, 0
        for idx, r in enumerate(rows):
            if r[0] < lo or r[0] >= hi:
                continue
            if not r[col_i] or float(r[col_i]) == 0.0:
                continue
            j = idx + k
            if j + 1 >= len(rows):
                continue
            entry = float(rows[j][4])
            tp = E.build_v2_plan(side, entry, bo, j)
            if tp is None or not tp.should_trade:
                continue
            legs = E._plan_to_legs(tp, entry)
            o, gr, amb, fl = E.walk_v2(side, entry, legs, bo, j + 1, max_bars=MAXBARS)
            opn = gr is None
            if opn:
                fin = min(len(bo) - 1, j + MAXBARS)
                gr = E._agg_r(side, entry, legs["_sl"], legs, fl, bo[fin].close)
            else:
                res += 1; win += int(o == "win")
            nets.append(cnet(gr, o == "win", opn, tp.risk_per_unit / entry))
        if not nets:
            return None
        return len(nets), round(100 * win / max(1, res), 1), round(statistics.fmean(nets), 4)

    for col_i, name, side in [(6, "bull_divergence", "buy"), (7, "bear_divergence", "sell")]:
        print(f"\n=== {name} ({side}) — entry-delay (repaint) test ===")
        print(f"{'k(bars delay)':<14} {'TRAIN n/win%/NET':<26} {'VALIDATE n/win%/NET'}")
        for k in (0, 1, 2, 3, 5, 8):
            tr = ev(col_i, side, k, 0, TRAIN_END)
            va = ev(col_i, side, k, TRAIN_END, VAL_END)
            f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
            print(f"{k:<14} {f(tr):<26} {f(va)}")


if __name__ == "__main__":
    run()
