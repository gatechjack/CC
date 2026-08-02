# S4 EV Forensic — does the flagged positive dual-EV survive?

**Date:** 2026-08-02  
**Scope:** Kalshi 15m up/down binaries, BTC/ETH/SOL/XRP, v1 holdout (last 20% by open_ts).  
**Standing:** read-only research; no order/placement surface; lab DB only; evidence only — no verdict.

## What changed vs the v1 benchmark

- **Traded-price OHLC** (`price.{open,high,low,close}`) re-pulled for the whole 15m corpus (only `price_mean` was stored before).
- **Executable entry** — the first in-window 1m candle that actually TRADED (vol>0, real range), NOT the ~1-min-in open candle whose quote band is degenerate (probe: `yes_ask` open ~0.999 / `yes_bid` open ~0.000 — no two-sided market at the open tick). Variant B enters on the SECOND tradeable minute (stricter).
- **Taker** buys the model's side at a real print you could cross to (`price_high` for YES / `1-price_low` for NO), fee included. `taker@quote` (the entry ask) is shown alongside — the gap is the stale-quote artifact.
- **Maker** rests at the entry-minute **TRADED CLOSE** (`price_close`, approved spec — a real print, not the stale first-minute bid quote) and fills ONLY on a real trade-through by >=1 tick (traded `price_low<=rest-tick` for YES / `price_high>=rest+tick` for NO), so **fill_rate is realistic (<1)** and is reported beside every maker figure. (The v1 fill_rate=1.0 came from counting bid-QUOTE wiggles as fills.) The bid/ask-quote resting level is shown only as a non-executable resting-level sensitivity contrast.

Realized P&L/contract = `(1 if win else 0) - fill_price - kalshi_fee`. `kalshi_fee = ceil(0.07*p*(1-p))` applied to every entry (taker AND maker; no settlement fee). SE = standard error of the mean; t = mean/SE (|t|<~2 is indistinguishable from zero).

## Summary — taker@traded (executable, guaranteed fill) vs maker per-ATTEMPT (traded-close rest)

| Asset | Var | Taker@traded $/ct (t) | Maker per-ATTEMPT $/ct (t) | maker fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| BTC | A | -0.0408 (t=-3.0) | +0.0404 (t=+3.1) | 96.5% | 57.7% / 100.0% |
| BTC | B | -0.0254 (t=-1.9) | +0.0342 (t=+2.7) | 95.4% | 57.2% / 100.0% |
| ETH | A | +0.0057 (t=+0.4) | +0.0706 (t=+5.5) | 95.1% | 61.4% / 100.0% |
| ETH | B | +0.0061 (t=+0.5) | +0.0580 (t=+4.6) | 94.5% | 61.2% / 100.0% |
| SOL | A | -0.0487 (t=-3.6) | +0.0074 (t=+0.6) | 93.7% | 54.4% / 100.0% |
| SOL | B | -0.0413 (t=-3.1) | +0.0048 (t=+0.4) | 93.5% | 54.3% / 100.0% |
| XRP | A | -0.0461 (t=-3.4) | +0.0146 (t=+1.1) | 94.6% | 54.9% / 100.0% |
| XRP | B | -0.0374 (t=-2.9) | +0.0108 (t=+0.9) | 94.3% | 54.7% / 100.0% |

_Maker per-ATTEMPT rides an OPTIMISTIC fill assumption (you fill at your resting price whenever a later trade prints >=1 tick through it — no queue position, no partial fills). Read it against that and the adverse-selection views below; a realistic queue model is the obvious next test._

## BTC

Holdout windows: 1304 | markets with re-pulled traded OHLC: 1298

### Variant A = first tradeable minute

- Covered windows (valid entry + fill): **1298**; taker win rate 59.2%; mean model_p 0.5102, mean market_p (entry price_mean) 0.4979, mean |edge| 0.0839.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | -0.0408 | +/-0.0135 | -3.02 |
| Taker @ quote (artifact contrast) | +0.0510 | +/-0.0133 | 3.83 |
| Taker @ traded, model-implied +EV subset (n=256) | +0.0165 | +/-0.0307 | 0.54 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 96.5%, 1252/1298) | +0.0404 | +/-0.0132 | 3.07 |
| Maker per-fill (traded-close rest, fills only) | +0.0419 | +/-0.0136 | 3.07 |

