# S4 Model v1 — Kalshi Crypto Binary Prediction

**Date:** 2026-08-02  
**Assets:** BTC, ETH, SOL, XRP  
**Label:** y=1 if Kalshi 15m result='yes' (price UP), y=0 if result='no'  
**Data:** Binance 1m bars, Coinalyze 1h flow, Kalshi 15m markets 2026-05-25 → present  

## Methodology

### Label Rule (S1)
For each 15m market with result in {yes, no}: y=1 if result=yes, y=0 if no.  
strike=floor_strike, settle=settlement_value,  
move_pct=(settle-strike)/|strike| (divide-by-zero or None → skip).  

### Leakage Rule
Features computed AS-OF the last fully-closed bar BEFORE the window opens: reference bar ts_ms ≤ open_ts_ms - 60 000 ms.  
All rolling indicators computed causally on the full bar series first (pandas rolling is causal), then joined via `merge_asof(direction='backward')` using key = open_ts_ms - 60 000.  
Post-join assertion: no feature row used a bar ts > open_ts_ms - 60 000.  

### Split
Chronological split: holdout = last 20% by count (touched once at final eval).  
Within TRAIN: calibration slice = last ~20% of train (time-ordered).  
GBM fitted on train_core (earlier 80% of train).  
Platt/sigmoid calibration fitted on calibration slice.  
CV sanity metric: expanding-window (k=5) Brier on train_core.  

### Model
CatBoostClassifier (iterations=400, depth=6, lr=0.05, seed=42).  
Calibrated via Platt (sklearn LogisticRegression on GBM raw probs).  

### Missing Flow Values
Coinalyze 1h flow NaN values left as NaN for the GBM to handle natively (CatBoost supports NaN). An `is_missing_flow_1h` flag column distinguishes true-zero from missing.

### Flat-Bucket Caveat
**FINDING:** `settlement_value` in `lab_kalshi_markets` is the Kalshi binary contract settlement (0.0=no, 1.0=yes), NOT the close-60s-avg RTI price. Computing `move_pct = (settle - strike) / |strike|` with a binary settle and RTI strike (~50k–80k) yields |move_pct| ≈ 1.0 for every window. The flat-bucket analysis is therefore trivially all-directional (n_flat=0 at all thresholds). To compute physically meaningful move_pct, the actual close-60s-avg RTI for each window would need to be derived from the cfbenchmarks feed or Binance bar averages — deferred to a future phase. The flat-bucket rows are reported as observed (all windows directional) for completeness.

---

## Per-Asset Results (v1 + Rider B)

### BTC

**Windows:** 6516 total | 5212 train | 1304 holdout  
**Label balance:** y=1 (yes): 3209 (0.492), y=0 (no): 3307  
**Leakage assertion:** PASS  

#### Brier Scores (holdout)

| Metric | Value |
|--------|-------|
| Brier_model (calibrated) | 0.23965 |
| Brier_const_0.5 | 0.25000 |
| Brier_base_rate | 0.25005 |
| Mean CV Brier (train_core) | 0.25708 |
| Brier_market | TODO_MARKET_BENCHMARK |
| Skill score vs market | TODO_MARKET_BENCHMARK |

#### Reliability Curve (holdout, 10 bins)

| Bin         | n    | mean_pred | obs_freq |
|-------------|------|-----------|----------|
| [0.00,0.10) | 0    |      -    |     -    |
| [0.10,0.20) | 0    |      -    |     -    |
| [0.20,0.30) | 0    |      -    |     -    |
| [0.30,0.40) | 93   |    0.3793 |   0.2688 |
| [0.40,0.50) | 468  |    0.4576 |   0.4188 |
| [0.50,0.60) | 631  |    0.5472 |   0.5531 |
| [0.60,0.70) | 112  |    0.6282 |   0.7232 |
| [0.70,0.80) | 0    |      -    |     -    |
| [0.80,0.90) | 0    |      -    |     -    |
| [0.90,1.00) | 0    |      -    |     -    |

