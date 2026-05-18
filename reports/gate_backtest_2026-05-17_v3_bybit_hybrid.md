# BitUnix Confluence-Gate v1.1 — Hybrid Bybit-bar backtest (v3)

**Date:** 2026-05-18 (Block A populated 00:30 UTC; Blocks B/C populated ~06:35 UTC)
**Status:** Complete. Block A headline: venue-gap is load-bearing — same gate, same alerts, Bybit bars instead of Coinbase ⇒ PF 2.63 → 1.14, WR 54.8% → 31.2%. Block B isolates the cause: per-factor pass rates are stable across windows (max Δ +3.6pp); synth-17d WR=31.1% matches prod-17d WR=31.2% exactly → cause is bar-source / trade-resolution, not alert-source. Block C: paper cutover is now the only path to discriminate the bar-fidelity vs over-fit hypotheses.

This report is the **venue-fidelity correction** of v1.0/v1.1's
Coinbase-1m backtests. Same v1.1 gate; same prod alerts (where
available); bars sourced from Bybit BTCUSDT.P 3m+15m (via
`data/btc_scalping.db`) plus Bitunix native 5m (cached pull). Alert
filter at ingest: drops `pink_box_*` and `smoke_test_*`.

---

## TL;DR

Same v1.1 gate, same 1,306 prod alerts, Bybit 3m+15m bars instead of
Coinbase 1m: **PF collapses 2.63 → 1.14, WR collapses 54.8% → 31.2%,
n stays roughly equal (31 → 32)**. Per-factor pass rates barely move
(biggest delta is vwap −7.4pp), so the gate is filtering similarly —
the **trades that pass are worse-quality** on Bybit data. Net +0.08%
on $10k starting equity is statistically indistinguishable from zero.

**Interpretation:** the venue gap was load-bearing in v1.1's Coinbase
result. The Bybit numbers are the corrected baseline. v1.1 on a
BitUnix-proximate venue does not clear the PF≥1.20 / WR≥45%
acceptance thresholds over 17 hostile-regime days.

**Caveats (do not skip):**
- CVD-fallback used in 100% of evaluations (Bybit DB has no taker
  buy/sell columns; tick-rule fallback was used throughout). Real
  CVD on Bybit may behave differently than this proxy.
- 17d window is hostile-regime — 29/32 fires were sells. A regime-
  varied sample would weight this differently.
- 3 of 72 prod-alert slices were stdout-truncated at the az 4KB
  cap (16T06, 17T12, 17T18); leading partial rows were dropped by
  the merge guard. The sample is missing ≤6 alerts of 1,306.
- Trade resolution is on Bybit 3m bars (no 1m source), coarser than
  v1.1's Coinbase 1m resolution. SL+TP-same-bar still assumes SL
  first per harness rule.

## Three result blocks

This report has three sections answering three distinct questions.
Do NOT blur them — each block stands alone.

### Block A: 17d Bybit-bar venue correction (HEADLINE)

**Question answered:** What changes when v1.1 is fed bars from a
venue (Bybit) closer to BitUnix than v1.0/v1.1's Coinbase source?

**Window:** 2026-04-30 → 2026-05-16 (overlap window where prod alerts
exist).

| Metric | v1.1 / Coinbase (from v2 report) | v1.1 / Bybit-hybrid (this run) | Δ |
|---|---|---|---|
| Fires | 31 | 32 | +1 |
| Round-trips | 31 | 32 | +1 |
| Win rate | 54.8% | **31.2%** | **−23.6 pp** |
| Avg R | +0.685 | +0.080 | −0.605 |
| Total R | +21.23 | +2.55 | −18.68 |
| Profit factor | 2.63 | **1.14** | **−1.49** |
| Fire rate | 1.73% | 2.45% | +0.72 pp |
| Return on $10k | — | +0.08% | — |
| Max DD | — | 0.28% | — |
| Avg bars held (1m equiv) | — | 35 | — |

