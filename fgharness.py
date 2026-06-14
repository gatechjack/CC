"""Fee-gate counterfactual harness — replay the fees_too_high_for_risk declines.
READ-ONLY analysis. Reuses the strategy's OWN build_trade_plan (no re-derivation).

Per decline:
  - validate: build_trade_plan(stored inputs, TAKER fees) reproduces the skip.
  - reconstruct structural plan via build_trade_plan(stored inputs, ZERO fees)
    -> TP1=0.5R, TP2=1R/snap, TP3=2.5R, SL=stop (fee-independent geometry).
  - does MAKER pricing (tp_is_maker=True) admit it? (fix-b ground truth, builder verdict)
  - walk structural plan forward on 3m bars -> gross R (partial exits + BE/TP1 ratchet).
  - net_taker = gross - RT_taker*entry/risk ; net_maker = gross - RT_maker*entry/risk.
VAL section: build_trade_plan(stored inputs, TAKER) == stored fired plan (recon guard).
"""
from __future__ import annotations
import os, sys, csv, io, importlib.util
from dataclasses import dataclass

# Load trade_plan.py DIRECTLY by file path (it is fully self-contained — stdlib only)
# to avoid the trading_corp package __init__ chain / venv-dep hunt. Python-agnostic.
HERE = os.path.dirname(os.path.abspath(__file__))
_TP = os.path.join(HERE, "trading_corp", "agents", "strategies", "trade_plan.py")
_spec = importlib.util.spec_from_file_location("tp_standalone", _TP)
_tp = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = _tp; _spec.loader.exec_module(_tp)
build_trade_plan, StrategyConfig, FeeConfig = _tp.build_trade_plan, _tp.StrategyConfig, _tp.FeeConfig

@dataclass
class Bar:
    ts_ms: int; open: float; high: float; low: float; close: float; volume: float = 0.0

print(f"=== IMPORTS OK (trade_plan from {os.path.relpath(_TP, HERE)}) ===")

CFG = StrategyConfig()
FEES_TK = FeeConfig()                                              # taker exits (prod)
FEES_MK = FeeConfig(tp_is_maker=True)                              # maker exits (fix b)
FEES_ZERO = FeeConfig(taker_fee_pct=0.0, maker_fee_pct=0.0, slippage_pct=0.0)
RT_TK = FEES_TK.round_trip_cost_pct()
RT_MK = FEES_MK.round_trip_cost_pct()
print(f"RT_taker={RT_TK:.5f}  RT_maker={RT_MK:.5f}  "
      f"taker_floor={2*RT_TK*100:.3f}%entry  maker_floor={2*RT_MK*100:.3f}%entry")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qdata2.out")
raw = open(OUT, encoding="utf-8-sig").read()
sec, cur = {}, None
for line in raw.splitlines():
    if line.startswith("===") and line.endswith("==="):
        cur = line.strip("="); sec[cur] = []; continue
    if cur and line.strip():
        sec[cur].append(line)
def rows(name): return list(csv.reader(io.StringIO("\n".join(sec.get(name, [])))))
def fnum(x):
    try: return float(x)
    except: return None

bars = [Bar(ts_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
            close=float(r[4]), volume=0.0) for r in rows("BARS3M")]
bars.sort(key=lambda b: b.ts_ms)
print(f"bars3m: {len(bars)}  range {bars[0].ts_ms}..{bars[-1].ts_ms}")

import datetime as dt
def iso_to_ms(s): return int(dt.datetime.fromisoformat(s).timestamp() * 1000)
def idx_at(ts_ms):
    lo, hi, ans = 0, len(bars) - 1, -1
    while lo <= hi:
        m = (lo + hi) // 2
        if bars[m].ts_ms <= ts_ms: ans = m; lo = m + 1
        else: hi = m - 1
    return ans
def idx_closed(ts_ms):
    i = idx_at(ts_ms)
    while i >= 0 and bars[i].ts_ms + 180000 > ts_ms:
        i -= 1
    return i

# ---------- walk (mirrors _classify_v2_multi_leg: SL-first tie, ordered TP, BE/TP1 ratchet) ----------
def _r_at(side, entry, osl, px):
    risk = abs(entry - osl)
    return 0.0 if risk <= 0 else (1.0 if side == "buy" else -1.0) * (px - entry) / risk
