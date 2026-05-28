# Candidate system spec — WX-EMP-1 (kalshi_weather)

**Status:** FROZEN candidate — **SHELVED 2026-05-28. The real-price gate (§9) was RUN
and FAILED.** Against actual Kalshi prices on the holdout (7,403 interior markets, real
fillable quotes, net fees), WX-EMP-1 is net **−2.1%/contract and negative at every
betting threshold**, and the market's own prices are a **better** probability estimate
of the outcome than the model (Brier 0.161 < 0.178). The market is efficient; no edge
survives. Do not deploy. Kept as a documented research artifact.
**Companion evidence:** `reports/2026-05-28_kalshi_weather_ev_discovery.md` (full analysis).
**Frozen model artifact:** `data/weather_emp_model_WX-EMP-1.json` (152 per-cell params + pooled
fallback, fit on TRAIN 2021-01-16…2024-12-31 ONLY).
**Engine:** `scripts/weather_edge_analysis.py` (`freeze` mode emits the artifact; band modes
reproduce the EV table).

This is the strongest candidate the deep corpus supports. Its honest holdout EV (§8) says it is
**not** robustly +EV; it is delivered frozen so it can be backtested against real Kalshi prices
(§9) — the one experiment that can convert "necessary condition met" into "deployable edge."

---

## 1. Identity & inviolable-constraint compliance

| field | value |
|-------|-------|
| system_id | WX-EMP-1 |
| venue / division | Kalshi `KXHIGH*` / `KXLOW*` daily temperature markets |
| settlement truth | NWS CLI via IEM (`actual_temp_f`), integer °F |
| station keying | **verified registry ICAO only** (`icao_source='registry_yaml'`); never city/grid |
| contamination | trained on `logic_era != 'pre_station_fix'` rows only |
| look-ahead | forecast `cycle_iso` strictly < target; asserted per decision |
| costs | Kalshi fee `⌈0.07·p·(1−p)⌉`/ct + spread; illiquid strikes skipped |
| mode | PAPER; `auto_execute` never granted; routes through `RiskAgent.evaluate()` |

## 2. Inputs (per market = station × target_date × kind∈{daily_max,daily_min})

- NBM bulletin (NODD/NOMADS), parsed to `temp_p10/p20/p50/p70/p90_f`, `temp_sigma_f`,
  `temp_mean_f`, keyed to the verified ICAO.
- Decision cycle: the **day-before** cycle, horizon ∈ [24, 40) h (the ~32h NBM cluster). The
  ~8h (`morning_of`) and ~56h (`two_day`) bands corroborate but are not the frozen decision point.
- Real Kalshi quote per strike at decision time: `yes_ask`, `no_ask` (dollars).

## 3. Probability model (frozen, train-only)

**Primary — M3_emp_z (empirical distribution; drops the Gaussian assumption):**
For each cell `(station, season, kind)`, the artifact stores the empirical distribution of the
standardized residual `z = (actual − p50) / σ_NBM` as a 0–100 percentile grid (fit on
2021-2024). Live, the CDF is the linear interpolation of that grid:

```
F_cell(x) = interp_z_quantiles( (x − p50)/σ_NBM )          # P(temp ≤ x)
P_bucket([lo,hi)) = F_cell(hi) − F_cell(lo)                 # 1°F Kalshi bucket
P(≥X) = 1 − F_cell(X−0.5);   P(≤X) = F_cell(X+0.5)          # tail / T-strikes
```
Cells with <150 train samples fall back to the pooled-all grid. The empirical grid captures
NBM's per-station-season **bias, skew, and fat tails** in one object. (`σ_NBM` folds in horizon.)

**Conservative variant — M1_debiased (2 params/cell):** `temp ~ N(p50 + bias_f, σ_NBM)`, where
`bias_f` is the train mean residual (also in the artifact). Captures ~70% of M3's calibration
gain with a far smaller parameter footprint → lower overfit surface. Recommended as the default
to carry into the real-price gate; M3 is the upper-skill variant.

Both beat raw NBM, NBM's own percentiles, and current production on **holdout** RPS (§8).

## 4. Decision pipeline (per market)

1. **Horizon gate:** accept only the day-before cycle (24 ≤ h < 40). Skip otherwise. (No
   near-zero-horizon settled markets — those collapse onto the observation, no forecast edge.)
2. **Model probs:** compute `P_bucket` for every listed strike (interior B-buckets and tail
   T-strikes) from §3.
3. **Edge vs REAL quote** (note: live uses the actual market price, not the §8 historical proxy):
   - `edge_yes(strike) = P_yes(strike) − yes_ask(strike)`
   - `edge_no(strike)  = (1 − P_yes(strike)) − no_ask(strike)`
4. **Liquidity gate:** quoted price ∈ [0.05, 0.95] and quoted size ≥ configured min. Skip
   far-tail no-book strikes.
5. **Bet rule (symmetric):** place YES if `edge_yes ≥ τ`; place NO if `edge_no ≥ τ`. `τ = 0.05`.
   Tail (T-strike) bets allowed under the same rule but **flagged high-variance** (see §8/§9 —
   their historical PnL is a cold-period artifact; default config may set a higher τ_tail or
   disable tails until the real-price gate clears them).
6. **Sizing:** §5.
7. **Risk gate:** `RiskAgent.evaluate()`; HITL approval (paper).

## 5. Bet-direction logic & sizing