**Tier mix:** PREMIUM 6 / STANDARD 26 / WEAK 0 (vs Coinbase mix not
broken out in v2 report — likely similar shape per v1.1 unchanged
score weights).

**Direction skew:** 29 sells / 3 buys — hostile-regime gate caught
the downside bias, but most fires resolved by SL not TP in the
post-knife chop.

**Exit decomposition:** TP 10 / SL 18 / flipped 3 / timeout 1.
Win R distribution is fat-tailed in both directions: range
[−9.61 R, +19.18 R], median per-trade R = −3.00. Net positive
comes from one 19.18 R outlier, not from a stable edge.

**SKIP-block decomposition (sanity vs first-attempt 0/1306):**
- Alerts processed: 1,306
- SKIPs: 766 (was 1,299 pre-fix — interval→tf conversion now correct)
- Cooldown-blocked: 72
- 5f-gate rejected: 380
- 5f-gate passed → fired: 32
- (1306 − 766 − 72 − 380 − 32 = 56 PA-rejected / disabled / other)

The 0/1306 pre-fix outcome was the alert-stream `interval`/`tf`
schema mismatch from CLAUDE.md `audit_event` payload (prod sends
`interval: '3'`, gate reads `tf: '3m'`); both `merge_prod_alert_slices.py`
and `tmp/pull_prod_alerts.sh` now convert. SKIP count of 766
is the post-fix steady-state (alerts outside structure-TF or pre-
warmup windows).

#### Per-factor pass rate diff vs Coinbase

Numerator: per-factor passes among the 461 evaluations where 5f-gate ran.
Denominators differ between Coinbase and Bybit (different bar series →
different windows survive warmup), so absolute counts are not directly
comparable, but rates are.

| Factor | v1.1 Coinbase | v1.1 Bybit | Δ |
|---|---|---|---|
| ema_alignment | 17.3% | 16.5% (76/461) | −0.8 pp |
| vwap | 42.8% | 35.4% (163/461) | **−7.4 pp** |
| volatility | 37.5% | 40.1% (185/461) | +2.6 pp |
| cvd | 56.7% | 55.7% (257/461) | −1.0 pp |
| volume_z | 16.8% | 14.5% (67/461) | −2.3 pp |

**CVD-fallback usage:** 461/461 (100%) on Bybit-hybrid (vs ~0% on
Coinbase, which had real tick-rule taker volume). The CVD factor's
pass rate is near-identical (56.7% → 55.7%), but the underlying
signal is the OHLCV proxy, not real CVD. **Real CVD on Bybit could
move this number materially in either direction.**

#### Interpretation

The venue-gap is **load-bearing** in v1.1's Coinbase result. Per-
factor pass rates moved only modestly (max −7.4pp on vwap, others
within ±3pp), but the trades that survived gating performed much
worse on Bybit-bar resolution + execution. Two non-exclusive
explanations:

1. **Coinbase 1m resolution flattered v1.1.** Finer entry/exit
   resolution gave better fills; coarser Bybit 3m resolution puts
   entries at less favorable points relative to SL/TP.
2. **Bybit alert-time prices differ from Coinbase.** TradingView
   alerts fire on TV's internal price feed; matching them to
   Coinbase bars on the v1.1 run vs Bybit bars on this run produces
   different effective entry prices for the same alert. That price
   delta compounds through SL/TP placement.

The CVD-fallback caveat is the largest remaining unknown. If
real-CVD on Bybit lifts pass rate to ~70%+, the score-distribution
shifts and tier mix changes; if it drops to ~30%, the gate becomes
even more restrictive. Either way, the headline collapse is large
enough that closing this gap is unlikely to flip the verdict to a
+PF≥1.20 result over this window.

**Acceptance threshold check:** v1.1 on Bybit-hybrid does **not**
clear the Phase C pre-committed bars: PF=1.14 vs ≥1.20 required;
WR=31.2% vs ≥45% required; n=32 vs ≥20 required (only this one passes);
fire rate=2.45% vs 5%–50% required (well below the 5% floor).
**3 of 4 thresholds fail.**

