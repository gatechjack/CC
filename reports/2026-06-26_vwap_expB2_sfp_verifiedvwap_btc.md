# Exp B2 — SFP × VWAP-proximity, AlexO's VERIFIED VWAP (hl2 / vol-weighted σ). BTCUSDT 15m

**Status:** PRE-REGISTERED re-analysis, read-only. Changes nothing live.

**Data:** Bitunix REST BTCUSDT 15m, 2024-01-01 → 2026-06-26 (87,088 bars).
**SFP signals resolved (REAL+CONSIDERABLE):** 97

VWAP/σ reused VERBATIM from the A2-verified code (hl2 vol-weighted; σ=√(Σ(vol·hl2²)/Σvol − VWAP²)). Distance at the BOS bar (closed, k=1). Resolution + fees replicate Exp B exactly (BASE must reproduce original B).

## ARM-1 (09:30 ET anchor, DST-aware)

**BASE:** n= 97  win= 40.2%  mean=+0.1279  med=-1.0434  — original B: n=97, win 40.2%, +0.1279R. Dropped no_vwap=0; σ=0 (kept for raw, excluded from σ views)=2.

### NEAR/FAR — raw % (median |dist| = 0.387%)

| bucket | n | win% | mean net-R | median |
|---|---|---|---|---|
| NEAR | 48 | 41.7% | +0.1703 | -1.0524 |
| FAR | 49 | 38.8% | +0.0864 | -1.0382 |
| **LIFT NEAR−FAR** | | | **+0.0840R** | _(orig B ≈ +0.2112R)_ |

### NEAR/FAR — σ-normalized (median |σ-dist| = 0.700σ, n=95)

| bucket | n | win% | mean net-R | median |
|---|---|---|---|---|
| NEAR | 47 | 42.6% | +0.1930 | -1.0389 |
| FAR | 48 | 35.4% | -0.0108 | -1.0501 |
| **LIFT NEAR−FAR** | | | **+0.2039R** | |

### σ-distance buckets (|entry − VWAP| in σ)

| bucket | n | win% | mean net-R | median |
|---|---|---|---|---|
| <0.5σ | 34 | 38.2% | +0.0599 | -1.0485 |
| 0.5–1σ | 30 | 36.7% | +0.0056 | -1.0624 |
| 1–2σ | 18 | 27.8% | -0.2262 | -1.0553 ⚠ |
| >2σ | 13 | 61.5% | +0.8014 | +1.9424 ⚠ |

### Signed — entry above vs below VWAP

| side | n | win% | mean net-R | median |
|---|---|---|---|---|
| ABOVE | 45 | 44.4% | +0.2693 | -1.0315 |
| BELOW | 50 | 34.0% | -0.0714 | -1.0624 |

## ARM-2 (00:00 UTC exchange-day anchor)

**BASE:** n= 97  win= 40.2%  mean=+0.1279  med=-1.0434  — original B: n=97, win 40.2%, +0.1279R. Dropped no_vwap=0; σ=0 (kept for raw, excluded from σ views)=0.

### NEAR/FAR — raw % (median |dist| = 0.246%)

| bucket | n | win% | mean net-R | median |
|---|---|---|---|---|
| NEAR | 48 | 35.4% | -0.0166 | -1.0596 |
| FAR | 49 | 44.9% | +0.2694 | -1.0317 |
| **LIFT NEAR−FAR** | | | **-0.2860R** | _(orig B ≈ +0.2112R)_ |

### NEAR/FAR — σ-normalized (median |σ-dist| = 0.532σ, n=97)

| bucket | n | win% | mean net-R | median |
|---|---|---|---|---|
| NEAR | 48 | 31.2% | -0.1308 | -1.0587 |
| FAR | 49 | 49.0% | +0.3813 | -1.0235 |
| **LIFT NEAR−FAR** | | | **-0.5121R** | |

### σ-distance buckets (|entry − VWAP| in σ)

