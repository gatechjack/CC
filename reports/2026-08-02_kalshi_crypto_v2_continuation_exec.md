# S4 Executable Continuation — is the mid-window under-reaction harvestable?

**Date:** 2026-08-02  
**Strategy:** at the first minute m in {1,2,3} where the underlying (Binance) moved |move|>=threshold from open, BUY the CONTINUATION side (YES if up / NO if down); hold to S1 settlement. One trade per window.  
**Standing:** read-only; on-disk (no pulls); lab DB only; evidence only — no verdict. Each window = one trade ⇒ independent obs, clean SEs.

Taker@traded = buy the side at a real print you could cross to (`price_high` YES / `1-price_low` NO); taker@quote = the entry ask (stale-quote contrast). Maker = rest at the entry-minute TRADED CLOSE, fill on a real >=1-tick trade-through; per-ATTEMPT books no-fills at $0; fill_rate beside every maker figure. `kalshi_fee` on every fill. Chronological holdout = last 20% by open_ts (the rule is deterministic; the split is a temporal-stability check).

> ★ **T5 BASIS CAVEAT (every table):** the qualifying MOVE is Binance; SETTLEMENT is CF-Benchmarks RTI. The Binance→RTI proxy mismatch is unquantified here and sits under every number.

## BTC

Settled windows: 6524.

### HOLDOUT — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 613 (300/313) | 70.3% | $+0.0698 (t=+3.9) | $+0.1367 (t=+7.6) | $+0.0902 (t=+5.1) | 86.6% | 65.7%/100.0% |
| 0.10% ★ | 232 (116/116) | 81.0% | $+0.1246 (t=+4.9) | $+0.1690 (t=+6.6) | $+0.1071 (t=+4.3) | 80.6% | 76.5%/100.0% |
| 0.15% | 91 (45/46) | 84.6% | $+0.1212 (t=+3.2) | $+0.1612 (t=+4.2) | $+0.0929 (t=+2.5) | 76.9% | 80.0%/100.0% |
| 0.20% | 39 (18/21) | 76.9% | $+0.0510 (t=+0.8) | $+0.1013 (t=+1.6) | $+0.0667 (t=+1.1) | 87.2% | 73.5%/100.0% |

### TRAIN — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 3282 (1594/1688) | 66.9% | $+0.0451 (t=+5.7) | $+0.1137 (t=+14.6) | $+0.0718 (t=+9.5) | 88.4% | 62.6%/100.0% |
| 0.10% ★ | 1586 (745/841) | 71.6% | $+0.0667 (t=+6.1) | $+0.1274 (t=+11.9) | $+0.0814 (t=+7.8) | 86.1% | 67.0%/100.0% |
| 0.15% | 818 (373/445) | 74.6% | $+0.0751 (t=+5.1) | $+0.1314 (t=+9.1) | $+0.0891 (t=+6.4) | 85.9% | 70.4%/100.0% |
| 0.20% | 470 (216/254) | 76.6% | $+0.0795 (t=+4.3) | $+0.1322 (t=+7.3) | $+0.0899 (t=+5.1) | 84.7% | 72.4%/100.0% |

**Maker adverse-selection views — HOLDOUT, threshold 0.10%:**

- *Fill timing:* median 4.0m (p25 3.0 / p75 4.0); early(<=7.5m, n=175) $+0.1396 (t=+4.4), late(>7.5m, n=12) $+0.0358 (t=+0.3).
- *Filled vs unfilled win-rate:* filled 76.5% (n=187), unfilled 100.0% (n=45).
- *Per-ATTEMPT vs per-fill:* $+0.1071 (t=+4.3) (n=232) vs per-fill $+0.1329 (t=+4.3) (n=187).

## ETH

Settled windows: 6524.

