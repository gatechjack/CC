# Wick-Test v4 - pre-registration (locked before code). 2026-07-02.

Single-variable change from v3: replace the earn-its-place momentum filter (impulse-displacement) with a
**20-bar price-slope directional trend filter**. Everything else identical to v3 (DR entry, k*ATR stop sweep
{1.0,1.5,2.0}, body-close of record + hard contrast, 2R, symmetric long/short, 4 coins, DR-skip diagnostic,
same-geometry direction-matched null 200x, pre-registered success gate, bear-beta de-confound). GROSS only,
k=1 causal, 3m, read-only, no prod/live/SFP writes, same branch, new file.

## The only change: trend filter = sign of 20-bar slope
- `slope20[b3]` = OLS linear-regression slope of close over the last 20 3m bars (close[b3-19..b3] vs x=0..19),
  computed CAUSALLY at the confirm bar b3.
- Motivation: v1 refuted the 15m-EMA200 with-trend filter as the WRONG TIMEFRAME for a 3m scalp; a 20-bar 3m
  slope (~1h) is a timeframe-local trend that directly tests the author's "with-trend" precondition on an
  appropriate horizon.
- FILTERS = {none (control, H0), slope}. `slope` = directional with-trend: LONG taken iff `slope20>0`; SHORT
  iff `slope20<0` (slope==0 -> skip under the filter). 15m ema200 regime stays informational only.
- Keep only if the slope-filtered variant clears the null where no-filter does not, or improves avgR at equal
  clearing (earn-its-place).

## Unchanged from v3
DR entry (2-body impulse + bar-3 breaks & closes beyond L; limit at L fills iff a bar in {b3+1..b3+3} retests L;
else skip). Stop k*ATR14 (sweep) + literal reference. Body-close = invalidation of record; hard = paired
contrast on identical fills. Target 2R (+1R robustness on winner only). Null: direction-matched random-entry,
same geometry, same exit mode, 200x, p95; cell PASSES iff avgR>0 AND avgR>=null_p95. DR-skip diagnostic
(would-have-been at bar-3 close). Bear-beta: drift + passive-short context; both-sides success gate.

## SUCCESS GATE (identical to v3, pre-registered, explicit PASS/FAIL)
>=3 body cells pass (avgR>0 AND >=null_p95) at n>=100; >=2 coins; BOTH sides represented; pooled avgR of passers
>= +0.15R; regime split reported informational. PASS -> advance to fee-modeled + longer/OOS validation.
FAIL -> the slope-trend variation does not rescue the wick test (adds to the retired ledger).
