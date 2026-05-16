# BitUnix scoring re-tune — decision log

Goal: improve BitUnix Phase 3.2 confluence score (weights, TTLs, formula) via
replay against alternative configurations.

## 2026-05-16 — kickoff + honest scope assessment

### What we have

| Data | Source | Window | Volume |
|---|---|---|---|
| BTC bars + TV indicator columns | `data/btc_scalping.db` | bars_3m: 2026-03-30 → 2026-05-16; bars_15m: 2025-12-04 →; bars_30m: 2025-04-22 → | 22,635 / 15,571 / 18,653 |
| Historical alert cache (real prod webhooks) | `data/historical_alerts/cache_alerts_20260430_20260512.json` | 2026-04-30 → 2026-05-11 17:21 (pre-Phase-3.2 ship) | 855 alerts, 27 distinct signals |
| Prior backtest | `data/backtest_runs/bitunix_20260511T173504/` | Apr 30 – May 9 | 625 alerts, 21 paper trades, 42.9% WR, +0.29R/trade |
| Local `paper_trade_record` for `bitunix_futures` | `data/trading_corp.db` | — | **0 rows** (data is on prod only) |

### What we DON'T have access to in this session

- **Prod `bitunix_signal_ledger`** — table only exists on the Azure VM. SSH to
  `trading.jacksumner.com:22` and `:2222` timed out. We cannot pull the
  post-Phase-3.2 (2026-05-11 → today) live ledger or the post-PR-3c
  (2026-05-14 → today) ledger with TF tags.
- **Prod `paper_trade_record` for `bitunix_futures`** — same reason. Goal text
  notes "paper-trade outcome data may only be ~5-7 days deep" anticipating
  exactly this thinness; we have effectively zero locally.

### Implication for methodology

The naive read of the goal — "pull prod ledger + paper trades, measure per-signal
edge, replay alt configs against actual trade outcomes" — is **not viable in
this session**. Three rescuable paths:

1. **Synthesize a ledger from `btc_scalping.db` bar columns.** Every TradingView
   indicator the YAML names has a corresponding column in `bars_3m / 15m / 30m`
   (verified — see Mapping appendix at bottom). For each fire-bar (column non-null
   and non-zero), emit an `AlertEvent(ts, signal_name, tf)` — exactly the shape
   `bitunix_signal_ledger` rows have post-PR-3c. This gives a multi-TF replay
   stream covering ~47 days on 3m, ~5 months on 15m, ~13 months on 30m.
2. **Simulate trade outcomes** via the same logic the live observer uses
   (`_build_proposal` legacy path — `trade_plan.enabled: false`), walking
   forward on 1m / 3m bars from `btc_scalping.db`.
3. **Honest limitations**: This is replay against historical signal stream, not
   live prod trades. We lose: live broker fill prices, real cooldown/dedupe
   interactions with the prod scheduler, latency between webhook delivery and
   scorer evaluation. We retain: every gate the scorer enforces (TF filter,
   dedupe-within-TTL, cooldown_seconds, tier classification, R:R floor,
   effective-risk cap).

Decision: **proceed with synthetic ledger path.** Cost model fixed at **9 bps
round-trip** (0.04% × 2 taker + 0.005% × 2 slippage — BitUnix VIP3 + Experience
Card per goal directive, NOT the 14 bps Coinbase number from the prior research).

### Why this is acceptable

The goal's question is "can we score the existing signal vocabulary better?"
The signal vocabulary is the TradingView indicator set, which `btc_scalping.db`
stores natively. The replay-vs-live gap matters when you're tuning fill
mechanics; it does not matter when tuning weights / TTLs / formula / thresholds
on the same indicator stream the live system already consumes. The score engine
is pure-function; whatever it would have decided on a live alert at time T,
it will decide on a synthetic alert at the same T with the same indicator
state.

### Carried assumptions (revisit if invalidated)

- 1.0/non-zero in an indicator column = "alert fires for that bar". Verified for
  all mapped columns — most are `{0, 1}`; the buy_circle / sell_circle family
  carries the panel y-coordinate as the truthy value, also fine.
