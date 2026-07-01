"""Two-layer regime-aware SFP -- DEPLOY-CANDIDATE backtest (live 15m SFP -> 3m BOS).

This is the EXACT live mechanism (not the 15m proxy): live ``SfpModeBDetector`` (15m
SFP sweep -> 3m BOS confirm -> enter next 3m open; stop = swept_wick - 0.001*entry),
unchanged. Two regime layers on top:

  LAYER 1 -- SIDE (15m EMA-200 + slope, 32-bar/8h slope; == regime_filter
    'ema200_pos_slope'):  UP -> LONG only,  DOWN -> SHORT only,  RANGE -> BOTH.
    Never counter-trend.
  LAYER 2 -- R:R TIER by HTF trend strength. HTF strength = expanding-percentile of
    |EMA200 slope (10-bar)| and |price-EMA200 distance|, DIRECTIONAL (must align with
    the trade side), mapped to 4 tiers:
       strong-aligned -> 2.0R | moderate -> 1.5R | mild -> 1.25R | weak/flat/counter -> 1.0R
    Run THREE ways: HTF = 1H / 4H / 1D (each a separate config). Plus a fixed-2R
    baseline (no Layer 2) for comparison.

DATA / CAUSALITY:
  - Entry leg: ``bars_3m`` (live-faithful window: BTC ~81d, others ~47d), 0 gaps.
  - Detector fed the 3m-window 15m + 3m bars (matches the validated Mode-B replication).
  - Regime + HTF context computed on the FULL native 15m (~230d) for proper EMA-200
    warmup, looked up at each entry using the LAST FULLY-CLOSED 15m/HTF bar before the
    3m entry ( (ts - ts%tf) - tf ) -- strict k=1, no look-ahead. Confirmed causal.
  - HTF 1D EMA-200 needs 200 daily bars; 230d of 15m gives ~230 daily bars so only the
    LAST ~30d are warmed -> 1D trades in the earlier window are unwarmed (tier=weak);
    the count is reported + FLAGGED.

COSTS: taker 0.019% x2 legs + 2bp slippage stub x2 = 0.078% notional/round-trip, net
  of, in R via real entry/stop (shorts recover real prices from the reflection midpoint).
SIZING (P&L illustration only): 0.05/0.10 risk, 10x lev.
NULL-GATE: (a) aligned edge and (b) strong-2R tier vs within-side shuffle (200x, p95).

Read-only research. Vendored detector byte-unchanged. No prod/main/live files touched.
"""
from __future__ import annotations
import bisect, math, os, sys, random
from statistics import mean

sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import SfpBar, STOP_BUFFER_PCT
import backtest as bt
import regime_filter as rf
import regime_native15 as rn        # load_native, _db_path, utc, count_gaps, COST_FRAC

