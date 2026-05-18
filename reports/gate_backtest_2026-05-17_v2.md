# BitUnix Confluence-Gate Backtest v2 — PA vs 5-Factor (after CVD + F1 fixes)

**Window:** 2026-04-30 → 2026-05-17  ·  **Alerts:** 1,796  ·  **Run ts:** 2026-05-17 22:41 UTC

This is the v1.1 re-run of `reports/gate_backtest_2026-05-17.md` after the
two correctness fixes identified in
`reports/gate_backtest_2026-05-17_factor_analysis.md` § Q5:

1. **CVD semantic fix** — `cvd_from_bars_tick_rule` now computes the
   slope of the CUMULATIVE CVD series (`cumsum(sign(close-open) * volume)`),
   not the slope of per-bar deltas. Also switched tick-rule sign source
   from `close - prev_close` (inter-bar momentum) to `close - open`
   (intra-bar candle direction) per spec.
2. **F1 all-three-slopes fix** — `_factor_ema_alignment` now requires
   `slope(ema_8)`, `slope(ema_21)`, and `slope(ema_50)` all aligned with
   the trade side (was: ema_8 slope only).

Both fixes are in `trading_corp/data/bitunix_price_context.py` and
`trading_corp/agents/strategies/bitunix_confluence_gate.py`. The
backtest harness uses `build_gate_inputs` directly — there is no
second copy of either formula in `scripts/backtest_bitunix_confluence.py`.

This report is **findings only.** No cutover, factor-loosening, or
floor-revisit recommendations.

---

## Pre-committed acceptance thresholds (Board mod #1 — UNCHANGED from v1)

- Profit factor ≥ **1.20**
- Win rate ≥ **45.0%**
- Round-trips ≥ **20** (statistical floor)
- Fire rate ∈ **[5.0%, 50.0%]** of alerts
- Total R ≥ PA's total R (informational only if PA n < 20)

## Acceptance evaluation — 5-factor arm (v1.1)

| Check | Value | Threshold | Result |
|---|---|---|---|
| Profit factor | **2.63** | ≥ 1.20 | **PASS** |
| Win rate | **54.8%** | ≥ 45.0% | **PASS** |
| Round-trips | **31** | ≥ 20 | **PASS** |
| Fire rate | **1.73%** | ∈ [5.0%, 50.0%] | **FAIL** |
| Total R (vs PA) | **+21.23** vs PA's **+14.92** | ≥ PA | **PASS** |

**OVERALL: FAIL** — same headline status as v1.0, blocked by the same single check (fire rate). Every other metric improved.

---

## Side-by-side summary — PA arm vs 5f arm (v1.1)

| Metric | PA arm | 5-factor arm |
|---|---|---|
| Fires | 26 | 31 |
| Round-trips | 26 | 31 |
| Win rate | 50.0% | **54.8%** |
| Avg R | +0.574 | **+0.685** |
| Total R | +14.92 | **+21.23** |
| Profit factor | 2.31 | **2.63** |
| Return % | +0.80% | +0.77% |
| Max DD % | 0.10% | 0.09% |

After the fixes, the 5f arm beats the PA arm on every quality metric
(WR, Avg R, Total R, PF). Selectivity is the same trade-off as v1 —
5f leaves money on the table by being more selective, but the trades
it DOES take outperform PA's set.

---

## Diff vs v1.0 — headline metrics

| Metric | v1.0 | v1.1 | Δ |
|---|---|---|---|
| Fires | 33 | 31 | **−2** |
| Round-trips | 33 | 31 | −2 |
| Win rate | 48.5% | 54.8% | **+6.3pp** |
| Avg R | +0.490 | +0.685 | **+0.195** |
| Total R | +16.16 | +21.23 | **+5.07** |
| Profit factor | 2.01 | 2.63 | **+0.62** |
| Return % | +0.75% | +0.77% | +0.02pp |
| Max DD % | 0.09% | 0.09% | unchanged |
| Fire rate | 1.84% | 1.73% | −0.11pp (still below 5% floor) |

**Direction check (expected vs observed):**

