"""Wick-Test v5 - Runner-Capture (breakout-continuation) spike (GROSS R).
BC_stop entry (fills on continuation) head-to-head vs DR_limit entry (fills on pullback), identical setups.
See PRE_REGISTRATION_v5.md. Read-only; k=1 causal; 3m; 4 coins; 47-81d bear (no multiregime 3m available).
Regime (ema200_pos_slope, INFORMATIONAL) copied verbatim from live-parity regime_filter.py.
"""
from __future__ import annotations
import bisect, math, os, random, sqlite3
from statistics import mean

DATA_DIR = os.environ.get("WICK_DATA_DIR", r"C:/Users/AA Incorporado/cc/data")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DB_KEY = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
_3M = 180_000; _15M = 900_000
ATR_N = 14
KS = [1.0, 1.5, 2.0]
TARGET = 2.0
DR_WINDOW = 3
BUFFER = 0.0005
MAX_HOLD = 100
WARMUP = 22
NULL_RUNS = 200; NULL_PCT = 95; NULL_SEED = 20260702
REGIMES = ("up", "down", "range")
ENTRIES = ["DR", "BC"]
FILTERS = ["none", "strength"]
SIDES = ["long", "short"]
GATE_MIN_N = 100; GATE_MIN_CELLS = 3; GATE_MIN_COINS = 2; GATE_POOLED_AVGR = 0.15


def load(coin, table):
    con = sqlite3.connect(os.path.join(DATA_DIR, f"{DB_KEY[coin]}_scalping.db"))
    rows = con.execute(f"SELECT ts,open,high,low,close FROM {table} WHERE open IS NOT NULL AND "
                       f"high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL ORDER BY ts").fetchall()
    con.close()
    return {"ts": [int(r[0]) * 1000 for r in rows], "o": [float(r[1]) for r in rows],
            "h": [float(r[2]) for r in rows], "l": [float(r[3]) for r in rows],
            "c": [float(r[4]) for r in rows], "n": len(rows)}


def ema(vals, span):
    a = 2.0 / (span + 1); e = None; out = []
    for c in vals:
        e = c if e is None else a * c + (1 - a) * e
        out.append(e)
    return out


def atr14(m3):
    h, l, c = m3["h"], m3["l"], m3["c"]; n = m3["n"]; tr = [0.0] * n
    for i in range(n):
        tr[i] = (h[i] - l[i]) if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = [None] * n; s = 0.0
    for i in range(n):
        s += tr[i]
        if i >= ATR_N: s -= tr[i - ATR_N]
        if i >= ATR_N - 1: out[i] = s / ATR_N
    return out


def regime_labels_15m(m15):
    closes = m15["c"]; em = ema(closes, 200); K = 32
    cts = [t + _15M for t in m15["ts"]]; lab = [None] * len(closes)
    for i in range(len(closes)):
        if i >= K:
            rising = em[i] > em[i - K]
            if closes[i] > em[i] and rising:       lab[i] = "up"
            elif closes[i] < em[i] and not rising:  lab[i] = "down"
            else:                                   lab[i] = "range"
    return cts, lab


def regime_3m(m3, cts, lab):
    out = []
    for t in m3["ts"]:
        j = bisect.bisect_right(cts, t + _3M) - 1
        out.append(lab[j] if j >= 0 else None)
    return out