def _agg_r(side, entry, osl, plan, filled, exitpx):
    tot = ff = 0.0
    for leg in ("tp1", "tp2", "tp3"):
        if leg in filled:
            tot += plan[leg]["target_r"] * plan[leg]["fraction"]; ff += plan[leg]["fraction"]
    unf = max(0.0, 1.0 - ff)
    if unf > 0: tot += _r_at(side, entry, osl, exitpx) * unf
    return tot
def _ratchet(side, entry, osl, csl, filled, plan):
    f = set(filled)
    if "tp1" not in f: return csl
    if "tp2" not in f:
        cand = entry
        return cand if ((side == "buy" and cand > csl) or (side == "sell" and cand < csl)) else csl
    cand = plan["tp1"]["price"]
    return cand if ((side == "buy" and cand > csl) or (side == "sell" and cand < csl)) else csl
def walk(side, entry, osl, plan, start_idx, max_bars=480):
    filled, csl, amb = [], osl, []
    tgt = {leg: plan[leg]["price"] for leg in ("tp1", "tp2", "tp3")}
    end = min(len(bars), start_idx + max_bars)
    for idx in range(start_idx, end):
        hi, lo = bars[idx].high, bars[idx].low
        sl_hit = (side == "buy" and lo <= csl) or (side == "sell" and hi >= csl)
        legs = []
        for leg in ("tp1", "tp2", "tp3"):
            if leg in filled: continue
            t = tgt[leg]
            if (side == "buy" and hi >= t) or (side == "sell" and lo <= t): legs.append(leg)
            else: break
        if sl_hit and legs: amb.append(idx)
        if sl_hit:
            r = _agg_r(side, entry, osl, plan, filled, csl)
            return ("win" if r > 0 else "loss", r, idx - start_idx + 1, amb, list(filled))
        for leg in legs:
            filled.append(leg); csl = _ratchet(side, entry, osl, csl, filled, plan)
        if "tp3" in filled:
            r = _agg_r(side, entry, osl, plan, filled, tgt["tp3"])
            return ("win", r, idx - start_idx + 1, amb, list(filled))
    return ("open", None, end - start_idx, amb, list(filled))

def plan_dict(tp, entry):
    rpu = tp.risk_per_unit
    return {leg: {"price": p, "target_r": abs(p - entry) / rpu, "fraction": f}
            for leg, p, f in (("tp1", tp.tp1, tp.tp1_qty_fraction),
                              ("tp2", tp.tp2, tp.tp2_qty_fraction),
                              ("tp3", tp.tp3, tp.tp3_qty_fraction))}

# ---------- VALIDATION V1: fired rows reproduce stored plan ----------
print("\n=== V1: build_trade_plan(stored inputs, TAKER) == stored fired plan ===")
v1 = v1n = 0; v1fail = []
for r in rows("VAL"):
    ts, entry, side, atr_u, swl, swh, res, sup, sl, t1, t2, t3, slm, t2m = r
    tp = build_trade_plan(entry=fnum(entry), side=side, atr=fnum(atr_u),
                          swing_low=fnum(swl), swing_high=fnum(swh),
                          resistance=fnum(res), support=fnum(sup), cfg=CFG, fees=FEES_TK)
    v1n += 1
    ok = (tp.should_trade and abs(tp.stop_loss - fnum(sl)) < 0.5
          and abs(tp.tp1 - fnum(t1)) < 0.5 and abs(tp.tp2 - fnum(t2)) < 0.5
          and abs(tp.tp3 - fnum(t3)) < 0.5 and tp.sl_method == slm and tp.tp2_method == t2m)
    if ok: v1 += 1
    else: v1fail.append((ts[5:16], round(tp.stop_loss, 1), sl, tp.sl_method, slm, tp.tp2_method, t2m))
print(f"V1 plan-match: {v1}/{v1n}")
for f in v1fail[:10]: print("   V1 MISMATCH:", f)
V1_OK = (v1 == v1n and v1n > 0)
print(f"V1 verdict: {'PASS' if V1_OK else 'FAIL -> reconstruction suspect'}")