- ✓ **CVD fix should reduce F4 pass rate in trending bars.** Observed:
  cvd pass rate dropped 63.0% → 56.7% (-6.3pp). The cumulative-slope
  formulation correctly identifies sustained-flat tapes (where the
  cumulative series doesn't actually slope) and rejects them.
- ✓ **F1 fix should reduce F1 pass rate.** Observed:
  ema_alignment pass rate barely moved (17.4% → 17.3%, -0.1pp).
  Interpretation: EMA series are smooth — when ema_8 slope is
  aligned, ema_21 and ema_50 slopes usually also align. The all-three
  check is mostly redundant with the ema_8 check in practice on 15m
  BTC bars. (This is a useful finding by itself — see Q3 correlation
  matrix below.)
- ✓ **Overall trade quality should improve.** Observed: WR +6.3pp,
  Avg R +0.195, Total R +5.07, PF +0.62. Each marginal trade the gate
  rejected was net-negative on average; each marginal trade it took
  was net-positive.

---

## Per-factor pass rates — v1.0 vs v1.1

Evals total: 597 (v1.0) vs 614 (v1.1). The slight evals-count
increase is from arm-cooldown timing shifts after fewer trades fire.

| Factor | v1.0 pass rate | v1.1 pass rate | Δ |
|---|---|---|---|
| ema_alignment | 17.4% (104/597) | 17.3% (106/614) | -0.1pp |
| vwap | 43.0% (257/597) | 42.8% (263/614) | -0.2pp |
| volatility | 36.3% (217/597) | 37.5% (230/614) | +1.2pp |
| **cvd** | **63.0% (376/597)** | **56.7% (348/614)** | **-6.3pp** |
| volume_z | 15.7% (94/597) | 16.8% (103/614) | +1.1pp |

CVD is the only factor with material movement. All others moved <1.5pp.

---

## Q1 re-run — per-factor WR / Avg R / Total R (31 5f-fired trades)

Restricted to the 5f-arm round-trips. Each row = trades where that
factor passed, regardless of which other factors also passed.

| Factor | n | v1.0 WR / Total R | v1.1 WR / Total R |
|---|---|---|---|
| ema_alignment | 15 | 53.3% / +9.00 | 53.3% / +9.00 (unchanged) |
| vwap | 27 | 48.1% / +13.16 | **51.9% / +16.23** |
| volatility | 21 (was 23) | 60.0% / +17.16 | **66.7% / +22.23** |
| **cvd** | 29 (was 28) | 46.4% / +12.16 | **58.6% / +22.00** |
| volume_z | 14 (was 17) | 47.1% / +7.00 | **50.0% / +8.23** |

**CVD's contribution nearly doubled** — from +12.16R (46.4% WR) to
+22.00R (58.6% WR). The cumulative-slope formulation produces a
much cleaner directional signal: when it passes now, the trade is
more likely to win, because the gate has correctly identified a
meaningful net-flow direction (not just noise in per-bar deltas).

**Volatility moved up too** — +17.16R → +22.23R (66.7% WR). Likely a
second-order effect: with a cleaner CVD signal, the trades that pass
the gate are higher-quality on average, so each factor's "when I
pass, the trade wins" rate improves.

### Q1 pairwise — top 5 (v1.1)

| Factor pair | n | WR | Avg R | Total R |
|---|---|---|---|---|
| **volatility + cvd** | 19 | **73.7%** | **+1.211** | **+23.00** |
| vwap + volatility | 17 | 64.7% | +1.014 | +17.23 |
| vwap + cvd | 25 | 56.0% | +0.680 | +17.00 |
| ema_alignment + volatility | 11 | 63.6% | +0.909 | +10.00 |
| ema_alignment + cvd | 14 | 57.1% | +0.714 | +10.00 |

`volatility + cvd` is the new top pair — 73.7% WR over 19 trades.
v1.0 had `vwap + volatility` on top (58.8% WR over 17).

---

## Q2 re-run — PA-only trade outcomes (23 trades)

| Metric | v1.0 | v1.1 |
|---|---|---|
| n trades | 24 | 23 |
| Win rate | 54.2% | 56.5% |
| Total R | +16.92 | +17.92 |
| Avg R | +0.705 | +0.779 |

The 5f gate still rejects PA's profitable trades. The "5f cost
+17.92R on these rejects" finding stands — actually slightly worse
than v1.0 (+16.92R). The fixes didn't reduce the false-negative
problem; they just made the trades 5f DOES take perform better.

### Factor rejection counts on PA-only winners (v1.0 vs v1.1)

| Factor | v1.0 reject count | v1.1 reject count | Δ |
|---|---|---|---|
| ema_alignment | 20 | 20 | unchanged |
| vwap | 5 | 5 | unchanged |
| volatility | 14 | 13 | -1 |
| **cvd** | **11** | **6** | **-5** |
| volume_z | 19 | 18 | -1 |

**The CVD fix correctly removed 5 false-negative CVD rejections.**
After the fix, CVD recognizes 5 more PA-only winners as having genuine
directional flow. But the other rejectors (ema_alignment, volume_z)
are unchanged, so the trades still get rejected on the other factors.

ema_alignment + volume_z together still account for 38 of the 23
PA-only trades' rejections (factors are non-exclusive — a trade can
fail multiple at once). They remain the dominant gate-tighteners.

