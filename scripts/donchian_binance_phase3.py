"""Donchian re-validation — PHASE 3 (parameter surface) + D benchmarks + E chop probe.

Fast O(n) rolling harness (validated against the Phase 2 evaluate_donchian run).
Fees=0; slippage bps/side; next-bar-open fills, 1-bar latency. Read-only research.
"""
from __future__ import annotations

import statistics as st
import sys
from collections import deque
from datetime import timezone
from pathlib import Path

REPO = Path(r"C:\Users\AA Incorporado\cc")
sys.path.insert(0, str(REPO))
from scripts.donchian_binance_revalidation import load_binance_1h, derive_6h  # noqa: E402

BPY = 365.25 * 24 / 6  # 1461 six-hour bars/year


# ---------- fast rolling helpers ----------
def roll_max_excl(a, w):
    n = len(a); R = [None] * n; dq = deque()
    for i in range(n):
        while dq and dq[0] < i - w:
            dq.popleft()
        if i >= w and dq:
            R[i] = a[dq[0]]
        while dq and a[dq[-1]] <= a[i]:
            dq.pop()
        dq.append(i)
    return R


def roll_min_excl(a, w):
    n = len(a); R = [None] * n; dq = deque()
    for i in range(n):
        while dq and dq[0] < i - w:
            dq.popleft()
        if i >= w and dq:
            R[i] = a[dq[0]]
        while dq and a[dq[-1]] >= a[i]:
            dq.pop()
        dq.append(i)
    return R


def roll_mean_incl(a, w):
    n = len(a); R = [None] * n; s = 0.0
    for i in range(n):
        s += a[i]
        if i >= w:
            s -= a[i - w]
        if i >= w - 1:
            R[i] = s / w
    return R


# ---------- fast backtest ----------
def fast_backtest(bars, entry, exit_, trend, slip_bps=3.0, start=10_000.0):
    high = [b["high"] for b in bars]; low = [b["low"] for b in bars]
    close = [b["close"] for b in bars]; opn = [b["open"] for b in bars]; ts = [b["ts"] for b in bars]
    ehi = roll_max_excl(high, entry); elo = roll_min_excl(low, exit_)
    sma = roll_mean_incl(close, trend) if trend else None
    need = max(entry, exit_, trend or 0)
    slip = slip_bps / 1e4
    cash, btc, in_btc, pending, entryp = start, 0.0, False, None, None
    n = len(bars); eq = [0.0] * n; inmkt = [False] * n; rets = [0.0] * n
    trades = []  # (entry_ts, exit_ts, ret)
    prev = start; open_ts = None
    for i in range(n):
        if pending == "buy":
            f = opn[i] * (1 + slip); btc = cash / f; cash = 0.0; in_btc = True; entryp = f; open_ts = ts[i]; pending = None
        elif pending == "sell":
            f = opn[i] * (1 - slip); proc = btc * f
            trades.append((open_ts, ts[i], proc / (btc * entryp) - 1)); cash = proc; btc = 0.0; in_btc = False; pending = None
        if i >= need:
            if not in_btc:
                if close[i] > ehi[i] and (trend is None or close[i] > sma[i]):
                    pending = "buy"
            else:
                if close[i] < elo[i]:
                    pending = "sell"
        e = cash + btc * close[i]
        eq[i] = e; inmkt[i] = in_btc; rets[i] = (e / prev - 1) if prev else 0.0; prev = e
    return {"ts": ts, "eq": eq, "inmkt": inmkt, "rets": rets, "trades": trades, "open_at_end": in_btc}


# ---------- metrics ----------
def maxdd(vals):
    peak, m = -1e18, 0.0
    for v in vals:
        peak = max(peak, v); m = max(m, (peak - v) / peak)
    return m * 100


