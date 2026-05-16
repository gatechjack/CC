# Strategy Hypotheses

Seven candidate strategies, each grounded in the schema from `data_inventory.md` and the layered-thinking principles from the goal doc:
- **Context (state):** HTF bias derived from `bars_30m` EMA flips, with 24h decay.
- **Arming (event in window):** divergence / spoon-analog events in last N bars.
- **Primary trigger (event this bar):** B-panel circles, Otter primary, top/bottom reversal triggers.
- **Confirmation (event same/next bar):** CVD flip, ribbon cross.

Each hypothesis specifies its thesis, the actual DB columns it uses, the entry/exit rule, and why I expect it to work *and* what would falsify it.

Notes on common conventions:
- Position is `{-1, 0, +1}` on each 3m bar. Long = +1, Short = -1, Flat = 0.
- All trades carry **14 bps round-trip cost** (5 bps taker/side + 2 bps slippage). Applied at entry and exit.
- Default exit policy unless otherwise stated: **fixed time stop = 20 bars (60 min)**, **ATR stop = entry ± 1.5 × ATR(14)**, **ATR target = entry ± 3.0 × ATR(14)** — whichever fires first.
- For LONG entries the bar's signal is checked at bar close; position assumed taken on **next bar's open** (no same-bar look-ahead).
- `is_bias_bull(ts) := bias_30m_at(ts) == "bull"`, derived by walking `bars_30m` `long_ema_signal` / `short_ema_signal` events with 24h decay window.
- `arm_within(col, N)` := "did `col` fire on any of the last N 3m bars (excluding current)".
- `flip_within(cvd_side, N)` := "did `cvd_flip_bullish` (or bearish) fire on any of the last N bars".

---

## H1 — Trend-following: Ribbon-cross + bias filter

**Family.** Trend-following with regime filter.

**Thesis.** `ribbon_buy_cross` shows a real but slow edge (h20 mean +5.95 bps, n=2089, hit 55.5%). Filtering by 30m bias should improve hit rate by skipping ribbon flips against the higher-timeframe trend (which are typically failed bounces in downtrends, or shakeouts in uptrends). This is a low-conviction high-frequency strategy — needs the filter to survive costs.

**Layers.**
- Context: `is_bias_bull(t)` for longs, `is_bias_bear(t)` for shorts.
- Trigger: `ribbon_buy_cross[t]` (long), `ribbon_sell_cross[t]` (short).
- Exit: opposite ribbon cross OR 20-bar time stop OR ±1.5 ATR stop.

**Pseudocode.**
```
LONG  := ribbon_buy_cross[t]  AND is_bias_bull(t)
SHORT := ribbon_sell_cross[t] AND is_bias_bear(t)
```

**Falsification.** If the bias filter does NOT improve hit rate vs raw ribbon cross (i.e., 30m EMA bias has no edge over the 47-day window), this collapses. Test in regime-breakdown.

---

## H2 — Mean reversion: WT-2nd-divergence reversal

**Family.** Mean-reversion at oversold/overbought extremes.

**Thesis.** `wt_2nd_bullish_divergence` (n=664, h5 hit 60.8%, mean +4.88 bps) and `wt_2nd_bearish_divergence` (n=1056, h5 hit 60.8%, mean +3.98 bps) are the strongest "real" (non-repaint-suspect) divergence signals on 3m. The "2nd" suffix means it's the higher-conviction Vumanchu WaveTrend divergence variant. As mean-reversion bets we expect: enter on the divergence fire, take profit on a few-bar bounce, cut losses fast. The h5 edge degrades by h10/h20 (esp. bearish: +3.98 → +2.18 → +1.61 bps), consistent with a short-lived reversal play.

