# Fee COUPLED-correction verification (Decision A) — 2026-06-22

**Verdict: PASS.** The coupled fee correction reproduces today's baseline book
exactly. **Flipped cohort A→C = 0** (all 6 windows). Net-R improves (genuine fee
saving), book composition unchanged.

READ-ONLY / clean corpus (`btc_scalping.db`) / local sim only. No strategy,
config, or production change; no prod contact.

---

## What was verified

The COUPLED change (Decision A) corrects the venue-actual taker rate AND bumps the
TP1 profit multiplier so the `fees_too_high_for_risk` gate's fee-floor is held
constant:

| knob | baseline | coupled |
|---|---|---|
| `fees.taker_pct` | 0.0004 | 0.00019 |
| → `round_trip_cost_pct` | 0.00090 | 0.00048 |
| `trade_plan.tp1_min_profit_multiplier` | 2.0 | 3.75 |
| → `tp1_fee_floor = mult × rt × entry` | **0.00180 × entry** | **0.00180 × entry** |

Identical fee-floor per entry ⇒ the gate (`tp1_distance ≥ tp2_distance`,
`trade_plan.py:251`) skips the SAME signals and places TP1 at the SAME distance.

Three configs were run on the SAME 6 lockbox windows at a FIXED redeem **cap = 2**
(the /goal windows; cap question is closed-NULL — this is the FEE axis):

