# kalshi_weather hourly re-evaluation — Phase D replay findings

**Status:** investigation result, **not** a deploy proposal. Phase D
ran the design from `planning/kalshi_weather_hourly_reeval_design.md`
against the 2026-05-15 → 2026-05-22 prod corpus pulled in Phase C
(`tmp/kw_whp.jsonl.gz` 636 rows + `tmp/kw_rt.jsonl.gz` 556 rows + 658 PO
rows, all sha-verified against prod-side hashes 2026-05-22).

Script: `scripts/replay_kalshi_weather_hourly_reeval.py`.
Raw output: `tmp/replay_results.csv` (11,141 rows) +
`tmp/replay_summary.json` (aggregates).

## Headline (one-liner)

**Hourly re-eval does not add net edge over the entry signal under
either zero-leak (Tier A.1) or leak-inflated (Tier A.2) assumptions.
Tier B PnL with assumed 3¢ spread is ~zero on Tier A.1 (+$25 across 22
positions) and negative on Tier A.2 (−$26 across 46). Phase C
operator-recommended next step (build `quote_snapshot` for Tier C real-
PnL) is not justified by this replay — the headline question (does
intraday data change the signal usefully?) reads as "no" before exit-
cost fidelity even enters.**

## Gate results (verbatim from `tmp/replay_summary.json`)

### Gate PARITY (#4) — recomputed prob_yes vs audit
```
n_positions:           556
n_match_strict (≤1e-9): 556  (100%)
max_deviation:         3.33e-16   (= float epsilon)
passed_strict:         true
```

Strict-match across every position. `forecast_probability` from
`trading_corp.agents.strategies._weather_math`, given the audit's
recorded `forecast_temp_f`, `sigma_used_f`, `threshold_f`,
`threshold_high_f`, `direction`, reproduces `prob_yes` to float-epsilon
precision. **No drift between replay math and live strategy math.**

### Gate LEAK GUARD (#5) — observed-floor uses only obs with obsTime ≤ H
```
asserts_run:            11141   (one per observed_extremum_through_H call)
hard-aborts on leak:    0       (script ran to completion → no leak fired)
A.1 overall row-correct: 64.17%
baseline RT win-rate:    61.33%
```

Asserts on every observed-floor call. A.1 overall row-correctness of
64.17% sits just 2.84 pp above the baseline win-rate of 61.33% — well
below leak-suspicion territory (per design §8.4, ~100% would indicate
future-state leak). Most rows are HOLD signals scored as
correct-if-won; that's why the overall correctness tracks baseline.

## Tier A.1 — METAR-only zero-leak headline

Observed-floor for HIGH/LOW: `forecast_at_H = max(observed-through-H,
entry_forecast)` for HIGH; `min` for LOW. Sigma held at entry's
`sigma_used_f`. Signal definition: CLOSE iff `prob_outcome_at_H <
entry_price` (i.e., updated fair value of our side is below cost basis).

| Metric | Value |
|---|---|
| Positions evaluated | 555 (1 KXTEMP hourly market skipped — local-tz handling out of scope) |
| Rows (position × H, hourly grid) | 11,141 |
| Positions whose signal *ever* flipped HOLD → CLOSE | **22 (3.96%)** |
| Of the 22 flipped, FINAL-hour signal correct | 13 / 22 (59.09%) |
| Of the 22 flipped, FIRST-close signal correct (position lost) | 11 / 22 (50.00%) |
| Tier A.1 per-position FIRST-close PnL delta — sum | **+$25.07** |
| Tier A.1 per-position FIRST-close PnL delta — mean | +$1.14 |
| Tier A.1 per-position FIRST-close PnL delta — median | −$0.01 |

The Tier-A.1 zero-leak result is a coin-flip on accuracy (11 right /
11 wrong on first close) and a near-zero median PnL effect. The +$25
sum is dominated by a few large wins (p75 = +$5.86, max = +$6.95) but
also has comparable losses (p25 = −$1.47, min = −$7.86). Across 22
positions out of 555, this is ~$1.14/position-with-signal on a
strategy that's currently running −$667 total realized PnL on 556
paper round-trips — i.e. ~3.8% of the loss recovered, with high
variance.

