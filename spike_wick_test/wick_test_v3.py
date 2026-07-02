"""Wick-Test v3 (GROSS R) - Deferred-Retest entry + k*ATR stop + body-close of record.
See PRE_REGISTRATION_v3.md for the locked spec. Read-only; k=1 causal; 3m; 4 coins.
Regime (ema200_pos_slope, INFORMATIONAL) copied verbatim from live-parity regime_filter.py.
"""
from __future__ import annotations
import bisect, math, os, random, sqlite3
from statistics import mean, median

DATA_DIR = os.environ.get("WICK_DATA_DIR", r"C:/Users/AA Incorporado/cc/data")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DB_KEY = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
_3M = 180_000; _15M = 900_000
ATR_N = 14
KS = [1.0, 1.5, 2.0]
TARGET = 2.0
DR_WINDOW = 3                 # bars 4-6 = b3+1..b3+3
LIT_BUFFER = 0.0005           # literal stop: a hair beyond setup-candle extreme
DISP_BODY_K = 1.0             # impulse-displacement: body(c1)+body(c2) >= 1.0*ATR
DISP_BREAK_K = 0.25           # close beyond L >= 0.25*ATR
MAX_HOLD = 100
WARMUP = 22
NULL_RUNS = 200; NULL_PCT = 95; NULL_SEED = 20260702
REGIMES = ("up", "down", "range")
FILTERS = ["none", "disp"]
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
        if i >= ATR_N:
            s -= tr[i - ATR_N]
        if i >= ATR_N - 1:
            out[i] = s / ATR_N
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


# ---- setups (DR entry) --------------------------------------------------------

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
        # DR fill within bars 4-6
        fill = None
        for f in range(b3 + 1, min(b3 + DR_WINDOW, n - 1) + 1):
            if (side == "long" and l[f] <= L) or (side == "short" and h[f] >= L):
                fill = f; break
        # impulse-displacement filter
        comb = abs(c[c1] - o[c1]) + abs(c[c2] - o[c2])
        brk = (c[b3] - L) if side == "long" else (L - c[b3])
        disp = comb >= DISP_BODY_K * atr[b3] and brk >= DISP_BREAK_K * atr[b3]
        setups.append({"b3": b3, "L": L, "atr": atr[b3], "b3low": l[b3], "b3high": h[b3],
                       "fill": fill, "disp": disp, "regime": reg3[b3]})
    return setups, triggered


def stop_line(side, L, entry, atrv, b3low, b3high, stopcfg):
    kind, k = stopcfg
    if kind == "atr":
        return (L - k * atrv) if side == "long" else (L + k * atrv)
    # literal: a hair beyond the setup-candle extreme, but strictly beyond entry=L
    if side == "long":
        return min(b3low, L) - LIT_BUFFER * L
    return max(b3high, L) + LIT_BUFFER * L


# ---- sims (GROSS R) -----------------------------------------------------------

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


def build(setups, side, stopcfg, filt, m3, target=TARGET):
    out = []; busy = -1
    for s in setups:
        if s["fill"] is None: continue
        if filt == "disp" and not s["disp"]: continue
        entry = s["L"]
        line = stop_line(side, s["L"], entry, s["atr"], s["b3low"], s["b3high"], stopcfg)
        rp = abs(entry - line)
        if rp <= 0: continue
        f = s["fill"]
        if f <= busy: continue
        tp = entry + target * rp if side == "long" else entry - target * rp
        hR, ho, hx = sim_hard(side, entry, line, tp, target, rp, f, m3)
        bR, bo, bx = sim_body(side, entry, line, tp, target, rp, f, m3)
        out.append({"regime": s["regime"], "rp": rp, "entry": entry, "hR": hR, "ho": ho,
                    "bR": bR, "bo": bo, "shakeout": (ho == "loss" and bo == "win")})
        busy = hx
    return out


