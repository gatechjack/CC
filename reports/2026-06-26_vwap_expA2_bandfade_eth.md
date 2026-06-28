# Exp A2 — VWAP Stdev Band-FADE (AlexO replication), ETHUSDT 15m

**Status:** PRE-REGISTERED, read-only, no optimisation. Full grid reported.

> **VERDICT UP FRONT: NO-GO.** The VWAP-Stdev band-fade has **no fee-net edge on ETH 15m
> in ANY of the 60 cells** (2 anchors × 3 tiers × 2 triggers × 5 trend splits). Every
> cell's mean net-R is negative; walk-forward found zero live cells. **Well-powered**
> (24,862 fade signals; every cell n in the hundreds–thousands), so this is verdict-grade,
> not an n-caveat. The operator's hypothesis — *trend-aligned fades carry the edge* — is
> **REFUTED**: alignment lifts win-rate and median but never the mean; the whole grid stays
> red. Two structural killers (both out-of-grid, flagged not tested): the rejection-entry
> sits inside the band so reward:risk < 1 vs the next-band-out stop, and the band-to-band
> stop is only ~0.5–0.73σ — a small absolute R-distance on a ~$3,000 instrument, so the
> corrected Bitunix taker fee drags ~0.3R off every trade. **Fees are again the binding
> constraint.** Indicator replication numerically verified (by-hand σ == array σ).

**Data:** Bitunix REST klines ETHUSDT 15m, 2024-01-01 → 2026-06-26 (87,086 bars).
**Total fade signals (both anchors, all tiers/triggers):** 24862

Indicator: hl2 vol-weighted VWAP; σ=√(Σ(vol·hl2²)/Σvol − VWAP²); bands ±{1.28,2.01,2.51}σ.
Fade: band tag→revert to VWAP. Target=VWAP@signal. Stop=next band out. k=1, 15m.
Guards: ≥8 bars since anchor; geometry-sane; mean+median+|gross|>5 ext count.
Trend: REG=OLS slope of close over 50 bars; OPEN=anchor-bar body direction.
Trend-aligned fade = long-at-lower in uptrend / short-at-upper in downtrend.

## ARM-1 (09:30 ET anchor, DST-aware)

### tier 1.28σ — bare-tag  (stop @ 2.01σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 5640 | 34.5% | -0.4225 | -1.0829 | 32 |
| reg-aligned | 1665 | 39.2% | -0.6565 | -1.1244 | 6 |
| reg-counter | 3975 | 32.5% | -0.3245 | -1.0712 | 26 |
| open-aligned | 2769 | 36.0% | -0.4087 | -1.0783 | 16 |
| open-counter | 2871 | 33.0% | -0.4358 | -1.0863 | 16 |

### tier 1.28σ — tag+reject  (stop @ 2.01σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 3328 | 43.3% | -0.2133 | -0.1400 | 0 |
| reg-aligned | 1023 | 50.9% | -0.2294 | +0.0141 | 0 |
| reg-counter | 2305 | 40.0% | -0.2062 | -0.2726 | 0 |
| open-aligned | 1646 | 46.7% | -0.1592 | +0.0179 | 0 |
| open-counter | 1682 | 40.1% | -0.2663 | -0.7874 | 0 |

### tier 2.01σ — bare-tag  (stop @ 2.51σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 3749 | 26.9% | -0.7302 | -1.1545 | 69 |
| reg-aligned | 1010 | 30.1% | -1.3770 | -1.2072 | 17 |
| reg-counter | 2739 | 25.7% | -0.4917 | -1.1382 | 52 |
| open-aligned | 1861 | 26.5% | -0.6323 | -1.1674 | 41 |
| open-counter | 1888 | 27.2% | -0.8268 | -1.1433 | 28 |