#### Feature Importances (top 15, v1)

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |    17.7466 |                        |
| ret_1m                    |    11.4521 |                        |
| vwap_dist                 |    10.3416 |                        |
| liq_1h                    |     7.0895 |                        |
| ret_15m                   |     6.3728 |                        |
| ret_5m                    |     5.7584 |                        |
| rsi_14                    |     4.7832 | FLAG: feature-only     |
| oi_delta_1h               |     4.6510 |                        |
| hour_of_day               |     4.5595 |                        |
| funding                   |     4.2056 |                        |
| ls_ratio                  |     4.2023 |                        |
| stoch_k_14                |     3.9781 | FLAG: feature-only     |
| rv_month                  |     3.1418 |                        |
| rv_day                    |     2.9757 |                        |
| day_of_week               |     2.8782 |                        |

#### Flat-Bucket Analysis (holdout)

| Threshold | n_directional | n_flat | Brier_directional | Brier_flat |
|-----------|---------------|--------|-------------------|------------|
|     0.02% |          1304 |      0 |           0.23965 |       -    |
|     0.05% |          1304 |      0 |           0.23965 |       -    |
|     0.10% |          1304 |      0 |           0.23965 |       -    |

#### Rider B (15m flow, ~last 21d)

**CAVEAT: Small-n evidence probe only.** n=3266 windows (15m flow coverage), holdout=654. Results not comparable to v1 (different time window, thinner data).

| Metric | Value |
|--------|-------|
| Brier_model (Rider B) | 0.24514 |
| Brier_const_0.5 | 0.25000 |
| Brier_market | TODO_MARKET_BENCHMARK |

Rider B top 10 feature importances:

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |     9.7273 |                        |
| ret_1m                    |     8.7691 |                        |
| vwap_dist                 |     8.0496 |                        |
| ret_15m                   |     7.7899 |                        |
| stoch_k_14                |     7.7626 | FLAG: feature-only     |
| liq_1h                    |     7.0759 |                        |
| ret_5m                    |     6.5735 |                        |
| oi_delta_1h               |     6.0634 |                        |
| rsi_14                    |     5.6265 | FLAG: feature-only     |
| rv_day                    |     4.6587 |                        |

_Interpretation note: if 15m flow features (cvd_15m, oi_delta_15m, ls_ratio_15m) rank highly, that supports investing in the deferred Binance-aggTrades fine-flow reconstruction for v2. If they rank near the bottom, v1 features are sufficient for near-term iteration._

---

### ETH

**Windows:** 6517 total | 5213 train | 1304 holdout  
**Label balance:** y=1 (yes): 3235 (0.496), y=0 (no): 3282  
**Leakage assertion:** PASS  

#### Brier Scores (holdout)

| Metric | Value |
|--------|-------|
| Brier_model (calibrated) | 0.23298 |
| Brier_const_0.5 | 0.25000 |
| Brier_base_rate | 0.25015 |
| Mean CV Brier (train_core) | 0.25568 |
| Brier_market | TODO_MARKET_BENCHMARK |
| Skill score vs market | TODO_MARKET_BENCHMARK |

#### Reliability Curve (holdout, 10 bins)

| Bin         | n    | mean_pred | obs_freq |
|-------------|------|-----------|----------|
| [0.00,0.10) | 0    |      -    |     -    |
| [0.10,0.20) | 0    |      -    |     -    |
| [0.20,0.30) | 13   |    0.2795 |   0.2308 |
| [0.30,0.40) | 203  |    0.3622 |   0.2956 |
| [0.40,0.50) | 421  |    0.4511 |   0.4323 |
| [0.50,0.60) | 447  |    0.5454 |   0.6242 |
| [0.60,0.70) | 192  |    0.6353 |   0.7135 |
| [0.70,0.80) | 28   |    0.7260 |   0.5714 |
| [0.80,0.90) | 0    |      -    |     -    |
| [0.90,1.00) | 0    |      -    |     -    |

