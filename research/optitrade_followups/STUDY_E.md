# STUDY E -- timeframe extension (frozen STUDY B + micro-aligned gate; counts only)

**PRE-REGISTRATION:** 1h is the anchor. 15m tests whether wide-stop fee math (~0.08 R/trade drag) + chop-removal (micro gate) rescues the low TF; 4h tests avgR-rises-with-TF and is expected n-thin (~50/coin). Gate = trade direction matches micro_regime direction at entry. GROSS primary; net06/net04 = 0.06%/0.04% per side. Own-bucket drift-null (side,macro60) on GROSS R, 200x, seeds pinned. NO per-TF parameter changes. Flag n<30.

## 15m

| coin | ung n | ung net06 | ung avgR | GATED n | gated gross | gated net06 | gated net04 | gated avgR | null pctl | flag |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| SOL | 809 | -95.8 | -0.048 | 361 | -41.0 | -64.0 | -56.3 | -0.114 | 22% |  |
| BTC | 860 | -115.7 | +0.014 | 386 | +6.0 | -42.2 | -26.1 | +0.016 | 44% |  |
| ETH | 843 | -22.0 | +0.078 | 405 | +27.0 | -10.6 | +2.0 | +0.067 | 39% |  |
| XRP | 820 | -24.1 | +0.060 | 340 | +4.0 | -24.6 | -15.1 | +0.012 | 23% |  |
| POOLED | 3332 | -257.5 | +0.026 | 1492 | -4.0 | -141.3 | -95.6 | -0.003 | 24% |  |

### 15m per-window net06 (pooled): ungated vs gated (w4 = most recent)

| stream | w0 | w1 | w2 | w3 | w4 | total |
|---|--:|--:|--:|--:|--:|--:|
| ungated | -115.6 | -27.5 | -60.1 | -32.2 | -22.2 | -257.5 |
| gated | -81.1 | +0.7 | -10.1 | -31.1 | -19.9 | -141.3 |

## 4h

| coin | ung n | ung net06 | ung avgR | GATED n | gated gross | gated net06 | gated net04 | gated avgR | null pctl | flag |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| SOL | 69 | +29.9 | +0.449 | 39 | +33.0 | +32.4 | +32.6 | +0.846 | 76% |  |
| BTC | 56 | +9.4 | +0.197 | 33 | +3.0 | +2.0 | +2.3 | +0.091 | 28% |  |
| ETH | 55 | -2.0 | -0.014 | 39 | +5.0 | +4.1 | +4.4 | +0.128 | 42% |  |
| XRP | 52 | +29.5 | +0.585 | 26 | +6.0 | +5.5 | +5.7 | +0.231 | 22% | gated n<30 |
| POOLED | 232 | +66.8 | +0.309 | 137 | +47.0 | +44.0 | +45.0 | +0.343 | 50% |  |

### 4h per-window net06 (pooled): ungated vs gated (w4 = most recent)

| stream | w0 | w1 | w2 | w3 | w4 | total |
|---|--:|--:|--:|--:|--:|--:|
| ungated | -0.7 | +10.8 | +10.0 | +19.9 | +26.7 | +66.8 |
| gated | -7.4 | +5.2 | +7.5 | +18.3 | +20.5 | +44.0 |

_Counts only, no verdicts. 1h anchor is in STUDY_C.md (micro-aligned pooled net06 +79, pctl 58%)._
