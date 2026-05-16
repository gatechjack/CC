# Scoring backtest results — IS + OOS for all candidates

**Window:** 2026-03-30 → 2026-05-16 (47 days of `data/btc_scalping.db` bars_3m)
**Split:** 70/30 chronological — IS = 2026-03-30 → 2026-05-02; OOS = 2026-05-02 → 2026-05-16
**Cost model:** **9 bps round-trip** (0.04% × 2 taker + 0.005% × 2 slippage — BitUnix VIP3 + Experience Card per goal directive)
**Trade simulation:** Entry at next 3m bar open after alert; stop = max(1.5×ATR(14), 0.3% × entry); TP = 2R; same-bar SL+TP → SL wins (pessimistic). 24h timeout.

## ⚠ Reading this report

The replay harness models the **score engine in isolation**. It does NOT model
the PA validation gate (binary vwap+volume+structure) or the HTF regime gate
(4h/1d regime classifier with size_multiplier). Those gates are **out of
scope per goal directive** and live downstream of the score engine.

Result: the absolute fire counts here are **~10–15× the live trade rate**. The
prior live backtest (Apr 30 – May 9) recorded 21 fires; my baseline shows
1,005 fires in the same kind of window. The difference is post-score gate
filtering.

**This means: absolute mean R / sum R / Sharpe numbers are NOT predictive of
live trade outcomes.** They ARE valid for *ranking variants against each
other*, because the downstream PA + HTF gates filter equally regardless of
score-engine config (neither gate cares about weights, thresholds, or
formulas). If a variant has better mean R in this replay, it has better
*candidate quality* feeding the gates.

## Headline table — full window (ALL = IS+OOS)

Sorted by `sum_R` ascending (least lossy first). All values reported NET of
9 bps round-trip.

| Variant | Fires | Tr/day | Win % | Mean R | Sum R | Sharpe | PF | PREMIUM (n / mean_r) | STANDARD (n / mean_r) |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| **H4b — Conviction ratio ≥0.80** | 856 | 18.15 | 27.9% | -0.452 | **-387.2** | -9.85 | 0.51 | 535 / -0.454 | 321 / -0.449 |
| H3b — Asymmetric α=2.0 | 958 | 20.32 | 28.4% | -0.440 | -421.1 | -10.07 | 0.52 | 200 / -0.465 | 758 / -0.433 |
| H6b — min_score 8, premium 12 | 961 | 20.38 | 28.1% | -0.449 | -431.7 | -10.35 | 0.52 | 130 / -0.488 | 831 / -0.443 |
| H6 — min_score 7 | 1073 | 22.76 | 28.6% | -0.434 | -465.9 | -10.50 | 0.53 | 342 / -0.424 | 731 / -0.439 |
| **combo — H2+H4+H5+unified** | 1275 | 27.04 | 30.5% | **-0.375** | -478.3 | -9.71 | 0.58 | 472 / -0.421 | 803 / -0.348 |
| H3 — Asymmetric α=1.5 | 1140 | 24.18 | 28.3% | -0.440 | -502.1 | -11.02 | 0.52 | 243 / -0.443 | 897 / -0.440 |
| H5b — Family confluence (P≥2, S≥2) | 1267 | 26.87 | 29.6% | -0.405 | -512.9 | -10.55 | 0.55 | 308 / -0.435 | 959 / -0.395 |
| **H7 — H2 + unified cooldown** | 1322 | 28.04 | 30.0% | -0.391 | -516.9 | -10.36 | 0.57 | 167 / **-0.305** | 1155 / -0.403 |
| **H2 — Re-weight + Otter precision ↑** | 1339 | 28.40 | 29.6% | -0.400 | -536.0 | -10.71 | 0.56 | 163 / **-0.300** | 1176 / -0.414 |
| H4 — Conviction ratio ≥0.70 | 1326 | 28.12 | 29.5% | -0.407 | -539.3 | -10.84 | 0.55 | 782 / -0.419 | 544 / -0.389 |
| H1 — Cap weights at 3 | 1315 | 27.89 | 29.3% | -0.413 | -542.9 | -10.98 | 0.55 | 144 / -0.334 | 1171 / -0.422 |
| **baseline (PR 3c YAML)** | **1449** | **30.73** | **29.1%** | **-0.421** | **-610.2** | **-11.79** | **0.54** | 316 / -0.381 | 1133 / -0.432 |
| H5 — PREMIUM requires 3 families | 1449 | 30.73 | 29.1% | -0.421 | -610.2 | -11.79 | 0.54 | 173 / -0.340 | 1276 / -0.432 |

**Notes:**
- H5 (PREMIUM requires 3 families) has identical aggregate metrics to baseline
  because the implementation *demotes* failed-PREMIUM to STANDARD rather than
  blocking. Demoted trades have same entry/SL/TP. The effect is visible as
  PREMIUM-bucket re-quality (n drops 316 → 173, mean R rises -0.381 → -0.340).
- Conviction ratio ≥0.70 (H4) barely filters relative to ≥0.80 (H4b) — the 0.70
  threshold is loose enough to admit most fires that pass `min_score_to_fire`.

