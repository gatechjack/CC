"""TP-ladder recalibration harness (analysis-only, READ-ONLY data).

Reuses the strategy's OWN build_trade_plan + the validated q3harness walk
mechanics (SL-first worst-case, ordered TP fills, BE-after-TP1 /
TP1-after-TP2 ratchet, full-position round-trip fee). Re-walks the SAME
trade set under alternative TP ladders (StrategyConfig variants that hold
ALL SL params fixed and vary only the TP legs) and reports net-after-fee
metrics.

Sets:
  (a) post-fix taken trades (VAL_TAKEN stored inputs + PTR recorded R)
  (b) silence-window vol_tier_extreme suppressed signals (reconstructed)

Validation gates BEFORE trusting any alt ladder:
  V1     baseline plan rebuilt from stored inputs == stored plan
  VWALK  baseline walk reproduces recorded actual_r_multiple on set(a)

NOTHING here changes code/config/prod. Output is a table for the report.
"""
from __future__ import annotations
import os, sys, csv, io, json, datetime as dt
from dataclasses import replace
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_corp.data.live_bar_cache import Bar, LiveBarCache
from trading_corp.agents.strategies.swing import get_recent_swing
from trading_corp.agents.strategies.levels import get_htf_levels
from trading_corp.agents.strategies.trade_plan import build_trade_plan, StrategyConfig, FeeConfig

CFG0 = StrategyConfig(); FEES = FeeConfig()
RT = FEES.round_trip_cost_pct()
PROX_PCT = 0.30          # proximity_to_support gate threshold (% of price); see code grounding
EPS = 1e-9

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tpdata.out")
raw = open(OUT, encoding="utf-8-sig").read()
sec, cur = {}, None
for line in raw.splitlines():
    if line.startswith("===") and line.endswith("==="):
        cur = line.strip("="); sec[cur] = []; continue
    if cur and line.strip():
        sec[cur].append(line)
def rows(name): return list(csv.reader(io.StringIO("\n".join(sec.get(name, [])))))

bars = [Bar(ts_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
            close=float(r[4]), volume=0.0) for r in rows("BARS3M")]
bars.sort(key=lambda b: b.ts_ms)

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
_LC = LiveBarCache()
def cache_atr(end_idx, period=14):
    if end_idx < 0: return None
    _LC.bars = bars[max(0, end_idx - 59): end_idx + 1]
    return _LC.get_atr(period)
def fnum(x):
    try: return float(x)
    except: return None

# ---------- walk (generalized N-leg; q3harness mechanics) ----------
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
    """Returns (outcome, grossR, nbars, n_ambiguous, filled_legs)."""
    filled = []; csl = osl; amb = []
    tgt = {leg: plan[leg]["price"] for leg in ("tp1", "tp2", "tp3")}
    end = min(len(bars), start_idx + max_bars)
    for idx in range(start_idx, end):
        hi, lo = bars[idx].high, bars[idx].low
        sl_hit = (side == "buy" and lo <= csl) or (side == "sell" and hi >= csl)
        legs = []
        for leg in ("tp1", "tp2", "tp3"):
            if leg in filled: continue
            if plan[leg]["fraction"] <= 0:           # dropped leg: never a real fill
                if (side == "buy" and hi >= tgt[leg]) or (side == "sell" and lo <= tgt[leg]):
                    continue                          # skip past it without filling
                else: break
            t = tgt[leg]
            if (side == "buy" and hi >= t) or (side == "sell" and lo <= t): legs.append(leg)
            else: break
        if sl_hit and legs: amb.append(idx)
        if sl_hit:
            r = _agg_r(side, entry, osl, plan, filled, csl)
            return ("win" if r > 0 else "loss", r, idx - start_idx + 1, len(amb), list(filled))
        for leg in legs:
            filled.append(leg); csl = _ratchet(side, entry, osl, csl, filled, plan)
        ff = sum(plan[leg]["fraction"] for leg in filled)
        if ff >= 1.0 - EPS:                           # position fully closed via TP legs
            deepest = filled[-1] if filled else "tp1"
            r = _agg_r(side, entry, osl, plan, filled, tgt[deepest])
            return ("win", r, idx - start_idx + 1, len(amb), list(filled))
    # ran out of bars: unfilled fraction marked-to-last-close
    r = _agg_r(side, entry, osl, plan, filled, bars[end - 1].close) if filled else None
    return ("open", r, end - start_idx, len(amb), list(filled))