### Block B: Synth-31d internal-consistency check

**Question answered:** Is v1.1's internal behavior stable across
different alert distributions, OR does it depend on input distribution
in ways worth investigating?

**Window:** 2026-03-30 → 2026-04-30 (truly out-of-sample for v1.1).
**Alert source:** synth alerts from `data/btc_scalping.db` (via
`scripts/research_scoring/synth_ledger.py`).

#### Inherited May 16 caveats (verbatim)

> Result: the absolute fire counts here are ~10–15× the live trade rate.
> The prior live backtest recorded 21 fires; baseline shows 1,005 fires
> in the same kind of window. The difference is post-score gate filtering.
> **This means: absolute mean R / sum R / Sharpe numbers are NOT
> predictive of live trade outcomes.** They ARE valid for ranking
> variants against each other.
>
> — `reports/scoring_backtest_results.md` lines 15–25

This block inherits the above verbatim. **Synthetic alert distribution;
not predictive of live trade outcomes.** The point of the block is to
check internal stability of v1.1's factor behavior, not to make
generalization claims.

#### Internal-consistency metrics

| Metric | synth-17d (2026-04-30→05-16) | synth-31d (2026-03-30→04-30) | Δ |
|---|---|---|---|
| Total alerts in window | 3,439 | 6,465 | +88% |
| Fires | 74 | 163 | +120% |
| Buy/sell ratio | 5 / 69 (6.8% buy) | 16 / 147 (9.8% buy) | +3.0 pp buy |
| Tier mix (PREM/STD/WEAK) | 23 / 51 / 0 | 48 / 115 / 0 | proportional |
| 5f-gate pass rate | 9.6% (187/1938) | 11.4% (389/3418) | +1.8 pp |
| Per-factor: cvd | 52.7% | 52.6% | −0.1 pp |
| Per-factor: ema_alignment | 8.6% | 10.6% | +2.0 pp |
| Per-factor: volatility | 40.6% | 42.3% | +1.7 pp |
| Per-factor: volume_z | 14.7% | 14.1% | −0.6 pp |
| Per-factor: vwap | 15.5% | 19.1% | +3.6 pp |
| CVD-fallback usage | 100% | 100% | 0 pp |

**Absolute outcome metrics (NOT predictive — see caveat above):**

| Metric | synth-17d | synth-31d |
|---|---|---|
| Win rate | 31.1% | 25.8% |
| Profit factor | 1.01 | 0.74 |
| Avg R per trade | +0.005 | −0.182 |
| Total R | +0.36 | −29.68 |
| Return on $10k | −0.02% | −1.19% |
| Max DD | 0.61% | 2.18% |

#### Diagnostic flag — NOT TRIPPED

Pass-rate deltas between windows: max +3.6pp (vwap), all others
within ±2.0pp. **All factors within ±5pp threshold; flag does not
fire.** v1.1's factor-pass behavior is stable across input
distributions — the Block A verdict collapse is not explained by
factor instability.

#### Block B finding

The internal mechanics (gate-pass rates, factor pass rates,
buy/sell skew, tier mix) are stable across the synth-17d and
synth-31d windows. The absolute R/PF/WR numbers degrade in the
31d window (PF 1.01 → 0.74) but per the May 16 caveat these
absolute numbers are not predictive of live outcomes — only useful
for ranking variants.

**The Block A result is therefore not a synth-distribution artifact
or a windowing artifact.** It is a **bar-source artifact** —
something about the Bybit 3m+15m bars (vs Coinbase 1m) makes
gate-passing entries resolve worse.

The synth-17d-vs-prod-17d comparison is the cleanest evidence:
synth-17d WR=31.1%, prod-17d WR=31.2% — **same window, same gate,
same bar source, two completely different alert sources, and WR
is identical within rounding.** That isolates the cause to
bars+resolution, not alerts.

