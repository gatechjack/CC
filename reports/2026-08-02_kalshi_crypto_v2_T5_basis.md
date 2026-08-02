# T5 Basis Characterization — Binance-move vs RTI-settle label noise

**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; evidence only — no verdict. Small-scope accounting.

Disagreement = the Binance close direction over the 15m window (open→close, 1m bars) differs from the RTI settlement direction (y). The RTI settle is a 60s-avg vs the open 60s-avg strike; the Binance side here is point close-to-close (a 60s-avg version would be marginally cleaner). **The directional-window disagreement rate is the label noise every directional 15m study carries.**

| Asset | n | overall disagree | **directional (|RTI move|>=0.05%)** | flat (<0.05%) |
|---|---|---|---|---|
| BTC | 6515 | 14.7% | **7.7%** (n=4607) | 31.7% (n=1908) |
| ETH | 6516 | 14.6% | **8.2%** (n=5028) | 36.3% (n=1488) |
| SOL | 6517 | 15.1% | **9.6%** (n=5286) | 38.9% (n=1231) |
| XRP | 6515 | 15.6% | **9.4%** (n=5098) | 37.8% (n=1417) |

### Disagreement by |Binance move| bucket

| Asset | <0.02% | 0.02-0.05% | 0.05-0.10% | 0.10-0.20% | >0.20% |
|---|---|---|---|---|---|
| BTC | 38.6% (n=782) | 27.3% (n=1073) | 14.7% (n=1472) | 6.3% (n=1714) | 2.7% (n=1471) |
| ETH | 43.4% (n=608) | 28.9% (n=876) | 16.7% (n=1308) | 8.5% (n=1673) | 3.7% (n=2047) |
| SOL | 44.7% (n=302) | 34.9% (n=713) | 23.9% (n=1160) | 10.0% (n=1634) | 3.2% (n=2529) |
| XRP | 43.8% (n=495) | 31.2% (n=763) | 19.2% (n=1210) | 10.8% (n=1775) | 2.9% (n=2136) |

## Reading this (evidence, not verdict)

- The **directional** column is the operative label noise: on windows with a real move (the ones every directional study conditions on), this fraction settled the OPPOSITE way on RTI vs Binance. Prior 15m results should be read as carrying ~this much proxy error in their labels.
- Disagreement should be HIGH in the flat / small-move buckets (near coin-flip, the proxy sign is noise) and DECAY as |move| grows (a large Binance move rarely settles the other way on RTI). A large-move disagreement that stays high would flag a real Binance↔RTI divergence.
- This measures direction agreement only; it does not quantify magnitude basis (the two indices can agree on sign but differ on the 60s-avg level). Sufficient for label-noise accounting on binary up/down studies.