def make_plan(tp, entry):
    """TradePlan -> walk plan dict (price/target_r/fraction per leg)."""
    rpu = tp.risk_per_unit
    return {"tp1": {"price": tp.tp1, "target_r": abs(tp.tp1 - entry) / rpu, "fraction": tp.tp1_qty_fraction},
            "tp2": {"price": tp.tp2, "target_r": abs(tp.tp2 - entry) / rpu, "fraction": tp.tp2_qty_fraction},
            "tp3": {"price": tp.tp3, "target_r": abs(tp.tp3 - entry) / rpu, "fraction": tp.tp3_qty_fraction}}

# ---------- build trade sets ----------
# set(a): stored inputs (entry,side,atr,swl,swh,res,sup) + recorded R matched by ts
PTR = {}
for r in rows("PTR_OUTCOMES"):
    PTR[r[0]] = dict(oid=r[1], entry=fnum(r[2]), stop=fnum(r[3]), recR=fnum(r[4]),
                     result=r[5], filled=r[6], tp_r=fnum(r[7]))
set_a = []
for r in rows("VAL_TAKEN"):
    ts, entry, side, atr_u, swl, swh, res, sup, sl, t1, t2, t3, slm, t2m, should = r
    if should != "1": continue
    set_a.append(dict(src="a", ts=ts, entry=fnum(entry), side=side, atr=fnum(atr_u),
                      swl=fnum(swl), swh=fnum(swh), res=fnum(res), sup=fnum(sup),
                      sl=fnum(sl), t1=fnum(t1), t2=fnum(t2), t3=fnum(t3),
                      slm=slm, t2m=t2m, ptr=PTR.get(ts)))

# set(b): silence vol_tier_extreme suppressed, reconstructed (q3harness reject method)
htf = {}
for r in rows("SIL_HTF"):
    htf.setdefault(r[1], []).append((r[0], r[2], fnum(r[3]), fnum(r[4]), fnum(r[5])))
def htf_match(sig, ts):
    c = htf.get(sig, [])
    if not c: return (None, None, None, None)
    b = min(c, key=lambda x: abs(iso_to_ms(x[0]) - iso_to_ms(ts)))
    return b[1], b[2], b[3], b[4]   # reason, atr_d1, dsup, dres
set_b = []; seen = set()
for r in rows("SIL_SCORE"):
    ts, sig, price, side = r[0], r[1], fnum(r[2]), r[3]
    reason, atr_d1, dsup, dres = htf_match(sig, ts)
    if reason != "vol_tier_extreme": continue
    ci = idx_closed(iso_to_ms(ts))
    key = (ci, side)
    if key in seen: continue
    seen.add(key)
    prox_block = (side == "sell" and dsup is not None and dsup < PROX_PCT)
    set_b.append(dict(src="b", ts=ts, sig=sig, entry=price, side=side, ci=ci,
                      atr=cache_atr(ci), swl=get_recent_swing(bars, ci, side="low", n=CFG0.swing_n, max_lookback=CFG0.swing_max_lookback),
                      swh=get_recent_swing(bars, ci, side="high", n=CFG0.swing_n, max_lookback=CFG0.swing_max_lookback),
                      atr_d1=atr_d1, dsup=dsup, prox_block=prox_block))
for t in set_b:
    t["res"], t["sup"] = get_htf_levels(bars, t["ci"], htf_minutes=CFG0.htf_minutes,
                                        lookback_bars_htf=CFG0.htf_lookback_bars, n=CFG0.swing_n)

# ---------- candidate run ----------
def run(cfg, trades):
    out = []
    for t in trades:
        tp = build_trade_plan(entry=t["entry"], side=t["side"], atr=t["atr"] or 0.0,
                              swing_low=t["swl"], swing_high=t["swh"],
                              resistance=t["res"], support=t["sup"], cfg=cfg, fees=FEES)
        if not tp.should_trade:
            out.append(dict(t=t, skip=tp.skip_reason)); continue
        if t["src"] == "a":
            start = idx_at(iso_to_ms(t["ts"])) + 1
        else:
            start = t["ci"] + 1
        oc, gr, nb, namb, filled = walk(t["side"], t["entry"], tp.stop_loss, make_plan(tp, t["entry"]), start)
        if gr is None:
            out.append(dict(t=t, skip="open_no_fill")); continue
        feeR = RT * t["entry"] / tp.risk_per_unit
        out.append(dict(t=t, oc=oc, grossR=gr, feeR=feeR, netR=gr - feeR, namb=namb,
                        filled=filled, depth=(len(filled)), tp=tp))
    return out

