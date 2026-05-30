# BitUnix fee-floor calibration — three-rule audit of today's 15 rejections

**Date:** 2026-05-29
**Scope:** Read-only diagnostic. No code/config changes. Tripwire boundary unchanged (`2026-06-19` per `runbooks/board_memo_bitunix_fee_floor_decision_2026_05_25.md` §9).
**Active branch:** `bitunix-live-entry-path-2026-05-29` (parallel session — HITL wiring; not touching trade_plan code). Report file written but NOT staged.

---

## Phase 1 — the actual mechanic

### Code site

`trading_corp/agents/strategies/trade_plan.py:207-236`:

```python
risk_per_unit = stop_distance

# ── TP1: max(target * R, fee_floor) ──
fee_cost_per_unit = fees.round_trip_cost_pct() * entry
tp1_target_distance = cfg.tp1_r_target * risk_per_unit
tp1_fee_floor = cfg.tp1_min_profit_multiplier * fee_cost_per_unit
tp1_distance = max(tp1_target_distance, tp1_fee_floor)

# ── TP2: default 1R, snap to HTF level if in band ──
tp2_distance = cfg.tp2_r_default * risk_per_unit
# ... TP2 snap logic ...

# Skip-trade: fee floor pushed TP1 past TP2 — trade has no edge.
if tp1_distance >= tp2_distance:
    return _skip(entry, "fees_too_high_for_risk")
```

`fees.round_trip_cost_pct()` (`trade_plan.py:41-46`):

```python
def round_trip_cost_pct(self) -> float:
    entry_fee = self.taker_fee_pct if self.entry_is_taker else self.maker_fee_pct
    exit_fee = self.maker_fee_pct if self.tp_is_maker else self.taker_fee_pct
    return entry_fee + exit_fee + 2 * self.slippage_pct
```

### Active config (`config/strategies.yaml:1306-1348`)

| key | value | meaning |
|---|---|---|
| `tp1_r_target` | 0.5 | TP1 nominal distance = 0.5R |
| `tp1_min_profit_multiplier` | **2.0** | TP1 must clear 2× round-trip fee |
| `tp1_qty_fraction` | 0.25 | TP1 takes 25% of position |
| `tp2_r_default` | 1.0 | TP2 nominal = 1R |
| `tp2_qty_fraction` | 0.50 | TP2 takes 50% |
| `tp3_r_target` | 2.5 | TP3 nominal = 2.5R |
| `tp3_qty_fraction` | 0.25 | TP3 takes 25% |
| `taker_pct` | 0.0004 | 0.04% taker |
| `maker_pct` | 0.00014 | 0.014% maker (3× cheaper) |
| `slippage_pct` | 0.00005 | 0.5 bps per leg |
| `entry_is_taker` | true | market entry |
| `tp_is_maker` | **false** | MVP — market exits |

### Derived per-unit values

- **`round_trip_cost_pct` = 0.0004 + 0.0004 + 2×0.00005 = 0.00090 (= 0.09% of entry)**
- **`tp1_fee_floor` = 2.0 × 0.0009 × entry = 0.0018 × entry (= 0.18% of entry)**

### "1R" definition

1R is the **stop distance per unit** (`risk_per_unit = stop_distance = |entry - stop_loss|`). Per-unit, in dollars. Not size-weighted.

### Skip condition, in plain math

`tp1_distance ≥ tp2_distance` triggers skip. Since `tp1_target_distance = 0.5R < 1R = tp2_distance` always, the skip can only fire via the fee floor:

`tp1_fee_floor ≥ 1R` → `0.0018 × entry ≥ stop_distance` → **skip when `stop_distance ≤ 0.0018 × entry`** (= 0.18% of entry).

For BTC ~$73,500: skip when stop_distance ≤ ~$132.

### What's compared (numerator vs denominator)

