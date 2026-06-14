"""Graft-fidelity check: the backtest's walk_v2 must reproduce the etharness walk
(SL-first tie, ordered TP fills, BE-after-TP1 / TP1-after-TP2 ratchet) exactly.
Reference = the etharness walk logic inline (verbatim); compare on shared cases.
Run: run_capped.ps1 python fidelity_check.py"""
from dataclasses import dataclass
import scripts.backtest_bitunix_confluence as m


@dataclass
class B:
    high: float
    low: float


# ── reference walk (verbatim from etharness.py walk/_agg/_ratchet) ──
def _r_at(side, e, osl, px):
    risk = abs(e - osl)
    return 0.0 if risk <= 0 else (1.0 if side == "buy" else -1.0) * (px - e) / risk


def _agg(side, e, osl, legs, filled, exitpx):
    tot = ff = 0.0
    for lg in ("tp1", "tp2", "tp3"):
        if lg in filled:
            tot += legs[lg]["r"] * legs[lg]["f"]; ff += legs[lg]["f"]
    unf = max(0.0, 1.0 - ff)
    if unf > 0:
        tot += _r_at(side, e, osl, exitpx) * unf
    return tot


def _ratchet(side, e, osl, csl, filled, legs):
    f = set(filled)
    if "tp1" not in f:
        return csl
    if "tp2" not in f:
        return e if ((side == "buy" and e > csl) or (side == "sell" and e < csl)) else csl
    c = legs["tp1"]["px"]
    return c if ((side == "buy" and c > csl) or (side == "sell" and c < csl)) else csl


def ref_walk(side, entry, legs, bars, start_idx, max_bars=480):
    osl = legs["_sl"]
    filled, csl, amb = [], osl, 0
    tgt = {lg: legs[lg]["px"] for lg in ("tp1", "tp2", "tp3")}
    end = min(len(bars), start_idx + max_bars)
    if start_idx < 0 or start_idx >= len(bars):
        return ("no_bars", None, 0, [])
    for idx in range(start_idx, end):
        hi, lo = bars[idx].high, bars[idx].low
        sl = (side == "buy" and lo <= csl) or (side == "sell" and hi >= csl)
        hit = []
        for lg in ("tp1", "tp2", "tp3"):
            if lg in filled:
                continue
            t = tgt[lg]
            if (side == "buy" and hi >= t) or (side == "sell" and lo <= t):
                hit.append(lg)
            else:
                break
        if sl and hit:
            amb += 1
        if sl:
            r = _agg(side, entry, osl, legs, filled, csl)
            return ("win" if r > 0 else "loss", r, amb, list(filled))
        for lg in hit:
            filled.append(lg); csl = _ratchet(side, entry, osl, csl, filled, legs)
        if "tp3" in filled:
            return ("win", _agg(side, entry, osl, legs, filled, tgt["tp3"]), amb, list(filled))
    return ("open", None, amb, list(filled))


def legs_sell(entry=100.0, sd=1.0, r1=0.5, r2=1.0, r3=2.5):
    # sell: stop above, TPs below
    return {"_sl": entry + sd,
            "tp1": {"px": entry - r1 * sd, "r": r1, "f": 0.25},
            "tp2": {"px": entry - r2 * sd, "r": r2, "f": 0.50},
            "tp3": {"px": entry - r3 * sd, "r": r3, "f": 0.25}}


SCEN = {
    # tp1+tp2 same bar → SL ratchets to tp1 → next bar stops there → gross 0.75
    "tp1_tp2_then_tp1floor": ([B(99.6, 98.9), B(100.0, 99.6)], 0.75),
    # immediate SL (sell, high>=101) → loss -1.0
    "immediate_sl":          ([B(101.2, 100.5)], -1.0),
    # tp1 only → SL to BE → next bar stops at entry → gross 0.125
    "tp1_then_be":           ([B(100.2, 99.4), B(100.3, 99.8)], 0.125),
    # all 3 TPs in one bar → gross 0.5*0.25+1*0.5+2.5*0.25 = 1.25
    "all_three_tp":          ([B(99.0, 97.4)], 1.25),
    # never hits anything within bars → open
    "open":                  ([B(100.1, 99.7), B(100.1, 99.7)], None),
}

print("=== GRAFT FIDELITY: walk_v2 vs etharness reference ===")
legs = legs_sell()
allok = True
for name, (bars, expected_gross) in SCEN.items():
    o_v2, gr_v2, _, _ = m.walk_v2("sell", 100.0, legs, bars, 0)
    o_ref, gr_ref, _, _ = ref_walk("sell", 100.0, legs, bars, 0)
    match = (o_v2 == o_ref) and (
        (gr_v2 is None and gr_ref is None) or
        (gr_v2 is not None and gr_ref is not None and abs(gr_v2 - gr_ref) < 1e-9))
    exp_ok = (expected_gross is None and gr_v2 is None) or (
        expected_gross is not None and gr_v2 is not None and abs(gr_v2 - expected_gross) < 1e-9)
    ok = match and exp_ok
    allok = allok and ok
    g = "None" if gr_v2 is None else f"{gr_v2:+.4f}"
    print(f"  {name:24} v2={o_v2}/{g}  ref-match={match}  expected-match={exp_ok}  {'OK' if ok else 'FAIL'}")
print(f"\nFIDELITY: {'PASS — walk_v2 == etharness walk on all shared cases' if allok else 'FAIL'}")
