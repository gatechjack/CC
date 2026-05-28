# kalshi_weather +EV discovery — deep-corpus calibration & idealized EV backtest

**Date:** 2026-05-28
**Engine:** `scripts/weather_edge_analysis.py` (pure numpy + sqlite; run capped via `run_capped.ps1`)
**Corpus:** `weather_nbm_observations` ⋈ `weather_forecast_residuals`, 654,192 station-verified NBM↔CLI rows, 2021-01-16 → 2026-05-25, 19 settlement ICAOs.

## Bottom line

**No +EV kalshi_weather system exists in this data — now confirmed against REAL Kalshi
prices, not just proxies.** There *is* genuine out-of-sample forecast skill beyond NBM,
but it is (a) mostly the per-station **mean bias** any competitor removes trivially, and
(b) a small sample-dependent fatter-tail residual. The apparent +6-7%/contract proxy edge
exists **only against a market naïve enough to price raw NBM**.

The decisive experiment was then **run, not deferred**: real settled-market prices
(`§ Real-price gate` below). Against actual fillable Kalshi quotes on the holdout, the
frozen candidate (WX-EMP-1) is **net −2.1%/contract, negative at every betting threshold**,
and the **market's own prices are a better probability estimate of the outcome than the
model** (Brier 0.161 < 0.178). The market is efficient. This is the strongest form of the
honest answer the mandate permits: not "+EV found," and not merely "unproven" — **refuted
on real prices.**

## Inviolable constraints — complied with (asserted in code)

- **Station = verified registry ICAO.** Every row `icao_source='registry_yaml'`; asserted.
- **Ground truth = IEM CLI** (`actual_temp_f`); integer-°F settlement modeled as the outcome.
- **Contamination filter:** `logic_era != 'pre_station_fix'`. (Note: the 654K NBM backfill is
  `native_post_fix` — station-clean by construction; the 76%-contaminated figure applied to
  the thin live `nws_blend` forecasts, which are not used here.)
- **No look-ahead:** forecast `cycle_iso` strictly < target timestamp, asserted per decision;
  21,132 near-settlement rows (cycle ≥ target) are excluded by horizon-band selection.
- **Costs real:** Kalshi fee `⌈0.07·p·(1−p)⌉`/contract + configurable spread; far-tail
  illiquid strikes (proxy price <0.05 or >0.95) skipped.
- **Time-split (mandated):** train ≤2024 / validate 2025 / **holdout ≥2026 frozen**; empirical
  model parameters fit on **train only**.

## Method (unbiased on model structure)

The deep corpus has **no historical Kalshi prices**, so EV is measured against price
*proxies* spanning market sophistication. The decisive question is not "can we beat a number"
but "**is there skill beyond the best free public model (NBM), and would a real market leave
it on the table?**"

Competing bracket-probability models (P(integer high/low lands in each 1°F Kalshi bucket)):

| id | model | role |
|----|-------|------|
| M0_prod | N(p50, √(σ_heur²+2²)) — current production | baseline |
| M1_nbm_gauss | N(p50, σ_NBM) | **naïve market proxy** |
| M1_debiased | N(p50 + train_bias, σ_NBM) | **bias-aware market proxy** (knows station mean error) |
| M2_nbm_pctl | piecewise-linear CDF through NBM p10/p20/p50/p70/p90 | "sophisticated"/NBM-native proxy |
| M3_emp_z | empirical CDF of z=(actual−p50)/σ_NBM fit per (station,season,kind) on train | **our candidate** (drops Gaussian; captures bias+skew+fat tails) |

Bets evaluated: interior 1°F buckets (YES and NO) and open tail thresholds (≥X / ≤X), the
latter being where the fat-tail hypothesis predicts edge. τ (min edge) = 0.05.

## Result 1 — Calibration: there IS skill beyond NBM (necessary condition MET)

Out-of-sample Ranked Probability Score (lower = better), `day_before` (~32h); identical
ordering at `morning_of` (~8h) and `two_day` (~56h):

| model | train RPS | val RPS | **hold RPS** |
|-------|-----------|---------|----------|
| M0_prod | 1.931 | 1.903 | 2.167 |
| M1_nbm_gauss | 1.904 | 1.862 | 2.116 |
| M1_debiased | 1.833 | 1.836 | 2.060 |
| M2_nbm_pctl | 1.919 | 1.873 | 2.134 |
| **M3_emp_z** | **1.800** | **1.821** | **2.033** |

- M3 beats raw NBM, NBM's own percentiles, and production at every split and horizon.
- **Most of the gain is just bias correction:** M1_debiased (Gaussian + mean offset) captures
  ~70% of M3's RPS improvement. The empirical shape adds a little more.
