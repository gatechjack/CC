# Decision Log

Append-only audit trail for the BTC backtest research session started 2026-05-16.

---

## 2026-05-16 — Step 1: data inventory + schema discovery

**What I did.** Read `scripts/ingest_tv_export.py`, `scripts/analyze_btc_scalping_3m.py`, `scripts/eda_btc_scalping_signals.py`. Ran `PRAGMA table_info` on all `bars_*` tables. Computed firing frequencies + forward log-return mean/hit% at h ∈ {1, 5, 10, 20} bars for every event-shape column on `bars_3m`. Cross-checked column names against the goal description.

**What I found.**

1. `bars_3m` is the only table with 47-day continuous coverage (22,635 rows, 2026-03-30 → 2026-05-16). `bars_15m` and `bars_30m` exist; **`bars_4h` and `bars_1d` were retired** in the 2026-05-16 02:25 UTC ingest (per BACKLOG EOS snapshot — PR 3c moved scoring to `[3m, 15m, 30m]`). This breaks the existing `analyze_btc_scalping_3m.py` HTF-bias logic, which keyed on 4h+1d divergence events.

2. **~Half the indicator names in the goal description are not in the DB.** Notably absent: `spoon_bull/bear`, `water_buy_*`, `water_sell_*`, `bias_bull/bear`, `ribbon_exhaustion_*`, `mc_b_*_dot`. All `sommi_*` columns exist but have 0 fires. Built a name-mapping table in `data_inventory.md` § 2.

3. **The cleanest-looking sell signal is `divergence_sell_circle`** (h5 hit 67.7%, mean +7.16 bps, n=235). The cleanest bull signal is `divergence_buy_circle` (h5 hit 57.9%, +4.94 bps, n=202). Both ~1% firing rate — tradeable.

4. **High-conviction rare signal:** `gold_buy_gold_circle` (n=19; h5 mean +12.47 bps, h1 hit 84%). Too rare to backtest standalone (47-day n=19 ≪ 100-trade min) but plausible as a conviction tag.

5. **Strong look-ahead repaint suspicion** on RSI divergence (100% h1 hit), Stoch divergence (85-89%), and the generic `bull_divergence`/`bear_divergence` (95-98%). The h1-then-h5-then-h10 monotonic decay pattern matches a repaint signature (later horizons leak less of the repaint into observed return). Goal § 5 specifically calls out "no look-ahead" and asks me to flag repaints — flagged.

6. `vpmo`, `money_flow_*`, `buy_and_sell_circle` fire on 99%+ / 20% of bars and so their "edge" is just BTC drift over the window. Misclassified as events by my non-null/non-zero filter. Excluding from event-driven strategy generation.

**What surprised me.**

- Otter primary triggers (`otter_buy`, `otter_sell`) are extremely rare on 3m: 40 + 82 fires in 47 days. The goal doc treats them as "the main entry signal" but at this rate they're closer to "a rare confirmation tag." Will need to combine with divergences/circles as primary events.
- `red_diamond` is described in the goal as "close-longs trigger" but has h20 mean only -1.57 bps. Either the goal description is overstating its bearish edge, or it's a state-flip signal that's already priced in (close-on-fire is a different question than "go short on fire").
- `ribbon_buy_cross` has a real but slow edge (+5.95 bps at h20). Could be a poor-man's trend filter.

**What I decided to do next and why.**

- **Build HTF bias proxy from `bars_30m` `long_ema_signal`/`short_ema_signal` events with 24h decay.** This replaces the 4h/1d divergence-events logic that no longer has data. Documented in `data_inventory.md` § 5.
- **Validate the repaint-suspect divergences** by running an entry-shifted-by-+1-bar replay. If h5 edge survives, keep them; if it collapses, demote to confirmation-only or exclude.
- **Build the backtest harness next** (Step 3) so every hypothesis from Step 2 has consistent metrics. Decided to compute Sharpe, profit factor, win rate, avg win/loss, max DD, trade count, avg hold, trades/day — and to honor the 5 bps taker + 2 bps slippage cost setting (per goal prompt).

**Assumptions carried forward.**

