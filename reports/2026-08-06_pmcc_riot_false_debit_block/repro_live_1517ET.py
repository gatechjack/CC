"""LIVE mid-session reproduction of the RIOT PMCC roll credit/debit gate.

Data: Robinhood MCP live quotes pulled 2026-08-06 ~19:17-19:22Z (15:17-15:22 ET).
Spot RIOT last_trade = 21.495 (bid 21.48 / ask 21.49).

The three gate functions below are COPIED VERBATIM from prod-live ef613e5
(trading_corp/agents/divisions/pmcc_robinhood.py):
  _days_to               (:516)
  _select_weekly_strike  (:460)
  _short_roll_credit     (:437)
plus a standalone _passes_liquidity mirroring :784 (min_oi=100, oi_bypass_vol=500,
max_spread=0.10) and the _find_best_weekly date/delta window (:4037-4114).

Purpose: given the LIVE chain, log the ACTUAL selected new-short strike and the
computed conservative_net the B2 gate compares to 0 — for the two candidate
current-short scenarios. No broker writes; pure arithmetic on captured quotes.
"""
from datetime import date

TODAY = date(2026, 8, 6)  # matches the live pull; _days_to uses date.today() in prod

# ---- VERBATIM prod-live helpers -------------------------------------------
def _days_to(expiry, today=TODAY):
    try:
        return max(0, (date.fromisoformat(expiry) - today).days)
    except (ValueError, TypeError):
        return 0

def _select_weekly_strike(calls, target_delta=0.30, target_strike=None,
                          target_delta_low=None, target_delta_high=None):
    if target_strike is not None:
        with_strike = [c for c in calls if c.get("strike_price") is not None]
        if not with_strike:
            return None
        return min(with_strike, key=lambda c: abs(float(c["strike_price"]) - target_strike))
    if target_delta_low is not None and target_delta_high is not None:
        lo, hi = min(target_delta_low, target_delta_high), max(target_delta_low, target_delta_high)
        in_band = [c for c in calls if c.get("delta") is not None and lo <= c["delta"] <= hi]
        if in_band:
            mid = (lo + hi) / 2.0
            return min(in_band, key=lambda c: abs(c["delta"] - mid))
        target_delta = (lo + hi) / 2.0
    otm = [c for c in calls if c.get("delta") is not None and c["delta"] < 0.40]
    pool = otm if otm else [c for c in calls if c.get("delta") is not None]
    if not pool:
        return None
    return min(pool, key=lambda c: abs(c["delta"] - target_delta))

def _short_roll_credit(new_weekly, close_mark):
    open_bid = new_weekly.get("bid")
    open_credit_conservative = open_bid if open_bid is not None else (new_weekly.get("mark_price") or 0.0)
    conservative_net = open_credit_conservative - close_mark
    mark_net = (new_weekly.get("mark_price") or new_weekly.get("bid") or 0.0) - close_mark
    return conservative_net, mark_net, open_bid

# mirrors _passes_liquidity :784 (returns (ok, reason))
def _passes_liquidity(o, min_oi=100, oi_bypass_vol=500, max_spread=0.10):
    bid = float(o.get("bid") or 0); ask = float(o.get("ask") or 0)
    oi = int(o.get("open_interest") or 0); vol = int(o.get("volume") or 0)
    if oi < min_oi and vol < oi_bypass_vol:
        return False, f"OI={oi}<{min_oi} AND vol={vol}<{oi_bypass_vol}"
    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
        sp = (ask - bid) / mid if mid > 0 else 1.0
        if sp > max_spread:
            return False, f"spread={sp*100:.1f}%>{max_spread*100:.0f}%"
    elif ask <= 0:
        return False, "no ask"
    return True, "ok"

# ---- LIVE quotes (strike -> dict), per expiry -----------------------------
def C(strike, bid, ask, mark, delta, oi, vol):
    return {"strike_price": strike, "bid": bid, "ask": ask, "mark_price": mark,
            "delta": delta, "open_interest": oi, "volume": vol}