- M2 (NBM percentiles) has catastrophic log-loss (~5.1 vs ~2.8) — its tails are too thin
  (p10–p90 spans only the central 80%; linear extrapolation underweights extremes). This is
  the documented NBM fat-tail miscalibration.

## Result 2 — EV collapses with market sophistication (sufficient condition NOT met)

Total EV per contract, `day_before`, τ=0.05, spread=0.02 (1¢/side):

| proxy (market assumption) | train | val (2025) | **hold (2026)** |
|---------------------------|-------|-----|------|
| M1_nbm_gauss — **naïve** | +0.061 | +0.038 | **+0.070** |
| M1_debiased — **bias-aware** | +0.024 | **+0.004** | **+0.017** |
| M2_nbm_pctl — NBM-native | +0.066 | +0.053 | +0.079 |

- Against the **naïve** market: +7%/ct. Against a market that merely subtracts the station's
  known mean bias: **+0.4%/ct on validation (≈ noise), +1.7%/ct on holdout.** ~4× collapse.
- The holdout residual is **entirely the cold tail** (`tail_low_YES` +7.6%/ct; everything else
  ≤+1.7% or negative). See Result 3.
- Same pattern at all horizons (naïve→bias-aware→val): `morning_of` +6.2%→+1.5%→+0.6%;
  `two_day` +7.4%→+1.9%→+0.5%.

## Result 3 — The residual edge is a cold-2026 anomaly, not skill

Tail reliability on holdout (predicted prob vs actual frequency), `day_before`:

```
            actual  gauss  debiased  pctl   M3_z
<=cen-3 :   0.251   0.185   0.235   0.161   0.207
>=cen+3 :   0.185   0.185   0.175   0.185   0.180
<=cen-5 :   0.132   0.071   0.099   0.064   0.104
>=cen+5 :   0.075   0.071   0.069   0.054   0.068
```

- The **hot tail is well-calibrated by everyone** → no hot-tail edge (`tail_high_YES` is
  negative vs the bias-aware proxy).
- The **cold tail is heavier than _every_ model predicts**, including ours (actual 0.251 vs
  M3 0.207; 0.132 vs 0.104). 2026 winter/spring simply ran colder than the 2021-2024 fit.
  Profit from `tail_low_YES` is "the period was cold and we bet cold" — exactly the
  rare-tail overfit the mandate warned against, and it is **not learnable** (our own model
  underpredicts it too).

## Result 4 — Fragile to realistic costs

At spread = 0.04 (2¢/side, realistic for thinner weather strikes), vs the **bias-aware**
market: validation total goes **−0.6%/ct (negative)**; holdout +0.7%/ct (cold-tail only).

## Result 5 — Effective sample & the real-price contradiction

- Bets within a market-day are highly correlated (all cold bets co-win; stations co-move
  regionally). Effective independent sample on holdout ≈ **5,278 markets** (fewer market-days),
  not the ~20-25K "bets." Tail EV rides on a handful of months.
- **The only real-price evidence contradicts the idealized edge.** The live production NO book
  (76 settled NOs, 59.2% WR, $0.712 avg entry → −$0.12/ct) lost money because the real market
  prices these contracts *richer* than NBM-implied — i.e., the real market is **not** the naïve
  raw-NBM proxy the idealized backtest assumes.

## Confidence & honest conclusion

- **High confidence:** there is real, persistent calibration skill over NBM, dominated by
  removable per-station mean bias (RPS, stable across 3 horizons × 2 OOS splits).
- **High confidence:** the apparent +EV is an artifact of the naïve-market assumption. It
  collapses ~4× under trivial bias-awareness, is ~0 on validation, negative under realistic
  spread, and its holdout residual is a non-learnable cold-tail anomaly.
- **Therefore: no robust +EV system is demonstrated.** The candidate that comes closest —
  bias-corrected pricing + selling over-peaked modal buckets — requires the market to price
  naïvely, which the live book directly contradicts.

## Real-price gate — RUN, decisive (no longer a future gate)

The open question was empirical: *does the real Kalshi market price closer to raw/biased NBM
(naïve → edge) or to the empirical distribution (efficient → no edge)?* It is now answered with
real data.

Pulled hourly candlesticks for all 38 **verified** weather series via pykalshi
(`scripts/kalshi_realprice_pull.py` → 14,346 settled markets; coverage 2026-03-21…05-26 = the
frozen holdout — Kalshi only retains ~2 months of settled-market history, so train/validate
real prices are unavailable, but holdout is the period that matters). Read the real two-sided
quote at the leak-safe evening-before moment (after the NBM cycle, before either extreme is
realized), and computed EV paying the ask, net Kalshi fee, realized on Kalshi's own settlement
(`scripts/weather_realprice_ev.py`). Interior B-buckets only (resolution rule auto-verified
9017/9024 against Kalshi results).