## Tier A.2 — METAR + Open-Meteo (LEAK-INFLATED upper bound)

OM `historical-forecast-api` (daily `temperature_2m_max` /
`temperature_2m_min`) is fetched once per (station, target-date). Per
spec, this is treated as a **leak-inflated** signal because the OM
endpoint does not control for issue time — the value reflects the best
historical archive forecast, which may incorporate model runs after
our `entry_ts`.

A.2 forecast_at_H combines OM's daily extremum with the observed-floor
the same way A.1 does (max for HIGH, min for LOW). Sigma unchanged.

| Metric | A.1 zero-leak | **A.2 leak-inflated** |
|---|---|---|
| Positions ever signaling CLOSE | 22 (3.96%) | **46 (8.29%)** |
| First-close correct (position lost) | 11 / 22 (50.0%) | **19 / 46 (41.3%)** |
| Per-position first-close PnL — sum | +$25.07 | **−$25.66** |
| Per-position first-close PnL — mean | +$1.14 | −$0.56 |
| Per-position first-close PnL — median | −$0.01 | −$0.93 |

**Counter-intuitive result:** the "leak-inflated upper bound" does not
sit above A.1 — it sits below. Adding OM's historical-forecast does
not improve signal quality; it makes the signal over-trigger and lose
money. Two readings, both worth saying out loud:

1. **Open-Meteo's historical-forecast may not leak meaningfully on
   this corpus.** The endpoint may return a forecast issued before our
   `entry_ts` (e.g., the model run from the prior 0Z/12Z cycle). If
   so, A.2 ≈ A.1 + noise, with the OM value introducing systematic
   bias that fires CLOSE on positions that subsequently won.
2. **The signal definition (`close iff prob_outcome < entry_price`) is
   not noise-robust.** A.2's extra triggers come from OM disagreeing
   with NWS-derived entry forecast on the daily extremum. Many of
   those disagreements don't predict the actual outcome direction;
   they're just NWS↔OM model disagreement, which the strategy already
   accounts for via `SOURCE_DIVERGENCE_SIGMA_F = 2.0` at entry but
   does not at H.

Either way, the "ceiling" framing fails: with the supposed leak, the
signal still doesn't help.

## A.1 ↔ A.2 divergence by horizon (the primary leak-magnitude probe)

Row-level signal disagreement between A.1 and A.2 by remaining horizon
(target_iso − H):

| Horizon bucket | n rows | A.1 % correct | A.2 % correct | A.1↔A.2 disagree % |
|---|---|---|---|---|
| 0–6h   | 2,619 | 66.13 | 64.95 | **5.54** |
| 6–12h  | 3,200 | 62.59 | 61.31 | **5.66** |
| 12–24h | 3,859 | 64.34 | 64.16 | **4.74** |
| 24h+   | 1,463 | 63.64 | 63.64 | **0.00** |

Observations:

- **24h+ is 0% disagreement.** At long horizons there are few METAR
  obs between `entry_ts` and `H`, so the observed-floor is rarely
  binding; A.1 and A.2 reduce to (entry_forecast) vs (OM_forecast)
  with no floor, and on this corpus they agree on the signal direction
  100% of the time at that horizon. *That is itself evidence that OM's
  historical-forecast value is close to NWS's entry-time forecast for
  the target date.*
- **0–24h sits at 4.7–5.7% disagreement.** A.2 fires more CLOSE
  signals than A.1 at short horizons, and the extra fires are net
  wrong (A.2 correctness ≤ A.1 correctness in every bucket).
- **A.2 never beats A.1 at any horizon.** No bucket where the
  leak-inflated signal outperforms the zero-leak signal.

The conclusion the divergence-by-horizon was designed to measure
("how much does intraday forecast-update data help, in theory?") is:
**not measurably, even with leak.**

## Tier B — DIRECTIONAL ONLY, spread assumed constant

Cost model (per Phase-D operator spec):
- Spread: **3¢ flat** (`ASSUMED_SPREAD = 0.03`). Calibrated empirical
  median spread per ticker series is NOT used — every Tier-B output is
  suffixed `_DIRECTIONAL_ONLY_spread_assumed_constant` and must not be
  read as a real-PnL forecast.