**Likely mechanical cause (hypothesis, not verified):**
trade resolution on Bybit 3m bars (no 1m source available) gives
SL+TP arbitration the SL-first benefit-of-doubt at 3m granularity,
where v1.1's Coinbase 1m run had finer resolution that more often
recorded a TP-touch before the SL-touch within the same coarse
window. This is a workable hypothesis to test — pull Bybit 1m
where available, re-run a single arm, see if WR recovers.

### Block C: What this work cannot deliver

**A truly-independent generalization test of v1.1 from available
backtest data.** Per state-of-knowledge memo § 6, prod's audit_event
table starts at 2026-04-30T17:27:16; no real prod alerts exist for
the 2026-03-30 → 2026-04-30 window. The btc_scalping.db's 47-day bar
history extends that far back, but cannot be matched against real
prod alerts.

The closest substitute would be synth alerts (Block B above), which
inherit the May 16 alertcondition-gap caveats and are explicitly NOT
generalization claims.

#### What Block A changed

Before Block A, the gap was "no out-of-sample data." After Block A,
the gap is sharper: **on the data we do have**, v1.1 fails 3 of 4
Phase C acceptance thresholds (PF=1.14 vs ≥1.20; WR=31.2% vs ≥45%;
fire-rate=2.45% vs ≥5%; only n=32 ≥ 20 passes). The Coinbase v1.1
result (PF=2.63) was the only PF-clearing result we had, and Block A
showed it was bar-source-dependent.

**Three load-bearing unknowns remain:**

1. Whether bar-resolution (Bybit 3m vs 1m) is the dominant cause of
   the verdict-collapse. Per Block B's mechanical hypothesis, this
   is testable with 1m Bybit pull if available.
2. Whether real CVD on Bybit (taker-volume column) materially shifts
   the gate's score distribution vs the OHLCV-proxy tick-rule
   fallback used 100% in Blocks A+B.
3. Whether the 17d hostile-regime window is representative of
   v1.1's behavior in mixed or constructive regimes. The synth-31d
   window (mostly pre-Apr-30 regime) shows further degradation
   (PF=0.74), suggesting the regime answer is "no" — v1.1 may be
   regime-fragile, not just window-fragile.

#### Paper cutover is the now the only path to vindication

**Paper-trading exposure is the only available path to answering the
generalization question.** The 60-day shadow-PA window planned for
paper cutover carries more evidential weight than it did when a
backtest backstop was expected.

**Specifically:** if paper-trading PF reverts to ≥1.20 over a 60-day
mixed-regime window, that is positive evidence the Block A collapse
was a backtest-fidelity artifact (bar resolution, CVD proxy, or
both). If paper-trading PF stays near 1.14 or below, that is positive
evidence v1.1 has been over-fit to v1.0's Coinbase-1m artifacts and
needs structural rework before live deployment.

**This shifts the framing of the 60-day shadow window from "confirm
the backtest result" to "discriminate between backtest-fidelity
explanation and over-fit explanation." Both possibilities should be
named in the paper-cutover decision memo so the data is interpreted
on the correct prior.**

---

## IS-contamination caveat (per Board direction)

The 31d synth backtest window (2026-03-30 → 2026-04-30) sits inside
the May 16 score-engine variant research's in-sample window (2026-03-30
→ 2026-05-02). May 16 selected H2/H6b/H4b variant recommendations
partly based on performance over this data.

**But:** the May 16 variant recommendations were not shipped to prod
(per `reports/scoring_recommendation.md`). The scoring weights v1.1
evaluates on are the pre-May-16 PR 3c baseline.

**Consequence:** the contamination affects hypothesis-selection bias
only for the score engine, NOT the actual gate evaluation. The 31d
window IS legitimately out-of-sample for the v1.1 5-factor gate.

## Methodology

- **Block A bars:** Bybit BTCUSDT.P 3m + 15m from `data/btc_scalping.db`;
  Bitunix BTCUSDT 5m from `data/historical_alerts/cache_ohlcv_bitunix_5m_*.json`
  (pulled this session, paginated via Bitunix native REST with
  startTime/endTime cursoring).
