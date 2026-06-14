"""Entry-timing counterfactual harness — early (signal-bar) vs late (fire-bar) entry.
READ-ONLY. For each fire: hold the recorded plan GEOMETRY fixed (stop-distance + TP
R-multiples) and re-anchor entry at (a) the signal bar vs (b) the confirmation/fire bar,
walk each forward on 3m bars. Isolates the pure latency/price effect of the gate chain
(dominated by the PA-redeem wait). Net at confirmed VIP3 fees (taker 0.04% / maker 0.014%).

Signal bar = original_cached_at (redeem) else fire ts. Fire bar = fire ts.
For non-redeem fires signal bar == fire bar -> delta == 0 by construction.
"""
from __future__ import annotations
import os, sys, csv, io, importlib.util
from dataclasses import dataclass
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
_TP = os.path.join(HERE, "trading_corp", "agents", "strategies", "trade_plan.py")
_spec = importlib.util.spec_from_file_location("tp_standalone", _TP)
_tp = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = _tp; _spec.loader.exec_module(_tp)
FeeConfig = _tp.FeeConfig
RT_TK = FeeConfig().round_trip_cost_pct()                 # 0.00090 taker exits
RT_MK = FeeConfig(tp_is_maker=True).round_trip_cost_pct() # 0.00064 maker exits
print(f"=== IMPORTS OK  RT_taker={RT_TK:.5f} RT_maker={RT_MK:.5f} ===")

@dataclass
class Bar:
    ts_ms: int; open: float; high: float; low: float; close: float

raw = open(os.path.join(HERE, "qdata3.out"), encoding="utf-8-sig").read()
sec, cur = {}, None
for line in raw.splitlines():
    if line.startswith("===") and line.endswith("==="):
        cur = line.strip("="); sec[cur] = []; continue
    if cur and line.strip(): sec[cur].append(line)
def rows(name, d=','): return list(csv.reader(io.StringIO("\n".join(sec.get(name, []))), delimiter=d))
def fnum(x):
    try: return float(x)
    except: return None

bars = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows("BARS3M", ',')]
bars.sort(key=lambda b: b.ts_ms)
print(f"bars3m: {len(bars)}  range {bars[0].ts_ms}..{bars[-1].ts_ms}")
def iso_ms(s): return int(dt.datetime.fromisoformat(s).timestamp() * 1000)
def idx_at(ms):
    lo, hi, ans = 0, len(bars) - 1, -1
    while lo <= hi:
        m = (lo + hi) // 2
        if bars[m].ts_ms <= ms: ans = m; lo = m + 1
        else: hi = m - 1
    return ans
def idx_closed(ms):
    i = idx_at(ms)
    while i >= 0 and bars[i].ts_ms + 180000 > ms: i -= 1
    return i

# ---- walk: SL-first tie, ordered TP fills, BE-after-TP1 / TP1-after-TP2 ratchet ----
def _r_at(side, e, osl, px):
    risk = abs(e - osl)
    return 0.0 if risk <= 0 else (1.0 if side == "buy" else -1.0) * (px - e) / risk
def _agg(side, e, osl, plan, filled, exitpx):
    tot = ff = 0.0
    for leg in ("tp1", "tp2", "tp3"):
        if leg in filled: tot += plan[leg]["r"] * plan[leg]["f"]; ff += plan[leg]["f"]
    unf = max(0.0, 1.0 - ff)
    if unf > 0: tot += _r_at(side, e, osl, exitpx) * unf
    return tot
def _ratchet(side, e, osl, csl, filled, plan):
    f = set(filled)
    if "tp1" not in f: return csl
    if "tp2" not in f:
        return e if ((side == "buy" and e > csl) or (side == "sell" and e < csl)) else csl
    c = plan["tp1"]["px"]
    return c if ((side == "buy" and c > csl) or (side == "sell" and c < csl)) else csl
def walk(side, e, osl, plan, start, max_bars=480):
    filled, csl, amb = [], osl, 0
    tgt = {lg: plan[lg]["px"] for lg in ("tp1", "tp2", "tp3")}
    end = min(len(bars), start + max_bars)
    if start < 0 or start >= len(bars): return ("no_bars", None, 0, [])
    for idx in range(start, end):
        hi, lo = bars[idx].high, bars[idx].low
        sl = (side == "buy" and lo <= csl) or (side == "sell" and hi >= csl)
        legs = []
        for lg in ("tp1", "tp2", "tp3"):
            if lg in filled: continue
            t = tgt[lg]
            if (side == "buy" and hi >= t) or (side == "sell" and lo <= t): legs.append(lg)
            else: break
        if sl and legs: amb += 1
        if sl:
            r = _agg(side, e, osl, plan, filled, csl)
            return ("win" if r > 0 else "loss", r, amb, list(filled))
        for lg in legs:
            filled.append(lg); csl = _ratchet(side, e, osl, csl, filled, plan)
        if "tp3" in filled:
            return ("win", _agg(side, e, osl, plan, filled, tgt["tp3"]), amb, list(filled))
    return ("open", None, amb, list(filled))

