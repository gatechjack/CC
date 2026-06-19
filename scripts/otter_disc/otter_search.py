"""Otter strategy discovery — Phase 1: per-signal net-per-fire, walk-forward split.

Tests each Lord Otter trigger (CYPHER BANNED) as a standalone entry with the strategy's
real exit economics (build_v2_plan 3-leg + walk_v2) and the CORRECTED effective fees
(entry 0.0243% / maker TP 0.0140% / taker SL 0.0400% / slip 0.005%/leg, on notional).
Reports TRAIN + VALIDATE per signal; the LOCKBOX (Jun1->Jun19) is NEVER read here —
only the single selected candidate is locked, in phase 2. Open trades marked-to-market.

Entries are corpus bar rows where the signal column != 0 (3m-aligned -> no find_bar_at bug).
"""
from __future__ import annotations
import argparse, json, sqlite3, statistics, sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))      # scripts/
sys.path.insert(0, str(_HERE.parents[2]))      # worktree root
import backtest_bitunix_confluence as E  # noqa: E402

ENTRY, MK, TK, SLIP2 = 0.000243, 0.00014, 0.0004, 0.0001  # corrected effective fees
MAXBARS = 480
TRAIN_END = datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp()
VAL_END = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()   # >= VAL_END is LOCKBOX (untouched here)

BULL = ["otter_buy", "super_buy_high", "super_buy_std", "bottom_signal", "bull_divergence", "cvd_flip_bullish"]
BEAR = ["otter_sell", "super_sell_high", "super_sell_std", "top_signal", "bear_divergence", "cvd_flip_bearish"]


def load_rows(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cols = ["ts", "open", "high", "low", "close", "volume"] + BULL + BEAR
    rows = con.execute(f"SELECT {','.join(cols)} FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    return cols, rows


def cnet(gross, is_win, is_open, stop_pct):
    exitf = MK if (is_win and not is_open) else TK
    return gross - (ENTRY + exitf + SLIP2) / stop_pct


def eval_signal(sig, side, col_i, rows, bar_objs, ts_lo, ts_hi):
    """net-per-fire over rows with rows[ts] in [ts_lo, ts_hi) and signal col != 0."""
    nets, gs, win, res, opn, skip = [], [], 0, 0, 0, 0
    for idx, r in enumerate(rows):
        ts = r[0]
        if ts < ts_lo or ts >= ts_hi:
            continue
        if not r[col_i] or float(r[col_i]) == 0.0:
            continue
        entry = float(r[4])  # close
        tp = E.build_v2_plan(side, entry, bar_objs, idx)
        if tp is None or not tp.should_trade:
            skip += 1
            continue
        legs = E._plan_to_legs(tp, entry)
        o, gr, amb, fl = E.walk_v2(side, entry, legs, bar_objs, idx + 1, max_bars=MAXBARS)
        is_open = gr is None
        if is_open:
            fin = min(len(bar_objs) - 1, idx + MAXBARS)
            gr = E._agg_r(side, entry, legs["_sl"], legs, fl, bar_objs[fin].close)
            opn += 1
        else:
            res += 1; win += int(o == "win")
        sp = tp.risk_per_unit / entry
        nets.append(cnet(gr, o == "win", is_open, sp)); gs.append(gr)
    if not nets:
        return None
    return {"n": len(nets), "win_pct": round(100 * win / max(1, res), 1), "open": opn, "skip": skip,
            "mean_gross_r": round(statistics.fmean(gs), 4), "net_r": round(statistics.fmean(nets), 4)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--bybit-db", default=r"C:\Users\AA Incorporado\cc\data\btc_scalping.db")
    p.add_argument("--out", default=str(_HERE.parents[2] / "data" / "otter_disc" / "phase1.json"))
    a = p.parse_args(argv)
    cols, rows = load_rows(a.bybit_db)
    ci = {c: i for i, c in enumerate(cols)}
    bars = [{"ts": datetime.fromtimestamp(int(r[0]), tz=timezone.utc), "open": r[1], "high": r[2],
             "low": r[3], "close": r[4], "volume": r[5] or 0.0} for r in rows]
    bar_objs = E._bars_to_objs(bars)

    out = {"split": {"train": f"..{datetime.fromtimestamp(TRAIN_END,tz=timezone.utc).date()}",
                     "validate": f"..{datetime.fromtimestamp(VAL_END,tz=timezone.utc).date()}",
                     "lockbox": ">=2026-06-01 (RESERVED)"}, "signals": {}}
    print(f"{'signal':<20} {'side':<4} | {'TRAIN n/win%/gross/NET':<34} | {'VALIDATE n/win%/gross/NET'}")
    for sig, side in [(s, "buy") for s in BULL] + [(s, "sell") for s in BEAR]:
        tr = eval_signal(sig, side, ci[sig], rows, bar_objs, 0, TRAIN_END)
        va = eval_signal(sig, side, ci[sig], rows, bar_objs, TRAIN_END, VAL_END)
        out["signals"][sig] = {"side": side, "train": tr, "validate": va}
        def fmt(d):
            return f"n={d['n']:<4} w={d['win_pct']:<5} g={d['mean_gross_r']:+.3f} N={d['net_r']:+.3f}" if d else "(none)"
        print(f"{sig:<20} {side:<4} | {fmt(tr):<34} | {fmt(va)}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