def met(ts, eq, inmkt, rets, trades):
    yrs = (ts[-1] - ts[0]).total_seconds() / (365.25 * 86400)
    cagr = ((eq[-1] / eq[0]) ** (1 / yrs) - 1) * 100
    md = maxdd(eq)
    sd = st.pstdev(rets); sharpe = (st.fmean(rets) / sd * BPY ** 0.5) if sd > 0 else 0.0
    tim = 100 * st.fmean([1.0 if x else 0.0 for x in inmkt])
    return {"total": (eq[-1] / eq[0] - 1) * 100, "cagr": cagr, "maxdd": md,
            "calmar": (cagr / md if md else 0.0), "sharpe": sharpe, "tim": tim, "trades": len(trades)}


# ---------- D benchmarks ----------
def hodl(bars, start=10_000.0):
    c0 = bars[0]["close"]; ts = [b["ts"] for b in bars]
    eq = [start * b["close"] / c0 for b in bars]
    rets = [0.0] + [bars[i]["close"] / bars[i - 1]["close"] - 1 for i in range(1, len(bars))]
    return ts, eq, [True] * len(bars), rets


def month_starts(bars):
    ms = [False] * len(bars); pm = None
    for i, b in enumerate(bars):
        if b["ts"].month != pm:
            ms[i] = True; pm = b["ts"].month
    return ms


def weight_portfolio(bars, wfun, slip_bps=3.0, start=10_000.0):
    ms = month_starts(bars); slip = slip_bps / 1e4
    cash, btc = start, 0.0
    ts = [b["ts"] for b in bars]; eq = [0.0] * len(bars); wts = [0.0] * len(bars); prev = start; rets = [0.0] * len(bars)
    for i, b in enumerate(bars):
        p = b["close"]
        if ms[i]:
            w = wfun(i)
            if w is not None:
                total = cash + btc * p
                tgt_val = w * total
                delta = abs(tgt_val - btc * p)
                total -= slip * delta  # rebalance slippage
                btc = tgt_val / p; cash = total - tgt_val
        e = cash + btc * p; eq[i] = e; wts[i] = (btc * p / e) if e else 0.0
        rets[i] = (e / prev - 1) if prev else 0.0; prev = e
    return ts, eq, wts, rets