#### Feature Importances (top 15, v1)

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |    13.9781 |                        |
| vwap_dist                 |    10.7676 |                        |
| btc_cvd_1h                |     7.5989 |                        |
| ret_1m                    |     6.2274 |                        |
| ret_15m                   |     5.8721 |                        |
| stoch_k_14                |     4.9806 | FLAG: feature-only     |
| ret_5m                    |     4.9292 |                        |
| btc_ret_1m                |     4.6315 |                        |
| liq_1h                    |     4.1417 |                        |
| oi_delta_1h               |     3.9749 |                        |
| rsi_14                    |     3.9091 | FLAG: feature-only     |
| btc_ret_5m                |     3.8129 |                        |
| funding                   |     3.1815 |                        |
| hour_of_day               |     3.0849 |                        |
| rv_month                  |     3.0213 |                        |

#### Flat-Bucket Analysis (holdout)

| Threshold | n_directional | n_flat | Brier_directional | Brier_flat |
|-----------|---------------|--------|-------------------|------------|
|     0.02% |          1304 |      0 |           0.23298 |       -    |
|     0.05% |          1304 |      0 |           0.23298 |       -    |
|     0.10% |          1304 |      0 |           0.23298 |       -    |

#### Rider B (15m flow, ~last 21d)

**CAVEAT: Small-n evidence probe only.** n=3103 windows (15m flow coverage), holdout=621. Results not comparable to v1 (different time window, thinner data).

| Metric | Value |
|--------|-------|
| Brier_model (Rider B) | 0.23321 |
| Brier_const_0.5 | 0.25000 |
| Brier_market | TODO_MARKET_BENCHMARK |

Rider B top 10 feature importances:

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |     9.6496 |                        |
| vwap_dist                 |     6.6866 |                        |
| ret_1m                    |     6.6452 |                        |
| btc_ret_15m               |     6.0416 |                        |
| btc_cvd_1h                |     5.9509 |                        |
| ret_15m                   |     5.8239 |                        |
| rsi_14                    |     5.7136 | FLAG: feature-only     |
| stoch_k_14                |     5.3953 | FLAG: feature-only     |
| btc_ret_1m                |     5.1150 |                        |
| btc_ret_5m                |     4.7542 |                        |

_Interpretation note: if 15m flow features (cvd_15m, oi_delta_15m, ls_ratio_15m) rank highly, that supports investing in the deferred Binance-aggTrades fine-flow reconstruction for v2. If they rank near the bottom, v1 features are sufficient for near-term iteration._

---

### SOL

**Windows:** 6518 total | 5214 train | 1304 holdout  
**Label balance:** y=1 (yes): 3228 (0.495), y=0 (no): 3290  
**Leakage assertion:** PASS  

#### Brier Scores (holdout)

| Metric | Value |
|--------|-------|
| Brier_model (calibrated) | 0.24191 |
| Brier_const_0.5 | 0.25000 |
| Brier_base_rate | 0.24996 |
| Mean CV Brier (train_core) | 0.25965 |
| Brier_market | TODO_MARKET_BENCHMARK |
| Skill score vs market | TODO_MARKET_BENCHMARK |

#### Reliability Curve (holdout, 10 bins)

| Bin         | n    | mean_pred | obs_freq |
|-------------|------|-----------|----------|
| [0.00,0.10) | 0    |      -    |     -    |
| [0.10,0.20) | 0    |      -    |     -    |
| [0.20,0.30) | 0    |      -    |     -    |
| [0.30,0.40) | 106  |    0.3749 |   0.2830 |
| [0.40,0.50) | 528  |    0.4566 |   0.4470 |
| [0.50,0.60) | 544  |    0.5445 |   0.5441 |
| [0.60,0.70) | 125  |    0.6249 |   0.6560 |
| [0.70,0.80) | 1    |    0.7076 |   0.0000 |
| [0.80,0.90) | 0    |      -    |     -    |
| [0.90,1.00) | 0    |      -    |     -    |

#### Feature Importances (top 15, v1)

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |    16.7800 |                        |
| btc_cvd_1h                |     8.0721 |                        |
| ret_1m                    |     7.2393 |                        |
| vwap_dist                 |     6.5073 |                        |
| btc_ret_15m               |     5.6339 |                        |
| btc_ret_1m                |     5.5103 |                        |
| stoch_k_14                |     5.3659 | FLAG: feature-only     |
| ret_15m                   |     4.9839 |                        |
| rsi_14                    |     4.2531 | FLAG: feature-only     |
| ret_5m                    |     4.1552 |                        |
| liq_1h                    |     4.0785 |                        |
| btc_ret_5m                |     3.9377 |                        |
| rv_week                   |     3.1957 |                        |
| day_of_week               |     3.0647 |                        |
| ls_ratio                  |     2.5929 |                        |

