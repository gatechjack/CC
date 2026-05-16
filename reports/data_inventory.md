# Data Inventory — `data/btc_scalping.db`

Generated 2026-05-16. Source script: `Goals/scratch/02_inventory.py`.
Backing JSON: `Goals/scratch/inventory.json`.

---

## 1. Database state

| Table | Rows | First bar (UTC) | Last bar (UTC) | Span | Columns |
|---|---:|---|---|---:|---:|
| `bars_3m` | 22,635 | 2026-03-30 00:00 | 2026-05-16 03:42 | **47.15 days** | 93 |
| `bars_15m` | 15,571 | 2025-12-04 19:00 | 2026-05-16 03:30 | ~163 days | 93 |
| `bars_30m` | 18,653 | 2025-04-22 12:30 | 2026-05-16 03:30 | ~389 days | 93 |
| `bars_1h` | — | — | — | — | **NOT PRESENT** |
| `bars_4h` | — | — | — | — | **NOT PRESENT** |
| `bars_1d` | — | — | — | — | **NOT PRESENT** |
| `source_files` | 14 | — | — | — | ingest log |

> ⚠ **`bars_4h` and `bars_1d` were retired** in the 2026-05-16 02:25 UTC ingest (per BACKLOG end-of-session snapshot — the old tables were obsolete after PR 3c shifted scoring to `[3m, 15m, 30m]`). The `analyze_btc_scalping_3m.py` script's HTF-bias logic was written against `bars_4h` + `bars_1d` and **no longer runs**. For this work, **HTF bias is derived from `bars_30m`** (the longest-window table available).

Realized volatility (for sizing context):
- 1-bar (3m) log-return std: **0.089%** (≈ 8.9 bps per 3m bar)
- 20-bar (60m) log-return std: **0.383%**

---

## 2. Schema map — goal description → actual column

The goal document described TradingView indicator suite "Market Cypher" + "Lord Otter" with specific names. The actual TV-export ingest produces different column names. This table maps each goal-doc name to the real DB column (or flags absence). All subsequent strategy work uses **real DB column names**.

### Market Cypher A-panel

| Goal name | DB column | Status |
|---|---|---|
| `mc_a_longema` | `long_ema_signal` | ✓ exists (214 fires) |
| `mc_a_bluetriangle` | `blue_triangle` | ✓ exists (548 fires) |
| `mc_a_redx` | `red_cross` | ✓ exists (985 fires) — goal says "soft bearish"; DB side annotation in `eda_btc_scalping_signals.py` agrees ("bear") |
| `mc_a_yellow_x` | `yellow_cross` | ✓ exists (44 fires — **rare, flagged**) — goal says "bearish warning"; `eda_btc_scalping_signals.py` labels as "bull". **DISCREPANCY** — went with goal-doc semantics (bear) but n is too small to matter for any backtest anyway |
| `mc_a_red_diamond` | `red_diamond` | ✓ exists (2229 fires) |
| `mc_a_blood_diamond` | `blood_diamond` | ✓ exists (272 fires) |

### Market Cypher B-panel

| Goal name | DB column | Status |
|---|---|---|
| `mc_b_gold_buy` | `gold_buy_gold_circle` | ✓ exists (19 fires — **very rare**) |
| `mc_b_buy_circle_div` | `divergence_buy_circle` | ✓ exists (202 fires) |
| `mc_b_buy_circle` | `buy_circle` | ✓ exists (557 fires) |
| `mc_b_buy_dot` | — | ✗ **NOT IN DB** |
| `mc_b_sell_circle_div` | `divergence_sell_circle` | ✓ exists (235 fires) |
| `mc_b_sell_circle` | `sell_circle` | ✓ exists (564 fires) |
| `mc_b_sell_dot` | — | ✗ **NOT IN DB** |
| `mc_b_sommi_bull` | `sommi_bullish_flag` | ✗ **0 fires** (column exists, never populated) |
| `mc_b_sommi_bear` | `sommi_bearish_flag` | ✗ **0 fires** |

All five `sommi_*` columns are empty. The Vumanchu indicator on the source TV chart isn't emitting Sommi VWAP-regime state. Cannot use `mc_b_sommi_*` as a filter.

### Lord Otter