**Layers.**
- Context: none (mean-reversion is supposed to work in any regime — we'll test this).
- Trigger: `wt_2nd_bullish_divergence[t]` (long), `wt_2nd_bearish_divergence[t]` (short).
- Exit: fixed time stop = **5 bars** (matching the h5 edge profile) OR ±1.5 ATR stop.

**Pseudocode.**
```
LONG  := wt_2nd_bullish_divergence[t]
SHORT := wt_2nd_bearish_divergence[t]
```

**Falsification.** If h5 mean evaporates after costs (14 bps round-trip vs +4-5 bps gross), strategy dies. This is the cost-survival stress test.

---

## H3 — Capitulation tag: gold-circle + divergence-circle

**Family.** Mean-reversion capitulation (extreme + confirmation).

**Thesis.** Goal § Indicator Semantics says `mc_b_gold_buy` is "RSI<30 + WT extreme + divergence — rare capitulation." DB column is `gold_buy_gold_circle` (n=19, h5 mean +12.47 bps). Too rare standalone to clear the 100-trade minimum, so this hypothesis BROADENS the trigger to all three highest-conviction B-panel buy/sell circles to assemble a tradeable n.

**Layers.**
- Context: none (capitulation is the context).
- Trigger ANY of: `gold_buy_gold_circle[t]` OR `divergence_buy_circle[t]` (long) // mirror for shorts: `divergence_sell_circle[t]`.
- Exit: 10-bar time stop OR ±2.0 ATR target / ±1.0 ATR stop (asymmetric — these signals get sharp bounces).

**Pseudocode.**
```
LONG  := gold_buy_gold_circle[t] OR divergence_buy_circle[t]
SHORT := divergence_sell_circle[t]   # no "gold_sell" column in DB
```

**Falsification.** If divergence_buy_circle alone (without the gold tag) shows materially weaker per-trade EV than the combined trigger, the gold tag IS the alpha and the broader set is noise. Will inspect.

---

## H4 — Divergence-armed Otter entry

**Family.** Divergence-armed entry (the goal's "spoon → otter" template, adapted).

**Thesis.** The goal asks for "spoon_bull within last N bars AND otter_buy this bar". DB has no `spoon_*`, so I substitute the closest analog — a recent WT-1st-divergence (the regular `wt_bullish_divergence` / `wt_bearish_divergence`) — as the arming event, and Otter as the trigger. Rationale: WT divergence sets up the "something's off" context; Otter primary fires to confirm.

**Layers.**
- Context: none.
- Arm: `wt_bullish_divergence` fired within last 5 bars (long) / `wt_bearish_divergence` for short.
- Trigger: `otter_buy[t]` (long) / `otter_sell[t]` (short).
- Exit: opposite Otter signal OR 20-bar time stop OR ±1.5 ATR stop.

**Pseudocode.**
```
LONG  := otter_buy[t]  AND arm_within(wt_bullish_divergence, 5)
SHORT := otter_sell[t] AND arm_within(wt_bearish_divergence, 5)
```

**Falsification.** Otter rarity (40+82 fires) means the AND-armed n will be very small. If trades < 100, strategy fails the minimum-validity bar from the goal. May need to relax arming window OR widen the arming event set.

---

## H5 — Cypher A-regime + B-trigger + Otter confluence

**Family.** Multi-source confluence (Cypher A-panel regime + B-panel timing + Otter primary). The goal's "Cypher-Otter confluence" template.

**Thesis.** A-panel state ⊕ B-panel divergence ⊕ Otter primary should fire rarely (good — sets a high bar) and align three independent indicator families. Each independent confirmation should compound the per-trade hit rate even if individual signals are 50-55% hit rate.

**Layers.**
- Context: A-panel — `blue_triangle` fired within last 30 bars (long: bull-flip warning) / `red_diamond` within last 30 bars (short: close-longs context).
- Arming: B-panel — `divergence_buy_circle` within last 10 bars (long) / `divergence_sell_circle` within last 10 bars (short).
- Trigger: Otter primary — `otter_buy[t]` (long) / `otter_sell[t]` (short).
- Exit: A-panel close trigger — `red_diamond` fires (closes longs) / `blue_triangle` fires (closes shorts), OR 20-bar time stop, OR ±2.0 ATR stop.

**Pseudocode.**
```
LONG  := otter_buy[t]
         AND arm_within(divergence_buy_circle, 10)
         AND arm_within(blue_triangle, 30)
SHORT := otter_sell[t]
         AND arm_within(divergence_sell_circle, 10)
         AND arm_within(red_diamond, 30)

EXIT longs on red_diamond OR stop/target/time
EXIT shorts on blue_triangle OR stop/target/time
```

**Falsification.** Given Otter rarity and 30-bar / 10-bar window stacking, expect VERY low trade count. If n < 30, this is unbacktestable on the current dataset and gets flagged "needs more data" rather than killed for poor metrics.

---

## H6 — Counter-trend reversal at exhaustion (top/bottom + opposite divergence)

**Family.** Counter-trend at exhaustion. Goal's "ribbon_exhaustion + opposite-side precision" template; substituting top/bottom signals for ribbon exhaustion (no exhaustion column in DB).

**Thesis.** `top_signal` (n=49, side bear) and `bottom_signal` (n=22, side bull) are Otter's precision reversal triggers — the closest analog to "money_bag_top/bottom" from the goal. They fire after a sustained move. Pairing with a contrarian B-panel divergence circle on the same/next bar should filter out trend-continuation false positives (i.e. don't short a top_signal in a fresh uptrend; require a bearish divergence circle nearby to confirm exhaustion).

**Layers.**
- Context: none.
- Trigger: `top_signal[t] OR top_signal[t-1] OR top_signal[t-2]` (short side) / `bottom_signal` window (long side).
- Confirmation: opposite-side B-panel divergence circle within ±2 bars.
- Exit: 10-bar time stop OR ±1.5 ATR stop / ±2.5 ATR target (looking for sharp reversal moves).

**Pseudocode.**
```
LONG  := arm_within(bottom_signal, 3)
         AND arm_within(divergence_buy_circle, 3)
SHORT := arm_within(top_signal, 3)
         AND arm_within(divergence_sell_circle, 3)
```

**Falsification.** Low n likely (top+bottom signals are rare). If <50 trades, demoted to confluence tag rather than primary.

---

## H7 — Pure Otter stack: arm + trigger + CVD confirm

**Family.** Pure Otter stack (the goal's "spoon → otter → water → cvd flip" template, adapted: spoon→WT-divergence, water→ribbon-on-side, cvd_flip preserved).

**Thesis.** Stack three Otter / Otter-adjacent confirmations:
1. WT divergence in last 5 bars (arming),
2. Otter primary this bar (trigger),
3. CVD flip on same bar or next (volume confirmation).

This is the goal's "rare-as-primary, common-as-confirmation" principle applied to the Otter family — Otter is rare, divergence is moderate, CVD flip is moderate.

**Layers.**
- Arm: WT-1st divergence in last 5 bars.
- Trigger: `otter_buy[t]` (long) / `otter_sell[t]` (short).
- Confirm: `cvd_flip_bullish` fires on bar t or t-1 (long) / `cvd_flip_bearish` for short.
- Exit: opposite Otter, OR `red_diamond` (longs) / `blue_triangle` (shorts), OR 20-bar time stop, OR ±1.5 ATR stop.

**Pseudocode.**
```
LONG  := otter_buy[t]
         AND arm_within(wt_bullish_divergence, 5)
         AND flip_within("bullish", 2)
SHORT := otter_sell[t]
         AND arm_within(wt_bearish_divergence, 5)
         AND flip_within("bearish", 2)
```

**Falsification.** Trade count likely too low. If n < 30, demoted.

---

## H8 — Repaint-validation control strategy

**Family.** Methodology validation, not a real candidate.

**Thesis.** This isn't a tradeable strategy — it's a control. The RSI/Stoch/generic divergences look too good to be true (100% h1 hit on RSI bull/bear divergences). H8 backtests them naively (enter on fire bar's close) AND with a +1-bar entry shift. If the naïve version is profitable and the shifted version is not, the signals repaint and the naïve results are unusable. If both versions are profitable to similar degree, the edge is real. Either way, the answer informs whether the repaint-suspect columns can be used as **confirmation overlays** in H1-H7 (cannot be used as triggers in a deployed bot — repaint means the value at bar t isn't known at bar t in real time).

**Pseudocode.**
```
# Naive version
LONG_NAIVE  := stoch_bullish_divergence[t]
SHORT_NAIVE := stoch_bearish_divergence[t]

# Shifted version (the honest one)
LONG_SHIFT  := stoch_bullish_divergence[t-1]
SHORT_SHIFT := stoch_bearish_divergence[t-1]

# Exit: 5-bar time stop OR ±1.5 ATR stop (matching H2)
```

**What this informs.** If RSI/Stoch divergences survive the shift, they go into a "repaint-cleared" pool that can be added to H2/H3/H4/H5 as additional triggers, expanding their n. If they don't survive, they're benched.

---

## Cost-survival back-of-envelope

The 14 bps round-trip cost is a tough hurdle for short-hold strategies. Per-trade gross edge required to break even:

| Strategy | Hold (bars) | Hold (min) | Required gross edge | Inventory mean (h5/h10/h20) | Verdict |
|---|---:|---:|---|---|---|
| H1 (ribbon) | up to 20 | 60 | +14 bps | ribbon_buy h20 +5.95 / ribbon_sell h20 -0.56 | Likely **dies** on raw, may survive with bias filter |
| H2 (WT-2nd) | 5 | 15 | +14 bps | wt_2nd_bull h5 +4.88 / wt_2nd_bear h5 +3.98 | **Marginal** — needs ATR stops to amplify per-trade |
| H3 (capitulation) | 10 | 30 | +14 bps | div_buy_circle h10 +5.22 / div_sell_circle h10 +6.82 | **Plausible survivor** with target > 2R |
| H4 (div-armed Otter) | 20 | 60 | +14 bps | rare n — depends on which Otter trigger and how recent the WT divergence | TBD |
| H5 (full confluence) | 20 | 60 | +14 bps | rare n — confluence should amplify per-trade EV | TBD |
| H6 (top/bottom) | 10 | 30 | +14 bps | top h10 +2.82 / bot h10 +5.06 standalone; armed should improve | **Plausible** if armed with div circle |
| H7 (Otter+div+CVD) | 20 | 60 | +14 bps | rare n; each layer adds 1-3 bps | TBD |
| H8 (control) | 5 | 15 | n/a (validation) | n/a | methodology |

**Going-in expectation:** H3 (capitulation circles) is the most likely standalone survivor. H1 (ribbon+bias) and H2 (WT-2nd) will be close calls dependent on stop/target tuning. H4-H7 are confluence plays that depend heavily on whether the trade-count survives the 100-trade minimum, and on whether the confluence actually amplifies the per-trade EV.

---

## What I would have built if the data supported it

(Documenting for the audit trail.)

- **A true "spoon→otter" strategy** with the goal's exact spoon (price-vs-CVD divergence) — DB has CVD but no precomputed "spoon" column. Could derive: `spoon_bull := (price_low > prior_low) AND (cvd_low < prior_cvd_low)` over a lookback window. Reserved for follow-up; H4 substitutes WT-divergence as the arming event.
- **Multi-TF water alignment** — would require joining 3m to 15m to 30m to 4h/1d EMA-stack states. Without 4h/1d tables this is partial; if more data ingests later, revisit.
- **Pink-box S/R confluence** — image-based per BACKLOG / `trading_corp_otter_tuned_for_3m`. Not exportable to backtest at all.
- **Sommi VWAP regime filter** — `sommi_*` columns exist but never populated in the export. Excluded.
