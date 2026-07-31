# OptiTrade -- independent replication & honest walk-forward backtest

**Corpus:** `binance_perp_corpus.db` -- Binance USD-M perp (provenance proven, see `03_venue_corpus_comparison.md`). 4 coins x 5 TFs, native, 100% contiguous, IS/OOS windows drawn from 2022-07 .. 2026-06-30 (OOS ends ~31d stale).

**Protocol:** walk-forward -- grid-optimize on first 70% (IS), freeze, evaluate on last 30% (OOS). Objective = **max GROSS sum-R s.t. IS n>=30**. Grid: L in {10..50 step5}, slMult {1.0..4.0 step0.5}, RR {1.0..4.5 step0.5}, bias {0..10 step2} = 3,024 combos/cell. minSep=6, warmup=120.

**Intrabar:** SL-FIRST (conservative) is primary; `OOS TPfirst` column is the same params under TP-first (optimistic) as a sensitivity. **GROSS R is primary**; `net06`/`net04` subtract Bitunix taker fees at 0.06%/side and 0.04%/side (both sides), expressed in R per each trade's own risk unit (`fee_rate*(entry+exit_notional)/risk`).

**R:** 1R = entry->SL = slMult*ATR(14). Four TP rungs at RR*(i/4) R, each closes 1/4; fixed SL closes remainder (no breakeven move -- spec is silent). Engine unit-tested 26/26 (`t_unit.py`).

**Full record:** `results_full.csv` holds every metric -- n, WR, avgR, sumR, PF, maxDD(R) -- for BOTH the IS and OOS windows and BOTH configs, plus net06/net04 and the TP-first sensitivity. The tables below summarise the decision-relevant subset (IS n/sumR/PF for the decay read; full OOS).


## 1. Walk-forward winner (optimized IS -> frozen OOS)

