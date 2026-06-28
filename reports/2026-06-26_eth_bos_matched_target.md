# ETH SFP→BOS × matched-target — the unrun cell (pre-registered, 2026-06-26)

The ETH battery tested the target ladder on SFP-DIRECT only; the BOS view was reported only at 2R
(−0.371) — the wrong target (above ETH's MFE). This runs ETH SFP→BOS (15m body-close BOS = BTC's live
mechanic) across the MATCHED targets. Frozen percoin harness on eth_scalping.db, 15m, honest taker
entry. Read-only.

## Result — ETH 15m SFP→BOS (BOS-confirmed n=22, k=1 0/28)
median entry→stop R% = **1.400%** (vs direct 0.64% — the BOS-wait drifts entry up, >2× the stop);
median MFE = **1.05R** (vs direct 1.52R — on the wider BOS-R, the same move is fewer R).

| target | n | win | avg net-R | WF | direct ref | per-quarter |
|---|---|---|---|---|---|---|
| **1.0R** | 22 | 54.5% | **+0.044** | STABLE+* | −0.096 | 25Q4 −0.07(n2) · **26Q1 +0.04(n13)** · 26Q2 +0.09(n7) |
| 1.25R | 22 | 40.9% | −0.130 | STABLE− | −0.010 | 25Q4 +0.06(n2) · 26Q1 −0.18(n13) · 26Q2 −0.09(n7) |
| 1.5R | 22 | 36.4% | −0.142 | STABLE− | +0.020(MIXED) | 25Q4 +0.18 · 26Q1 −0.28 · 26Q2 +0.02 |
| 2.0R | 22 | 22.7% | −0.371 | STABLE− | −0.371 | (cross-check ✓ matches prior BOS 2R) |

## Honest read of the one positive (ETH BOS @1.0R = +0.044, "STABLE+")
- **BOS DOES help ETH at the right target** — exactly the BTC-analogous lift: direct@1.0R −0.096 →
  BOS@1.0R **+0.044**. (BTC: direct −0.066 → BOS +0.368.) So the BOS-confirmation mechanism that is
  BTC's edge also nudges ETH from negative to ~breakeven, *when paired with a tight (1.0R) target*.
- **But it does NOT clear verdict-grade — three independent disqualifiers:**
  1. **n=22 < 30** → SUGGESTIVE, not verdict-grade (per the pre-registered n caveat).
  2. **The "STABLE+" flag rests on a SINGLE n≥10 quarter** — only 26Q1 (n13, +0.04) qualifies; 25Q4
     (n2) and 26Q2 (n7) are below the n≥10 threshold the flag uses. Per the pre-registered rule, a
     single-quarter positive does NOT count — this is not genuine multi-quarter walk-forward stability.
     (And 26Q1's +0.04 is itself barely positive.)
  3. **+0.044R is economically ~breakeven** — it would not survive any real cost beyond the model.
- **Magnitude vs BTC:** ETH's best (+0.044 @1.0R, suggestive) is an order of magnitude below BTC's
  edge (+0.368 @2R, n27, STABLE+ on multiple quarters). At 2R, BTC is +0.368 while ETH is −0.371 —
  opposite signs. BTC's SFP→BOS is a real edge; ETH's is marginal-at-best even optimally configured.

## VERDICT
- **ETH SFP→BOS × 1.0R is the nearest miss of the whole alt program** — the first alt cell that turns
  positive with a WF flag, and it confirms the mechanism (BOS helps ETH, like BTC). **But it FAILS the
  gate:** n<30 (suggestive), "STABLE+" from a single qualifying quarter (not real stability), and
  ~breakeven magnitude. **NOT promotable. ETH stays monitor-only.**
- The limiter is **sample**: only 22 BOS-confirmed long setups in ETH's 15m history — BOS halves an
  already-sparse set. Genuine validation would need more data (forward-track) to see if 26Q1's
  thin positive holds across multiple well-sampled quarters. Not promoted on this evidence.
- Note: the BOS entry also *widens* ETH's stop (R 0.64%→1.40% via drift) and shrinks MFE-in-R to
  1.05R — so even the suggestive +0.044 is on a wide-stop, fee-heavy footing.

## Consolidated (SFP alt program complete)
BTC = the edge (BOS+wide, +0.267 pooled / +0.368 15m, live, load-bearing). SOL = dead (no excursion).
XRP = excursion but no positive target. **ETH = has excursion AND BOS+1.0R reaches a suggestive
~breakeven (+0.044, n22, single-quarter) — the closest, but not a verdict-grade edge.** All alts
monitor-only; live config unchanged (BTC-only). The SFP→BOS edge is BTC-specific; ETH is the only alt
worth a forward-track revisit if more data accrues, purely on the BOS×1.0R suggestion.