- The btc_scalping.db indicator columns are computed identically to the
  TradingView indicator pinescripts that emit the prod webhooks. If they're not,
  the gap shows up as per-signal hit-rate divergence vs the prior backtest
  numbers — we cross-check against the existing Apr 30 – May 9 verdict
  (42.9% WR, +0.29R/trade across 21 trades) as a sanity floor.

### Mapping appendix — YAML factor → btc_scalping.db column

| YAML factor | Bar column | Notes |
|---|---|---|
| `mc_a_blood_diamond` | `blood_diamond` | Rare A-panel bear |
| `mc_a_red_diamond` | `red_diamond` | Primary A-panel bear |
| `mc_a_bluetriangle` | `blue_triangle` | Primary A-panel bull |
| `mc_a_redx` | `red_cross` | A-panel bear divergence |
| `mc_a_longema` | `long_ema_signal` | A-panel bull EMA turn |
| `mc_a_yellow_x` | `yellow_cross` | A-panel bull divergence |
| `mc_b_gold_buy` | `gold_buy_gold_circle` | Rarest B-panel bull |
| `mc_b_buy_circle_div` | `divergence_buy_circle` | B-panel bull w/ divergence |
| `mc_b_sell_circle_div` | `divergence_sell_circle` | B-panel bear w/ divergence |
| `mc_b_buy_circle` | `buy_circle` | B-panel bull |
| `mc_b_sell_circle` | `sell_circle` | B-panel bear |
| `mc_b_buy_dot` / `mc_b_sell_dot` | — (no column) | Smallest panel signal; unmapped, inert in replay |
| `otter_buy` / `otter_sell` | same | 3m chart only |
| `money_bag_top` / `money_bag_bottom` | `top_signal` / `bottom_signal` | |
| `water_buy_large` / `water_sell_large` | `super_buy_high` / `super_sell_high` | |
| `water_buy_small` / `water_sell_small` | `super_buy_std` / `super_sell_std` | |
| `spoon_bull` / `spoon_bear` | `bull_divergence` / `bear_divergence` | |
| `cvd_bull_flip` / `cvd_bear_flip` | `cvd_flip_bullish` / `cvd_flip_bearish` | |
| `bias_bull` / `bias_bear` | `ribbon_buy_cross` / `ribbon_sell_cross` | 90-min ribbon flip on Otter chart |
| `pink_box_bull` / `pink_box_bear` | — (image-based, not a TV alert) | Inert (matches live) |

`mc_b_buy_dot` / `mc_b_sell_dot` and `pink_box_*` are intentionally inert in
both replay and live — flagged so we don't tune them on noise. This matches
correction 2 from `trading_corp_bitunix_strategy_gaps.md`.

---

## Subsequent entries

### 2026-05-16 — Step 1 inventory observations

Wrote `reports/scoring_inventory.md`. Headline findings:

- **No individual 3m signal has positive forward edge in isolation.** Range from
  `water_buy_large` at +0.1R (n=15) down to `otter_sell` at -0.64R (n=82). This
  is expected — the entire point of the confluence engine is to combine
  individually-marginal signals into a stronger composite. The inventory's
  per-signal mean_r should be read as *relative ordering*, not as "any of these
  is profitable alone".
- **15m and 30m signals split on direction.** BUY signals on 15m/30m have
  positive mean_r (e.g. `water_buy_large` 15m = +1.7, `spoon_bull` 30m = +1.6)
  while SELL signals are flat-to-negative. Regime artifact: BTC was net-up over
  the 47-day window. Bear signals get whipsawed at the structural-stop
  distances.
- **Heavy-weight signals (weight 4-5) are NOT measurably better than
  weight-2-3 signals at per-signal level.** `mc_a_blood_diamond` (wt 5) has
  -0.54R on 3m vs `mc_a_red_diamond` (wt 4) at -0.43R. `mc_b_gold_buy` (wt 5)
  has -0.19R vs `mc_b_buy_circle` (wt 3) at -0.16R. Calibration drift since
  weights were assigned by intuition rather than measurement.
- **Otter precision family (water_*, spoon_*, money_bag_*) has the LEAST-bad
  weighted mean_r at -0.21R on 3m** — argues for slight up-weighting on these,
  matching their higher cleanliness on 15m/30m.

These observations feed `scoring_hypotheses.md`.

