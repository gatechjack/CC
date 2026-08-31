# Whale-proportional sizing (mode 3) -- PER-CATEGORY findings (2026-08-31, READ-ONLY)

Runners: `cc\pm_whale_proportional_ro.ps1` (pooled -- superseded) + `cc\pm_whale_proportional_bycat_ro.ps1`
(per-category -- authoritative). Read-only over `pm_closed_position`; nothing written or built. Size metric =
`cost_basis` ($ at risk); "large" = above the whale's OWN typical bet WITHIN THE CATEGORY; verdict metric = RETURN
PER DOLLAR (sizing scales money, not frequency). Split is per (whale, category), aggregated across whales per category.

## ★ SCOPE CORRECTION (Jack was right)
The first pass had NO category filter and grouped by wallet, so it **POOLED all 18 categories** (footprint below;
~22k of the 120,542 is uncategorized "unknown"). Sizing is configured per (account, category) sub-division, so a
pooled number is the wrong grain -- it hid the per-category variation and averaged categories that disagree. The
global "do not build" is **withdrawn** and replaced with per-category verdicts, each scope-labelled. Bottom line
still lands in the same place for the live sub-divisions, but for a defensible reason now.

## Per-category footprint (what the pooled run mixed)
```
category   n_pos  whales  whales>=20     category   n_pos  whales  whales>=20
unknown   22277    49       36           ufc        3410    34       21
nba       16171    40       27           nfl        3104    33       14
mlb       14967    48       30           cbb        2647    16        8
soccer    13773    47       35           epl        1855    41       16
atp       10989    44       27           wnba       1404    32       14
fifwc      9802    47       32           ucl        1352    37       12
cs2        7042    29       21           tennis      662    22        8
wta        5348    39       21           golf        398    12        3   <- INSUFFICIENT
nhl        5090    28       12           fed         251    13        2   <- INSUFFICIENT
```

## Per-category verdict -- RETURN PER DOLLAR, per-whale median(large - typical); win-rate + price gap alongside
```
category  rpd Δ(L-T)   p     winrate Δ  price Δ  whales  READ
unknown    -0.049   0.046    +0.070    +0.086    36    large WORSE (significant)
atp        -0.168   0.034    +0.008    +0.100    27    large WORSE (significant)
fifwc      -0.156   0.034    +0.041    +0.105    32    large WORSE (significant)
cs2        -0.188   0.050    +0.069    +0.083    21    large WORSE (significant)
ucl        -0.235   0.004    +0.000    +0.108    12    large WORSE (significant, moderate n)
mlb        -0.077   0.068    +0.023    +0.040    30    large worse (weak lean)   <- LIVE NOW
nba        -0.136   0.178    +0.012    +0.036    27    large worse (weak)
soccer     -0.037   0.237    +0.047    +0.068    35    large worse (weak)
wta        -0.042   0.127    +0.013    +0.083    21    large worse (weak)
nhl        -0.053   0.083    +0.041    +0.043    12    large worse (weak)
wnba       -0.162   0.285    +0.024    +0.069    14    large worse (weak)
cbb        -0.251   0.157    +0.008    +0.027     8    large worse (thin)
tennis     -0.681   0.157    +0.026    +0.058     8    large worse (thin)
ufc        -0.003   0.827    +0.100    +0.127    21    NO SIGNAL (flat)          <- NEXT GO-LIVE
nfl        +0.038   1.000    +0.062    +0.048    14    NO SIGNAL (flat)
epl        -0.031   1.000    +0.047    +0.114    16    NO SIGNAL (flat)
golf         --      --       --        --        3    INSUFFICIENT DATA
fed          --      --       --        --        2    INSUFFICIENT DATA
```

### What replicates across ALL categories
- **No category shows large bets returning significantly MORE per dollar.** Mode 3 is never *justified* anywhere.
- **The win-rate edge is universal AND a chalk artefact everywhere:** in every category the large group wins more
  often (winrate Δ > 0) AND enters at a higher price (price Δ > 0). Bigger bets are systematically on favorites.

