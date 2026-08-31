# Whale-proportional sizing (mode 3) -- DECIDING-QUESTION findings (2026-08-31, READ-ONLY)

Runner: `cc\pm_whale_proportional_ro.ps1` (read-only over `pm_closed_position`, mode=ro; nothing written/built).
Data: **120,542 scoreable positions across 61 whales** (pnl_suspect=0, won known, cost_basis>0); 60 whales have
n>=20 and enter the split. Per-whale n_resolved median = 707 (p25=215, p90=8000) -- this is a data-rich question,
not a thin one. Size metric = **cost_basis** (dollars at risk); "large" = above the whale's OWN typical, per-whale.

## ★ VERDICT: DO NOT BUILD MODE 3.
**Larger-than-own-typical bets return LESS per dollar, not more** -- and the metric that decides sizing is return
per dollar, because sizing scales money, not frequency. The result is consistent per-whale and pooled, and it gets
STRONGER under the stationarity-robust split. The one place big bets look better -- win rate -- is a price artefact.
Whale-proportional sizing would put MORE money on the LOWER-return-per-dollar bets. The flat `contracts` mode is
better aligned than proportional would be. This saves the build.

## The two questions, split (they diverge -- and only one decides the build)

**RETURN PER DOLLAR (the verdict metric):** large bets return LESS.
- Pooled: large group **+25.5%** per dollar vs typical **+36.1%** (static split); **+25.2%** vs **+36.7%** (rolling).
- Per-whale: median(large - typical) = **-0.052** static / **-0.101** rolling; only 38% (static) / 32% (rolling) of
  whales have large>typical (rolling sign-test p=0.005). Both the pooled and the per-whale-aggregate agree, so this
  is not a few-big-whales artefact.
- Note big bets are still PROFITABLE (+25% per dollar) -- just *less* profitable per dollar than the whale's typical
  bet. Sizing proportionally would over-allocate to them relative to flat sizing: strictly worse on the metric.

**WIN RATE:** large bets win MORE often -- +0.056 (static) / +0.042 (rolling); 77-80% of whales (p=0.000). This is
the divergence you flagged: higher win frequency, lower per-dollar return. It does NOT carry the verdict, and here is
why it happens ->

## The confound is real and explains the win-rate edge: BIG BETS ARE CHALK
Average entry price, large minus typical = **+0.068** per-whale median (pooled: large 0.593 vs typical 0.512). Whales
bet bigger on shorter-priced favorites. A higher win rate at 0.59 than at 0.51 is largely the price, not skill -- and
chalk wins often while returning little per dollar, which is exactly the pooled picture (large: 78% win / +25% per $;
typical: 72% win / +36% per $). So the win-rate "edge" on big bets is a price artefact, and the return-per-dollar
metric -- which already nets the price in -- is the honest read. It says: don't.

## Stationarity (run FIRST -- and it sharpens the verdict, doesn't rescue it)
Whale bet sizes DRIFT UP over time (bankroll growth): median late/early size = **1.45x**; **67% of whales drift
>1.5x, 20% >3x**. That is high enough that a STATIC per-whale median mislabels early bets "large" and late bets
"typical." So the split was also run on a ROLLING local median (each bet vs the median of its ~15 predecessors) --
the trustworthy version. It makes the return-per-dollar finding MORE negative (-0.101 vs -0.052) and more significant
(p=0.005). Stationarity does not save mode 3; it deepens the "do not build."

## Distribution shape
Bet sizes are LONG-TAILED: per-whale IQR/median = 2.06, p90/median = 4.92 (big bets sit ~5x above typical). So there
IS real spread to size on -- "relative to own typical" is a well-defined axis. The spread simply does not translate
into better per-dollar returns; the axis exists, the edge does not.

## New-whale fallback threshold
The STATIC per-whale size-median is slow to stabilize: first-N vs full-history median rel-error is 77% at N=10, 56%
at N=20, and still **45% at N=50** -- because of the upward drift above (the full median includes later, larger bets
a small early sample hasn't seen). So there is no clean small-N threshold for a *static* median; a *rolling/local*
median (~15 recent positions) is the only defensible normalization. Moot for the build, but recorded: any future
relative-sizing scheme must use a rolling local typical, never a static lifetime median.

## Loss-omission contamination -- the worst-case bound is not even needed
The concern was that big LOSSES dropped by /closed-positions (the F-1 bias) would inflate the large group's return.
But the large group's return-per-dollar edge is already **negative** -- there is no positive edge to erode. Dropped
held-to-worthless losses would only push the large-group return LOWER, deepening the negative finding. So the analytic
worst case is moot and no per-whale `loss_grounding` pass is required: the bias runs in the direction that
strengthens "do not build," never against it.

## Absolute vs relative (your original framing)
The test above is relative-to-own-history (the right framing -- a whale who always bets $100 tells you nothing by
betting $100). Absolute size would fare no better and likely worse: it conflates a small whale's "big" bet with a
large whale's "typical" one, and the relative signal already fails. Absolute is not worth testing separately.

## Bottom line
Mode 3 would scale money toward bets that return **~11 cents less per dollar** than the whale's typical bet, on the
strength of a win-rate signal that is really a chalk-price artefact, using a per-whale typical that is unstable under
the observed size drift. Do not build it. Keep flat `contracts` sizing; it is better aligned than proportional would
be. If a future sizing idea is revisited, it should look at *per-dollar edge by price bucket*, not bet size.