| Goal name | DB column | Status |
|---|---|---|
| `otter_buy` / `otter_sell` | `otter_buy` / `otter_sell` | ✓ exact match (40 / 82 fires — **rare on 3m**) |
| `spoon_bull` / `spoon_bear` | — | ✗ **NOT IN DB**. Closest analog: WT divergences on 3m (`wt_bullish_divergence`, `wt_2nd_*_divergence`). Goal describes spoon as "price-vs-CVD divergence"; these are price-vs-WaveTrend, semantically related but not identical. |
| `water_buy_small` / `water_buy_large` (and sell counterparts) | — | ✗ **NOT IN DB**. No multi-TF-alignment Otter signal in the export. Closest analog must be derived from joining `bars_15m` / `bars_30m` `long_ema_signal` to `bars_3m` (manual confluence). |
| `money_bag_top` / `money_bag_bottom` | `top_signal` / `bottom_signal` | ✓ semantically similar (Otter exhaustion reversal triggers; n=49 / n=22). Used as analog. |
| `cvd_bull_flip` / `cvd_bear_flip` | `cvd_flip_bullish` / `cvd_flip_bearish` | ✓ exact (542 / 542 fires) |
| `bias_bull` / `bias_bear` | — | ✗ **NOT IN DB**. Must derive from `bars_30m` `long_ema_signal` cross (or 30m SMA), or from the divergence-event-with-decay logic on `bars_30m`. |
| `ribbon_exhaustion_bull` / `ribbon_exhaustion_bear` | — | ✗ **NOT IN DB**. `ribbon_buy_cross` / `ribbon_sell_cross` exist (2089 / 2200 fires) but those are EMA-stack flips, NOT exhaustion. Treat ribbon crosses as state filters, not exhaustion. |

### Extra columns the goal didn't mention (but exist and may be useful)

- `super_buy_high` / `super_sell_high` / `super_buy_std` / `super_sell_std` — rare Otter "super" triggers (22 / 47 / 4 / 7 fires). Treated as rare conviction tags.
- `bull_divergence` / `bear_divergence` — generic divergence (168 / 156 fires). Strong per-fire returns; included in candidate sets.
- Continuous indicators: `atr`, `rsi`, `wt_wave_1`, `wt_wave_2`, `macd`, `signal_line`, `histogram`, `vwap`, `cvd_close`, `donchian_high_entry_channel`, `donchian_low_exit_channel`, `trend_filter_sma`, `volume`. All ≥99.8% non-null. Available for filters and ATR-based stops.

---

## 3. Indicator firing frequencies and forward-return profile

The full table is in `Goals/scratch/inventory.json`. Summary: 40 indicator columns inventoried (out of 47 with `_fires`/event-shape semantics). Below are the most decision-relevant subsets.

**Firing-frequency flags:**
- **Rare (<50 fires in 47 days)** — too thin to backtest alone, but usable as conviction overlays: `yellow_cross` (44), `gold_buy_gold_circle` (19), `otter_buy` (40), `super_buy_high` (22), `super_sell_high` (47), `super_buy_std` (4), `super_sell_std` (7), `top_signal` (49), `bottom_signal` (22).
- **State-like (>20% of bars)** — these are NOT events; they were misclassified by my non-null/non-zero filter: `vpmo_glow` and `vpmo` (99.94% of bars), `money_flow_glow` and `money_flow_signal` (99.98%). **Their 5-bps + 99%-hit numbers below are an artifact of BTC drift over this window, NOT predictive edge. Exclude from event-driven strategies.**
- **`buy_and_sell_circle`** (19.98% of bars) — combined buy/sell circle marker; similar drift artifact. Don't use as a directional event.

### Cleanest "real-looking" signals (event-shape, plausible edge)

Sorted by absolute h5 (15-min) mean directional return:

| Column | Side | n_fires | h5 hit% | h5 mean (bps) | h10 mean (bps) | h20 mean (bps) | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| `divergence_sell_circle` | bear | 235 | **67.7%** | +7.16 | +6.82 | +5.11 | Strongest sell-side B-panel |
| `divergence_buy_circle` | bull | 202 | 57.9% | +4.94 | +5.22 | +7.07 | |
| `wt_2nd_bullish_divergence` | bull | 664 | 60.8% | +4.88 | +5.44 | +6.12 | High-n, monotonic edge across horizons |
| `wt_2nd_bearish_divergence` | bear | 1056 | 60.8% | +3.98 | +2.18 | +1.61 | Fades after 15m |
| `cvd_flip_bullish` | bull | 542 | 51.3% | +2.74 | +2.75 | +5.14 | Slow-burning |
| `ribbon_buy_cross` | bull | 2089 | 52.7% | +1.59 | +3.09 | +5.95 | Trend-follow on 3m EMA stack |
| `bull_candle` | bull | 2320 | 47.5% | +0.96 | +1.14 | +2.09 | Marginal |
| `red_diamond` | bear | 2229 | 49.4% | -0.46 | -1.17 | -1.57 | Close-longs trigger per goal doc — weak edge as standalone short |
| `blood_diamond` | bear | 272 | 48.5% | -1.03 | -0.42 | -4.23 | h20 looks decent |

### Look-ahead suspects (must validate before trusting)

| Column | Side | n_fires | h1 hit% | h1 mean (bps) | Risk |
|---|---|---:|---:|---:|---|
| `rsi_bullish_divergence` | bull | 203 | **100.00%** | +9.26 | Repaint near-certain |
| `rsi_bearish_divergence` | bear | 1179 | **100.00%** | +7.19 | Repaint near-certain |
| `stoch_bullish_divergence` | bull | 1739 | 85.7% | +6.09 | Repaint likely |
| `stoch_bearish_divergence` | bear | 2155 | 89.3% | +6.73 | Repaint likely |
| `bull_divergence` | bull | 168 | 95.8% | +8.03 | Repaint suspect |
| `bear_divergence` | bear | 156 | 98.7% | +9.45 | Repaint suspect |