COINS   = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
PIVOTS  = [5, 8, 10]                 # union (dedup on entry bar), matching the research
MAX_HOLD_3M = bt.MAX_HOLD_BARS       # 7 days in 3m bars = 3360
COST_FRAC = rn.COST_FRAC             # 0.00078
_15M = 900_000
_3M  = 180_000
HTF_MS = {"1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}
SLOPE_S = 10                         # HTF EMA-200 slope lookback (HTF bars)
EMA_WARM = 200                       # HTF EMA-200 warmup (bars)
TIER_R = {"strong": 2.0, "moderate": 1.5, "mild": 1.25, "weak": 1.0}
TIER_ORDER = ["strong", "moderate", "mild", "weak"]
REGIMES = ("up", "down", "range")
NULL_RUNS, NULL_PCT, NULL_SEED = 200, 95, 20260701
RISK_FRACS, LEV = (0.05, 0.10), 10   # sizing for P&L illustration


# -- HTF strength / tier (causal) -----------------------------------------------

def resample_htf_closes(bars15, period):
    buck = {}
    for b in bars15:                 # bars15 sorted asc -> last write = bucket-final close
        buck[b.ts_ms - (b.ts_ms % period)] = b.close
    return sorted(buck.items())      # [(bucket_ts, close)]


def expanding_pct(vals, warm):
    """Causal expanding percentile of vals (None before warm). p[i] in (0,1]."""
    out = [None] * len(vals); s = []
    for i, v in enumerate(vals):
        if i < warm or v is None:
            continue
        bisect.insort(s, v)
        out[i] = bisect.bisect_right(s, v) / len(s)
    return out


def build_htf(bars15, period):
    """Per-HTF-bar causal arrays: warmed / htf_up / htf_down / strength_pct + a
    bucket_ts->index map. EMA-200 + 10-bar slope + price-EMA200 distance."""
    htf = resample_htf_closes(bars15, period)
    ts = [t for t, _ in htf]; closes = [c for _, c in htf]
    em = rf.ema(closes, 200)
    n = len(closes)
    slope = [None] * n; dist = [None] * n
    up = [False] * n; dn = [False] * n
    for i in range(n):
        warm = i >= EMA_WARM
        dist[i] = abs(closes[i] - em[i]) / em[i] if warm else None
        if warm and i >= SLOPE_S and em[i - SLOPE_S]:
            slope[i] = (em[i] - em[i - SLOPE_S]) / em[i - SLOPE_S]
        if warm and slope[i] is not None:
            up[i] = closes[i] > em[i] and slope[i] > 0
            dn[i] = closes[i] < em[i] and slope[i] < 0
    p_slope = expanding_pct([abs(x) if x is not None else None for x in slope], EMA_WARM)
    p_dist  = expanding_pct(dist, EMA_WARM)
    warmed = [(p_slope[i] is not None and p_dist[i] is not None) for i in range(n)]
    strength = [0.5 * (p_slope[i] + p_dist[i]) if warmed[i] else None for i in range(n)]
    return {"period": period, "map": {t: i for i, t in enumerate(ts)},
            "warmed": warmed, "up": up, "dn": dn, "strength": strength,
            "first_ts": ts[0] if ts else None}


def htf_tier(htf, entry_ts, side):
    """(tier, tp_r, unwarmed_bool). Last CLOSED HTF bar before entry_ts (strict k=1)."""
    period = htf["period"]
    target = (entry_ts - (entry_ts % period)) - period
    i = htf["map"].get(target)
    if i is None or not htf["warmed"][i]:
        return "weak", TIER_R["weak"], True
    aligned = (side == "long" and htf["up"][i]) or (side == "short" and htf["dn"][i])
    if not aligned:
        return "weak", TIER_R["weak"], False
    sp = htf["strength"][i]
    tier = "strong" if sp >= 0.75 else "moderate" if sp >= 0.5 else "mild" if sp >= 0.25 else "weak"
    return tier, TIER_R[tier], False


def regime15_at(lab, entry_ts):
    """Layer-1 side regime from the last CLOSED 15m bar before entry_ts (strict k=1)."""
    return lab.get((entry_ts - (entry_ts % _15M)) - _15M)


# -- Signals + sim --------------------------------------------------------------

def dedup_union(bars15, bars3):
    """Union Mode-B signals over PIVOTS, dedup on entry_bar_index (keep first)."""
    seen = set(); out = []
    allsig = []
    for pl in PIVOTS:
        allsig.extend(bt.get_signals(bars15, bars3, pl))   # SfpModeBDetector, 3m BOS
    for s in sorted(allsig, key=lambda x: x.entry_bar_index):
        if s.entry_bar_index not in seen:
            seen.add(s.entry_bar_index); out.append(s)
    return out


def sim3(bars, idx, swept_low, tp_r):
    if idx >= len(bars):
        return None
    entry = bars[idx].open
    stop = swept_low - STOP_BUFFER_PCT * entry
    rp = entry - stop
    if rp <= 0:
        return None
    tp = entry + tp_r * rp
    for i in range(idx + 1, min(idx + MAX_HOLD_3M + 1, len(bars))):
        b = bars[i]
        if b.low <= stop:                       # stop-first (conservative)
            return {"g": -1.0, "entry": entry, "rp": rp, "hold": i - idx, "out": "loss"}
        if b.high >= tp:
            return {"g": tp_r, "entry": entry, "rp": rp, "hold": i - idx, "out": "win"}
    last = bars[min(idx + MAX_HOLD_3M, len(bars) - 1)]
    return {"g": (last.close - entry) / rp, "entry": entry, "rp": rp,
            "hold": MAX_HOLD_3M, "out": "timeout"}


def net_r(res, side, swept_low, M2):
    if side == "long":
        e, rpr = res["entry"], res["rp"]
    else:
        e = M2 - res["entry"]; sh = M2 - swept_low
        rpr = (sh - e) + STOP_BUFFER_PCT * e
        if rpr <= 0:
            return None
    return res["g"] - COST_FRAC * e / rpr


# -- Per-coin candidates (Layer-1 gated; HTF-independent) -----------------------

def build_candidates(coin, native15, native3):
    """Layer-1-gated signals (drop counter-trend), sorted by entry_ts. Each carries
    the bars array, entry idx, swept_low, side, M2, regime."""
    win15 = [b for b in native15 if native3[0].ts_ms <= b.ts_ms <= native3[-1].ts_ms]
    lab = rf.regime_series(native15, "ema200_pos_slope")     # regime on FULL 230d
    # long
    longs = dedup_union(win15, native3)
    # short: reflect 3m, resample -> reflected 15m (shared midpoint), detect
    r3 = rf.reflect(native3); r15 = bt.resample_15m(r3)
    M2 = max(b.high for b in native3) + min(b.low for b in native3)
    shorts = dedup_union(r15, r3)
    cands = []
    for s in longs:
        idx = s.entry_bar_index
        if idx >= len(native3):
            continue
        ets = native3[idx].ts_ms
        reg = regime15_at(lab, ets)
        if reg not in ("up", "range"):           # Layer-1: long only in up/range
            continue
        cands.append({"ts": ets, "side": "long", "bars": native3, "idx": idx,
                      "swept": s.swept_low, "M2": None, "reg": reg})
    for s in shorts:
        idx = s.entry_bar_index
        if idx >= len(r3):
            continue
        ets = r3[idx].ts_ms
        reg = regime15_at(lab, ets)
        if reg not in ("down", "range"):         # Layer-1: short only in down/range
            continue
        cands.append({"ts": ets, "side": "short", "bars": r3, "idx": idx,
                      "swept": s.swept_low, "M2": M2, "reg": reg})
    cands.sort(key=lambda c: c["ts"])
    return cands, lab


def run_oneopen(cands, tier_fn):
    """One position per coin at a time; tp_r from tier_fn(side, ts). Returns trades."""
    trades = []; open_until = -1
    for c in cands:
        if c["ts"] <= open_until:
            continue
        tier, tp_r, unwarm = tier_fn(c["side"], c["ts"])
        res = sim3(c["bars"], c["idx"], c["swept"], tp_r)
        if res is None:
            continue
        nr = net_r(res, c["side"], c["swept"], c["M2"])
        if nr is None:
            continue
        exit_idx = c["idx"] + res["hold"]
        open_until = c["bars"][min(exit_idx, len(c["bars"]) - 1)].ts_ms
        trades.append({"coin": None, "side": c["side"], "reg": c["reg"], "tier": tier,
                       "tp_r": tp_r, "net": nr, "gross": res["g"], "unwarm": unwarm,
                       "win": nr > 0, "ts": c["ts"]})
    return trades


# -- Aggregation / null ---------------------------------------------------------

def agg(vals):
    n = len(vals)
    if n == 0:
        return (0, float("nan"), float("nan"))
    return (n, sum(1 for v in vals if v > 0) / n, sum(vals) / n)


def fc(vals):
    n, wr, ex = agg(vals)
    return "n=   0        --" if n == 0 else f"n={n:4d} WR{wr*100:4.0f}% {ex:+.3f}R"


def _pctl(vals, pct):
    v = sorted(x for x in vals if not math.isnan(x))
    if not v:
        return float("nan")
    idx = pct / 100 * (len(v) - 1); lo = int(idx); hi = min(lo + 1, len(v) - 1)
    return v[lo] * (1 - (idx - lo)) + v[hi] * (idx - lo)


def null_aligned(long_reg, short_reg):
    """within-side 15m-regime shuffle; returns p95 for aligned (long-up+short-down)."""
    rng = random.Random(NULL_SEED)
    Ll = [r for r, _ in long_reg]; Lv = [v for _, v in long_reg]
    Sl = [r for r, _ in short_reg]; Sv = [v for _, v in short_reg]
    dist = []
    for _ in range(NULL_RUNS):
        lp = Ll[:]; rng.shuffle(lp); sp = Sl[:]; rng.shuffle(sp)
        up = [Lv[i] for i in range(len(lp)) if lp[i] == "up"]
        dn = [Sv[i] for i in range(len(sp)) if sp[i] == "down"]
        both = up + dn
        dist.append(mean(both) if both else float("nan"))
    return _pctl(dist, NULL_PCT)


def null_tier(trades):
    """within-side tier-label shuffle; returns p95 for the 'strong' bucket expR."""
    rng = random.Random(NULL_SEED + 1)
    byside = {"long": [], "short": []}
    for t in trades:
        byside[t["side"]].append((t["tier"], t["net"]))
    dist = []
    for _ in range(NULL_RUNS):
        strong = []
        for side, arr in byside.items():
            labs = [a for a, _ in arr]; vals = [v for _, v in arr]
            perm = labs[:]; rng.shuffle(perm)
            strong += [vals[i] for i in range(len(perm)) if perm[i] == "strong"]
        dist.append(mean(strong) if strong else float("nan"))
    return _pctl(dist, NULL_PCT)


# -- Main -----------------------------------------------------------------------

def main():
    print("=" * 80)
    print("TWO-LAYER REGIME-AWARE SFP -- deploy-candidate backtest (live 15m SFP -> 3m BOS)")
    print("=" * 80)
    n15 = {}; n3 = {}; cands = {}; labs = {}; htf = {c: {} for c in COINS}
    weeks = {}
    print("\n[LOAD] 3m entry window (gaps must be 0) + 230d 15m context:")
    for c in COINS:
        n15[c] = rn.load_native(c, "bars_15m"); n3[c] = rn.load_native(c, "bars_3m")
        g3 = rn.count_gaps(n3[c], _3M)
        weeks[c] = (n3[c][-1].ts_ms - n3[c][0].ts_ms) / (7 * 86400_000)
        print(f"  {c}: 3m n={len(n3[c]):5d} {rn.utc(n3[c][0].ts_ms)}->{rn.utc(n3[c][-1].ts_ms)} "
              f"({weeks[c]:.1f}w) gaps={g3} | 15m {len(n15[c])} bars")
    print("\n[BUILD] Layer-1-gated candidates + HTF strength tiers (1H/4H/1D):")
    for c in COINS:
        cands[c], labs[c] = build_candidates(c, n15[c], n3[c])
        for tf, ms in HTF_MS.items():
            htf[c][tf] = build_htf(n15[c], ms)
        nl = sum(1 for x in cands[c] if x["side"] == "long")
        ns = sum(1 for x in cands[c] if x["side"] == "short")
        print(f"  {c}: candidates long={nl} short={ns} (post Layer-1 side gate, pre one-open)")

    # ungated trades (fixed 2R) for the aligned-edge null -- all signals, regime-tagged
    print("\n[NULL PREP] ungated long/short @2R regime-tagged (for aligned-edge null)...")
    ung_long = []; ung_short = []
    for c in COINS:
        win15 = [b for b in n15[c] if n3[c][0].ts_ms <= b.ts_ms <= n3[c][-1].ts_ms]
        for s in dedup_union(win15, n3[c]):
            if s.entry_bar_index >= len(n3[c]):
                continue
            ets = n3[c][s.entry_bar_index].ts_ms; reg = regime15_at(labs[c], ets)
            if reg is None:
                continue
            res = sim3(n3[c], s.entry_bar_index, s.swept_low, 2.0)
            if res:
                nr = net_r(res, "long", s.swept_low, None)
                if nr is not None:
                    ung_long.append((reg, nr))
        r3 = rf.reflect(n3[c]); r15 = bt.resample_15m(r3)
        M2 = max(b.high for b in n3[c]) + min(b.low for b in n3[c])
        for s in dedup_union(r15, r3):
            if s.entry_bar_index >= len(r3):
                continue
            ets = r3[s.entry_bar_index].ts_ms; reg = regime15_at(labs[c], ets)
            if reg is None:
                continue
            res = sim3(r3, s.entry_bar_index, s.swept_low, 2.0)
            if res:
                nr = net_r(res, "short", s.swept_low, M2)
                if nr is not None:
                    ung_short.append((reg, nr))
    aligned_p95 = null_aligned(ung_long, ung_short)
    al = [v for r, v in ung_long if r == "up"] + [v for r, v in ung_short if r == "down"]
    print(f"  aligned (ungated, @2R, net): {fc(al)}  null_p95={aligned_p95:+.3f} -> "
          f"{'BEATS' if agg(al)[2] >= aligned_p95 else 'no'}")

    # -- run each HTF config + fixed-2R baseline --
    configs = ["BASELINE-2R", "1H", "4H", "1D"]
    for cfg in configs:
        def tier_fn(side, ts, _cfg=cfg):
            if _cfg == "BASELINE-2R":
                return "strong", 2.0, False
            return None  # replaced below per coin
        all_tr = []
        unwarm_1d = 0
        for c in COINS:
            if cfg == "BASELINE-2R":
                tf_fn = lambda side, ts: ("na", 2.0, False)
            else:
                h = htf[c][cfg]
                tf_fn = lambda side, ts, _h=h: htf_tier(_h, ts, side)
            tr = run_oneopen(cands[c], tf_fn)
            for t in tr:
                t["coin"] = c
                if cfg == "1D" and t["unwarm"]:
                    unwarm_1d += 1
            all_tr.extend(tr)

        print("\n" + "=" * 80)
        print(f"CONFIG: {cfg}" + ("   (no Layer 2 -- 2R everywhere)" if cfg == "BASELINE-2R"
              else f"   (Layer 2 tier by {cfg} EMA-200 strength)"))
        print("=" * 80)
        no, wo, eo = agg([t["net"] for t in all_tr])
        totR = sum(t["net"] for t in all_tr)
        pnl = "  ".join(f"@{rfc:.2f}risk={totR*rfc*100:+.1f}%eq" for rfc in RISK_FRACS)
        print(f"  OVERALL net: {fc([t['net'] for t in all_tr])}  "
              f"(gross avg {mean([t['gross'] for t in all_tr]):+.3f}R)  totR={totR:+.1f}")
        print(f"  P&L illustration (sum net R x risk, 10x lev enables size): {pnl}  "
              f"[pooled over 4 coins' ~7-12wk windows]")
        # side x regime
        print(f"  {'side x regime (net)':22s} {'UP':>20s} {'RANGE':>20s} {'DOWN':>20s}")
        for side in ("long", "short"):
            cells = " ".join(f"{fc([t['net'] for t in all_tr if t['side']==side and t['reg']==r]):>20s}"
                             for r in ("up", "range", "down"))
            print(f"  {'  '+side:22s} {cells}")
        # by tier
        if cfg != "BASELINE-2R":
            print(f"  {'by R:R tier (net)':22s} " +
                  " ".join(f"{tt+'/'+str(TIER_R[tt])+'R':>16s}" for tt in TIER_ORDER))
            row = " ".join(f"{fc([t['net'] for t in all_tr if t['tier']==tt]):>16s}" for tt in TIER_ORDER)
            print(f"  {'  expectancy':22s} {row}")
            exps = [agg([t["net"] for t in all_tr if t["tier"] == tt])[2] for tt in TIER_ORDER]
            mono = all((not math.isnan(exps[i]) and not math.isnan(exps[i+1]) and exps[i] >= exps[i+1] - 1e-9)
                       for i in range(len(exps)-1))
            sgw = (not math.isnan(exps[0]) and not math.isnan(exps[3]) and exps[0] > exps[3])
            tp95 = null_tier(all_tr)
            se = agg([t["net"] for t in all_tr if t["tier"] == "strong"])[2]
            print(f"  TIER SEPARATION: strong>weak={sgw}  monotonic(strong>=mod>=mild>=weak)={mono} | "
                  f"strong-2R null_p95={tp95:+.3f} -> {'BEATS' if (not math.isnan(se) and se>=tp95) else 'no'}")
            if cfg == "1D":
                print(f"  ** 1D FLAG: {unwarm_1d}/{len(all_tr)} trades fell in the EMA-200 UNWARMED "
                      f"region (forced weak/1R) -- 1D tiering is unreliable on this window. **")
        # per-coin: net + fire rate
        print(f"  {'per-coin':8s} {'net expR (n)':>16s} {'fires/wk':>9s}  side x regime net (L:up/rng | S:rng/dn)")
        for c in COINS:
            ct = [t for t in all_tr if t["coin"] == c]
            n, wr, ex = agg([t["net"] for t in ct])
            fpw = n / weeks[c] if weeks[c] else 0
            def sr(side, r):
                a = agg([t["net"] for t in ct if t["side"] == side and t["reg"] == r])
                return f"{a[2]:+.2f}({a[0]})" if a[0] else "--(0)"
            print(f"  {c:8s} {ex:+.3f}({n:3d})      {fpw:5.2f}   "
                  f"L:{sr('long','up')}/{sr('long','range')} | S:{sr('short','range')}/{sr('short','down')}")

    print("\n" + "=" * 80)
    print("READOUT: pick the HTF config whose tiers are MONOTONIC (strong-2R > weak-1R) and")
    print("whose strong-2R beats the null. Fire rate = accepted trades/week per coin above.")
    print("=" * 80)


if __name__ == "__main__":
    main()
