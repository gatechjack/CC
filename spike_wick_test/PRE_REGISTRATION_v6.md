# Wick-Test v6 - OOS + FEE validation of the v5 lead (BC-long + strength). 2026-07-02.

CONFIRMATORY, not exploratory. Take the ONLY non-beta signal from v5 - breakout-continuation (BC stop) LONG
with the strength filter - and test whether it survives (a) a longer MULTI-REGIME window, (b) an in-sample /
out-of-sample split, and (c) a FEE model. Mechanism is FIXED; NO re-sweeping of shorts or other entries.
GROSS + NET reported. k=1 causal. Read-only; same branch, new file.

## Timeframe & data - 15m (multi-regime; step-0 confirmed no multi-regime 3m exists)
Native `bars_15m` per coin (~230-238d, spans up + down + range regimes, unlike the 3m 47-81d bear window).
Accept the coarser TF (operator-authorized) to get an out-of-bear test. ATR14, setup, entry, stop, exit all on
15m bars. Report span/gaps/drift/regime-distribution per coin (the multi-regime check).

## FIXED mechanism (from the v5 passing cells - no changes, no shorts, no other entries)
- Setup: 2 bullish 15m bodies; L=max(high[c1],high[c2]); confirm bar b3 breaks AND closes above L.
- Entry BC_stop LONG: buy-stop at E=high[b3]; fills iff a bar in {b3+1..b3+3} makes high>=E. entry=E.
- Filter STRENGTH (the v5 earn-its-place winner): bar3 body >= 1.0*ATR AND close in the top third of bar3
  range ((close-low) >= 2/3*range). Also run filter=none as the within-15m control.
- Stop k*ATR14 below E, k{1.0,1.5,2.0} (same 3 values as v5; not a new sweep). Target 2R. BODY-CLOSE of record
  + HARD contrast. Fill bar: stop-first loss only, target from fill+1 (conservative, as v5). MAX_HOLD 100 (15m)
  = ~25h. One-open-at-a-time gated by HARD exit.
- Reference only (NOT in the gate): one BC-short+strength line per coin (bear-beta sanity - shorts should stay
  null-discounted on a multi-regime window too, i.e. not systematically pass).

## FEE model (pre-registered)
COST_FRAC = 0.00058 of notional round-trip = 2 x taker(0.00019, Bitunix) + 2 x slippage(0.0001; BC is a
stop-entry that slips + body-close market exit). NET_R = gross_R - COST_FRAC*entry/rp. Report GROSS, NET@0.00038
(taker-only), NET@0.00058 (base) for fee sensitivity. The NULL is computed NET too (random long, same geometry,
minus the same COST_FRAC) - apples-to-apples. Cell PASSES iff NET avgR > 0 AND NET avgR >= NET-null p95.

## IS / OOS split
Chronological 60/40 on the 15m series (IS = first 60% of bars, OOS = last 40%). Mechanism is FIXED (nothing
fitted) so no leakage; the split tests temporal/regime STABILITY. Report NET avgR per coin per half.

## SUCCESS GATE (pre-registered, explicit PASS/FAIL) - confirmatory, net, stable, multi-coin
A COIN passes iff: >=2 of its 3 k-cells (BC-long+strength) have NET avgR > 0 at n>=100 AND >=1 of them beats the
NET-null. v6 PASSES iff: (1) >=2 coins pass; (2) for each passing coin, NET avgR > 0 in BOTH the IS and OOS
halves (sign-stable); (3) pooled NET avgR over passing (coin,k) cells >= +0.05R.
PASS -> the long-continuation edge is real, net-positive, OOS-stable, multi-coin -> advance to live-data /
paper validation. FAIL -> the v5 3m signal did not survive TF-transfer + OOS + fees -> WICK TEST RETIRED for good
(the runner-capture long lead was window/TF-specific).

## Caveats to state
15m != the 3m scalp the author describes (TF transfer is itself part of the test); GROSS shown alongside NET;
fee/slippage are modeled estimates (operator's real schedule may differ - sensitivity reported); one 230d sample
(IS/OOS are correlated halves, not independent draws). No lookahead: all reads from closed 15m bars; fills/sims
forward, stop-first.