CALLS = {
 "2026-08-14": [
   C(21.5,1.37,1.58,1.475,0.540,2507,32), C(22.0,1.21,1.35,1.28,0.487,347,112),
   C(22.5,1.04,1.16,1.10,0.437,1251,284), C(23.0,0.87,0.97,0.92,0.387,474,61),
   C(23.5,0.66,0.84,0.75,0.337,63,44),    C(24.0,0.60,0.69,0.645,0.299,618,55),
   C(24.5,0.45,0.61,0.53,0.257,105,9),    C(25.0,0.43,0.51,0.47,0.229,603,143),
 ],
 "2026-08-21": [
   C(21.5,1.81,2.05,1.93,0.550,687,10),   C(22.0,1.57,1.85,1.71,0.509,2102,30),
   C(22.5,1.40,1.62,1.51,0.468,1224,136), C(23.0,1.29,1.44,1.365,0.432,5911,410),
   C(23.5,1.04,1.30,1.17,0.392,1348,2),   C(24.0,0.91,1.13,1.02,0.355,6475,2439),
   C(24.5,0.86,0.97,0.915,0.326,1515,65), C(25.0,0.75,0.82,0.785,0.292,35802,1207),
   C(25.5,0.64,0.87,0.755,0.275,2334,15), C(26.0,0.53,0.69,0.61,0.238,3358,10),
   C(26.5,0.47,0.64,0.555,0.219,63,0),
 ],
}
# Current-short buyback marks (live):
MARK_25C_0807 = 0.025   # $25 8/7  (this-morning's near-worthless short; delta 0.042)
MARK_235C_0814 = 0.75   # $23.5 8/14 (operator's manual roll this morning; delta 0.337)

def find_best_weekly(after_dte, target_delta, target_dte=7, target_strike=None):
    # date window (target_dte provided -> [max(3,td-7), td+14]); rolls_out d>after_dte
    dte_lo, dte_hi = max(3, target_dte - 7), target_dte + 14
    cand_dates = [d for d in CALLS if dte_lo <= _days_to(d) <= dte_hi and _days_to(d) > after_dte]
    cand_dates.sort(key=_days_to)
    if not cand_dates:
        return None, "no_rollout_weekly", []
    target_date = cand_dates[0]
    liquid, rejected = [], []
    for c in CALLS[target_date]:
        ok, why = _passes_liquidity(c)
        (liquid if ok else rejected).append((c, why))
    liq = [c for c, _ in liquid]
    best = _select_weekly_strike(liq, target_delta, target_strike=target_strike)
    if best is not None:
        best = dict(best); best["expiration_date"] = target_date; best["dte"] = _days_to(target_date)
    return best, target_date, (liquid, rejected)

def run(label, buyback_mark, after_dte, target_delta):
    print(f"\n===== {label}  (target_delta={target_delta}, after_dte={after_dte}, buyback_mark={buyback_mark}) =====")
    best, target_date, pools = find_best_weekly(after_dte, target_delta)
    if isinstance(pools, tuple):
        liquid, rejected = pools
        print(f"  roll-out expiry selected: {target_date} (DTE={_days_to(target_date)})")
        print(f"  LIQUID (passed): {[c['strike_price'] for c,_ in liquid]}")
        print("  REJECTED by liquidity gate:")
        for c, why in rejected:
            print(f"      C{c['strike_price']:<5} d={c['delta']:.3f} bid={c['bid']} ask={c['ask']}  -> {why}")
    if best is None:
        print("  SELECTED: None"); return
    cons, mark, obid = _short_roll_credit(best, buyback_mark)
    verdict = "BLOCKED (net_debit_roll)" if cons < 0 else "CLEAR (credit)"
    print(f"  SELECTED new short: C{best['strike_price']} {best['expiration_date']} "
          f"(delta {best['delta']:.3f}, bid {best['bid']}, ask {best['ask']}, mark {best['mark_price']})")
    print(f"  conservative_net = new.bid({best['bid']}) - buyback.mark({buyback_mark}) = {cons:+.4f}")
    print(f"  mark_net         = new.mark({best['mark_price']}) - buyback.mark({buyback_mark}) = {mark:+.4f}")
    print(f"  GATE (conservative_net < 0 ?): {verdict}")

# Scenario S-OLD: engine still rolling the $25 8/7 (mark 0.025), roll-out past 1 DTE
run("S-OLD: buyback $25 C 8/7", MARK_25C_0807, after_dte=1, target_delta=0.35)
# Scenario S-NEW: engine rolling the CURRENT $23.5 8/14 (mark 0.75), roll-out past 8 DTE
run("S-NEW: buyback $23.5 C 8/14 (delta 0.35 target)", MARK_235C_0814, after_dte=8, target_delta=0.35)
run("S-NEW: buyback $23.5 C 8/14 (delta 0.30 config default)", MARK_235C_0814, after_dte=8, target_delta=0.30)

# Buyback-mark sensitivity for S-NEW (what read of the $23.5 8/14 flips it to a debit):
print("\n===== S-NEW buyback-mark sensitivity (selected strike from delta=0.35 run) =====")
best, td, _ = find_best_weekly(after_dte=8, target_delta=0.35)
for bm, tag in [(0.66,"bid"),(0.75,"mark/mid"),(0.785,"adj_mark_of_25C"),(0.84,"ask"),(1.23,"prior_close_0805")]:
    cons, _, _ = _short_roll_credit(best, bm)
    print(f"   buyback_mark={bm:<5} ({tag:<16}) -> conservative_net={cons:+.4f} -> "
          f"{'BLOCKED' if cons<0 else 'clear'}")