A 100% h1-hit-rate is a flashing red light. Will run an entry-shifted-by-+1-bar replay during backtest; if expected edge survives, signal is real, if it collapses, signal repaints. The h5/h10/h20 numbers degrading-but-not-collapsing supports the repaint hypothesis (later horizons leak less from the repaint into the observed return).

> **Caveat in the ingester docstring (`scripts/ingest_tv_export.py:11-13`):** "TradingView occasionally repaints recent bars. Re-running with an overlapping window UPSERTs by ts. Newer indicator values overwrite older ones for the same bar." Confirms the repaint vector exists.

### CVD direction and ribbon-state info

`cvd_close` is populated 100%; `cvd_flip_bullish` / `cvd_flip_bearish` are 2.4% events each. Suitable for both the "did CVD flip recently" event check and the "what direction is CVD trending" state check (compare `cvd_close` to its lagged value, per `analyze_btc_scalping_3m.detect_3m_volume_confluence`).

`ribbon_buy_cross` / `ribbon_sell_cross` fire ~9% of bars. Combined they're ~19% of bars, which means the ribbon flips state about every 11 bars on average — too noisy to use as a trigger but possibly OK as a "is the ribbon on our side right now?" state check derived from "which cross was last".

---

## 4. Forward-return correlation summary

For each indicator, Pearson correlation between the binary fire indicator (0/1) and forward log-return at h ∈ {1, 5, 10, 20}. Reported in `inventory.json` as `h{1,5,10,20}_corr`.

The largest absolute correlations:
- `stoch_bearish_divergence` h1: **-0.249** (repaint-amplified)
- `rsi_bearish_divergence` h1: **-0.192** (repaint-amplified)
- `stoch_bullish_divergence` h1: **+0.196** (repaint-amplified)
- `wt_2nd_bearish_divergence` h1: **-0.095** (cleaner, more credible)
- `wt_2nd_bullish_divergence` h1: **+0.071**
- `bear_divergence` h1: **-0.090** (repaint-suspect)
- `bull_divergence` h1: **+0.078** (repaint-suspect)
- `rsi_bullish_divergence` h1: **+0.099** (repaint-amplified)

Correlations are small in absolute terms (BTC microstructure is noisy at 3m), but the pattern matches the table above: the cleanest signals are the WT-2nd divergences, and the repaint-suspects show inflated h1 correlations that decay across horizons.

---

## 5. HTF bias derivation plan

Because `bars_4h` and `bars_1d` are gone, **30m EMA-stack flips will be used as the HTF bias proxy**. Specifically, on `bars_30m`:

- `long_ema_signal` event → bias flips to `bull` (decay 24h)
- `short_ema_signal` event → bias flips to `bear` (decay 24h)
- Latest active event wins; both expired → `neutral`

This mirrors the analyze-script's bias-events-with-decay pattern but uses 30m EMA-cross events (which fire 1-2/day on 30m) instead of 4h/1d divergence events.

This is a **deliberate methodology change** from the original analyze script, justified by data availability. Logged in `decision_log.md`.

---

## 6. What was deliberately excluded

- **`vpmo`, `vpmo_glow`, `money_flow_glow`, `money_flow_signal`, `buy_and_sell_circle`** — fire on 99%+ / 20% of bars. State-like, not events. Treating them as triggers would dominate any signal-and-state composite with noise.
- **All `sommi_*`** — 0 fires.
- **`rsi_*_divergence`, `stoch_*_divergence`, `bull_divergence`, `bear_divergence`** — kept in scope but only after the +1-bar shift validates them. If they collapse to near-zero edge under the shift, they're excluded from candidate strategies.
- **Continuous columns (`ema_*`, `wt_wave_*`, `rsi`, `macd`, `vwap`, `cvd_close`, `donchian_*`)** — used as inputs to derived states / filters / stops, not as standalone signals.

---

## 7. Open follow-ups (will resolve in next step)

1. **Repaint validation.** For each suspect column (rsi/stoch divergences + bull/bear divergence), backtest with entry shifted to bar `t+1`. If h5 mean drops by >50% under shift, exclude from strategy primary triggers; relegate to confirmation-only.
2. **Bias-derivation soundness check.** Spot-check whether 30m EMA-flip bias correlates with directional outcomes on 3m bars (i.e. is "bias_30m=bull at trigger time" predictive of positive 3m forward returns?). If not, use a simpler 30m SMA slope.
3. **Goal indicators absent from DB.** Will produce strategies only from real columns. `mc_b_*_dot` and `water_*` strategies cannot be built without those columns.