def agg(res, label):
    walked = [x for x in res if "netR" in x]
    skips = [x for x in res if "skip" in x]
    n = len(walked)
    if n == 0:
        return dict(label=label, n=0, skips=len(skips))
    g = sum(x["grossR"] for x in walked); ncum = sum(x["netR"] for x in walked)
    W = sum(1 for x in walked if x["grossR"] > 0); L = n - W
    netpos = sum(1 for x in walked if x["netR"] > 0)
    fee = sum(x["feeR"] for x in walked) / n
    fill = {0: 0, 1: 0, 2: 0, 3: 0}
    for x in walked: fill[x["depth"]] = fill.get(x["depth"], 0) + 1
    nets = sorted(x["netR"] for x in walked)
    return dict(label=label, n=n, skips=len(skips), W=W, L=L,
                grossavg=g / n, netavg=ncum / n, netcum=ncum,
                pctnetpos=100.0 * netpos / n, feeavg=fee, fill=fill, nets=nets,
                skipreasons=[x["skip"] for x in skips])

# ---------- candidate ladders (SL params fixed; only TP legs vary) ----------
def C(**kw): return replace(CFG0, **kw)
CANDS = [
    ("baseline 0.5/1.0/2.5 @25/50/25",        C()),
    ("H1 heavy-TP1 0.5/1.0/2.5 @40/40/20",    C(tp1_qty_fraction=0.40, tp2_qty_fraction=0.40, tp3_qty_fraction=0.20)),
    ("H2 pull-in 0.5/0.8/1.3 @25/50/25",      C(tp2_r_default=0.8, tp3_r_target=1.3)),
    ("H1+H2 0.5/0.8/1.3 @40/40/20",           C(tp2_r_default=0.8, tp3_r_target=1.3, tp1_qty_fraction=0.40, tp2_qty_fraction=0.40, tp3_qty_fraction=0.20)),
    ("2-leg drop-TP3 0.5/1.0 @40/60",         C(tp2_r_default=1.0, tp3_r_target=99.0, tp1_qty_fraction=0.40, tp2_qty_fraction=0.60, tp3_qty_fraction=0.0)),
    ("2-leg pulled 0.5/0.9 @50/50",           C(tp2_r_default=0.9, tp3_r_target=99.0, tp1_qty_fraction=0.50, tp2_qty_fraction=0.50, tp3_qty_fraction=0.0)),
    ("far-TP1 0.8/1.3/2.5 @25/50/25",         C(tp1_r_target=0.8, tp2_r_default=1.3)),
    ("single-tgt feefloor @100%",             C(tp2_r_default=99.0, tp3_r_target=99.0, tp1_qty_fraction=1.0, tp2_qty_fraction=0.0, tp3_qty_fraction=0.0)),
    ("single-tgt 1.0R @100%",                 C(tp1_r_target=1.0, tp2_r_default=99.0, tp3_r_target=99.0, tp1_qty_fraction=1.0, tp2_qty_fraction=0.0, tp3_qty_fraction=0.0)),
]

def show(title, trades):
    print(f"\n{'='*96}\n{title}  (N_input={len(trades)})\n{'='*96}")
    print(f"{'candidate':40} {'N':>3} {'sk':>2} {'W/L':>6} {'grossR':>7} {'netR':>7} {'netcum':>7} {'%net+':>6} {'feeR':>5}  fills(0/1/2/3)")
    res_all = {}
    for label, cfg in CANDS:
        a = agg(run(cfg, trades), label); res_all[label] = a
        if a["n"] == 0:
            print(f"{label:40} {0:>3} {a['skips']:>2}  -- all skipped"); continue
        f = a["fill"]
        print(f"{label:40} {a['n']:>3} {a['skips']:>2} {a['W']:>2}/{a['L']:<3} "
              f"{a['grossavg']:>+7.3f} {a['netavg']:>+7.3f} {a['netcum']:>+7.2f} {a['pctnetpos']:>5.0f}% "
              f"{a['feeavg']:>5.3f}  {f.get(0,0)}/{f.get(1,0)}/{f.get(2,0)}/{f.get(3,0)}")
    return res_all

print(f"=== TP-RECAL HARNESS ===  round_trip_cost={RT:.5f}  fee_drag~{RT:.4f}*price/risk")
print(f"set(a) taken (should_trade=1): {len(set_a)}   set(b) vol-zeroed unique: {len(set_b)} "
      f"(prox-block {sum(1 for t in set_b if t['prox_block'])})")