- **Block A trade resolution:** Bybit 3m bars (no 1m source available).
  Coarser than Coinbase 1m resolution. SL+TP-same-bar still assumes
  SL-first per existing harness rule.
- **Block A 5m alignment check:** 20/20 sample points within 1.3bps
  vs Bybit 3m at 15-min boundaries (`tmp/alignment_check.txt`).
- **Block A prod alerts:** pulled via `az vm run-command invoke`
  paginated by 6h windows (az stdout capped at ~4KB); filtered at
  ingest for pink_box, smoke_test, empty-signal rows.
- **Block B alerts:** `synth_ledger.load_synth_ledger()` rising-edge
  detection on DB indicator columns; pink_box already inert per
  COL_TO_FACTOR's existing marking.
- **Gate config:** `ConfluenceGateConfig(enabled=True, min_gate_score=3)`
  with v1.1 defaults — same as v1.0/v1.1/v2 runs.
- **No changes to acceptance thresholds** (still PF≥1.20, WR≥45%,
  n≥20, fire rate ∈ [5%, 50%]).

## What this report does NOT do

- Make a cutover recommendation. (Decision authority is Board after
  reviewing this + the state-of-knowledge memo.)
- Recommend factor loosening. (Held pending Q2 re-analysis on hybrid
  results.)
- Recommend floor revisit. (Per Board, floor decision is independent
  of factor analysis.)
- Provide a truly-independent generalization test. (Block C explains
  why — data gap.)

## Artifacts

- **Block A hybrid 17d run:** `data/backtest_runs/bitunix_20260518T042506_five_factor/` (summary.md, trades.json, ledger.json)
- **Block A prod-alert cache:** `data/historical_alerts/cache_alerts_prod_filtered_20260430_20260518.json` (1,717 unique alerts merged from 72 az-paginated 6h slices; 3 truncation-flagged but leading-row guard fired)
- **Block B synth-17d run:** `data/backtest_runs/bitunix_20260518T103210_synth_17d/`
- **Block B synth-31d run:** `data/backtest_runs/bitunix_20260518T103208_synth_31d/`
- Prior runs: `reports/gate_backtest_2026-05-17.md` (v1), `reports/gate_backtest_2026-05-17_v2.md` (v1.1 Coinbase), `reports/gate_backtest_2026-05-17_factor_analysis.md`
- State-of-knowledge memo: `docs/memos/2026-05-17_gate_v1.1_state_of_knowledge.md`

---

## Addendum (Branch A, 2026-05-18) — 1m trade-resolution disambiguation

**Status:** Complete. Branch A picked up after the v3 report's Block C
identified two named hypotheses (H1: bar-resolution flattered v1.1
on Coinbase; H2: cross-venue alert-time price divergence) and proposed
that 1m trade-resolution could partially disambiguate them locally
before paper data lands.

### What this addendum does NOT modify

Blocks A, B, and C of the original v3 report are unchanged. This
addendum adds a fourth experiment that varies a single dimension
(trade-resolution bar source) while holding the rest of Block A
constant.

### What changed in this addendum vs Block A

Hold v1.1 gate + alert source + entry-price context constant. Swap
**only** the trade-resolution bars from Bybit 3m → Bitunix 1m.

| Dimension | Block A | Addendum 1m arm |
|---|---|---|
| Gate evaluation bars (3m / 5m / 15m) | Bybit 3m+15m + Bitunix 5m | unchanged |
| Entry-price context (`ctx.current_price`) | Bybit 3m bar close | unchanged |
| Trade-resolution walk (`resolve_trade`) | Bybit 3m bars | **Bitunix 1m bars** |
| Alert stream | Prod cache | unchanged |
| Window | 17d (2026-04-30 → 2026-05-16) | unchanged |
| Gate config | v1.1 (`min_gate_score=3`) | unchanged |

This is a **mixed-venue** experiment by necessity: Bybit is geo-blocked
from the local network, so Bybit 1m is not accessible. Bitunix is the
production trade venue, making Bitunix 1m the most operationally
relevant substitute. The venue-swap confound on the resolution path
is bounded but real; see § Cross-venue confound below.

