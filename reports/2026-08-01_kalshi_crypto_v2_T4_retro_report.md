# kalshi_crypto_v2 — T4 Retro-Test Report (SFP UP-signals vs Kalshi 15-min up/down)

**Date:** 2026-08-01 · **Branch:** `claude-2026-08-01b` · **Scripts:** `research/kalshi_crypto_v2/t4_retro.py`
(+ `run_signals_retro.py`, `signals_retro.csv`, `t4_alignment.csv`).

> **STRUCTURAL READ ONLY — n=23.** This ranks the lifted SFP signal and warns of gross mis-prediction; it
> **cannot green-light anything**. There is no verdict here and none should be inferred. With n=17–23 the
> standard error on a proportion is ~0.11, so any skill under ~±0.11 is inside noise. Canonical edge is
> decided ONLY by the T2 forward corpus with **EV-at-fill** (yes-ask + fees + real spread at realistic
> size). Every number below labeled **pseudo-EV(candle)** is a TRADE-PRICE-BASED ranking proxy (candle
> yes-mid); it understates the ask you would actually pay and is **never** EV-at-fill.

## Method
- Signals: the 23 BOS-confirmed **long (UP)** SFP entries from T3a (lifted `bitunix_sfp` AS-IS, no tuning),
  Mode-A (15m BOS) + Mode-B (3m BOS), across BTC/ETH/SOL/XRP, 2026-07-12 → 07-29.
- Alignment: a Kalshi up/down window `[T, T+900]` sets `floor_strike = BRTI(T)` at open and settles **YES
  iff `BRTI(T+900) >= floor_strike`** (price rose over the 15 min). Each signal maps to the **next** window
  opening at `ceil(entry_ts/900)*900`, predicting **YES**. All 23 matched a settled market (0 unmatched).
- `move% = (expiration_value − floor_strike)/floor_strike`. Flat-window rule: `|move%| < threshold` →
  excluded from headline accuracy/EV, reported separately. Sensitivity at **0.02% / 0.05% / 0.10%**.
- pseudo-EV(candle) per signal = `payoff(1 if YES else 0) − yes_mid_at_open − kalshi_fee(1, yes_mid)`,
  using the 1-minute candle at the window's first minute. Fee = canonical `_sports_math.kalshi_fee`.

## UP base rate (ALL settled 15-min windows in the overlap, per asset)
Overlap = 2026-06-23 → 2026-08-01 (Bitunix-bar limited). Skill is accuracy **relative to this base**.

| asset | series | n windows | UP base rate |
|---|---|---|---|
| BTC | KXBTC15M | 3,770 | 0.499 |
| ETH | KXETH15M | 3,770 | 0.506 |
| SOL | KXSOL15M | 3,770 | 0.496 |
| XRP | KXXRP15M | 3,770 | 0.502 |
| **pooled** | — | 15,080 | **0.501** |

The market is a near-symmetric coin flip (~0.50) — **unlike** the shelved inquiry's 90%-base-rate
near-certain buckets. So win-rate is at least *informative* here (0.50 reference), though still not a gate.

## Signal accuracy vs base rate — flat-window sensitivity
| flat threshold | directional n | accuracy | base | **skill** | pseudo-EV(candle) [ranking, fees in] | excluded flat (n / acc) |
|---|---|---|---|---|---|---|
| `|move| < 0.02%` | 23 | 0.522 | 0.501 | **+0.021** | +0.0209 | 0 / — |
| `|move| < 0.05%` | 21 | 0.524 | 0.501 | **+0.023** | +0.0283 | 2 / 0.500 |
| `|move| < 0.10%` | 17 | 0.529 | 0.501 | **+0.029** | +0.0362 | 6 / 0.500 |

- Skill is **weakly positive and sign-stable** across all three thresholds (not threshold-fragile), rising
  slightly as flat windows are removed — consistent with "flat windows are direction-noise" (their bucket
  resolves at exactly 0.500). But +0.02–0.03 on n=17–23 is **inside the noise band** (SE ~0.11).