### 2026-05-16 — Step 2 replay harness — sanity check

Built `scripts/research_scoring/replay.py`. Baseline (current YAML, PR 3c
calibration) on the full 47-day window produces:

- **1,449 fires, 30 trades/day, -0.42R mean, 29% win rate.**

This is **~10–15× the live trade rate** (live runs ~2-3/day per the prior
backtest). The gap is the **PA validation gate + HTF regime gate**, which are
explicitly OUT OF SCOPE per the goal and not modeled in my replay. They're the
two binary filters that turn the score engine's verdict into an actual trade.

**Implication for methodology:** the replay's absolute trade-count and
absolute P&L are NOT predictive of live performance. They ARE valid for
*comparing variants* — both because the PA/HTF gates are downstream of the
score engine (and therefore filter equally regardless of upstream config), and
because variant-vs-variant differences in fire count and mean R survive
downstream filtering as long as the downstream gates don't have strong
correlation with the score engine's specific config choices (which they don't —
PA = vwap+volume+structure check; HTF = 4h/1d regime; neither cares about the
score formula or weights).

**Reading metrics:** "trades/day" in the replay should be divided by ~10–15 to
roughly estimate live trade rate; mean R, win rate, profit factor, Sharpe are
directly meaningful for variant comparison.

### 2026-05-16 — Step 3 hypotheses (written BEFORE running candidates)

Wrote `reports/scoring_hypotheses.md` with 7 candidate configs (H1–H7) plus
two extra parameter-sweeps (H3b α=2.0, H4b conviction 0.80, H5b family-
confluence on both tiers, H6b min_score 8 / premium 12). Each has a
prediction documented BEFORE the IS backtest so we can check whether the data
supports it.

### 2026-05-16 — Steps 4+5 IS+OOS backtest verdicts

Ran 13 variants (baseline + 12 candidates) via
`scripts/research_scoring/run_all_variants.py`. Full results in
`reports/scoring_backtest_results.md`; key calls:

- **No variant achieves positive expectancy on the 47-day window.** Mean R per
  trade ranges from -0.375 (best, combo) to -0.452 (worst, H4b) — all
  negative. This is largely *replay artifact*: missing PA + HTF gates means
  trade rate is ~10–15× live. Variants are honestly compared *relative* to
  baseline, not as live-trade predictors.

- **No overfit signal.** Every variant has LESS-bad OOS than IS (regime drift
  from bear-leg into chop, not learning). `combo` is most IS-OOS stable
  (ΔmeanR = +0.013); H4b had the biggest OOS recovery (+0.251).

- **Hypotheses verdicts:**
  - H1 (cap weights at 3) — **partial** — 10% fire reduction predicted, 9%
    actual; PREMIUM quality gap rose +0.037 (predicted ≥+0.05).
  - H2 (+ Otter precision up) — **held** — sum_R less negative than baseline
    AND H1; PREMIUM quality gap +0.114 (biggest in field).
  - H3 / H3b (asymmetric α) — **refuted** — filtering reduces fires without
    lifting per-trade quality.
  - H4 (conviction ratio ≥0.70) — **refuted** — too loose to filter.
  - H4b (ratio ≥0.80) — **partial** — cuts fires 41%, best OOS Sharpe (-3.20),
    but per-trade mean R actually *worsened* (-0.452 vs -0.421). Aggressive
    filter, not a quality lift.
  - H5 (PREMIUM ≥3 families, demote-to-STANDARD) — **held on quality only** —
    PREMIUM mean R rose -0.381→-0.340 but total trade count unchanged.
  - H5b (family on both tiers) — **refuted** — inverted PREMIUM/STANDARD
    quality gap.
  - H6 (min_score 7) — **refuted** — +0.013 mean R vs predicted ≥+0.10.
  - H6b (min_score 8, premium 12) — **partial** — approximates pre-PR-3c
    calibration. 2nd-best OOS Sharpe. But per-trade mean R worse than baseline.
  - H7 (H2 + unified cooldown) — **held** — beats H2 on every aggregate.

### 2026-05-16 — Step 6 recommendation

Wrote `reports/scoring_recommendation.md` with three finalists:

