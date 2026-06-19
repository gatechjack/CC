"""Stop-distance sensitivity sweep (read-only) — net-per-fire vs stop width.

The fee reconciliation showed the real lever is the STOP, not the fee: at a 0.30% stop,
fee-drag-in-R = round_trip% / stop% is large. Here we sweep the stop width and recompute
net-per-fire using the CORRECTED effective fees (discounted entry ~0.0243%, maker TP exit
0.0140%, taker SL exit 0.0400%), to find the fee-drag-vs-width curve, the breakeven crossing
(if any), and the stop this window's faint gross edge would need.

Population = the real score-cleared LONG signals (same as the PA cost test). Two views:
  - BASELINE "actual": the strategy's own ATR/swing stop via build_v2_plan (pins the current
    ~0.30% empirically; includes the real fee-gate should_trade).
  - SWEEP "fixed-%": a MECHANICAL idealization — SL at a fixed % of entry, TP ladder at the
    strategy's R-multiples (0.5/1.0/2.5R, 0.25/0.50/0.25) scaling with the stop; ALL longs
    walked (no fee-gate) to isolate the stop-width mechanic.
Per-trade fee: win -> TP -> maker exit; loss/open -> taker exit; entry always discounted.
Open trades (timeout at max_bars) are marked-to-market at the final bar (unbiased vs width).

INTERIM: one bear/quiet window, faint one-directional gross edge -> the "best stop" here is
for THIS tape, NOT a verdict. N=5 real-fill trades anchor the FEE rates only; win/gross come
from the larger backtest population. Fixed-% sweep abstracts the ATR/swing SL logic.
"""
from __future__ import annotations
import argparse, csv, json, sqlite3, statistics, sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))      # scripts/
sys.path.insert(0, str(_HERE.parents[2]))      # worktree root
import backtest_bitunix_confluence as E  # noqa: E402
import yaml  # noqa: E402

ENTRY_ACT = 0.000243            # actual discounted entry (Fee Discount Card)
MK, TK = 0.00014, 0.0004        # confirmed exact: maker TP exit / taker SL exit
SLIP2 = 0.0001                  # 2x0.005% slippage (model)
WIDTHS = [0.0030, 0.0040, 0.0050, 0.0075, 0.0100, 0.0150, 0.0200]
RMULT = [("tp1", 0.5, 0.25), ("tp2", 1.0, 0.50), ("tp3", 2.5, 0.25)]  # StrategyConfig ladder
MAXBARS = 480