_Model-implied EV (not realized): taker@traded $-0.0748, maker@fills $+0.0228._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **2.0** / median **2.0** / p75 **2.0** (mean 2.2, range 2.0-14.0). P&L by fill half: early (<=7.5m, n=1240) $+0.0430+/-0.0137 (t=+3.1); late (>7.5m, n=12) $-0.0767+/-0.1589 (t=-0.5).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **57.7%** (n=1252); unfilled attempts would have won **100.0%** (n=46). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0404+/-0.0132 (t=+3.1)** (n=1298 attempts) vs per-FILL $+0.0419+/-0.0136 (t=+3.1) (n=1252).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 93.8%, per-ATTEMPT $+0.0324+/-0.0130 (t=+2.5), per-fill $+0.0345+/-0.0138 (t=+2.5) — shown only to size how much the resting-level choice moves the result.

### Variant B = second tradeable minute (stricter)

- Covered windows (valid entry + fill): **1298**; taker win rate 59.2%; mean model_p 0.5102, mean market_p (entry price_mean) 0.4953, mean |edge| 0.1156.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | -0.0254 | +/-0.0131 | -1.93 |
| Taker @ quote (artifact contrast) | +0.0480 | +/-0.0130 | 3.71 |
| Taker @ traded, model-implied +EV subset (n=425) | -0.0338 | +/-0.0236 | -1.43 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 95.4%, 1238/1298) | +0.0342 | +/-0.0128 | 2.68 |
| Maker per-fill (traded-close rest, fills only) | +0.0359 | +/-0.0134 | 2.68 |

_Model-implied EV (not realized): taker@traded $-0.0594, maker@fills $+0.0213._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **3.0** / median **3.0** / p75 **3.0** (mean 3.2, range 3.0-13.0). P&L by fill half: early (<=7.5m, n=1221) $+0.0365+/-0.0135 (t=+2.7); late (>7.5m, n=17) $-0.0047+/-0.1181 (t=-0.0).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **57.2%** (n=1238); unfilled attempts would have won **100.0%** (n=60). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0342+/-0.0128 (t=+2.7)** (n=1298 attempts) vs per-FILL $+0.0359+/-0.0134 (t=+2.7) (n=1238).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 92.6%, per-ATTEMPT $+0.0261+/-0.0126 (t=+2.1), per-fill $+0.0282+/-0.0136 (t=+2.1) — shown only to size how much the resting-level choice moves the result.

## ETH

Holdout windows: 1304 | markets with re-pulled traded OHLC: 1298

### Variant A = first tradeable minute

- Covered windows (valid entry + fill): **1298**; taker win rate 63.3%; mean model_p 0.5008, mean market_p (entry price_mean) 0.5013, mean |edge| 0.0970.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | +0.0057 | +/-0.0132 | 0.44 |
| Taker @ quote (artifact contrast) | +0.0869 | +/-0.0131 | 6.62 |
| Taker @ traded, model-implied +EV subset (n=423) | +0.0313 | +/-0.0238 | 1.32 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 95.1%, 1234/1298) | +0.0706 | +/-0.0129 | 5.47 |
| Maker per-fill (traded-close rest, fills only) | +0.0743 | +/-0.0136 | 5.47 |

_Model-implied EV (not realized): taker@traded $-0.0477, maker@fills $+0.0388._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **2.0** / median **2.0** / p75 **2.0** (mean 2.4, range 2.0-14.0). P&L by fill half: early (<=7.5m, n=1205) $+0.0793+/-0.0137 (t=+5.8); late (>7.5m, n=29) $-0.1362+/-0.0901 (t=-1.5).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **61.4%** (n=1234); unfilled attempts would have won **100.0%** (n=64). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0706+/-0.0129 (t=+5.5)** (n=1298 attempts) vs per-FILL $+0.0743+/-0.0136 (t=+5.5) (n=1234).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 92.1%, per-ATTEMPT $+0.0631+/-0.0127 (t=+5.0), per-fill $+0.0686+/-0.0138 (t=+5.0) — shown only to size how much the resting-level choice moves the result.

### Variant B = second tradeable minute (stricter)

- Covered windows (valid entry + fill): **1298**; taker win rate 63.3%; mean model_p 0.5008, mean market_p (entry price_mean) 0.4969, mean |edge| 0.1226.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | +0.0061 | +/-0.0129 | 0.47 |
| Taker @ quote (artifact contrast) | +0.0746 | +/-0.0128 | 5.82 |
| Taker @ traded, model-implied +EV subset (n=483) | +0.0284 | +/-0.0223 | 1.27 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 94.5%, 1227/1298) | +0.0580 | +/-0.0126 | 4.60 |
| Maker per-fill (traded-close rest, fills only) | +0.0613 | +/-0.0133 | 4.61 |

