# NBM-σ Calibration Measurement (Item 2.2 read-only)

**Date:** 2026-05-25
**Source corpus:** 668,952 NBM probabilistic observations from
`scripts/backfill_nbm_historical.py` run (AWS S3 noaa-nbm-grib2-pds,
2021-01-15 → 2026-05-25, 1,956 distinct cycle dates × 19 stations × 9
forecast days × 2 kinds).
**Residuals joined to IEM CLI actuals:** 1,362,895 rows total in
`weather_forecast_residuals`; 654,192 of those are `forecast_source='nbm_p50'`
joinable rows after filtering `logic_era != 'pre_station_fix'`, `temp_sigma_f > 0`.

**Scope:** READ-ONLY measurement. No consumption wiring into
`_weather_math.py`. Per Tier 1 plan: NBM-σ substitution stays gated
until observation week closes AND anomaly #2 confirms on independent
station-dates.

---

## Headline

| | empirical | theoretical | ratio |
|---|---|---|---|
| All-season \|z\|<1 | 58.02% | 68.27% | **0.85×** |
| All-season \|z\|≥2 | **11.38%** | 4.55% | **2.50×** |
| All-season \|z\|≥3 | **3.42%** | 0.27% | **12.66×** |
| All-season mean(z) | -0.057 | 0 | ≈0 |
| All-season std(z) | **1.322** | 1 | NBM σ is ~24% too narrow |

**Compared to autopsy's heuristic-σ result (|z|≥2 at 3.10× theoretical
on the 75-row autopsy sample): NBM σ improves the |z|≥2 fat-tail ratio
from 3.10× → 2.50×. The improvement is real but partial. NBM σ does NOT
fully close the tail-fat gap on its own.** The |z|≥3 tail is severely
under-priced at 12.66× theoretical.

**Caveat on the comparison:** the autopsy's 3.10× was computed on 75
single-day rows; this NBM result is over 654K rows across 5.5 years.
For a truly apples-to-apples comparison, the heuristic-σ |z|
distribution should be recomputed on the same 654K-row population. The
20% improvement reported here is the cleanest available pre-vs-post
contrast given the two different sample shapes.

---

## Per-season breakdown

z = (cli_actual − nbm_p50) / nbm_sigma_f, joined per (station, cycle, valid, kind).

### Winter (n = 165,078)

mean(z) = +0.016 (no significant bias)   std(z) = 1.364

| band | empirical | theoretical | ratio |
|---|---|---|---|
| \|z\|<1 | 57.85% | 68.27% | 0.85× |
| 1≤\|z\|<2 | 30.77% | 27.18% | 1.13× |
| 2≤\|z\|<3 | 8.01% | 4.28% | 1.87× |
| \|z\|≥3 | 3.37% | 0.27% | **12.49×** |

### Spring (n = 182,376)

mean(z) = **-0.222** (notable cold bias — actuals run ~0.22σ cooler than NBM median)
std(z) = 1.306

| band | empirical | theoretical | ratio |
|---|---|---|---|
| \|z\|<1 | 59.30% | 68.27% | 0.87× |
| 1≤\|z\|<2 | 29.78% | 27.18% | 1.10× |
| 2≤\|z\|<3 | 7.45% | 4.28% | 1.74× |
| \|z\|≥3 | 3.47% | 0.27% | **12.86×** |

### Summer (n = 153,486)

mean(z) = -0.119 (mild cold bias)   std(z) = 1.222

| band | empirical | theoretical | ratio |
|---|---|---|---|
| \|z\|<1 | 57.73% | 68.27% | 0.85× |
| 1≤\|z\|<2 | 30.78% | 27.18% | 1.13× |
| 2≤\|z\|<3 | 8.18% | 4.28% | 1.91× |
| \|z\|≥3 | 3.31% | 0.27% | **12.25×** |

### Fall (n = 153,252)

mean(z) = +0.121 (mild warm bias)   std(z) = 1.363

| band | empirical | theoretical | ratio |
|---|---|---|---|
| \|z\|<1 | 56.98% | 68.27% | 0.83× |
| 1≤\|z\|<2 | 31.23% | 27.18% | 1.15× |
| 2≤\|z\|<3 | 8.28% | 4.28% | 1.93× |
| \|z\|≥3 | 3.51% | 0.27% | **13.02×** |

---

## What the measurement says