| bucket | n | win% | mean net-R | median |
|---|---|---|---|---|
| <0.5σ | 43 | 27.9% | -0.2338 | -1.0598 |
| 0.5–1σ | 27 | 48.1% | +0.3597 | -1.0235 ⚠ |
| 1–2σ | 22 | 59.1% | +0.6829 | +1.8524 ⚠ |
| >2σ | 5 | 20.0% | -0.4550 | -1.0569 ⚠ |

### Signed — entry above vs below VWAP

| side | n | win% | mean net-R | median |
|---|---|---|---|---|
| ABOVE | 42 | 42.9% | +0.2155 | -1.0370 |
| BELOW | 55 | 38.2% | +0.0610 | -1.0472 |

---
## Verdict

**The proximity lift was LARGELY AN ARTIFACT of the wrong VWAP. It does NOT robustly
survive AlexO's verified (hl2 / vol-weighted-σ) indicator — and it INVERTS under his
actual (00:00-UTC) anchor.** Retract Exp B's "VWAP-proximity sharpens SFP → candidate
filter" conclusion.

**Internal check — passed.** BASE reproduces original B exactly under both anchors (n=97,
win 40.2%, mean +0.1279R). Trade outcomes are VWAP-independent, so only the NEAR/FAR/σ
classification changed — a clean apples-to-apples re-analysis.

**What happened to the +0.21R lift:**

| | original B (HLC3, 09:30-ET) | B2 ARM-1 (hl2, 09:30-ET) | B2 ARM-2 (hl2, 00:00-UTC) |
|---|---|---|---|
| NEAR−FAR, raw % | **+0.2112R** | +0.0840R | **−0.2860R** |
| NEAR−FAR, σ-norm | — | +0.2039R | **−0.5121R** |

- **Same anchor (ET), correct line:** the raw lift **collapses to +0.08R** — most of original
  B's lift was the HLC3-vs-hl2 difference. The σ-normalized split recovers ~+0.20R, **but the
  σ-bucket gradient is non-monotone** (<0.5σ +0.06, 0.5–1σ +0.01, 1–2σ −0.23, >2σ **+0.80**
  [thin n=13]) — the "lift" is a soft middle plus a thin *far* tail, not a clean
  near-good/far-bad gradient. Not the shape of a real proximity edge.
- **His actual anchor (UTC):** the lift **inverts** — FAR beats NEAR by −0.29R (raw) / −0.51R
  (σ). Under the indicator's real daily anchor, "near VWAP" is *worse*.
- **Anchor contradiction = artifact signature.** ET says NEAR better; UTC says FAR better. A
  finding that flips sign on an arbitrary anchor choice, on n=97, is noise sliced two ways. The
  medians are ≈ −1.04R in **every** bucket (most SFPs stop out regardless of VWAP position); the
  whole signal lives in the win-tail frequency, where n≈48/bucket has wide error bars.

**The one thread that DOES survive — a SIGN, not a proximity, effect:** entries **ABOVE VWAP
outperform BELOW**, consistently across both anchors and reasonably powered: ARM-1 above
+0.2693R (n45, 44.4% win) vs below −0.0714R (n50); ARM-2 above +0.2155R (n42) vs below +0.0610R
(n55). The SFP detector is **long-only**, so "above VWAP" = long *with* intraday trend, "below"
= counter-trend dip-long. This corroborates original B's underpowered Q4 hint — but it is a
momentum-confluence (directional) signal, **not** "closer to VWAP is better," and at n≈45 it is
suggestive, not deploy-grade.

**Bottom line for the live SFP division:** do **not** add a VWAP-distance (near/far) filter — it
doesn't survive the correct indicator and inverts on the real anchor. Base SFP edge unchanged
(VWAP-independent; 15m-only +0.1279R, still below the all-TF validated +0.267R). The only VWAP
idea worth a *future* pre-registered test is the weaker **above/below-VWAP sign**
(long-with-trend), tracked forward on live fires — not proximity.

---
*Generated by scripts/vwap_expB2_sfp_verifiedvwap_btc.py — pre-registered, no optimisation.*