### Three tiers of verdict
- **Contraindicated (significant, p<0.05):** unknown, atp, fifwc, cs2, ucl. Large bets clearly return less per dollar.
- **Not justified (weak lean, p 0.07-0.29):** mlb, nba, soccer, wta, nhl, wnba, cbb, tennis. No positive edge; the
  majority of whales do worse with big bets, but not at significance.
- **No signal (p>0.8, delta ~0):** ufc, nfl, epl. The data cannot distinguish large from typical per dollar --
  neither justifies mode 3 nor calls it harmful.
- **Insufficient:** golf (3 whales), fed (2). A confident verdict on this n would be false precision.

## The two live-relevant sub-divisions
- **MLB (live now): mode 3 NOT justified.** Weak lean to large-worse (-0.077 per-whale, p=0.068), no positive
  per-dollar edge, and the win-rate edge is chalk (+0.040 price gap). Keep flat `contracts` sizing.
- **UFC (next go-live): mode 3 UNSUPPORTED -- no signal.** The per-dollar delta is essentially zero (-0.003,
  p=0.827) across 21 whales / 3410 positions. Your intuition that UFC is DIFFERENT is confirmed -- it is the one
  live-relevant category without a clear per-dollar penalty -- but different as *no signal*, not as *positive
  signal*. And the mechanism is the opposite of the guess: UFC's price confound is the LARGEST (price Δ +0.127, the
  biggest of any category), and its win-rate edge is also the largest (+0.100, 86% of whales). UFC whales bet EVEN
  MORE on favorites when betting big; the wider price dispersion (p10=0.29/p90=0.90 vs MLB 0.28/0.72) is real, but it
  makes the confound bigger, not absent. Net: enough data to say "no signal," not enough to endorse sizing up. Default
  to flat until re-asked with more data.

## Distribution shape / new-whale threshold / stationarity (unchanged in direction, now per category)
Sizes are long-tailed within category; typical size DRIFTS UP over time in most categories (median late/early 1.0-1.4x,
higher in cbb 2.36x / epl 1.58x / ufc 1.30x), so any relative-sizing must use a ROLLING local typical, never a static
lifetime median (the static median does not stabilize within 50 positions -- it is chasing the drift). New-whale
fallback: treat a whale as un-sizable on the relative axis until it has a stable local window (~15 recent in-category
positions); below that, flat.

## ★ THE FORWARD RESULT (price-bucket) -- the more useful finding, but NOT yet trustworthy
The same confound seen from the other side is striking and CONSISTENT in every category: **return per dollar decreases
monotonically with entry price.** Longshots pay hugely per dollar, favorites pay ~nothing. E.g. MLB: [0.0,0.2) rpd
**+1.65** -> [0.8,1.0) rpd **+0.05**; UFC [0.0,0.2) **+2.58** -> [0.8,1.0) **+0.08**; every category the same shape.
The edge is in PRICE (longshots), not bet size -- which is the real lesson of this whole investigation.

**BUT this is the MOST loss-omission-contaminated number in the study, and in the direction that inflates it.** A
losing longshot resolves to $0 = a held-to-worthless loss, which is EXACTLY what `/closed-positions` under-reports
(the F-1 bias). So the low-price buckets are missing their losers -> the +1.6/+2.6 longshot rpd is an **UPPER BOUND**,
possibly a large one. (Contrast the bet-size finding, where the bias direction is ambiguous and the significant-negative
categories are negative before any correction.) **The price-bucket angle is a hypothesis, not a verdict**, and Stage
5's `loss_grounding` is the exact tool to re-ground it -- run per (whale, category, price-bucket) before it informs any
selection or sizing. That makes Stage 5 the prerequisite for the one idea in here worth pursuing.

## Status
Mode 3 is **NOT globally resolved.** It is: contraindicated (significant) in unknown/atp/fifwc/cs2/ucl; not-justified
(weak) in mlb/nba/soccer/wta/nhl/wnba/cbb/tennis; no-signal in ufc/nfl/epl; insufficient in golf/fed. **No category
supports building it**, so no live sub-division should adopt it now -- but the question is re-asked per category before
adoption, and UFC specifically should be re-run once it has more resolved history. The price-bucket/longshot angle is
the promising follow-up, gated on Stage-5 loss-grounding to remove the survivorship inflation.
