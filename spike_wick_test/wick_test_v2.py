"""Wick-Test v2 (GROSS R) - pre-positioned limit + body-close exit mode.
See PRE_REGISTRATION_v2.md for the locked spec. Read-only; k=1 causal; 3m bars; all 4 coins.
Regime (ema200_pos_slope, INFORMATIONAL column) copied verbatim from live-parity regime_filter.py.
"""
from __future__ import annotations
import bisect, math, os, random, sqlite3
from statistics import mean, median

DATA_DIR = os.environ.get("WICK_DATA_DIR", r"C:/Users/AA Incorporado/cc/data")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DB_KEY = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
_3M = 180_000
_15M = 900_000
STOP_FRAC = 0.001          # line = L -/+ 0.001*entry ; rp = 0.1% of price (constant by construction)
MAX_HOLD = 100             # 5h resolution cap
TARGETS = [1.0, 1.5, 2.0]
SIDES = ["long", "short"]
FILTERS = ["mom", "control"]
REGIMES = ("up", "down", "range")
MOM_LOOKBACK = 10          # net change over last 10 3m closes
MOM_BODY_N = 20            # bars 1,2 body >= median body of prior 20 bars
WARMUP = 22
NULL_RUNS = 200
NULL_PCT = 95
NULL_SEED = 20260702


def load(coin, table):
    con = sqlite3.connect(os.path.join(DATA_DIR, f"{DB_KEY[coin]}_scalping.db"))
    rows = con.execute(
        f"SELECT ts,open,high,low,close FROM {table} WHERE open IS NOT NULL AND high IS NOT NULL "
        f"AND low IS NOT NULL AND close IS NOT NULL ORDER BY ts").fetchall()
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


def regime_labels_15m(m15):
    closes = m15["c"]; em = ema(closes, 200); K = 32
    close_ts = [t + _15M for t in m15["ts"]]; labels = [None] * len(closes)
    for i in range(len(closes)):
        if i >= K:
            rising = em[i] > em[i - K]
            if closes[i] > em[i] and rising:       labels[i] = "up"
            elif closes[i] < em[i] and not rising:  labels[i] = "down"
            else:                                   labels[i] = "range"
    return close_ts, labels


def regime_3m(m3, close_ts, labels):
    out = []
    for t in m3["ts"]:
        j = bisect.bisect_right(close_ts, t + _3M) - 1
        out.append(labels[j] if j >= 0 else None)
    return out


# ---- signal generation (v2 pattern) -------------------------------------------

def gen_signals(m3, side, reg3):
    """Returns (fills, counts). fills = list of dicts for filled bar-3 setups:
    {k, entry=L, line, rp, mom, regime, ambig}. counts = trigger/fill/bar4 tallies."""
    o, h, l, c = m3["o"], m3["h"], m3["l"], m3["c"]; n = m3["n"]
    fills = []; triggered = 0; filled = 0; bar4 = 0
    for k in range(WARMUP, n):
        c1, c2 = k - 2, k - 1
        if side == "long":
            if not (c[c1] > o[c1] and c[c2] > o[c2]):
                continue
            L = max(h[c1], h[c2])
            if not (h[k] > L):                      # trigger
                continue
            triggered += 1
            if not (l[k] <= L):                     # fill needs the retest tap within bar 3
                if k + 1 < n and l[k + 1] <= L:
                    bar4 += 1
                continue
            entry = L; line = L - STOP_FRAC * entry
            ambig = l[k] <= line                    # same-bar stop+fill
        else:
            if not (c[c1] < o[c1] and c[c2] < o[c2]):
                continue
            L = min(l[c1], l[c2])
            if not (l[k] < L):
                continue
            triggered += 1
            if not (h[k] >= L):
                if k + 1 < n and h[k + 1] >= L:
                    bar4 += 1
                continue
            entry = L; line = L + STOP_FRAC * entry
            ambig = h[k] >= line
        filled += 1
        rp = STOP_FRAC * entry
        mom = mom_pass(m3, side, k)
        fills.append({"k": k, "entry": entry, "line": line, "rp": rp,
                      "mom": mom, "regime": reg3[k], "ambig": ambig})
    return fills, {"triggered": triggered, "filled": filled, "bar4": bar4}