- **Symmetric edge, no one-way valve.** WX-EMP-1 **removes** the production bucket-guard
  one-way valve and the asymmetric entry-price floors. The analysis found these were not
  EV-justified: they suppressed one side of a symmetric mispricing rather than filtering losers.
  Edge sign + `τ` is the only side selector.
- **Fractional Kelly:** `f* = (p·b − (1−p))/b`, `b = (1−price)/price`, `p = P_model`, then
  ×0.25. Caps: ≤1% bankroll per market, ≤ configured per-strike notional. `kelly_fraction()` in
  `_weather_math.py` already implements the core formula.

## 6. What this REPLACES vs current production (the questioned structures)

| production choice | WX-EMP-1 disposition | reason |
|-------------------|----------------------|--------|
| Gaussian CDF over bucket | **replaced** by empirical CDF (M3) / debiased Gaussian (M1d) | Gaussian over-peaks the mode & under-weights tails |
| hand-built σ (`√(σ_heur²+2²)`) | **replaced** by NBM σ (+ empirical shape) | heuristic σ worse on OOS RPS than NBM σ |
| bucket-guard one-way valve | **removed** | not EV-justified; suppressed symmetric edge |
| asymmetric entry-price floors | **removed** | replaced by symmetric τ on net edge |
| 10% min-divergence | **lowered to τ=5%**, symmetric | 10% was tuned on the negative-EV book |
| NO-on-between concentration | **removed** | now data-driven side selection |

## 7. Frozen parameters (snapshot)

```
fit_train_window      2021-01-16 .. 2024-12-31   (holdout 2026 NEVER fit)
decision_horizon      24–40 h (day_before)
primary_model         M3_emp_z   (conservative: M1_debiased)
edge_threshold τ      0.05
kelly_fraction        0.25
liquidity_price_band  [0.05, 0.95]
bucket_width          1 °F (integer CLI settlement)
n_cells               152   (+ pooled fallback)
```

## 8. Out-of-sample EV (honest — the deliverable number, NOT a single best-fit)

Total EV per contract, day_before, τ=0.05, **measured against historical price PROXIES** because
the deep corpus has no real prices. Proxies span market sophistication:

| market assumption | train | **val 2025** | **holdout 2026** |
|-------------------|-------|--------------|------------------|
| naïve (prices raw NBM) | +0.061 | +0.038 | **+0.070** |
| bias-aware (knows station bias) | +0.024 | **+0.004** | **+0.017** |
| bias-aware, 2¢/side spread | — | **−0.006** | +0.007 |
| NBM-percentile-native | +0.066 | +0.053 | +0.079 |

Holdout calibration (RPS, lower=better): M3 **2.033** < M1d 2.060 < raw-NBM 2.116 < NBM-pctl
2.134 < prod 2.167. Pattern identical at the ~8h and ~56h horizons.

**Edge attribution:** mostly the per-station **mean bias** (removable by any competitor); the
empirical-shape residual is small and, on holdout, concentrated in **cold-tail** bets that *all*
models (incl. ours) underpredict — a 2026 cold anomaly, not learnable skill. Effective
independent sample ≈ #markets (~5,278 holdout), not the ~20k correlated intra-day bets.

## 9. The capital gate — RUN 2026-05-28. RESULT: FAILED.

The decisive experiment (real Kalshi prices, not proxies) was executed. Pulled hourly
candlesticks for all 38 verified weather series (`scripts/kalshi_realprice_pull.py` →
`tmp/kalshi_realprice_candles.jsonl`, 14,346 settled markets, coverage 2026-03-21…05-26 =
the frozen holdout). Joined each interior B-market to its NBM decision and read the real
two-sided quote at the leak-safe evening-before moment (after the NBM cycle, before either
extreme is realized). EV via the WX-EMP-1 rule, **paying the ask, net Kalshi fee, realized
on Kalshi's own settlement** (`scripts/weather_realprice_ev.py`).

**Result (7,403 interior markets, real fillable prices):**

| τ | bets | pnl$ | **ev/ct** | win rate |
|---|------|------|-----------|----------|
| 0.03 | 5369 | −114.8 | **−0.0214** | 0.543 |
| 0.05 | 4594 | −98.4 | **−0.0214** | 0.537 |
| 0.08 | 3560 | −65.6 | **−0.0184** | 0.535 |
| 0.12 | 2478 | −56.6 | **−0.0228** | 0.523 |
| 0.18 | 1304 | −29.2 | **−0.0224** | 0.506 |

- **Negative at every threshold** — no selectivity rescues it.
- **Brier vs outcome: market 0.1607 < WX-EMP-1 0.1780.** The market's prices are a
  *better* probability estimate than the model. Direct proof of efficiency.
- Market is equidistant from raw-NBM and empirical (MAE ≈0.115 each; corr 0.42/0.45) →
  the real market uses information beyond any NBM-based model.

**Verdict: SHELVE.** Necessary condition (beat NBM on calibration) was met; sufficient
condition (market leaves it on the table) is **refuted by real prices**. This matches and
explains the live production NO-book loss. No +EV kalshi_weather system exists in this data.

## 10. Reproduce

```
.\scripts\run_capped.ps1 python scripts\weather_edge_analysis.py freeze data\weather_emp_model_WX-EMP-1.json
.\scripts\run_capped.ps1 python scripts\weather_edge_analysis.py day_before        # proxy EV table §8
.\scripts\run_capped.ps1 python scripts\kalshi_realprice_pull.py                    # pull real Kalshi prices
.\scripts\run_capped.ps1 python scripts\weather_realprice_ev.py                     # §9 real-price gate
```
