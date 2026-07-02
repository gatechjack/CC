# Wick-Test v5 - Runner-Capture (breakout-continuation) spike - pre-registration. 2026-07-02.

Test whether a STOP entry that captures continuation (runners) beats the DR limit entry that captured
pullbacks (v3/v4 losers). GROSS only, k=1 causal, 3m, 4 coins. Read-only; same branch, new file.

## Step-0 data check (done): multi-regime 3m NOT available
bars_1m is SHORTER than bars_3m (BTC 50d<81d; alts 18.8d<46.8d); bars_3m (47-81d, bear) is the longest 3m
source. Proceeding on 47-81d per operator option (a): the LONG-ALPHA tell + drift-embedding null carry the
bear-proof (a LONG cell beating a drift-null in a bear window cannot be bear-beta).

## Setup (unchanged from v3/v4)
2-bar same-direction impulse; L=impulse high(long)/low(short); confirm bar b3 breaks AND closes beyond L.

## Entry - HEAD-TO-HEAD on identical setups
- DR_limit (baseline = v3/v4): limit at L; fills iff a bar in {b3+1..b3+3} retests L. entry=L.
- BC_stop (hypothesis): buy-stop at E=high[b3] (short: sell-stop at low[b3]); fills iff a bar in {b3+1..b3+3}
  takes out the confirm-bar extreme (long high>=E; short low<=E). entry=E. Captures runners (fills on
  continuation), skips pullback-failures. Justification: DR-skip runners were +0.45..+0.76R would-have-been.

## Stop / target / exit (reuse v3/v4 machinery; SAME risk unit -> DR vs BC apples-to-apples)
k*ATR14 below/above entry, sweep k{1.0,1.5,2.0}; author's structural "back-below/above-L" stop as reference.
Target 2R. BODY-CLOSE of record + HARD contrast on identical fills. Fill bar: stop-first loss check only,
TARGET from fill+1 (conservative + SYMMETRIC for both entries; note this understates same-bar BC continuation
wins - conservative direction). MAX_HOLD 100, timeout=mtm. One-open-at-a-time gated by that cell's HARD exit.

## Filter - no-filter H0 (trend filters failed x3: v1/v3/v4)
FILTER {none (H0), strength}. strength challenger = bar3 body >= 1.0*ATR AND close in the extreme third of
bar3 range (long: (close-low)>=2/3*range; short: (high-close)>=2/3*range). Earn-its-place only.

## Grid
entry{DR,BC} x k{1.0,1.5,2.0} x filter{none,strength} = 4 coins x 2 sides x that = 96 body cells (+ structural
reference). 1R robustness only on a winning BC config.

## Fill-matrix diagnostic (decisive, identical setups)
At k=1.5 none, per coin x side: n and body-avgR of DR-fills (pullback) vs BC-fills (continuation); and the
BOTH-filled subset avgR_DR vs avgR_BC (identical setups, differ only in entry). Shows causally whether
continuation-fills are the winners and pullback-fills the losers.

## Null + bear-beta de-confound
Null = direction-matched random-entry, SAME k*ATR geometry, SAME exit mode, 200x, p95 (EMBEDS drift = bear-beta
control). Cell PASSES iff avgR>0 AND avgR>=null_p95. Report per-coin drift + passive-short context. De-trended
alpha-R (gross minus coin mean per-bar drift x hold, in R) reported alongside as a supplement.

## SUCCESS GATE (pre-registered, explicit PASS/FAIL) - v3-consistent + LONG-ALPHA requirement
(1) >=3 body cells pass (avgR>0 AND >=null_p95) at n>=100; (2) >=2 coins; (3) both sides represented AND
>=1 LONG cell passes (bear-proof alpha tell); (4) pooled gross avgR of passers >= +0.15R; (5) regime split
informational. PASS -> fee-modeled + OOS validation. FAIL -> runner-capture also yields no alpha here; the
wick-test family is exhausted on this data.

## Deviation from author spec (flagged)
BC_stop is NOT the retest wick test - it is breakout-continuation / BOS-follow-through (the author's other named
continuation context). Tested head-to-head vs the retest baseline so the comparison is explicit, not smuggled.
