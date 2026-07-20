# Coinbase BTC Donchian — Re-validation on the Binance 4Y corpus

**Started:** 2026-07-19 · **Status:** Phases 0–7 complete. Read-only; nothing deployed or committed.
**Harness:** `scripts/donchian_binance_*.py` (reuse prod's byte-identical `donchian_btc.evaluate_donchian`). **Fees = 0** (Coinbase spot); 3 bps/side slippage base.

> **HIGHEST-CERTAINTY FINDING (independent of everything below):** the sleeve sits in cash ~74% of the time earning **0%**. Yield-ifying that idle cash (money-market / T-bill / USDC-yield, swept when flat) is worth **~+3.3%/yr (~$2,800/yr on $85K) at near-zero risk** — a larger and far more *certain* return than the strategy's own modest risk-adjusted edge over matched exposure. It is **independent of the sizing and isolation decisions, and capturable whether or not the strategy ever activates.** (Detail: §6.4.)

---

## PHASE 0 — Prior work (recap; full detail in `2026-07-18_donchian_rebacktest_phase0.md`)

- **Deployed params:** `entry=20, exit=6, trend_filter=168 SMA, 6h bars`, 100%-in/out.
- **Headline:** "24mo full corpus +56.30% vs HODL +30.42% = **+25.89% alpha**", 35 round-trips, 49% WR, maxDD 16.49%, ~25% time in BTC — sourced from `strategies.yaml`/`deploy_log.md` 2026-05-09.
- **In-sample-optimized: YES.** The +25.89% is a **full-sample** run of a grid-selected config. The only OOS was a **single 6-month fold in a bear market (Nov 2025→May 2026, BTC −26.85%)** — where any exit-to-cash rule beats HODL near-tautologically — and that fold is a **subset of the same 24mo data**. No parameter-stability evidence. Original data source: Coinbase public REST, BTC-USD spot, 6h; **no fees/slippage modeled**.

---

## PHASE 1 — Data validation

### 1a. The Binance database

| Property | Value |
|---|---|
| Symbol | **BTCUSDT** — Binance **USD-M perpetual futures** (`data.binance.vision/.../futures/um/monthly/klines`) |
| Timeframes | 1m, 3m, 15m, 1h, 4h, 1d (native; **no 6h**) |
| Date range | **2022-07-01 00:00 → 2026-06-30 UTC (48 months = 4.0 years)** |
| Row counts | 1d=1,461 · 4h=8,766 · **1h=35,064** · 15m=140,256 · 3m=701,280 (each = exact theoretical max) |
| Gaps / dupes | **0 missing bars, 0 duplicate open_times, 0 missing months**; 240/240 checksums PASS |
| Storage | monthly CSV per interval; 12-col Binance kline schema; **header row in every file** (skip row 0); `open_time` = 13-digit ms-UTC; drop col 12 `ignore` |
| Query | loaded directly from CSV (stdlib `csv`); no DB engine |

### 1b. 6h derivation (shown + validated)

6h is not native → **resample 1h → 6h**, bucketing on `open_time // (6·3600·1000)` (aligns to **00/06/12/18 UTC** — the same grid as the prod scheduler and as Coinbase's 21600s candles):
`open = first 1h open · high = max · low = min · close = last 1h close · volume = Σ`.

| Check | Result |
|---|---|
| 1h rows loaded | 35,064 (2022-07-01 00:00 → 2026-06-30 23:00) |
| 6h buckets total | 5,844 |
| **Complete (exactly 6×1h)** | **5,844** |
| Incomplete dropped | **0** |
| Contiguity gaps (≠21600s) | **0** |
| 6h span | 2022-07-01 00:00 → 2026-06-30 18:00 UTC |

Derivation is **exact and gap-free** (35,064 / 6 = 5,844). ✅

### 1c. Cross-venue check (Binance perp vs Coinbase spot)

Overlap window = **2024-05-09 → 2026-05-09 (2,921 aligned 6h bars)**, using the cached Coinbase BTC-USD 6h series. This spans the 2024 bull, 2025, and the early-2026 decline (multi-regime); extendable to the full 4Y if wanted, but the disagreement is a microstructure property and already stable.

**Price/extreme agreement** (Binance − Coinbase, as % of Coinbase):

| Series | median abs% | mean abs% | p95 abs% | max abs% |
|---|---:|---:|---:|---:|
| Close | 0.053% | 0.057% | 0.119% | 0.257% |
| **20-bar high** (entry extreme) | 0.056% | 0.074% | 0.195% | 0.875% |
| **6-bar low** (exit extreme) | 0.058% | 0.089% | 0.203% | **5.125%** |

Signed median close diff = **−0.040%** (Binance perp trades a hair below Coinbase spot — small USDT-peg/basis offset).

**Signal disagreement** (does the venue flip the Donchian trigger?), N = 2,753 evaluable bars:

| Signal | Binance events | Coinbase events | Bars disagree | % of bars |
|---|---:|---:|---:|---:|
| Up-break (`close > 20-bar high`) | 146 | 150 | 6 | **0.218%** |
| Down-break (`close < 6-bar low`) | 248 | 249 | 11 | **0.400%** |
| Trend-ok (`close > SMA168`) | — | — | 6 | 0.218% |
| **Entry signal** (up-break ∧ trend-ok) | — | — | 5 | **0.182%** |
| **Compounded position state** (full 20/168/6 run) | — | — | 52 / 2,921 | **1.78%** |

**Verdict on data:** Binance perp is a **valid price proxy** for the Coinbase-spot Donchian. Closes agree to ~0.05%, extremes to ~0.06% median, and the raw signal flips on only **0.18–0.40% of bars**; the path-compounded position differs **1.78%** of the time. The one caveat — a rare 6-bar-low disagreement up to **5.1%** — confirms your instinct that *extremes* (perp liquidation wicks) matter more than closes, but at 6h it's rare (0.4% of bars) and roughly washes out. Backtesting 20/168/6 on Binance perp will track a Coinbase-spot backtest closely.

### 1d. Plan-affecting finding

**The 4-year window is strongly bull-dominated: HODL = +201.20%** (2022-07 close $19,457 → 2026-06 close $58,605). This **inverts** the original validation (a 6-month −27% *bear*). A 100%-in/out trend-follower that spends time in cash faces a **high bar to beat +201% HODL**, and its "alpha" will be dominated by (a) how much of the 2023–2024 up-leg it captured and (b) how much 2022/2025 drawdown it dodged. **Phase 2's per-year and regime breakdowns are therefore the load-bearing analysis, not the headline total.**

---

## Plan for Phase 2+ (proposed — confirm or redirect)

- **Primary corpus:** Binance perp 6h, full 4Y (2022-07 → 2026-06), validated above.
- **Slippage/spread (fees=0):** base **3 bps/side** applied at each state change (buy/sell), with a sensitivity band **{0, 2, 5, 10} bps/side** reported.
- **Objective (Phase 3/4 selection):** rank by **Calmar** (risk-adjusted), with return/maxDD/Sharpe/time-in-market all reported; never select on raw return alone.
- **Fills:** next-6h-bar open, one-bar latency (matches the original harness + prod's "can't trade the close you saw").
- **Walk-forward (Phase 4):** rolling, multi-fold across the 4Y so multiple regimes appear in both train and test; parameter-drift + overfit-tax quantified.

---

## PHASE 2 — Baseline replication (20/168/6 on Binance 4Y)

Fills: next-6h-bar open, 1-bar latency. Fees 0; base slippage 3 bps/side. Harness `scripts/donchian_binance_phase2.py`.
**Sanity vs the original:** maxDD 16.7% ≈ original 16.49%; time-in-market 26% ≈ 25%; ~17 round-trips/yr ≈ 17.5 — the rule behaves identically on the new venue/window, so the harness is trustworthy.

### 2a. Lead finding (per your rule B): the 4Y aggregate is NOT the headline

**Answer to "is there any regime where this adds risk-adjusted value, or does it only win by being in cash during declines?":**
**It only wins by sitting in cash during declines.** It is a **market-timing / drawdown-avoidance overlay, not a trend-capture edge.**

- **Bull regimes:** captures just **1/3 of the upside** (+245% vs HODL +749%), in-market only 40% of the time → **destroys value vs holding when BTC rises.**
- **Bear regimes:** avoids most of the loss (−18% vs −69%) by being ~91% in cash → this is the entire source of its outperformance.
- **Chop:** modest genuine-looking value (+23.8% vs +10.6% at 26% time-in-market) — the *one* place worth probing, but small and possibly noise.
- **Edge-concentration:** excluding the single best-alpha year (2026 H1 decline) flips the 4Y result from +49 pts to **−120.7 pts vs HODL.** The "outperformance" lives in the decline periods, not in trend capture.

### 2b. Per-CALENDAR-YEAR (base 3 bps) — the real story

| Year | Strat % | HODL % | **Alpha** | Strat maxDD | HODL maxDD | TIM % | RT |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2022 (H2) | −13.0 | −15.0 | **+2.0** | 16.0 | 36.5 | 13.2 | 5 |
| 2023 | +96.3 | +155.9 | **−59.6** | 12.4 | 20.8 | 32.7 | 18 |
| 2024 | +75.1 | +121.1 | **−46.0** | 14.7 | 29.9 | 31.3 | 22 |
| 2025 | +10.2 | −6.4 | **+16.6** | 15.1 | 34.3 | 23.6 | 16 |
| 2026 (H1) | +6.4 | −33.1 | **+39.5** | 7.7 | 39.7 | 19.6 | 7 |

**Lags HODL in every up-year (2023, 2024) and only beats in the down/chop years (2025, 2026, 2022).** Consistent drawdown reduction every year (strat maxDD always < HODL).

### 2c. Per-REGIME attribution

Method: causal daily regime = trailing-60-day return, **bull > +10%, bear < −10%, chop between**; each 6h bar tagged by its day.

| Regime | % of time | Strat ret | HODL ret | Strat TIM % | Read |
|---|---:|---:|---:|---:|---|
| Bull | 37.4% | +245.4% | +749.1% | 40.4% | captures ~33% of upside — **lags badly** |
| Bear | 24.5% | −18.0% | −69.2% | 8.9% | sits in cash — **the whole edge** |
| Chop | 34.0% | +23.8% | +10.6% | 25.7% | modest real value — worth probing |
| (warmup) | 4.1% | — | — | — | first 60d |

### 2d. Aggregate 4Y (report, but treat as one regime-conditional number)

| Metric | Strat 20/168/6 | HODL | Note |
|---|---:|---:|---|
| Total return | **+250.6%** | +201.2% | +49.4 pts — **regime-mix artifact** |
| CAGR | +36.8% | +31.7% | |
| Max drawdown | **16.7%** | 53.4% | genuine DD reduction |
| Calmar | **2.21** | 0.59 | flattering, but conditional |
| Sharpe | 1.48 | 0.82 | |
| Time in market | **26.0%** | 100% | ~74% idle (see C) |
| Round-trips | 68 | 0 | 44.1% win, avg win +7.78% / loss −2.30% |
| Longest flat stretch | 74 days | 0 | |
| **DD constraint (A)** | **PASS** (16.7% ≤ 53.4%) | — | shallower than HODL |

**Slippage robustness:** total return +265.2% / +255.4% / +250.6% / +241.2% / +218.7% at 0 / 2 / 3 / 5 / 10 bps/side — beats HODL (+201.2%) **even at 10 bps/side** (Calmar 1.92). The edge is not a fee/slippage mirage.

**Best-year counterfactuals:** dropping the best *return* year (2023, a year it *lagged*) *raises* alpha to +60.9 (wrong test); dropping the best *alpha* year (2026) drops it to **−120.7** (right test). The outperformance is **concentrated in the decline periods.**

### 2e. Time-in-market (per your rule C)

26% overall; **only 40% even during bull regimes.** ~74% of the sleeve is idle on average. Because the outperformance is drawdown-avoidance rather than BTC-beta capture, the risk-adjusted "win" is essentially **"cash + occasional BTC"** — capital-inefficient. If the goal is drawdown-controlled BTC exposure, a smaller BTC allocation held continuously might achieve similar risk-adjusted numbers without a market-timing bet. **Carried to Phase 6 as the capital-efficiency question.**

### 2f. Bottom line for Phase 2

On this **bull-dominated** 4Y window the strategy beats HODL on total return *and* risk-adjusted terms (Calmar 2.21 vs 0.59, maxDD 16.7% vs 53.4%) — but **that is a lower-volatility, cash-heavy, drawdown-avoidance profile, not a trend-capture edge.** It **lags HODL in every rising market** and its whole outperformance is dodging declines. Whether that is worth deploying depends on whether you want *drawdown-controlled partial BTC exposure* (it delivers that) or *an edge that beats holding BTC* (it does not, in up-markets). Phase 3–5 will test whether tuning/variants convert the chop-regime hint into something more, or just re-fit the decline-avoidance.

---

---

## PHASE 3 — Parameter surface + exposure benchmarks (D) + chop probe (E)

Harness `scripts/donchian_binance_phase3.py` (fast O(n) rolling; **validated exactly** against the Phase 2 evaluate_donchian run: 20/168/6 → +250.6% / maxDD 16.7% / Calmar 2.21 / 68 RT).

### 3a. D — does the SIGNAL beat simply holding less BTC? **Yes, decisively.**

All exposure-matched, monthly-rebalanced, 3 bps slippage, full 4Y:

| Portfolio | Total % | CAGR % | maxDD % | **Calmar** | Sharpe | avg exposure (TIM) |
|---|---:|---:|---:|---:|---:|---:|
| **Donchian 20/168/6** | +250.6 | +36.8 | 16.7 | **2.21** | 1.48 | 26.0% |
| Static 26% BTC (monthly) | +45.9 | +9.9 | 16.8 | **0.59** | 0.82 | 26.4% |
| Vol-target BTC (23% ann) | +99.3 | +18.8 | 40.9 | **0.46** | 0.80 | 54.5% |
| HODL | +201.2 | +31.7 | 53.4 | **0.59** | 0.82 | 100% |

**The cleanest result in the whole study:** at **identical average exposure (26%) and identical max drawdown (~16.7%)**, the Donchian returns **+250.6% vs the static-26% benchmark's +45.9%** — a **+205-pt gap attributable entirely to *when* it holds BTC.** The signal's Calmar (2.21) is ~3.7× static-26% (0.59) and ~4.8× vol-target (0.46). **This refines the Phase 2 lean:** it is **not** "merely lower exposure" — the timing carries genuine risk-adjusted value. (It still lags full HODL on *total return* in this bull window because 26% < 100% exposure — both things are true.)

### 3b. Grid (264 combos: entry{10,15,20,28,40,55,75,100} × trend{None,84,168,336,504,720} × exit{3,4,6,8,12,20}, exit<entry)

Ranges chosen to bracket the incumbent (entry 20, trend 168, exit 6) with faster/slower on every axis. Objective = Calmar. **DD-disqualification (rule A): 0/264 disqualified — no config drew down deeper than HODL** (the family is inherently low-DD; the DQ table is empty).

**Top 10 by Calmar:**

| entry | exit | trend | Calmar | Total % | maxDD % | Sharpe | TIM % | RT |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20 | 3 | 336 | **2.72** | +212.9 | 12.1 | 1.53 | 17.7 | 68 |
| 15 | 3 | 336 | 2.29 | +223.4 | 14.9 | 1.51 | 19.2 | 77 |
| **20** | **6** | **168** | **2.21** | +250.6 | 16.7 | 1.48 | 26.0 | 68 |
| 20 | 6 | 336 | 2.20 | +264.4 | 17.4 | 1.59 | 23.3 | 60 |
| 15 | 6 | 168 | 2.14 | +294.2 | 19.1 | 1.57 | 27.3 | 72 |
| 20 | 4 | 336 | 2.13 | +221.3 | 15.9 | 1.52 | 19.5 | 65 |
| 20 | 8 | 336 | 2.10 | +263.4 | 18.1 | 1.50 | 27.2 | 54 |
| 15 | 12 | 168 | 2.05 | +325.4 | 21.3 | 1.48 | 38.1 | 53 |
| 20 | 4 | 168 | 2.04 | +197.7 | 15.4 | 1.37 | 21.6 | 75 |
| 10 | 6 | 504 | 1.99 | +263.4 | 19.2 | 1.47 | 27.9 | 81 |

**Grid median (n=264): Calmar 1.06, total +135.5%, maxDD 22.7%, TIM 23.1%.** The **median beats HODL's Calmar (0.59)** — so the family is robust, not a lucky winner (per your median rule). But only **46/264 (17%) beat HODL on total return** — the exposure story again (most configs hold <100% and lag in the bull window).

### 3c. Plateau vs spike — **plateau.** 20/168/6 ranks **3rd of 264** by Calmar; immediate neighbors:

| Move | Config | Calmar | Total % | maxDD % | TIM % |
|---|---|---:|---:|---:|---:|
| incumbent | 20/6/168 | 2.21 | +250.6 | 16.7 | 26.0 |
| entry −1 step | 15/6/168 | 2.14 | +294.2 | 19.1 | 27.3 |
| entry +1 | 28/6/168 | 1.73 | +192.6 | 17.8 | 23.4 |
| exit −1 | 20/4/168 | 2.04 | +197.7 | 15.4 | 21.6 |
| exit +1 | 20/8/168 | 1.73 | +219.8 | 19.5 | 30.0 |
| trend −1 | 20/6/84 | **1.24** | +207.2 | 26.1 | 31.0 |
| trend +1 | 20/6/336 | 2.20 | +264.4 | 17.4 | 23.3 |

Neighbors stay in Calmar **1.7–2.2** on the entry/exit axes and 2.20 at longer trend; the only soft spot is a **short (84-bar/21d) trend filter → 1.24**. **20/168/6 is on a smooth plateau, not a fitted spike.** The best configs favor **shorter exits (x=3) + longer trend (t=336)** → lower DD (12–15%) and *lower* TIM (18–20%) for similar return — a lead for Phase 5, but it *reduces* capital deployment further.

### 3d. E — interrogating the chop cell: **it does not survive.**

**E1 — label sensitivity (chop-cell strat vs HODL return across lookback × band):**

| lookback | band | chop %time | strat % | HODL % | verdict |
|---:|---:|---:|---:|---:|---|
| 30d | ±5 | 32.7 | −32.0 | −15.4 | strat **worse** |
| 30d | ±10 | 54.7 | −3.4 | +93.6 | strat **worse** |
| 30d | ±15 | 73.3 | +42.3 | +33.1 | better |
| 60d | ±5 | 17.7 | +3.4 | −11.8 | better |
| **60d** | **±10** | 34.0 | **+23.8** | **+10.6** | **base (better)** |
| 60d | ±15 | 52.2 | +22.7 | +65.5 | strat **worse** |
| 90d | ±5 | 12.6 | +11.3 | +20.6 | strat **worse** |
| 90d | ±10 | 26.6 | +45.4 | +81.5 | strat **worse** |
| 90d | ±15 | 42.3 | +56.0 | +56.7 | tie |

The chop outperformance **flips sign with the labeling** — strat *underperforms* HODL-in-chop in **5 of 9** reasonable label choices. The base 60d/±10% cell was a favorable draw, not a stable property.

**E2 — chop round-trips (base labeling): n=24, win-rate 46%, sum +66.5%.** Returns: `[-7.9,-5.4,-3.5,-2.6,-2.3,-1.8,-1.5,-1.5,-1.2,-1.2,-1.0,-0.8,-0.3, +0.4,+0.8,+1.8,+1.9,+2.9,+5.8,+6.1,+8.7,+15.5,+20.4,+33.2]`. **The top 2 trades (+33.2%, +20.4%) = 80% of the positive P&L.** Drop those two and the chop "edge" is gone.

**E3 — intra-chop bursts (65 chop segments):** run-up median +2.5% / p90 +10.6% / **max +22.4%**; run-down median −1.9% / p90 −10.2% / max −18.1%. "Chop" windows contain **±10–22% directional bursts** — the strat's chop gains are it occasionally riding one of these (the 2 big trades), not a chop-specific skill.

**Verdict on chop:** the one cell that looked like return-generation is an **artifact** — label-dependent and carried by 2 trades. **Not a chop edge.**

### 3e. Phase 3 net

- **The signal is real, not just lower exposure:** it beats matched-exposure benchmarks ~3.7–4.8× on Calmar; at identical 26% exposure and 16.7% DD it returns +250.6% vs +45.9%. The timing (when to hold BTC) genuinely adds risk-adjusted value.
- **20/168/6 is found, not fitted:** rank 3/264, smooth plateau, grid-median Calmar (1.06) beats HODL (0.59).
- **But** it still lags HODL *total return* in bull markets (17% of configs beat HODL total), and the chop "return edge" is noise. The value is **drawdown-controlled, well-timed partial BTC exposure** — strong risk-adjusted, weak absolute-vs-HODL in bulls, capital-light (TIM 18–26%).
- Nothing here overturns the Phase 6 capital-efficiency question; if anything the best configs deploy *less* (TIM ~18%).

---

---

## PHASE 4 — Rolling walk-forward (the decision point)

**Design (justified):** rolling, **12-month train / 6-month test / roll 6 months → 6 folds**, OOS coverage **2023-07 → 2026-06 (~3 years)**. 12mo train gives the ≤180-day SMA room plus ~6mo of signal to optimize; 6mo test (~8 expected trades) balances fold count vs per-fold sample. Each fold: sweep the Phase-3 grid on train, pick **Calmar-max among eligible (train maxDD ≤ train-HODL maxDD)**, trade that fixed config on the unseen test. **6 folds is the minimum meaningful — the OOS aggregate is itself a modest sample.** Harness `scripts/donchian_binance_phase4.py`. *(OOS-only below; in-sample numbers appear only in the overfit-tax section, per your rule F.)*

### 4a. Every fold (G + J: all four benchmarks; matched-exposure central)

| F | Test window | Chosen (e/x/t) | Strat Ret% | Strat maxDD% | Strat Cal | Strat TIM% | HODL% | **Static-matched%** | Vol-tgt% | Fixed 20/168/6% |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 2023-07→2024-01 | 20/6/336 | +7.5 | 12.8 | 1.21 | 33.7 | +39.1 | +13.0 | +25.1 | +15.4 |
| 1 | 2024-01→2024-07 | 55/6/None | +24.3 | 12.0 | 4.55 | 23.2 | +48.5 | +11.6 | +30.0 | +35.2 |
| 2 | 2024-07→2025-01 | 100/12/504 | +5.6 | 14.5 | 0.79 | 27.7 | +47.5 | +12.9 | +33.3 | +29.5 |
| 3 | 2025-01→2025-07 | 20/6/84 | **−8.8** | 26.1 | −0.65 | 35.2 | +14.2 | +5.6 | +2.4 | +2.5 |
| 4 | 2025-07→2026-01 | 15/3/336 | −0.0 | 11.9 | 0.00 | 16.6 | −18.0 | −2.7 | −9.1 | +7.7 |
| 5 | 2026-01→2026-07 | 28/12/168 | −4.3 | 10.9 | −0.78 | 23.9 | −33.2 | −8.3 | −15.5 | +6.3 |

The **re-optimized** strategy lags HODL in 4 of 6 folds and only "wins" (folds 4–5) by sitting flat through declines. It also **lags the fixed config in 5 of 6 folds.**

### 4b. H — Parameter drift (first-class result): **no stable parameter set**

| Fold | entry | exit | trend |
|---|---:|---:|---:|
| 0 | 20 | 6 | 336 |
| 1 | 55 | 6 | None |
| 2 | 100 | 12 | 504 |
| 3 | 20 | 6 | 84 |
| 4 | 15 | 3 | 336 |
| 5 | 28 | 12 | 168 |

entry swings **15 → 100**, trend spans **{None, 84, 168, 336, 504}**, exit **3 → 12**. The Calmar-optimal params are **effectively random across folds.** Per your rule H, this is the single strongest piece of evidence in the study: **the Phase-3 in-sample plateau was a full-sample optimization-surface artifact — there is no stable optimum to relocate forward.** Stable params would have been the strongest *positive* evidence; we got the opposite.

### 4c. Aggregate OOS (stitched 2023-07 → 2026-06)

| Portfolio | Total% | CAGR% | maxDD% | **Calmar** | Sharpe | TIM% |
|---|---:|---:|---:|---:|---:|---:|
| **WF-optimized (re-tuned each fold)** | +23.1 | +7.2 | 31.8 | **0.23** | 0.43 | 26.7 |
| **Fixed 20/168/6 (I — no re-opt)** | **+137.2** | +33.4 | 16.7 | **2.00** | 1.41 | 26.9 |
| Static matched-exposure | +34.1 | +10.3 | 13.4 | 0.77 | 0.83 | 100 |
| Vol-target | +70.5 | +19.5 | 26.8 | 0.73 | 0.91 | 100 |
| HODL | +90.7 | +24.0 | 53.4 | 0.45 | 0.70 | 100 |

**The two headline OOS facts:**
1. **Re-optimization is a disaster OOS** — Calmar **0.23**, *below HODL (0.45)*, below static-matched (0.77), below vol-target (0.73). Naive periodic re-tuning **destroys** the strategy.
2. **The fixed deployed config holds up OOS** — Calmar **2.00**, maxDD 16.7%, beating HODL, **static-matched-exposure (0.77)**, and vol-target. So the timing value survives OOS *for the fixed config*, and it beats holding the same average exposure statically.

### 4d. I — the deployed config does NOT need retuning (indeed must not be retuned)

Fixed 20/168/6 OOS (+137.2%, Calmar 2.00) **massively beats** the walk-forward-re-optimized result (+23.1%, Calmar 0.23). Operationally decisive: **leave the production config alone.** Periodic re-optimization is not just unnecessary — it is actively harmful here.

### 4e. Overfit tax (revealed last, per F)

| Fold | Chosen | Train Calmar (IS) | Test Calmar (OOS) | Train Ret% (IS) | Test Ret% (OOS) |
|---|---|---:|---:|---:|---:|
| 0 | 20/6/336 | 9.18 | 1.21 | +70.3 | +7.5 |
| 1 | 55/6/None | 13.26 | 4.55 | +94.2 | +24.3 |
| 2 | 100/12/504 | 8.11 | 0.79 | +82.3 | +5.6 |
| 3 | 20/6/84 | 9.12 | −0.65 | +93.8 | −8.8 |
| 4 | 15/3/336 | 4.52 | −0.00 | +44.3 | −0.0 |
| 5 | 28/12/168 | 2.73 | −0.78 | +24.6 | −4.3 |

**Mean train Calmar 7.82 → mean test Calmar 0.85 → OOS keeps just 11% of in-sample Calmar (an ~89% overfit tax).** Against the Phase-3 full-sample in-sample optimum (Calmar 2.72 @ 20/3/336), the **WF-OOS aggregate is 0.23** — the in-sample number was almost entirely optimization luck.

### 4f. Phase 4 verdict (decision-relevant)

- **You cannot tune this strategy.** Re-optimization overfits catastrophically (89% tax), parameters are unstable across folds (no findable optimum), and the re-tuned OOS Calmar (0.23) is *worse than buy-and-hold.* The Phase-3 plateau/median were surface artifacts, not forward performance.
- **But the specific deployed 20/168/6, held fixed, generalizes OOS** — Calmar 2.00, maxDD 16.7%, beating HODL and the matched-exposure benchmarks. This is the classic *robust-but-not-optimizable* trend-following signature: a broad range of sensible fixed params captures a real drawdown-controlled-exposure benefit; optimizing among them destroys it.
- **Caveats (prominent):** (1) the fixed config's OOS win still leans on the decline-avoidance folds (3–5); in the 2023-24 bull folds it lagged HODL, same regime-conditional character as before. (2) Only 6 folds — a modest OOS sample. (3) TIM ~27% — the capital-efficiency question (Phase 6) is unchanged.

**Implication for Phase 5:** the bar for any variant is now **"beat fixed 20/168/6 on walk-forward OOS,"** and Phase 4 shows *optimized* variants tend to fail OOS. Variants must be judged by walk-forward, not in-sample, and the default expectation is that added complexity will not survive.

---

---

## PHASE 4.5 — Is 20/168/6 special, or survivorship?

You flagged the confound correctly: 20/168/6 was selected on Coinbase ~2024-2026 data that **overlaps** the Phase-4 OOS window, so "fixed 20/168/6 beats HODL OOS" is a previously-selected survivor re-tested on partially-overlapping data. Three tests. Harness `scripts/donchian_binance_phase4_5.py`.

### 1. Fixed-config cohort — all 264 configs held fixed across OOS (2023-07 → 2026-06)

HODL OOS: total +92.7%, **Calmar 0.46**, maxDD 53.4%.

| Cohort OOS metric | min | Q1 | **median** | Q3 | max |
|---|---:|---:|---:|---:|---:|
| Calmar | 0.42 | 0.70 | **0.91** | 1.21 | 2.30 |
| Total % | +35 | +60 | **+76** | +95 | +219 |

- **Median config Calmar 0.91 > HODL 0.46**, and **257/264 (97%) beat HODL on Calmar.** → The drawdown-control edge is a **robust property of the whole family**, not a thin tail. (Good news per your criterion — config choice barely matters *for the ~0.9-Calmar effect*.)
- **But only 73/264 (28%) beat HODL on total return** — the median config *lags* HODL on return (+76% vs +92.7%). The family's edge is risk-adjusted/drawdown, not return.
- **20/168/6 sits at the 97th percentile** (OOS Calmar **1.99**, total +136.9%). Its specific magnitude (~2.0 Calmar, beating HODL on return) is a **top-3% draw — that part is survivorship.** The honest family expectation is ~0.9 Calmar and return that *trails* HODL.

### 2. True holdout (select on 2022-07 → 2023-12 ONLY, trade forward 2024-01 → 2026-06) — the cleanest test

Pre-2024 data selects **(40, 6, 336)** — *not* 20/168/6.

| Config | Forward total% | CAGR% | maxDD% | **Calmar** | TIM% |
|---|---:|---:|---:|---:|---:|
| Clean-selected (40/6/336) | +55.0 | +19.2 | 19.5 | **0.98** | 20.5 |
| Fixed 20/168/6 | +105.4 | +33.4 | 16.7 | **2.00** | 25.9 |
| HODL | +38.6 | +14.0 | 53.4 | **0.26** | 100 |

- The **clean-selected config beats HODL** forward (Calmar 0.98 vs 0.26) — so **the family's drawdown-control edge is real out-of-sample** (~1.0 Calmar, matching the cohort median).
- **But clean selection lands at Calmar ~1.0, not 2.0.** 20/168/6's 2.00 is again shown to be a favorable draw that *clean, non-overlapping selection would not have found.* **The survivorship-free forward expectation is ~1.0 Calmar.**

### 3. Fold-level concentration of the fixed 20/168/6 OOS win

| Fold | Test window | Fixed Ret% | HODL Ret% | **Alpha** |
|---|---|---:|---:|---:|
| 0 | 2023-07→2024-01 | +15.4 | +39.1 | −23.8 |
| 1 | 2024-01→2024-07 | +35.2 | +48.5 | −13.3 |
| 2 | 2024-07→2025-01 | +29.5 | +47.5 | −18.0 |
| 3 | 2025-01→2025-07 | +2.5 | +14.2 | −11.7 |
| 4 | 2025-07→2026-01 | +7.7 | −18.0 | **+25.7** |
| 5 | 2026-01→2026-07 | +6.3 | −33.2 | **+39.5** |

- **Lag-folds 0–3 (2023-07→2025-07, up/chop):** fixed **+107.1%** vs HODL **+248.1%** — captured just 43% of the up-move.
- **Win-folds 4–5 (2025-07→2026-06, declines):** fixed **+14.5%** vs HODL **−45.2%**.
- **The entire OOS outperformance is carried by 2 of 6 folds (the declines).** Same concentration as Phase 2: it lags HODL in every rising fold and only wins by dodging the 2025–26 drawdowns.

### 4.5 verdict — survivorship call

**Partial survivorship, and it is the decision-relevant part:**

| Claim | Status |
|---|---|
| "Donchian family beats HODL on risk-adjusted terms OOS" | **REAL** — median config Calmar 0.91 > HODL 0.46; 97% beat; clean-holdout config 0.98. A genuine ~1.0-Calmar drawdown-control effect. |
| "20/168/6 specifically has Calmar ~2.0 / beats HODL on return OOS" | **SURVIVORSHIP** — 97th-percentile draw; clean selection finds ~1.0; return-beat is carried by 2 decline folds. |
| "The strategy can be tuned to an optimum" | **FALSE** (Phase 4: no stable params, 89% overfit tax). |

**So the honest, survivorship-free picture:** the Donchian family is a **modest, robust drawdown-control overlay worth ~1.0 Calmar OOS** (vs HODL 0.46) — real, but far below the deployed config's flattering ~2.0. It **lags HODL on total return in every rising market**, its outperformance is **concentrated in decline periods**, and it **cannot be improved by tuning.** The deployed 20/168/6's specific headline numbers should be treated as a favorable draw, not an expectation.

**Implication for Phase 5:** per your rule, this is enough survivorship in the *magnitude* that chasing variant improvements is low-value — Phase 4 already showed optimized variants fail OOS, and 4.5 shows the "impressive" config is a top-draw. **Recommend proceeding to the Phase 6 verdict rather than Phase 5 variant hunting.**

---

---

## PHASE 6 — Verdict

### 6.1 Verdict table (survivorship-free forward expectation)

Ranked over the ~3-year OOS window (2023-07 → 2026-06). The Donchian row is the **survivorship-free** expectation (family median + clean pre-2024 holdout), stated as a **range** — **not** the deployed config's flattering point estimate, which is shown separately and labeled do-not-rely.

| Portfolio | Total return (OOS ~3y) | maxDD | **Calmar** | Sharpe | TIM |
|---|---:|---:|---:|---:|---:|
| **Donchian family (forward expectation)** | **~+60% to +95%** (median +76 — *lags HODL*) | ~15–25% | **~0.9–1.1** | ~0.8–1.1 | ~20–27% |
| Static matched-exposure (~26% BTC) | +34% | 13.4% | 0.77 | 0.83 | ~26% |
| Vol-target BTC | +70% | 26.8% | 0.73 | 0.91 | 100% |
| HODL | +93% | 53.4% | 0.46 | 0.70 | 100% |
| ~~Deployed 20/168/6 (SURVIVOR — do not rely)~~ | ~~+137%~~ | ~~16.7%~~ | ~~2.00~~ | ~~1.41~~ | ~~27%~~ |

**Ranking, honestly:**
- **Calmar (risk-adjusted):** Donchian (~1.0) > static-matched (0.77) > vol-target (0.73) > HODL (0.46). The signal wins here *even survivorship-free* — and it beats matched-exposure (0.77), so the timing adds ~30% Calmar uplift over simply holding the same 26% statically. Real, but modest (not the 2.6× the survivor implied).
- **Total return:** HODL (+93) > vol-target (+70) ≈ Donchian (+76 median) > static (+34). **The Donchian family lags HODL on return.**
- **maxDD:** static (13%) < Donchian (15–25%) < vol-target (27%) < HODL (53%).
- **TIM:** Donchian lowest (~20–27%).

**One line:** it is a **modest, genuine drawdown-control overlay** (~1.0 Calmar vs HODL 0.46, and modestly above matched-exposure), **not a return edge** — it trails HODL whenever BTC rises and earns its keep only by dodging declines.

### 6.2 What NOT to deploy, and why

- **Do NOT re-optimize / retune — ever.** Walk-forward re-tuning collapses to **Calmar 0.23 (below HODL 0.45)**; per-fold params are unstable (entry 15→100, trend None→504); the **overfit tax is ~89%** (mean train Calmar 7.82 → OOS 0.85, keeps 11%). Retuning is not neutral — it is *actively destructive.* Hold 20/168/6 (or any sensible fixed config) fixed.
- **Do NOT report or rely on the deploy log's "+25.89% alpha vs HODL."** It is an **in-sample, full-sample** number on data **overlapping the selection window**. Replace it with §6.3.
- **Rule out preemptively:** per-fold adaptive params; "faster-exit optimization," partial-sizing, whipsaw-re-entry *as tuned improvements* (Phase 4 shows optimized variants fail OOS); treating the chop cell as an edge (refuted — label-dependent, 2-trade artifact); and **leverage** — *out of scope*: this is a **spot-only division**, so leverage is not available and not under consideration. (This is a scope fact, not a signal-quality judgment — the signal was not evaluated for lever-ability.)

### 6.3 Corrected division expectation (paste-ready — REPLACES the in-sample headline)

```
# Coinbase BTC Donchian (20/168/6, 6h) — VALIDATED EXPECTATION (Binance 4Y re-backtest, 2026-07-19).
# SUPERSEDES the "+25.89% alpha vs HODL" headline: that was in-sample, full-sample, on data
# overlapping the selection window — DO NOT rely on it.
# This is a drawdown-CONTROL overlay, not a return edge. Holds BTC only ~20-26% of the time.
# Survivorship-free forward expectation (rolling walk-forward + clean pre-2024 holdout):
#   Calmar ~0.9-1.1 (vs HODL ~0.46) — real but modest, drawdown-driven.
#   LAGS HODL on total return in rising markets (captures ~40% of bull upside).
#   Wins only by sitting in cash during declines; outperformance is decline-concentrated.
#   maxDD ~15-25% (vs HODL ~53%); time-in-market ~20-26%.
# Regime: bull -> lags HODL; bear -> outperforms (cash); chop -> ~neutral (NO chop edge).
# DO NOT RE-OPTIMIZE. Walk-forward re-tuning fails OOS (Calmar 0.23, below HODL) with an ~89%
# overfit tax and unstable params. Hold 20/168/6 FIXED. Deployed config's ~2.0 Calmar is a
# top-3% survivorship draw — plan around ~1.0.
```

### 6.4 Capital efficiency — the open question (arguments + math, no recommended number)

At ~26% time-in-market on an **$85K sleeve**, the position is binary (100% in / 100% out); time-averaged that is **~$22K in BTC, ~$63K in cash**, and the cash currently earns **0%** (USD/USDC on Coinbase).

- **Opportunity cost of the idle cash (signal-independent):** out ~74% of the time → a liquid yield instrument at ~4.5% would add **≈ 0.74 × 4.5% ≈ +3.3%/yr on the sleeve (~+$2,800/yr on $85K) at ~0 added risk.** Pure "free" pickup, untouched today.
- **Absolute $ profile:** $85K at the survivorship-free ~1.0-Calmar / ~+19% CAGR expectation ≈ **~$15–17K/yr, regime-dependent** — *less* than a HODL of the same $85K in bull years (HODL CAGR ~24–31%), *more* in down years, with **~⅓ the drawdown**.

| Option | For | Against |
|---|---|---|
| **Leave as-is** | Simplest; drawdown-controlled; no new risk | Capital-inefficient — ~74%-of-time cash at 0% yield; lags HODL return |
| **Yield-ify idle cash** (money-market / T-bill / USDC-yield, swept when flat) | ~+3%/yr on the sleeve at ~0 risk; signal untouched; 6h cadence leaves ample time to redeem before a BUY | Operational plumbing; must stay liquid enough to go 100%-in on signal |
| **Resize the sleeve** (commit less $) | Frees capital for higher-Calmar uses; scaling is linear (Calmar unchanged) | Shrinks absolute $ P&L; the real question is how much $ a ~1.0-Calmar drawdown-control play deserves |
| ~~Leverage the signal~~ | *Out of scope* | **Spot-only division — leverage is not available and not under consideration.** (Scope fact, not a signal-quality judgment.) |

The two levers are (1) **how much capital** a ~1.0-Calmar profile deserves and (2) **whether to yield-ify the idle cash** — both your call.

### 6.5 What would change the answer

**Would UPGRADE (worth research time):**
- **A clean forward track** past 2026-06 (live/paper) — the only fully overlap-free OOS sample; accumulating real fills settles the survivorship question directly.
- **An older/longer corpus (2017–2022)** covering the 2018 bear + 2021 bull — more independent cycles, more folds.
- **A structurally different exit** (ATR/chandelier trailing stop, or a daily-timeframe version) that could lift bull-capture from ~40% toward HODL *without* reintroducing drawdown — that would turn a drawdown-overlay into a genuine edge. Must be walk-forward-validated; prior is skeptical (optimized variants failed OOS).

**Would DOWNGRADE:**
- A **relentless-bull forward period** (2020–21 style) with no declines to dodge — the Calmar edge would shrink toward HODL, since drawdown-avoidance has nothing to avoid.
- **Live slippage materially worse than modeled** (>10 bps) or execution/HITL degradation eroding the thin edge.

**Net research guidance:** the edge is real-but-modest and **not improvable by tuning.** Highest-value next steps are a clean forward track and *one* strictly-walk-forward exit-structure test (ATR/daily). Absent those, **leave the division alone** — do not spend more optimization effort.

### 6.6 Residual risks from the earlier audits (execution gates this regardless of the backtest)

Everything above assumes execution works — it does not, yet. The earlier ownership/isolation audit found the division **sizes off the whole live commingled account** (BUY = 100% of account cash, SELL = 100% of account BTC), the **risk gate evaluates against a synthetic $100K equity** blind to the real balance, and the account is **actively commingled with Board treasury flows** (cash $0–88K, BTC 0–1.35 over the window). These are **unresolved and gate any live activation regardless of signal merit** — even a perfect signal would transact the wrong (Board) money, and `auto_execute` cannot be safely enabled until a dedicated sub-account / real-equity-into-the-risk-gate isolation is in place. The strategy question ("is the signal worth trading?" → *modest yes, as drawdown control*) and the execution question ("can it be traded safely today?" → *no*) are separate, and the latter dominates.

---

## PHASE 7 — Timeframe structural test (12h / 1d / 3d / 1w vs 6h)

Bar interval is a *structural* choice, not a tuned parameter — but it is still a new search dimension, so same discipline (cohort median + clean holdout as the conclusion, not the best config). All timeframes derived uniformly from 1h (N-hour epoch buckets, complete buckets only). Harness `scripts/donchian_binance_phase7.py`.

**Grid scaling (justified):** the 6h grid is expressed in *calendar days* (entry 2.5–25d, trend 21–180d + None, exit 0.75–5d — which reproduces the 6h integer grid exactly at 4 bars/day), then each TF's grid is those same day-targets × its bars/day, rounded to integers (entry ≥ 2, exit ≥ 1, exit < entry). So 6h entry=20 (5d) ↔ daily entry≈5, weekly entry≈1–2, etc. **Not** the reused 6h integer grid. maxDD-disqualification applied at selection (all selected/clean configs draw down < HODL).

### 7a. Cohort — family distribution per TF (median = headline; OOS 2023-07→2026-06)

HODL OOS Calmar ≈ 0.46–0.53 across TFs.

| TF | grid | med RT (range) | OOS Calmar min/Q1/**median**/Q3/max | med total% | med TIM% | %beat HODL Calmar | %beat HODL ret |
|---|---:|---:|---|---:|---:|---:|---:|
| **6h** | 264 | 42 (20–123) | 0.42 / 0.70 / **0.91** / 1.21 / 2.30 | +76 | 24.6 | **97%** | 28% |
| 12h | 216 | 36 (18–91) | 0.06 / 0.41 / **0.60** / 0.81 / 1.21 | +51 | 26.7 | 65% | 4% |
| 1d | 162 | 30 (16–78) | 0.04 / 0.44 / **0.68** / 0.92 / 1.90 | +57 | 28.8 | 73% | 6% |
| 3d | 54 | 18 (12–28) | 0.29 / 0.36 / **0.45** / 0.55 / 1.07 | +50 | 37.3 | 46% | 0% |
| 1w | 18 | 11 (9–13) | 0.30 / 0.52 / **0.55** / 0.71 / 1.27 | +72 | 45.2 | 67% | 6% |

**6h has the highest family-median Calmar (0.91); every slower TF is worse.** None reaches ~1.0. **3d and 1w are underpowered** (median 18 and 11 round-trips; 1w only 9–13 total) — treat their numbers as indicative, not conclusive.

### 7b. Clean holdout per TF (select 2022-07→2023-12, trade forward 2024-01→2026-06)

| TF | selected cfg | fwd total% | Calmar | maxDD% | TIM% | RT | HODL total/Cal | static-matched% | bull-capture% |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| **6h** | (40/6/336) | +55.0 | **0.98** | 19.5 | 20.5 | 37 | +38.6/0.26 | +12.2 | 23 |
| 12h | (20/3/168) | +21.3 | 0.34 | 23.7 | 20.1 | 34 | +37.2/0.25 | +12.1 | 13 |
| 1d | (10/1/84) | +8.6 | **0.14** | 23.3 | 18.6 | 33 | +32.5/0.23 | +10.5 | 5 |
| 3d | (3/2/42) | −1.8 | −0.02 | 40.6 | 39.9 | 16 | +36.4/0.26 | +23.1 | 49 |
| 1w | (2/1/18) | +60.0 | 0.95 | 22.3 | 42.6 | 10 | +30.8/0.23 | +23.8 | 163 |

**6h's clean-holdout Calmar (0.98) is the best; daily's is the worst (0.14).** The 1w clean config (0.95) is a small-sample draw (family median only 0.55, RT=10) and its "163% bull-capture / 42.6% TIM" reflects that its params (entry 2w / trend 18w / exit 1w) make it **near-HODL** — barely trading — not a better drawdown-control strategy.

### 7c. Walk-forward parameter stability per TF — unstable at *every* timeframe

| TF | chosen per fold (e/x/t) | entry range | trend set | exit range |
|---|---|---:|---|---:|
| 6h | 20/6/336 · 55/6/∅ · 100/12/504 · 20/6/84 · 15/3/336 · 28/12/168 | 15–100 | {∅,84,168,336,504} | 3–12 |
| 12h | 5/4/360 · 20/3/84 · 5/4/84 · 5/4/∅ · 8/3/168 · 8/6/84 | 5–20 | {∅,84,168,360} | 3–6 |
| 1d | 7/3/180 · 10/1/42 · 14/5/126 · 14/2/84 · 7/3/21 · 4/1/42 | 4–14 | {21,42,84,126,180} | 1–5 |
| 3d | 3/2/42 · 3/2/14 · 3/2/42 · 3/1/60 · 5/1/60 · 6/1/28 | 3–6 | {14,28,42,60} | 1–2 |
| 1w | 2/1/18 · 2/1/∅ · 2/1/∅ · 2/1/12 · 4/1/∅ · 3/1/∅ | 2–4 | {∅,12,18} | 1 |

The trend filter still swings across its whole range at every TF (e.g. daily trend 21↔180). The *apparent* narrowing at 3d/1w is a **grid-size + small-sample artifact** (18 and 54 combos), not genuine stability. **Phase 4's "no stable optimum" holds at all timeframes.**

### 7d. Decline-concentration persists at every TF (clean-config per-fold alpha vs HODL)

| TF | fold 0 | 1 | 2 | 3 | 4 | 5 | wins in |
|---|---:|---:|---:|---:|---:|---:|---|
| 6h | −17.5 | −22.5 | −26.0 | −16.1 | +24.3 | +30.4 | 4–5 (declines) |
| 12h | −1.0 | −39.6 | −31.1 | −21.3 | +24.5 | +29.3 | 4–5 |
| 1d | +2.7 | −37.9 | −32.7 | −26.1 | +23.2 | +31.3 | 4–5 |
| 3d | +6.8 | −4.7 | −49.1 | −19.7 | −6.3 | +30.6 | 5 |
| 1w | −21.1 | −12.7 | −29.6 | −1.0 | +11.9 | +32.3 | 4–5 |

**Every timeframe lags HODL in the 2023–24 bull folds and wins only in the 2025–26 decline folds.** Slower timeframes do **not** distribute the edge more evenly.

**Slippage robustness (daily family-median Calmar):** 0.72 / 0.68 / 0.68 / 0.65 / 0.60 at 0/2/3/5/10 bps — stable, but always below 6h's 0.91.

### 7e. Answers to the direct questions

| Question | Answer |
|---|---|
| Any TF raises survivorship-free family median Calmar above ~1.0? | **No.** 6h is highest at 0.91; all slower TFs are 0.45–0.68. |
| Slower TF raises TIM materially at *similar Calmar*? | **No.** TIM rises (1w 45%) only by *lowering* Calmar (1w 0.55). Daily = 28.8% TIM at 0.68 Calmar — both worse than 6h. The 26%-TIM capital problem is **not** solved by timeframe. |
| Slower TF reduces bull-market lag? | **No genuine reduction.** 12h/1d bull-capture (13%/5%) is *worse* than 6h (23%); 1w's 163% is just because its config is near-HODL (barely trades, tiny sample). |
| Outperformance more evenly distributed at slower TF? | **No.** Decline-concentrated at every TF (§7d). |
| Trade counts / small samples? | 6h best-powered (med RT 42). **3d (18) and 1w (11, range 9–13) are underpowered — do not conclude from them.** |

### 7f. Phase 7 verdict — null result

**6h is as good as or better than any other timeframe, and slower timeframes do not help.** No interval raises the survivorship-free family median Calmar to ~1.0 (6h's 0.91 is the best), none fixes the ~26% TIM capital-efficiency problem without degrading Calmar, none reduces the bull-market lag while keeping the drawdown-control character, and the decline-concentration + parameter-instability are universal. The §6.5 "try daily" lead is **refuted** — daily is *worse* (family median 0.68, clean-holdout 0.14, bull-capture 5%). **The timeframe is not a lever; the Phase 6 verdict stands unchanged, and there is no structural-timeframe research left to do here.**

---

## Study conclusion

The Coinbase BTC Donchian is a **modest, genuine, non-tunable drawdown-control overlay** (6h is the best interval — Phase 7 found no faster or slower timeframe improves the survivorship-free family median Calmar or fixes the TIM/bull-lag weaknesses) — survivorship-free Calmar ~1.0 (vs HODL 0.46, and modestly above matched-exposure ~0.77), lagging HODL on return in rising markets and earning its keep only by dodging declines. The deployed 20/168/6's ~2.0 Calmar / +25.89% headline are **survivorship-inflated in-sample artifacts** and should be replaced (§6.3). It **must not be re-optimized** (89% overfit tax). Whether to keep, resize, or yield-ify the ~$85K sleeve is a capital-allocation decision (§6.4). **The single highest-certainty, highest-value action is unrelated to the signal: yield-ifying the ~74% idle cash captures ~$2,800/yr at near-zero risk — independent of the sizing and isolation decisions, and doable whether or not the strategy ever activates.** Live activation of the *strategy* is **blocked by the unresolved isolation risks** (§6.6) irrespective of the backtest.

*Report complete. Saved to `reports/2026-07-19_donchian_binance_revalidation.md`; not committed. Research harnesses: `scripts/donchian_binance_{revalidation,phase2,phase3,phase4,phase4_5}.py` (untracked).*