1. **NBM σ helps materially on |z|≥2 but doesn't close the gap.** Fat-tail
   over-representation drops from 3.10× (heuristic) to 2.50× (NBM).
   That's ~20% improvement, not the ~70% required to hit ≤1.5× — the
   loose "calibration acceptable" threshold the existing forecast-quality
   plan (Item 2.2) sketched for `sigma_for_city_horizon` substitution.

2. **|z|≥3 is severely under-priced everywhere, every season.** 12-13×
   theoretical across all four seasons. This is the "black swan tail"
   problem: NBM's published σ assumes the residual distribution is
   approximately Gaussian, but the actual residuals have substantially
   heavier tails than N(0,1) predicts. Per-station-date weather is
   non-Gaussian.

3. **Center of distribution is too wide too:** |z|<1 is 0.85× theoretical
   across the board — fewer rows in the central band than N(0,1) predicts,
   which is consistent with the fat tails (mass redistributed to the
   wings). std(z) = 1.322 says NBM σ is about 24% too narrow on a
   global scale.

4. **Seasonal biases exist but are small:**
   - Spring: -0.222 mean(z) — actuals run cooler than NBM median in spring
   - Fall: +0.121 — actuals warmer than NBM median in fall
   - Winter/summer: near-zero bias
   The spring bias is the largest at ~0.22σ. Substantively this is
   "NBM is on average a bit warm in spring forecasts at these 19
   stations." Could be addressed by a per-season mean-residual
   correction on top of NBM σ.

5. **All 19 × 4 partitions are ≥20-sample sufficient** — coverage no
   longer a constraint for Item 2.2's pass/fail gate.

---

## Implications for the anomaly-#2 fix scheduling

The 2026-05-25 Tier 1 plan promoted NBM-σ substitution to the
**primary** anomaly-#2 candidate after C3 rounding-flip was ruled out
(0/4 events). This measurement says:

- **NBM σ substitution would help.** Improvement from 3.10× → 2.50× at
  |z|≥2 is meaningful and would partly compensate for the autopsy-era
  fat-tail problem. Worth doing.
- **NBM σ alone is NOT a complete fix.** The 2.50× residual fat-tail
  ratio is still well above the ≤1.5× target. To close the rest of the
  gap, additional treatment is needed:
  - **Per-station residual-correction layer on top of NBM** (Item 2.2
    part 2 in the existing plan — the `sigma_for_city_horizon` lookup
    using measured per-(station, source, horizon, season) residual
    stdevs). The 12.66× |z|≥3 ratio suggests this is necessary.
  - **OR a non-Gaussian model** that uses the NBM decile vector
    P10/P20/P50/P70/P90 directly instead of substituting σ into a
    Gaussian. The schema captures all 5 percentiles + mean + σ for this
    option (Tier 1 plan open question 5).
- **Reprioritization (no change, just confirmed):** boundary treatments
  (C3 rounding, Item 2.1 σ-widening) still rejected — even with full
  cross-season measurement, the fat tails are not boundary-localized;
  they're systemic.

**Recommended next step (when Board says go, not now):** measure the
heuristic-σ |z| distribution on the same 654K-row population for a true
apples-to-apples baseline. Then evaluate residual-corrected NBM σ:
take NBM σ × (residual_stdev / nbm_sigma_mean) per (station, season,
horizon) partition, and re-run the |z| measurement. If that closes
the gap to ≤1.5×, the gated consumption path is to substitute
residual-corrected NBM σ into `_weather_math.py:165`, not raw NBM σ
alone.

**No consumption wiring in this commit.** Measurement only.

---

## Coverage summary (calibration sufficiency)

All 19 stations × 4 seasons × 5+ years ≥20 samples per partition.
KMSY has shorter history (starts spring 2022 for IEM CLI side) —
~3.5yr vs 5.5yr for the other 18 stations. KMSY's winter 2021 = 0
samples (no IEM CLI), winter 2022 = 1,116 (partial), then full.

NBM observations source (`weather_nbm_observations`): 668,952 rows,
100% tagged `ingest_mode='historical_backfill'`,
`nbm_source='nomads_s3_archive'`, `icao_source='registry_yaml'`. Note:
the 342 original `live_cron` rows from today's 13z cycle were
UPSERT-overwritten by the backfill (same data, just relabeled);
live cron will re-tag back when the poller deploys.

Residuals (`weather_forecast_residuals`): 1,362,895 rows total across
all sources (`nws_blend` + `nbm_p50` + `nbm_mean`). NBM-source
`logic_era != 'pre_station_fix'` filter applies to the 654,192 rows
used in the |z| measurement above.
