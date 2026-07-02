"""Wick-Test v6 - OOS + FEE validation of the v5 lead (BC-long + strength) on 15m multi-regime.
CONFIRMATORY: fixed mechanism, no re-sweep. See PRE_REGISTRATION_v6.md. k=1 causal. Read-only.
Regime (ema200_pos_slope) computed on the traded 15m TF for the informational split.
"""
from __future__ import annotations
import math, os, random, sqlite3
from statistics import mean

DATA_DIR = os.environ.get("WICK_DATA_DIR", r"C:/Users/AA Incorporado/cc/data")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DB_KEY = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
_15M = 900_000
ATR_N = 14
KS = [1.0, 1.5, 2.0]
TARGET = 2.0
DR_WINDOW = 3
MAX_HOLD = 100
WARMUP = 16
COST_BASE = 0.00058          # 2x taker(0.00019) + 2x slippage(0.0001) round-trip
COST_TAKER = 0.00038         # taker-only sensitivity
NULL_RUNS = 200; NULL_PCT = 95; NULL_SEED = 20260702
REGIMES = ("up", "down", "range")
IS_FRAC = 0.6
GATE_MIN_N = 100; GATE_POOLED_NET = 0.05; GATE_MIN_COINS = 2


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


def atr14(m):
    h, l, c = m["h"], m["l"], m["c"]; n = m["n"]; tr = [0.0] * n
    for i in range(n):
        tr[i] = (h[i] - l[i]) if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = [None] * n; s = 0.0
    for i in range(n):
        s += tr[i]
        if i >= ATR_N: s -= tr[i - ATR_N]
        if i >= ATR_N - 1: out[i] = s / ATR_N
    return out


def regime_series(m):
    closes = m["c"]; em = ema(closes, 200); K = 32; lab = [None] * len(closes)
    for i in range(len(closes)):
        if i >= K:
            rising = em[i] > em[i - K]
            if closes[i] > em[i] and rising:       lab[i] = "up"
            elif closes[i] < em[i] and not rising:  lab[i] = "down"
            else:                                   lab[i] = "range"
    return lab


def gen_setups(m, side, atr, reg):
    o, h, l, c, n = m["o"], m["h"], m["l"], m["c"], m["n"]
    setups = []; triggered = 0
    for b3 in range(WARMUP, n):
        c1, c2 = b3 - 2, b3 - 1
        if side == "long":
            if not (c[c1] > o[c1] and c[c2] > o[c2]): continue
            L = max(h[c1], h[c2])
            if not (h[b3] > L and c[b3] > L): continue
            E = h[b3]
        else:
            if not (c[c1] < o[c1] and c[c2] < o[c2]): continue
            L = min(l[c1], l[c2])
            if not (l[b3] < L and c[b3] < L): continue
            E = l[b3]
        if atr[b3] is None or atr[b3] <= 0: continue
        triggered += 1
        bc_fill = None
        for f in range(b3 + 1, min(b3 + DR_WINDOW, n - 1) + 1):
            if (side == "long" and h[f] >= E) or (side == "short" and l[f] <= E):
                bc_fill = f; break
        rng = h[b3] - l[b3]; body = abs(c[b3] - o[b3])
        if rng > 0:
            strong = body >= atr[b3] and ((c[b3] - l[b3]) >= (2 / 3) * rng if side == "long"
                                          else (h[b3] - c[b3]) >= (2 / 3) * rng)
        else:
            strong = False
        setups.append({"b3": b3, "E": E, "atr": atr[b3], "bc_fill": bc_fill,
                       "strong": strong, "regime": reg[b3]})
    return setups, triggered


def sim_hard(side, entry, line, tp, target, rp, fk, m):
    h, l, c = m["h"], m["l"], m["c"]; end = min(fk + MAX_HOLD, m["n"] - 1)
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


def sim_body(side, entry, line, tp, target, rp, fk, m):
    h, l, c = m["h"], m["l"], m["c"]; end = min(fk + MAX_HOLD, m["n"] - 1)
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


def fee_R(entry, rp, cost):
    return cost * entry / rp


def build(setups, side, k, filt, m, split_idx):
    out = []; busy = -1
    for s in setups:
        if s["bc_fill"] is None: continue
        if filt == "strength" and not s["strong"]: continue
        entry = s["E"]; rp = k * s["atr"]
        line = entry - rp if side == "long" else entry + rp
        f = s["bc_fill"]
        if f <= busy: continue
        tp = entry + TARGET * rp if side == "long" else entry - TARGET * rp
        gR, go, gx = sim_body(side, entry, line, tp, TARGET, rp, f, m)
        hR, ho, hx = sim_hard(side, entry, line, tp, TARGET, rp, f, m)
        out.append({"regime": s["regime"], "gross": gR, "go": go, "hard": hR,
                    "entry": entry, "rp": rp, "seg": "IS" if f < split_idx else "OOS"})
        busy = hx
    return out


def net_avg(trades, cost):
    if not trades: return float("nan")
    return mean(t["gross"] - fee_R(t["entry"], t["rp"], cost) for t in trades)