_Model-implied EV (not realized): taker@traded $-0.0474, maker@fills $+0.0279._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **3.0** / median **3.0** / p75 **3.0** (mean 3.3, range 3.0-15.0). P&L by fill half: early (<=7.5m, n=1200) $+0.0650+/-0.0134 (t=+4.8); late (>7.5m, n=27) $-0.1026+/-0.0993 (t=-1.0).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **61.2%** (n=1227); unfilled attempts would have won **100.0%** (n=71). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0580+/-0.0126 (t=+4.6)** (n=1298 attempts) vs per-FILL $+0.0613+/-0.0133 (t=+4.6) (n=1227).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 91.5%, per-ATTEMPT $+0.0520+/-0.0125 (t=+4.2), per-fill $+0.0568+/-0.0136 (t=+4.2) — shown only to size how much the resting-level choice moves the result.

## SOL

Holdout windows: 1304 | markets with re-pulled traded OHLC: 1298

### Variant A = first tradeable minute

- Covered windows (valid entry + fill): **1298**; taker win rate 57.3%; mean model_p 0.5030, mean market_p (entry price_mean) 0.4962, mean |edge| 0.0910.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | -0.0487 | +/-0.0135 | -3.60 |
| Taker @ quote (artifact contrast) | +0.0300 | +/-0.0134 | 2.24 |
| Taker @ traded, model-implied +EV subset (n=340) | -0.0147 | +/-0.0271 | -0.54 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 93.7%, 1216/1298) | +0.0074 | +/-0.0130 | 0.57 |
| Maker per-fill (traded-close rest, fills only) | +0.0080 | +/-0.0139 | 0.57 |

_Model-implied EV (not realized): taker@traded $-0.0635, maker@fills $+0.0216._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **2.0** / median **2.0** / p75 **2.0** (mean 2.5, range 2.0-15.0). P&L by fill half: early (<=7.5m, n=1184) $+0.0095+/-0.0141 (t=+0.7); late (>7.5m, n=32) $-0.0478+/-0.0787 (t=-0.6).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **54.4%** (n=1216); unfilled attempts would have won **100.0%** (n=82). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0074+/-0.0130 (t=+0.6)** (n=1298 attempts) vs per-FILL $+0.0080+/-0.0139 (t=+0.6) (n=1216).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 91.7%, per-ATTEMPT $+0.0057+/-0.0129 (t=+0.4), per-fill $+0.0062+/-0.0140 (t=+0.4) — shown only to size how much the resting-level choice moves the result.

### Variant B = second tradeable minute (stricter)

- Covered windows (valid entry + fill): **1298**; taker win rate 57.3%; mean model_p 0.5030, mean market_p (entry price_mean) 0.4941, mean |edge| 0.1172.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | -0.0413 | +/-0.0132 | -3.12 |
| Taker @ quote (artifact contrast) | +0.0255 | +/-0.0131 | 1.94 |
| Taker @ traded, model-implied +EV subset (n=432) | -0.0338 | +/-0.0235 | -1.44 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 93.5%, 1213/1298) | +0.0048 | +/-0.0127 | 0.38 |
| Maker per-fill (traded-close rest, fills only) | +0.0052 | +/-0.0136 | 0.38 |

_Model-implied EV (not realized): taker@traded $-0.0560, maker@fills $+0.0196._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **3.0** / median **3.0** / p75 **3.0** (mean 3.4, range 3.0-15.0). P&L by fill half: early (<=7.5m, n=1183) $+0.0090+/-0.0138 (t=+0.6); late (>7.5m, n=30) $-0.1439+/-0.0778 (t=-1.8).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **54.3%** (n=1213); unfilled attempts would have won **100.0%** (n=85). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0048+/-0.0127 (t=+0.4)** (n=1298 attempts) vs per-FILL $+0.0052+/-0.0136 (t=+0.4) (n=1213).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 91.2%, per-ATTEMPT $+0.0027+/-0.0126 (t=+0.2), per-fill $+0.0030+/-0.0138 (t=+0.2) — shown only to size how much the resting-level choice moves the result.

## XRP

Holdout windows: 1304 | markets with re-pulled traded OHLC: 1298

### Variant A = first tradeable minute

- Covered windows (valid entry + fill): **1298**; taker win rate 57.3%; mean model_p 0.5037, mean market_p (entry price_mean) 0.5048, mean |edge| 0.1015.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | -0.0461 | +/-0.0135 | -3.42 |
| Taker @ quote (artifact contrast) | +0.0316 | +/-0.0133 | 2.38 |
| Taker @ traded, model-implied +EV subset (n=411) | +0.0054 | +/-0.0241 | 0.22 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 94.6%, 1228/1298) | +0.0146 | +/-0.0130 | 1.13 |
| Maker per-fill (traded-close rest, fills only) | +0.0155 | +/-0.0137 | 1.13 |