# ---------- DECL reconstruction + walk ----------
print("\n=== DECL reconstruction + skip-reproduce guard + walk ===")
recs = []; seen = set()
vskip = vskipn = 0
for r in rows("DECL"):
    ts, entry, side, atr_u, swl, swh, res, sup, tier, sig = r
    entry = fnum(entry); atr_u = fnum(atr_u)
    kw = dict(side=side, atr=atr_u, swing_low=fnum(swl), swing_high=fnum(swh),
              resistance=fnum(res), support=fnum(sup), cfg=CFG)
    tp_tk = build_trade_plan(entry=entry, fees=FEES_TK, **kw)
    tp_mk = build_trade_plan(entry=entry, fees=FEES_MK, **kw)
    tp_z = build_trade_plan(entry=entry, fees=FEES_ZERO, **kw)
    vskipn += 1
    repro = (tp_tk.skip_reason == "fees_too_high_for_risk")
    if repro: vskip += 1
    ci = idx_closed(iso_to_ms(ts))
    key = (ci, side)
    dup = key in seen; seen.add(key)
    rec = dict(ts=ts, side=side, entry=entry, tier=tier, sig=sig, ci=ci, dup=dup,
               repro=repro, maker_clears=tp_mk.should_trade,
               z_ok=tp_z.should_trade, skip_z=tp_z.skip_reason)
    if tp_z.should_trade:
        risk = tp_z.risk_per_unit
        rec["risk"] = risk; rec["r_pct"] = risk / entry
        rec["tp2_dist"] = abs(tp_z.tp2 - entry)
        rec["mstar"] = rec["tp2_dist"] / (RT_TK * entry)        # multiplier that just admits
        rec["tp2_method"] = tp_z.tp2_method
        if ci >= 0 and ci + 1 < len(bars):
            o, gr, nb, amb, fl = walk(side, entry, tp_z.stop_loss, plan_dict(tp_z, entry), ci + 1)
            rec.update(outcome=o, grossR=gr, nbars=nb, namb=len(amb), filled=",".join(fl))
            if gr is not None:
                rec["feeR_tk"] = RT_TK * entry / risk; rec["netR_tk"] = gr - rec["feeR_tk"]
                rec["feeR_mk"] = RT_MK * entry / risk; rec["netR_mk"] = gr - rec["feeR_mk"]
            # fix(b) REALISTIC: walk the actual maker-admitted plan (TP1 at maker floor, not 0.5R)
            if tp_mk.should_trade and tp_mk.risk_per_unit > 0:
                om, grm, _, ambm, flm = walk(side, entry, tp_mk.stop_loss, plan_dict(tp_mk, entry), ci + 1)
                if grm is not None:
                    rec["grossR_mkplan"] = grm
                    rec["netR_mkplan"] = grm - RT_MK * entry / tp_mk.risk_per_unit
                    rec["amb_mkplan"] = len(ambm)
        else:
            rec["outcome"] = "no_bars"
    recs.append(rec)
print(f"skip-reproduce guard (taker build == fees_too_high): {vskip}/{vskipn}")

# ---------- report table ----------
def fm(x, n=3): return "-" if x is None else (f"{x:.{n}f}" if isinstance(x, float) else str(x))
print(f"\n{'ts':16} {'side':4} {'entry':9} {'r_pct%':7} {'m*':5} {'mk?':3} {'out':5} "
      f"{'grossR':7} {'netTk':7} {'netMk':7} {'amb':3} {'filled':9}")
for x in recs:
    if x["dup"]: continue
    print(f"{x['ts'][5:16]:16} {x['side']:4} {fm(x['entry'],1):9} "
          f"{fm(x.get('r_pct',0)*100,3):7} {fm(x.get('mstar'),2):5} "
          f"{('Y' if x.get('maker_clears') else 'n'):3} {fm(x.get('outcome')):5} "
          f"{fm(x.get('grossR')):7} {fm(x.get('netR_tk')):7} {fm(x.get('netR_mk')):7} "
          f"{fm(x.get('namb')):3} {x.get('filled',''):9}")