### Data + tooling additions (all additive)

- `data/historical_alerts/cache_ohlcv_bitunix_1m_20260430_20260517.json` —
  Bitunix 1m bars (24,442 rows; 17×24×60 = 24,480 expected, 38
  intermittent single-bar drops; not material).
- `bars_1m` table in `data/btc_scalping.db` — 7-column OHLCV subset.
  **Lineage note:** bars_3m / bars_15m come from a TradingView CSV
  export of BYBIT_BTCUSDT.P (via `scripts/ingest_tv_export.py`);
  bars_1m comes from Bitunix native REST. Different venues, recorded
  in `scripts/ingest_bitunix_1m_to_db.py` docstring.
- `scripts/ingest_bitunix_1m_to_db.py` — JSON cache → SQLite UPSERT.
- `scripts/verify_bars_1m_alignment.py` — cross-venue close + open
  alignment vs bars_3m at 3-minute boundaries.
- `scripts/backtest_bitunix_confluence.py` — additive change:
  `resolution_bars: list[dict] | None = None` parameter on
  `run_backtest()` (defaults to existing `bars`, preserving legacy
  behavior). `--resolution-tf {3m,1m}` CLI flag (default 3m).
- No changes to v1.1 gate code, PA validator, scorer, or risk path.

### Cross-venue alignment (Bitunix 1m vs Bybit 3m)

Match Bybit `bars_3m[T].close` to Bitunix `bars_1m[T+120].close` (the
last 1m bar of the 3-minute window), and Bybit `bars_3m[T].open` to
Bitunix `bars_1m[T].open`. 7,745 + 7,741 pairs over the 17d window.

| Stat | Value |
|---|---|
| Median |Δ| | 0.53 bps |
| p95 |Δ| | 1.57 bps |
| Max |Δ| | 11.56 bps |
| Outliers > 10 bps | 1 / 15,486 (~0.006%) |

Single outlier on 2026-05-07T02:30:00 UTC (Bybit 80,988.60 vs Bitunix
80,895.00). Neither outcome-shifted trade (see Results below) occurs
near that timestamp.

### Asymmetric resolution-statistics caveat (named pre-run)

Finer trade-resolution bars give more independent high/low
observations per unit time. This generally **increases** the rate of
SL touches detected. Intra-3m-bar wicks that didn't reach SL when
smoothed into a single 3m bar may produce real SL touches when split
into 1m bars. The 1m arm could plausibly show WR **decline** rather
than recovery, exposing hidden losses the 3m harness missed.

This is named explicitly so outcome interpretation is disciplined: a
WR drop would not invalidate v1.1's design but would invalidate the
3m backtest's outcome accuracy. Three outcomes were framed pre-run:

1. WR recovers significantly → H1 supported.
2. WR roughly unchanged → weak evidence against H1; H2 surviving by default.
3. WR drops → 3m backtest may have been optimistic; finer bars revealed hidden SL touches.

### Baseline re-run (default 3m) — apples-to-apples reference

Re-ran Block A's configuration with `--resolution-tf 3m` (default;
same code path as the original v3 Block A) to establish the
apples-to-apples baseline against the 1m arm.

| Metric | Original v3 Block A | Re-baseline 3m (today) | Drift |
|---|---|---|---|
| Alerts processed | 1,306 | 1,504 | +198 (cache extended) |
| Fires | 32 | 32 | 0 |
| Round-trips | 32 | 32 | 0 |
| Win rate | 31.2% | 31.2% | 0 |
| Profit factor | 1.14 | 1.14 | 0 |
| Avg R | +0.082 | +0.082 | 0 |
| Total R | +2.55 | +2.61 | +0.06 |

The drift is isolated to a single trade (trade #31 opening at
2026-05-15T19:03:01) whose timeout-close shifted from
2026-05-15T23:57 → 2026-05-16T03:42. Cause: `bars_3m` table extended
by +3h45min between the original v3 run (~04:25 UTC) and the
re-baseline (~14:20 UTC), giving the walk more bars to traverse
before timing out. The first 31 trades are byte-identical to the
original artifact.

