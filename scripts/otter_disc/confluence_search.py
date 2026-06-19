"""Otter + CVD/MACD/EMA/RSI confluence + coarse-regime search (read-only) — rigor for the null.

The single-signal probe rejected divergence (repaint) and found all other Otter triggers
net-negative at k=0. This pass tests whether CONFLUENCE (a single direction-aware filter) or
REGIME-conditioning rescues any TRADEABLE (non-repainting) trigger. Optimize/observe on TRAIN
+ VALIDATE; LOCKBOX untouched. Corrected fees. Complexity penalty: only 0- or 1-filter rules.
A candidate must clear BOTH train and validate (net>0, train N>=15, validate N>=8) to advance.
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

# tradeable (non-divergence) triggers from phase-1 + their corpus column
TRIGGERS = [("otter_buy", "buy"), ("otter_sell", "sell"),
            ("cvd_flip_bullish", "buy"), ("cvd_flip_bearish", "sell"),
            ("super_buy_high", "buy"), ("super_sell_high", "sell"),
            ("bottom_signal", "buy"), ("top_signal", "sell")]
COLS = ["ts", "open", "high", "low", "close", "volume", "histogram", "ema_8", "ema_8_2",
        "rsi", "cvd_close"] + sorted({t for t, _ in TRIGGERS})


def cnet(g, win, opn, sp):
    return g - (ENTRY + (MK if (win and not opn) else TK) + SLIP2) / sp


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(f"SELECT {','.join(COLS)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    ci = {c: i for i, c in enumerate(COLS)}
    bars = [{"ts": datetime.fromtimestamp(int(r[ci['ts']]), tz=timezone.utc), "open": r[ci['open']],
             "high": r[ci['high']], "low": r[ci['low']], "close": r[ci['close']],
             "volume": r[ci['volume']] or 0.0} for r in rows]
    bo = E._bars_to_objs(bars)

    def val(r, c):
        v = r[ci[c]]
        return float(v) if v is not None else None

    # coarse regime proxy: 3m close vs ema_8_2 (slowest available) — up/down/None
    def filt(name, idx, side):
        r = rows[idx]
        c = val(r, "close")
        if name == "none":
            return True
        if name == "macd":
            h = val(r, "histogram")
            return h is not None and ((h > 0) if side == "buy" else (h < 0))
        if name == "ema":
            e = val(r, "ema_8")
            return e is not None and ((c > e) if side == "buy" else (c < e))
        if name == "cvd_align":
            if idx < 3:
                return False
            now, then = val(r, "cvd_close"), val(rows[idx - 3], "cvd_close")
            if now is None or then is None:
                return False
            return (now > then) if side == "buy" else (now < then)
        if name == "cvd_contra":
            if idx < 3:
                return False
            now, then = val(r, "cvd_close"), val(rows[idx - 3], "cvd_close")
            if now is None or then is None:
                return False
            return (now < then) if side == "buy" else (now > then)
        if name == "rsi_mr":  # mean-reversion
            rs = val(r, "rsi")
            return rs is not None and ((rs < 45) if side == "buy" else (rs > 55))
        if name == "regime_with":  # trade WITH the slow trend (proxy)
            e = val(r, "ema_8_2")
            return e is not None and ((c > e) if side == "buy" else (c < e))
        if name == "regime_counter":  # mean-revert AGAINST the slow trend
            e = val(r, "ema_8_2")
            return e is not None and ((c < e) if side == "buy" else (c > e))
        return True

    def ev(trig, side, fname, lo, hi):
        col = ci[trig]; nets, win, res = [], 0, 0
        for idx, r in enumerate(rows):
            if r[ci['ts']] < lo or r[ci['ts']] >= hi:
                continue
            if not r[col] or float(r[col]) == 0.0:
                continue
            if not filt(fname, idx, side):
                continue
            entry = float(r[ci['close']])
            tp = E.build_v2_plan(side, entry, bo, idx)
            if tp is None or not tp.should_trade:
                continue
            o, gr, amb, fl = E.walk_v2(side, entry, E._plan_to_legs(tp, entry), bo, idx + 1, max_bars=MAXBARS)
            opn = gr is None
            if opn:
                fin = min(len(bo) - 1, idx + MAXBARS)
                gr = E._agg_r(side, entry, E._plan_to_legs(tp, entry)["_sl"], E._plan_to_legs(tp, entry), fl, bo[fin].close)
            else:
                res += 1; win += int(o == "win")
            nets.append(cnet(gr, o == "win", opn, tp.risk_per_unit / entry))
        if not nets:
            return None
        return len(nets), round(100 * win / max(1, res), 1), round(statistics.fmean(nets), 4)

    FILTERS = ["none", "macd", "ema", "cvd_align", "cvd_contra", "rsi_mr", "regime_with", "regime_counter"]
    winners = []
    print(f"{'trigger':<17}{'side':<5}{'filter':<15}{'TRAIN n/win/NET':<26}{'VALIDATE n/win/NET'}")
    for trig, side in TRIGGERS:
        for fn in FILTERS:
            tr = ev(trig, side, fn, 0, TRAIN_END)
            va = ev(trig, side, fn, TRAIN_END, VAL_END)
            f = lambda d: f"n={d[0]:<4} w={d[1]:<5} N={d[2]:+.3f}" if d else "(none)"
            print(f"{trig:<17}{side:<5}{fn:<15}{f(tr):<26}{f(va)}")
            if tr and va and tr[2] > 0 and va[2] > 0 and tr[0] >= 15 and va[0] >= 8:
                winners.append((trig, side, fn, tr, va))
    print("\n=== candidates clearing BOTH train & validate (net>0, N gates) ===")
    print(winners if winners else "  NONE")


if __name__ == "__main__":
    main()