def main():
    rows_1h, _ = load_binance_1h()
    bars, inc, _ = derive_6h(rows_1h)
    ts_all = [b["ts"] for b in bars]
    print(f"6h bars={len(bars)} {bars[0]['ts']}..{bars[-1]['ts']}")

    # validate fast harness vs Phase 2 baseline (20/168/6 @3bps -> +250.6%, maxDD16.7%, 68 RT)
    base = fast_backtest(bars, 20, 6, 168, 3.0)
    bm = met(base["ts"], base["eq"], base["inmkt"], base["rets"], base["trades"])
    print(f"\n[VALIDATE] 20/168/6@3bps fast: total={bm['total']:+.1f}% maxDD={bm['maxdd']:.1f}% "
          f"Calmar={bm['calmar']:.2f} TIM={bm['tim']:.1f}% RT={bm['trades']} (expect ~+250.6/16.7/2.21/26/68)")

    # ---- D benchmarks ----
    hts, heq, hin, hret = hodl(bars)
    hm = met(hts, heq, hin, hret, [])
    sig_vol = st.pstdev(base["rets"]) * BPY ** 0.5  # strategy realized ann vol
    # static 26%
    sts, seq, swt, sret = weight_portfolio(bars, lambda i: 0.26)
    sm = met(sts, seq, [w > 0 for w in swt], sret, []); sm["tim"] = 100 * st.fmean(swt)
    # vol-target: monthly weight = clip(sig_vol / trailing-30d BTC ann vol, 0, 1)
    def vt_w(i):
        if i < 120:
            return 0.0
        window = [hret[j] for j in range(i - 120, i)]
        bvol = st.pstdev(window) * BPY ** 0.5
        return max(0.0, min(1.0, sig_vol / bvol)) if bvol > 0 else 0.0
    vts, veq, vwt, vret = weight_portfolio(bars, vt_w)
    vm = met(vts, veq, [w > 0 for w in vwt], vret, []); vm["tim"] = 100 * st.fmean(vwt)

    print(f"\n=== D BENCHMARKS (strategy ann vol target = {sig_vol*100:.1f}%) ===")
    print(f"{'Portfolio':<22}{'Total%':>9}{'CAGR%':>7}{'maxDD%':>7}{'Calmar':>7}{'Sharpe':>7}{'TIM%':>6}")
    def prow(name, m):
        print(f"{name:<22}{m['total']:+9.1f}{m['cagr']:+7.1f}{m['maxdd']:7.1f}{m['calmar']:7.2f}{m['sharpe']:7.2f}{m['tim']:6.1f}")
    prow("Donchian 20/168/6", bm); prow("Static 26% (monthly)", sm)
    prow("Vol-target BTC", vm); prow("HODL", hm)

    # ---- Phase 3 grid ----
    ENTRY = [10, 15, 20, 28, 40, 55, 75, 100]
    TREND = [None, 84, 168, 336, 504, 720]
    EXIT = [3, 4, 6, 8, 12, 20]
    combos = [(e, x, t) for e in ENTRY for t in TREND for x in EXIT if x < e]
    print(f"\n=== PHASE 3 GRID: {len(combos)} combos (entry{ENTRY} x trend{TREND} x exit{EXIT}, exit<entry) ===")
    res = []
    for (e, x, t) in combos:
        bt = fast_backtest(bars, e, x, t, 3.0)
        m = met(bt["ts"], bt["eq"], bt["inmkt"], bt["rets"], bt["trades"])
        m["cfg"] = (e, x, t); res.append(m)
    hodl_dd = hm["maxdd"]
    eligible = [m for m in res if m["maxdd"] <= hodl_dd]
    dq = [m for m in res if m["maxdd"] > hodl_dd]
    eligible.sort(key=lambda m: m["calmar"], reverse=True)
    dq.sort(key=lambda m: m["calmar"], reverse=True)

    def line(m):
        e, x, t = m["cfg"]
        return (f"e={e:<3} x={x:<2} t={str(t):<4} | Cal={m['calmar']:6.2f} tot={m['total']:+8.1f} "
                f"cagr={m['cagr']:+6.1f} mdd={m['maxdd']:5.1f} shp={m['sharpe']:5.2f} tim={m['tim']:5.1f} rt={m['trades']:>3}")

    print(f"\n--- TOP 10 by Calmar (ELIGIBLE: maxDD <= HODL {hodl_dd:.1f}%) ---")
    for m in eligible[:10]:
        print(line(m))
    print(f"\n--- Top 5 DISQUALIFIED (maxDD > HODL {hodl_dd:.1f}%) but high Calmar [rule A] ---")
    for m in dq[:5]:
        print(line(m))

    cals = sorted(m["calmar"] for m in res); tots = sorted(m["total"] for m in res)
    mdds = sorted(m["maxdd"] for m in res); tims = sorted(m["tim"] for m in res)
    n = len(res)
    print(f"\n--- GRID MEDIAN (n={n}) ---")
    print(f"median Calmar={cals[n//2]:.2f}  median total={tots[n//2]:+.1f}%  "
          f"median maxDD={mdds[n//2]:.1f}%  median TIM={tims[n//2]:.1f}%")
    print(f"eligible (maxDD<=HODL): {len(eligible)}/{n}  beat-HODL-total: {sum(1 for m in res if m['total']>hm['total'])}/{n}")

    # rank of 20/168/6 + neighbors
    res_by_cfg = {m["cfg"]: m for m in res}
    inc_m = res_by_cfg.get((20, 6, 168))
    allcal = sorted((m["calmar"] for m in res), reverse=True)
    rank = allcal.index(inc_m["calmar"]) + 1 if inc_m else None
    print(f"\n--- 20/168/6 = {line(inc_m)}   (Calmar rank {rank}/{n}) ---")
    print("immediate neighbors (one grid step per axis):")
    for cfg in [(15, 6, 168), (28, 6, 168), (20, 4, 168), (20, 8, 168), (20, 6, 84), (20, 6, 336)]:
        m = res_by_cfg.get(cfg)
        if m:
            print("  " + line(m))

    # ---- E: chop interrogation ----
    print("\n=== E1. REGIME-LABEL SENSITIVITY: chop-cell strat vs HODL return ===")
    print(f"{'lookback_d':>10}{'band%':>7}{'chop%time':>10}{'strat_ret%':>11}{'hodl_ret%':>10}{'RTin_chop':>10}")
    day_close = {}
    for b in bars:
        day_close[b["ts"].date()] = b["close"]
    days = sorted(day_close)
    for lb in [30, 60, 90]:
        for band in [5, 10, 15]:
            lab = {}
            for i, d in enumerate(days):
                if i < lb:
                    lab[d] = "warmup"; continue
                r = day_close[d] / day_close[days[i - lb]] - 1
                lab[d] = "bull" if r > band / 100 else ("bear" if r < -band / 100 else "chop")
            idx = [i for i, b in enumerate(bars) if lab[b["ts"].date()] == "chop"]
            sr = hr = 1.0
            for i in idx:
                sr *= (1 + base["rets"][i]); hr *= (1 + hret[i])
            rt_chop = sum(1 for (ent, ex, r) in base["trades"] if lab[ent.date()] == "chop")
            print(f"{lb:>10}{band:>7}{100*len(idx)/len(bars):>10.1f}{(sr-1)*100:>11.1f}{(hr-1)*100:>10.1f}{rt_chop:>10}")

    # E2: chop round-trip return distribution (base labeling 60d/10%)
    lb, band = 60, 10
    lab = {}
    for i, d in enumerate(days):
        lab[d] = "warmup" if i < lb else ("bull" if day_close[d]/day_close[days[i-lb]]-1 > band/100 else ("bear" if day_close[d]/day_close[days[i-lb]]-1 < -band/100 else "chop"))
    chop_rt = sorted(r for (ent, ex, r) in base["trades"] if lab[ent.date()] == "chop")
    print(f"\n=== E2. CHOP ROUND-TRIPS (base 60d/10%): n={len(chop_rt)} ===")
    if chop_rt:
        print("returns%:", [round(r*100, 1) for r in chop_rt])
        wins = [r for r in chop_rt if r > 0]
        print(f"win-rate={100*len(wins)/len(chop_rt):.0f}%  sum={sum(chop_rt)*100:+.1f}%  "
              f"top2_share={sum(chop_rt[-2:])/sum(chop_rt)*100:.0f}% of positive sum" if sum(chop_rt) else "")

    # E3: intra-chop directional bursts (contiguous chop segments)
    segs = []
    cur = None
    for i, b in enumerate(bars):
        R = lab[b["ts"].date()]
        if R == "chop":
            if cur is None:
                cur = [i, i]
            else:
                cur[1] = i
        else:
            if cur:
                segs.append(tuple(cur)); cur = None
    if cur:
        segs.append(tuple(cur))
    runs_up, runs_dn = [], []
    for a, z in segs:
        c0 = bars[a]["close"]
        hi = max(bars[j]["high"] for j in range(a, z + 1))
        lo = min(bars[j]["low"] for j in range(a, z + 1))
        runs_up.append((hi - c0) / c0 * 100); runs_dn.append((lo - c0) / c0 * 100)
    runs_up.sort(); runs_dn.sort()
    print(f"\n=== E3. INTRA-CHOP BURSTS: {len(segs)} chop segments ===")
    if segs:
        print(f"run-up%  median={st.median(runs_up):.1f} p90={runs_up[int(0.9*len(runs_up))]:.1f} max={runs_up[-1]:.1f}")
        print(f"run-down% median={st.median(runs_dn):.1f} p90={runs_dn[int(0.1*len(runs_dn))]:.1f} max={runs_dn[0]:.1f}")


if __name__ == "__main__":
    main()
