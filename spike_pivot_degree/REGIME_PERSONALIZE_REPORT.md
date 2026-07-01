# Per-Coin Personalization Bake-Off — Regime-Aware SFP (GROSS R)

*2026-07-01. Read-only. Harness `spike_pivot_degree/regime_personalize.py`. GROSS R
only (no fees — operator factors fees). Base = live `SfpModeBDetector` 15m SFP → 3m BOS
→ next-3m-open entry, live stop; regime = 15m EMA-200+slope; side-by-regime (long-UP /
short-DOWN / both-RANGE, never counter-trend); fixed 2R; one position/coin; pivots
{5,8,10} union. **IS/OOS = first 60% / last 40% by entry time** within each coin's 3m
window. Selection on OOS (per spec) — optimistic; IS shown for consistency. k=1 causal
confirmed on every layer (EMA/slope/structure at bar t use data ≤ t; regime looked up at
the last fully-closed 15m/HTF bar before the 3m entry). Regime-shuffle null: 200×, p95.*

## HEADLINE
**Only BTC supports personalization.** BTC (81d, the only coin with enough OOS sample)
adopts the **4H regime engine + short-down 3R target** → **+0.417R OOS vs +0.138R base**,
clearing the null and winning IS too. **ETH/SOL/XRP (47d) keep base entirely** — no engine
or target beat base + null, 15/16 R:R cells are n<20, and SOL/XRP's positive base OOS does
**not** clear its own null (their edge is "short the bear," not the regime refinement).

## Per-coin sample (the binding constraint)
| coin | window | signals (long/short) | OOS trades (base) |
|---|---|---|---|
| BTC | 81d | 241 (118/123) | ~29–36 |
| ETH | 47d | 141 (78/63) | ~23–29 |
| SOL | 47d | 124 (70/54) | ~22–32 |
| XRP | 47d | 136 (65/71) | ~16–21 |

## STEP 1 — regime-engine bake-off (side-by-regime, fixed 2R, GROSS OOS)
| coin | base15 (IS→OOS) | 1H OOS | 4H OOS | struct OOS | adopt? |
|---|---|---|---|---|---|
| **BTC** | +0.020→**+0.138** (n29) | +0.324 (n34) | **+0.333** (n36) | −0.189 | **4H** — beats base+null (p95 +0.243) **and wins IS** (4H +0.053 > base +0.020) |
| ETH | +0.147→−0.073 (n27) | −0.241 | +0.479 (n23) | −0.091 | **base** — 4H beats base but **fails null** (p95 +0.683) |
| SOL | +0.241→+0.364 (n22) | −0.062 | +0.375 (n24) | +0.355 | **base** — 4H ties base but **fails null** (p95 +0.626) |
| XRP | +0.300→+0.925 (n16) | +0.467 | +0.752 | +0.765 | **base** — no engine beats base OOS |

- **BTC adopts 4H:** the only engine change that beats base on OOS **and** the null
  **and** IS. BTC has 81d (2× the others) → enough OOS to detect a real engine difference.
- **The null did its job:** ETH-4H (+0.479) and SOL-4H (+0.375) beat base but their null
  p95 (+0.68 / +0.63) is *higher* than the observed — random regime relabelings routinely
  match them, so the regime label isn't the source. Rejected.

## STEP 2 — R:R per cell (chosen engine, GROSS OOS, adopt vs 2R)
**15 of 16 cells are n<20 → directional-only, NOT hard-adopted.** The **only** cell with
n≥20 is **BTC short-down (n=28)**: 2R +0.179 → **3R +0.286 → ADOPT**. Everything else
kept at 2R (provisional bests flagged below).

| coin | cell | n@2R | 2R OOS | best target (OOS) | decision |
|---|---|---|---|---|---|
| **BTC** | **short-down** | **28** | +0.179 | **3R (+0.286)** | **ADOPT 3R** |
| BTC | long-range | 5 | +0.200 | 2R | keep 2R (thin) |
| BTC | short-range | 4 | +1.250 | 2R | keep 2R (thin) |
| ETH | short-down | 9 | +0.114 | 3R (+0.504) | provisional, keep 2R |
| SOL | long-up | 12 | +0.000 | 3R (+0.333) | provisional, keep 2R |
| SOL | short-down | 6 | +0.500 | trail (+1.167) | provisional, keep 2R |
| XRP | short-down | 8 | +0.975 | 3R (+1.100) | provisional, keep 2R |