# ---------- V1 + VWALK validation on set(a) baseline ----------
print("\n=== V1: baseline plan from stored inputs == stored plan ===")
v1 = v1n = 0
for t in set_a:
    tp = build_trade_plan(entry=t["entry"], side=t["side"], atr=t["atr"], swing_low=t["swl"],
                          swing_high=t["swh"], resistance=t["res"], support=t["sup"], cfg=CFG0, fees=FEES)
    v1n += 1
    ok = (abs(tp.stop_loss - t["sl"]) < 0.5 and abs(tp.tp1 - t["t1"]) < 0.5
          and abs(tp.tp2 - t["t2"]) < 0.5 and abs(tp.tp3 - t["t3"]) < 0.5)
    v1 += ok
    if not ok: print(f"  V1 MISMATCH {t['ts'][5:16]} sl {tp.stop_loss:.1f}/{t['sl']:.1f} t1 {tp.tp1:.1f}/{t['t1']:.1f}")
print(f"V1: {v1}/{v1n}")

print("\n=== VWALK: baseline walk vs recorded actual_r_multiple (set a) ===")
vw = vwn = 0; deltas = []
bl = run(CFG0, set_a)
for x in bl:
    if "netR" not in x: continue
    ptr = x["t"]["ptr"]
    if not ptr or ptr["recR"] is None: continue
    vwn += 1; d = x["grossR"] - ptr["recR"]; deltas.append((x["t"]["ts"][5:16], ptr["oid"], round(x["grossR"],3), ptr["recR"], round(d,3), ",".join(x["filled"]), ptr["filled"]))
    if abs(d) < 0.05: vw += 1
print(f"VWALK within 0.05R: {vw}/{vwn}")
for d in deltas:
    flag = "" if abs(d[4]) < 0.05 else "  <-- DELTA"
    print(f"  {d[0]} {d[1]} walk {d[2]:>+6} rec {d[3]:>+6} d {d[4]:>+6} legs[{d[5]}] rec{d[6]}{flag}")

setb_trade = [t for t in set_b if not t["prox_block"]]    # post-fix proximity gate applied
combined = set_a + setb_trade

ra  = show("SET (a) — post-fix taken trades (recorded-validated)", set_a)
rb  = show("SET (b) — silence-window vol-zeroed (reconstructed, same ~3-4% regime)", setb_trade)
rab = show("SET (a)+(b) COMBINED", combined)

# ---------- robustness / outlier-dependence on leaders (combined) ----------
def robust(label, cfg, trades):
    res = [x for x in run(cfg, trades) if "netR" in x]
    nets = sorted(x["netR"] for x in res); n = len(nets)
    cum = sum(nets)
    losses = [x for x in nets if x < 0]
    q = lambda p: nets[min(n - 1, int(p * n))]
    drop_worst = cum - nets[0]; drop_best = cum - nets[-1]
    drop_both = cum - nets[0] - nets[-1]
    print(f"\n[{label}]  N={n} netcum={cum:+.2f} netavg={cum/n:+.3f}")
    print(f"   netR dist: min {nets[0]:+.2f} | q25 {q(.25):+.2f} | med {q(.5):+.2f} | q75 {q(.75):+.2f} | max {nets[-1]:+.2f}")
    print(f"   losers(netR<0): {len(losses)}/{n}  sum {sum(losses):+.2f}")
    print(f"   netavg drop-worst {drop_worst/(n-1):+.3f} | drop-best {drop_best/(n-1):+.3f} | drop-both {drop_both/(n-2):+.3f}")

print(f"\n{'='*96}\nROBUSTNESS — leaders on COMBINED set (outlier-dependence)\n{'='*96}")
for label, cfg in CANDS:
    if label.startswith(("2-leg pulled", "single-tgt feefloor", "H1+H2", "2-leg drop")):
        robust(label, cfg, combined)

