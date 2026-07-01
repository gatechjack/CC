"""Per-coin personalization bake-off for the regime-aware SFP strategy. GROSS R only
(no fees -- operator factors fees himself). Read-only research.

BASE (control, identical for all coins): live SfpModeBDetector 15m SFP -> 3m BOS ->
next-3m-open entry, live stop; regime = 15m EMA-200+slope (K=32); side-by-regime
(long-UP / short-DOWN / both-RANGE, never counter-trend); fixed 2R; one position/coin.

Sequential search per coin (one axis at a time to protect sample):
  STEP 1  regime-engine bake-off: {15m EMA200+slope (BASE), 1H trend, 4H trend,
          structure HH/HL}. Adopt a non-base engine ONLY if OOS gross > base OOS AND
          beats a regime-shuffle null (200x, p95). Else keep base.
  STEP 2  R:R per active cell (long-UP, short-DOWN, long-RANGE, short-RANGE), given the
          coin's winning engine: sweep target in {1,1.5,2,2.5,3,trail}. Adopt a non-2R
          target for a cell ONLY if it beats 2R OOS on that cell. Flag n<20 (do not hard
          adopt on <20; directional only).
  STEP 3  regime thresholds (light): try a stricter slope cutoff; keep base unless it
          clearly beats OOS. Do not over-tune.

DISCIPLINE: k=1 causal on every layer (EMA/slope/structure at bar t use data <= t;
regime looked up at the LAST FULLY-CLOSED bar before the 3m entry: (ts-ts%tf)-tf).
IS/OOS = first 60% / last 40% by entry TIME within each coin's 3m window. Selection is
on OOS per spec -- this is optimistic (selecting on the test split); IS is reported
alongside so OOS-only wins can be flagged. GROSS throughout. Vendored detector unchanged.
"""
from __future__ import annotations
import math, os, sys, random
from statistics import mean

sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import SfpBar, STOP_BUFFER_PCT
import backtest as bt
import regime_filter as rf
import regime_native15 as rn

COINS   = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
PIVOTS  = [5, 8, 10]
MAXHOLD = bt.MAX_HOLD_BARS
BUF     = STOP_BUFFER_PCT
IS_FRAC = 0.60
_15M, _1H, _4H = 900_000, 3_600_000, 14_400_000
STRUCT_W = 10
TARGETS = [("1", 1.0), ("1.5", 1.5), ("2", 2.0), ("2.5", 2.5), ("3", 3.0), ("trail", "trail")]
CELLS   = [("long", "up"), ("long", "range"), ("short", "range"), ("short", "down")]
NULL_RUNS, NULL_PCT, NULL_SEED = 200, 95, 20260701


# -- resampling / structure engine ----------------------------------------------

def resample_htf(bars15, period):
    bk = {}
    for b in bars15:
        k = b.ts_ms - (b.ts_ms % period)
        if k not in bk:
            bk[k] = [b.open, b.high, b.low, b.close]
        else:
            v = bk[k]; v[1] = max(v[1], b.high); v[2] = min(v[2], b.low); v[3] = b.close
    return [SfpBar(k, o, h, l, c) for k, (o, h, l, c) in sorted(bk.items())]


def structure_regime(bars15, w=STRUCT_W):
    """Causal HH/HL: strict degree-w swing highs/lows confirmed w bars forward; UP if
    last two highs AND last two lows are rising, DOWN if both falling, else RANGE."""
    n = len(bars15); hi = [b.high for b in bars15]; lo = [b.low for b in bars15]
    sh = []; sl = []
    for i in range(w, n - w):
        seg = range(i - w, i + w + 1)
        if all(hi[i] > hi[j] for j in seg if j != i): sh.append((i + w, hi[i]))
        if all(lo[i] < lo[j] for j in seg if j != i): sl.append((i + w, lo[i]))
    lab = {}; hp = lp = 0; ch = []; cl = []
    for t in range(n):
        while hp < len(sh) and sh[hp][0] <= t: ch.append(sh[hp][1]); hp += 1
        while lp < len(sl) and sl[lp][0] <= t: cl.append(sl[lp][1]); lp += 1
        if len(ch) >= 2 and len(cl) >= 2:
            hh, hl = ch[-1] > ch[-2], cl[-1] > cl[-2]
            lh, ll = ch[-1] < ch[-2], cl[-1] < cl[-2]
            lab[bars15[t].ts_ms] = "up" if (hh and hl) else "down" if (lh and ll) else "range"
    return lab


