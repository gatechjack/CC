# STUDY B item 4 -- construct-overlap check (counts only)

STUDY B (wide-stop trend-cross, Binance 1h) vs the SFP construct ARMED stream (RD-trend gate, the deployed arm), common corpus period. (a) position-time overlap, (b) same-day same-coin same-direction collisions, (c) daily-R correlation. Independent stream -> low overlap / low collisions / near-zero correlation.

| coin | B trades | C(RD) trades | (a) overlap% of B time | (b) collision% of B | (c) daily-R corr |
|---|--:|--:|--:|--:|--:|
| SOL | 205 | 175 | 2.4% | 3.4% | +0.00 |
| BTC | 216 | 154 | 5.2% | 5.6% | +0.02 |
| ETH | 237 | 151 | 6.0% | 4.2% | +0.01 |
| XRP | 186 | 149 | 5.0% | 3.8% | -0.03 |

**Pooled portfolio daily-R correlation (sum across coins per day, n_days=854):** -0.020

_Counts only. (a) is % of STUDY B position-time that coincides with a construct position on the same coin; (b) is % of STUDY B trades sharing a construct trade's day+direction; (c) is Pearson on aligned daily summed-R.

## Reproduce
`python study_b_overlap.py` -> STUDY_B_OVERLAP.md (needs construct_rd_trades.csv).
