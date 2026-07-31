# GO-LIVE MATH (DRAFT) -- 1h micro-aligned wide-stop trend-cross

**DRAFT -- subject to revision if the adopted stream changes.** Adopted stream = 1h + micro-aligned gate (STUDY C best causal). Frozen STUDY B config. In-sample Binance-perp proxy (shares the live feed -> a LEAD, not OOS). GROSS primary; net06/net04 = 0.06%/0.04% per side. Counts only, no verdicts.

## R economics (per coin + pooled)

| coin | armed n | net06 | net04 | avgR gross | trades/mo | net06 R/mo | E[net06 R]@n=30 |
|---|--:|--:|--:|--:|--:|--:|--:|
| SOL | 164 | +35.0 | +36.7 | +0.244 | 3.4 | +0.73 | +6.41 |
| BTC | 164 | +25.7 | +29.2 | +0.220 | 3.4 | +0.54 | +4.71 |
| ETH | 188 | +0.4 | +3.3 | +0.047 | 3.9 | +0.01 | +0.07 |
| XRP | 129 | +18.1 | +19.7 | +0.178 | 2.7 | +0.38 | +4.21 |
| POOLED | 645 | +79.3 | +88.8 | +0.167 | 13.5 | +1.66 | +3.69 |

_Corpus span ~47.8 months. **ETH gated net06 = +0.4** (ungated was -16.8 in STUDY B -> ETH re-enters marginally positive when gated).

## Rolling 30-trade net06-R distribution (kill/keep envelope)

| coin | #windows | p5 | p50 | p95 | min | max | % windows <0 |
|---|--:|--:|--:|--:|--:|--:|--:|
| SOL | 135 | -6.8 | +9.0 | +17.1 | -7.2 | +21.1 | 15% |
| BTC | 135 | -15.8 | +3.7 | +21.6 | -19.9 | +28.4 | 38% |
| ETH | 159 | -6.4 | +0.7 | +5.0 | -7.6 | +8.9 | 30% |
| XRP | 100 | -7.5 | +1.1 | +25.1 | -11.3 | +29.1 | 34% |
| POOLED | 616 | -10.4 | +3.6 | +20.4 | -19.9 | +29.1 | 31% |

## Stop distance & implied leverage (per coin; 3*ATR stop as % of price)

| coin | stop% p50 | stop% p95 | max-safe lev @stop-p50 | max-safe lev @stop-p95 |
|---|--:|--:|--:|--:|
| SOL | 4.08% | 8.31% | 12.3x | 6.0x |
| BTC | 2.08% | 3.80% | 24.0x | 13.2x |
| ETH | 2.81% | 5.13% | 17.8x | 9.8x |
| XRP | 3.35% | 8.08% | 14.9x | 6.2x |

_max-safe leverage = the leverage at which liquidation sits 2x the stop distance away (lev = 50 / stop%%); @p95 uses the widest 5%% of stops (conservative bound). Isolated-margin first-order approximation (ignores funding/fees/maintenance-margin curve).