1. **H2** — re-weight (cap heavy weights at 3, up-weight Otter precision to 3).
   Best PREMIUM/STANDARD quality gap; simplest YAML diff (10-12 weight edits,
   no formula change, no threshold change). **Primary recommendation.**

2. **H7** — H2 + unified cooldown. Marginal but consistent additional lift
   over H2. ~6 LOC observer change. **Second recommendation.**

3. **H4b** — conviction-ratio formula ≥0.80. Cuts fires 41%, best OOS Sharpe.
   Biggest implementation surface and requires shadow-data validation of
   conviction-ratio stratification before commit. **Third recommendation.**

**Surprises worth flagging for the next session:**

- The score engine on this dataset doesn't have enough lever to move trade
  expectancy positive in isolation. The trade outcome lever is downstream
  (PA gate + HTF gate + trade_plan v2 — all shipped or pending shipment).
  H2/H7's value is *feeding cleaner candidates into those gates*, not
  fixing scoring math.
- The "heavy weights" (5/4) on `mc_a_blood_diamond`, `mc_a_red_diamond`,
  `mc_b_gold_buy`, `mc_b_*_circle_div` are not justified by per-signal
  forward edge measurement. They were intuited as "rarity = importance"
  and the data does not support that.
- Otter precision family (water_*, spoon_*, money_bag_*) is consistently the
  best-performing family on 15m and 30m measurements; up-weighting from 2→3
  is the only signal-side change that widens the PREMIUM/STANDARD quality
  gap meaningfully.
- The combo variant (everything-and-the-kitchen-sink) inverted the
  PREMIUM/STANDARD quality gap. Stacking filters fights itself; the goal's
  prescription of "test one thing at a time" is vindicated.

### Assumptions carried forward (revisit if invalidated)

- Synthetic ledger from bar columns ≈ live webhook stream in *composition*
  (per-signal share of total fires). Validated against the 855-alert cached
  prod sample for top signals (red_diamond, blue_triangle, otter_buy) within
  2× of expected ratio.
- 9 bps round-trip cost is the right floor. Funding not modeled (per goal —
  noise relative to entry/exit at 30-60 min holds).
- PA + HTF gates are downstream of the score engine and filter independent of
  score-engine config. If shadow data shows H2's PREMIUM-quality lift fails to
  survive PA/HTF gating, this assumption is wrong and we need a different
  recommendation.

### What's left as future-session work

- **Prod-data validation.** Pull `bitunix_signal_ledger` + `paper_trade_record`
  off the Azure VM (SSH was unavailable in this session); replay the same 13
  variants against actual prod fire history. Expected to be lower-volume but
  higher-fidelity; should refine the rankings.
- **Per-tier blocking for family confluence.** H5's current implementation
  demotes failed-PREMIUM to STANDARD; a stricter variant that SKIPs failed-PREMIUM
  outright is the obvious next test. Predicted: total fires drop ~15%, PREMIUM
  mean R unchanged.
- **TTL sweeps.** H2 keeps current TTLs. The goal flagged 3/5/8/12-bar TTL
  windows as worth backtesting; haven't done that yet because the per-signal
  ranking was the lower-hanging fruit. Future-session work.
- **Stacking-within-TTL variants (H8 dedupe-decay).** Dedupe currently keeps
  most-recent only. A "1st full weight, 2nd half, 3rd quarter" decay variant
  could capture multi-fire conviction without the over-counting failure that
  motivated dedupe. Not run.
- **Re-test after PR 4 (HTF gate enforce flip).** The current shadow-mode
  gate doesn't filter; flipping to enforce will reshape the candidate
  distribution downstream of the score engine. The ranking here might shift.

### 2026-05-16 18:51 UTC — H2 deployed to prod

H2 re-tune shipped via `scripts/patch_bitunix_scoring_h2.py --apply` (10/11 edits applied — `mc_b_gold_buy` was already at weight 3 with the `# H2: was 5` marker pre-deploy, origin unknown). Prod md5 `da18d6c5180cd09592b4475e4df8893e` → `6dc03a793e1e6e58df832aa89407ef93`. All 11 H2 targets verified at weight 3 via `yaml.safe_load`. Hot-reload via mtime cache; no service restart. Full deploy notes in `runbooks/deploy_log.md`.

