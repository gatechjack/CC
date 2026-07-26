# Kalshi Market-Universe Survey (bottom-up, read-only)

**Date:** 2026-07-26 · **Scope:** shallow all-category efficiency survey to decide WHERE to look next. No build/orders/config. 504 live Karen-read-only calls, ~5 min, quiet window (copy idle).
**Bottom line:** **No category clears the fee vig with statistical confidence.** The categories with enough unbiased settled data (Economics, Financials, Weather, Crypto, Sports-moneyline) are calibrated within CIs. The one exploit-flag (Sports 0.9–1.0) is a **measurement artifact** that sign-contradicts the clean moneyline study. Long-horizon categories (Politics, Elections) are **unmeasured** (few recent settlements) — the only genuine blind spot. Cross-market arb: **none credible**. **Confirms the "Kalshi broadly efficient" prior; no build warranted.**

---

## STEP 5 — Honest ranking (lead)

| category | measurable? | CI-clearing exploitable edge | assessment |
|---|---|---|---|
| Sports | yes (n=387) | flag: 0.9–1.0 bucket −16pp *(artifact)* | **not real** — heterogeneous market mix + coarse lead; contradicts clean moneyline study (which found +2pp, statistically zero) |
| Economics | thin (per-bucket) | **none** | calibrated within CIs |
| Financials | thin | **none** | calibrated within CIs |
| Climate & Weather | yes | **none** | calibrated within CIs |
| Crypto | yes | **none** | calibrated within CIs (very tight spreads) |
| Politics, Elections | **NO** (few settled) | unmeasured | long-horizon; markets resolve on future dates — blind spot |
| Science/Tech, Companies, World, Health, Culture | NO | unmeasured/absent | insufficient settled sample or category empty |

**Verdict: real-and-untapped = none found. Real-but-already-harvested / efficient = Economics, Financials, Weather, Crypto, Sports-moneyline. Marginal/noise = the Sports 0.9–1.0 flag. Unmeasured = Politics/Elections.**

---

## STEP 1 — Catalog (from OPEN sample; per-category)

| category | series (total) | open sampled | med volume | med spread | settled sampled |
|---|---|---|---|---|---|
| Politics | 2,120 | 14 | 5,444 | 0.06 | 4 |
| Elections | 1,523 | 38 | 659 | 0.05 | 8 |
| Economics | 624 | 61 | 323 | 0.11 | 70 |
| Financials | 763 | 98 | 45 | 0.02 | 80 |
| Climate & Weather | 291 | 60 | 465 | 0.01 | 202 |
| Crypto | 270 | 94 | 200 | 0.013 | 191 |
| Science & Technology | 358 | 71 | 84 | 0.01 | 21 |
| Companies | 319 | 129 | 525 | 0.06 | 7 |
| Sports | 3,005 | 60 | 0* | 0.99* | 390 |
| World | 149 | 0 | — | — | 0 |
| Health | 98 | 0 | — | — | 0 |
| Culture | 0 | — | — | — | — |

- **Spreads are generally tight** (0.01–0.11) → consistent with efficient, competitive books. Crypto/Weather/Sci-Tech tightest (0.01).
- **Highest volume:** Politics (5,444), Companies (525), Weather (465), Economics (323).
- \*Sports OPEN sample is polluted by dead `KXMVE*` parlay markets (spread 0.99 / vol 0 = one-sided books) — not representative of real moneylines (which the settlement study showed are deeply liquid). Caveat.
- **`liquidity_dollars` = 0 across the board** — field not populated via this path; **book depth is NOT measurable** without live orderbook (same limitation as the settlement study).
- **Long-horizon categories (Politics/Elections) have almost no recent settlements** — their markets resolve on future dates, so recent-settled calibration can't see them.

---

## STEP 2 — Calibration ranking (THE lead deliverable)

Price = candlestick close **24h before settlement** (fallback: earliest candle if market <24h old) vs realized outcome, bucketed, with **Wilson 95% CI**. `EXPLOIT` = CI excludes the price by more than the per-price fee vig (`0.07·P·(1−P)`).

| category | bucket | n | mean price | realized | Wilson [lo, hi] | vig pp | edge pp | EXPLOIT |
|---|---|---|---|---|---|---|---|---|
| Economics | 0.9–1.0 | 22 | 0.969 | 1.000 | [0.851, 1.000] | 0.21 | +3.14 | – |
| Financials | 0.0–0.1 | 33 | 0.047 | 0.030 | [0.005, 0.153] | 0.32 | −1.70 | – |
| Weather | 0.0–0.1 | 90 | 0.027 | 0.011 | [0.002, 0.060] | 0.19 | −1.61 | – |
| Weather | 0.1–0.3 | 41 | 0.188 | 0.220 | [0.120, 0.367] | 1.07 | +3.20 | – |
| Weather | 0.3–0.5 | 33 | 0.375 | 0.364 | [0.222, 0.534] | 1.64 | −1.09 | – |
| Crypto | 0.0–0.1 | 51 | 0.006 | 0.000 | [0.000, 0.070] | 0.04 | −0.56 | – |
| Crypto | 0.9–1.0 | 44 | 0.996 | 1.000 | [0.920, 1.000] | 0.03 | +0.42 | – |
| Sports | 0.0–0.1 | 70 | 0.044 | 0.071 | [0.031, 0.157] | 0.30 | +2.70 | – |
| Sports | 0.1–0.3 | 84 | 0.183 | 0.131 | [0.075, 0.219] | 1.05 | −5.20 | – |
| Sports | 0.3–0.5 | 70 | 0.388 | 0.357 | [0.255, 0.474] | 1.66 | −3.04 | – |
| Sports | 0.5–0.7 | 70 | 0.590 | 0.614 | [0.497, 0.720] | 1.69 | +2.46 | – |
| Sports | 0.7–0.9 | 62 | 0.803 | 0.694 | [0.570, 0.794] | 1.11 | −10.90 | – |
| **Sports** | **0.9–1.0** | **31** | **0.936** | **0.774** | **[0.602, 0.886]** | 0.42 | **−16.19** | **YES** |