| coin | tf | L/sl/RR/bias | IS n | IS sumR | IS PF | OOS n | OOS WR | OOS avgR | OOS sumR(gross) | OOS PF | OOS maxDD | OOS net06 | OOS net04 | OOS TPfirst | flag |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 3m | 15/1.5/4.5/2 | 8470 | 404.2 | 1.08 | 3655 | 32.4% | 0.044 | 159.8 | 1.07 | -55.0 | -3234.5 | -2103.0 | 171.1 |  |
| BTCUSDT | 15m | 10/1.5/4.0/0 | 1829 | 130.1 | 1.13 | 819 | 32.7% | -0.038 | -31.1 | 0.93 | -43.0 | -294.6 | -206.7 | -29.3 |  |
| BTCUSDT | 1h | 15/2.5/2.5/2 | 238 | 47.8 | 1.49 | 128 | 37.5% | 0.023 | 2.9 | 1.05 | -12.2 | -8.0 | -4.4 | 2.9 |  |
| BTCUSDT | 4h | 20/1.5/2.5/2 | 94 | 29.9 | 1.86 | 43 | 32.6% | -0.045 | -1.9 | 0.92 | -8.4 | -5.0 | -4.0 | -1.9 |  |
| BTCUSDT | 1d | 10/1.0/2.0/0 | 30 | 12.1 | 2.33 | 15 | 66.7% | 0.500 | 7.5 | 2.76 | -2.6 | 6.9 | 7.1 | 7.5 | OOS n<30 |
| ETHUSDT | 3m | 10/1.0/4.5/0 | 15080 | 674.3 | 1.07 | 6443 | 30.7% | 0.009 | 59.0 | 1.01 | -103.6 | -5414.2 | -3589.8 | 96.9 |  |
| ETHUSDT | 15m | 15/1.0/4.5/4 | 2064 | 293.8 | 1.25 | 890 | 29.9% | 0.002 | 1.6 | 1.00 | -92.8 | -287.5 | -191.2 | 14.4 |  |
| ETHUSDT | 1h | 25/1.0/4.5/10 | 221 | 45.5 | 1.37 | 119 | 28.6% | -0.077 | -9.2 | 0.88 | -17.2 | -25.5 | -20.1 | -8.7 |  |
| ETHUSDT | 4h | 30/1.0/4.5/4 | 62 | 32.4 | 2.21 | 36 | 36.1% | 0.247 | 8.9 | 1.39 | -8.8 | 6.4 | 7.2 | 8.9 |  |
| ETHUSDT | 1d | 10/1.0/2.5/0 | 30 | 1.7 | 1.12 | 11 | 54.5% | 0.409 | 4.5 | 3.00 | -2.2 | 4.2 | 4.3 | 4.5 | OOS n<30 |
| SOLUSDT | 3m | 10/1.0/4.5/2 | 14587 | 493.4 | 1.06 | 6359 | 31.2% | 0.019 | 120.2 | 1.03 | -131.1 | -4078.6 | -2679.0 | 149.6 |  |
| SOLUSDT | 15m | 50/1.0/4.5/0 | 745 | 147.7 | 1.36 | 325 | 30.8% | -0.003 | -0.9 | 1.00 | -35.9 | -81.6 | -54.7 | 2.9 |  |
| SOLUSDT | 1h | 15/1.0/3.0/2 | 544 | 90.9 | 1.36 | 256 | 44.9% | 0.113 | 29.0 | 1.24 | -9.6 | -1.5 | 8.6 | 30.9 |  |
| SOLUSDT | 4h | 20/1.5/4.5/10 | 60 | 39.4 | 2.54 | 24 | 37.5% | 0.277 | 6.7 | 1.54 | -6.1 | 5.7 | 6.0 | 6.7 | OOS n<30 |
| SOLUSDT | 1d | 10/1.0/2.0/0 | 33 | 14.1 | 2.59 | 14 | 42.9% | 0.027 | 0.4 | 1.06 | -2.6 | 0.0 | 0.2 | 0.4 | OOS n<30 |
| XRPUSDT | 3m | 20/2.5/4.5/6 | 3184 | 250.8 | 1.14 | 1464 | 30.7% | -0.012 | -17.9 | 0.98 | -60.9 | -435.5 | -296.3 | -16.9 |  |
| XRPUSDT | 15m | 10/1.5/3.5/0 | 1788 | 123.8 | 1.13 | 831 | 34.3% | -0.044 | -36.5 | 0.92 | -66.5 | -194.5 | -141.9 | -32.9 |  |
| XRPUSDT | 1h | 35/1.0/4.0/2 | 284 | 57.0 | 1.38 | 117 | 37.6% | 0.162 | 19.0 | 1.30 | -11.8 | 1.9 | 7.6 | 19.0 |  |
| XRPUSDT | 4h | 10/1.0/3.5/4 | 155 | 38.6 | 1.53 | 77 | 37.7% | 0.059 | 4.6 | 1.11 | -14.2 | -0.6 | 1.1 | 4.6 |  |
| XRPUSDT | 1d | 10/1.0/2.0/2 | 30 | 0.6 | 1.04 | 14 | 42.9% | 0.143 | 2.0 | 1.36 | -3.6 | 1.6 | 1.8 | 2.0 | OOS n<30 |

## 2. Fixed-default baseline (L=30, slMult=2.1, RR=3.5, bias=5; no optimization)