- Fee: `ceil(0.07 × qty × P × (1−P))` cents, min 1¢ — Kalshi formula.
- Exit price: `bid_at_H = max(0, prob_outcome_at_H − 0.015)` (sell
  into bid, cross half spread).

| Metric | A.1 (METAR-only) | A.2 (LEAK-INFLATED) |
|---|---|---|
| Per-row CLOSE-signal n | 242 | 751 |
| Per-row sum PnL Δ | +$337 | −$264 |
| Per-position first-close n | 22 | 46 |
| Per-position first-close sum | +$25.07 | −$25.66 |
| Per-position first-close mean | +$1.14 | −$0.56 |
| Per-position first-close median | −$0.01 | −$0.93 |

**Tier-B is directional only — do not treat the +$25 as $25 of
recovered edge.** Real-data exit costs (Tier C in the design) require
30+ days of `quote_snapshot` accumulation. Today's snapshot-at-entry
spreads in the WHP audit are NOT calibrated into Tier B (deferred per
spec). Even taking +$25 at face value, it's 3.8% of the −$667 paper
loss, with sample n=22.

## What this replay does *not* tell us

1. **Real exit-price feasibility.** Without `quote_snapshot`, we
   cannot test whether the modeled bid (prob_outcome − half spread) is
   actually fillable. Wide-market periods (overnight, weekends) may
   see no bid at all on weather markets, in which case the modeled
   close PnL is fiction.
2. **Whether Tier A.2 is genuinely leak-inflated.** OM's
   `historical-forecast-api` may quietly return a model run issued
   BEFORE our `entry_ts`, in which case A.2 isn't actually
   leak-inflated and the comparison "A.2 = ceiling" doesn't hold. A
   targeted probe (request OM forecast for an entry's target date, compare
   to audit-recorded entry-time NWS forecast) would resolve this in
   ≤15 min — separately scoped if you want it.
3. **ADD signals.** The replay only emits HOLD vs CLOSE — no ADD or
   NEW. Per design §4.3, ADD analysis was conditionally scoped
   ("operator decides whether ADD is allowed"). The headline result
   doesn't justify scoping ADD further until the close-signal edge
   becomes positive.
4. **Strategy parameter sensitivity.** The CLOSE-iff-underwater
   threshold has no parameter knob in the replay. A different
   threshold (e.g., close only when prob_outcome < entry_price − cost-
   of-spread) would produce different numbers. The current threshold
   is the most-aggressive (zero-buffer); any buffer would reduce close
   counts further. The result was already "≤ break-even on PnL" with
   the most aggressive trigger, so adding a buffer would not flip the
   sign.

## Recommended next-step posture (Phase E decisions — operator's call)

Given the zero/negative net signal:

- **Do not deploy quote_snapshot persistence on this strength of
  result.** The design recommended parallel build "regardless of replay
  outcome" because it's cheap. That holds only if the close-signal
  edge is plausibly real. The replay says it isn't; spend the
  observation-week capacity elsewhere.
- **Do not deploy any hourly re-eval logic to live trading.**
- **Re-open this question if** (a) `quote_snapshot` is built for
  unrelated reasons and we get 30+ days of intraday quotes for free,
  OR (b) the weather strategy gets a redesign that changes the entry
  signal in a way that would also change the intraday signal
  meaningfully.

## Reproducing

```pwsh
# Phase C (corpus already on disk in tmp/, sha-verified):
ls tmp/kw_*.jsonl.gz   # whp 636, rt 556, po 658

# Phase D:
.\scripts\run_capped.ps1 python scripts\replay_kalshi_weather_hourly_reeval.py
# → tmp/replay_results.csv  (11,141 rows)
# → tmp/replay_summary.json (gates + aggregates)
```

The script is deterministic given the corpus + the NWS/OM cache in
`tmp/metar_cache/` and `tmp/open_meteo_cache/` (re-fetch on cache
miss). Re-running with a different `HOUR_STEP_H` (currently 1) would
change row counts but not the signal-changed-pct or first-close
headlines materially.