def mkplan(side, entry, stop_dist, rmul, fr):
    s = (entry + stop_dist) if side == "sell" else (entry - stop_dist)
    px = {lg: (entry - rmul[lg] * stop_dist) if side == "sell" else (entry + rmul[lg] * stop_dist)
          for lg in ("tp1", "tp2", "tp3")}
    return s, {lg: {"px": px[lg], "r": rmul[lg], "f": fr[lg]} for lg in ("tp1", "tp2", "tp3")}

# ---- match PLAN (full 3-leg) to each FIRE by signal + nearest ts ----
plan_rows = []
for r in rows("PLAN", '|'):
    ts, sig, entry, side, atr, swl, swh, res, sup, sl, t1, t2, t3, slm, t2m = r
    plan_rows.append(dict(ms=iso_ms(ts), sig=sig, entry=fnum(entry), side=side,
                          stop=fnum(sl), t1=fnum(t1), t2=fnum(t2), t3=fnum(t3)))
def match_plan(sig, ms):
    cand = [p for p in plan_rows if p["sig"] == sig]
    return min(cand, key=lambda p: abs(p["ms"] - ms)) if cand else None

FR = {"tp1": 0.25, "tp2": 0.50, "tp3": 0.25}
recs = []
val_entry = []; val_recorded = []
for r in rows("FIRES", '|'):
    (oid, ts, sig, side, e_sig, stop_px, result, res_px, act_r, b2r,
     redeemed, bw, sw, oca, legs, mode) = r
    e_sig = fnum(e_sig); stop_px = fnum(stop_px); act_r = fnum(act_r)
    redeemed = (redeemed == "1"); bw = int(fnum(bw)) if fnum(bw) is not None else 0
    fire_ms = iso_ms(ts)
    sig_ms = iso_ms(oca) if (redeemed and oca) else fire_ms
    p = match_plan(sig, fire_ms)
    if p is None or not e_sig or not stop_px:
        continue
    stop_dist = abs(e_sig - stop_px)
    rmul = {"tp1": abs(p["t1"] - e_sig) / stop_dist,
            "tp2": abs(p["t2"] - e_sig) / stop_dist,
            "tp3": abs(p["t3"] - e_sig) / stop_dist}
    sb = idx_closed(sig_ms); fb = idx_closed(fire_ms)
    if sb < 0 or fb < 0: continue
    p_early = bars[sb].close; p_late = bars[fb].close
    val_entry.append(abs(p_early - e_sig) / e_sig)         # recorded sig price vs sig-bar close
    rec = dict(sig=sig, side=side, ts=ts[5:16], redeemed=redeemed, bw=bw,
               e_sig=e_sig, p_early=p_early, p_late=p_late, stop_dist=stop_dist,
               rmul=rmul, result=result, act_r=act_r, sb=sb, fb=fb,
               tp1=p["t1"], open=(result == "" or result is None))
    # early walk (signal bar)
    s_a, pl_a = mkplan(side, p_early, stop_dist, rmul, FR)
    oa, gra, _, fa = walk(side, p_early, s_a, pl_a, sb + 1)
    # late walk (fire bar)
    s_b, pl_b = mkplan(side, p_late, stop_dist, rmul, FR)
    ob, grb, _, fb_ = walk(side, p_late, s_b, pl_b, fb + 1)
    rec["gr_a"], rec["out_a"], rec["fill_a"] = gra, oa, ",".join(fa)
    rec["gr_b"], rec["out_b"], rec["fill_b"] = grb, ob, ",".join(fb_)
    if gra is not None:
        rec["net_a_tk"] = gra - RT_TK * p_early / stop_dist
        rec["net_a_mk"] = gra - RT_MK * p_early / stop_dist
    if grb is not None:
        rec["net_b_tk"] = grb - RT_TK * p_late / stop_dist
        rec["net_b_mk"] = grb - RT_MK * p_late / stop_dist
    # Q3 move decomposition (favorable move spent pre-fire vs to TP1), redeem only
    if side == "sell":
        mv_total = e_sig - p["t1"]; mv_pre = e_sig - p_late
    else:
        mv_total = p["t1"] - e_sig; mv_pre = p_late - e_sig
    rec["frac_pre"] = (mv_pre / mv_total) if mv_total else None
    # validate recorded actual_r vs my early gross for NON-redeem (signal bar == fire bar)
    if not redeemed and act_r is not None and gra is not None:
        val_recorded.append(abs(gra - act_r))
    recs.append(rec)