def build_engines(native15):
    return {
        "base15": (rf.regime_series(native15, "ema200_pos_slope"), _15M),
        "1H":     (rf.regime_series(resample_htf(native15, _1H), "ema200_pos_slope"), _1H),
        "4H":     (rf.regime_series(resample_htf(native15, _4H), "ema200_pos_slope"), _4H),
        "struct": (structure_regime(native15), _15M),
    }


def regime_at(lab, ts, tf):
    return lab.get((ts - (ts % tf)) - tf)     # last fully-closed bar before ts (k=1)


# -- signals + sim (GROSS) ------------------------------------------------------

def dedup_union(bars15, bars3):
    seen = set(); out = []; alls = []
    for pl in PIVOTS:
        alls.extend(bt.get_signals(bars15, bars3, pl))
    for s in sorted(alls, key=lambda x: x.entry_bar_index):
        if s.entry_bar_index not in seen:
            seen.add(s.entry_bar_index); out.append(s)
    return out


def gen_signals(native15, native3):
    win15 = [b for b in native15 if native3[0].ts_ms <= b.ts_ms <= native3[-1].ts_ms]
    sigs = []
    for s in dedup_union(win15, native3):
        if s.entry_bar_index < len(native3):
            sigs.append({"ts": native3[s.entry_bar_index].ts_ms, "side": "long",
                         "bars": native3, "idx": s.entry_bar_index, "swept": s.swept_low})
    r3 = rf.reflect(native3); r15 = bt.resample_15m(r3)
    for s in dedup_union(r15, r3):
        if s.entry_bar_index < len(r3):
            sigs.append({"ts": r3[s.entry_bar_index].ts_ms, "side": "short",
                         "bars": r3, "idx": s.entry_bar_index, "swept": s.swept_low})
    sigs.sort(key=lambda x: x["ts"])
    return sigs


def sim_fixed(bars, idx, swept, tp_r):
    if idx >= len(bars): return None
    e = bars[idx].open; stop = swept - BUF * e; rp = e - stop
    if rp <= 0: return None
    tp = e + tp_r * rp
    for i in range(idx + 1, min(idx + MAXHOLD + 1, len(bars))):
        b = bars[i]
        if b.low <= stop: return (-1.0, i - idx)
        if b.high >= tp: return (tp_r, i - idx)
    last = bars[min(idx + MAXHOLD, len(bars) - 1)]
    return ((last.close - e) / rp, MAXHOLD)


def sim_trail(bars, idx, swept):
    if idx >= len(bars): return None
    e = bars[idx].open; stop = swept - BUF * e; rp = e - stop
    if rp <= 0: return None
    for i in range(idx + 1, min(idx + MAXHOLD + 1, len(bars))):
        b = bars[i]
        if b.low <= stop: return ((stop - e) / rp, i - idx)   # exit at trailed stop
        k = int((b.high - e) / rp)                            # 1R-ratchet after +1R
        if k >= 1:
            ns = e + (k - 1) * rp
            if ns > stop: stop = ns
    last = bars[min(idx + MAXHOLD, len(bars) - 1)]
    return ((last.close - e) / rp, MAXHOLD)


def _sim(sig, tgt):
    if tgt == "trail":
        return sim_trail(sig["bars"], sig["idx"], sig["swept"])
    return sim_fixed(sig["bars"], sig["idx"], sig["swept"], tgt)


def gate_oneopen(sig_reg, target_fn):
    """sig_reg = [(sig, regime)] sorted by ts. Side-by-regime gate + one position/coin.
    Returns trades [{side,reg,cell,g,ts}]."""
    trades = []; open_until = -1
    for sig, reg in sig_reg:
        if reg is None: continue
        side = sig["side"]
        if side == "long" and reg not in ("up", "range"): continue
        if side == "short" and reg not in ("down", "range"): continue
        if sig["ts"] <= open_until: continue
        cell = (side, reg)
        res = _sim(sig, target_fn(cell))
        if res is None: continue
        g, hold = res
        open_until = sig["bars"][min(sig["idx"] + hold, len(sig["bars"]) - 1)].ts_ms
        trades.append({"side": side, "reg": reg, "cell": cell, "g": g, "ts": sig["ts"]})
    return trades