### HOLDOUT — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 833 (410/423) | 66.3% | $+0.0480 (t=+3.0) | $+0.1137 (t=+7.2) | $+0.0611 (t=+4.1) | 86.3% | 60.9%/100.0% |
| 0.10% ★ | 407 (209/198) | 73.2% | $+0.0613 (t=+2.8) | $+0.1165 (t=+5.4) | $+0.0595 (t=+2.9) | 83.5% | 67.9%/100.0% |
| 0.15% | 201 (101/100) | 77.1% | $+0.0630 (t=+2.2) | $+0.1095 (t=+4.0) | $+0.0569 (t=+2.1) | 81.1% | 71.8%/100.0% |
| 0.20% | 102 (49/53) | 81.4% | $+0.0930 (t=+2.4) | $+0.1390 (t=+3.7) | $+0.0863 (t=+2.4) | 81.4% | 77.1%/100.0% |

### TRAIN — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 3863 (1905/1958) | 66.0% | $+0.0496 (t=+6.7) | $+0.1130 (t=+15.6) | $+0.0538 (t=+7.8) | 83.8% | 59.4%/100.0% |
| 0.10% ★ | 2153 (1017/1136) | 71.6% | $+0.0805 (t=+8.6) | $+0.1345 (t=+14.5) | $+0.0669 (t=+7.6) | 80.6% | 64.7%/100.0% |
| 0.15% | 1247 (588/659) | 74.7% | $+0.0920 (t=+7.8) | $+0.1423 (t=+12.2) | $+0.0647 (t=+5.9) | 76.8% | 67.1%/100.0% |
| 0.20% | 767 (359/408) | 77.4% | $+0.1060 (t=+7.2) | $+0.1537 (t=+10.6) | $+0.0784 (t=+5.7) | 76.4% | 70.5%/100.0% |

**Maker adverse-selection views — HOLDOUT, threshold 0.10%:**

- *Fill timing:* median 4.0m (p25 3.0 / p75 4.0); early(<=7.5m, n=309) $+0.0761 (t=+3.0), late(>7.5m, n=31) $+0.0226 (t=+0.2).
- *Filled vs unfilled win-rate:* filled 67.9% (n=340), unfilled 100.0% (n=67).
- *Per-ATTEMPT vs per-fill:* $+0.0595 (t=+2.9) (n=407) vs per-fill $+0.0713 (t=+2.9) (n=340).

## SOL

Settled windows: 6524.

### HOLDOUT — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 948 (477/471) | 65.7% | $+0.0543 (t=+3.6) | $+0.1108 (t=+7.4) | $+0.0433 (t=+3.1) | 83.1% | 58.8%/100.0% |
| 0.10% ★ | 408 (205/203) | 72.5% | $+0.0788 (t=+3.7) | $+0.1235 (t=+5.9) | $+0.0580 (t=+2.9) | 80.4% | 65.9%/100.0% |
| 0.15% | 172 (84/88) | 77.9% | $+0.0898 (t=+2.9) | $+0.1307 (t=+4.3) | $+0.0465 (t=+1.7) | 75.0% | 70.5%/100.0% |
| 0.20% | 90 (40/50) | 82.2% | $+0.1003 (t=+2.6) | $+0.1289 (t=+3.3) | $+0.0505 (t=+1.4) | 72.2% | 75.4%/100.0% |

### TRAIN — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 4324 (2113/2211) | 64.8% | $+0.0507 (t=+7.2) | $+0.1023 (t=+14.8) | $+0.0349 (t=+5.4) | 81.8% | 57.0%/100.0% |
| 0.10% ★ | 2779 (1311/1468) | 69.3% | $+0.0773 (t=+9.2) | $+0.1201 (t=+14.4) | $+0.0421 (t=+5.4) | 78.0% | 60.6%/100.0% |
| 0.15% | 1679 (776/903) | 73.5% | $+0.1005 (t=+9.7) | $+0.1358 (t=+13.2) | $+0.0502 (t=+5.3) | 74.6% | 64.5%/100.0% |
| 0.20% | 1063 (472/591) | 74.9% | $+0.0986 (t=+7.7) | $+0.1335 (t=+10.6) | $+0.0452 (t=+3.9) | 72.5% | 65.4%/100.0% |