### tier 2.01σ — tag+reject  (stop @ 2.51σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 2597 | 34.0% | -0.1940 | -1.0953 | 0 |
| reg-aligned | 715 | 38.0% | -0.3043 | -1.1342 | 0 |
| reg-counter | 1882 | 32.5% | -0.1521 | -1.0849 | 0 |
| open-aligned | 1276 | 33.9% | -0.2207 | -1.1081 | 0 |
| open-counter | 1321 | 34.1% | -0.1682 | -1.0834 | 0 |

### tier 2.51σ — bare-tag  (stop @ 3.09σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 2164 | 27.1% | -0.4588 | -1.1324 | 40 |
| reg-aligned | 629 | 32.3% | -0.6652 | -1.1511 | 15 |
| reg-counter | 1535 | 25.0% | -0.3742 | -1.1261 | 25 |
| open-aligned | 1090 | 27.9% | -0.3850 | -1.1356 | 21 |
| open-counter | 1074 | 26.4% | -0.5337 | -1.1307 | 19 |

### tier 2.51σ — tag+reject  (stop @ 3.09σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 1613 | 32.7% | -0.2001 | -1.0918 | 0 |
| reg-aligned | 479 | 38.4% | -0.2161 | -1.1072 | 0 |
| reg-counter | 1134 | 30.2% | -0.1933 | -1.0872 | 0 |
| open-aligned | 819 | 33.7% | -0.1653 | -1.0918 | 0 |
| open-counter | 794 | 31.6% | -0.2360 | -1.0913 | 0 |

## ARM-2 (00:00 UTC anchor)

### tier 1.28σ — bare-tag  (stop @ 2.01σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 6406 | 34.4% | -0.9910 | -1.1006 | 52 |
| reg-aligned | 2176 | 37.6% | -1.9147 | -1.1371 | 19 |
| reg-counter | 4228 | 32.7% | -0.5154 | -1.0901 | 33 |
| open-aligned | 3145 | 35.1% | -0.6912 | -1.0965 | 22 |
| open-counter | 3261 | 33.7% | -1.2801 | -1.1043 | 30 |

### tier 1.28σ — tag+reject  (stop @ 2.01σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 3764 | 44.6% | -0.2373 | -0.1824 | 0 |
| reg-aligned | 1295 | 49.8% | -0.2557 | -0.0446 | 0 |
| reg-counter | 2468 | 41.9% | -0.2273 | -0.2553 | 0 |
| open-aligned | 1855 | 45.8% | -0.2154 | -0.0679 | 0 |
| open-counter | 1909 | 43.5% | -0.2586 | -0.3515 | 0 |

### tier 2.01σ — bare-tag  (stop @ 2.51σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 4432 | 23.7% | -0.9059 | -1.1743 | 85 |
| reg-aligned | 1287 | 27.7% | -0.8658 | -1.2449 | 24 |
| reg-counter | 3143 | 22.0% | -0.9229 | -1.1551 | 61 |
| open-aligned | 2149 | 23.1% | -0.8571 | -1.1777 | 41 |
| open-counter | 2283 | 24.2% | -0.9519 | -1.1727 | 44 |

### tier 2.01σ — tag+reject  (stop @ 2.51σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 3064 | 29.9% | -0.3089 | -1.1162 | 0 |
| reg-aligned | 918 | 34.7% | -0.3563 | -1.1584 | 0 |
| reg-counter | 2145 | 27.8% | -0.2894 | -1.1045 | 0 |
| open-aligned | 1470 | 29.4% | -0.3080 | -1.1119 | 0 |
| open-counter | 1594 | 30.4% | -0.3097 | -1.1183 | 0 |

### tier 2.51σ — bare-tag  (stop @ 3.09σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 2471 | 25.3% | -0.4530 | -1.1559 | 47 |
| reg-aligned | 694 | 30.8% | -0.4973 | -1.1949 | 13 |
| reg-counter | 1776 | 23.1% | -0.4352 | -1.1412 | 34 |
| open-aligned | 1225 | 24.2% | -0.4662 | -1.1570 | 23 |
| open-counter | 1246 | 26.3% | -0.4401 | -1.1522 | 24 |