def gen_setups(m3, side, atr, reg3):
    o, h, l, c, n = m3["o"], m3["h"], m3["l"], m3["c"], m3["n"]
    setups = []; triggered = 0
    for b3 in range(WARMUP, n):
        c1, c2 = b3 - 2, b3 - 1
        if side == "long":
            if not (c[c1] > o[c1] and c[c2] > o[c2]): continue
            L = max(h[c1], h[c2])
            if not (h[b3] > L and c[b3] > L): continue
        else:
            if not (c[c1] < o[c1] and c[c2] < o[c2]): continue
            L = min(l[c1], l[c2])
            if not (l[b3] < L and c[b3] < L): continue
        if atr[b3] is None or atr[b3] <= 0: continue
        triggered += 1
        E = h[b3] if side == "long" else l[b3]        # BC stop level = confirm-bar extreme
        dr_fill = None; bc_fill = None
        for f in range(b3 + 1, min(b3 + DR_WINDOW, n - 1) + 1):
            if dr_fill is None and ((side == "long" and l[f] <= L) or (side == "short" and h[f] >= L)):
                dr_fill = f
            if bc_fill is None and ((side == "long" and h[f] >= E) or (side == "short" and l[f] <= E)):
                bc_fill = f
        rng = h[b3] - l[b3]
        body = abs(c[b3] - o[b3])
        if side == "long":
            strong = body >= atr[b3] and (c[b3] - l[b3]) >= (2 / 3) * rng if rng > 0 else False
        else:
            strong = body >= atr[b3] and (h[b3] - c[b3]) >= (2 / 3) * rng if rng > 0 else False
        setups.append({"b3": b3, "L": L, "E": E, "atr": atr[b3], "b3low": l[b3], "b3high": h[b3],
                       "dr_fill": dr_fill, "bc_fill": bc_fill, "strong": strong, "regime": reg3[b3]})
    return setups, triggered


def entry_of(side, entry_type, s):
    if entry_type == "DR":
        return s["L"], s["dr_fill"]
    return s["E"], s["bc_fill"]


def stop_line(side, entry_type, entry, s, stopcfg):
    kind, k = stopcfg
    if kind == "atr":
        return (entry - k * s["atr"]) if side == "long" else (entry + k * s["atr"])
    # structural: DR = setup-candle extreme; BC = reclaimed level L
    if entry_type == "DR":
        return (min(s["b3low"], s["L"]) - BUFFER * entry) if side == "long" \
            else (max(s["b3high"], s["L"]) + BUFFER * entry)
    return (s["L"] - BUFFER * entry) if side == "long" else (s["L"] + BUFFER * entry)


def sim_hard(side, entry, line, tp, target, rp, fk, m3):
    h, l, c = m3["h"], m3["l"], m3["c"]; end = min(fk + MAX_HOLD, m3["n"] - 1)
    if side == "long" and l[fk] <= line: return (-1.0, "loss", fk)
    if side == "short" and h[fk] >= line: return (-1.0, "loss", fk)
    for i in range(fk + 1, end + 1):
        if side == "long":
            if l[i] <= line: return (-1.0, "loss", i)
            if h[i] >= tp:   return (target, "win", i)
        else:
            if h[i] >= line: return (-1.0, "loss", i)
            if l[i] <= tp:   return (target, "win", i)
    last = c[end]
    return ((last - entry) / rp if side == "long" else (entry - last) / rp, "timeout", end)


def sim_body(side, entry, line, tp, target, rp, fk, m3):
    h, l, c = m3["h"], m3["l"], m3["c"]; end = min(fk + MAX_HOLD, m3["n"] - 1)
    if side == "long" and c[fk] < line: return ((c[fk] - entry) / rp, "loss", fk)
    if side == "short" and c[fk] > line: return ((entry - c[fk]) / rp, "loss", fk)
    for i in range(fk + 1, end + 1):
        if side == "long":
            if c[i] < line: return ((c[i] - entry) / rp, "loss", i)
            if h[i] >= tp:  return (target, "win", i)
        else:
            if c[i] > line: return ((entry - c[i]) / rp, "loss", i)
            if l[i] <= tp:  return (target, "win", i)
    last = c[end]
    return ((last - entry) / rp if side == "long" else (entry - last) / rp, "timeout", end)