**Maker adverse-selection views — HOLDOUT, threshold 0.10%:**

- *Fill timing:* median 4.0m (p25 3.0 / p75 5.0); early(<=7.5m, n=294) $+0.1015 (t=+4.0), late(>7.5m, n=34) $-0.1812 (t=-2.4).
- *Filled vs unfilled win-rate:* filled 65.9% (n=328), unfilled 100.0% (n=80).
- *Per-ATTEMPT vs per-fill:* $+0.0580 (t=+2.9) (n=408) vs per-fill $+0.0722 (t=+2.9) (n=328).

## XRP

Settled windows: 6524.

### HOLDOUT — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 780 (396/384) | 67.4% | $+0.0604 (t=+3.7) | $+0.1098 (t=+6.8) | $+0.0423 (t=+2.8) | 82.1% | 60.3%/100.0% |
| 0.10% ★ | 361 (181/180) | 76.7% | $+0.1030 (t=+4.8) | $+0.1461 (t=+6.8) | $+0.0882 (t=+4.2) | 81.7% | 71.5%/100.0% |
| 0.15% | 138 (66/72) | 81.9% | $+0.1101 (t=+3.5) | $+0.1455 (t=+4.6) | $+0.0944 (t=+3.1) | 79.7% | 77.3%/100.0% |
| 0.20% | 60 (32/28) | 83.3% | $+0.1094 (t=+2.5) | $+0.1459 (t=+3.4) | $+0.0794 (t=+1.9) | 71.7% | 76.7%/100.0% |

### TRAIN — threshold sweep

| Threshold | n trades (up/down) | taker win% | taker@traded (t) | taker@quote (t) | maker per-ATTEMPT (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|---|---|
| 0.05% | 4033 (1967/2066) | 65.5% | $+0.0606 (t=+8.3) | $+0.1014 (t=+14.2) | $+0.0330 (t=+5.0) | 80.2% | 57.0%/99.9% |
| 0.10% ★ | 2339 (1108/1231) | 71.6% | $+0.0956 (t=+10.7) | $+0.1274 (t=+14.4) | $+0.0497 (t=+6.1) | 76.1% | 62.6%/100.0% |
| 0.15% | 1320 (614/706) | 74.8% | $+0.1068 (t=+9.3) | $+0.1312 (t=+11.5) | $+0.0447 (t=+4.3) | 71.9% | 65.0%/100.0% |
| 0.20% | 794 (382/412) | 77.1% | $+0.1148 (t=+7.9) | $+0.1360 (t=+9.4) | $+0.0454 (t=+3.5) | 69.5% | 67.0%/100.0% |

**Maker adverse-selection views — HOLDOUT, threshold 0.10%:**

- *Fill timing:* median 4.0m (p25 3.0 / p75 5.0); early(<=7.5m, n=263) $+0.1210 (t=+4.6), late(>7.5m, n=32) $+0.0003 (t=+0.0).
- *Filled vs unfilled win-rate:* filled 71.5% (n=295), unfilled 100.0% (n=66).
- *Per-ATTEMPT vs per-fill:* $+0.0882 (t=+4.2) (n=361) vs per-fill $+0.1079 (t=+4.3) (n=295).

## Reading this (evidence, not verdict)

- **Taker@traded <=0 or ~0 ⇒ the continuation gap is NOT harvestable by crossing the spread** (the calibration gap lived in the traded MEAN; the executable ask already reflects it). taker@quote >> taker@traded again sizes the stale-quote effect.
- **Maker per-ATTEMPT** is the honest number (no-fills at $0); judge it with fill_rate + the filled/unfilled split. A positive per-fill with a ~0 per-attempt is not tradeable.
- **Holdout vs train** stability + the ★0.10% row are the headline; the sweep shows threshold sensitivity. **T5 basis (Binance move vs RTI settle) is unquantified and could move all of this.**