def mom_pass(m3, side, k):
    o, c = m3["o"], m3["c"]
    net10 = c[k - 1] - c[k - 1 - MOM_LOOKBACK]
    if side == "long" and not (net10 > 0):
        return False
    if side == "short" and not (net10 < 0):
        return False
    bodies = [abs(c[j] - o[j]) for j in range(k - 2 - MOM_BODY_N, k - 2)]
    med = median(bodies) if bodies else 0.0
    b1 = abs(c[k - 2] - o[k - 2]); b2 = abs(c[k - 1] - o[k - 1])
    return b1 >= med and b2 >= med


# ---- exit-mode simulators (GROSS R) -------------------------------------------

def sim_hard(side, entry, line, tp, target, rp, fk, m3):
    h, l, c = m3["h"], m3["l"], m3["c"]; end = min(fk + MAX_HOLD, m3["n"] - 1)
    if side == "long" and l[fk] <= line:            # same-bar stop+fill -> stop-first
        return (-1.0, "loss", fk)
    if side == "short" and h[fk] >= line:
        return (-1.0, "loss", fk)
    for i in range(fk + 1, end + 1):
        if side == "long":
            if l[i] <= line:  return (-1.0, "loss", i)
            if h[i] >= tp:    return (target, "win", i)
        else:
            if h[i] >= line:  return (-1.0, "loss", i)
            if l[i] <= tp:    return (target, "win", i)
    last = c[end]
    return ((last - entry) / rp if side == "long" else (entry - last) / rp, "timeout", end)


def sim_body(side, entry, line, tp, target, rp, fk, m3):
    """Returns (R, outcome, exit_idx, tp_first_flag). Body-close loss can be < -1R.
    tp_first_flag = a loss bar whose high/low also reached tp (would-have-won under TP-intrabar)."""
    h, l, c = m3["h"], m3["l"], m3["c"]; end = min(fk + MAX_HOLD, m3["n"] - 1)
    if side == "long" and c[fk] < line:
        return ((c[fk] - entry) / rp, "loss", fk, h[fk] >= tp)
    if side == "short" and c[fk] > line:
        return ((entry - c[fk]) / rp, "loss", fk, l[fk] <= tp)
    for i in range(fk + 1, end + 1):
        if side == "long":
            if c[i] < line:   return ((c[i] - entry) / rp, "loss", i, h[i] >= tp)
            if h[i] >= tp:    return (target, "win", i, False)
        else:
            if c[i] > line:   return ((entry - c[i]) / rp, "loss", i, l[i] <= tp)
            if l[i] <= tp:    return (target, "win", i, False)
    last = c[end]
    return ((last - entry) / rp if side == "long" else (entry - last) / rp, "timeout", end, False)


# ---- build trades (identical fills; both modes) -------------------------------

def build(fills, side, target, m3, filt):
    """One-open-at-a-time gated by HARD exit. Each taken fill simulated in BOTH modes."""
    out = []; busy = -1
    for s in fills:
        if filt == "mom" and not s["mom"]:
            continue
        if s["k"] <= busy:
            continue
        entry, line, rp = s["entry"], s["line"], s["rp"]
        tp = entry + target * rp if side == "long" else entry - target * rp
        hR, ho, hx = sim_hard(side, entry, line, tp, target, rp, s["k"], m3)
        bR, bo, bx, tpf = sim_body(side, entry, line, tp, target, rp, s["k"], m3)
        out.append({"regime": s["regime"], "ambig": s["ambig"], "rp": rp, "entry": entry,
                    "hR": hR, "ho": ho, "bR": bR, "bo": bo, "b_realloss": (bR if bo == "loss" else None),
                    "tp_first": tpf, "shakeout": (ho == "loss" and bo == "win")})
        busy = hx
    return out


def agg(trades, mode):
    key = "hR" if mode == "hard" else "bR"
    okey = "ho" if mode == "hard" else "bo"
    rs = [t[key] for t in trades]; n = len(rs)
    if n == 0:
        return {"n": 0, "wr": float("nan"), "avgR": float("nan"), "totR": 0.0}
    wins = sum(1 for t in trades if t[okey] == "win")
    return {"n": n, "wr": wins / n, "avgR": sum(rs) / n, "totR": sum(rs)}


# ---- null (per exit mode) -----------------------------------------------------