- pseudo-EV(candle) is mildly positive but is a **ranking proxy only**; the real fill (yes-ask + spread +
  fees) would be worse, and could flip it negative. Not evidence of EV-at-fill.

## Breakdown at 0.05% flat (per asset × mode × BOS-tf)
Each cell n=1–4 → **individually pure noise; do not interpret single cells.** Shown for completeness only.

| asset | mode | bos | n | acc | base | skill | pEV(candle) |
|---|---|---|---|---|---|---|---|
| BTC | REAL | 15m | 1 | 0.00 | 0.50 | −0.50 | −0.5800 |
| BTC | REAL | 3m | 1 | 0.00 | 0.50 | −0.50 | −0.4750 |
| ETH | CONSIDERABLE | 15m | 1 | 0.00 | 0.51 | −0.51 | −0.3350 |
| ETH | CONSIDERABLE | 3m | 1 | 0.00 | 0.51 | −0.51 | −0.3350 |
| ETH | REAL | 15m | 1 | 1.00 | 0.51 | +0.49 | +0.4550 |
| ETH | REAL | 3m | 1 | 1.00 | 0.51 | +0.49 | +0.4550 |
| SOL | CONSIDERABLE | 15m | 2 | 1.00 | 0.50 | +0.50 | +0.4650 |
| SOL | CONSIDERABLE | 3m | 3 | 0.33 | 0.50 | −0.16 | −0.2100 |
| SOL | REAL | 15m | 3 | 0.00 | 0.50 | −0.50 | −0.4167 |
| SOL | REAL | 3m | 4 | 1.00 | 0.50 | +0.50 | +0.4512 |
| XRP | REAL | 15m | 2 | 1.00 | 0.50 | +0.50 | +0.5200 |
| XRP | REAL | 3m | 1 | 0.00 | 0.50 | −0.50 | −0.4850 |

## Assumptions that could FLATTER this result (read before trusting any positive number)
1. **Proxy mismatch (biggest):** the SFP fires on **Bitunix** bars; the window resolves on **BRTI** (a
   non-Bitunix constituent index). Signal and resolution are measured on different price series. T5 (basis)
   will quantify how often they disagree as a function of `|move|`.
2. **pseudo-EV uses candle MID, not the yes-ask** you'd pay → understates cost, flatters EV. Labeled throughout.
3. **Fillability:** assumes you transact at the observed mid in the window's first minute; open-of-window
   liquidity may be thin/worse; no slippage or partial-fill modeling.
4. **Single-window proxy:** the SFP thesis is a multi-hour move (to 2R TP); collapsing it to one 15-min
   window is a crude horizon that may or may not favor the signal. A different alignment (containing vs next
   window) could change the result.
5. **Multiple comparisons:** 23 signals sliced into 12 cells guarantees some cells look strong by chance.
6. **Small n:** +0.02–0.03 skill is not statistically distinguishable from 0 at this sample size.
7. **Arbitrary flat threshold:** 0.05% is a starting point, not empirically calibrated; T5's base-rate move
   distribution should calibrate it.
8. **Long-only:** only UP-side windows are addressed; no claim about DOWN moves (short detector not built).
9. **No transaction-cost realism beyond the fee** (no maker/taker, no queue, no adverse selection).

## What this does / does not say
- **Does:** the lifted SFP signal is **not grossly anti-predictive** on Kalshi 15-min up/down (it clears the
  prior inquiry's failure screen), and shows a hair of positive directional skill above a ~0.50 base rate,
  sign-stable across flat thresholds. It **ranks** as worth carrying into forward observation.
- **Does NOT:** establish edge, EV-at-fill, or readiness. n is far too small; pseudo-EV is a proxy; the
  proxy-mismatch (Bitunix↔BRTI) is unquantified until T5. No Phase-2 conclusion is drawn here.
- **Next:** T2 forward corpus accumulates canonical EV-at-fill on live both-sided quotes; T5 basis quantifies
  the Bitunix↔BRTI divergence that this retro assumes away.