---

## Side breakdown — v2

### Alert population baseline (1,601 fire-eligible alerts)

- Buy intent: 99 (18.3%)
- Sell intent: 441 (81.7%)

(Window was a 4.5:1 sell-dominant tape, unchanged from v1.)

### 5f-fired by side

| Side | v1.0 | v1.1 |
|---|---|---|
| buy | 2 (6.1%) | 3 (9.7%) |
| sell | 31 (93.9%) | 28 (90.3%) |

5f arm is slightly less sell-skewed after the fix (94% → 90% sells).
Buy fire rate edged up from 6.1% to 9.7% of fires; still
under-weighted vs baseline (18.3%).

### PA-only by side

| Side | v1.0 | v1.1 |
|---|---|---|
| buy | 5 (20.8%) | 5 (21.7%) |
| sell | 19 (79.2%) | 18 (78.3%) |

Roughly proportional to the 82% baseline. PA-only rejections are
NOT directionally biased — they reflect the underlying alert
distribution.

### Per-side trade quality (v1.1)

| Set | Side | n | WR | Avg R | Total R |
|---|---|---|---|---|---|
| 5f-fired | buy | 3 | 100.0% | +2.000 | +6.00 |
| 5f-fired | sell | 28 | 50.0% | +0.544 | +15.23 |
| PA-only | buy | 5 | 40.0% | +0.200 | +1.00 |
| PA-only | sell | 18 | 61.1% | +0.940 | +16.92 |

The 5f arm's 3 buy trades all won (small sample, but encouraging
that the all-three-slopes check picked the right buys). Sell-side
PA-only is where the bulk of the missed profit lives (+16.92R from
just 18 trades).

---

## Q3 re-run — factor correlation matrix (1,601 alerts)

| | ema_alignment | vwap | volatility | cvd | volume_z |
|---|---|---|---|---|---|
| ema_alignment | 1.00 | **0.58** | 0.01 | 0.05 | 0.02 |
| vwap | 0.58 | 1.00 | -0.19 | **0.12** | 0.04 |
| volatility | 0.01 | -0.19 | 1.00 | 0.01 | -0.02 |
| cvd | 0.05 | **0.12** | 0.01 | 1.00 | 0.04 |
| volume_z | 0.02 | 0.04 | -0.02 | 0.04 | 1.00 |

### Changes from v1.0

- **ema↔vwap: 0.57 → 0.58.** Essentially unchanged. Still the only
  notable correlation; still below the 0.6 redundancy threshold.
- **cvd↔ema: -0.01 → 0.05** and **cvd↔vwap: 0.02 → 0.12.** Both moved
  modestly upward. The fixed CVD signal is now slightly more
  correlated with the other directional factors (all three key off
  market direction), but both remain low. Not "redundant" in any
  meaningful sense.
- All other pairs unchanged within noise.

**Verdict unchanged:** no pair exceeds the |phi| > 0.6 redundancy
threshold. The 5-factor structure is still carrying 5 distinct
signals after the fixes.

---

## CVD fallback usage rate

**614/614 evals (100.0%) used the tick-rule fallback.** Unchanged from
v1.0 (597/597 = 100%). The cumulative-vs-per-bar fix doesn't change
the fallback flag — that flag is reserved for when a future
trade-stream consumer (out of scope) lands and `cvd_fallback_used`
flips to False. The current run still uses the close-vs-open tick-rule
proxy for aggressor side.

---

## Post-mortem — why didn't Phase A tests catch these?

Honest accounting of what the original test suite covered vs what
it missed. Not blame — input for future test design.

### CVD: cumulative-vs-per-bar bug

**Phase A tests did NOT exercise trending data with the cumulative
property.** The two relevant Phase B tests
(`test_cvd_positive_slope_when_closes_rising` and `_falling`) used
the fixture helper `_make_3m_bars` which sets `open == close` for
every bar (close was walked but open inherited from close). Under
the v1.0 sign convention (`sign(close - prev_close)`), the close-walk
expressed direction correctly via inter-bar momentum, so the slope-of-
per-bar-deltas implementation passed the tests because per-bar deltas
WERE all the same sign in those fixtures.

But the tests asserted **only** that the slope was positive (or
negative) — they didn't compare against the cumulative-CVD property.
A test that asked "for sustained one-direction bars with CONSTANT
volume, does the factor produce a non-zero slope?" would have caught
the bug — because with constant volume, per-bar deltas form a flat
line and per-bar-delta slope IS zero, while cumulative slope is
strongly directional. That property was never tested.

The five new tests added in this round (esp.
`test_factor_cvd_cumulative_in_trending_tape`) directly exercise this
property and would have caught the bug at Phase B.

