"""Post-fix re-run against the ACTUAL patched prod code (imports the real functions,
not verbatim copies). Live fixtures = the same 15:17-15:50 ET quotes as
repro_live_1517ET.py. Shows RIOT now prices a CREDIT with NO substitution, and OPEN
open_short now selects a sellable strike. Run: python reports/.../repro_live_postfix.py
"""
import tempfile, os
from trading_corp.agents.divisions.pmcc_robinhood import (
    PMCCAgent, _select_weekly_strike, _short_roll_credit,
)

def _c(k, b, a, m, d, oi, v):
    return {"strike_price": k, "bid": b, "ask": a, "mark_price": m, "delta": d,
            "open_interest": oi, "volume": v, "option_id": f"c_{k}"}

RIOT_821 = [
    _c(21.5,1.81,2.05,1.930,0.550,687,10), _c(22.0,1.57,1.85,1.710,0.509,2102,30),
    _c(22.5,1.40,1.62,1.510,0.468,1224,136), _c(23.0,1.29,1.44,1.365,0.432,5911,410),
    _c(23.5,1.04,1.30,1.170,0.392,1348,2), _c(24.0,0.91,1.13,1.020,0.355,6475,2439),
    _c(24.5,0.86,0.97,0.915,0.326,1515,65), _c(25.0,0.75,0.82,0.785,0.292,35802,1207),
    _c(25.5,0.64,0.87,0.755,0.275,2334,15), _c(26.0,0.53,0.69,0.610,0.238,3358,10),
    _c(26.5,0.47,0.64,0.555,0.219,63,0),
]
OPEN_814 = [
    _c(2.5,0.67,1.29,0.980,0.923,6,5), _c(3.0,0.25,0.71,0.480,0.867,333,184),
    _c(3.5,0.12,0.13,0.125,0.469,2475,2856), _c(4.0,0.03,0.04,0.035,0.164,6432,1963),
    _c(4.5,0.01,0.02,0.015,0.078,4170,746), _c(5.0,0.00,0.01,0.005,0.039,10150,510),
]

def _agent():
    d = tempfile.mkdtemp()
    sp = os.path.join(d, "strategies.yaml"); rp = os.path.join(d, "risk.yaml")
    open(sp, "w").write("robinhood_pmcc:\n  enabled: true\n  universe_source: positions\n")
    open(rp, "w").write("pmcc:\n  short_call_target_delta: 0.30\n")
    from pathlib import Path
    return PMCCAgent(strategies_yaml=Path(sp), risk_yaml=Path(rp))

ag = _agent()

print("===== RIOT roll: current short $23.5 8/14 (buyback mid 0.75) -> roll-out 8/21, delta 0.35 =====")
liq = ag._filter_liquid(RIOT_821, "RIOT")
print(f"  liquid strikes (Fix 1): {sorted(o['strike_price'] for o in liq)}")
best = _select_weekly_strike(liq, 0.35)
cons, mid, ob = _short_roll_credit(best, 0.75)
print(f"  SELECTED: C{best['strike_price']} (delta {best['delta']}, bid {best['bid']}, mark {best['mark_price']})")
print(f"  -> {'NO substitution (on-target $24)' if best['strike_price']==24.0 else 'SUBSTITUTED to '+str(best['strike_price'])}")
print(f"  gate MID net = new.mark({best['mark_price']}) - buyback.mark(0.75) = {mid:+.4f} -> "
      f"{'CREDIT (clears)' if mid>=0 else 'DEBIT (blocked)'}")
print(f"  (bid-based conservative net = {cons:+.4f})")

print("\n===== OPEN open_short: uncovered LEAP, band 0.30-0.45 (spot 3.445) =====")
liqo = ag._filter_liquid(OPEN_814, "OPEN")
print(f"  liquid strikes (Fix 1; $5.0 no-bid + $2.5 thin dropped): {sorted(o['strike_price'] for o in liqo)}")
besto = _select_weekly_strike(liqo, 0.35, target_delta_low=0.30, target_delta_high=0.45)
print(f"  SELECTED: C{besto['strike_price']} (delta {besto['delta']}, bid {besto['bid']}) -> "
      f"{'SELLS for the mid credit' if besto['bid']>0 else 'UNSELLABLE'}")
print(f"  -> {'clamped to nearest $3.5 (Fix 3)' if besto['strike_price']==3.5 else 'picked '+str(besto['strike_price'])}")