def null_p95(m3, side, target, mode, n_target, rng):
    if n_target == 0:
        return float("nan")
    n = m3["n"]; c = m3["c"]; means = []
    for _ in range(NULL_RUNS):
        rs = []
        for _ in range(n_target):
            j = rng.randrange(0, n - 2)
            entry = c[j]; rp = STOP_FRAC * entry
            line = entry - rp if side == "long" else entry + rp
            tp = entry + target * rp if side == "long" else entry - target * rp
            if mode == "hard":
                R = sim_hard(side, entry, line, tp, target, rp, j + 1, m3)[0]
            else:
                R = sim_body(side, entry, line, tp, target, rp, j + 1, m3)[0]
            rs.append(R)
        means.append(mean(rs))
    means.sort(); idx = (NULL_PCT / 100) * (len(means) - 1)
    lo = int(idx); hi = min(lo + 1, len(means) - 1)
    return means[lo] * (1 - (idx - lo)) + means[hi] * (idx - lo)


def quart(v):
    if not v:
        return (float("nan"),) * 3
    s = sorted(v); n = len(s)
    def q(p):
        i = p * (n - 1); lo = int(i); hi = min(lo + 1, n - 1)
        return s[lo] * (1 - (i - lo)) + s[hi] * (i - lo)
    return (q(0.25), q(0.5), q(0.75))


