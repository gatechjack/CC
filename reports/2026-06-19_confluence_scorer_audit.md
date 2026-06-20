# Confluence Scorer Audit — Real Confluence or Correlated Stacking?

**Date:** 2026-06-19   **Type:** read-only analysis. No code, no config, no deploy.
**Scorer:** `trading_corp/agents/strategies/bitunix_confluence.py` (Phase 3.2 accumulator).
**Config:** `config/strategies.yaml` → `bitunix_futures.scoring` (live: `enabled: true`).
**Corpus:** `btc_scalping.db` `bars_3m`, 38,899 bars (3m, Mar 30 – Jun 19 2026).

## Headline verdict

> **The signals are NOT correlated-stacking. At the firing level the scorer's 15 measurable
> signals represent ~12.9 of 15 independent dimensions (86%); no pair reaches the redundancy
> threshold (max within-side phi = 0.44, threshold 0.70). The "thin confluence wearing a thick
> costume" hypothesis is REFUTED at the correlation level — show the numbers below.**
>
> **The real confluence weakness is ROLE, not correlation:** ~79% of all scoring weight is a single
> NNFX role — CONFIRMATION (momentum). The score carries **no independent baseline and no
> volume/volatility filter**; those roles exist only as *external gates* (HTF-regime + `pa_validation`),
> not as votes in the score. So the score is many *timing-independent* looks at **one question**
> ("is momentum turning now?"). Diverse in signal, monocultural in role.

---

## 1. ROLE COVERAGE

The scorer sums per-side weights of live alerts. Critically, the live config **disables** the
non-momentum inputs from the score:
- `pa_factors_in_score: false` → VWAP, HH/LL structure, volume **do not score** (they became binary
  validators in `pa_validation`).
- `guards_in_score: false` → sell-on-rush / buy-on-fall **do not score** (binary hard-rejects in
  `pa_validation`).
- `score_timeframes: ["3m","15m","30m"]` → Cypher 4H/1D fires score 0 (HTF-regime block is the
  directional authority instead).

So the **score** is built from **29 directional signal factors**. Classified into NNFX roles:

| Role | Count | Σ weight | % of weight | Factors |
|---|---|---|---|---|
| **CONFIRMATION** (momentum/entry) | 21 | 58 | **~79%** | Cypher-A diamonds (red/blood/redx), yellow_x; all 7 Cypher-B (gold_buy, buy/sell_circle, ±_div, ±_dot); Otter (otter_buy/sell, money_bag, water×4, spoon) |
| BASELINE (trend direction) | 4 | 9 | ~12% | mc_a_bluetriangle, mc_a_longema, bias_bull, bias_bear |
| VOLUME/flow (used directionally) | 2 | 4 | ~5% | cvd_bull_flip, cvd_bear_flip (CVD-derived, but scored as direction, not a filter) |
| STRUCTURE/level | 2 | 2 | ~3% | pink_box_bull, pink_box_bear |
| **VOLUME/VOLATILITY filter (independent)** | **0** | **0** | **0%** | — none in the score |

**Finding:** the score clusters hard in one role. ~79% of scoring weight is confirmation-momentum;
there is **no independent volume/volatility filter** and only a thin (~12%), low-weight baseline. The
orthogonal roles a real confluence wants — a trend baseline and a volume/volatility gate — are present
in the *system* but **as gates outside the score** (HTF-regime classifier = baseline; `pa_validation`
`volume_confirmation` + `structure_alignment` + `vwap_alignment` = volume/structure validators).

That architecture is **defensible** (NNFX itself keeps baseline & volume as separate gates, not
confluence votes). But it means the *score's* "confluence" is confirmation-only: it measures conviction
of one momentum read from many angles, not agreement across independent roles.

## 2. SIGNAL CORRELATION

Measured 15 of the 29 scoring factors that map cleanly to `bars_3m` columns (covers all of Cypher-A,
the core of Cypher-B, Otter core, and CVD). Firing = `col not null and ≠ 0`. Correlation computed on
the **scorer's actual view — "live within TTL"** (signal active if fired within its ttl_bars: Cypher-A
& CVD = 10 bars/30 min, Cypher-B & Otter = 5 bars/15 min), via the phi coefficient (base-rate
corrected). 14 factors are **unmeasured** (no corpus column): mc_b_±_dot, money_bag×2, water×4,
spoon×2, **bias_bull/bear**, pink_box×2.

**Base rates (sparse):** buy signals fire 0.10–2.65% of bars; sell 0.29–9.88% (red_diamond is the
outlier at 9.9%). Live-coverage ranges 0.5–78% (red_diamond is "live" 78% of the time given its 10-bar
TTL — a near-permanent bear nudge).