**Result (7,403 interior markets):**
- EV **negative at every threshold** (τ 0.03→0.18: ev/ct −0.018 to −0.023). Selectivity does
  not rescue it.
- **Brier vs outcome: market 0.1607 < empirical 0.1780** → the market's prices are a *better*
  probability than WX-EMP-1. Efficient.
- Market equidistant from raw-NBM and empirical (MAE ≈0.115; corr 0.42/0.45) → it uses
  information beyond any NBM-based model.

This explains the live production NO-book loss and converts the verdict from "unproven" to
**refuted on real prices: no +EV system exists.**

**Segment scan (mandate: "be explicit where edge comes from").** Broke the real-price WX-EMP-1
bets down by station (19), kind (max/min), price-decile, and side (YES/NO), with an
effective-sample bar (n_eff = distinct market-days; flag only if ev/ct > 2·SE and n_days ≥ 15).
**No segment is robustly +EV.** Every weakly-positive cell (e.g. KATL +3.2%/ct, price-decile-5
+3.4%/ct) is <1 SE from zero (SE ≈10%/ct on ~60 correlated market-days) — noise. daily_max
−1.6%/ct, daily_min −2.9%/ct; both sides negative (YES −1.7%, NO −2.4%). Edge is absent at
every cut. *Where does the edge come from? Nowhere.* (Season untestable on real prices: holdout
is spring-only.)

## Model-free market-structure test (the last avenue: no forecast at all)

To rule out a forecast-independent edge, tested the **market's own calibration** on all 14,336
real markets (`scripts/kalshi_market_calibration.py`): decision-time yes-price vs realized
frequency, leak-safe, net of ask + fee.

- A genuine **favorite-longshot bias exists** (~2%): longshots overpriced (price 0.07 →
  realized 0.049), favorites underpriced (gap +0.02). As theory predicts.
- **But it is fully absorbed by costs.** Best mechanical play (fade cheapest longshots: buy NO
  at yes_mid<0.05) nets **+0.2%/ct ≈ 0**; every wider/other threshold is net-negative
  (buy-NO <0.20 = −0.4%/ct; mid-range = −2 to −6%/ct).
- **Not stable:** the cheap-longshot fade decays Mar +0.86% → Apr +0.12% → **May −0.25%/ct** —
  in-sample noise, not edge (and 97% win rate means a single miss erases months).

So even with NO forecast, there is no robust +EV market-structure play net of real costs.

## Intraday morning-obs nowcast (the last untested available-data avenue)

Hypothesis: at a mid-morning decision (16Z), accumulated ASOS obs constrain the day's CLI
**high**; if the market lags the obs, a nowcast beats it. Pulled IEM ASOS hourly temps for all
19 stations 2021-2026 (`scripts/asos_pull.py`); trained residual `CLI_high − obs(16Z)` per
(station,season) on 2021-2024; applied to spring-2026 holdout vs the real 16Z market price
(`scripts/weather_nowcast_ev.py`). ASOS used only as a leak-safe feature (obs ≤16Z; CLI still
settlement). Result: **Brier(market) 0.175 < Brier(nowcast) 0.221** — the market is *sharper*
than the obs-nowcast (it already prices the obs + NBM + later guidance); EV **negative at every
threshold** (−1.1% to −2.2%/ct, all ≪ 2·SE). The "market is slow intraday" premise is false.

## Verdict — SIX independent avenues converge

(1) calibration vs NBM, (2) idealized proxy EV, (3) real-price forecast EV (Brier: market <
model), (4) real-price segment scan (no robust +EV at any station/kind/price/side cut),
(5) model-free market structure (favorite-longshot eaten by costs), (6) intraday obs-nowcast
(market Brier < nowcast) — **all conclude no robust +EV system exists net of fees and realistic
fills.** The market is efficient against every model buildable from all available data (NBM
point + percentiles, empirical distributions, intraday obs, market microstructure). The honest,
mandate-blessed answer.

## Reproduce (real-price gate)

```
.\scripts\run_capped.ps1 python scripts\kalshi_realprice_pull.py        # pull real Kalshi candlesticks
.\scripts\run_capped.ps1 python scripts\weather_realprice_ev.py         # forecast-model EV + Brier + tau sweep
.\scripts\run_capped.ps1 python scripts\kalshi_market_calibration.py    # model-free favorite-longshot test
```

## Reproduce

```
.\scripts\run_capped.ps1 python scripts\weather_edge_analysis.py day_before        # primary
.\scripts\run_capped.ps1 python scripts\weather_edge_analysis.py morning_of         # ~8h
.\scripts\run_capped.ps1 python scripts\weather_edge_analysis.py two_day            # ~56h
.\scripts\run_capped.ps1 python scripts\weather_edge_analysis.py day_before 0.04    # spread sensitivity
```