#### Flat-Bucket Analysis (holdout)

| Threshold | n_directional | n_flat | Brier_directional | Brier_flat |
|-----------|---------------|--------|-------------------|------------|
|     0.02% |          1304 |      0 |           0.24191 |       -    |
|     0.05% |          1304 |      0 |           0.24191 |       -    |
|     0.10% |          1304 |      0 |           0.24191 |       -    |

#### Rider B (15m flow, ~last 21d)

**CAVEAT: Small-n evidence probe only.** n=4719 windows (15m flow coverage), holdout=944. Results not comparable to v1 (different time window, thinner data).

| Metric | Value |
|--------|-------|
| Brier_model (Rider B) | 0.24452 |
| Brier_const_0.5 | 0.25000 |
| Brier_market | TODO_MARKET_BENCHMARK |

Rider B top 10 feature importances:

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |     9.5212 |                        |
| ret_15m                   |     6.7626 |                        |
| vwap_dist                 |     6.4852 |                        |
| stoch_k_14                |     6.4387 | FLAG: feature-only     |
| ret_1m                    |     5.9610 |                        |
| btc_cvd_1h                |     5.8798 |                        |
| ret_5m                    |     5.6311 |                        |
| btc_ret_15m               |     5.5241 |                        |
| btc_ret_1m                |     5.3527 |                        |
| btc_ret_5m                |     5.0842 |                        |

_Interpretation note: if 15m flow features (cvd_15m, oi_delta_15m, ls_ratio_15m) rank highly, that supports investing in the deferred Binance-aggTrades fine-flow reconstruction for v2. If they rank near the bottom, v1 features are sufficient for near-term iteration._

---

### XRP

**Windows:** 6516 total | 5212 train | 1304 holdout  
**Label balance:** y=1 (yes): 3225 (0.495), y=0 (no): 3291  
**Leakage assertion:** PASS  

#### Brier Scores (holdout)

| Metric | Value |
|--------|-------|
| Brier_model (calibrated) | 0.24194 |
| Brier_const_0.5 | 0.25000 |
| Brier_base_rate | 0.25013 |
| Mean CV Brier (train_core) | 0.25954 |
| Brier_market | TODO_MARKET_BENCHMARK |
| Skill score vs market | TODO_MARKET_BENCHMARK |

#### Reliability Curve (holdout, 10 bins)

| Bin         | n    | mean_pred | obs_freq |
|-------------|------|-----------|----------|
| [0.00,0.10) | 0    |      -    |     -    |
| [0.10,0.20) | 0    |      -    |     -    |
| [0.20,0.30) | 4    |    0.2921 |   0.2500 |
| [0.30,0.40) | 148  |    0.3669 |   0.3919 |
| [0.40,0.50) | 493  |    0.4554 |   0.4503 |
| [0.50,0.60) | 483  |    0.5457 |   0.5631 |
| [0.60,0.70) | 161  |    0.6376 |   0.6211 |
| [0.70,0.80) | 15   |    0.7216 |   0.8667 |
| [0.80,0.90) | 0    |      -    |     -    |
| [0.90,1.00) | 0    |      -    |     -    |

#### Feature Importances (top 15, v1)

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |    10.5093 |                        |
| btc_cvd_1h                |     9.0388 |                        |
| rsi_14                    |     7.1781 | FLAG: feature-only     |
| vwap_dist                 |     6.5122 |                        |
| ret_1m                    |     6.4989 |                        |
| btc_ret_15m               |     5.4241 |                        |
| stoch_k_14                |     5.0074 | FLAG: feature-only     |
| liq_1h                    |     4.5869 |                        |
| btc_ret_1m                |     4.5202 |                        |
| btc_ret_5m                |     4.4676 |                        |
| ret_15m                   |     4.2733 |                        |
| ret_5m                    |     4.1579 |                        |
| oi_delta_1h               |     3.8544 |                        |
| funding                   |     3.7469 |                        |
| rv_day                    |     3.6608 |                        |

