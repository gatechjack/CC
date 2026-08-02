# S4 EV Forensic — Maker Resolution Mini-Study

**Date:** 2026-08-02  
**Question:** is the maker per-ATTEMPT positive (BTC ~+0.04, ETH ~+0.07 at traded-close rest) a real executable edge, or signal-independent spread capture riding an optimistic fill?  
**Standing:** read-only; on-disk (no pulls); lab DB only; evidence only — no verdict.

All numbers are maker **per-ATTEMPT** $/contract (fills@realized, no-fills@$0) unless noted; t = mean/SE (|t|<~2 ~ zero). Resting level = entry-minute TRADED CLOSE. Fill = a later REAL trade prints THROUGH by >= `through` ticks (OPTIMISTIC: no queue/partial fills).

## BTC

### 1a — Null controls (same traded-close rest, baseline fill)

| Variant | model side | random side | always-YES | always-NO | model − mean(controls) |
|---|---|---|---|---|---|
| A | +0.0404 (t=+3.1) | -0.0292 (t=-2.2) | -0.0258 (t=-1.9) | -0.0361 (t=-2.7) | +0.0708 |
| B | +0.0342 (t=+2.7) | -0.0361 (t=-2.8) | -0.0297 (t=-2.3) | -0.0401 (t=-3.1) | +0.0696 |

