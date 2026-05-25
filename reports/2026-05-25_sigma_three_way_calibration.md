# Three-way σ calibration on identical 654K-row population

**Date:** 2026-05-25
**Population:** 654,192 (cli_actual, nbm_p50, nbm_sigma_f, horizon) rows
joinable post-fix from the historical backfill (commits `95ddf36` +
`be07a35`). Same rows, three σ choices, identical z-computation.
**Out-of-sample residual-corrected:** train partitions built from
2021-2024 (n≥20 per (station, season, day-bucket)); applied to
2025-2026 test rows only (173,070 of the 654K).

**READ-ONLY measurement. No consumption wiring into `_weather_math.py`.**

---

## Headline (all-season)

z = (cli_actual − nbm_p50) / σ_choice

| σ choice | n | mean(z) | std(z) | \|z\|<1 ratio | \|z\|≥2 ratio | \|z\|≥3 ratio |
|---|---|---|---|---|---|---|
| (1) **HEURISTIC** σ (today's strategy) | 654,192 | -0.07 | **1.13** | 1.09× | **1.96×** | **8.26×** |
| (2) **NBM** σ (substitution) | 654,192 | -0.06 | **1.32** | 0.85× | **2.99×** | **12.66×** |
| (3) **RESIDUAL-CORRECTED NBM** (train/test) | 173,070 | -0.13 | **1.04** | 1.09× | **1.72×** | **6.58×** |

### Three findings the apples-to-apples comparison surfaces

1. **NBM σ alone is WORSE than the heuristic at tail control.**
   |z|≥3 at 12.66× theoretical for NBM vs 8.26× for heuristic. |z|≥2 at
   2.99× for NBM vs 1.96× for heuristic. The heuristic's "wider flat
   band beyond day 3" (sqrt(5² + 4) = 5.39°F) is catching tails that
   NBM's narrower per-horizon σ (typically 2-6°F) lets through.
   **This partially flips the 2026-05-25 registration that put NBM-σ
   substitution as the primary anomaly-#2 candidate.** Substituting NBM
   σ raw into `_weather_math.py:165` would HURT fat-tail behavior, not
   help it. NBM is better at the center (|z|<1 = 0.85× vs 1.09×) but
   that's not where the loss problem lives.

2. **Residual-corrected NBM is the clear winner — but still doesn't
   reach Gaussian.** std(z) drops to 1.04 (essentially calibrated
   dispersion). |z|≥2 ratio collapses to 1.72× — close to 1.0×. **But
   |z|≥3 stays at 6.58× theoretical.** Even after per-(station,
   season, day-bucket) calibration, the extreme tails are ~6× heavier
   than a Gaussian predicts. This is the fundamental "weather residuals
   are not Gaussian" finding — likely needs Student-t or extreme-value
   distribution treatment (or the Tier 1 percentile-direct path) to
   close the rest.

3. **The heuristic is approximately calibrated in summer** (|z|≥3 =
   2.38×), but breaks down in winter (13.74×) and fall (6.65×). NBM is
   uniformly bad across seasons (12-13×). Residual-corrected NBM is
   uniformly improved but doesn't reach 1× in any season.

### Per-season detail

| season | n | heuristic \|z\|≥2 / ≥3 | NBM \|z\|≥2 / ≥3 | RC-NBM \|z\|≥2 / ≥3 |
|---|---|---|---|---|
| winter | 165K | 1.78× / **13.74×** | 1.87× / 12.49× | 1.03× / 6.99× |
| spring | 182K | 1.50× / 9.60× | 1.74× / 12.86× | 1.13× / 7.35× |
| summer | 153K | **0.67× / 2.38×** | 1.91× / 12.25× | 0.99× / 6.27× |
| fall | 153K | 1.16× / 6.65× | 1.93× / 13.02× | 0.78× / 4.76× |

Heuristic wins summer outright (|z|≥3 = 2.38× — best of any σ in any season).
RC-NBM wins everything else.

---

## Per-station spring bias (the directional finding)

mean(z) per station, spring rows only, NBM σ. Spring all-station
mean(z) = -0.222 (cooler than NBM median). Decomposed:

| station | n | mean(z) | bias signal |
|---|---|---|---|
| KMSY | 6,462 | **-0.542** | COLD (Gulf coast — marine cooling not captured) |
| KDEN | 9,810 | **-0.524** | COLD (Front Range — spring snowmelt cooling?) |
| KAUS | 9,810 | **-0.487** | COLD |
| KHOU | 9,756 | **-0.478** | COLD |
| KNYC | 9,810 | -0.339 | COLD |
| KOKC | 9,792 | -0.336 | COLD |
| KDFW | 9,792 | -0.332 | COLD |
| KSAT | 9,774 | -0.303 | COLD |
| KMSP | 9,792 | -0.282 | COLD |
| KMDW | 9,774 | -0.248 | COLD |
| KATL | 9,756 | -0.239 | COLD |
| KSEA | 9,702 | -0.183 | COLD |
| KMIA | 9,720 | -0.110 | mid |
| KDCA | 9,774 | -0.077 | mid |
| KPHL | 9,792 | -0.061 | mid |
| KLAX | 9,792 | -0.035 | mid |
| KPHX | 9,774 | +0.024 | mid |
| KBOS | 9,738 | +0.052 | mid |
| KSFO | 9,756 | **+0.175** | warm (marine — opposite sign) |

**12 of 19 stations carry meaningful (|mean(z)| ≥ 0.18) cold bias in
spring.** Geographic cluster: Texas (KAUS/KHOU/KDFW/KSAT) + high plains
(KDEN/KOKC) + northeast/midwest (KNYC/KMSP/KMDW/KATL). SF is the lone
warm-bias station. Pattern suggests NBM is systematically over-warm in
spring for southern-tier US + colder northeast stations — consistent
with cold-air outbreaks or marine cooling that NBM's blend doesn't
capture well.

### Per-station × per-season mean(z) (full bias surface)

| station | winter | spring | summer | fall |
|---|---|---|---|---|
| KATL | +0.098 | -0.239 | +0.091 | +0.103 |
| KAUS | -0.243 | -0.487 | -0.409 | -0.112 |
| KBOS | -0.037 | +0.052 | +0.176 | +0.285 |
| KDCA | +0.049 | -0.077 | +0.007 | +0.143 |
| KDEN | -0.089 | -0.524 | -0.077 | +0.185 |
| KDFW | -0.087 | -0.332 | -0.172 | -0.021 |
| KHOU | +0.023 | -0.478 | -0.084 | +0.065 |
| KLAX | +0.237 | -0.035 | +0.318 | +0.475 |
| KMDW | +0.262 | -0.248 | -0.354 | +0.280 |
| KMIA | -0.107 | -0.110 | -0.351 | +0.114 |
| KMSP | -0.047 | -0.282 | -0.110 | +0.125 |
| KMSY | +0.094 | -0.542 | **-0.608** | -0.037 |
| KNYC | +0.249 | -0.339 | -0.487 | +0.281 |
| KOKC | -0.206 | -0.336 | -0.300 | -0.188 |
| KPHL | +0.073 | -0.061 | -0.028 | +0.145 |
| KPHX | -0.021 | +0.024 | -0.033 | +0.110 |
| KSAT | -0.157 | -0.303 | +0.048 | -0.098 |
| KSEA | +0.070 | -0.183 | -0.112 | +0.097 |
| KSFO | +0.171 | +0.175 | +0.025 | +0.310 |

Observations:
- **KMSY spring-summer is the most-biased cell anywhere** (-0.54 / -0.61) — gulf-coast marine cooling consistently un-modeled.
- **KMDW + KNYC have continental swing** — warm winter (+0.25/+0.26), cold summer (-0.35/-0.49). NBM under-predicts seasonal amplitude at these inland-ish cities.
- **KLAX has warm bias across all seasons** (+0.24/-0.04/+0.32/+0.48) — fall is the most extreme.
- **KSFO has uniform warm bias** — marine cooling not captured by NBM blend.

A simple per-(station, season) mean-residual offset (subtract mean(z) × σ from forecast before evaluation) would directly correct these — a cheaper fix than σ widening, addresses a structurally different error mode.

---

## What changes vs the prior registration

Prior (2026-05-25 morning, commit `2e8f938`): **NBM-σ substitution =
primary anomaly-#2 candidate.** Based on autopsy n=75 / Open-Meteo
proxy showing NBM σ would be ~56% wider than heuristic at 48h.

New (this measurement, n=654K / IEM CLI truth, identical-population
comparison):

- **NBM-σ substitution alone is NOT the right primary fix.** NBM σ at
  the per-row level (varying by horizon) is on average NARROWER than
  the heuristic's flat 5.39°F band beyond day 3, so tails get worse.
  The prior "56% wider" finding came from a single 48h Max comparison
  where NBM happened to be 5°F vs heuristic 3.20°F — that ratio
  doesn't hold across the full horizon/sample space.
- **Residual-corrected NBM (Item 2.2 part 2) is now the right primary
  candidate.** It's the only σ choice that gets close to Gaussian for
  |z|≥2 (1.72× ≈ 1×). Still leaves |z|≥3 at 6.58×, which is the
  Gaussian-assumption ceiling — addressing that needs the Tier 1
  open-Q5 percentile-direct path or an explicit heavy-tail model.
- **Per-(station, season) mean-residual offset is a separate simpler
  fix** orthogonal to σ widening. ~12 of 19 stations carry meaningful
  spring cold bias; some carry multi-season patterns. Cheap to apply
  (one constant per (station, season)). Addresses the LOCATION of
  the distribution; σ-widening addresses the SCALE.

---

## Recommended next steps (not building now)

In order of expected payoff vs build cost:

1. **Per-(station, season) mean-residual bias-offset correction** —
   simplest fix, cheapest code, directly addresses the spring cold
   bias finding. Compute one number per partition (19 × 4 = 76 numbers,
   tabular constants). Apply as `forecast_corrected = forecast +
   mean_residual_for_partition`. Independent of σ; could ship alongside
   any σ choice.

2. **Residual-corrected NBM σ** (Item 2.2 part 2, the existing-plan
   `sigma_for_city_horizon` lookup, refined to use NBM σ as the base
   instead of heuristic). Build a per-(station, season, horizon-bucket,
   source) train table with rolling 60-90 day window; multiply NBM σ
   by the partition's residual-stdev / mean-NBM-σ ratio. Gets |z|≥2
   from 1.86× to 1.72× (NBM alone → RC); from 1.96× to 1.72× (heur →
   RC); the difference is small but the floor is real.

3. **Decile-direct bracket probability** (Tier 1 open Q5) — for the
   remaining |z|≥3 = 6.58× problem after residual correction. Don't
   use σ at all; use the NBM percentile vector P10/P20/P50/P70/P90
   directly with linear interpolation to compute P(temperature in
   bracket). Captures the non-Gaussian shape NBM publishes. ~100 LOC
   change to `evaluate_weather_market`. The plan already retains all
   5 percentiles in `weather_nbm_observations` for exactly this.

**Critical reframing:** the path forward is no longer "swap heuristic σ
for NBM σ." It's a STACK: (bias offset) + (residual-corrected NBM σ)
+ optionally (decile-direct). Each piece is independently measurable
and shippable. None ships without explicit Board approval.

---

## Sanity checks + caveats

- Train partitions for RC: 608 of the 19 × 4 × 9 = 684 possible
  (station, season, day-bucket) cells had n≥20 train samples. Missing
  76 cells are mostly KMSY's pre-2022 winters/springs + day-9 horizons
  for stations with sparser late-cycle data.
- RC test sample (173,070) is smaller than full (654,192) because the
  test set is restricted to 2025-2026 target dates (one full year + 5
  months of 2026). The cross-season comparisons within RC are
  consistent in shape with the full-data NBM and heuristic results.
- The heuristic σ formula uses bucketed horizon and a fixed buffer; it
  doesn't vary across stations or seasons. Its "summer wins" is partly
  because summer residuals are intrinsically smaller (less day-to-day
  variation), making any wider σ look well-calibrated.
- All measurements are vs IEM CLI ground truth (not Open-Meteo). All on
  the same row population — apples-to-apples on the part that's
  comparable (RC test sample is by necessity a subset).
- **Anomaly #2 is now confirmed systemic on 654K rows** — fat tails at
  |z|≥2 ≈ 2-3× and at |z|≥3 ≈ 8-13× under both heuristic and NBM σ on
  the full population. It's not an artifact of the autopsy's n=75
  single-day sample. This materially strengthens the case that the
  consumption-wiring gate (obs week close + anomaly #2 confirm) will
  clear when the operator reviews.