def main():
    rng = random.Random(NULL_SEED)
    print("=" * 104)
    print("WICK-TEST v2 - GROSS R - pre-positioned limit + body-close exit. See PRE_REGISTRATION_v2.md")
    print("=" * 104)
    data = {}
    for coin in COINS:
        m3 = load(coin, "bars_3m"); m15 = load(coin, "bars_15m")
        cts, lab = regime_labels_15m(m15); reg3 = regime_3m(m3, cts, lab)
        days = (m3["ts"][-1] - m3["ts"][0]) / 86_400_000
        data[coin] = dict(m3=m3, reg3=reg3, days=days)
        dist = {}
        for r in reg3:
            dist[r] = dist.get(r, 0) + 1
        print(f"[{coin}] 3m n={m3['n']} ({days:.1f}d) regime(info)={dist}")

    # signals + fill / ambiguity / bar-4
    print("\n" + "=" * 104)
    print("TRIGGER/FILL FREQUENCY  (fill rate on TRIGGERED setups; vs v1 58-68%)")
    print("=" * 104)
    allfills = {}
    for coin in COINS:
        d = data[coin]; wk = d["days"] / 7.0
        for side in SIDES:
            fills, cnt = gen_signals(d["m3"], side, d["reg3"])
            allfills[(coin, side)] = fills
            fr = cnt["filled"] / cnt["triggered"] * 100 if cnt["triggered"] else float("nan")
            amb = sum(1 for f in fills if f["ambig"])
            mom = sum(1 for f in fills if f["mom"])
            print(f"  {coin} {side:5s}: triggered={cnt['triggered']:4d} filled={cnt['filled']:4d} "
                  f"({fr:4.1f}%, {cnt['filled']/wk:4.1f}/wk) mom-pass={mom:4d} "
                  f"ambig(samebar stop+fill)={amb:3d} bar4-would-fill={cnt['bar4']:3d}")

    # main grid
    print("\n" + "=" * 104)
    print("MAIN GRID - GROSS R  (n = identical fills for both modes; HARD vs BODY-CLOSE)")
    print("=" * 104)
    print(f"  {'coin':7s}{'side':5s}{'tgt':4s}{'filt':8s}{'n':>5s}  | "
          f"{'HARD WR/avgR/totR beats(null)':^34s} | {'BODY WR/avgR/totR beats(null)':^36s}")
    results = []
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            fills = allfills[(coin, side)]
            for tgt in TARGETS:
                for filt in FILTERS:
                    tr = build(fills, side, tgt, d["m3"], filt)
                    row = {"coin": coin, "side": side, "tgt": tgt, "filt": filt, "trades": tr}
                    cells = {}
                    for mode in ("hard", "body"):
                        a = agg(tr, mode); p95 = float("nan"); beats = "-"
                        if a["n"] > 0 and a["avgR"] > 0:
                            p95 = null_p95(d["m3"], side, tgt, mode, a["n"], rng)
                            beats = "YES" if (not math.isnan(p95) and a["avgR"] >= p95) else "no"
                        cells[mode] = (a, p95, beats)
                        row[mode] = (a, p95, beats)
                    results.append(row)
                    n = cells["hard"][0]["n"]
                    def fmt(mode):
                        a, p95, beats = cells[mode]
                        if a["n"] == 0:
                            return f"{'--':^34s}"
                        ps = f"{p95:+.3f}" if not math.isnan(p95) else "  -- "
                        return f"WR{a['wr']*100:4.1f} avgR{a['avgR']:+.3f} tot{a['totR']:+6.1f} {beats:>3s}({ps})"
                    thin = "*" if 0 < n < 30 else " "
                    print(f"  {coin:7s}{side:5s}{tgt:<4}{filt:8s}{n:>5d}{thin} | "
                          f"{fmt('hard'):^34s} | {fmt('body'):^36s}")

    # shakeout-survival
    print("\n" + "=" * 104)
    print("SHAKEOUT-SURVIVAL - trades HARD-stopped that reach TARGET under BODY-CLOSE (+net R value)")
    print("  net value = sum(bodyR - hardR) over survivors = the direct payoff of the body-close discipline")
    print("=" * 104)
    print(f"  {'coin':7s}{'side':5s}{'tgt':4s}{'filt':8s}{'n':>5s}{'shakeout':>10s}{'%ofn':>7s}"
          f"{'survR(body)':>12s}{'netVal':>9s}{'HARDtot':>9s}{'BODYtot':>9s}")
    for r in results:
        tr = r["trades"]; n = len(tr)
        if n == 0:
            continue
        so = [t for t in tr if t["shakeout"]]
        surv_body = sum(t["bR"] for t in so)
        netval = sum(t["bR"] - t["hR"] for t in so)
        htot = sum(t["hR"] for t in tr); btot = sum(t["bR"] for t in tr)
        print(f"  {r['coin']:7s}{r['side']:5s}{r['tgt']:<4}{r['filt']:8s}{n:>5d}{len(so):>10d}"
              f"{len(so)/n*100:>6.1f}%{surv_body:>+12.1f}{netval:>+9.1f}{htot:>+9.1f}{btot:>+9.1f}")

    # realized-loss distribution under body-close (pooled per coin, tgt=2.0, control)
    print("\n" + "=" * 104)
    print("BODY-CLOSE REALIZED-LOSS DISTRIBUTION (R) - pooled per coin, tgt=2.0 control "
          "(HARD loss is always -1R)")
    print("=" * 104)
    for coin in COINS:
        d = data[coin]; losses = []; tpf = 0; nres = 0
        for side in SIDES:
            tr = build(allfills[(coin, side)], side, 2.0, d["m3"], "control")
            for t in tr:
                if t["bo"] == "loss":
                    losses.append(t["bR"])
                if t["bo"] in ("win", "loss"):
                    nres += 1
                if t["tp_first"]:
                    tpf += 1
        if losses:
            q = quart(losses)
            print(f"  {coin}: body-losses={len(losses)} R q25/med/q75={q[0]:.2f}/{q[1]:.2f}/{q[2]:.2f} "
                  f"worst={min(losses):.2f}  | tp-would-fill-first(loss bars)={tpf}/{nres}")
        else:
            print(f"  {coin}: no body-close losses")

    # regime split (informational), tgt=2.0 control, BODY mode
    print("\n" + "=" * 104)
    print("REGIME COLUMN SPLIT (INFORMATIONAL, not a filter) - BODY avgR by 15m regime, tgt=2.0 control")
    print("=" * 104)
    print(f"  {'coin':7s}{'side':5s}{'up':>16s}{'range':>16s}{'down':>16s}")
    for coin in COINS:
        d = data[coin]
        for side in SIDES:
            tr = build(allfills[(coin, side)], side, 2.0, d["m3"], "control")
            def cell(rg):
                rs = [t["bR"] for t in tr if t["regime"] == rg]
                return f"n={len(rs)} {mean(rs):+.3f}" if rs else "n=0    --"
            print(f"  {coin:7s}{side:5s}{cell('up'):>16s}{cell('range'):>16s}{cell('down'):>16s}")

    # headline
    print("\n" + "=" * 104)
    print("HEADLINE - cells that BEAT the null (avgR >= null p95), n>=30")
    print("=" * 104)
    any_beat = False
    for r in results:
        for mode in ("hard", "body"):
            a, p95, beats = r[mode]
            if beats == "YES" and a["n"] >= 30:
                any_beat = True
                print(f"  {r['coin']} {r['side']} tgt={r['tgt']} {r['filt']:7s} [{mode:4s}]: "
                      f"n={a['n']} WR={a['wr']*100:.1f}% avgR={a['avgR']:+.3f} totR={a['totR']:+.1f} "
                      f"(null_p95={p95:+.3f})")
    if not any_beat:
        print("  (none cleared the null at n>=30)")
    print("\nDONE.")


if __name__ == "__main__":
    main()