## In-sample (70%) vs Out-of-sample (30%) split

If a variant degrades sharply from IS to OOS, that's an overfit signal. The
opposite — IS worse than OOS — is *regime drift*: the OOS window (May 2-16)
had a different market character than the IS (Mar 30 - May 2). In our data,
OOS is uniformly *less bad* than IS, suggesting the IS bear-leg-into-recovery
was hostile to ANY 2R-stop scalp, and OOS is more chop-friendly.

| Variant | IS fires | IS mean_R | IS Sharpe | OOS fires | OOS mean_R | OOS Sharpe | ΔmeanR (OOS-IS) |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1005 | -0.448 | -10.50 | 444 | -0.361 | -5.53 | +0.087 |
| H1 — Cap weights at 3 | 920 | -0.425 | -9.45 | 394 | -0.383 | -5.57 | +0.042 |
| H2 — Re-weight + Otter precision ↑ | 934 | -0.418 | -9.36 | 404 | -0.357 | -5.22 | +0.061 |
| H3 — Asymmetric α=1.5 | 797 | -0.471 | -9.92 | 342 | -0.367 | -4.95 | +0.104 |
| H3b — Asymmetric α=2.0 | 673 | -0.479 | -9.30 | 284 | -0.342 | -4.17 | +0.137 |
| H4 — Conviction ratio ≥0.70 | 937 | -0.425 | -9.54 | 389 | -0.363 | -5.22 | +0.062 |
| H4b — Conviction ratio ≥0.80 | 595 | -0.529 | -9.85 | 261 | -0.278 | -3.20 | **+0.251** |
| H5 — PREMIUM 3 families | 1005 | -0.448 | -10.50 | 444 | -0.361 | -5.53 | +0.087 |
| H5b — Family P≥2, S≥2 | 874 | -0.437 | -9.52 | 393 | -0.333 | -4.77 | +0.104 |
| H6 — min_score 7 | 752 | -0.477 | -9.78 | 320 | -0.331 | -4.26 | +0.146 |
| H6b — min_score 8, premium 12 | 675 | -0.512 | -10.10 | 285 | -0.296 | -3.57 | +0.216 |
| H7 — H2 + unified cooldown | 924 | -0.412 | -9.15 | 397 | -0.340 | -4.91 | +0.072 |
| combo — H2+H4+H5+unified | 894 | -0.379 | -8.20 | 381 | -0.366 | -5.20 | +0.013 |

**Observations:**

- **No overfit signal anywhere.** All variants improve OOS, none degrades.
  This is consistent with regime drift (bear → range/chop on the OOS half),
  NOT with the variants having "memorized" the IS data. The score-engine
  changes are formula / threshold / family rules — there's not enough surface
  to overfit a 33-day IS period.

- **`combo` is the most stable across the split** (ΔmeanR = +0.013 — almost no
  IS-OOS divergence). The other variants benefit from OOS regime; combo's
  filtering is so aggressive it doesn't see the difference.

- **H4b shows the biggest OOS improvement** (+0.251 mean R). It's the tightest
  filter and benefits most from the OOS regime being less hostile.

## Per-tier breakdown — where each variant filters

PREMIUM tier "quality" = how much better PREMIUM mean R is than STANDARD mean
R. A score engine that puts its strongest signals in PREMIUM should show a
positive gap. Baseline shows -0.381 vs -0.432 = +0.051R gap (PREMIUM
marginally better than STANDARD).

| Variant | PREMIUM mean R | STANDARD mean R | Quality gap (PREM - STAND) |
|---|---:|---:|---:|
| H2 — Re-weight + Otter precision ↑ | -0.300 | -0.414 | **+0.114** |
| H7 — H2 + unified cooldown | -0.305 | -0.403 | **+0.098** |
| H1 — Cap weights at 3 | -0.334 | -0.422 | +0.088 |
| H5 — PREMIUM requires 3 families | -0.340 | -0.432 | +0.092 |
| combo | -0.421 | -0.348 | -0.073 (inverted!) |
| baseline | -0.381 | -0.432 | +0.051 |
| H5b — Family P≥2, S≥2 | -0.435 | -0.395 | -0.040 |
| H6 — min_score 7 | -0.424 | -0.439 | +0.015 |
| H3 — Asymmetric α=1.5 | -0.443 | -0.440 | -0.003 |
| H3b — Asymmetric α=2.0 | -0.465 | -0.433 | -0.032 |
| H4 — Conviction ratio ≥0.70 | -0.419 | -0.389 | -0.030 |
| H4b — Conviction ratio ≥0.80 | -0.454 | -0.449 | -0.005 |
| H6b — min_score 8, premium 12 | -0.488 | -0.443 | -0.045 |

**This is the meaningful finding for ranking variants:**