**Within-side correlation — ranked, live basis:**

| pair | phi (live) | Jaccard | exact-bar phi | flag |
|---|---|---|---|---|
| mc_a_blood_diamond ~ mc_a_redx (sell) | **+0.44** | 0.29 | +0.52 | notable |
| mc_b_buycirc_div ~ mc_b_buy_circle (buy) | +0.33 | 0.20 | +0.22 | — |
| mc_b_gold_buy ~ mc_b_buycirc_div (buy) | +0.32 | 0.11 | +0.32 | — |
| mc_b_sellcirc_div ~ mc_b_sell_circle (sell) | +0.26 | 0.17 | +0.19 | — |
| mc_a_red_diamond ~ mc_a_blood_diamond (sell) | +0.19 | 0.15 | +0.33 | — |
| mc_a_red_diamond ~ mc_a_redx (sell) | +0.13 | 0.39 | +0.13 | — |
| *(all other within-side pairs)* | ≤ ±0.17 | ≤0.16 | — | — |

- **No pair reaches the 0.70 redundancy threshold.** The single highest within-side live correlation
  is **0.44** (blood_diamond ~ redx — both Cypher-A sell markers off the same WaveTrend engine).
- The mild clusters are exactly where you'd expect: **intra-panel** Cypher-A sell trio and the
  Cypher-B div+circle relatives. Even those fire on largely *different* bars (Jaccard 0.11–0.39).
- Cross-side note: cvd_bull_flip ~ cvd_bear_flip = +0.33 — the two CVD flips cluster in the same
  *volatile* windows but point opposite directions (not redundancy; they correctly oppose).

**Buy-side §5 question (are buy signals all proxies for the same short-term-weakness read?):**
**No — refuted at the firing level.** The 8 buy signals carry **~7.2 independent dimensions (90%)**;
the strongest buy pair is gold_buy~buycirc_div at 0.32. They fire on genuinely different bars.

## 3. WEIGHT vs INDEPENDENCE

Because correlations are low, **weight is not being meaningfully assigned to redundant signals.** The
only place double-counting exists at all is the Cypher-A sell cluster (red_diamond w4 + blood_diamond
w5 + redx w2 = 11 sell-weight, partial phi 0.13–0.44) and the Cypher-B div/circle pairs (~0.26–0.33).
Treating blood_diamond+redx (phi 0.44) as ~1.6 effective signals instead of 2 over-counts ≈1.4 weight
points on the sell side — immaterial against 23 sell-weight. **No inflated-confluence problem from
correlation.**

## Effective independent dimensions (participation ratio, PR = N² / Σ phiᵢⱼ²)

| block | signals | independent dims | % of nominal |
|---|---|---|---|
| BUY | 8 | **~7.2** | 90% |
| SELL | 7 | **~6.2** | 89% |
| ALL | 15 | **~12.9** | 86% |

## Plain verdict

**The scorer's 15 measured signals represent ~13 independent dimensions of *firing* information — it
does NOT stack correlated signals counted as independent.** The classic confluence failure mode
(redundant signals double-counted) is **absent** on this corpus.

**The genuine weakness is role monoculture in the score:** ~79% of scoring weight is one role
(confirmation/momentum), with zero independent volume/volatility filter and only a thin baseline *in
the score*. The score answers one question — momentum direction — from many timing-independent angles.
Real role diversity (baseline, volume) exists only as external gates. Whether that is "real
confluence" is a definitional call: it is **multi-angle momentum confluence**, not **multi-role**
confluence.

### Caveats (load-bearing — don't over-read the green result)
1. **Firing-independence ≠ edge-independence.** Signals can fire on different bars yet *win/lose
   together* — all momentum reads share a common failure mode (chop / mean-reversion regimes). This
   audit shows timing orthogonality, **not** PnL orthogonality. It is fully consistent with the
   regime-dependent nulls we keep finding (everything fails in bear/chop) — a confirmation-only score
   can read "high confluence" precisely when many momentum signals agree in a chop, i.e. the trap.
2. **Coverage 15/29.** Unmeasured: the Otter sub-signals (water/spoon/money_bag), pink_box, and
   crucially **bias_bull/bias_bear** — the main baseline-role factors. bias is likely CVD/trend-derived
   and *may* correlate with cvd_flip; that pair could not be tested here.
3. **Repaint-suspect inputs scored as independent:** mc_b_buycirc_div / sellcirc_div are divergence
   markers; prior work ([[bitunix-otter-strategy-discovery]]) showed divergence signals are
   repaint artifacts that collapse at entry-delay. They contribute weight 4 each.