The alert-count delta (1,306 → 1,504) is also a data extension: the
prod-alerts cache file was added to between original run and today;
the additional 198 alerts all SKIP or get cooldown-blocked (fire
count is unchanged at 32). Both the re-baseline and the 1m arm see
the same 1,504-alert input. Apples-to-apples within today's data state.

### 1m Bitunix-resolution arm — results

| Metric | Baseline (3m re-run) | 1m Bitunix arm | Delta |
|---|---|---|---|
| Alerts processed | 1,504 | 1,504 | 0 |
| Fires | 32 | 31 | −1 |
| Round-trips | 32 | 31 | −1 |
| Win rate | 31.2% | **35.5%** | **+4.3 pp** |
| Profit factor | 1.14 | **1.30** | **+0.16** |
| Total R | +2.61 | **+5.24** | **+2.63** |
| Avg R | +0.082 | +0.169 | +0.087 |
| Return on $10k | +0.08% | +0.22% | +0.14 pp |
| Max DD | 0.28% | 0.28% | 0 |
| TP / SL / Flipped / Timeout | 10 / 18 / 3 / 1 | 11 / 17 / 3 / 0 | +1 TP, −1 SL, −1 timeout |
| Per-factor pass rates | (see Block A) | within ±2 pp of baseline | minor |
| CVD-fallback usage | 100% | 100% | unchanged |
| Buy / Sell mix | 3 / 29 | 3 / 28 | −1 sell |

**Two outcome shifts isolated to specific trades** (out of 30 shared
open-timestamp trades; 2 baseline-only opens, 1 1m-arm-only open due
to cascade effects of slightly different in-flight resolution):

| Open ts | Side | 3m outcome | 1m outcome | Δ R |
|---|---|---|---|---|
| 2026-05-13T21:09:01 | sell | sl (−1.00R) | tp (+2.00R) | +3.00 |
| 2026-05-15T19:03:01 | sell | timeout (+0.36R) | tp (+2.00R) | +1.64 |

**Both shifts went L/timeout → TP.** The asymmetric resolution effect
(hypothesized as a possible mechanism for WR decline) did **not**
manifest on this window — SL count fell from 18 → 17 rather than
rising. This is informative: the 3m backtest was not optimistically
biased by SL-smoothing on this sample.

### Phase C acceptance check on the 1m arm

Pre-committed acceptance thresholds (unchanged from Phase C):

| Criterion | Threshold | 3m baseline | 1m Bitunix | Was → Is |
|---|---|---|---|---|
| Profit factor | ≥ 1.20 | 1.14 (FAIL) | 1.30 (PASS) | newly passes |
| Win rate | ≥ 45.0% | 31.2% (FAIL) | 35.5% (FAIL) | still fails |
| Round-trips | ≥ 20 | 32 (PASS) | 31 (PASS) | still passes |
| Fire rate | ∈ [5.0%, 50.0%] | 2.13% (FAIL) | 2.06% (FAIL) | still fails |

**2 of 4 thresholds pass on 1m** vs **1 of 4 on 3m**. PF crosses the
acceptance bar for the first time on Bitunix-proximate data. WR and
fire-rate still fail. This is **not a cutover-eligible result** even
under the most favorable trade-resolution arm.

### Three-outcome interpretation (per pre-run framing)

The observed result is closest to **outcome 1 (H1 partially
supported)** with significant residual gap:

- **H1 (bar-resolution flattered v1.1):** Real but partial.
  Moving from 3m → 1m on the same gate-pass alerts recovers ~+0.16
  PF and ~+4.3 pp WR. Direction matches the H1 prediction; magnitude
  is modest. PF crosses the 1.20 acceptance bar for the first time
  on Bitunix-proximate data, which is a meaningful improvement
  in the evidence picture even though it does not bridge to the
  v1.1/Coinbase PF=2.63 / WR=54.8% headline.