_Model-implied EV (not realized): taker@traded $-0.0503, maker@fills $+0.0354._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **2.0** / median **2.0** / p75 **2.0** (mean 2.4, range 2.0-15.0). P&L by fill half: early (<=7.5m, n=1199) $+0.0211+/-0.0139 (t=+1.5); late (>7.5m, n=29) $-0.2176+/-0.0765 (t=-2.8).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **54.9%** (n=1228); unfilled attempts would have won **100.0%** (n=70). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0146+/-0.0130 (t=+1.1)** (n=1298 attempts) vs per-FILL $+0.0155+/-0.0137 (t=+1.1) (n=1228).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 92.4%, per-ATTEMPT $+0.0137+/-0.0128 (t=+1.1), per-fill $+0.0148+/-0.0139 (t=+1.1) — shown only to size how much the resting-level choice moves the result.

### Variant B = second tradeable minute (stricter)

- Covered windows (valid entry + fill): **1298**; taker win rate 57.3%; mean model_p 0.5037, mean market_p (entry price_mean) 0.5012, mean |edge| 0.1290.

| Leg | mean $/contract | SE | t |
|---|---|---|---|
| **Taker @ traded** (primary, all covered) | -0.0374 | +/-0.0131 | -2.86 |
| Taker @ quote (artifact contrast) | +0.0251 | +/-0.0130 | 1.94 |
| Taker @ traded, model-implied +EV subset (n=507) | -0.0260 | +/-0.0214 | -1.21 |
| **Maker per-ATTEMPT** (traded-close rest, fill_rate 94.3%, 1224/1298) | +0.0108 | +/-0.0127 | 0.85 |
| Maker per-fill (traded-close rest, fills only) | +0.0115 | +/-0.0135 | 0.85 |

_Model-implied EV (not realized): taker@traded $-0.0417, maker@fills $+0.0323._

**Maker adverse-selection views** (resting level = entry-minute TRADED CLOSE, approved spec)

- *View 1 — fill timing:* minutes-into-window at fill (15m window) p25 **3.0** / median **3.0** / p75 **3.0** (mean 3.4, range 3.0-14.0). P&L by fill half: early (<=7.5m, n=1187) $+0.0144+/-0.0137 (t=+1.1); late (>7.5m, n=37) $-0.0811+/-0.0806 (t=-1.0).
- *View 2 — filled vs unfilled win-rate:* filled attempts won **54.7%** (n=1224); unfilled attempts would have won **100.0%** (n=74). If unfilled >> filled, the per-fill P&L is selection, not edge.
- *View 3 — per-ATTEMPT EV* (fills@realized, no-fills@$0, the number a strategy actually earns): **$+0.0108+/-0.0127 (t=+0.9)** (n=1298 attempts) vs per-FILL $+0.0115+/-0.0135 (t=+0.9) (n=1224).
- *Resting-level sensitivity (contrast, non-executable):* resting at the entry-minute BID/ASK QUOTE instead (stale-first-minute-quote family) gives fill_rate 92.1%, per-ATTEMPT $+0.0084+/-0.0125 (t=+0.7), per-fill $+0.0091+/-0.0136 (t=+0.7) — shown only to size how much the resting-level choice moves the result.

## Reading this (evidence, not verdict)

- If **taker@traded** mean P&L is <= 0 or within ~2 SE of zero, the positive EV does NOT survive an executable entry — it was the stale-quote artifact (visible as taker@quote >> taker@traded).
- **Maker: judge on the adverse-selection views, not per-fill P&L.** (1) If fills cluster LATE (near settlement) and late-fill P&L is worse, fills are convergence-driven adverse selection. (2) If UNFILLED attempts have a higher would-have-won rate than FILLED, the per-fill P&L is selection — you miss precisely the winners. (3) **Per-ATTEMPT EV** (no-fills booked at $0) is what a resting-maker strategy actually earns; a positive per-fill number with a negative/near-zero per-attempt number is not a tradeable edge.
- Consistent with the settled Brier result (model ~= market, skill +/-0.02 noise): a real directional 5-9%/contract edge would beat the market Brier; it does not. So any positive maker number is NOT the model's directional skill.
- **What a positive maker per-attempt would be, if real:** spread/range capture — the maker enters at the traded CLOSE while the taker pays the HIGH; the gap is the intra-minute range. The model only picks which side to rest on, and at ~coin-flip skill that side is ~random. **Open diagnostic (not run):** re-run the maker with a fixed/random side — if the positive persists, it is signal-INDEPENDENT microstructure capture, not the SFP/model signal this study set out to test.
- **Load-bearing assumption — the maker fill is OPTIMISTIC:** it books a fill at your resting price whenever a later trade prints >=1 tick through it, with NO queue position and NO partial fills. fill_rate 0.93-0.97 is very high; a realistic queue/size model would lower fills (and the missed ones are ~100% winners, View 2) and could erase the maker positive. This is the make-or-break follow-up before any maker EV claim.