def build(setups, side, entry_type, stopcfg, filt, m3, mean_ret, target=TARGET):
    out = []; busy = -1
    for s in setups:
        entry, fill = entry_of(side, entry_type, s)
        if fill is None: continue
        if filt == "strength" and not s["strong"]: continue
        line = stop_line(side, entry_type, entry, s, stopcfg)
        rp = abs(entry - line)
        if rp <= 0: continue
        if fill <= busy: continue
        tp = entry + target * rp if side == "long" else entry - target * rp
        hR, ho, hx = sim_hard(side, entry, line, tp, target, rp, fill, m3)
        bR, bo, bx = sim_body(side, entry, line, tp, target, rp, fill, m3)
        hold = bx - fill
        sdir = 1 if side == "long" else -1
        beta_R = sdir * mean_ret * hold * entry / rp        # de-trend: expected drift move in R
        out.append({"regime": s["regime"], "hR": hR, "ho": ho, "bR": bR, "bo": bo,
                    "alpha": bR - beta_R, "shakeout": (ho == "loss" and bo == "win")})
        busy = hx
    return out


def agg(trades, mode):
    kR = "hR" if mode == "hard" else "bR"; kO = "ho" if mode == "hard" else "bo"
    rs = [t[kR] for t in trades]; n = len(rs)
    if n == 0: return {"n": 0, "wr": float("nan"), "avgR": float("nan"), "totR": 0.0, "alpha": float("nan")}
    return {"n": n, "wr": sum(1 for t in trades if t[kO] == "win") / n, "avgR": sum(rs) / n,
            "totR": sum(rs), "alpha": mean(t["alpha"] for t in trades)}


def null_p95(m3, side, k, target, mode, n_target, atr, rng):
    if n_target == 0: return float("nan")
    n = m3["n"]; c = m3["c"]; means = []
    valid = [i for i in range(n - 2) if atr[i] is not None and atr[i] > 0]
    if len(valid) < 5: return float("nan")
    for _ in range(NULL_RUNS):
        rs = []
        for _ in range(n_target):
            j = valid[rng.randrange(len(valid))]
            entry = c[j]; rp = k * atr[j]
            line = entry - rp if side == "long" else entry + rp
            tp = entry + target * rp if side == "long" else entry - target * rp
            sim = sim_hard if mode == "hard" else sim_body
            rs.append(sim(side, entry, line, tp, target, rp, j + 1, m3)[0])
        means.append(mean(rs))
    means.sort(); idx = (NULL_PCT / 100) * (len(means) - 1)
    lo = int(idx); hi = min(lo + 1, len(means) - 1)
    return means[lo] * (1 - (idx - lo)) + means[hi] * (idx - lo)