#### Flat-Bucket Analysis (holdout)

| Threshold | n_directional | n_flat | Brier_directional | Brier_flat |
|-----------|---------------|--------|-------------------|------------|
|     0.02% |          1304 |      0 |           0.24194 |       -    |
|     0.05% |          1304 |      0 |           0.24194 |       -    |
|     0.10% |          1304 |      0 |           0.24194 |       -    |

#### Rider B (15m flow, ~last 21d)

**CAVEAT: Small-n evidence probe only.** n=6516 windows (15m flow coverage), holdout=1304. Results not comparable to v1 (different time window, thinner data).

| Metric | Value |
|--------|-------|
| Brier_model (Rider B) | 0.24085 |
| Brier_const_0.5 | 0.25000 |
| Brier_market | TODO_MARKET_BENCHMARK |

Rider B top 10 feature importances:

| Feature                   | Importance | Notes                  |
|---------------------------|------------|------------------------|
| cvd_1h                    |     7.6746 |                        |
| btc_cvd_1h                |     6.6043 |                        |
| vwap_dist                 |     6.4848 |                        |
| ret_1m                    |     6.3565 |                        |
| stoch_k_14                |     6.3190 | FLAG: feature-only     |
| rsi_14                    |     6.2938 | FLAG: feature-only     |
| ret_5m                    |     5.8875 |                        |
| btc_ret_1m                |     5.8089 |                        |
| btc_ret_5m                |     5.6963 |                        |
| btc_ret_15m               |     5.3698 |                        |

_Interpretation note: if 15m flow features (cvd_15m, oi_delta_15m, ls_ratio_15m) rank highly, that supports investing in the deferred Binance-aggTrades fine-flow reconstruction for v2. If they rank near the bottom, v1 features are sufficient for near-term iteration._

---

## TODO Hooks (lead engineer — S5)

### TODO_MARKET_BENCHMARK
```python
# Wire in from lab/calibration.py:
from calibration import compare_to_market
# market_p_list: list of Kalshi window-open candle implied probs
# (e.g., yes_bid_close or (yes_bid+yes_ask)/2 at the 1m candle
#  closest to but before window open, from lab_kalshi_candles)
result = compare_to_market(model_p_list, market_p_list, y_holdout)
# result keys: brier_model, brier_market, skill_score_vs_market, n
```

### TODO_DUAL_EV
```python
# Wire in from lab/ev.py:
from ev import taker_ev, maker_ev, aggregate_maker, aggregate_taker
# For each holdout window:
#   side = 'yes' if cal_prob > 0.5 else 'no'
#   taker_result = taker_ev(cal_prob, side, yes_ask, no_ask)
#   post_candles = [{ts, yes_low, no_low, volume}, ...]
#                  from lab_kalshi_candles after window open
#   maker_result = maker_ev(cal_prob, side, bid, post_candles,
#                           window_close_ts=close_ts)
# Aggregation:
#   agg_t = aggregate_taker(taker_results)
#   agg_m = aggregate_maker(maker_results)
#   # MUST report agg_m['fill_rate'] alongside agg_m['mean_ev_on_fills']
```

---

## Summary Table

| Asset | N total | N holdout | Base rate | Brier_model | Brier_const05 | CV Brier | Rider B Brier |
|-------|---------|-----------|-----------|-------------|---------------|----------|---------------|
| BTC | 6516 | 1304 | 0.492 | 0.23965 | 0.25000 | 0.25708 | 0.24514 |
| ETH | 6517 | 1304 | 0.496 | 0.23298 | 0.25000 | 0.25568 | 0.23321 |
| SOL | 6518 | 1304 | 0.495 | 0.24191 | 0.25000 | 0.25965 | 0.24452 |
| XRP | 6516 | 1304 | 0.495 | 0.24194 | 0.25000 | 0.25954 | 0.24085 |

_Distributions and leads only. No verdict on whether the model 'works' or 'beats the market' is drawn here — that gate requires brier_market (TODO) and real edge validation under live conditions. Accuracy is reported, never gates._

