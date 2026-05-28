# kalshi_weather per-station +EV scan — multiple-comparisons + holdout (read-only)

**Date:** 2026-05-28
**Engine:** `scripts/weather_station_ev_scan.py` (reuses the leak-safe real-price join
from `weather_confirmation_filter.collect`; run capped).
**Question:** Does any individual station have +EV that survives BOTH a holdout split
AND a Bonferroni multiple-comparisons correction AND has a physical mechanism? Re-runs
avenue-4's per-station cut with explicit MC discipline.

## Bottom line — NO station survives. Market efficient at the station level.

Candidates (+EV on both chrono train & holdout): **KATL, KBOS, KMIA**. Of those,
clearing Bonferroni-corrected significance: **NONE** (best z = 1.28, bar = 3.008).
Mechanism does not align (the strong-microclimate stations are flat-to-worst). Every
positive station EV is the top of a noise distribution. Confirms avenue 4. No trade.

## Method

- **Bet = fixed WX-EMP-1 rule, tau=0.05** (buy YES if model_prob − yes_ask ≥ tau, else
  buy NO if yes_bid − model_prob ≥ tau), pay the fillable price, net Kalshi fee. **No
  per-station tau/side optimization** — that would itself inflate the MC problem.
- **Real prices = spring-2026 only** (2026-03-20…05-25, 7,403 interior B-markets,
  65 market-days; 4,594 bets fired; overall EV/ct = −0.0214). True 5-yr split impossible.
- **Splits:** chronological train(<2026-05-01)/holdout(≥2026-05-01) PRIMARY; even/odd
  day-of-month secondary (seasonality-robust). Candidate ⇔ +EV on BOTH chrono halves.
- **SE = cluster-robust, clustering by market-day** (intra-day bets co-move).
- **Multiple comparisons:** family = 19 stations (all ≥30 bets). Bonferroni two-sided
  α=0.05 ⇒ per-test α=0.0026 ⇒ **critical |z| = 3.008** (vs raw 1.960). State family
  size honestly: adding kind/season splits would push it to ~38–76 and raise the bar
  further; 19 is the conservative (smallest) family, so it is the easiest bar to clear —
  and nothing clears even that.

## Per-station table (sorted by full EV/ct; all 19 incl. losers)

```
 stn   n  days  EVfull     z   raw  bonf  EVtrain  EVhold  EVeven   EVodd  +both  mechanism
KATL  214  60  +0.0320  1.28   -    -    +0.0185 +0.0531 +0.0383 +0.0270   Y    Piedmont UHI (Atlanta)
KBOS  193  62  +0.0263  0.88   -    -    +0.0045 +0.0629 +0.0594 -0.0020   Y    coastal sea-breeze (Boston)
KMIA  254  63  +0.0118  0.38   -    -    +0.0136 +0.0092 +0.0478 -0.0181   Y    subtropical sea-breeze (Miami)
KOKC  240  61  +0.0016  0.06   -    -    +0.0151 -0.0227 -0.0142 +0.0145   -    continental plains
KDEN  255  64  +0.0001  0.01   -    -    +0.0389 -0.0456 -0.0192 +0.0170   -    Front Range / elevation  [micro]
KPHX  205  58  -0.0005 -0.02   -    -    +0.0104 -0.0214 -0.0279 +0.0272   -    desert UHI (Phoenix)
KSEA  218  61  -0.0059 -0.19   -    -    +0.0105 -0.0297 +0.0314 -0.0393   -    marine layer (Seattle)   [micro]
KSAT  256  64  -0.0076 -0.36   -    -    -0.0105 -0.0038 -0.0317 +0.0181   -    -
KAUS  267  64  -0.0115 -0.50   -    -    -0.0217 +0.0041 -0.0016 -0.0209   -    -
KMDW  290  61  -0.0133 -0.66   -    -    -0.0082 -0.0220 -0.0190 -0.0078   -    lake-breeze (Chicago)
KSFO  241  60  -0.0312 -1.07   -    -    -0.0556 -0.0016 -0.0352 -0.0272   -    marine layer (SF)        [micro]
KHOU  229  62  -0.0324 -1.11   -    -    -0.0217 -0.0458 -0.1070 +0.0454   -    humid Gulf (Houston)
KDCA  268  62  -0.0360 -1.25   -    -    -0.0102 -0.0748 -0.0370 -0.0350   -    urban/Potomac (DC)
KPHL  279  62  -0.0403 -1.70   -    -    -0.0150 -0.0909 -0.0523 -0.0301   -    coastal-plain urban
KDFW  212  62  -0.0487 -2.01  YES   -    -0.0592 -0.0337 -0.0075 -0.0849   -    -
KMSY  219  63  -0.0497 -1.90   -    -    -0.0720 -0.0213 -0.0008 -0.0940   -    humid Gulf (New Orleans) [micro]
KNYC  297  64  -0.0583 -2.75  YES   -    -0.0587 -0.0577 -0.0881 -0.0303   -    UHI (Central Park)       [micro]
KLAX  246  63  -0.0593 -2.52  YES   -    -0.0759 -0.0324 -0.0431 -0.0743   -    marine layer (LA)        [micro]
KMSP  211  63  -0.0688 -2.78  YES   -    -0.0716 -0.0638 -0.0922 -0.0461   -    radiational cooling (MSP)
```

## Three gates, all agree

1. **Holdout: 19 → 3.** Only KATL/KBOS/KMIA are +EV on both chrono halves. KBOS and KMIA
   go negative on the odd-day split → already unstable on the secondary check.
2. **Multiple comparisons: 3 → 0.** z = 1.28 / 0.88 / 0.38 — none clears even raw 1.96,
   far below Bonferroni 3.008. KATL (best in the corpus) at z=1.28 is right where the max
   of 19 noise draws is expected. The only raw-significant stations are LOSERS (KDFW,
   KLAX, KNYC, KMSP, |z|=2.0–2.8) and even they miss Bonferroni.
3. **Mechanism: none.** The microclimate stations (`[micro]`: KSEA, KDEN, KSFO, KMSY,
   KNYC, KLAX) do NOT cluster positive — KMSY/KNYC/KLAX are among the WORST. If
   microclimate mispricing were real, the model would win there; instead it is most
   anti-predictive there (the market prices those microclimates; the model adds noise).
   The only positive with a story (KATL/UHI) is indistinguishable from zero.

## Relation to prior work

Confirms avenue 4 (real-price segment scan) and avenue 7 (confirmation filter): the
market is efficient against our model at the aggregate, segment, favorite, AND station
level. No re-open.

## Reproduce

```
.\scripts\run_capped.ps1 <python> scripts\weather_station_ev_scan.py
```
(needs `tmp/kalshi_realprice_candles.jsonl` + `data/weather_emp_model_WX-EMP-1.json`.)