- **A. BASELINE**  taker 0.0004,  tp1_mult 2.0  (today's behaviour)
- **B. RATE-ONLY** taker 0.00019, tp1_mult 2.0  (the rejected standalone)
- **C. COUPLED**   taker 0.00019, tp1_mult 3.75 (the fix)

Tool: `scripts/run_redeem_sim.py` (redeem sim driving
`backtest_bitunix_confluence.run_redeem_cap_backtest`). A minimal additive
`--tp1-mult` override (`_tp1_mult_override`, mirrors the existing `_fee_override`;
rebinds the frozen `_SCFG.tp1_min_profit_multiplier`, default None = no-op) was
added and unit-tested. Analysis driver:
`scripts/research_scoring/fee_coupled_verify.py`. Raw JSON:
`scripts/_redeem_goal_out/fee_coupled_verify.json`.

---

## Per-window results (cap = 2)

`fee_skip` = signals plan-skipped specifically for `fees_too_high_for_risk`.
`flip A→C` = baseline fee-skips re-admitted under coupled (MUST be 0).
`flip A→B` = baseline fee-skips re-admitted under rate-only (the rig-sanity cohort).

| window | lockbox | walked A | walked C | fee_skip A | fee_skip C | **flip A→C** | flip A→B | skip-set A==C | gross-R diff A↔C |
|---|---|---|---|---|---|---|---|---|---|
| 2026-04-01…04-15 | TRAIN | 31 | 31 | 64 | 64 | **0** | 21 | ✅ identical | 0.0 |
| 2026-04-15…04-29 | TRAIN | 30 | 30 | 87 | 87 | **0** | 39 | ✅ identical | 0.0 |
| 2026-05-01…05-15 | TRAIN | 23 | 23 | 74 | 74 | **0** | 29 | ✅ identical | 0.0 |
| 2026-05-15…05-29 | VALIDATE | 38 | 38 | 113 | 113 | **0** | 50 | ✅ identical | 0.0 |
| 2026-05-20…06-03 | VALIDATE | 42 | 42 | 104 | 104 | **0** | 38 | ✅ identical | 0.0 |
| 2026-06-03…06-17 | VALIDATE | 33 | 33 | 18 | 18 | **0** | 6 | ✅ identical | 0.0 |
| **TOTAL** | | **197** | **197** | **460** | **460** | **0** | **183** | **all identical** | **0.0** |

---

## net-R: book unchanged, NOT worsened, NOT more conservative

The admitted **set** and **gross-R / TP placement** are byte-identical A↔C
(`gross_R_max_diff = 0.0` every window). Net-R legitimately IMPROVES under C
because the realised round-trip cost genuinely fell (0.00090 → 0.00048) — the
multiplier neutralises the *gate loosening*, NOT the *fee saving*.

| window | total net-R A | total net-R B (rate-only) | total net-R C (coupled) |
|---|---|---|---|
| 2026-04-01…04-15 | -16.47 | -17.01 | -11.14 |
| 2026-04-15…04-29 | -11.28 | -25.43 | -5.81 |
| 2026-05-01…05-15 | -4.75 | -13.09 | -0.47 |
| 2026-05-15…05-29 | -9.23 | -19.09 | -2.19 |
| 2026-05-20…06-03 | -11.94 | -14.97 | -4.53 |
| 2026-06-03…06-17 | -7.66 | -5.76 | -2.47 |
| **TOTAL** | **-61.33** | **-95.35** | **-26.62** |

Per-trade net-R on the SAME shared trade differs A→C by ~+0.23R (the realised
round-trip-cost delta `(0.0009 − 0.00048) × entry/risk`), in the favourable
direction for every trade. The book is the same trades, sitting at the same
targets, costing less — exactly as designed.

---

## Confirmation against the four required checks

1. **C's `fees_too_high_for_risk` skip set == A's skip set** — TRUE on all 6
   windows (skip counts identical: 64/87/74/113/104/18). **Flipped cohort A→C = 0**
   (exactly 0, not just ~0).

2. **C's traded set + gross-R / TP placement == A's** — TRUE. Same 197 walked
   trades, identical admitted set per window, `gross_R_max_diff = 0.0`. The book
   is IDENTICAL in composition. (net-R is *better*, not equal — the genuine fee
   saving; the book is not worsened and not more conservative.)

3. **Rig sanity — B re-admits a large net-negative cohort** — TRUE. Rate-only
   re-admits **183** baseline fee-skips (matches the Step-2 number exactly), and
   that cohort is net-NEGATIVE (B re-admit total net-R = **-67.37**; B whole-book
   total -95.35 vs baseline -61.33 — strictly worse). So the multiplier bump is
   what neutralises the rate correction; it is not a no-op.

4. **Fee-floor identity spot-check (algebraic, corpus-free)** — TRUE. For sample
   entries {30000, 60000, 105123.45, 1.0}: `2.0 × 0.00090 × entry` ==
   `3.75 × 0.00048 × entry` == `0.00180 × entry` to float tolerance
   (unit test `test_coupled_fee_floor_identity_is_algebraic`).

---

## Re-derivation check

The claimed multiplier **3.75** is exactly correct, NOT approximate:

```
required:  mult_C × rt_C  ==  mult_A × rt_A
           mult_C × 0.00048 == 2.0 × 0.00090 == 0.00180
           mult_C = 0.00180 / 0.00048 = 3.75   ✔ exact
```

`rt_C = 0.00019 + 0.00019 + 2×0.00005 = 0.00048` (taker entry + taker exit + 2 slip).
No discrepancy; no corrected multiplier needed.

---

## PASS/FAIL

**PASS — "coupled reproduces baseline at the true rate."**

- Flipped cohort A→C = **0** (all windows)
- Skip sets identical A↔C (all windows)
- Admitted sets identical A↔C, gross-R diff = 0.0 (TP placement unchanged)
- Rig validated: B (rate-only) re-admits 183 net-negative trades
- net-R improves (genuine fee saving) — book not worsened, not more conservative

The coupled change is gate-/book-neutral by construction and the identity holds
empirically on the clean corpus. The only effect on the admitted book is a
uniformly lower (correct) fee cost.

---

### Artifacts (sim branch `bitunix-redeem-sim-2026-06-22`)
- `scripts/run_redeem_sim.py` — added `--tp1-mult` / `_tp1_mult_override` (additive, default off)
- `scripts/research_scoring/fee_coupled_verify.py` — A/B/C analysis driver
- `scripts/_redeem_goal_out/fee_coupled_verify.json` — raw per-window results
- `tests/test_run_redeem_sim.py` — 6 new tests (override no-op/restore/reject + book-composition identity + algebraic fee-floor identity); full file 47/47 pass