# -- helpers --------------------------------------------------------------------

def stat(trades):
    n = len(trades)
    return (n, mean(t["g"] for t in trades) if n else float("nan"))


def split(trades, cut):
    return ([t for t in trades if t["ts"] < cut], [t for t in trades if t["ts"] >= cut])


def _pctl(v, p):
    v = sorted(x for x in v if not math.isnan(x))
    if not v: return float("nan")
    i = p / 100 * (len(v) - 1); lo = int(i); hi = min(lo + 1, len(v) - 1)
    return v[lo] * (1 - (i - lo)) + v[hi] * (i - lo)


def null_p95(sigs, lab, tf, cut, target_fn, seed=NULL_SEED):
    """Regime-shuffle within side over all signals -> re-gate+one-open -> OOS mean. p95."""
    truereg = [(s, regime_at(lab, s["ts"], tf)) for s in sigs]
    by = {"long": [], "short": []}
    for s, r in truereg:
        by[s["side"]].append(r)
    rng = random.Random(seed); dist = []
    for _ in range(NULL_RUNS):
        perm = {}
        for side in by:
            pr = by[side][:]; rng.shuffle(pr); perm[side] = pr
        idxs = {"long": 0, "short": 0}
        sr = []
        for s in sigs:
            side = s["side"]; r = perm[side][idxs[side]]; idxs[side] += 1
            sr.append((s, r))
        _, oos = split(gate_oneopen(sr, target_fn), cut)
        dist.append(stat(oos)[1])
    return _pctl(dist, NULL_PCT)


def fmt(n, ex):
    return "n=  0    --" if n == 0 else f"n={n:3d} {ex:+.3f}R"


# -- Main -----------------------------------------------------------------------