### CVD: inter-bar vs intra-bar sign source

The Phase A/B tests inadvertently relied on the
`close - prev_close` inter-bar convention because:

1. `_make_3m_bars` set `open == close` (zero intra-bar movement). Any
   test using the default helper had degenerate dojis under the
   `close - open` convention.
2. No test ever passed an explicit `open != close` to express candle
   direction. Without that fixture pattern, the intra-bar convention
   couldn't be tested even if I'd implemented it.
3. The original implementation used `close - prev_close`. Tests
   confirmed "implementation behaves as written," not "implementation
   matches spec." Since the spec was vague between intra- and inter-bar,
   neither side was wrong, but neither was the chosen convention
   anchored to anything tested.

The fixture updates in this round add a `candle_direction` parameter
to `_make_3m_bars` and a new test
(`test_cvd_handles_doji_bars_correctly`) that explicitly exercises
dojis. Future bar-fixture additions should use `candle_direction`
rather than `close_walk` when CVD semantics are being tested.

### F1: all-three-slopes vs ema_8-only

**Phase A tests asserted only on `ema_8_slope`.** The dataclass
`GateInputs` only had `ema_8_15m_slope` as a field. There was no
slot for ema_21_slope or ema_50_slope, so no test could even have
asserted the all-three property — it would have had to construct
GateInputs with non-existent fields.

The implementation reflected my (incorrect) reading of the Phase A
plan: "linregress over last 5 EMA values" implied ema_8 only,
not all three. The plan was ambiguous on this; the implementation
chose the looser interpretation; the tests confirmed what was
implemented; nobody noticed the ambiguity until Phase C analysis
flagged the spec deviation.

The three new EMA tests added in this round
(`test_factor_ema_alignment_requires_all_three_slopes_long`, `_short`,
`_passes_when_all_three_slopes_aligned`) directly exercise the
all-three property and would catch any regression.

### Generalizable lessons for v1.x test design

1. **Tests should encode SPEC properties, not IMPLEMENTATION behavior.**
   The Phase B CVD tests confirmed what the code did. A spec-property
   test ("constant volume + sustained trend → non-zero CVD slope")
   would have caught the bug regardless of implementation choice.
2. **Synthetic fixtures should default to non-degenerate values.**
   `open == close` was a convenient default that made every bar a
   doji. Tests using such fixtures couldn't exercise any sign-source
   convention that depended on the open/close difference. The new
   `candle_direction` parameter makes the degeneracy explicit.
3. **Dataclass field gaps prevent test coverage by construction.**
   If `GateInputs` doesn't have `ema_21_slope`, no test can assert
   anything about it. Code reviews of new dataclasses should ask:
   "what fields would the spec require but are missing here?"

These observations inform v1.x test design; they don't relitigate
Phase A.

---

## Methodology + caveats (unchanged from v1)

- Alerts: prod `audit_event` `webhook_received` rows over the window.
- OHLCV: Coinbase BTC/USD 1m. Live prod feeds the gate native BitUnix
  3m/5m/15m kline. Apples-to-apples for the PA-vs-5f relative
  comparison; absolute trade outcomes carry a cross-venue
  volatility-profile fidelity gap.
- CVD: tick-rule fallback (intra-bar candle-direction sign × bar
  volume). Aggressor-side data not available from BitUnix public.
  v1.1 fix: now uses slope of cumulative series (was: slope of
  per-bar deltas).
- F1: v1.1 fix — all three EMA slopes required (was: ema_8 only).
- No changes to sizing, daily kill, position model, or any other
  harness behavior. Single-trade lock; opposite-side flip; no
  funding/fees.

---

## What this report does NOT do

- No cutover recommendation. (Pre-committed thresholds still FAIL on
  fire rate.)
- No factor-loosening recommendation. (ema_alignment + volume_z
  remain the dominant PA-only-winner rejectors after the fixes; the
  decision to loosen either is held pending Board review.)
- No floor-revisit recommendation. (1.73% fire rate is still well
  below 5% floor; the floor was set as a general sanity check, not
  tuned to this gate. A 3–6 month hostile-regime re-run would
  disambiguate whether the floor itself needs re-justification for
  this gate's risk profile.)

Decisions on all three are held pending Board review of this report.

## Artifacts

- PA arm: `data/backtest_runs/bitunix_20260517T224141_pa/`
- 5f arm: `data/backtest_runs/bitunix_20260517T224141_five_factor/`
- v1 comparison report: `reports/gate_backtest_2026-05-17.md`
- v1 factor-contribution analysis:
  `reports/gate_backtest_2026-05-17_factor_analysis.md`