def main():
    rng = random.Random(NULL_SEED)
    print("=" * 110)
    print("WICK-TEST v5 - Runner-Capture (BC stop) vs DR limit - GROSS R. See PRE_REGISTRATION_v5.md")
    print("=" * 110)
    data = {}
    for coin in COINS:
        m3 = load(coin, "bars_3m"); m15 = load(coin, "bars_15m")
        cts, lab = regime_labels_15m(m15); reg3 = regime_3m(m3, cts, lab); atr = atr14(m3)
        rets = [m3["c"][i] / m3["c"][i - 1] - 1 for i in range(1, m3["n"])]
        mean_ret = sum(rets) / len(rets)
        days = (m3["ts"][-1] - m3["ts"][0]) / 86_400_000
        drift = (m3["c"][-1] / m3["c"][0] - 1) * 100
        data[coin] = dict(m3=m3, reg3=reg3, atr=atr, mean_ret=mean_ret, days=days, drift=drift)
        print(f"[{coin}] 3m n={m3['n']} ({days:.1f}d) drift={drift:+.1f}% mean_bar_ret={mean_ret*1e4:+.2f}bp")

    setups = {}; trig = {}
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            setups[(coin, side)], trig[(coin, side)] = gen_setups(d["m3"], side, d["atr"], d["reg3"])

    print("\n" + "=" * 110)
    print("FILL RATES on triggered setups: DR (pullback) vs BC (continuation)")
    print("=" * 110)
    for coin in COINS:
        d = data[coin]; wk = d["days"] / 7.0
        for side in SIDES:
            su = setups[(coin, side)]; t = trig[(coin, side)]
            dr = sum(1 for s in su if s["dr_fill"] is not None)
            bc = sum(1 for s in su if s["bc_fill"] is not None)
            both = sum(1 for s in su if s["dr_fill"] is not None and s["bc_fill"] is not None)
            print(f"  {coin} {side:5s}: triggered={t:4d} | DR-fill={dr:4d} ({dr/t*100:4.1f}%, {dr/wk:5.1f}/wk) "
                  f"BC-fill={bc:4d} ({bc/t*100:4.1f}%, {bc/wk:5.1f}/wk) both={both:4d}")

    # fill-matrix diagnostic (k=1.5 none, no one-open gating - raw fill quality)
    print("\n" + "=" * 110)
    print("FILL-MATRIX DIAGNOSTIC (k=1.5 none, raw per-fill BODY avgR) - are continuation-fills the winners?")
    print("=" * 110)
    print(f"  {'coin':7s}{'side':5s}{'DR-fills avgR':>18s}{'BC-fills avgR':>18s}"
          f"{'BOTH: DR vs BC':>24s}")
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            su = setups[(coin, side)]
            dr_rs = []; bc_rs = []; both_dr = []; both_bc = []
            for s in su:
                both = s["dr_fill"] is not None and s["bc_fill"] is not None
                if s["dr_fill"] is not None:
                    e = s["L"]; rp = 1.5 * s["atr"]; ln = e - rp if side == "long" else e + rp
                    tp = e + TARGET * rp if side == "long" else e - TARGET * rp
                    r = sim_body(side, e, ln, tp, TARGET, rp, s["dr_fill"], d["m3"])[0]
                    dr_rs.append(r)
                    if both: both_dr.append(r)
                if s["bc_fill"] is not None:
                    e = s["E"]; rp = 1.5 * s["atr"]; ln = e - rp if side == "long" else e + rp
                    tp = e + TARGET * rp if side == "long" else e - TARGET * rp
                    r = sim_body(side, e, ln, tp, TARGET, rp, s["bc_fill"], d["m3"])[0]
                    bc_rs.append(r)
                    if both: both_bc.append(r)
            dr_s = f"n={len(dr_rs)} {mean(dr_rs):+.3f}" if dr_rs else "n=0 --"
            bc_s = f"n={len(bc_rs)} {mean(bc_rs):+.3f}" if bc_rs else "n=0 --"
            bo_s = f"{mean(both_dr):+.3f} vs {mean(both_bc):+.3f}" if both_dr else "--"
            print(f"  {coin:7s}{side:5s}{dr_s:>18s}{bc_s:>18s}{bo_s:>24s}")

    # main grid
    print("\n" + "=" * 110)
    print("MAIN GRID - GROSS R (2R; BODY=record, HARD=contrast). entry DR=limit@L / BC=stop@confirm-extreme")
    print("=" * 110)
    print(f"  {'coin':7s}{'side':5s}{'entry':6s}{'stop':7s}{'filt':9s}{'n':>5s} | "
          f"{'BODY WR/avgR/tot/alpha beats(null)':^40s} | {'HARD avgR':^10s} | {'shk%':^6s}")
    STOPCFGS = [("atr", 1.0), ("atr", 1.5), ("atr", 2.0), ("struct", None)]
    results = []
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            su = setups[(coin, side)]
            for et in ENTRIES:
                for sc in STOPCFGS:
                    for filt in FILTERS:
                        if sc[0] == "struct" and filt != "none":
                            continue
                        tr = build(su, side, et, sc, filt, d["m3"], d["mean_ret"])
                        b = agg(tr, "body"); hh = agg(tr, "hard")
                        p95 = float("nan"); beats = "-"
                        if b["n"] > 0 and b["avgR"] > 0 and sc[0] == "atr":
                            p95 = null_p95(d["m3"], side, sc[1], TARGET, "body", b["n"], d["atr"], rng)
                            beats = "PASS" if (not math.isnan(p95) and b["avgR"] >= p95) else "no"
                        so = sum(1 for t in tr if t["shakeout"])
                        scn = f"atr{sc[1]}" if sc[0] == "atr" else "struct"
                        results.append(dict(coin=coin, side=side, entry=et, stop=scn, sccfg=sc,
                                            filt=filt, b=b, hh=hh, p95=p95, beats=beats, n=b["n"]))
                        if b["n"] == 0: continue
                        ps = f"{p95:+.3f}" if not math.isnan(p95) else " ref"
                        bc = (f"WR{b['wr']*100:4.1f} {b['avgR']:+.3f} {b['totR']:+6.1f} "
                              f"a{b['alpha']:+.3f} {beats:>4s}({ps})")
                        thin = "*" if 0 < b["n"] < GATE_MIN_N else " "
                        print(f"  {coin:7s}{side:5s}{et:6s}{scn:7s}{filt:9s}{b['n']:>4d}{thin}| "
                              f"{bc:^40s} | {hh['avgR']:^+10.3f} | {so/b['n']*100:^5.1f}")

    # regime split (informational, BC k=1.5 none)
    print("\n" + "=" * 110)
    print("REGIME SPLIT (INFORMATIONAL) - BC entry, BODY avgR by 15m regime, k=1.5 none")
    print("=" * 110)
    print(f"  {'coin':7s}{'side':5s}{'up':>16s}{'range':>16s}{'down':>16s}")
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            tr = build(setups[(coin, side)], side, "BC", ("atr", 1.5), "none", d["m3"], d["mean_ret"])
            def cell(rg):
                rs = [t["bR"] for t in tr if t["regime"] == rg]
                return f"n={len(rs)} {mean(rs):+.3f}" if rs else "n=0    --"
            print(f"  {coin:7s}{side:5s}{cell('up'):>16s}{cell('range'):>16s}{cell('down'):>16s}")

    print("\n" + "=" * 110)
    print("BEAR-BETA CONTEXT - drift per coin (null embeds it; long-alpha is the bear-proof)")
    print("=" * 110)
    for coin in COINS:
        print(f"  {coin}: drift={data[coin]['drift']:+.1f}%")

    # SUCCESS GATE
    print("\n" + "=" * 110)
    print("SUCCESS GATE (pre-registered) - explicit PASS/FAIL (+ long-alpha requirement)")
    print("=" * 110)
    passers = [r for r in results if r["sccfg"][0] == "atr" and r["beats"] == "PASS" and r["n"] >= GATE_MIN_N]
    coins_p = sorted(set(r["coin"] for r in passers)); sides_p = sorted(set(r["side"] for r in passers))
    pooled = mean([r["b"]["avgR"] for r in passers]) if passers else float("nan")
    c1 = len(passers) >= GATE_MIN_CELLS; c2 = len(coins_p) >= GATE_MIN_COINS
    c3 = ("long" in sides_p) and ("short" in sides_p); c3b = "long" in sides_p
    c4 = (not math.isnan(pooled)) and pooled >= GATE_POOLED_AVGR
    print(f"  passing cells (avgR>0 & >=null_p95, n>={GATE_MIN_N}): {len(passers)} [need>={GATE_MIN_CELLS}] -> {c1}")
    for r in passers:
        print(f"      {r['coin']} {r['side']} {r['entry']} {r['stop']} {r['filt']}: n={r['n']} "
              f"avgR={r['b']['avgR']:+.3f} alpha={r['b']['alpha']:+.3f} null={r['p95']:+.3f}")
    print(f"  coins spanned: {coins_p} [need>={GATE_MIN_COINS}] -> {c2}")
    print(f"  both sides: {sides_p} -> {c3}   | LONG-alpha (>=1 long passes): {c3b}")
    print(f"  pooled avgR of passers: {pooled:+.3f} [need>=+{GATE_POOLED_AVGR}] -> {c4}")
    verdict = "PASS" if (c1 and c2 and c3 and c3b and c4) else "FAIL"
    print(f"\n  >>> v5 VERDICT: {verdict} <<<")
    print("\nDONE.")


if __name__ == "__main__":
    main()
