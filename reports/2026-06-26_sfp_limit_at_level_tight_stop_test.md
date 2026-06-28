# SFP LIMIT-AT-LEVEL (tight-stop) test — SOL + BTC, pre-registered, 2026-06-26

**Hypothesis:** the harness used a WIDE stop (R = full sweep bar, ~0.52% direct / 1.34% BOS) because
entry sits at the reclaim/BOS-drifted price. A true SFP enters at the swept level with a stop just
under the wick → R ≈ 0.26%. Does tight geometry flip the verdict WITHOUT changing the signal?

**Entry (pre-registered, honest fill, k=1):** on an SFP (sweep of pivot-50/50 low → reclaim close above
level), place a LIMIT BUY at the swept_level; it fills ONLY if a bar in [b+1..b+4] trades down to
low ≤ level (price genuinely returned); else SKIP. Stop = swept_wick − 0.001·entry (tight). **Maker**
entry fee (limit). 2R primary + 0.5/1/1.5% + BOS-level. N=4, no sweep. Frozen oracle + DBs read-only.

## Geometry fix CONFIRMED + the mechanism worked
| coin | fires | filled | skipped | median entry→stop R% | median MFE | k=1 |
|---|---|---|---|---|---|---|
| SOL | 55 | 39 | 16 (29%) | **0.271%** | **1.53 R** | 0/28 |
| BTC | 56 | 42 | 14 (25%) | **0.291%** | **2.08 R** | 0/28 |

R% is now tight (~0.27–0.29% vs wide direct ~0.52 / BOS ~1.34), and median MFE jumped to 1.5–2R (vs
~0.6R on the wide stop) — the same price moves ARE 2–4× the R now, exactly as predicted. ~25–29% of
SFPs never returned to the level (the cost of limit entry — those runners are skipped).

## But net-R does NOT improve — both still negative
### SOL 15m (limit-tight, maker-net)
| target | n | win | avgR | WF |
|---|---|---|---|---|
| 2R | 39 | 20.5% | **−0.606** | STABLE− |
| BOS-lvl | 39 | 15.4% | −0.595 | STABLE− |
| 0.5% | 39 | 30.8% | −0.370 | STABLE− |
| 1% | 39 | 12.8% | −0.640 | STABLE− |
| 1.5% | 39 | 10.3% | −0.475 | STABLE− |

Wide-stop ref @2R: SFP-direct −0.424, SFP→BOS −0.053. **Tight-stop is WORSE (−0.606), not better.**

### BTC 15m (limit-tight, maker-net) — ★ the live-edge check
| target | n | win | avgR | WF |
|---|---|---|---|---|
| 2R | 42 | 38.1% | **−0.069** | MIXED |
| BOS-lvl | 42 | 31.0% | −0.141 | MIXED |
| 0.5% | 42 | 38.1% | −0.260 | STABLE− |
| 1% | 42 | 28.6% | −0.137 | MIXED |
| 1.5% | 42 | 23.8% | +0.079 | MIXED |

Wide-stop ref @2R: SFP-direct −0.066, **SFP→BOS +0.368** (= BTC's live 15m edge; pooled +0.267).
**Tight-stop limit entry (−0.069) is FAR WORSE than BTC's live BOS wide-stop edge (+0.368).** Only the
1.5% target is marginally positive (+0.079) and it's MIXED (not WF-stable) — not an edge.

## Why tight-stop doesn't win (despite bigger MFE-in-R)
Two offsetting costs swamp the higher reward:R: (1) **win rate falls** — a 0.27% stop is hit by normal
retest noise, so more stop-outs; reaching a 2R *target* (0.54%) before the tight stop is hard. (2)
**fee drag in R explodes** — at R≈0.27%, each trade carries ~0.14R (maker win) to ~0.24R (taker stop)
in fees, vs a fraction of that on the wide stop. The bigger MFE-in-R is real but doesn't convert: you
must REACH the target before the tight stop, and fees-per-R are heavy. (Modeled with the *favorable*
maker entry fee — it still loses.)

## VERDICT (pre-registered)
1. **SOL — tight-stop does NOT flip it.** Every target is negative and STABLE− (2R −0.606, worse than
   the wide stop). **The wide stop was NOT the artifact** — SOL's SFP is not an edge under tight OR
   wide geometry. SOL stays monitor-only.
2. **BTC — tight-stop is WORSE and would DESTROY the live edge.** The live geometry (**BOS-confirmed +
   wide stop, +0.368 on 15m / +0.267 pooled**) is load-bearing; the limit-at-level tight-stop variant
   is −0.069 (≈breakeven-negative). For BTC the BOS-wait itself is the edge (direct −0.066 → BOS
   +0.368) — and the wide stop is part of it. **DO NOT change the live BTC SFP geometry to tight-stop
   / limit-entry.** The current +0.267R live edge stands; this change would lose it.

Net: the stop-geometry diagnostic correctly identified the wide stop, but fixing it does not rescue SOL
and is harmful to BTC. The live config is validated as-is. No live change.
