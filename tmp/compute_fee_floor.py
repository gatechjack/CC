"""Compute R / fee-floor / would-have-skipped for each of the 11
trade_plan_decision payloads. Pure compute — no prod query."""

# yaml constants (verified from prod /home/azureuser/trading_corp/config/strategies.yaml)
TAKER_PCT = 0.0004     # 0.04%
SLIPPAGE_PCT = 0.00005 # 0.5 bps
MIN_STOP_ATR_MULT = 0.5
MAX_STOP_ATR_MULT = 2.5
ATR_MULTIPLIER = 1.5   # fallback SL = 1.5×ATR
SWING_BUFFER_PCT = 0.0005
TP1_R_TARGET = 0.5
TP1_MIN_PROFIT_MULTIPLIER = 2.0
TP2_R_DEFAULT = 1.0

# Round-trip cost (entry taker + exit taker since tp_is_maker:false, both with slippage)
RT_COST_PCT = 2 * TAKER_PCT + 2 * SLIPPAGE_PCT  # 0.0009 = 0.09%
FEE_FLOOR_PCT_OF_ENTRY = TP1_MIN_PROFIT_MULTIPLIER * RT_COST_PCT  # 0.18%

print(f"RT cost: {RT_COST_PCT*100:.3f}% | TP1 fee floor: {FEE_FLOOR_PCT_OF_ENTRY*100:.3f}% of entry price")
print()

# (ts, should, skip, tier, side, entry, sl, rpu, atr, sw_lo, sw_hi)
rows = [
    ("2026-05-17T16:51", 0, "fees", "STANDARD", "sell", 78014.3, None, 0.0, 45.04, 78002.7, 78129.9),
    ("2026-05-17T16:54", 0, "fees", "STANDARD", "sell", 78019.4, None, 0.0, 46.01, 78002.7, 78161.8),
    ("2026-05-17T18:24", 0, "fees", "STANDARD", "sell", 78008.9, None, 0.0, 58.31, 77870.1, 78213.5),
    ("2026-05-18T05:21a",0, "fees", "STANDARD", "sell", 76965.3, None, 0.0, 70.56, 76961.9, 77056.0),
    ("2026-05-18T05:21b",0, "fees", "STANDARD", "sell", 76965.3, None, 0.0, 70.56, 76961.9, 77056.0),
    ("2026-05-18T08:40", 0, "fees", "STANDARD", "sell", 76956.7, None, 0.0, 68.70, 76925.9, 77034.2),
    ("2026-05-18T16:24", 1, "",     "STANDARD", "sell", 76407.4, 76610.9037, 203.50, 131.22, 76213.0, 76572.7),
    ("2026-05-18T18:30", 1, "",     "STANDARD", "sell", 76319.1, 76466.1085, 147.01, 98.01, 76517.2, 76725.7),
    ("2026-05-19T04:04", 0, "fees", "STANDARD", "sell", 76705.8, None, 0.0, 64.00, 76550.0, 76830.4),
    ("2026-05-19T04:25", 0, "fees", "STANDARD", "sell", 76686.0, None, 0.0, 71.84, 76691.2, 76916.5),
    ("2026-05-19T13:22", 0, "fees", "STANDARD", "sell", 76665.4, None, 0.0, 66.78, 76707.0, 76850.0),
]

print(f"{'ts':18s} {'res':4s} {'side':4s} {'entry':>9s} {'atr':>6s} "
      f"{'swing_R':>8s} {'sw/atr':>6s} {'pick':>8s} {'R_$':>7s} {'R_%':>6s} "
      f"{'floor_%':>7s} {'verdict':>10s}")
print("-" * 130)

