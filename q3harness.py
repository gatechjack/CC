"""Q3 counterfactual harness — reconstruct + walk the 24 HTF-rejected bitunix signals.
READ-ONLY analysis. Reuses the strategy's OWN build_trade_plan / get_recent_swing /
get_htf_levels (no re-derivation). Validates reconstruction against the 20 stored
trade_plan_decision inputs (plan-mismatch guard) BEFORE walking rejects.
Tier-1 = 3m-bar walk (DB); ambiguous 3m bars (SL+TP in same bar) are flagged for Tier-2.
"""
from __future__ import annotations
import os, sys, csv, io, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_corp.data.live_bar_cache import Bar, LiveBarCache
from trading_corp.agents.strategies.swing import get_recent_swing
from trading_corp.agents.strategies.levels import get_htf_levels
from trading_corp.agents.strategies.trade_plan import build_trade_plan, StrategyConfig, FeeConfig
print("=== IMPORTS OK ===")

CFG = StrategyConfig(); FEES = FeeConfig()
RT = FEES.round_trip_cost_pct()                      # round-trip cost fraction
print(f"round_trip_cost_pct = {RT:.5f}  (taker {FEES.taker_fee_pct}, slip {FEES.slippage_pct})")

# ---------- load qdata.out ----------
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qdata.out")
raw = open(OUT, encoding="utf-8-sig").read()
sec = {}
cur = None
for line in raw.splitlines():
    if line.startswith("===") and line.endswith("==="):
        cur = line.strip("="); sec[cur] = []; continue
    if cur and line.strip():
        sec[cur].append(line)
def rows(name):
    return list(csv.reader(io.StringIO("\n".join(sec.get(name, [])))))

bars = [Bar(ts_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
            close=float(r[4]), volume=0.0) for r in rows("BARS3M")]
bars.sort(key=lambda b: b.ts_ms)
print(f"bars3m: {len(bars)}  range {bars[0].ts_ms}..{bars[-1].ts_ms}")

import datetime as dt
def iso_to_ms(s):
    return int(dt.datetime.fromisoformat(s).timestamp() * 1000)
def idx_at(ts_ms):
    lo, hi, ans = 0, len(bars) - 1, -1
    while lo <= hi:
        m = (lo + hi) // 2
        if bars[m].ts_ms <= ts_ms: ans = m; lo = m + 1
        else: hi = m - 1
    return ans

def idx_closed(ts_ms):           # last FULLY-CLOSED 3m bar before ts (live cache convention)
    i = idx_at(ts_ms)
    while i >= 0 and bars[i].ts_ms + 180000 > ts_ms:
        i -= 1
    return i
_LC = LiveBarCache()             # max_bars=60 default; reuse the strategy's own get_atr
def cache_atr(end_idx, period=14):
    if end_idx < 0: return None
    _LC.bars = bars[max(0, end_idx - 59): end_idx + 1]   # 60-bar window ending at end_idx
    return _LC.get_atr(period)

# ---------- replay-mirrored walk ----------
def _r_at(side, entry, osl, px):
    risk = abs(entry - osl)
    if risk <= 0: return 0.0
    return (1.0 if side == "buy" else -1.0) * (px - entry) / risk
def _agg_r(side, entry, osl, plan, filled, exitpx):
    tot = 0.0; ff = 0.0
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
    filled = []; csl = osl; amb = []
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
        if sl_hit and legs: amb.append(idx)           # AMBIGUOUS: SL+TP same bar
        if sl_hit:
            r = _agg_r(side, entry, osl, plan, filled, csl)
            return ("win" if r > 0 else "loss", r, idx - start_idx + 1, amb, list(filled))
        for leg in legs:
            filled.append(leg); csl = _ratchet(side, entry, osl, csl, filled, plan)
        if "tp3" in filled:
            r = _agg_r(side, entry, osl, plan, filled, tgt["tp3"])
            return ("win", r, idx - start_idx + 1, amb, list(filled))
    return ("open", None, end - start_idx, amb, list(filled))

# ---------- VALIDATION (plan-mismatch guard) ----------
def fnum(x):
    try: return float(x)
    except: return None