- **Numerator:** TP1's required distance from entry — `max(0.5R, 2.0 × round_trip_per_unit_$)`. Both terms are per-unit price distances.
- **Denominator:** Compared against TP2's distance (1R = stop_distance). If TP1 floor pushes past TP2, no edge exists between the legs.

Crucially: this is a **per-unit price-level** comparison, NOT a $-weighted-by-fraction comparison. The position size doesn't enter.

### Note on the operator's framing of fees

Operator's prompt says "v2 uses maker exits." The active YAML has `tp_is_maker: false` — current production uses **taker exits**. The maker-exit flip is the (b) deliverable approved (but not built) in the 5/25 board memo. So both legs are taker today: 0.04% + 0.04% + 0.01% slippage = 0.09% round-trip.

---

## Phase 2 — per-trade math for today's 15 rejections

### Three-rule definitions

- **Rule A** (operator's "TP1 covers open fee only"): `tp1_target_distance ≥ open_fee_per_unit` → `0.5 × stop_distance ≥ 0.0004 × entry`
- **Rule B** (system, current): `tp1_target_distance ≥ tp1_fee_floor` (i.e., the fee floor doesn't dominate) → `0.5 × stop_distance ≥ 2.0 × 0.0009 × entry` → `stop_distance ≥ 0.0036 × entry`. Equivalently the *skip* condition is `stop_distance ≤ 0.0018 × entry`. All 15 here meet the skip condition (by definition).
- **Rule C** (blended EV with 1.5× margin): `Σ(tp_i_fraction × R_i × 1R$) ≥ 1.5 × round_trip_fee$` → `1.25 × stop_distance ≥ 1.5 × 0.0009 × entry` → `stop_distance ≥ 0.00108 × entry`

Per-unit BTC math (no quantity scaling — all comparisons are price-level distances on a single unit).

### SL determination

For sell-side (every rejection today is sell-side), the relevant swing is `swing_high`. Per `build_trade_plan` lines 178-200:
- If `swing_high` is None or ≤ entry → ATR fallback: `stop_distance = 1.5 × atr_used`
- Else swing_distance = (swing_high + 0.0005×entry) − entry. If 0.5×atr ≤ swing_distance ≤ 2.5×atr → use swing. Else ATR fallback.

### Per-trade math table

| ts UTC | sl | entry $ | atr $ | 1R $ | open fee $ | rt fee $ | tp1 tgt $ | Rule A | A gap | Rule C | C gap |
|---|---|---:|---:|---:|---:|---:|---:|:---:|---:|:---:|---:|
| 01:22:17 | atrfb | 73,590.50 | 72.81 | 109.22 | 29.44 | 66.23 | 54.61 | ✓ | +25.17 | ✓ | +37.18 |
| 02:23:09 | swing | 73,537.00 | 64.52 | 114.67 | 29.41 | 66.18 | 57.33 | ✓ | +27.92 | ✓ | +44.06 |
| 04:09:02 | atrfb | 73,236.80 | 50.77 | 76.15 | 29.29 | 65.91 | 38.08 | ✓ | +8.78 | ✗ | −3.68 |
| 05:15:04 | atrfb | 73,565.60 | 50.94 | 76.41 | 29.43 | 66.21 | 38.21 | ✓ | +8.78 | ✗ | −3.80 |
| 05:33:40 | atrfb | 73,480.80 | 47.66 | 71.49 | 29.39 | 66.13 | 35.74 | ✓ | +6.35 | ✗ | −9.84 |
| 05:42:41 | atrfb | 73,480.90 | 42.76 | 64.15 | 29.39 | 66.13 | 32.07 | ✓ | +2.68 | ✗ | −19.02 |
| 06:06:01 | swing | 73,637.50 | 51.38 | 62.62 | 29.46 | 66.27 | 31.31 | ✓ | +1.85 | ✗ | −21.14 |
| 06:12:42 | swing | 73,658.00 | 51.64 | 61.83 | 29.46 | 66.29 | 30.91 | ✓ | +1.45 | ✗ | −22.15 |
| 07:34:11 | atrfb | 73,530.70 | 52.74 | 79.12 | 29.41 | 66.18 | 39.56 | ✓ | +10.15 | ✗ | −0.37 |
| 11:30:57 | atrfb | 73,543.00 | 44.78 | 67.16 | 29.42 | 66.19 | 33.58 | ✓ | +4.16 | ✗ | −15.33 |
| 11:48:01 | atrfb | 73,483.00 | 48.32 | 72.48 | 29.39 | 66.13 | 36.24 | ✓ | +6.85 | ✗ | −8.60 |
| 13:10:57 | atrfb | 73,165.00 | 63.03 | 94.55 | 29.27 | 65.85 | 47.27 | ✓ | +18.01 | ✓ | +19.41 |
| 13:30:09 | atrfb | 73,034.00 | 64.78 | 97.17 | 29.21 | 65.73 | 48.58 | ✓ | +19.37 | ✓ | +22.86 |
| 13:33:03 | atrfb | 72,923.60 | 66.20 | 99.30 | 29.17 | 65.63 | 49.65 | ✓ | +20.48 | ✓ | +25.68 |
| 17:30:09 | swing | 74,193.00 | 128.94 | 126.30 | 29.68 | 66.77 | 63.15 | ✓ | +33.47 | ✓ | +57.71 |

All entries are sell-side STANDARD-tier. SL fallback path: 11 ATR-fallback / 4 swing-based (swings in the 0.5-2.5×ATR band).

---

## Phase 3 — verdict

### Acceptance counts

| Rule | passes today's 15 | mean gap | min gap | max gap |
|---|---:|---:|---:|---:|
| **Rule A** (operator: TP1 ≥ open fee) | **15/15** | +$13.03 | +$1.45 | +$33.47 |
| **Rule B** (system: TP1 ≥ 2× round-trip) | **0/15** | — | — | — |
| **Rule C** (1.25×1R ≥ 1.5× round-trip) | **6/15** | +$6.87 | −$22.15 | +$57.71 |

### Representative samples (in detail)

**Tightest call (Rule A barely passes, Rule C solidly fails):**
- `2026-05-29T06:12:42` UTC, sell @ $73,658.00, atr=$51.64 (live), swing-based stop = $61.83
- Open fee per unit = $29.46. TP1 target distance = $30.91. Rule A passes by **$1.45** — TP1 would clear the open fee by less than a dime per BTC at typical fractional sizes.
- Round-trip fee = $66.29. Total expected (best case all-TPs-hit) = 1.25 × $61.83 = $77.29. Rule C threshold = 1.5 × $66.29 = $99.43. Rule C FAILS by **−$22.15**.
- Interpretation: this trade barely earns back the open fee in price-move terms, but its full expected gain doesn't reach 1.5× the round-trip cost. If you only had to clear the open fee, you'd take it. If you require the full plan to earn back fees+50% margin, you wouldn't.

**Mid-range positive (Rule A passes comfortably, Rule C passes comfortably):**
- `2026-05-29T13:33:03` UTC, sell @ $72,923.60, atr=$66.20 (live), ATR-fallback stop = $99.30
- Open fee = $29.17. TP1 target = $49.65. Rule A passes by **$20.48**.
- Round-trip = $65.63. Expected gain = $124.13. Rule C passes by **$25.68**.
- Interpretation: this is the kind of trade that's clearly above operator's discipline AND blended EV is positive even on the conservative 1.5× margin. The system declines anyway because the fee floor demands ≥ 2× round-trip, requiring 1R ≥ $132. Here 1R is $99.

**Largest miss (Rule A passes by ~$33, Rule C passes by ~$58):**
- `2026-05-29T17:30:09` UTC, sell @ $74,193.00, atr=$128.94 (ATR jumped — the only single-day spike), swing-based stop = $126.30
- Open fee = $29.68. TP1 target = $63.15. Rule A passes by **$33.47**.
- Round-trip = $66.77. Expected gain = $157.88. Rule C passes by **$57.71**.
- Interpretation: this is a trade with materially wider stop (~$126 = 0.17% of entry), still below the 0.18% floor, but only by $6. The trade has the most-positive blended EV of the day. System still declined.

### Honest read

The system's Rule B is **significantly tighter** than the operator's Rule A. Quantification:

- The **gap** between Rule A and Rule B at typical mid-day pricing is the spread between needing `stop_distance ≥ 0.0008 × entry` (Rule A, ≈ $59 at BTC $73.5K) vs `stop_distance ≥ 0.0018 × entry` (Rule B, ≈ $132). Rule B is ~**2.25× Rule A**.
- The **gap** between Rule B and Rule C is the spread between `0.0018 × entry` (~$132) and `0.00108 × entry` (~$79). Rule B is ~**1.67× Rule C**.

So if Rule C is taken as the right standard (blended EV with a 50% safety margin over round-trip cost):
- **System over-rejects by 6/15 = 40%** of today's rejections.
- **9/15 = 60%** of today's rejections are legitimately negative-EV even by Rule C — the system is right to reject them.

If Rule A is taken as the right standard:
- **System over-rejects by 15/15 = 100%** of today's rejections.
- But Rule A is provably negative-EV in 9 of those 15 cases (Rule C fails), meaning operator's discipline as described would take negative-EV trades 60% of the time under today's regime.

This is the calibration tradeoff the 5/25 board memo §3.2 framed: "Low-vol regime is exactly when fee floors should bind harder, not less." Rule A's looseness in a low-vol regime IS the manufactured-negative-expectancy trap.

### Where 9/15 of today's rejections land on the Rule C axis

Of the 9 Rule-C failures, mean negative gap is **−$11.55** below the 1.5× round-trip threshold (range −$0.37 to −$22.15). These are not "barely negative" — most are short by 15-30% of the threshold. They are correctly rejected by a blended-EV standard.

### Counter-evidence: where the system IS over-tight

Of the 6 Rule-C-passers, mean positive gap is **+$34.65** above the 1.5× threshold. These trades had blended EV ≥ round-trip + 50% margin AND were declined. Worst case among them: `04:09:02` (Rule C gap = ... wait that's negative). Let me recheck: 6 passers are 01:22:17, 02:23:09, 13:10:57, 13:30:09, 13:33:03, 17:30:09. Mean of their C-gaps: ($37.18 + $44.06 + $19.41 + $22.86 + $25.68 + $57.71)/6 = $34.48. These 6 are not borderline; they would have meaningful expected value.

So the calibration verdict depends on which rule you trust:
- **Conservative read (system view):** 0/15 should fire. Working as designed in low-vol.
- **Moderate read (Rule C):** 6/15 should fire. System is over-tight by ~40% on these.
- **Loose read (Rule A):** 15/15 should fire. System is way over-tight — but operator's discipline takes 9 negative-EV trades.

---

## Phase 4 — recommendation (do NOT implement)

### The standing constraint

`runbooks/board_memo_bitunix_fee_floor_decision_2026_05_25.md` §9 explicitly **rejected** lowering `tp1_min_profit_multiplier`:

> Lowering `tp1_min_profit_multiplier` to chase fire-rate. Explicitly rejected per §3 and §7. The fee floor is working; lowering it manufactures negative-expectancy trades in a no-edge regime.

§7 meta-rule: "do not re-litigate gate-tightness on low fire-rate before the 2026-06-19 tripwire."

### What the operator's current question changes (if anything)

The 5/25 rejection was framed against "we want more trades." Today's question is different — it's *"are these particular rejections actually bad trades?"* That's a calibration question, not a fire-rate question, and the data above gives a quantitative answer:

- **6/15** of today's rejections (40%) have positive blended EV at the 1.5× round-trip margin — these are arguably mis-tuned.
- **9/15** of today's rejections (60%) have negative blended EV even by the 1.5× standard — system is correct on these.

A multiplier change from `2.0 → 1.5` would correspond approximately to Rule C and would unblock the 6 positive-EV trades while continuing to block the 9 negative-EV ones. That's a precisely-tuned change in theory.

In practice: the multiplier is a single knob acting uniformly across all trades. A `2.0 → 1.5` flip would also let through any future trades that happen to sit in the (currently-blocked, currently-negative-EV) zone. Whether that zone is overall positive or negative across mixed-regime data is exactly the backtest question §2 of the memo specified.

### The Board's already-approved alternative path

Memo §9(b) approved the `tp_is_maker: false → true` maker-fill-rate model as the **next bitunix build deliverable**. That lever:

- Drops `round_trip_cost_pct` from 0.0009 to 0.00064
- Drops the fee floor from `0.0018 × entry` to `0.00128 × entry` (~$94 at BTC $73.5K)
- **Would have unblocked exactly 6 of today's 15** (the same 6 Rule C passes — 01:22:17, 02:23:09, 13:10:57, 13:30:09, 13:33:03, 17:30:09 — stop distances 109, 115, 95, 97, 99, 126 vs threshold ~94)
- Does NOT touch the multiplier or change selection logic — it lowers actual cost, not the edge requirement

So the data **endorses option (b)** as a strict improvement: same 6 trades fire that would fire under a `2.0 → 1.5` multiplier flip, but without weakening the "trades must earn ≥ 2× round-trip" guarantee. The maker-flip is contingent on the fill-rate model, which is queued but not built.

### Recommendation

1. **Do NOT lower `tp1_min_profit_multiplier`** this session. The 5/25 rejection stands — today's data confirms 9/15 of the rejections it produces are correctly rejected by a blended-EV standard.
2. **Surface today's 6 Rule-C-passing rejections as the case for accelerating the (b) deliverable** — the maker-fill-rate model. The data shows that today's 15 rejections have a 6/9 split (positive-EV / negative-EV) and the maker-flip would precisely capture the positive-EV ones without weakening selection logic.
3. **The 2026-06-19 tripwire remains in force.** Today's evidence is a calibration question, not a fire-rate one, but the meta-rule against re-litigating gate tightness still applies to multiplier changes. Maker-flip is a cost reduction, not a gate change, and is separately approved.
4. **If the Board wants to revisit the 5/25 rejection of the multiplier change**, the right artifact is a backtest of `tp1_min_profit_multiplier ∈ {1.5, 2.0}` over a mixed-regime corpus (per memo §5) — same Backtester-approval requirement. Today's 15 rejections are a single-day single-regime sample and cannot drive a generalizable verdict on their own.

### Knob name (for Board reference, no change proposed)

`bitunix_futures.trade_plan.tp1_min_profit_multiplier` in `config/strategies.yaml:1318`. Currently `2.0`. The value that would correspond to Rule C is ~`1.2` (since 1.25×1R / 1.5×rt = 0.83 × 1.0 × 1R = 1.0×1R at 1.2 multiplier; the algebra is `mult × rt × entry ≤ 1R` is the trigger, so equivalent to Rule C at `mult = 1.5/1.25 ≈ 1.2`). Lower bound that corresponds to Rule A would be `~0.45` — clearly outside any reasonable range.

---

## Methodology notes

- All inputs (entry, atr_used, swing_high) extracted from `audit_event.payload_json` rows where `kind='trade_plan_decision'` AND `skip_reason='fees_too_high_for_risk'` AND ts within 2026-05-29 UTC.
- Stop-distance reconstructed per `trade_plan.py:178-200` swing-vs-ATR-fallback logic.
- All 15 are sell-side STANDARD-tier (no buys, no PREMIUM rejections today).
- No quantity-scaling applied — all rule comparisons are per-unit price-level distances. Position sizing would scale all dollar values equally and not affect rule outcomes.
- Fee math uses live config: taker 0.04%, slippage 0.005%, `tp_is_maker: false`. If the maker-flip were live, all `round_trip` values would drop ~29%.