def null_net_p95(m, side, k, cost, n_target, atr, rng):
    if n_target == 0: return float("nan")
    n = m["n"]; c = m["c"]; means = []
    valid = [i for i in range(n - 2) if atr[i] is not None and atr[i] > 0]
    if len(valid) < 5: return float("nan")
    for _ in range(NULL_RUNS):
        rs = []
        for _ in range(n_target):
            j = valid[rng.randrange(len(valid))]
            entry = c[j]; rp = k * atr[j]
            line = entry - rp if side == "long" else entry + rp
            tp = entry + TARGET * rp if side == "long" else entry - TARGET * rp
            g = sim_body(side, entry, line, tp, TARGET, rp, j + 1, m)[0]
            rs.append(g - fee_R(entry, rp, cost))
        means.append(mean(rs))
    means.sort(); idx = (NULL_PCT / 100) * (len(means) - 1)
    lo = int(idx); hi = min(lo + 1, len(means) - 1)
    return means[lo] * (1 - (idx - lo)) + means[hi] * (idx - lo)


def count_gaps(ts):
    return sum(1 for i in range(len(ts) - 1) if ts[i + 1] - ts[i] != _15M)


def main():
    rng = random.Random(NULL_SEED)
    print("=" * 108)
    print("WICK-TEST v6 - OOS+FEE validation of BC-long+strength on 15m multi-regime. See PRE_REGISTRATION_v6.md")
    print(f"  fee model NET base COST_FRAC={COST_BASE} (2 taker + 2 slip); taker-only={COST_TAKER}")
    print("=" * 108)
    data = {}
    for coin in COINS:
        m = load(coin, "bars_15m"); atr = atr14(m); reg = regime_series(m)
        days = (m["ts"][-1] - m["ts"][0]) / 86_400_000
        drift = (m["c"][-1] / m["c"][0] - 1) * 100
        dist = {}
        for r in reg:
            dist[r] = dist.get(r, 0) + 1
        split = int(IS_FRAC * m["n"])
        data[coin] = dict(m=m, atr=atr, reg=reg, days=days, drift=drift, split=split, dist=dist)
        print(f"[{coin}] 15m n={m['n']} ({days:.0f}d, gaps={count_gaps(m['ts'])}) drift={drift:+.1f}% "
              f"regime={{up:{dist.get('up',0)},down:{dist.get('down',0)},range:{dist.get('range',0)}}}")

    su = {}
    for coin in COINS:
        d = data[coin]
        su[(coin, "long")] = gen_setups(d["m"], "long", d["atr"], d["reg"])[0]
        su[(coin, "short")] = gen_setups(d["m"], "short", d["atr"], d["reg"])[0]

    print("\n" + "=" * 108)
    print("BC-LONG on 15m - GROSS vs NET avgR, net-null gate  (2R, body-close; filter none vs strength)")
    print("=" * 108)
    print(f"  {'coin':7s}{'filt':9s}{'k':>4s}{'n':>6s}{'WR':>7s}{'grossR':>9s}"
          f"{'netR@taker':>11s}{'netR@base':>11s}{'net-null':>10s}{'beats':>7s}")
    results = []
    for coin in COINS:
        d = data[coin]
        for filt in ("none", "strength"):
            for k in KS:
                tr = build(su[(coin, "long")], "long", k, filt, d["m"], d["split"])
                n = len(tr)
                if n == 0:
                    continue
                wr = sum(1 for t in tr if t["go"] == "win") / n
                g = mean(t["gross"] for t in tr)
                nt = net_avg(tr, COST_TAKER); nb = net_avg(tr, COST_BASE)
                p95 = null_net_p95(d["m"], "long", k, COST_BASE, n, d["atr"], rng) if nb > 0 else float("nan")
                beats = "PASS" if (nb > 0 and not math.isnan(p95) and nb >= p95) else "no"
                results.append(dict(coin=coin, filt=filt, k=k, n=n, tr=tr, gross=g, nett=nt,
                                    netb=nb, p95=p95, beats=beats))
                thin = "*" if n < GATE_MIN_N else " "
                ps = f"{p95:+.3f}" if not math.isnan(p95) else "  -- "
                print(f"  {coin:7s}{filt:9s}{k:>4}{n:>5d}{thin}{wr*100:>6.1f}{g:>+9.3f}"
                      f"{nt:>+11.3f}{nb:>+11.3f}{ps:>10s}{beats:>7s}")

    print("\n" + "=" * 108)
    print("IN-SAMPLE / OUT-OF-SAMPLE (60/40 chrono) - BC-long+strength NET@base avgR (sign-stability)")
    print("=" * 108)
    print(f"  {'coin':7s}{'k':>4s}{'IS n/netR':>18s}{'OOS n/netR':>18s}{'stable(both>0)':>16s}")
    for coin in COINS:
        for k in KS:
            r = next((x for x in results if x["coin"] == coin and x["filt"] == "strength" and x["k"] == k), None)
            if not r:
                continue
            isr = [t for t in r["tr"] if t["seg"] == "IS"]; oosr = [t for t in r["tr"] if t["seg"] == "OOS"]
            isn = net_avg(isr, COST_BASE); oon = net_avg(oosr, COST_BASE)
            stable = (not math.isnan(isn)) and (not math.isnan(oon)) and isn > 0 and oon > 0
            iss = f"n={len(isr)} {isn:+.3f}" if isr else "n=0 --"
            oos = f"n={len(oosr)} {oon:+.3f}" if oosr else "n=0 --"
            print(f"  {coin:7s}{k:>4}{iss:>18s}{oos:>18s}{str(stable):>16s}")

    print("\n" + "=" * 108)
    print("REGIME SPLIT (informational) - BC-long+strength k=1.5 NET@base avgR by 15m regime")
    print("=" * 108)
    print(f"  {'coin':7s}{'up':>16s}{'range':>16s}{'down':>16s}")
    for coin in COINS:
        r = next((x for x in results if x["coin"] == coin and x["filt"] == "strength" and x["k"] == 1.5), None)
        if not r:
            print(f"  {coin}: n=0"); continue
        def cell(rg):
            sub = [t for t in r["tr"] if t["regime"] == rg]
            return f"n={len(sub)} {net_avg(sub, COST_BASE):+.3f}" if sub else "n=0    --"
        print(f"  {coin:7s}{cell('up'):>16s}{cell('range'):>16s}{cell('down'):>16s}")

    print("\n" + "=" * 108)
    print("BC-SHORT+strength REFERENCE (bear-beta sanity, NOT in gate) - NET@base avgR + beats")
    print("=" * 108)
    for coin in COINS:
        d = data[coin]
        for k in [1.5, 2.0]:
            tr = build(su[(coin, "short")], "short", k, "strength", d["m"], d["split"])
            if not tr: continue
            nb = net_avg(tr, COST_BASE)
            p95 = null_net_p95(d["m"], "short", k, COST_BASE, len(tr), d["atr"], rng) if nb > 0 else float("nan")
            beats = "pass" if (nb > 0 and not math.isnan(p95) and nb >= p95) else "no"
            print(f"  {coin} short k={k}: n={len(tr)} netR={nb:+.3f} null={p95 if not math.isnan(p95) else 'na'} -> {beats}")

    # GATE
    print("\n" + "=" * 108)
    print("SUCCESS GATE (pre-registered) - NET, OOS-stable, multi-coin")
    print("=" * 108)
    coin_pass = {}
    pooled_cells = []
    for coin in COINS:
        cells = [r for r in results if r["coin"] == coin and r["filt"] == "strength" and r["n"] >= GATE_MIN_N]
        pos = [r for r in cells if r["netb"] > 0]
        beats = [r for r in cells if r["beats"] == "PASS"]
        passed = len(pos) >= 2 and len(beats) >= 1
        coin_pass[coin] = passed
        if passed:
            pooled_cells += [r["netb"] for r in beats]
        print(f"  {coin}: k-cells n>=100={len(cells)}, netR>0 in {len(pos)}/3, beats-null in {len(beats)} "
              f"-> coin {'PASS' if passed else 'fail'}")
    passing_coins = [c for c in COINS if coin_pass[c]]
    # OOS stability for passing coins (any k with both IS>0 and OOS>0)
    stable_ok = True
    for coin in passing_coins:
        d = data[coin]; any_stable = False
        for k in KS:
            r = next((x for x in results if x["coin"] == coin and x["filt"] == "strength" and x["k"] == k), None)
            if not r: continue
            isr = [t for t in r["tr"] if t["seg"] == "IS"]; oosr = [t for t in r["tr"] if t["seg"] == "OOS"]
            if isr and oosr and net_avg(isr, COST_BASE) > 0 and net_avg(oosr, COST_BASE) > 0:
                any_stable = True
        if not any_stable:
            stable_ok = False
    pooled = mean(pooled_cells) if pooled_cells else float("nan")
    c1 = len(passing_coins) >= GATE_MIN_COINS
    c2 = stable_ok and len(passing_coins) >= GATE_MIN_COINS
    c3 = (not math.isnan(pooled)) and pooled >= GATE_POOLED_NET
    print(f"\n  passing coins: {passing_coins} [need>={GATE_MIN_COINS}] -> {c1}")
    print(f"  IS&OOS both net>0 for each passing coin: {stable_ok} -> {c2}")
    print(f"  pooled NET avgR of beats-null cells: {pooled:+.3f} [need>=+{GATE_POOLED_NET}] -> {c3}")
    verdict = "PASS" if (c1 and c2 and c3) else "FAIL"
    print(f"\n  >>> v6 VERDICT: {verdict} <<<")
    if verdict == "PASS":
        print("  -> long-continuation edge is real, net-positive, OOS-stable, multi-coin -> advance to paper/live validation.")
    else:
        print("  -> v5 3m signal did NOT survive TF-transfer + OOS + fees -> WICK TEST RETIRED for good.")
    print("\nDONE.")


if __name__ == "__main__":
    main()