**The single EXPLOIT flag (Sports 0.9–1.0) is a measurement artifact, not an edge:**
- The Sports sample here = 20 series sampled from `get_all_series("Sports")` = a **heterogeneous mix** of props, futures, series markets, tennis, etc. — NOT clean game moneylines.
- The 24h-lead with earliest-candle fallback gives an **inconsistent price snapshot** across same-day (→ near-open) vs multi-day (→ near-final) markets.
- It **sign-contradicts** the clean moneyline study (which found favorites *underpriced* by +2pp at T-1h, statistically zero). n=31.
- Conclusion: artifact of coarse survey methodology on a mixed market population, not a tradeable "short favorites" edge. **Not deepened (operator's call).**

Every other bucket in every measurable category is **calibrated within its CI** / does not clear the vig. Insufficient settled sample (<30) to calibrate: Politics (2), Elections (8), Science/Tech (17), Companies (7), World (0), Health (0), Culture (0).

---

## STEP 3 — Structural (cross-market arb)

Computed sum-of-`yes_ask` per event from the OPEN sample; **top candidates verified by full-event re-pull.** Both refuted:

| event | sample | full-event | verdict |
|---|---|---|---|
| `KXNASDAQ100-26JUL27H1600` | 10 legs, sum 0.18 | **30 legs, sum 1.390** | **refuted** — partial sample was misleading; full set sums >$1 (normal, with vig) |
| `KXETHMINMON-ETH-26JUL31` | 6 legs, sum 0.070 | 6 legs, sum 0.070 | **false positive** — these are **non-mutually-exclusive** nested thresholds (e.g. "min > X" for 6 X's), not an exhaustive outcome set; summing them is meaningless. A real 93% arb would be instantly taken; the 1¢ asks are stale/illiquid one-sided books |

**No credible cross-market arbitrage found.** The partial-event caveat (shallow sample captures incomplete outcome sets) is real — the verification step was necessary and correctly killed both candidates.

---

## STEP 6 — Which categories warrant a deeper pull?

**On this evidence: none warrant a deeper pull for an edge.** The measurable categories are efficient; the one flag is an artifact.

The only genuine **blind spot** is **long-horizon Politics & Elections** (few recent settlements → uncalibrated here). *If* an untapped retail edge exists anywhere on Kalshi, immature/long-horizon political markets are the a-priori-likeliest home. Measuring them needs a **different method** than recent-settled calibration:
- Pull settled markets from **past** election/political cycles (older `status=SETTLED` series) for a historical calibration, **or**
- **Forward-track** current open political markets (snapshot price now, compare at resolution) — a weeks-long paper exercise.

Given (a) efficiency confirmed elsewhere, (b) every Kalshi forward strategy is net-negative, and (c) the vig bar, **my recommendation is not to pursue** — but the Politics/Elections blind spot is the operator's to weigh.

### If pursued — minimal validation shapes (NOT builds)
- **Politics/Elections calibration:** pull ~1,000 settled markets from prior cycles, 24h-lead price vs outcome, per-bucket Wilson CIs; candidate only if a bucket's CI clears the vig. Kill check: does the CI clear zero-edge net of vig? (Same trap-guard as the settlement study.)
- **Cross-market arb (if ever):** systematic full-event pulls restricted to **verified mutually-exclusive-exhaustive** events (temperature brackets, Fed-decision brackets), check `sum(yes_ask) < 1 − fees` with real orderbook depth. Kill check: is there fillable size at the quoted asks, or are they stale 1¢ one-sided books (as `KXETHMINMON` was)?

---

## Data caveats (same as settlement study + survey-specific)

1. **No historical orderbook** → book depth / fillable size **not measured** (`liquidity_dollars`=0); fills would be tape-approximated. The efficiency finding is about *price calibration*, not proof that any (non-existent) edge would be fillable.
2. **`count_fp` fractional** → volume magnitudes are soft (affects STEP 1 volume figures, not the calibration verdict).
3. **24h-lead calibration is coarse** and, for same-day markets, falls back to the earliest candle (near-open price) — heterogeneous across market types. The Sports 0.9–1.0 artifact is a direct symptom. A clean calibration needs per-market-type, per-lead consistency.
4. **Shallow sample** (20 series/category; per-bucket n = 20–90) → wide CIs → the survey can only detect **large** mis-calibrations. "No edge found" = "no *large* mis-calibration; sub-vig effects remain possible but are, by definition, not exploitable."
5. **Politics/Elections/Companies/Sci-Tech/World/Health uncalibrated** (insufficient recent settlements).

**Net:** the survey confirms Kalshi is broadly efficient for the retail-accessible, measurable categories, with a real blind spot only in long-horizon political markets. Consistent with the venue-level prior (five losing strategies). No edge clears the vig with statistical confidence.