Directional pattern across the thin cells: **bigger targets (3R) tend to win the
short-down cells** (bear trends run) — consistent with the prior R:R spike (shorts want
big targets) — but only BTC short-down has the sample to adopt it.

## STEP 3 — regime thresholds (light slope-cutoff probe)
No threshold change adopted. Stricter slope cutoffs mostly hurt or were marginal/thin
(SOL thr=0.005 +0.393 vs +0.364; XRP thr=0.002 +1.053 vs +0.925, n=15). None "clearly"
beat OOS; base thresholds kept for all coins (do-not-over-tune).

## FINAL — base vs personalized, GROSS OOS
| coin | engine | base-2R OOS | personalized OOS | null p95 | beats null | change |
|---|---|---|---|---|---|---|
| **BTC** | **4H** | +0.138 (n29) | **+0.417 (n36)** | +0.258 | **YES** | 4H engine + short-down 3R |
| ETH | base15 | −0.073 (n27) | −0.073 (n27) | +0.551 | no | none (base) |
| SOL | base15 | +0.364 (n22) | +0.364 (n22) | +0.600 | no | none (base) |
| XRP | base15 | +0.925 (n16) | +0.925 (n16) | +1.257 | no | none (base) |

**Only BTC's config beats its null.** SOL (+0.364) and XRP (+0.925) look positive but do
**not** clear their regime-shuffle null (p95 +0.600 / +1.257) — because shuffling the
regime label still selects mostly-positive bear-short trades, i.e. the edge is "short the
bear," not the regime refinement. ETH is negative OOS.

## Flags & overfit note
- **Thin cells (n<20, directional-only):** every STEP-2 cell except BTC short-down.
- **Provisional (confirm live):** the only hard adoption is BTC short-down 2R→3R (n=28,
  just over the bar); BTC 4H engine is on n≈30–36 OOS — modest. Both "confirm live."
- **Beats-null:** BTC only. ETH/SOL/XRP do **not** clear the null (SOL/XRP = bear-beta).
- **Selection-on-OOS is upward-biased.** The personalized OOS numbers were chosen on the
  same split they're scored on. The only trustworthy adoption is one that wins **IS + OOS
  + null** — which is **BTC alone** (4H wins IS +0.053>+0.020, OOS +0.333>+0.138, null).
- **Free-parameter / overfit accounting:** the per-coin search space is large (4 engines ×
  6⁴ per-cell targets × 3 thresholds ≈ 15k configs/coin), but one-axis-at-a-time + the
  null-gate limited hard adoptions to **exactly 2 parameters, both on BTC** (engine=4H,
  short-down target=3R). ETH/SOL/XRP defaulted to base on every axis. With ~16–36 OOS
  trades/coin, per-cell tuning is not statistically supportable — the honest result is
  "personalize BTC only; keep the other three on base and gather live data."

## Recommendation
1. **BTC: adopt 4H regime engine + short-down 3R** (the one IS+OOS+null-supported change),
   but confirm live (n≈30). Everything else BTC = base.
2. **ETH/SOL/XRP: keep base** (15m engine, all cells 2R). No personalization is
   statistically supportable on 47d; SOL/XRP's positive base numbers are bear-beta, not
   regime edge (fail null). ETH base is negative OOS — watch it.
3. **The real bottleneck is sample.** Per the operator's plan to build the dataset live,
   revisit per-coin personalization once each coin has a few hundred trades — then the
   per-cell R:R sweep (where 3R-for-short-down keeps recurring directionally) can be
   adopted with confidence rather than provisionally.

*GROSS throughout (operator adds fees). Single bear-ish regime (~8mo incl. bounces); the
short-side edges are partly bear-beta and will need re-checking on a regime flip.*