_If the controls earn ~= model side, the positive is signal-INDEPENDENT spread capture (the model's side choice adds ~nothing over always-YES / always-NO / random)._

### 1b/1c — Pessimism surface (model side) + adverse-selection views

| Variant | Config | per-ATTEMPT (t) | fill_rate | filled/unfilled win% | median fill-min | late-half P&L |
|---|---|---|---|---|---|---|
| A | baseline | +0.0404 (t=+3.1) | 96.5% | 57.7%/100.0% | 2.0 | -0.0767 (n=12) |
| A | through 2 ticks | +0.0163 (t=+1.3) | 91.4% | 55.3%/100.0% | 2.0 | -0.0939 (n=28) |
| A | fill 1 tick worse | +0.0308 (t=+2.3) | 96.5% | 57.7%/100.0% | 2.0 | -0.0867 (n=12) |
| A | skip entry min 1-2 | +0.0346 (t=+2.8) | 96.0% | 57.5%/100.0% | 4.0 | +0.0691 (n=24) |
| A | ALL combined | +0.0081 (t=+0.7) | 91.3% | 55.3%/100.0% | 4.0 | +0.0390 (n=52) |
| B | baseline | +0.0342 (t=+2.7) | 95.4% | 57.2%/100.0% | 3.0 | -0.0047 (n=17) |
| B | through 2 ticks | +0.0114 (t=+0.9) | 89.8% | 54.5%/100.0% | 3.0 | +0.0271 (n=34) |
| B | fill 1 tick worse | +0.0247 (t=+1.9) | 95.4% | 57.2%/100.0% | 3.0 | -0.0147 (n=17) |
| B | skip entry min 1-2 | +0.0248 (t=+2.1) | 94.8% | 56.9%/100.0% | 5.0 | +0.1275 (n=37) |
| B | ALL combined | -0.0065 (t=-0.6) | 88.8% | 54.0%/100.0% | 5.0 | -0.0193 (n=69) |

## ETH

### 1a — Null controls (same traded-close rest, baseline fill)

| Variant | model side | random side | always-YES | always-NO | model − mean(controls) |
|---|---|---|---|---|---|
| A | +0.0706 (t=+5.5) | -0.0256 (t=-1.9) | -0.0146 (t=-1.1) | -0.0564 (t=-4.3) | +0.1028 |
| B | +0.0580 (t=+4.6) | -0.0312 (t=-2.4) | -0.0162 (t=-1.3) | -0.0579 (t=-4.5) | +0.0931 |

_If the controls earn ~= model side, the positive is signal-INDEPENDENT spread capture (the model's side choice adds ~nothing over always-YES / always-NO / random)._

### 1b/1c — Pessimism surface (model side) + adverse-selection views

| Variant | Config | per-ATTEMPT (t) | fill_rate | filled/unfilled win% | median fill-min | late-half P&L |
|---|---|---|---|---|---|---|
| A | baseline | +0.0706 (t=+5.5) | 95.1% | 61.4%/100.0% | 2.0 | -0.1362 (n=29) |
| A | through 2 ticks | +0.0525 (t=+4.1) | 91.0% | 59.7%/100.0% | 2.0 | -0.1492 (n=36) |
| A | fill 1 tick worse | +0.0611 (t=+4.7) | 95.1% | 61.4%/100.0% | 2.0 | -0.1462 (n=29) |
| A | skip entry min 1-2 | +0.0544 (t=+4.4) | 94.7% | 61.3%/100.0% | 4.0 | -0.0507 (n=34) |
| A | ALL combined | +0.0300 (t=+2.5) | 90.7% | 59.6%/100.0% | 4.0 | -0.0753 (n=64) |
| B | baseline | +0.0580 (t=+4.6) | 94.5% | 61.2%/100.0% | 3.0 | -0.1026 (n=27) |
| B | through 2 ticks | +0.0427 (t=+3.4) | 90.7% | 59.6%/100.0% | 3.0 | -0.0078 (n=51) |
| B | fill 1 tick worse | +0.0486 (t=+3.9) | 94.5% | 61.2%/100.0% | 3.0 | -0.1122 (n=27) |
| B | skip entry min 1-2 | +0.0418 (t=+3.5) | 93.1% | 60.6%/100.0% | 5.0 | -0.0258 (n=42) |
| B | ALL combined | +0.0206 (t=+1.7) | 89.5% | 59.0%/100.0% | 5.0 | -0.0228 (n=72) |

## SOL

### 1a — Null controls (same traded-close rest, baseline fill)

| Variant | model side | random side | always-YES | always-NO | model − mean(controls) |
|---|---|---|---|---|---|
| A | +0.0074 (t=+0.6) | -0.0492 (t=-3.8) | -0.0487 (t=-3.7) | -0.0421 (t=-3.2) | +0.0541 |
| B | +0.0048 (t=+0.4) | -0.0382 (t=-3.0) | -0.0459 (t=-3.6) | -0.0396 (t=-3.1) | +0.0461 |

_If the controls earn ~= model side, the positive is signal-INDEPENDENT spread capture (the model's side choice adds ~nothing over always-YES / always-NO / random)._

### 1b/1c — Pessimism surface (model side) + adverse-selection views

| Variant | Config | per-ATTEMPT (t) | fill_rate | filled/unfilled win% | median fill-min | late-half P&L |
|---|---|---|---|---|---|---|
| A | baseline | +0.0074 (t=+0.6) | 93.7% | 54.4%/100.0% | 2.0 | -0.0478 (n=32) |
| A | through 2 ticks | -0.0057 (t=-0.4) | 90.8% | 53.0%/100.0% | 2.0 | +0.0119 (n=47) |
| A | fill 1 tick worse | -0.0019 (t=-0.1) | 93.7% | 54.4%/100.0% | 2.0 | -0.0578 (n=32) |
| A | skip entry min 1-2 | +0.0036 (t=+0.3) | 93.4% | 54.3%/100.0% | 4.0 | -0.1900 (n=41) |
| A | ALL combined | -0.0176 (t=-1.4) | 90.0% | 52.6%/100.0% | 4.0 | -0.1643 (n=63) |
| B | baseline | +0.0048 (t=+0.4) | 93.5% | 54.3%/100.0% | 3.0 | -0.1439 (n=30) |
| B | through 2 ticks | -0.0057 (t=-0.5) | 91.1% | 53.1%/100.0% | 3.0 | -0.0692 (n=40) |
| B | fill 1 tick worse | -0.0045 (t=-0.4) | 93.5% | 54.3%/100.0% | 3.0 | -0.1539 (n=30) |
| B | skip entry min 1-2 | -0.0039 (t=-0.3) | 92.4% | 53.8%/100.0% | 5.0 | -0.0380 (n=59) |
| B | ALL combined | -0.0256 (t=-2.1) | 88.6% | 51.8%/100.0% | 5.0 | -0.0131 (n=87) |

## XRP

### 1a — Null controls (same traded-close rest, baseline fill)

| Variant | model side | random side | always-YES | always-NO | model − mean(controls) |
|---|---|---|---|---|---|
| A | +0.0146 (t=+1.1) | -0.0336 (t=-2.6) | -0.0300 (t=-2.3) | -0.0508 (t=-3.9) | +0.0528 |
| B | +0.0108 (t=+0.9) | -0.0336 (t=-2.6) | -0.0342 (t=-2.7) | -0.0448 (t=-3.5) | +0.0484 |

_If the controls earn ~= model side, the positive is signal-INDEPENDENT spread capture (the model's side choice adds ~nothing over always-YES / always-NO / random)._

### 1b/1c — Pessimism surface (model side) + adverse-selection views

| Variant | Config | per-ATTEMPT (t) | fill_rate | filled/unfilled win% | median fill-min | late-half P&L |
|---|---|---|---|---|---|---|
| A | baseline | +0.0146 (t=+1.1) | 94.6% | 54.9%/100.0% | 2.0 | -0.2176 (n=29) |
| A | through 2 ticks | +0.0023 (t=+0.2) | 91.8% | 53.5%/100.0% | 2.0 | -0.2265 (n=40) |
| A | fill 1 tick worse | +0.0052 (t=+0.4) | 94.6% | 54.9%/100.0% | 2.0 | -0.2276 (n=29) |
| A | skip entry min 1-2 | +0.0076 (t=+0.6) | 93.9% | 54.6%/100.0% | 4.0 | -0.0488 (n=41) |
| A | ALL combined | -0.0157 (t=-1.3) | 90.1% | 52.6%/100.0% | 4.0 | -0.0320 (n=61) |
| B | baseline | +0.0108 (t=+0.9) | 94.3% | 54.7%/100.0% | 3.0 | -0.0811 (n=37) |
| B | through 2 ticks | -0.0013 (t=-0.1) | 91.4% | 53.3%/100.0% | 3.0 | -0.0180 (n=55) |
| B | fill 1 tick worse | +0.0015 (t=+0.1) | 94.3% | 54.7%/100.0% | 3.0 | -0.0911 (n=37) |
| B | skip entry min 1-2 | +0.0062 (t=+0.5) | 92.9% | 54.1%/100.0% | 5.0 | +0.0083 (n=55) |
| B | ALL combined | -0.0125 (t=-1.0) | 90.1% | 52.6%/100.0% | 5.0 | +0.0107 (n=77) |

## Reading this (evidence, not verdict)

- **1a:** model ≈ controls ⇒ spread capture (signal-independent); model ≫ controls ⇒ a directional-signal component. always-YES vs always-NO also shows any structural long/short bias in the fill model.
- **1b:** the pessimism knobs each remove a slice of the optimism. If the per-ATTEMPT positive collapses to ~0 (within ~2 SE) under 2-tick / rest-worse / skip-1-2 / ALL, it did not survive executable frictions.
- **1c:** unfilled ~100% winners persists ⇒ the fill model keeps missing the winners; per-ATTEMPT already books those at $0, so it is the honest number to judge.