4. **Stale factor:** `mc_a_yellow_x` is still scored `side: buy, weight: 2`, despite the P2 finding
   ([[bitunix-p2-classifier-signbug]]) that yellow_x is non-directional (declassification was
   deferred). It is contributing directional buy weight it shouldn't.

### Method note (reusable)
For "is this real confluence?": (a) classify every *scoring* factor into NNFX roles and check the
weight isn't monocultural; (b) correlate signals as the scorer sees them — **live-within-TTL**, not
same-bar — because TTL accumulation is what stacks them; (c) report participation ratio N²/Σphi² for
effective dimensions; (d) remember firing-orthogonality is necessary but not sufficient — orthogonal
*edge* needs a conditional-outcome test, not just a firing correlation.

---

# Part 2 — Conditional-Outcome Test (answers caveat #1: edge vs conviction)

**Question:** firing-independence is established (Part 2 above). Does multi-signal *agreement* improve
**edge** (net-R), or only **conviction** (tighter win clustering at the same expectancy)? If the 79%
momentum monoculture is real confluence, deeper agreement → higher net-R. If it's the monoculture
trap, win-rate may rise while net-R doesn't — or worse.

**Method (read-only, corpus-only, same discipline as the range-fade studies):** firing derived from
`bars_3m` columns (15 mapped scoring signals). At each bar, **confluence depth** = count of same-side
signals live-within-TTL; the dominant side (depth ≥2, beating the other side) opens a trade subject to
the standard gates — stop = max(1.5×ATR, 0.3% floor), TP = 2R single-target, 30-min (10-bar) per-side
cooldown, 24h max hold — walked forward on 3m bars, **fee-net** (corrected model). Bucketed by entry
depth (2/3/4/5+). Run WITH and WITHOUT the repaint-suspect divergence markers
(`buycirc_div`/`sellcirc_div`). Regime-split by ER (chop ≤0.35 / trend). Walk-forward TRAIN≤May15 /
VAL≤Jun1. Corpus: 38,899 bars, **81% chop / 19% trend** (the usual bear/quiet tape).

## Bucket tables — net-R by agreement depth

**WITH divergence markers** (3,342 entries):

| depth | n | win% | **net-R** |
|---|---|---|---|
| 2 | 2249 | 34.3 | **−0.181** |
| 3 | 937 | 32.8 | **−0.228** |
| 4 | 140 | 28.6 | **−0.350** |
| 5+ | 16 | 37.5 | −0.083 *(N=16, noise)* |

**WITHOUT divergence** (3,160 entries) — the clean read:

| depth | n | win% | **net-R** |
|---|---|---|---|
| 2 | 2273 | 34.6 | **−0.172** |
| 3 | 788 | 31.2 | **−0.276** |
| 4 | 95 | 29.5 | **−0.317** |
| 5+ | 4 | 25.0 | −0.473 *(N=4)* |

→ **Monotonically *negative*.** More agreement → *worse* net-R, and **win-rate falls** (34.6 → 31.2 →
29.5%), not rises. This is not even the "conviction-not-edge" case (which needs win-rate up, net flat)
— it's worse: extra confirmations add cost without adding hit-rate.

## Regime split (WITH div) — depth helps in NEITHER regime

| regime | d2 | d3 | d4 | d5+ |
|---|---|---|---|---|
| CHOP (81%) | −0.186 | −0.248 | −0.371 | +0.079 *(N=14)* |
| TREND (19%) | −0.162 | −0.126 | −0.208 | −1.217 *(N=2)* |

→ Caveat #1's *specific* prediction ("depth helps in trend, fails in chop") is **not** supported —
depth produces no rising net-R slope in *either* regime. Trend buckets are a touch less negative at d2
(a level effect, not a depth effect). The only positive cell anywhere (chop d5+ = +0.079) is N=14 and
**vanishes once divergence is dropped** — i.e. the sole hint of high-depth "edge" was carried by the
repaint-suspect signals. Dismissed.

## Walk-forward (net-R) — robust across train/val

| split | d2 | d3 | d4 |
|---|---|---|---|
| TRAIN ≤ May15 (WITH div) | −0.266 | −0.373 | −0.501 |
| VAL ≤ Jun1 (WITH div) | −0.106 | −0.015 | −0.152 |

→ VAL is less negative than TRAIN overall (recent tape), but in **neither** split does net-R rise with
depth. The no-depth-edge result holds out-of-sample.

## Plain verdict (Part 2)

