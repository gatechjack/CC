# SOL SFP-DIRECT entry test (no BOS wait) — pre-registered, 2026-06-26

**Theory (operator):** on alts the BOS-wait is redundant latency; entering at the SFP sweep itself may
capture the scalp the BOS-wait misses. **Test:** detector config FIXED; remove the BOS gate; enter
LONG at the open of the bar AFTER the sweep-completion bar (= the harness's own `raw_trade` path),
stop = swept wick − 0.001·entry. Targets: 2R (primary), the would-be-BOS level, and 0.5/1/1.5%
scalps. Frozen percoin harness on `sol_scalping.db`, read-only. (This is NOT the BTC-leads test.)

Fidelity: my 2R aggregate == the harness's own `raw` aggregate at every native TF (15m n55 23.6%
−0.424; 30m n55 27.3% −0.284; 1h n46 21.7% −0.398) → the extraction is the detector's logic.
k=1 mismatches = 0 at every TF.

## n is healthy now (BOS filter removed → ~2–4× more signals, as predicted)
SFP-direct n: 15m=55, 3m=57, 30m=55, 1h=46 — all ≥30 (vs BOS-confirmed 12/28/16/11). So this is a
real test, not thin.

## Results — net-R by (TF × target). EVERY cell is negative.
| TF | n | 2R | BOS-lvl | 0.5% | 1% | 1.5% | WF(2R) | median MFE |
|---|---|---|---|---|---|---|---|---|
| 15m | 55 | −0.424 | −0.303 | −0.378 | −0.535 | −0.379 | STABLE− | 0.62R / 0.38% |
| 3m | 57 | −0.327 | −0.421 | −0.370 | −0.321 | −0.591 | STABLE− | 1.02R / 0.37% |
| 30m | 55 | −0.284 | −0.173 | −0.247 | **−0.138** | −0.200 | STABLE− | 0.70R / 0.68% |
| 1h | 46 | −0.398 | −0.300 | −0.164 | −0.163 | −0.193 | thin | 0.92R / 0.99% |

Best cell anywhere = 30m @1% target: **−0.138, STABLE+** — still **negative**. No positive cell exists.

## SFP-direct vs BOS-confirmed @2R (matched TF) — does removing the wait help?
| TF | SFP-direct @2R | BOS-confirmed @2R | direct better? |
|---|---|---|---|
| 15m | −0.424 (n55) | −0.053 (n12) | **NO** (worse) |
| 3m | −0.327 (n57) | −0.483 (n28) | marginally (both bad) |
| 30m | −0.284 (n55) | +0.192 (n16) | **NO** (BOS positive, direct negative) |
| 1h | −0.398 (n46) | −0.478 (n11) | marginally (both bad) |

Removing the BOS-wait does NOT consistently help — it's **worse** at 15m and 30m. The BOS-wait is not
"redundant latency that hurts."

## Root cause (the honest takeaway)
**SOL's SFP favorable excursion is tiny relative to its stop.** Median MFE is only ~0.4–1.0% (≈0.6–1.0R)
across TFs — i.e. the *median* SFP-direct trade never even runs 1R in favor (15m 0.62R, 30m 0.70R). The
stop is the full swept-wick (~1R), so reward < risk: small scalps can't cover stop-outs + fees. That's
why **no target rule rescues it** — 0.5% hits more often (38–63%) but each win is a small R that fees
erode, while losers are −1R; 2R is far beyond the natural move. You cannot extract a positive
expectancy from a move that medians <1R favorable, regardless of entry timing or target.

## VERDICT (pre-registered, honest)
- **SFP-direct gives SOL NO positive + WF-stable edge at n≥30.** Every TF × every target is negative;
  n is healthy (so it's not a sample problem) and mostly STABLE− (consistently negative across WF).
  The least-bad cell (30m @1%, −0.138, STABLE+) is still a loser.
- **Theory REFUTED.** The BOS-wait is not the problem; the SOL SFP simply doesn't produce a
  favorable-enough excursion to beat stop+fees, with or without the wait.
- Combined with the TF test (lower TF → closer BOS but worse R), the picture is consistent: **SOL's
  SFP is not an edge at any TF or entry style.** SOL stays **monitor-only, no re-fit.**