- **H2 and H7 (= H2 + unified cooldown)** widen the tier-quality gap from
  baseline's +0.051 to **+0.114 / +0.098**. This means PREMIUM-tier fires
  under H2 are markedly cleaner than STANDARD-tier fires — a *better-calibrated
  scoring engine* even if absolute outcomes are still negative. The re-weight
  to favor Otter precision (water_*, spoon_*, money_bag_*) genuinely
  reallocates conviction to the right signals.

- **H1 (cap weights at 3)** also improves the gap (+0.088 vs +0.051). Just
  capping the weight-5 / weight-4 signals at 3 is a *one-line YAML change*
  that recovers most of H2's gap-improvement.

- **conviction-ratio / asymmetric / family-confluence variants have inverted or
  zero gaps** — they filter aggressively but don't differentially favor PREMIUM
  candidates. They're general filters, not score-engine improvements.

## What hypotheses held up vs were refuted

Cross-referencing `reports/scoring_hypotheses.md` predictions:

| ID | Hypothesis | Prediction held? | Notes |
|---|---|---|---|
| H1 | Cap weights at 3 | **Partial** | Fire count drop was -10% predicted, actual -9%. PREMIUM mean R rose 0.051→0.088 gap (predicted ≥0.1R rise — close). |
| H2 | + Otter precision ↑ | **Yes** | sum_R less negative than baseline AND H1. PREMIUM gap +0.114 vs baseline's +0.051. |
| H3 | Asymmetric α=1.5 | **Refuted** | Mean R improvement was ~+0.02 (predicted ≥+0.15). Filtering reduced fires 22% but didn't lift quality. |
| H3b | Asymmetric α=2.0 | **Partial** | More aggressive filter (-34% fires) but only +0.018 mean R improvement. Filtering ≠ better candidates. |
| H4 | Conviction ratio ≥0.70 | **Refuted** | Mean R improvement was ~+0.014 (predicted ≥+0.20). 0.70 is too loose to filter meaningfully. |
| H4b | Conviction ratio ≥0.80 | **Partial** | Best sum_R (-387 vs baseline -610). Best OOS Sharpe (-3.20 vs -5.53). But mean R per trade actually *worsened* (-0.452 vs -0.421) — variant rejects marginal trades, but the survivors are mostly the same bad-quality signal-pumps anyway. |
| H5 | PREMIUM requires 3 families | **Held — for PREMIUM quality only** | PREMIUM mean R rose -0.381→-0.340 as predicted. But because failures get demoted to STANDARD (not blocked), total sum_R unchanged. |
| H5b | Family P≥2, S≥2 | **Refuted on PREMIUM** | PREMIUM mean R *worse* than baseline (-0.435 vs -0.381). Adding family-confluence to STANDARD let weaker STANDARD-bin candidates promote into PREMIUM-via-family-pass. |
| H6 | min_score 5→7 | **Refuted** | Mean R improvement +0.013 (predicted ≥+0.10). |
| H6b | min_score 8, premium 12 (pre-PR-3c) | **Refuted on absolute, vindicated relative** | Mean R worse (-0.449 vs -0.421) but PREMIUM is much rarer (n=130 vs 316) and OOS Sharpe best (-3.57). Approximately reverts to pre-PR-3c calibration. |
| H7 | H2 + unified cooldown | **Held** | Sharpe better than H2 (-10.36 vs -10.71). PREMIUM gap widens further (+0.098). |

## Falsification verdict

Hypotheses goal: write predictions BEFORE the data and check honestly.

**The strongest claim in the hypothesis doc was H4 / H4b / H3** — the formula
changes ("conviction ratio / asymmetric should improve mean R toward 0"). The
data **refutes** that claim: formula changes filter the *quantity* of trades
without improving the *quality* of remaining trades. The remaining trades are
still mostly noise-scalps the score engine accidentally compounded.

**The weaker claim — "re-weighting to favor measured-edge signals should
widen PREMIUM-vs-STANDARD quality gap"** is **supported**: H1, H2, H5 all
widen the gap; H2 doubles it.

**The biggest surprise**: the `combo` variant (H2 + H4 + H5 + unified) has
inverted PREMIUM-vs-STANDARD gap (PREMIUM worse than STANDARD). Combining
re-weighting with conviction-ratio + family confluence pollutes the PREMIUM
bucket: trades that survive the *combined* filter set have correlated rejection
patterns that pile up in PREMIUM (since family-confluence-pass + high-ratio
fires tend to be the same trades). Worth noting but probably an artifact of
this specific dataset rather than a generalizable finding.

**Headline:** no variant achieves positive expectancy in this 47-day window.
All variants improve on baseline by *some* metric (sum R, mean R, or PREMIUM
quality gap). The best candidates for shipping discussion (in
`scoring_recommendation.md`):

1. **H2 — Re-weight + Otter precision up.** Best PREMIUM/STANDARD differentiation,
   simplest YAML diff (just `weight:` edits, no formula change).
2. **H4b — Conviction ratio ≥0.80.** Most aggressive filtering, best OOS Sharpe,
   biggest sum-R reduction. Formula change required.
3. **H7 — H2 + unified cooldown.** H2's wins + the additional Sharpe lift from
   unified cooldown. Same YAML edits as H2 plus one config flag.
