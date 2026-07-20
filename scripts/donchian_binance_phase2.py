"""Donchian re-validation — PHASE 2 (baseline 20/168/6 on Binance 4Y).

Reuses prod's byte-identical evaluate_donchian. Fees=0; slippage bps/side at
each state change; next-6h-bar-open fills, 1-bar latency. Read-only research.
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import timezone
from pathlib import Path

REPO = Path(r"C:\Users\AA Incorporado\cc")
sys.path.insert(0, str(REPO))
from trading_corp.agents.strategies.donchian_btc import (  # noqa: E402
    Decision, DonchianConfig, State, evaluate_donchian,
)
from scripts.donchian_binance_revalidation import load_binance_1h, derive_6h  # noqa: E402

BPY = 365.25 * 24 / 6  # 6h bars per year = 1461


def backtest(bars, cfg, slip_bps, start=10_000.0):
    slip = slip_bps / 1e4
    cash, btc, state, pending, entry = start, 0.0, State.CASH, None, None
    eq, inmkt, rets, rt, trade_ts = [], [], [], [], []
    prev = start
    for i, b in enumerate(bars):
        o, c = b["open"], b["close"]
        if pending == "buy":
            f = o * (1 + slip); btc = cash / f; cash = 0.0; state = State.BTC; entry = f; pending = None
        elif pending == "sell":
            f = o * (1 - slip); proc = btc * f
            rt.append(proc / (btc * entry) - 1); cash = proc; btc = 0.0; state = State.CASH
            trade_ts.append(b["ts"]); pending = None
        v = evaluate_donchian(state=state, bars_window=bars[:i + 1], config=cfg, now=b["ts"])
        if v.decision == Decision.BUY and state == State.CASH:
            pending = "buy"
        elif v.decision == Decision.SELL and state == State.BTC:
            pending = "sell"
        e = cash + btc * c
        eq.append((b["ts"], e)); inmkt.append(state == State.BTC)
        rets.append(e / prev - 1 if prev else 0.0); prev = e
    return {"eq": eq, "inmkt": inmkt, "rets": rets, "rt": rt, "trade_ts": trade_ts,
            "open_at_end": state == State.BTC}


def maxdd(eq_vals):
    peak, mdd = -1e18, 0.0
    for v in eq_vals:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak)
    return mdd * 100


def cagr_of(eq):
    start, end = eq[0][1], eq[-1][1]
    yrs = (eq[-1][0] - eq[0][0]).total_seconds() / (365.25 * 86400)
    return ((end / start) ** (1 / yrs) - 1) * 100, yrs


def sharpe(rets):
    m, sd = st.fmean(rets), st.pstdev(rets)
    return (m / sd * (BPY ** 0.5)) if sd > 0 else 0.0


def longest_flat_days(inmkt):
    best = cur = 0
    for x in inmkt:
        cur = 0 if x else cur + 1
        best = max(best, cur)
    return best * 6 / 24.0


def hodl_series(bars, start=10_000.0):
    c0 = bars[0]["close"]
    eq = [(b["ts"], start * b["close"] / c0) for b in bars]
    rets = [0.0] + [bars[i]["close"] / bars[i - 1]["close"] - 1 for i in range(1, len(bars))]
    return eq, rets


def metrics(eq, rets, inmkt, rt, open_at_end, label):
    vals = [v for _, v in eq]
    c, yrs = cagr_of(eq)
    md = maxdd(vals)
    total = (vals[-1] / vals[0] - 1) * 100
    tim = 100.0 * st.fmean([1.0 if x else 0.0 for x in inmkt])
    wins = [x for x in rt if x > 0]; losses = [x for x in rt if x < 0]
    return {
        "label": label, "total": total, "cagr": c, "maxdd": md,
        "calmar": (c / md if md else float("inf")),
        "sharpe": sharpe(rets), "tim": tim, "trades": len(rt),
        "wr": (100.0 * len(wins) / len(rt) if rt else 0.0),
        "avg_win": (100.0 * st.fmean(wins) if wins else 0.0),
        "avg_loss": (100.0 * st.fmean(losses) if losses else 0.0),
        "flat_days": longest_flat_days(inmkt), "open_at_end": open_at_end, "yrs": yrs,
    }


def row(m):
    return (f"{m['label']:<22} {m['total']:+9.1f} {m['cagr']:+7.1f} {m['maxdd']:7.1f} "
            f"{m['calmar']:7.2f} {m['sharpe']:7.2f} {m['tim']:6.1f} {m['trades']:>4} "
            f"{m['wr']:6.1f} {m['avg_win']:+7.2f} {m['avg_loss']:+7.2f} {m['flat_days']:8.0f}")


HDR = f"{'':<22} {'Total%':>9} {'CAGR%':>7} {'maxDD%':>7} {'Calmar':>7} {'Sharpe':>7} {'TIM%':>6} {'RT':>4} {'WR%':>6} {'avgW%':>7} {'avgL%':>7} {'flatDay':>8}"


def daily_regime(bars, lookback_days=60, band=10.0):
    # daily close from 6h (last bar of each date), causal trailing-return regime
    day_close = {}
    for b in bars:
        day_close[b["ts"].date()] = b["close"]  # last write per day = 18:00 close
    days = sorted(day_close)
    lab = {}
    for i, d in enumerate(days):
        if i < lookback_days:
            lab[d] = "warmup"; continue
        r = day_close[d] / day_close[days[i - lookback_days]] - 1
        lab[d] = "bull" if r > band / 100 else ("bear" if r < -band / 100 else "chop")
    return lab


def main():
    rows_1h, _ = load_binance_1h()
    bars, inc, tot = derive_6h(rows_1h)
    print(f"6h bars={len(bars)} {bars[0]['ts']} .. {bars[-1]['ts']} (incomplete={inc})")
    cfg = DonchianConfig(entry_lookback=20, exit_lookback=6, trend_filter_lookback=168, granularity_seconds=21600)

    # --- slippage sensitivity ---
    print("\n=== SLIPPAGE SENSITIVITY (20/168/6, full 4Y) ===")
    print(HDR)
    base = None
    for s in [0, 2, 3, 5, 10]:
        bt = backtest(bars, cfg, s)
        m = metrics(bt["eq"], bt["rets"], bt["inmkt"], bt["rt"], bt["open_at_end"], f"strat slip={s}bps")
        print(row(m))
        if s == 3:
            base = bt
    heq, hrets = hodl_series(bars)
    hm = metrics(heq, hrets, [True] * len(bars), [], True, "HODL")
    print(row(hm))

    # --- base (3bps) headline + DD constraint ---
    bm = metrics(base["eq"], base["rets"], base["inmkt"], base["rt"], base["open_at_end"], "strat 3bps")
    print("\n=== DD CONSTRAINT (base 3bps) ===")
    print(f"strat maxDD={bm['maxdd']:.1f}%  HODL maxDD={hm['maxdd']:.1f}%  "
          f"=> {'PASS (<=HODL)' if bm['maxdd'] <= hm['maxdd'] else 'FAIL (DEEPER THAN HODL)'}")
    print(f"strat 4Y alpha vs HODL (total): {bm['total'] - hm['total']:+.1f} pts")

    # --- per calendar year (base 3bps) ---
    print("\n=== PER CALENDAR YEAR (base 3bps) ===")
    print(f"{'Year':<6} {'sTot%':>8} {'hTot%':>8} {'alpha':>8} {'sMDD%':>7} {'hMDD%':>7} {'TIM%':>6} {'RT':>4}")
    idx_by_year = {}
    for i, b in enumerate(bars):
        idx_by_year.setdefault(b["ts"].year, []).append(i)
    year_strat, year_hodl = {}, {}
    for y in sorted(idx_by_year):
        idx = idx_by_year[y]
        sr = 1.0; hr = 1.0
        for i in idx:
            sr *= (1 + base["rets"][i]); hr *= (1 + hrets[i])
        sr = (sr - 1) * 100; hr = (hr - 1) * 100
        year_strat[y], year_hodl[y] = sr, hr
        seq = [base["eq"][i][1] for i in idx]; heq_y = [heq[i][1] for i in idx]
        smdd = maxdd(seq); hmdd = maxdd(heq_y)
        tim = 100 * st.fmean([1.0 if base["inmkt"][i] else 0.0 for i in idx])
        rts = sum(1 for t in base["trade_ts"] if t.year == y)
        print(f"{y:<6} {sr:+8.1f} {hr:+8.1f} {sr - hr:+8.1f} {smdd:7.1f} {hmdd:7.1f} {tim:6.1f} {rts:>4}")

    # --- best-year-excluded counterfactual ---
    best_y = max(year_strat, key=lambda k: year_strat[k])
    sr_ex = 1.0; hr_ex = 1.0
    for y in year_strat:
        if y == best_y:
            continue
        sr_ex *= (1 + year_strat[y] / 100); hr_ex *= (1 + year_hodl[y] / 100)
    print(f"\n=== BEST-RETURN-YEAR-EXCLUDED (drop {best_y}, strat's best return {year_strat[best_y]:+.1f}%) ===")
    print(f"strat ex-{best_y}: {(sr_ex - 1) * 100:+.1f}%   HODL ex-{best_y}: {(hr_ex - 1) * 100:+.1f}%   "
          f"alpha: {(sr_ex - hr_ex) * 100:+.1f} pts")

    alpha_y = {y: year_strat[y] - year_hodl[y] for y in year_strat}
    best_a = max(alpha_y, key=lambda k: alpha_y[k])
    sra = 1.0; hra = 1.0
    for y in year_strat:
        if y == best_a:
            continue
        sra *= (1 + year_strat[y] / 100); hra *= (1 + year_hodl[y] / 100)
    print(f"=== BEST-ALPHA-YEAR-EXCLUDED (drop {best_a}, alpha {alpha_y[best_a]:+.1f} pts) ===")
    print(f"strat ex-{best_a}: {(sra - 1) * 100:+.1f}%   HODL ex-{best_a}: {(hra - 1) * 100:+.1f}%   "
          f"alpha: {(sra - hra) * 100:+.1f} pts   (worst-case edge-concentration test)")

    # --- regime attribution (base 3bps) ---
    lab = daily_regime(bars)
    print("\n=== REGIME ATTRIBUTION (trailing-60d return, +/-10% bands; base 3bps) ===")
    print(f"{'Regime':<8} {'nDays':>6} {'nBars':>6} {'%time':>6} {'sRet%':>9} {'hRet%':>9} {'TIM%':>6}")
    reg_days = {}
    for d, r in lab.items():
        reg_days[r] = reg_days.get(r, 0) + 1
    for R in ["bull", "bear", "chop", "warmup"]:
        idx = [i for i, b in enumerate(bars) if lab[b["ts"].date()] == R]
        if not idx:
            continue
        sr = 1.0; hr = 1.0
        for i in idx:
            sr *= (1 + base["rets"][i]); hr *= (1 + hrets[i])
        tim = 100 * st.fmean([1.0 if base["inmkt"][i] else 0.0 for i in idx])
        print(f"{R:<8} {reg_days.get(R, 0):>6} {len(idx):>6} {100 * len(idx) / len(bars):6.1f} "
              f"{(sr - 1) * 100:+9.1f} {(hr - 1) * 100:+9.1f} {tim:6.1f}")


if __name__ == "__main__":
    main()