print("\n=== VALIDATION vs 20 stored trade_plan_decision rows ===")
v1 = v1n = v2 = v2n = v3 = v3n = v4 = v4n = 0
v1_fail = []
for r in rows("VAL"):
    ts, entry, side, atr_u, swl, swh, res, sup, sl, t1, t2, t3, slm, t2m, should = r
    entry = fnum(entry); atr_u = fnum(atr_u)
    swl = fnum(swl); swh = fnum(swh); res = fnum(res); sup = fnum(sup)
    tp = build_trade_plan(entry=entry, side=side, atr=atr_u, swing_low=swl, swing_high=swh,
                          resistance=res, support=sup, cfg=CFG, fees=FEES)
    if should == "1":
        v1n += 1
        ok = (abs(tp.stop_loss - fnum(sl)) < 0.5 and abs(tp.tp1 - fnum(t1)) < 0.5
              and abs(tp.tp2 - fnum(t2)) < 0.5 and abs(tp.tp3 - fnum(t3)) < 0.5
              and tp.sl_method == slm and tp.tp2_method == t2m)
        if ok: v1 += 1
        else: v1_fail.append((ts, round(tp.stop_loss,1), sl, tp.sl_method, slm))
    # input reconstruction (my compute vs stored), at this row's bar
    ci = idx_closed(iso_to_ms(ts))
    my_atr = cache_atr(ci)
    if my_atr and atr_u:
        v2n += 1
        if abs(my_atr - atr_u) / atr_u < 0.05: v2 += 1
    my_swh = get_recent_swing(bars, ci, side="high", n=CFG.swing_n, max_lookback=CFG.swing_max_lookback)
    my_swl = get_recent_swing(bars, ci, side="low", n=CFG.swing_n, max_lookback=CFG.swing_max_lookback)
    for mine, stored in ((my_swh, swh), (my_swl, swl)):
        if stored is not None:
            v3n += 1
            if mine is not None and abs(mine - stored) / stored < 0.005: v3 += 1
    my_res, my_sup = get_htf_levels(bars, ci, htf_minutes=CFG.htf_minutes,
                                    lookback_bars_htf=CFG.htf_lookback_bars, n=CFG.swing_n)
    for mine, stored in ((my_res, res), (my_sup, sup)):
        if stored is not None:
            v4n += 1
            if mine is not None and abs(mine - stored) / stored < 0.005: v4 += 1
print(f"V1 plan-from-stored-inputs == stored plan : {v1}/{v1n}")
for f in v1_fail: print("   V1 MISMATCH:", f)
print(f"V2 my Wilder-ATR ~ stored atr_used (<5%)  : {v2}/{v2n}")
print(f"V3 my swing ~ stored swing (<0.5%)        : {v3}/{v3n}")
print(f"V4 my htf-levels ~ stored S/R (<0.5%)     : {v4}/{v4n}")
V1_OK = (v1 == v1n and v1n > 0)
print(f"VALIDATION (V1 code-path): {'PASS' if V1_OK else 'FAIL -> STOP'}")

# ---------- reconstruct + walk the 24 rejects ----------
# join REJ_SCORE (ts,sig,price,side) with REJ_HTF (ts,sig,reason,atr_d1,dsup,dres) by sig+nearest ts
htf = {}
for r in rows("REJ_HTF"):
    htf.setdefault(r[1], []).append((r[0], r[2], r[3], r[4], r[5]))
def htf_match(sig, ts):
    cands = htf.get(sig, [])
    if not cands: return (None, None, None, None)
    best = min(cands, key=lambda c: abs(iso_to_ms(c[0]) - iso_to_ms(ts)))
    return best[1], best[2], best[3], best[4]   # reason, atr_d1, dsup, dres

