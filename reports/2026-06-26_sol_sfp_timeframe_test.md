# SOL SFP→BOS — timeframe test (pre-registered, 2026-06-26)

**Hypothesis (operator, eye-derived):** on 15m the BOS confirmation lands far (~2.3%) from the SFP, so
momentum is exhausted by entry → losers. A **lower TF** should bring BOS closer to the SFP (finer
structure) and catch the move while momentum is live → restore the edge.

**Test:** BTC's config FIXED (pivot 50/50, REAL+CONSIDERABLE, long-only, fixed 2R, stop = swept wick −
0.001·entry); the ONLY variable is the detector's bar timeframe. Frozen percoin harness on
`sol_scalping.db`, read-only. ★ key diagnostic = **BOS-distance %** (swept level → BOS-confirm close).

## Data spans (SOL, native)
3m: 2026-05-11→06-26 (**46d**) · 15m: 2025-11-01→06-26 (237d) · 30m: 2025-01-01→06-26 (541d) · 1h:
2024-01-01→06-26 (907d). **5m: not cleanly available** — SOL 1m is only ~18d (2026-06-08+), too short
for pivot(50,50)+n; 3m→5m is not an integer multiple. Not fabricated.

## Summary — SOL long, config fixed, TF varied
| TF | n | win@2R | avgR | WF | median BOS-dist% | wins/loss med BOS-dist% |
|---|---|---|---|---|---|---|
| 3m | 28 | 21.4% | **−0.483** | STABLE− | **0.39** | 0.42 / 0.38 |
| 15m (ref) | 12 | 33.3% | −0.053 | thin | 1.14 | 1.14 / 1.22 |
| 30m | 16 | 37.5% | **+0.192** | thin | 1.33 | 1.34 / 1.33 |
| 1h | 11 | 18.2% | −0.478 | thin | 2.54 | 1.45 / 2.74 |

k=1 mismatches = 0 on every TF. Outcomes: 3m 6W/22L, 15m 4W/8L, 30m 6W/9L/1to, 1h 2W/9L.
bars-armed gap medians: 3m 4, 15m 10.5, 30m 5.5, 1h 7.

## Mechanism check (the part that separates "real" from "lucky TF")
1. **Lower TF → closer BOS: CONFIRMED.** Median BOS-distance shrinks monotonically with TF —
   1h 2.54% → 30m 1.33% → 15m 1.14% → 3m 0.39%. The structural half of the hypothesis is real.
   (Note: the *15m* median is ~1.1%, not the ~2.3% eyeballed; 2.5% is the *1h* figure.)
2. **Closer BOS → positive edge: REFUTED.** The TF with the SMALLEST BOS-distance (3m, 0.39%) is the
   MOST NEGATIVE (−0.483R, STABLE−). Lower TF makes SOL **worse**, not better.
3. **Within-TF, smaller BOS-distance does NOT predict wins.** 3m: wins' BOS-dist (0.42) is *larger*
   than losses' (0.38) — opposite sign. 15m/30m: wins≈losses (no separation). Only 1h shows
   wins<losses (1.45 vs 2.74) but on n=2 wins = noise. No consistent correlation.
4. **The one positive (30m +0.192) is a small-n TF blip, not the mechanism.** n=16 (<30), WF "thin"
   (no quarter reaches n≥10 → stability unassessable), AND 30m's BOS-distance (1.33%) is *larger* than
   15m's and 3m's — the opposite of what the mechanism would require if BOS-distance were the lever.

## VERDICT (pre-registered, honest)
- **No timeframe gives SOL a positive + WF-stable edge at n≥30.** 3m is negative + STABLE− (and only
  46d history); 15m/30m/1h individually have n<30 with WF "thin" (the n≥30 in the transfer test came
  only from pooling TFs). 30m's +0.192 fails n≥30 and WF.
- **The hypothesis is REFUTED at the conclusion level.** Its premise (lower TF → closer BOS) is
  mechanically confirmed, but the payoff (closer BOS → live momentum → profit) does not hold: the
  closest-BOS TF (3m) is the worst, and BOS-distance doesn't correlate with R within any TF. TF alone
  is not the lever; SOL's SFP→BOS is simply not an edge at any tested TF.
- **SOL stays monitor-only. No re-fit.** Per the discipline: a BOS-distance gate is NOT added (and the
  no-correlation result makes it unlikely to help, but that's a separate experiment if pursued).
