# kalshi_weather model-confirmation filter — read-only test

**Date:** 2026-05-28
**Engine:** `scripts/weather_confirmation_filter.py` (reuses the leak-safe real-price
join from `scripts/weather_realprice_ev.py`; run capped).
**Question:** Among market favorites, does OUR model's *agreement* identify a subset
that wins MORE than the market's implied price (underpriced → edge)? And does
disagreement flag shaky favorites that win less? Distinct from raw favorite-buying
(zero-EV) and from standalone-model edge (already disproven, Brier 0.178 > 0.161).

## Bottom line — NULL (confirms avenue-4 via a distinct cut)

**Model-confirmation does not create a +EV favorite-filter.** No cell is robustly +EV;
the agree>disagree ordering is in-sample only and inverts on every holdout; and
disagreed favorites do not win less (at the tails they win slightly *more* OOS). Our
model's agreement is uninformative on real prices — consistent with it being a noisier
version of the same public signal the market already prices. **No trade, no deploy.**

## Two structural facts that frame the read

1. **Real Kalshi prices exist only spring-2026** (2026-03-20…05-25; 7,403 interior
   B-markets; 65 market-days). Kalshi retains ~2 months of settled history, so the
   mandated 2021-24-train / 2025-26-holdout split is **impossible on real prices**, and
   the deep-corpus proxy cannot substitute: there the "market" is derived from NBM (the
   same input our model uses), so model↔market agreement is mechanical, not informative.
   Best available OOS discipline used instead:
   - **Split A** chronological (≤2026-04-30 train / ≥2026-05-01 holdout)
   - **Split B** interleaved even/odd day-of-month (neutralizes spring-warming
     seasonality the prior report flagged).
2. **This corpus has ~no YES-favorites** (literal "market YES-implied ≥0.65" → n=8).
   Interior 1°F buckets are priced NO ("temp won't land in this exact 1° window"). So
   the meaningful test is over favorites of *whichever side the market backs* (~99% NO
   here). Reported combined; YES-only grid shown empty for honesty.

Definitions (favored-side POV): `gap_mid` = win-rate − implied mid; `gap_ask` =
win-rate − price actually paid; `ev/ct` = per-contract PnL net of spread + Kalshi fee;
`2*SE` day-clustered (n_eff = distinct market-days). AGREE = our model also ≥ band on the
favored side; DISAGREE = our model prob < market-implied − 0.10; MIDDLE = the rest.
Model = frozen WX-EMP-1 (the 0.178-Brier candidate the mandate referenced).

## FULL real-price grid (spring-2026, combined favorites)

```
 band      cell      n  days  winrate  implied  gap_mid  gap_ask    ev/ct    2*SE  flag
 0.65   ALL-fav   5829    65    0.816    0.812   +0.004   -0.016  -0.0305  0.0928
 0.65     AGREE   5430    65    0.824    0.815   +0.009   -0.011  -0.0257  0.0914
 0.65    MIDDLE     71    45    0.563    0.676   -0.113   -0.141  -0.1606  0.1472
 0.65  DISAGREE    954    64    0.856    0.861   -0.005   -0.019  -0.0318  0.0841
 0.75   ALL-fav   4075    65    0.875    0.860   +0.015   -0.003  -0.0158  0.0806
 0.75     AGREE   3345    65    0.883    0.863   +0.020   +0.001  -0.0109  0.0784
 0.75    MIDDLE    167    62    0.766    0.785   -0.019   -0.045  -0.0619  0.1076
 0.75  DISAGREE    840    64    0.887    0.883   +0.004   -0.010  -0.0213  0.0774
 0.85   ALL-fav   2318    65    0.926    0.906   +0.020   +0.005  -0.0054  0.0647
 0.85     AGREE   1406    65    0.933    0.910   +0.024   +0.007  -0.0027  0.0618
 0.85    MIDDLE    290    65    0.893    0.883   +0.010   -0.005  -0.0146  0.0762
 0.85  DISAGREE    622    64    0.924    0.909   +0.015   +0.003  -0.0074  0.0657
 0.90   ALL-fav   1289    64    0.947    0.930   +0.017   +0.004  -0.0059  0.0557
 0.90     AGREE    654    64    0.948    0.931   +0.017   +0.002  -0.0081  0.0552
 0.90    MIDDLE    264    63    0.939    0.927   +0.012   +0.000  -0.0096  0.0595
 0.90  DISAGREE    371    64    0.951    0.929   +0.022   +0.011  +0.0007  0.0539  +ev(weak)
```

## Train vs holdout (the decisive part)

| split | AGREE gap monotone (agree>middle>disagree)? | any ROBUST+EV cell? |
|---|---|---|
| FULL | 3 of 4 bands MONO+ | none |
| A-TRAIN (Mar–Apr) | 4 of 4 MONO+ | none (one +ev weak, band .85 AGREE +0.0018) |
| A-HOLDOUT (May)   | inverted at 0.65 / 0.85 / 0.90 | none (band .85 AGREE → −0.0114) |
| B-TRAIN (even)    | 4 of 4 MONO+ | none (weak) |
| B-HOLDOUT (odd)   | inverted at 0.75 / 0.85 / 0.90 | none |

## Why null (all three visible in the grid)

1. **No cell is robustly +EV.** Real ~1–2% favorite underpricing exists in `gap_mid`
   (the favorite-longshot bias), but spread + fee eat it: `gap_ask`/`ev/ct` go negative.
   AGREE-filtering doesn't rescue it. Every weakly-positive `ev/ct` (max +0.013/ct) is
   5–50× smaller than its own `2*SE`.
2. **Agree>disagree is in-sample only.** Crisp MONO+ in *both* train halves (4/4 bands),
   then inverts in *both* holdouts at the high bands. The one marginally-tradeable spot
   (band .85 AGREE train, +0.002–0.007/ct) flips to −0.011/ct in the matching holdout.
   Textbook overfit; holdout discipline caught it.
3. **DISAGREE favorites don't win less** — at the tails they win slightly more OOS
   (A-HOLDOUT 0.90: DISAGREE gap +0.034 vs AGREE +0.015). Model disagreement is
   uninformative-to-mildly-anti-informative. Only the tiny n=22–71 MIDDLE/band-0.65 cell
   underperforms, non-monotone with DISAGREE → noise.

## Relation to the prior /goal

Avenue 4 (real-price segment scan by station/kind/price/side) found no robust +EV. This
agreement-filter is a *distinct* segmentation it didn't test, and it also returns null.
Per the mandate, that is **confirmation**, not failure. The market is efficient against
our model's agreement just as it is against the model's point estimate.

## Reproduce

```
.\scripts\run_capped.ps1 <python> scripts\weather_confirmation_filter.py
```
(requires `tmp/kalshi_realprice_candles.jsonl` + `data/weather_emp_model_WX-EMP-1.json`
from the avenue-4 run.)
```