def parse_ts(s):
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def load_bars(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT ts,open,high,low,close,volume FROM bars_3m ORDER BY ts").fetchall()
    con.close()
    return [{"ts": datetime.fromtimestamp(int(t), tz=timezone.utc), "open": o, "high": h,
             "low": l, "close": c, "volume": (v or 0.0)} for t, o, h, l, c, v in rows]


def corrected_drag(stop_pct, is_win, is_open):
    exit_fee = MK if (is_win and not is_open) else TK
    return (ENTRY_ACT + exit_fee + SLIP2) / stop_pct


def fixed_legs(entry, stop_pct):
    risk = stop_pct * entry
    legs = {"_sl": entry - risk}
    for lg, r, f in RMULT:
        legs[lg] = {"px": entry + r * risk, "r": r, "f": f}
    return legs


def walk_one(entry, legs, bar_objs, start_idx):
    o, gr, amb, fl = E.walk_v2("buy", entry, legs, bar_objs, start_idx, max_bars=MAXBARS)
    is_open = gr is None
    if is_open:
        fin = min(len(bar_objs) - 1, start_idx + MAXBARS - 1)
        gr = E._agg_r("buy", entry, legs["_sl"], legs, fl, bar_objs[fin].close)
    return o, gr, is_open


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cleared-buy", required=True)
    p.add_argument("--bybit-db", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)

    bars = load_bars(a.bybit_db)
    bar_objs = E._bars_to_objs(bars)
    idxs = []
    with open(a.cleared_buy, newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("cooldown_blocked", "")).strip().lower() in ("1", "true"):
                continue
            ats = parse_ts(r["ts"])
            ats = datetime.fromtimestamp((int(ats.timestamp()) // 180) * 180, tz=timezone.utc)
            i = E._bar_idx_at(bar_objs, ats)
            if i >= 0:
                idxs.append(i)

    # BASELINE: strategy's own ATR/swing stop (build_v2_plan) — pins the current stop
    b_net, b_stop, b_win, b_res, b_skip = [], [], 0, 0, 0
    for i in idxs:
        entry = bar_objs[i].close
        tp = E.build_v2_plan("buy", entry, bar_objs, i)
        if tp is None:
            continue
        if not tp.should_trade:
            b_skip += 1
            continue
        o, gr, is_open = walk_one(entry, E._plan_to_legs(tp, entry), bar_objs, i + 1)
        sp = tp.risk_per_unit / entry
        win = (o == "win")
        if not is_open:
            b_res += 1; b_win += int(win)
        b_net.append(gr - corrected_drag(sp, win, is_open)); b_stop.append(sp)
    baseline = {
        "kind": "actual_atr_swing(build_v2_plan, real fee-gate)",
        "n_traded": len(b_net), "n_plan_skipped": b_skip,
        "mean_stop_pct": round(100 * statistics.fmean(b_stop), 4),
        "median_stop_pct": round(100 * statistics.median(b_stop), 4),
        "win_pct": round(100 * b_win / max(1, b_res), 1),
        "net_r": round(statistics.fmean(b_net), 4),
    }

    # SWEEP: fixed-% stop, R-multiple TPs, all longs walked
    sweep = []
    for w in WIDTHS:
        nets, gross, win, res, opn = [], [], 0, 0, 0
        for i in idxs:
            entry = bar_objs[i].close
            o, gr, is_open = walk_one(entry, fixed_legs(entry, w), bar_objs, i + 1)
            wn = (o == "win")
            if is_open:
                opn += 1
            else:
                res += 1; win += int(wn)
            nets.append(gr - corrected_drag(w, wn, is_open)); gross.append(gr)
        wr = win / max(1, res)
        rt = ENTRY_ACT + (wr * MK + (1 - wr) * TK) + SLIP2
        sweep.append({
            "stop_pct": round(w * 100, 2), "n": len(idxs),
            "win_pct": round(100 * wr, 1), "open_pct": round(100 * opn / len(idxs), 1),
            "mean_gross_r": round(statistics.fmean(gross), 4),
            "round_trip_pct": round(rt * 100, 4), "fee_drag_r": round(rt / w, 3),
            "net_r": round(statistics.fmean(nets), 4),
        })

    out = {"baseline": baseline, "sweep": sweep,
           "corrected_fees": {"entry": ENTRY_ACT, "maker_exit": MK, "taker_exit": TK, "slippage_rt": SLIP2}}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"BASELINE (actual ATR/swing): mean_stop={baseline['mean_stop_pct']}% "
          f"median={baseline['median_stop_pct']}% win={baseline['win_pct']}% "
          f"net={baseline['net_r']:+}R  (n_traded={baseline['n_traded']}, plan_skip={baseline['n_plan_skipped']})")
    print(f"\nFIXED-% SWEEP (corrected fees; all {len(idxs)} longs walked):")
    print(f"{'stop%':>6} {'win%':>6} {'open%':>6} {'gross_R':>9} {'rt%':>8} {'fee_drag_R':>11} {'net_R':>9}")
    prev = None
    cross = None
    for s in sweep:
        print(f"{s['stop_pct']:>6} {s['win_pct']:>6} {s['open_pct']:>6} {s['mean_gross_r']:>9} "
              f"{s['round_trip_pct']:>8} {s['fee_drag_r']:>11} {s['net_r']:>+9}")
        if prev is not None and cross is None and prev['net_r'] < 0 <= s['net_r']:
            cross = (prev['stop_pct'], s['stop_pct'])
        prev = s
    if cross:
        print(f"\nBREAKEVEN crossing between {cross[0]}% and {cross[1]}% stop")
    else:
        best = max(sweep, key=lambda x: x['net_r'])
        print(f"\nNO breakeven crossing in swept range; best net {best['net_r']:+}R at {best['stop_pct']}% "
              f"(still {'positive' if best['net_r']>=0 else 'NEGATIVE'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