def main():
    print("=" * 84)
    print("PER-COIN PERSONALIZATION BAKE-OFF -- regime-aware SFP (GROSS R, no fees)")
    print("IS/OOS = 60/40 by entry time; selection on OOS (optimistic -- IS shown for consistency)")
    print("=" * 84)
    data = {}
    for c in COINS:
        n15 = rn.load_native(c, "bars_15m"); n3 = rn.load_native(c, "bars_3m")
        sigs = gen_signals(n15, n3)
        eng = build_engines(n15)
        cut = n3[0].ts_ms + IS_FRAC * (n3[-1].ts_ms - n3[0].ts_ms)
        data[c] = {"n15": n15, "n3": n3, "sigs": sigs, "eng": eng, "cut": cut,
                   "wk": (n3[-1].ts_ms - n3[0].ts_ms) / (7 * 86400_000)}
        print(f"  {c}: sigs={len(sigs)} (long {sum(1 for s in sigs if s['side']=='long')}/"
              f"short {sum(1 for s in sigs if s['side']=='short')}), "
              f"3m {rn.utc(n3[0].ts_ms)}->{rn.utc(n3[-1].ts_ms)}, IS/OOS cut={rn.utc(int(cut))}")

    chosen = {}
    # ---------------- STEP 1: regime-engine bake-off ----------------
    print("\n" + "=" * 84 + "\nSTEP 1 -- regime-engine bake-off (side-by-regime, fixed 2R). GROSS OOS.\n" + "=" * 84)
    for c in COINS:
        d = data[c]; base_oos = None; results = {}
        for name, (lab, tf) in d["eng"].items():
            sr = [(s, regime_at(lab, s["ts"], tf)) for s in d["sigs"]]
            tr = gate_oneopen(sr, lambda cell: 2.0)
            isr, oos = split(tr, d["cut"])
            results[name] = (stat(isr), stat(oos))
            if name == "base15":
                base_oos = stat(oos)[1]
        # decisions
        print(f"\n  {c}  (base OOS {base_oos:+.3f}R):")
        best = "base15"; best_oos = base_oos
        for name, (iss, oos) in results.items():
            tag = "  <BASE>" if name == "base15" else ""
            print(f"    {name:7s} IS {fmt(*iss)} | OOS {fmt(*oos)}{tag}")
        adopt = "base15"; reason = "no non-base engine beat base OOS + null"
        for name, (iss, oos) in results.items():
            if name == "base15": continue
            if not math.isnan(oos[1]) and oos[1] > base_oos and oos[0] >= 1:
                lab, tf = d["eng"][name]
                p95 = null_p95(d["sigs"], lab, tf, d["cut"], lambda cell: 2.0)
                beats = oos[1] > p95
                also_is = (not math.isnan(results[name][0][1])) and results[name][0][1] > results["base15"][0][1]
                verdict = ("ADOPT" if beats else "reject(null)")
                if beats and oos[1] > best_oos:
                    if not also_is:
                        verdict += " [OOS-only, IS disagrees -> PROVISIONAL]"
                    adopt = name; best_oos = oos[1]
                    reason = f"{name} OOS {oos[1]:+.3f} > base {base_oos:+.3f}, null_p95 {p95:+.3f}"
                print(f"      -> {name}: OOS {oos[1]:+.3f} vs base {base_oos:+.3f}, "
                      f"null_p95 {p95:+.3f} -> {verdict}")
        chosen[c] = adopt
        print(f"    ==> {c} regime engine: {adopt}  ({reason})")

    # ---------------- STEP 2: R:R per cell (chosen engine) ----------------
    print("\n" + "=" * 84 + "\nSTEP 2 -- R:R per active cell (chosen engine fixed). GROSS OOS. Adopt vs 2R.\n" + "=" * 84)
    per_cell_target = {}
    for c in COINS:
        d = data[c]; lab, tf = d["eng"][chosen[c]]
        sr = [(s, regime_at(lab, s["ts"], tf)) for s in d["sigs"]]
        # per-cell signal streams
        print(f"\n  {c} (engine {chosen[c]}):")
        tmap = {}
        for cell in CELLS:
            cell_sigs = [(s, r) for s, r in sr if r is not None and
                         ((s["side"] == "long" and cell[0] == "long" and r == cell[1]) or
                          (s["side"] == "short" and cell[0] == "short" and r == cell[1]))]
            if not cell_sigs:
                print(f"    {cell[0]}-{cell[1]:5s}: no signals"); tmap[cell] = 2.0; continue
            row = []; base2 = None; best_t = "2"; best_oos = None; best_val = 2.0
            for tname, tval in TARGETS:
                tr = gate_oneopen(cell_sigs, lambda cl, _t=tval: _t)
                _, oos = split(tr, d["cut"]); n, ex = stat(oos)
                row.append(f"{tname}:{fmt(n, ex)}")
                if tname == "2": base2 = (n, ex)
                if best_oos is None or (not math.isnan(ex) and (math.isnan(best_oos) or ex > best_oos)):
                    best_oos = ex; best_t = tname; best_val = tval; best_n = n
            adopt_t = "2"; adopt_val = 2.0
            thin = base2[0] < 20
            if best_t != "2" and not math.isnan(best_oos) and best_oos > base2[1]:
                if best_n >= 20:
                    adopt_t = best_t; adopt_val = best_val
                    note = "ADOPT"
                else:
                    note = f"best={best_t} but n={best_n}<20 -> PROVISIONAL (keep 2R)"
            else:
                note = "keep 2R"
            tmap[cell] = adopt_val
            flag = " [THIN n<20]" if thin else ""
            print(f"    {cell[0]}-{cell[1]:5s}{flag}: " + "  ".join(row))
            print(f"        -> 2R OOS {fmt(*base2)}; best={best_t}({best_oos:+.3f}); {note}")
        per_cell_target[c] = tmap

    # ---------------- STEP 3: threshold probe (light) ----------------
    print("\n" + "=" * 84 + "\nSTEP 3 -- regime threshold probe (light; slope cutoff). GROSS OOS.\n" + "=" * 84)
    for c in COINS:
        d = data[c]
        if chosen[c] not in ("base15", "1H", "4H"):
            print(f"  {c}: engine {chosen[c]} is structure-based (no slope cutoff) -> skip threshold probe")
            continue
        # rebuild the chosen EMA200+slope engine with a stricter slope cutoff: require
        # |EMA200 slope over 32 bars| > thr to call UP/DOWN, else RANGE. thr swept lightly.
        tf = {"base15": _15M, "1H": _1H, "4H": _4H}[chosen[c]]
        bars = d["n15"] if chosen[c] == "base15" else resample_htf(d["n15"], tf)
        closes = [b.close for b in bars]; em = rf.ema(closes, 200); K = 32
        base_oos = _eng_oos(d, chosen[c], regime_at, gate_oneopen, split, stat)
        print(f"  {c} (engine {chosen[c]}, base OOS {base_oos:+.3f}R):")
        for thr in (0.0, 0.002, 0.005):
            lab = {}
            for i, b in enumerate(bars):
                if i >= K and em[i - K]:
                    sl = (em[i] - em[i - K]) / em[i - K]
                    rising = em[i] > em[i - K]
                    strong = abs(sl) >= thr
                    if closes[i] > em[i] and rising and strong: lab[b.ts_ms] = "up"
                    elif closes[i] < em[i] and (not rising) and strong: lab[b.ts_ms] = "down"
                    else: lab[b.ts_ms] = "range"
            sr = [(s, regime_at(lab, s["ts"], tf)) for s in d["sigs"]]
            _, oos = split(gate_oneopen(sr, lambda cell: 2.0), d["cut"])
            n, ex = stat(oos)
            mark = "  (= base thr=0)" if thr == 0.0 else ("  BEATS base" if (not math.isnan(ex) and ex > base_oos) else "")
            print(f"    slope_thr={thr:.3f}: OOS {fmt(n, ex)}{mark}")
        print("    -> keep base thresholds unless a shift CLEARLY beats OOS (sample is thin; do not over-tune)")

    # ---------------- FINAL side-by-side ----------------
    print("\n" + "=" * 84 + "\nFINAL -- base vs personalized, GROSS OOS, per coin\n" + "=" * 84)
    print(f"  {'coin':8s} {'engine':7s} {'base-2R OOS':>14s} {'personalized OOS':>18s} "
          f"{'null_p95':>9s} {'beats_null':>10s}  per-cell targets")
    for c in COINS:
        d = data[c]; lab, tf = d["eng"][chosen[c]]
        sr = [(s, regime_at(lab, s["ts"], tf)) for s in d["sigs"]]
        base_tr = gate_oneopen([(s, regime_at(d["eng"]["base15"][0], s["ts"], _15M)) for s in d["sigs"]],
                               lambda cell: 2.0)
        _, base_oos = split(base_tr, d["cut"])
        pers_tr = gate_oneopen(sr, lambda cell, _c=c: per_cell_target[_c].get(cell, 2.0))
        _, pers_oos = split(pers_tr, d["cut"])
        p95 = null_p95(d["sigs"], lab, tf, d["cut"], lambda cell, _c=c: per_cell_target[_c].get(cell, 2.0))
        bn, bex = stat(base_oos); pn, pex = stat(pers_oos)
        beats = "YES" if (not math.isnan(pex) and pex > p95) else "no"
        tg = ",".join(f"{cell[0][0].upper()}{cell[1][:2]}={('trail' if per_cell_target[c][cell]=='trail' else per_cell_target[c][cell])}"
                      for cell in CELLS if per_cell_target[c].get(cell, 2.0) != 2.0) or "all 2R"
        print(f"  {c:8s} {chosen[c]:7s} {fmt(bn, bex):>14s} {fmt(pn, pex):>18s} "
              f"{p95:+.3f}   {beats:>8s}   {tg}")
    print("\n  (personalized OOS is UPWARD-BIASED: targets/engine were SELECTED on OOS. Trust")
    print("   only configs that also win IS and beat null; treat thin-cell adoptions as provisional.)")


def _eng_oos(d, name, regime_at, gate_oneopen, split, stat):
    lab, tf = d["eng"][name]
    sr = [(s, regime_at(lab, s["ts"], tf)) for s in d["sigs"]]
    _, oos = split(gate_oneopen(sr, lambda cell: 2.0), d["cut"])
    return stat(oos)[1]


if __name__ == "__main__":
    main()