| coin | tf | L/sl/RR/bias | IS n | IS sumR | IS PF | OOS n | OOS WR | OOS avgR | OOS sumR(gross) | OOS PF | OOS maxDD | OOS net06 | OOS net04 | OOS TPfirst | flag |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| BTCUSDT | 3m | 30/2.1/3.5/5 | 4791 | 97.7 | 1.04 | 2053 | 36.6% | 0.009 | 18.9 | 1.02 | -52.3 | -1348.4 | -892.6 | 22.6 |  |
| BTCUSDT | 15m | 30/2.1/3.5/5 | 865 | 66.8 | 1.15 | 399 | 35.8% | -0.017 | -6.8 | 0.97 | -24.4 | -102.8 | -70.8 | -5.8 |  |
| BTCUSDT | 1h | 30/2.1/3.5/5 | 211 | 19.8 | 1.17 | 112 | 33.9% | -0.035 | -3.9 | 0.94 | -16.5 | -16.1 | -12.1 | -3.9 |  |
| BTCUSDT | 4h | 30/2.1/3.5/5 | 43 | 1.0 | 1.05 | 21 | 47.6% | 0.353 | 7.4 | 1.81 | -3.5 | 6.3 | 6.7 | 7.4 | OOS n<30 |
| BTCUSDT | 1d | 30/2.1/3.5/5 | 11 | -2.5 | 0.64 | 4 | 75.0% | 1.344 | 5.4 | 11.12 | -0.5 | 5.3 | 5.3 | 5.4 | OOS n<30 |
| ETHUSDT | 3m | 30/2.1/3.5/5 | 4918 | 114.7 | 1.04 | 2071 | 38.0% | 0.039 | 80.0 | 1.07 | -74.8 | -719.5 | -453.0 | 85.5 |  |
| ETHUSDT | 15m | 30/2.1/3.5/5 | 878 | 76.6 | 1.17 | 373 | 38.9% | 0.098 | 36.6 | 1.18 | -15.2 | -14.1 | 2.8 | 37.1 |  |
| ETHUSDT | 1h | 30/2.1/3.5/5 | 238 | 13.3 | 1.10 | 105 | 29.5% | -0.086 | -9.0 | 0.86 | -16.9 | -15.4 | -13.3 | -9.0 |  |
| ETHUSDT | 4h | 30/2.1/3.5/5 | 47 | 0.4 | 1.02 | 23 | 39.1% | 0.036 | 0.8 | 1.06 | -6.8 | 0.1 | 0.4 | 0.8 | OOS n<30 |
| ETHUSDT | 1d | 30/2.1/3.5/5 | 9 | 1.0 | 1.19 | 4 | 50.0% | 0.594 | 2.4 | 2.19 | -1.0 | 2.3 | 2.3 | 2.4 | OOS n<30 |
| SOLUSDT | 3m | 30/2.1/3.5/5 | 4733 | 143.9 | 1.06 | 2018 | 38.1% | 0.053 | 107.0 | 1.10 | -43.8 | -515.0 | -307.7 | 107.5 |  |
| SOLUSDT | 15m | 30/2.1/3.5/5 | 856 | 60.1 | 1.14 | 362 | 37.8% | 0.026 | 9.3 | 1.05 | -36.1 | -33.9 | -19.5 | 9.3 |  |
| SOLUSDT | 1h | 30/2.1/3.5/5 | 217 | 20.4 | 1.17 | 110 | 37.3% | -0.008 | -0.9 | 0.99 | -11.2 | -7.2 | -5.1 | -0.9 |  |
| SOLUSDT | 4h | 30/2.1/3.5/5 | 64 | 10.9 | 1.33 | 25 | 40.0% | 0.106 | 2.7 | 1.20 | -5.4 | 2.0 | 2.2 | 2.7 | OOS n<30 |
| SOLUSDT | 1d | 30/2.1/3.5/5 | 13 | -6.1 | 0.32 | 4 | 50.0% | 0.711 | 2.8 | 2.86 | -0.5 | 2.8 | 2.8 | 2.8 | OOS n<30 |
| XRPUSDT | 3m | 30/2.1/3.5/5 | 4748 | -16.7 | 0.99 | 1963 | 38.2% | 0.050 | 99.1 | 1.09 | -29.2 | -624.0 | -382.9 | 104.4 |  |
| XRPUSDT | 15m | 30/2.1/3.5/5 | 839 | -32.3 | 0.93 | 367 | 40.1% | 0.107 | 39.3 | 1.21 | -12.8 | -13.9 | 3.8 | 39.3 |  |
| XRPUSDT | 1h | 30/2.1/3.5/5 | 225 | 4.6 | 1.04 | 106 | 36.8% | 0.123 | 13.0 | 1.23 | -12.0 | 6.0 | 8.3 | 13.0 |  |
| XRPUSDT | 4h | 30/2.1/3.5/5 | 53 | 12.2 | 1.46 | 22 | 59.1% | 0.839 | 18.5 | 3.43 | -1.1 | 17.8 | 18.0 | 18.5 | OOS n<30 |
| XRPUSDT | 1d | 30/2.1/3.5/5 | 10 | -0.9 | 0.85 | 4 | 50.0% | 0.664 | 2.7 | 2.73 | -0.5 | 2.6 | 2.6 | 2.7 | OOS n<30 |