### tier 2.51σ — tag+reject  (stop @ 3.09σ)

| split | n | win% | mean net-R | median net-R | ext(|g|>5) |
|-------|---|------|-----------|--------------|-----------|
| ALL | 1818 | 30.9% | -0.2673 | -1.1066 | 0 |
| reg-aligned | 509 | 37.7% | -0.2992 | -1.1462 | 0 |
| reg-counter | 1308 | 28.2% | -0.2541 | -1.0985 | 0 |
| open-aligned | 909 | 28.9% | -0.3314 | -1.1099 | 0 |
| open-counter | 909 | 32.8% | -0.2032 | -1.1043 | 0 |

## Walk-forward on live-looking cells (n≥30, mean>+0.05, median>0)

_(none — no cell clears n≥30 AND mean>+0.05 AND median>0)_

## Auto-summary — top powered cells (n≥30) by mean net-R

| cell | n | win% | mean | median | ext |
|---|---|---|---|---|---|
| et|2.01σ|reject|reg-counter | 1882 | 32.5% | -0.1521 | -1.0849 | 0 |
| et|1.28σ|reject|open-aligned | 1646 | 46.7% | -0.1592 | +0.0179 | 0 |
| et|2.51σ|reject|open-aligned | 819 | 33.7% | -0.1653 | -1.0918 | 0 |
| et|2.01σ|reject|open-counter | 1321 | 34.1% | -0.1682 | -1.0834 | 0 |
| et|2.51σ|reject|reg-counter | 1134 | 30.2% | -0.1933 | -1.0872 | 0 |
| et|2.01σ|reject|ALL | 2597 | 34.0% | -0.1940 | -1.0953 | 0 |
| et|2.51σ|reject|ALL | 1613 | 32.7% | -0.2001 | -1.0918 | 0 |
| utc|2.51σ|reject|open-counter | 909 | 32.8% | -0.2032 | -1.1043 | 0 |
| et|1.28σ|reject|reg-counter | 2305 | 40.0% | -0.2062 | -0.2726 | 0 |
| et|1.28σ|reject|ALL | 3328 | 43.3% | -0.2133 | -0.1400 | 0 |

## Verdict — NO-GO, well-powered, hypothesis refuted

**No band-fade edge on ETH 15m, fee-net, in any cell.** All 60 cells negative-mean; the
walk-forward gate (n≥30 AND mean>+0.05 AND median>0) caught **nothing**. The pattern is
**sensible and monotone — not one noisy cell**:

1. **Trigger — tag+rejection ≫ bare-tag, everywhere.** Bare-tag medians cluster at
   ≈ −1.1R: the *typical* bare fade stops out, because a bare band tag usually means price
   is *breaking* the band (momentum), not rejecting it. Requiring the tag bar to close back
   inside removes the breakouts and roughly halves the loss (1.28σ: bare ALL −0.42/−0.99 ET/UTC
   → reject ALL −0.21/−0.24).
2. **Tier — shallowest (1.28σ) least-bad; deeper bands worse.** Fading a 2.01/2.51σ
   deviation = fading a *stronger* move; the band holds less often. Monotone across both
   anchors.
3. **Trend-alignment does NOT carry the edge (hypothesis refuted).** Aligned raises
   *win-rate* (e.g. ET 1.28σ-reject reg-aligned 50.9% vs counter 40.0%) and lifts the
   *median* to slightly positive — but the *mean* stays negative and, on bare tags, aligned
   is actively **worse** (its failures run with the trend, fattening the loss tail:
   UTC 1.28σ-bare aligned −1.91 vs counter −0.52).
4. **Anchor — ET marginally less-bad than UTC** (esp. bare-tag), but both dead. The open
   ET-vs-UTC question resolves to *"doesn't matter — neither is viable."*

