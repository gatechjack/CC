# S5 Ladder — Breeden-Litzenberger consistency + density vs HAR-RV

**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; evidence only — no verdict.

Hourly ladder snapshots at window-open (disjoint range buckets; bucket YES mid = implied probability mass). Sum-to-one deviation is **arbitrageable** only if Σask<1 (buy-all lock) or Σbid>1 (sell-all lock); else it is **inside-spread** overround. Monotonicity = the survival p_above(X) non-increasing (density≥0), violations flagged tradeable vs inside-spread. Implied 1h return vol from the bucket density vs a trailing HAR-style realized-vol forecast at open.

### B-L consistency

| Asset | events | overround Σmid (p10/med/p90) | bounds viol | sum!=1 arb / inside | monotonicity trade / inside |
|---|---|---|---|---|---|
| BTC | 40 | 0.893 / 0.965 / 1.254 | 0 | 0 / 40 | 0 / 40 |
| ETH | 28 | 0.884 / 0.980 / 1.252 | 0 | 0 / 27 | 0 / 28 |
| SOL | 21 | 0.915 / 0.975 / 0.995 | 0 | 0 / 20 | 0 / 21 |
| XRP | 4 | 0.986 / 1.002 / 1.016 | 0 | 0 / 3 | 0 / 4 |

### Bucket density vs HAR-RV (implied 1h return vol / realized forecast)

| Asset | n events | vol ratio (p10 / median / p90) |
|---|---|---|
| BTC | 38 | 0.32 / 0.64 / 3.72 |
| ETH | 26 | 0.52 / 0.77 / 1.69 |
| SOL | 21 | 0.65 / 0.84 / 1.49 |
| XRP | 4 | 1.38 / 1.84 / 2.21 |

## Reading this (evidence, not verdict)

- **Overround Σmid > 1** is the ladder's total priced probability; the excess over 1 is the market's vig/spread. **Arbitrageable** sum/monotonicity violations (Σask<1 / Σbid>1 / tradeable monotonicity) are the only ones a taker could exploit; inside-spread ones are not.
- **Vol ratio > 1** ⇒ the ladder prices MORE 1h vol than recently realized (a variance risk premium); < 1 ⇒ less. This is a first HAR-RV read; a proper HAR fit (lagged day/week/month RV regression) is the obvious follow-up. Tail buckets are open — centers approximated by the median bucket width, so the implied σ is a lower bound if mass sits in the tails.
- The 15m efficiency result does NOT bind here: the ladder is a different instrument (full distribution, hourly). These are structural diagnostics, not an edge claim.