# ---------- aggregate (resolved unique) ----------
uniq = [x for x in recs if not x["dup"]]
walked = [x for x in uniq if x.get("grossR") is not None and x.get("outcome") in ("win", "loss")]
openr = [x for x in uniq if x.get("outcome") in ("open", "no_bars")]
amb_n = [x for x in walked if x.get("namb", 0) > 0]
def stats(s, key):
    return (sum(x[key] for x in s) / len(s)) if s else 0.0
print(f"\n=== AGGREGATE (raw DECL={len(recs)}  unique setups={len(uniq)}  "
      f"resolved={len(walked)}  open/no_bars={len(openr)}  ambiguous3m={len(amb_n)}) ===")
if walked:
    w = sum(1 for x in walked if x["outcome"] == "win"); l = len(walked) - w
    print(f"resolved: n={len(walked)} W={w} L={l}")
    print(f"  GROSS  expectancy/trade: {stats(walked,'grossR'):+.4f}R   cum {sum(x['grossR'] for x in walked):+.3f}R")
    print(f"  NET-TAKER expectancy/trade: {stats(walked,'netR_tk'):+.4f}R   cum {sum(x['netR_tk'] for x in walked):+.3f}R")
    print(f"  NET-MAKER expectancy/trade: {stats(walked,'netR_mk'):+.4f}R   cum {sum(x['netR_mk'] for x in walked):+.3f}R")
    mk = [x for x in walked if x["maker_clears"]]
    bmk = [x for x in walked if not x["maker_clears"]]
    print(f"\n  -- fix(b) maker-ADMITTED subset (builder verdict): n={len(mk)} "
          f"W={sum(1 for x in mk if x['outcome']=='win')} L={sum(1 for x in mk if x['outcome']=='loss')}")
    if mk:
        print(f"     [structural-plan TP1=0.5R] gross {stats(mk,'grossR'):+.4f}R  net-maker {stats(mk,'netR_mk'):+.4f}R")
        mkp = [x for x in mk if x.get("netR_mkplan") is not None]
        if mkp:
            ambp = sum(1 for x in mkp if x.get("amb_mkplan", 0) > 0)
            print(f"     [REALISTIC fix-b plan, TP1 at maker floor] n={len(mkp)} amb={ambp}  "
                  f"gross {stats(mkp,'grossR_mkplan'):+.4f}R  net-maker {stats(mkp,'netR_mkplan'):+.4f}R")
    print(f"  -- below-maker-floor subset (declined even under fix b): n={len(bmk)}")
    if bmk:
        print(f"     gross {stats(bmk,'grossR'):+.4f}R  net-taker {stats(bmk,'netR_tk'):+.4f}R  net-maker {stats(bmk,'netR_mk'):+.4f}R")
    # outcome by filled-legs bucket (the "chart-win vs net-loss" anatomy)
    print("\n  -- outcome anatomy by deepest leg reached --")
    def bucket(x):
        if x["outcome"] == "loss": return "stopped (-1R)"
        f = x.get("filled", "")
        if "tp3" in f: return "tp1+tp2+tp3"
        if "tp2" in f: return "tp1+tp2"
        if "tp1" in f: return "tp1-only"
        return "other"
    for b in ("tp1-only", "tp1+tp2", "tp1+tp2+tp3", "stopped (-1R)"):
        g = [x for x in walked if bucket(x) == b]
        if g:
            print(f"     {b:16} n={len(g):2}  gross {stats(g,'grossR'):+.3f}R  "
                  f"net-taker {stats(g,'netR_tk'):+.3f}R  net-maker {stats(g,'netR_mk'):+.3f}R")
    # r_pct distribution
    rp = sorted(x["r_pct"] * 100 for x in walked)
    print(f"\n  r_pct%(stop/entry) over resolved: min {rp[0]:.3f}  med {rp[len(rp)//2]:.3f}  max {rp[-1]:.3f}")
    ms = sorted(x["mstar"] for x in walked)
    print(f"  m*(admit multiplier) over resolved: min {ms[0]:.2f}  med {ms[len(ms)//2]:.2f}  max {ms[-1]:.2f}")
print("\n=== END FGHARNESS ===")