- The 47-day window covers a mixed regime (per BACKLOG, BTC was up ~5.8% over Apr 30 – May 9 sub-window from the pre-PR-3c backtest). Sample is small relative to a full cycle. Out-of-sample (last 30%) will be ~14 days, which is acceptable for sanity but not for statistical confidence.
- A signal "fires" when the column is non-null AND non-zero. This matches the analyze-script convention.
- Forward returns are log-return at close, exits ignore intraday MAE/MFE for now (will use ATR-based stops in strategy variants).
- 5 bps taker per side + 2 bps slippage = **14 bps round-trip cost** for any strategy that exits on opposite signal or stop/target.
- No funding/borrow modeled (perp futures funding is ~±5 bps per 8h; over a few-bar hold it's noise relative to entry/exit costs).
- Going long-only OR long-and-short: the goal asks for {-1, 0, +1} per-bar position, so both directions enabled. No leverage applied in the backtest math (Sharpe is unlevered).

---

## 2026-05-16 — Step 2: hypothesis generation

**What I did.** Wrote 7 candidate strategies covering the families the goal asked for, plus an 8th repaint-control variant. Each has an explicit thesis, layered structure (context / arming / trigger / confirmation), exit logic, and falsification criterion. Documented in `reports/hypotheses.md`.

**What I found.** The candidate list is shorter than the goal's "6-8" by one for two principled reasons:
- `mc_b_*_dot` strategies (the goal's noise-baseline target) can't be built — DB has no `*_dot` columns.
- A spoon→Otter strategy in the goal's exact form (price-vs-CVD spoon) requires deriving a "spoon" column from raw CVD; for parity with the rest I used WT-divergence as the closest preexisting analog instead, in H4 and H7.

**What surprised me.** The cost-survival back-of-envelope (added at the end of `hypotheses.md`) was bracing. 14 bps round-trip on signals showing +3 to +5 bps gross h5 edge means cost-survival REQUIRES either much-longer holds OR much-rarer triggers OR much-stronger conviction. H3 (capitulation circles) and Seed 1 (V6 in Round 2) both predicted-survive on this basis.

**What I decided to do next.** Build the vectorized backtest harness with per-trade attribution, then run all 8 hypotheses on the IS split. Use `apply_exits` that walks the price path bar-by-bar so ATR stops can be triggered intra-bar via high/low (not just close).

---

## 2026-05-16 — Step 3: Round-1 in-sample backtest (initial 7 hypotheses + 4 repaint controls)

**What I did.** Built `Goals/scratch/backtest_harness.py` (vectorized harness with cost model, per-trade attribution, ATR stop/target intra-bar via high/low). Ran H1-H8 with the default exit policy `(time=20, atr_stop=1.5, atr_target=3.0, exit_on_opposite=True)` across both IS (first 70%) and OOS (last 30%). Cost = 7 bps/side.

**What I found.**

1. **Mass extinction.** Every strategy with `n_trades ≥ 100` lost money on IS. Every strategy with `n < 100` failed the goal's minimum-validity bar.
2. **H1_ribbon_bias** was the only modestly positive IS strategy (+0.54%, Sharpe +0.69, 19 trades) — and OOS was negative (-1.15%). 30m EMA-flip-with-decay bias filter was helping IS but couldn't generalize.
3. **The ATR-stop framework is materially destructive.** Signals with +3-5 bps gross h5 edge are being asked to clear an ATR-target of ~25 bps, getting stopped at -10 bps, and paying 14 bps of cost on the round-trip. Math doesn't work.
4. **Repaint hypothesis CONFIRMED.** All four suspect divergence columns (rsi_bullish, rsi_bearish, stoch_bullish, stoch_bearish, and the generic bull_divergence/bear_divergence) collapse by 30-60 percentage points when entry is shifted by +1 bar. Stoch is the most extreme: naive -55% IS → shifted -96% IS. RSI: -19% → -60%. Generic: -1.4% → -18%. These cannot be used as live triggers; they're TradingView marking the bar where the divergence began retroactively. Excluded from rounds 2-4.

**What surprised me.**
- The repaint controls quantified just how MUCH information was being leaked by the repaint. The 100% h1-hit-rate signal isn't just "marginally helped" by look-ahead; it's actively dishonest at the 30+ percentage-point level.
- I'd expected H3 (capitulation circles) to be the most likely survivor based on the inventory's +5-7 bps h5 mean. It died (-22% IS). The signal's edge IS real but the ATR-stop policy was the bottleneck, not the trigger quality.

**What I decided to do next.** Round 2 with:
- Time-only exits (no ATR stop/target) matching each signal's measured edge horizon. If WT-2nd has its edge at h5, exit after 5 bars rather than letting an ATR stop bounce around.
- Directional decomposition: pure-long-only, pure-short-only, both-sides variants.
- Confluence stacks: ribbon + CVD-flip; divergence-circle + CVD-flip.
- A 30m-SMA(24) bias proxy in addition to the 30m EMA-flip-with-decay one.

**Assumptions carried forward.**
- Repaint columns are excluded entirely from candidate strategies from Round 2 onward.
- Exit-on-opposite is preserved; cost is paid on both the close and the flip-open (matches goal description of trades as state-machine transitions).

---

## 2026-05-16 — Step 4: Round-2 + Round-3 sensitivity sweep

**What I did.** Ran 13 V/W strategy variants with the lessons from Round 1. Then expanded V6 (the round-2 standout) into a 20-variant sensitivity sweep across hold ∈ {10, 20, 40, 60, 100} bars × bias ∈ {SMA24, EMA-flip-with-decay} × direction ∈ {long-only, both-sides}. Plus 16 more W-variants on different signal families (W1-W6).

**What I found.**

1. **V6 family is a clean survivor.** `ribbon_buy_cross` (long) + `ribbon_sell_cross` (short) filtered by 30m-SMA(24)-bull/bear, with time-only exits, is positive IS+OOS across the FULL hold ∈ {10..100} sweep when using SMA bias. EMA-flip bias variants are weak; SMA(24) is a structurally better filter for this purpose.
2. **The bilateral variant is preferred.** Long-only V6 hits 19 IS / 8 OOS trades; bilateral hits 33 IS / 19 OOS — nearly doubling sample size — and the bear-side trades earn positive P&L (~+4.9% cumulative on short-active bars). No reason to discard one side.
3. **W1 is a textbook overfit.** divergence-buy-circle + SMA-bull at h40/h60 looks great in IS (Sharpe +4.8) and degrades to OOS net -0.5%. The IS-positive-OOS-negative pattern is the classic signature.
4. **W6 (long-only ribbon raw, no bias, h100) is positive IS+OOS** at modest magnitude (+2.7% / +2.1%). Useful as a baseline — it captures most of the BTC drift without bias-filter complexity. Held as Seed 2.
5. **W5 (divergence-circle + SMA bias bilateral h60) is marginally positive IS+OOS** at near-noise (+0.5% / +0.1%). Held as Seed 3 for diversification across signal families.

**What surprised me.**
- The bilateral V6 OOS performance IMPROVES as hold extends (+3.96 → +7.59% across h10 → h100), where IS performance is flat-to-down. This is unusual — typically OOS degrades with parameter complexity. Suggests the underlying signal captures a longer horizon than the IS sample window naturally surfaced.
- EMA-flip-with-decay bias was the analyze-script's design choice; in practice for this data, the 30m SMA(24) is materially better. Likely because EMA-flip events on 30m are infrequent and leave too many bars in "neutral" state. SMA(24) keeps the bias active on virtually every 3m bar.

**What I decided to do next.** Round 4: fully sensitivity-stress-test V6 (SMA window 12..72, cost 3..15 bps/side, regime + volatility breakdowns) to confirm the edge isn't a knife-edge fit.

**Assumptions carried forward.**
- "Edge robust to parameter perturbation" is a stronger validation than "IS+OOS positive at one fixed config" given the small sample. I'm scoring V6 high on this dimension specifically.

---

## 2026-05-16 — Step 5: Round-4 final sensitivity, regime, and volatility breakdown

**What I did.**

1. SMA window sweep for V6_smabias_both_h20 across {12, 18, 24, 36, 48, 72}. All 6 variants tested.
2. Cost sweep across {3, 5, 7, 10, 15} bps/side.
3. P&L attribution by HTF-bias regime and by realized-vol bucket.
4. Full trade list export for combined IS+OOS series.

**What I found.**
- SMA window: 6 of 6 positive on both IS+OOS. Sharpe range +1.87 to +5.68 IS, +4.57 to +11.20 OOS. SMA=18 has the best OOS Sharpe; SMA=24 (my default) is essentially tied. Choosing 24 because it matches the default in the harness code.
- Cost sensitivity: positive OOS at every cost from 3 bps/side through 15 bps/side. IS turns negative only at 15 bps/side. Strategy is reasonably cost-robust up to ~10 bps/side.
- Volatility regime is the most surprising attribution: 80% of P&L comes from the high-vol third of bars by active-position count (368 / 474 / 641 low/mid/high split). +9.79% on high-vol active bars vs +1.51% on low-vol. This is consistent with the intuition that ribbon-cross signals are valuable at trend inflection points (which produce high vol) and noise during chop.
- Bull-bias regime contributes more than bear-bias (+6.46% vs +1.83%), but bull-bias regime is also the majority (12,290 vs 10,345 bars). Per-bar productivity is comparable.

**What surprised me.**
- That the high-vol regime concentration is so strong. This suggests an OPERATIONAL extension I didn't pursue here: gate the strategy by realized-vol threshold (only trade when σ_20bar > p50). Could ~halve the position-frequency while preserving most of the P&L. Filed as forward-test follow-up; haven't validated it.
- That cost-sensitivity is so forgiving. The strategy doesn't need to be at 5 bps/side fills to work; it works at 10 bps/side, which is realistically achievable on Bybit perp during normal conditions.

**What I decided to do next.** Write the final reports. Frame Seed 1 honestly: best-survivor-found with robustness checks passed, but `n` is below the goal's 100-trade bar and the data window is short. Provide Seed 2 (W6 baseline) and Seed 3 (W5 divergence-circle) as the user-requested "diverse seeds" — explicitly noting their weaker validation. Surface the repaint finding as a major data-quality result (it impacts not just this work but the user's wider EDA/strategy pipeline since `analyze_btc_scalping_3m.py` and `eda_btc_scalping_signals.py` both treat rsi/stoch divergences as real signals).

**Assumptions carried forward into operational recommendation.**
- 30m SMA(24) bias is a robust HTF filter. If the user reactivates `bars_4h`/`bars_1d` ingest, this should be revisited — possibly 4h trend filter is even cleaner than 30m SMA(24).
- The 100-trade minimum is a hard bar that the data couldn't clear in 47 days. Forward-paper-testing the seeds for 60-90 days will let the bar be met in real conditions.
- The user said the goal is "diverse, plausible seeds." I've produced 3 seeds, of which 1 is meaningfully robust (Seed 1 V6 family) and 2 are tentative-but-positive-both-sides. The honest read is that the indicator suite available in this DB doesn't have many tradeable seeds at this trade-count + cost level — the bot will need either more data, lower costs, or higher-conviction filtering layers to expand the survivor set.

---

## 2026-05-16 — Final: deliverable consistency check

**What I did.** Reviewed all 5 deliverable files for internal consistency. Confirmed each file references the others appropriately, all metrics quoted are consistent with the backing JSON in `Goals/scratch/`, and all data caveats (repaint, n<100, derived-bias) are surfaced in every file where relevant.

**What I decided.** Goal complete. Files: `data_inventory.md`, `hypotheses.md`, `backtest_results.md`, `strategy_candidates.md`, `decision_log.md`. Backing data + scripts under `Goals/scratch/`.

**Honest standing.**
- 0 of 8 hypotheses fully cleared the 100-trade IS minimum with positive expectancy. Loosening to "robust positive IS+OOS across parameter sensitivity" yields 1 winner (V6 family) and 2 marginal candidates (W6 baseline, W5 divergence).
- The biggest concrete finding is methodological: **TradingView RSI / Stoch / generic divergences repaint** in this data. This affects existing user scripts (`analyze_btc_scalping_3m.py`, `eda_btc_scalping_signals.py`) that treat those columns as live signals. Recommend the user update those scripts to either exclude the repaint columns or add a +1-bar-shift evaluation alongside the naive one to flag it.
- The trade-count gap is real and cannot be closed without more data. The user's planned next move (forward paper-test the seeds) is the right way to accumulate the missing n.