print("\n=== REJECT RECONSTRUCTION + TIER-1 WALK ===")
seen = set(); recs = []
for r in rows("REJ_SCORE"):
    ts, sig, price, side = r[0], r[1], fnum(r[2]), r[3]
    reason, atr_d1, dsup, dres = htf_match(sig, ts)
    ci = idx_closed(iso_to_ms(ts))
    key = (ci, side, reason)                      # dedup same-bar repeats of one setup
    dup = key in seen; seen.add(key)
    atr = cache_atr(ci)
    swl = get_recent_swing(bars, ci, side="low", n=CFG.swing_n, max_lookback=CFG.swing_max_lookback)
    swh = get_recent_swing(bars, ci, side="high", n=CFG.swing_n, max_lookback=CFG.swing_max_lookback)
    res, sup = get_htf_levels(bars, ci, htf_minutes=CFG.htf_minutes,
                              lookback_bars_htf=CFG.htf_lookback_bars, n=CFG.swing_n)
    tp = build_trade_plan(entry=price, side=side, atr=atr or 0.0, swing_low=swl, swing_high=swh,
                          resistance=res, support=sup, cfg=CFG, fees=FEES)
    rec = dict(ts=ts, sig=sig, side=side, gate=reason, entry=price, ci=ci, dup=dup,
               atr=atr, should=tp.should_trade, skip=tp.skip_reason,
               sl=tp.stop_loss, risk=tp.risk_per_unit)
    if tp.should_trade:
        rpu = tp.risk_per_unit
        plan = {"tp1": {"price": tp.tp1, "target_r": abs(tp.tp1 - price) / rpu, "fraction": tp.tp1_qty_fraction},
                "tp2": {"price": tp.tp2, "target_r": abs(tp.tp2 - price) / rpu, "fraction": tp.tp2_qty_fraction},
                "tp3": {"price": tp.tp3, "target_r": abs(tp.tp3 - price) / rpu, "fraction": tp.tp3_qty_fraction}}
        res_w = walk(side, price, tp.stop_loss, plan, ci + 1)
        rec.update(outcome=res_w[0], grossR=res_w[1], nbars=res_w[2],
                   namb=len(res_w[3]), filled=",".join(res_w[4]))
        if res_w[1] is not None:
            rec["feeR"] = RT * price / tp.risk_per_unit
            rec["netR"] = res_w[1] - rec["feeR"]
    recs.append(rec)

def fm(x, n=3):
    return "-" if x is None else (f"{x:.{n}f}" if isinstance(x, float) else str(x))
print(f"{'ts':20} {'side':4} {'gate':20} {'dup':3} {'trade?':6} {'out':5} {'grossR':7} {'netR':7} {'namb':4} {'filled':9} {'skip'}")
for x in recs:
    print(f"{x['ts'][5:19]:20} {x['side']:4} {str(x['gate'])[:20]:20} {('Y' if x['dup'] else ''):3} "
          f"{str(x['should']):6} {fm(x.get('outcome')):5} {fm(x.get('grossR')):7} {fm(x.get('netR')):7} "
          f"{fm(x.get('namb')):4} {x.get('filled',''):9} {x.get('skip') or ''}")

# ---------- tier accounting + per-gate aggregate ----------
uniq = [x for x in recs if not x["dup"]]
print(f"\n=== TIER ACCOUNTING (unique setups: {len(uniq)} of {len(recs)} raw) ===")
walked = [x for x in uniq if x["should"] and x.get("grossR") is not None]
planskip = [x for x in uniq if not x["should"]]
openr = [x for x in uniq if x["should"] and x.get("outcome") == "open"]
t2 = [x for x in walked if x.get("namb", 0) > 0]
t1 = [x for x in walked if x.get("namb", 0) == 0]
print(f"plan-would-skip (gate moot): {len(planskip)}  {[x['skip'] for x in planskip]}")
print(f"still-open (ran past avail bars): {len(openr)}")
print(f"Tier-1 clean (0 ambiguous bars): {len(t1)}")
print(f"Tier-2 needed (>=1 ambiguous 3m bar): {len(t2)}  -> {[(x['ts'][5:16], x['namb']) for x in t2]}")
for gate in ("proximity_to_support", "regime_forbids_side"):
    g = [x for x in walked if x["gate"] == gate]
    if not g: continue
    w = sum(1 for x in g if x["outcome"] == "win"); l = sum(1 for x in g if x["outcome"] == "loss")
    gr = sum(x["grossR"] for x in g); nr = sum(x["netR"] for x in g)
    print(f"\n[{gate}] walked={len(g)} W={w} L={l}  grossR cum={gr:.3f} avg={gr/len(g):.3f}  "
          f"netR cum={nr:.3f} avg={nr/len(g):.3f}")
print("\n=== END HARNESS ===")