> **Agreement depth does NOT improve net-R — it mildly degrades it.** Once the repaint-suspect
> divergence markers are removed, the depth→net-R slope is monotonically negative (−0.17 → −0.28 →
> −0.32), and win-rate falls with depth rather than rising. This is regime-robust (holds in chop and
> trend, train and val) within this corpus.
>
> **This answers caveat #1: the 79% momentum monoculture buys conviction-sizing, not expectancy.**
> Stacking more confirmation signals adds fee/entry cost without adding edge — consistent with the
> mechanistic read that high-depth bars cluster in volatile chop (more signals fire when price whips),
> i.e. the *worst* entries, not the best. The Part-1 firing-independence (≈13 dimensions) is real but
> **does not translate into edge-independence** — exactly the gap caveat #1 flagged.

**Framing (unchanged):** MECHANICS test, regime-bounded. Every bucket is net-negative because this is
the same bear/quiet tape where all strategies null; the *novel, regime-robust* finding is the **slope**
(depth ≠ edge), not the levels. It does not prove "confluence can never pay" in a regime we haven't
sampled — but it removes the "more agreement = better" assumption the score's architecture relies on.
Read-only; no config, no deploy.

---

# Part 3 — Weighted net_score Test (steelman: does the bot's actual output track edge?)

**Question:** depth (raw count) failed. But the bot trades on the **weighted `net_score`**, not the
count. Steelman: does `net_score` track net-R better than raw depth did? **Same trade universe + gates
as Part 2** (dominant side, depth ≥2, 10-bar cooldown, 1.5×ATR/2R, fee-net) — only the *bucketing*
changes (net_score bins, tier-aligned: weak 3 / standard 5 / premium 10).

**Cross-check first (is score just depth re-expressed?):** depth ↔ net_score Pearson = **+0.66**
(with div) / **+0.62** (without). Moderate — so the weighting *does* genuinely re-rank entries, it is
**not** merely depth in different units. That makes the result below a real test of the weights, not a
tautology.

**Net-R by net_score (WITHOUT divergence — clean read):**

| net_score bin | n | win% | net-R |
|---|---|---|---|
| <5 (below fire) | 1275 | 35.2 | **−0.153** |
| 5–7 (standard) | 1324 | 33.7 | **−0.201** |
| 8–10 (std/prem) | 293 | 29.0 | **−0.335** |
| 11+ (premium) | 268 | 30.6 | **−0.300** |

→ **Higher score → worse net-R**, win-rate falls (35→29%). Same shape as depth, despite the
re-ranking. The lowest bin (`<5` — trades the bot would **SKIP**) is the *least* negative.

**Regime split (without div):** the relationship is **inverted in both** — `s<5` is least-negative in
trend (−0.048) and chop (−0.177); higher score is worse in each. The "higher score = better entry"
assumption isn't just absent, it's **backwards** on this tape.

**Walk-forward (without div) — the one nuance:**

| split | s<5 | s5–7 | s8–10 | s11+ |
|---|---|---|---|---|
| TRAIN ≤ May15 | −0.194 | −0.322 | −0.495 | **−0.460** |
| VAL ≤ Jun1 | −0.096 | −0.154 | −0.170 | **+0.130** *(N=56)* |

→ TRAIN: strong **negative** slope (higher score = much worse). VAL: a small **positive** at the top
bin (+0.130, N=56). **The slope flips sign between train and val** → not a stable edge; the only
non-negative high-score cell is a recent-window blip the train period directly contradicts (and N=56).
It is a *candidate to watch in a future window*, not evidence the weights work.

## Plain verdict (Part 3)

> **net_score does NOT track net-R — and does NOT improve on raw depth.** Full-sample and TRAIN: higher
> score → flat-to-*worse* net-R (inverted in both regimes). The weighting genuinely re-ranks entries
> (depth↔score r≈0.65) yet still fails to select better trades. The single positive cell (VAL premium
> bin, +0.130, N=56) flips sign versus TRAIN — unstable, candidate-not-verdict.
>
> **This is stronger than the depth result:** the scorer's **central output — the exact quantity the
> bot sizes and gates on — does not track expectancy on this corpus.** Mechanism is the same: score,
> like depth, rises when many signals fire, which happens in volatile chop = the worst entries.

**Net of all three parts:** the scorer is *well-built where people usually look* (signals are
firing-independent, ~13 dims, not double-counted) and *empty where it matters* (one role; neither
agreement-breadth nor weighted-score tracks edge here). Regime-bounded — doesn't condemn confluence in
an unsampled regime, but on the data we have, **more/higher confluence ≠ better outcome.** The lever is
not better weighting; it's an orthogonal *role* (a real baseline/volume edge) or a different regime.
Read-only; no config, no deploy.
