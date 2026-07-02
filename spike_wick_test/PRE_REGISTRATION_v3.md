# Wick-Test v3 - pre-registration (locked before code). Authorized 2026-07-02.

Refine wick-test into a tradeable setup by combining v1+v2 lessons. GROSS only, k=1 causal, 3m, 4 coins.
Read-only; no prod/live/SFP writes. Same branch, new files.

## Entry - Deferred-Retest (DR) at L
- SETUP bars c1=b3-2, c2=b3-1: two same-direction bodies. LONG both bullish; SHORT both bearish.
- LEVEL: LONG `L=max(high[c1],high[c2])`; SHORT `L=min(low[c1],low[c2])`.
- CONFIRM bar b3 (breaks AND closes beyond L): LONG `high[b3]>L AND close[b3]>L`; SHORT `low[b3]<L AND
  close[b3]<L`. (Two selection gates the vacuous v2 fill lacked.)
- FILL: limit at L fills iff a bar in {b3+1,b3+2,b3+3} (bars 4-6) retests L (LONG low<=L; SHORT high>=L);
  entry=L, fill bar = first such. No fill in window -> SKIP (feeds the DR-skip diagnostic).

## Stop - k*ATR14 (sweep) + author's literal (reference)
- ATR14 = SMA(TrueRange,14) on 3m, causal; use `atr[b3]` (known when the limit is placed).
- ATR stop: LONG line=`L - k*atr[b3]`; SHORT `L + k*atr[b3]`. R-unit=k*atr[b3]. SWEEP k in {1.0,1.5,2.0}.
- LITERAL reference: line = `min(low[b3],L)-buffer` (LONG) / `max(high[b3],L)+buffer` (SHORT), buffer=0.0005*L
  (a hair beyond the setup candle). Reported ALONGSIDE (filter=none), NOT null-gated, NOT counted in the gate.

## Targets / exit modes
- Target 2R (fixed) off the shared R-unit; single 1R robustness re-run on the winning (k,filter) only.
- BODY-CLOSE = invalidation of record (exit at close of first bar whose BODY closes beyond line; realized
  loss can be < -1R). HARD = paired contrast (any line touch -> -1R) on IDENTICAL fills, for the
  shakeout-value re-measurement under the wider stop. Fill bar: loss-checked, target from fill+1
  (conservative; pre-fill excluded). Same-bar stop+fill -> stop-first. MAX_HOLD 100 bars, timeout=mtm.

## Momentum filter - earn-its-place (no-filter H0)
- Default = NO filter. Challenger = impulse-displacement: `body(c1)+body(c2) >= 1.0*atr[b3]` AND close beyond
  L by `>= 0.25*atr[b3]`. Keep only if it clears the null where no-filter doesn't, or improves avgR at equal
  clearing. 15m ema200 regime = informational column only.

## One-open-at-a-time / identical fills
Per (side, stop, filter) config, gate by that config's HARD exit index so HARD and BODY run on identical
fills. n may differ across stops (wider holds longer) - reported per cell; each cell null-gated at its own n.

## Grid (tight)
FIXED: level L, DR entry (window 4-6), body-close record + hard contrast, 2R, symmetric long/short, 4 coins.
SWEEP: k{1.0,1.5,2.0} x filter{none, displacement} = 6 -> 48 body cells; + literal x filter=none = 8 ref cells.

## Null (upgraded)
Per cell: direction-matched random-entry, SAME geometry (entry=close[j], line=entry -/+ k*atr[j], tp target*R),
SAME exit mode (body-close carries its loss profile), 200x, p95 of avgR at the cell's n. A cell PASSES iff
`avgR_body > 0 AND avgR_body >= null_p95` (closes v2's negative-null loophole). Literal cells: no null (ref).

## DR-skip diagnostic (added, reported prominently)
Every TRIGGERED-but-unfilled setup (filter=none): simulate an alternative entry at bar-3 CLOSE with the SAME
k*ATR stop + 2R target (body-close), from b3+1. Report would-have-been-R pooled + per coin x side (k=1.5) and
pooled per k. Isolates whether DR filters WINNERS (v1's fill-selection problem) or NOISE.

## Bear-beta de-confound
Per coin: close-to-close drift% and passive-short R-equivalent. The direction-matched null INHERENTLY embeds
drift (random shorts profit in a bear window), so beating it is a bear-beta control; the both-sides success gate
is the hard guard. Flag any short cell whose avgR <= its null p95 (fails to beat drift-random). 47-81d one-bear
caveat stated up front.

## SUCCESS GATE (pre-registered; explicit PASS/FAIL, no wiggle)
v3 is a tradeable candidate iff ALL: (1) >=3 body cells pass (avgR>0 AND >=null_p95) at n>=100; (2) spanning
>=2 coins; (3) BOTH sides represented (>=1 long AND >=1 short passing) - short-only = bear-beta = FAIL; (4)
pooled gross avgR of passers >= +0.15R; (5) passes not confined to one regime bucket (informational split
reported). PASS -> advance to fee-modeled + longer/OOS validation. FAIL -> "wick test retired: three
independent strikes" (v1 no edge, v2 gross-negative, v3 fail).