def agg(trades, mode):
    kR = "hR" if mode == "hard" else "bR"; kO = "ho" if mode == "hard" else "bo"
    rs = [t[kR] for t in trades]; n = len(rs)
    if n == 0: return {"n": 0, "wr": float("nan"), "avgR": float("nan"), "totR": 0.0}
    return {"n": n, "wr": sum(1 for t in trades if t[kO] == "win") / n,
            "avgR": sum(rs) / n, "totR": sum(rs)}


def null_p95(m3, side, stopcfg, target, mode, n_target, atr, rng):
    if n_target == 0 or stopcfg[0] != "atr": return float("nan")
    k = stopcfg[1]; n = m3["n"]; c = m3["c"]; means = []
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


def quart(v):
    if not v: return (float("nan"),) * 3
    s = sorted(v); n = len(s)
    def q(p):
        i = p * (n - 1); lo = int(i); hi = min(lo + 1, n - 1)
        return s[lo] * (1 - (i - lo)) + s[hi] * (i - lo)
    return (q(0.25), q(0.5), q(0.75))


def main():
    rng = random.Random(NULL_SEED)
    print("=" * 108)
    print("WICK-TEST v3 - GROSS R - DR entry + k*ATR stop + body-close of record. See PRE_REGISTRATION_v3.md")
    print("=" * 108)
    data = {}
    for coin in COINS:
        m3 = load(coin, "bars_3m"); m15 = load(coin, "bars_15m")
        cts, lab = regime_labels_15m(m15); reg3 = regime_3m(m3, cts, lab); atr = atr14(m3)
        days = (m3["ts"][-1] - m3["ts"][0]) / 86_400_000
        drift = (m3["c"][-1] / m3["c"][0] - 1) * 100
        med_runit_pct = median([1.5 * atr[i] / m3["c"][i] * 100 for i in range(m3["n"]) if atr[i]])
        data[coin] = dict(m3=m3, reg3=reg3, atr=atr, days=days, drift=drift, mrp=med_runit_pct)
        print(f"[{coin}] 3m n={m3['n']} ({days:.1f}d) drift(close-close)={drift:+.1f}% "
              f"medR-unit(1.5ATR)={med_runit_pct:.3f}%ofpx")

    setups = {}; trig = {}
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            setups[(coin, side)], trig[(coin, side)] = gen_setups(d["m3"], side, d["atr"], d["reg3"])

    # fill selectivity (vs v2's 100%)
    print("\n" + "=" * 108)
    print("DR FILL SELECTIVITY  (triggered = impulse+confirm; filled = retest to L within bars 4-6)")
    print("=" * 108)
    for coin in COINS:
        d = data[coin]; wk = d["days"] / 7.0
        for side in SIDES:
            su = setups[(coin, side)]; t = trig[(coin, side)]
            filled = sum(1 for s in su if s["fill"] is not None)
            disp = sum(1 for s in su if s["fill"] is not None and s["disp"])
            fr = filled / t * 100 if t else float("nan")
            print(f"  {coin} {side:5s}: triggered={t:4d} filled={filled:4d} ({fr:4.1f}%, {filled/wk:4.1f}/wk) "
                  f"disp-pass={disp:4d} skip={t-filled}")

    # main grid
    print("\n" + "=" * 108)
    print("MAIN GRID - GROSS R  (2R; n=identical fills; BODY=record, HARD=contrast) + shakeout")
    print("=" * 108)
    print(f"  {'coin':7s}{'side':5s}{'stop':7s}{'filt':6s}{'n':>5s} | "
          f"{'BODY WR/avgR/tot beats(null)':^32s} | {'HARD WR/avgR/tot':^22s} | {'shake %/net':^14s}")
    STOPCFGS = [("atr", 1.0), ("atr", 1.5), ("atr", 2.0), ("literal", None)]
    results = []
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            su = setups[(coin, side)]
            for sc in STOPCFGS:
                for filt in FILTERS:
                    if sc[0] == "literal" and filt == "disp":
                        continue                      # literal = reference, filter=none only
                    tr = build(su, side, sc, filt, d["m3"])
                    b = agg(tr, "body"); hh = agg(tr, "hard")
                    p95 = float("nan"); beats = "-"
                    if b["n"] > 0 and b["avgR"] > 0 and sc[0] == "atr":
                        p95 = null_p95(d["m3"], side, sc, TARGET, "body", b["n"], d["atr"], rng)
                        beats = "PASS" if (not math.isnan(p95) and b["avgR"] >= p95) else "no"
                    so = [t for t in tr if t["shakeout"]]
                    netval = sum(t["bR"] - t["hR"] for t in so)
                    scn = f"atr{sc[1]}" if sc[0] == "atr" else "LITrl"
                    results.append(dict(coin=coin, side=side, stop=scn, sccfg=sc, filt=filt,
                                        b=b, hh=hh, p95=p95, beats=beats, n=b["n"],
                                        shake=len(so), netval=netval, regime_tr=tr))
                    if b["n"] == 0:
                        continue
                    ps = f"{p95:+.3f}" if not math.isnan(p95) else "  ref"
                    bcell = f"WR{b['wr']*100:4.1f} {b['avgR']:+.3f} {b['totR']:+6.1f} {beats:>4s}({ps})"
                    hcell = f"WR{hh['wr']*100:4.1f} {hh['avgR']:+.3f} {hh['totR']:+6.1f}"
                    shk = f"{len(so)/b['n']*100:4.1f}% {netval:+6.1f}" if b["n"] else ""
                    thin = "*" if 0 < b["n"] < GATE_MIN_N else " "
                    print(f"  {coin:7s}{side:5s}{scn:7s}{filt:6s}{b['n']:>4d}{thin}| "
                          f"{bcell:^32s} | {hcell:^22s} | {shk:^14s}")

    # DR-skip diagnostic (filter=none; would-have-been at bar-3 close, body-close)
    print("\n" + "=" * 108)
    print("DR-SKIP DIAGNOSTIC - triggered-but-unfilled setups: would-have-been R at bar-3 close (BODY, 2R)")
    print("  Does DR filter WINNERS (would-have-been >0 -> v1 problem) or NOISE (<=0 -> DR helps)?")
    print("=" * 108)
    for k in KS:
        pooled = []
        for coin in COINS:
            d = data[coin]
            for side in SIDES:
                for s in setups[(coin, side)]:
                    if s["fill"] is not None or s["b3"] + 1 >= d["m3"]["n"]: continue
                    entry = d["m3"]["c"][s["b3"]]; rp = k * s["atr"]
                    line = entry - rp if side == "long" else entry + rp
                    tp = entry + TARGET * rp if side == "long" else entry - TARGET * rp
                    R = sim_body(side, entry, line, tp, TARGET, rp, s["b3"] + 1, d["m3"])[0]
                    pooled.append(R)
        a = mean(pooled) if pooled else float("nan")
        print(f"  k={k}: skips={len(pooled)} would-have-been avgR(body)={a:+.4f}  "
              f"(pooled all coins/sides)")
    print("  per coin x side (k=1.5):")
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            rs = []
            for s in setups[(coin, side)]:
                if s["fill"] is not None or s["b3"] + 1 >= d["m3"]["n"]: continue
                entry = d["m3"]["c"][s["b3"]]; rp = 1.5 * s["atr"]
                line = entry - rp if side == "long" else entry + rp
                tp = entry + TARGET * rp if side == "long" else entry - TARGET * rp
                rs.append(sim_body(side, entry, line, tp, TARGET, rp, s["b3"] + 1, d["m3"])[0])
            a = mean(rs) if rs else float("nan")
            print(f"    {coin} {side:5s}: skips={len(rs):4d} would-have avgR={a:+.4f}")

    # realized-loss under body-close (pooled per coin, k=1.5, none)
    print("\n" + "=" * 108)
    print("BODY-CLOSE REALIZED-LOSS DISTRIBUTION (R) - per coin, k=1.5 none (HARD loss=-1R)")
    print("=" * 108)
    for coin in COINS:
        d = data[coin]; losses = []
        for side in SIDES:
            for t in build(setups[(coin, side)], side, ("atr", 1.5), "none", d["m3"]):
                if t["bo"] == "loss": losses.append(t["bR"])
        if losses:
            q = quart(losses)
            print(f"  {coin}: body-losses={len(losses)} q25/med/q75={q[0]:.2f}/{q[1]:.2f}/{q[2]:.2f} "
                  f"worst={min(losses):.2f}")
        else:
            print(f"  {coin}: no body losses")

    # regime split (informational), k=1.5 none, body
    print("\n" + "=" * 108)
    print("REGIME COLUMN SPLIT (INFORMATIONAL) - BODY avgR by 15m regime, k=1.5 none")
    print("=" * 108)
    print(f"  {'coin':7s}{'side':5s}{'up':>16s}{'range':>16s}{'down':>16s}")
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            tr = build(setups[(coin, side)], side, ("atr", 1.5), "none", d["m3"])
            def cell(rg):
                rs = [t["bR"] for t in tr if t["regime"] == rg]
                return f"n={len(rs)} {mean(rs):+.3f}" if rs else "n=0    --"
            print(f"  {coin:7s}{side:5s}{cell('up'):>16s}{cell('range'):>16s}{cell('down'):>16s}")

    # bear-beta context
    print("\n" + "=" * 108)
    print("BEAR-BETA CONTEXT - window drift + passive-short R-equiv (null p95 embeds drift; both-sides gate)")
    print("=" * 108)
    for coin in COINS:
        d = data[coin]
        pshort = (-d["drift"] / 100) / (d["mrp"] / 100)
        print(f"  {coin}: drift={d['drift']:+.1f}%  passive-short whole-window ~{pshort:+.1f}R "
              f"(1.5ATR units)  -> shorts inherit drift; must beat direction-matched null")

    # ---- SUCCESS GATE ----
    print("\n" + "=" * 108)
    print("SUCCESS GATE (pre-registered) - explicit PASS/FAIL")
    print("=" * 108)
    passers = [r for r in results if r["sccfg"][0] == "atr" and r["beats"] == "PASS" and r["n"] >= GATE_MIN_N]
    coins_p = sorted(set(r["coin"] for r in passers))
    sides_p = sorted(set(r["side"] for r in passers))
    pooled_avg = mean([r["b"]["avgR"] for r in passers]) if passers else float("nan")
    c1 = len(passers) >= GATE_MIN_CELLS
    c2 = len(coins_p) >= GATE_MIN_COINS
    c3 = ("long" in sides_p) and ("short" in sides_p)
    c4 = (not math.isnan(pooled_avg)) and pooled_avg >= GATE_POOLED_AVGR
    print(f"  passing cells (avgR>0 & >=null_p95, n>={GATE_MIN_N}): {len(passers)}  [need >={GATE_MIN_CELLS}]  -> {c1}")
    for r in passers:
        print(f"      {r['coin']} {r['side']} {r['stop']} {r['filt']}: n={r['n']} "
              f"avgR={r['b']['avgR']:+.3f} null={r['p95']:+.3f}")
    print(f"  coins spanned: {coins_p}  [need >={GATE_MIN_COINS}]  -> {c2}")
    print(f"  both sides represented: long={'long' in sides_p} short={'short' in sides_p}  -> {c3}")
    print(f"  pooled avgR of passers: {pooled_avg:+.3f}  [need >=+{GATE_POOLED_AVGR}]  -> {c4}")
    verdict = "PASS" if (c1 and c2 and c3 and c4) else "FAIL"
    print(f"\n  >>> v3 VERDICT: {verdict} <<<")
    if verdict == "FAIL":
        print("  Ledger: WICK TEST RETIRED - three independent strikes (v1 no edge, v2 gross-negative, v3 fail).")
    else:
        print("  -> advance to fee-modeled + longer/OOS-window validation.")
    print("\nDONE.")


if __name__ == "__main__":
    main()