print(f"\nfires loaded: {len(recs)}  (matched plan + bars)")
print(f"VAL recorded-entry vs signal-bar-close: max rel-diff {max(val_entry)*100:.3f}%  "
      f"mean {sum(val_entry)/len(val_entry)*100:.3f}%")
if val_recorded:
    print(f"VAL non-redeem recorded actual_r vs my early gross: n={len(val_recorded)} "
          f"max |Δ| {max(val_recorded):.3f}  mean {sum(val_recorded)/len(val_recorded):.3f}")

def fm(x, n=3): return "-" if x is None else (f"{x:.{n}f}" if isinstance(x, float) else str(x))
print(f"\n{'ts':16}{'sig':22}{'rdm':4}{'bw':3} {'gr_a':6} {'gr_b':6} {'dA-B':6} "
      f"{'netA_tk':7} {'netB_tk':7} {'fracPre':7} {'out_a':5}/{'out_b':5}")
for x in sorted(recs, key=lambda z: -z["bw"]):
    if x["open"]: continue
    d = (x["gr_a"] - x["gr_b"]) if (x["gr_a"] is not None and x["gr_b"] is not None) else None
    print(f"{x['ts']:16}{x['sig'][:21]:22}{('Y' if x['redeemed'] else 'n'):4}{x['bw']:<3} "
          f"{fm(x['gr_a']):6} {fm(x['gr_b']):6} {fm(d):6} "
          f"{fm(x.get('net_a_tk')):7} {fm(x.get('net_b_tk')):7} {fm(x.get('frac_pre')):7} "
          f"{str(x['out_a'])[:5]:5}/{str(x['out_b'])[:5]:5}")

# ---- aggregates ----
def agg(s, k): return sum(x[k] for x in s) / len(s) if s else 0.0
closed = [x for x in recs if not x["open"] and x["gr_a"] is not None and x["gr_b"] is not None]
redeem = [x for x in closed if x["redeemed"] and x["bw"] >= 1]
nonred = [x for x in closed if x["bw"] == 0]
print(f"\n=== AGGREGATE (closed={len(closed)}  redeem bw>=1={len(redeem)}  bw0/nonredeem={len(nonred)}) ===")
for label, s in (("ALL closed", closed), ("REDEEM (bw>=1)", redeem), ("bw==0/nonredeem", nonred)):
    if not s: continue
    print(f"\n[{label}] n={len(s)}")
    rec_act = [x['act_r'] for x in s if x['act_r'] is not None]
    if rec_act:
        print(f"  RECORDED actual_r (paper, books stale signal px): mean {sum(rec_act)/len(rec_act):+.4f}R  (n={len(rec_act)})")
    print(f"  (a) EARLY  gross {agg(s,'gr_a'):+.4f}R  net-taker {agg([x for x in s if 'net_a_tk' in x],'net_a_tk'):+.4f}R  net-maker {agg([x for x in s if 'net_a_mk' in x],'net_a_mk'):+.4f}R")
    print(f"  (b) LATE   gross {agg(s,'gr_b'):+.4f}R  net-taker {agg([x for x in s if 'net_b_tk' in x],'net_b_tk'):+.4f}R  net-maker {agg([x for x in s if 'net_b_mk' in x],'net_b_mk'):+.4f}R")
    deltas = [x['gr_a'] - x['gr_b'] for x in s]
    print(f"  DELTA (early-late) gross: mean {sum(deltas)/len(deltas):+.4f}R  cum {sum(deltas):+.3f}R")

# Q3 move decomposition for redeem fires whose EARLY walk reaches TP1
print("\n=== Q3 MOVE DECOMPOSITION (redeem bw>=1, favorable move spent before fire) ===")
fp = sorted(x["frac_pre"] for x in redeem if x["frac_pre"] is not None)
if fp:
    print(f"  frac of (signal->TP1) move already spent by fire-time: "
          f"min {fp[0]:.2f}  med {fp[len(fp)//2]:.2f}  max {fp[-1]:.2f}  (n={len(fp)})")
    print(f"  redeem fires where >50% of the TP1 move was spent pre-fire: "
          f"{sum(1 for f in fp if f > 0.5)}/{len(fp)}")
print("\n=== END ETHARNESS ===")