- **H2 (cross-venue alert-time price differs):** Surviving as the
  most parsimonious explanation for the remaining ~20pp WR gap and
  the residual ~1.3 PF gap. H2 is **not confirmed by this
  experiment** — the residual could also be CVD-fallback artifacts
  (100% in both arms), bar-source differences in the gate-evaluation
  path (Bybit 3m+15m + Bitunix 5m vs Coinbase 1m resampled in the
  original v1.1 run), or other factors. The residual is the size H2
  would need to plausibly account for.
- **Outcome 3 (3m hid SLs):** Did not occur. SL count decreased on
  1m. The original v3 Block A's 3m result was not optimistically
  biased by SL-smoothing on this sample.

**Synthesis:** H1 is partially load-bearing. Paper-trading shadow
data should be expected to land somewhere in the range bounded by
PF=1.14 (3m-on-Bybit-hybrid) and PF=2.63 (1m-on-Coinbase), reflecting
**partial-H1-recovery + partial-residual-effect** — rather than
either extreme. The paper-cutover decision memo should name this
expectation explicitly so the 60-day shadow window is interpreted on
the correct prior.

### Cross-venue confound (what this experiment does NOT prove)

The experimental setup couples bar-resolution with venue. Trade
resolution uses **Bitunix 1m**; entry-price context uses **Bybit 3m**.
The cross-venue alignment is tight (median 0.53 bps cross-venue close
delta at 3m boundaries) but not zero. This experiment does NOT
discriminate:

1. Whether the SL/TP touch dynamics on Bitunix venue would have been
   identical to Bybit venue if Bybit 1m had been accessible. Cross-
   venue micro-divergence at the single-minute scale can shift exact
   SL/TP touch timestamps.
2. Whether the residual ~20pp WR gap is H2 (alert-time price
   source), CVD-fallback artifact (100% in both arms), or
   gate-input-bar-source artifact (Bybit 3m+15m vs Coinbase
   1m-resampled).
3. Whether v1.1 would generalize to mixed-regime data — this 17d
   window is hostile-regime per v3 Block A's caveat (29/32 fires
   were sells). Block B's synth-31d already showed regime fragility
   (PF=0.74 over an earlier 31d window) for the synth alert stream.

### Updates to the state-of-knowledge memo § 8 framing

The memo's pre-addendum framing named two hypotheses paper-cutover
shadow data would discriminate. This addendum partially resolves H1
(real but insufficient on its own) and elevates the importance of
H2 + other residual factors in the cutover memo's expected-outcome
range. The paper-cutover decision memo's expected PF range should
be specified as **[1.14, 2.63] with central tendency near the
middle**, not either extreme. See `docs/memos/2026-05-17_gate_v1.1_state_of_knowledge.md`
§ 8 for the updated framing.

### Artifacts

- 3m baseline re-run: `data/backtest_runs/bitunix_20260518T142023_baseline_3m_repro/`
- 1m Bitunix arm: `data/backtest_runs/bitunix_20260518T142136_resolution_1m_bitunix/`
- Bitunix 1m JSON cache: `data/historical_alerts/cache_ohlcv_bitunix_1m_20260430_20260517.json` (24,442 bars; 38 single-bar drops)
- `bars_1m` table in `data/btc_scalping.db`
- Ingest: `scripts/ingest_bitunix_1m_to_db.py`
- Alignment verify: `scripts/verify_bars_1m_alignment.py`
- Harness change: `scripts/backtest_bitunix_confluence.py` (additive `resolution_bars` parameter + `--resolution-tf` flag)

**Tests:** All 78 BitUnix-related tests in
`tests/test_backtest_bitunix_confluence_five_factor.py`,
`tests/test_bitunix_confluence_gate.py`, and
`tests/test_bitunix_gate_inputs.py` pass. The additive `resolution_bars`
parameter defaults to None which falls back to the existing `bars` walk,
preserving the legacy default-path behavior the tests cover.