## 3. Vendor-methodology reproduction vs honest number -- BTC 15m

Quantifies the inflation from (in-sample + optimistic fills + zero costs) vs (out-of-sample + conservative fills + real fees), same cell.

| | Vendor methodology | Honest |
|---|---|---|
| Grid basis | full history (in-sample) | walk-forward OOS (last 30%) |
| Fills | TP-first (optimistic) | SL-first (conservative) |
| Fees | zero | gross shown; net06 also |
| Params | (10, 1.0, 4.0, 0) (best) | (10, 1.5, 4.0, 0) (frozen) |
| n | 4,157 | 819 |
| **sum R** | **+183.1** | **-31.1 gross / -294.6 net06** |
| WR | 34.7% | (see table 1) |
| PF | 1.077 | (see table 1) |

> The marketed **+183 R** becomes **-31 R gross** (and **-295 R** net of 0.06%/side fees) out-of-sample. Even the vendor's own best-case PF is 1.077.


## 4. Leads & observations (evidence, not verdicts)

- **Fee drag scales inversely with stop size.** Fee-in-R per trade: ~0.66-0.93 R on 3m, ~0.19-0.33 on 15m, ~0.09-0.15 on 1h, ~0.04-0.07 on 4h, ~0.02-0.04 on 1d. At Bitunix taker (0.06%/side) the high-frequency cells cannot overcome costs: e.g. BTC 3m OOS gross +159.8 R -> net06 -3,234 R.
- **Gross vs net divergence is the whole story on low TFs.** 9 of 20 cells are OOS gross-positive with n>=30; after 0.06%/side fees only **2** remain net-positive: ETHUSDT 4h (net06 +6.4, n=36), XRPUSDT 1h (net06 +1.9, n=117).
- Those two net-positive cells rest on modest n and a **single 30% OOS window (one regime slice)** -- leads worth a dedicated look, not established edge. At the lower 0.04%/side tier a few more (SOL 1h, XRP 4h) tip marginally positive.
- **All five 1d cells have OOS n<30** (BTCUSDT 1d n=15, ETHUSDT 1d n=11, SOLUSDT 1d n=14, XRPUSDT 1d n=14) -> flagged insufficient; no daily-timeframe conclusion is supported by this sample.
- **IS->OOS decay is common.** The optimizer repeatedly favours grid-edge params (L=10, bias=0); several strong IS cells go negative OOS (BTC 15m IS +130 -> OOS -31 gross). Consistent with limited robustness of the optimized parameters.
- **The un-optimized baseline is often *less bad* net-of-fees.** The WF optimizer gravitates to tight stops (slMult 1.0-1.5) that maximise GROSS sum-R but carry the highest fee-in-R; the fixed default (slMult=2.1, wider stop) has lower fee drag, so on several cells (e.g. BTC 3m net06 -1,348 vs WF -3,234; ETH 15m net04 +2.8) it survives fees better than the 'winner'. A lead that GROSS-sum-R is the wrong objective under taker fees.
- **Parked Bybit cross-venue pass** would run only on the cells that survive OOS here (the net-positive / gross-positive-with-n>=30 set), per your instruction.

## 5. Reproduce
`python run_study.py` (25s) -> results_full.csv + results_vendor.json; `python mk_report.py` -> this file. Engine: `optitrade_bt.py` (`python t_unit.py`).
