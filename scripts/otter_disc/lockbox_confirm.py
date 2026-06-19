"""FINAL LOCKBOX eval (one touch) — held-out Jun 1 -> Jun 19, never used in the search.

The only train+validate-positive candidate was bull/bear_divergence, which the repaint test
exposed as look-ahead (collapses at k>=1). This confirms it on the LOCKBOX: k=0 (the inflated,
NON-tradeable repaint entry) vs k>=1 (the honest tradeable entry). The headline lockbox number
is the HONEST (k=1) net — if negative, the verdict is a confirmed null. Corrected fees.
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
LOCKBOX_LO = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()  # >= this = LOCKBOX (never searched)


def cnet(g, win, opn, sp):
    return g - (ENTRY + (MK if (win and not opn) else TK) + SLIP2) / sp


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT ts,open,high,low,close,volume,bull_divergence,bear_divergence "
                       "FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    bars = [{"ts": datetime.fromtimestamp(int(r[0]), tz=timezone.utc), "open": r[1], "high": r[2],
             "low": r[3], "close": r[4], "volume": r[5] or 0.0} for r in rows]
    bo = E._bars_to_objs(bars)

    def ev(col_i, side, k):
        nets, win, res = [], 0, 0
        for idx, r in enumerate(rows):
            if r[0] < LOCKBOX_LO:
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
            o, gr, amb, fl = E.walk_v2(side, entry, E._plan_to_legs(tp, entry), bo, j + 1, max_bars=MAXBARS)
            opn = gr is None
            if opn:
                fin = min(len(bo) - 1, j + MAXBARS)
                gr = E._agg_r(side, entry, E._plan_to_legs(tp, entry)["_sl"], E._plan_to_legs(tp, entry), fl, bo[fin].close)
            else:
                res += 1; win += int(o == "win")
            nets.append(cnet(gr, o == "win", opn, tp.risk_per_unit / entry))
        if not nets:
            return None
        return len(nets), round(100 * win / max(1, res), 1), round(statistics.fmean(nets), 4)

    print("=== LOCKBOX (Jun 1 -> Jun 19, held out) — divergence candidate ===")
    print(f"{'signal':<18} {'k':<3} {'n':<5} {'win%':<6} {'net_R':<9} note")
    for col_i, name, side in [(6, "bull_divergence", "buy"), (7, "bear_divergence", "sell")]:
        for k in (0, 1, 2, 3):
            d = ev(col_i, side, k)
            note = "REPAINT (non-tradeable)" if k == 0 else "HONEST tradeable entry"
            s = f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
            print(f"{name:<18} {k:<3} {s:<28} {note}")


if __name__ == "__main__":
    main()