skip_R_pcts = []
fire_R_pcts = []
for r in rows:
    ts, should, skip, tier, side, entry, sl, rpu, atr, sw_lo, sw_hi = r
    fee_floor_dollar = FEE_FLOOR_PCT_OF_ENTRY * entry
    if side == "sell":
        # Swing SL above entry, at swing_high + buffer
        swing_R = (sw_hi - entry) + entry * SWING_BUFFER_PCT
    else:
        swing_R = (entry - sw_lo) + entry * SWING_BUFFER_PCT
    sw_over_atr = swing_R / atr if atr > 0 else 0
    in_bounds = MIN_STOP_ATR_MULT <= sw_over_atr <= MAX_STOP_ATR_MULT
    pick = "swing" if in_bounds else "atr_fb"
    if in_bounds:
        R_dollar = swing_R
    else:
        R_dollar = ATR_MULTIPLIER * atr
    R_pct = R_dollar / entry * 100
    tp2_dist = TP2_R_DEFAULT * R_dollar
    fires = tp2_dist >= fee_floor_dollar
    verdict = "FIRE" if fires else "SKIP_fees"
    # actual outcome
    actual = "FIRED" if should == 1 else "SKIPPED"
    label = f"{verdict}/{actual}"
    if should == 1:
        fire_R_pcts.append(R_pct)
    else:
        skip_R_pcts.append(R_pct)
    print(f"{ts:18s} {actual[:4]:4s} {side:4s} {entry:9.1f} {atr:6.1f} "
          f"{swing_R:8.1f} {sw_over_atr:6.2f} {pick:>8s} {R_dollar:7.1f} "
          f"{R_pct:6.3f} {FEE_FLOOR_PCT_OF_ENTRY*100:7.3f} {label:>10s}")

print()
print("=== DISTRIBUTION SUMMARY ===")
print(f"Fire R% (n={len(fire_R_pcts)}):   {fire_R_pcts}")
print(f"Skip R% (n={len(skip_R_pcts)}):   {[round(x,3) for x in skip_R_pcts]}")
print(f"Fee floor: {FEE_FLOOR_PCT_OF_ENTRY*100:.3f}% of entry")
print(f"All fire R% > floor? {all(x > FEE_FLOOR_PCT_OF_ENTRY*100 for x in fire_R_pcts)}")
print(f"All skip R% <= floor? {all(x <= FEE_FLOOR_PCT_OF_ENTRY*100 + 0.001 for x in skip_R_pcts)}")
print()
print("--- swing-vs-ATR-fallback choice breakdown for skips ---")
swing_skips = [r for r in rows if r[1]==0 and (r[10]-r[5]) + r[5]*SWING_BUFFER_PCT <= MAX_STOP_ATR_MULT*r[8]]
atr_skips = [r for r in rows if r[1]==0 and (r[10]-r[5]) + r[5]*SWING_BUFFER_PCT > MAX_STOP_ATR_MULT*r[8]]
print(f"Skips that would have used swing SL (sw/atr <= 2.5): {len(swing_skips)}")
print(f"Skips that fell to ATR fallback (sw/atr > 2.5):       {len(atr_skips)}")
print()
print("--- alternative-scenario simulation ---")
print(f"If max_stop_atr_mult was relaxed 2.5 -> 4.0:")
extra = 0
for r in rows:
    ts, should, _, _, side, entry, _, _, atr, sw_lo, sw_hi = r
    if should == 1: continue
    swing_R = (sw_hi - entry) + entry * SWING_BUFFER_PCT
    sw_over_atr = swing_R / atr
    if MAX_STOP_ATR_MULT < sw_over_atr <= 4.0:
        # would now use swing -> R is larger
        R_dollar = swing_R
        R_pct = R_dollar / entry * 100
        fires = R_dollar >= FEE_FLOOR_PCT_OF_ENTRY * entry
        if fires:
            extra += 1
            print(f"  {ts}: sw/atr={sw_over_atr:.2f} -> swing R={swing_R:.1f} ({R_pct:.3f}%) -> FIRES")
print(f"  Extra fires under relaxed max: {extra}")
print()
print(f"If tp_is_maker was true (round-trip 0.054% instead of 0.09%):")
RT_MIXED = 1*TAKER_PCT + 1*0.00014 + 2*SLIPPAGE_PCT  # taker-in + maker-out
FLOOR_MIXED = 2.0 * RT_MIXED
print(f"  Mixed RT cost: {RT_MIXED*100:.4f}% | new fee floor: {FLOOR_MIXED*100:.4f}% of entry")
saved = sum(1 for x in skip_R_pcts if x > FLOOR_MIXED*100)
print(f"  Skips that would FIRE under mixed maker exits: {saved}/{len(skip_R_pcts)}")