# ---------- fee sensitivity: is the LADDER the lever, or the FEE TIER? ----------
print(f"\n{'='*96}\nFEE SENSITIVITY (combined set) — net avgR by fee assumption (ladder held)\n{'='*96}")
RT_mk = FeeConfig(tp_is_maker=True).round_trip_cost_pct()
ratio = RT_mk / RT
print(f"  taker rt {RT:.5f} (current, exits taker) | maker-exit rt {RT_mk:.5f} (ratio {ratio:.3f}) | zero-fee = gross")
print(f"  {'candidate':40} {'gross/net@0%':>12} {'net@0.064%mk':>13} {'net@0.09%tk':>12}")
for label in ["baseline 0.5/1.0/2.5 @25/50/25", "single-tgt 1.0R @100%",
              "single-tgt feefloor @100%", "2-leg pulled 0.5/0.9 @50/50", "H1+H2 0.5/0.8/1.3 @40/40/20"]:
    a = rab[label]
    print(f"  {label:40} {a['grossavg']:>+12.3f} {a['grossavg']-a['feeavg']*ratio:>+13.3f} {a['netavg']:>+12.3f}")

# ---------- intrabar ambiguity (caveat quantification, P3 70d50f7) ----------
amb_a = [x for x in run(CFG0, set_a) if x.get("namb", 0) > 0]
amb_c = [x for x in run(CFG0, combined) if x.get("namb", 0) > 0]
print(f"\nintrabar-ambiguous trades (>=1 bar with SL+TP both touched), baseline ladder:")
print(f"  set(a): {len(amb_a)}/{len([x for x in run(CFG0,set_a) if 'netR' in x])}  "
      f"combined: {len(amb_c)}/{len([x for x in run(CFG0,combined) if 'netR' in x])}")

# ---------- FEE-MODEL VERIFICATION (operator dispute) ----------
print(f"\n{'='*96}\nFEE-MODEL VERIFICATION\n{'='*96}")
print(f"FeeConfig (== prod YAML bitunix_futures.fees, built via main.py:356 FeeConfig.from_dict):")
print(f"  taker={FEES.taker_fee_pct}  maker={FEES.maker_fee_pct}  slip={FEES.slippage_pct}  "
      f"entry_is_taker={FEES.entry_is_taker}  tp_is_maker={FEES.tp_is_maker}")
print(f"  round_trip = entry_fee + exit_fee + 2*slip = {FEES.taker_fee_pct}+{FEES.taker_fee_pct}+2*{FEES.slippage_pct} = {RT:.5f}")

# hand-walk cf40deeb
hw = next((t for t in set_a if t["ts"].startswith("2026-06-09T04:57")), None)
if hw:
    e, sl = hw["entry"], hw["sl"]; risk = abs(e - sl); stoppct = risk / e * 100
    feeR = RT * e / risk
    rt_risk = 50.0; qty = rt_risk / risk; notional = e * qty
    ef = FEES.taker_fee_pct * notional; xf = FEES.taker_fee_pct * notional
    slp = 2 * FEES.slippage_pct * notional; tot = ef + xf + slp
    print(f"\nHAND-WALK cf40deeb (sell): entry={e} stop={sl} risk_per_unit={risk:.4f} "
          f"stop%={stoppct:.4f}%")
    print(f"  feeR = RT*entry/risk = {RT:.5f}*{e}/{risk:.3f} = {feeR:.4f} R")
    print(f"  @ position sized so 1R=$50: qty={qty:.5f} BTC, notional=${notional:,.0f}")
    print(f"    entry taker ${ef:.2f} + exit taker ${xf:.2f} + slip(2x) ${slp:.2f} = ${tot:.2f}")
    print(f"    fee in R = ${tot:.2f}/$50 = {tot/rt_risk:.4f} R  (== feeR, matches)")

# breakeven fee + scenarios for baseline + best combined ladder
def be_and_scen(label):
    a = rab[label]; g, fa = a["grossavg"], a["feeavg"]
    rt_be = RT * g / fa
    print(f"\n[{label}]  gross={g:+.3f}  avg feeR@{RT:.5f}={fa:.3f}  net@current={a['netavg']:+.3f}")
    print(f"  BREAKEVEN round-trip = {RT:.5f}*{g:.3f}/{fa:.3f} = {rt_be:.5f}  ({rt_be*100:.4f}%)")
    for name, rt_s in [("taker-both 0.090%", 0.0009), ("entry-taker/exit-maker 0.064%", 0.00064),
                       ("blended est ~0.072%", 0.00072), ("maker-both 0.038%", 0.00038)]:
        print(f"    net @ {name:30} = {g - fa*(rt_s/RT):+.3f} R")
print("\nBREAKEVEN FEE — at what round-trip does net cross 0? (combined set)")
for lbl in ["baseline 0.5/1.0/2.5 @25/50/25", "single-tgt 1.0R @100%"]:
    be_and_scen(lbl)
print("\n=== END HARNESS ===")