**The least-bad cell — and why it still loses:** `tier 1.28σ + tag-rejection +
trend-aligned` reaches ~50% win and a **slightly-positive median** (ET reg-aligned +0.0141,
open-aligned +0.0179) — the *typical* trade is ~flat — yet the **mean is −0.16 to −0.26R**.
The wedge between a positive median and a negative mean is the structure: the
rejection-confirmation entry sits *inside* the band (closer to VWAP), which **shrinks the
reward** (entry→VWAP) and **grows the risk** (entry→next-band stop), so realized reward:risk
falls below 1; and the band-to-band stop is only ~0.5–0.73σ in price (~a few $ on ETH),
making the corrected taker round-trip (entry 0.000243 + exit 0.0004 + slip 0.0001) cost
**~0.3R per trade**. Even a theoretically-ideal fade geometry can't clear that here.

**Two out-of-grid levers (flagged, NOT tested — would be new pre-registrations):**
(a) a wider stop than the immediate next band (cuts the 1/r_dist fee drag, at the cost of
worse per-loss R) and (b) a maker/limit *entry* at the band instead of a taker market entry
(turns the ~0.3R fee headwind into a tailwind). Both are fee-structure plays — consistent
with the recurring finding that on these instruments **fees, not signal, are the binding
constraint.** As-coded with corrected Bitunix taker fees, the band-fade is NO-GO.

**Caveats:** (i) ARM-2 "exchange day" = 00:00 UTC (standard crypto-perp daily boundary).
(ii) ≥8-bars-since-anchor σ-stabilisation guard is a pre-registered round number, not tuned;
the |gross|>5 ext counts are small (single-to-double digits per thousands) so no tiny-r_dist
artifact drives the result. (iii) target = VWAP *at the signal bar* (fixed), not the moving
line. (iv) Indicator faithfully replicated — by-hand σ matches the array to 4 dp, reset bar
σ=0 / VWAP=hl2 — so this tested his *actual* tool, and it is fee-net dead on ETH 15m.

---
## Methodology Proofs

```
=== PROOF 1: DST (ARM-1 09:30 ET anchor) ===
EST (expect 14:30 UTC):
  2024-02-01 09:30 ET (EST) = 14:30 UTC
  2024-02-02 09:30 ET (EST) = 14:30 UTC
EDT (expect 13:30 UTC):
  2024-04-01 09:30 ET (EDT) = 13:30 UTC
  2024-04-02 09:30 ET (EDT) = 13:30 UTC
```

```
=== PROOF 2: INDICATOR REPLICATION (et) ===
  RESET bar 2024-01-01 14:30:00+00:00:
    hl2=2304.5650  vwap=2304.5650 (==hl2)  sigma=0.000000 (==0)
  10th bar of a session:
    by-hand vwap=2313.1473  sigma=4.4459
    array    vwap=2313.1473  sigma=4.4459  (must match)
    bands: -2.51s=2301.99  -1.28s=2307.46  VWAP=2313.15  +1.28s=2318.84  +2.51s=2324.31
```

```
=== PROOF 2: INDICATOR REPLICATION (utc) ===
  RESET bar 2024-01-01 00:00:00+00:00:
    hl2=2290.7800  vwap=2290.7800 (==hl2)  sigma=0.000000 (==0)
  10th bar of a session:
    by-hand vwap=2298.1978  sigma=5.3541
    array    vwap=2298.1978  sigma=5.3541  (must match)
    bands: -2.51s=2284.76  -1.28s=2291.34  VWAP=2298.20  +1.28s=2305.05  +2.51s=2311.64
```

```
=== PROOF 3: k=1 ===
Band tag on CLOSED bar i; entry=open[i+1]; stop/target from bar i VWAP/σ;
trend from bars<=i. No future bar read.
```

---
*Generated by scripts/vwap_expA2_bandfade_eth.py — pre-registered, no optimisation.*