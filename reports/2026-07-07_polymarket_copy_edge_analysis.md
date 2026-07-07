# Polymarket Copy-Trading — Resolution-Grounded Edge Analysis

**Date:** 2026-07-07
**Question:** Is there a tradeable edge in the `polymarket_copy_trading` division — enough to justify
standing up EU egress + funding the wallet to go live?
**Method:** Resolution-grounded copy P&L from `polymarket_round_trips` (prod, read-only). Every copy BUY
is on a market that resolves YES/NO; the resolver already pairs each BUY to its settlement
(`_compute_round_trip_row`: `pnl = qty*(1-price) if won else -qty*price`). This bypasses the broken
99.86% sell-pairing entirely — settlement outcome is the ground truth.
**Verdict:** **NO tradeable copy edge. Go-live / EU standup is NOT justified by the data.**

---

## The measurement and its one big caveat

The round-trip `entry_price` is the **whale's fill price**, not our post-lag price — the copy-trader
records `limit_price=activity.price` and "mirror[s] at the same price (paper-mode simplification)"
(`polymarket_copy_trader.py:454-469`). So this P&L is the **whale's directional edge run through our
resolved-market + 30%-drift filters, at ZERO assumed slippage.** It is an **optimistic upper bound** on
the real copy edge. The true number (entering at the post-lag market price) is lower — paper mode does
not store our fill price on taken trades, so real slippage is not directly measurable without a code
change or live fills. **This matters only if the optimistic number is strong. It isn't.**

---

## Headline (whole division; prod DB `data/trading_corp.db`)

| metric | value |
|---|---|
| round-trip rows | 6,274 |
| distinct positions (dedup partial fills) | 1,469 (**4.27× duplication**) |
| win rate | 53.6% |
| avg entry price (implied prob paid) | **0.559** |
| capital deployed (notional) | $10,647.83 |
| realized P&L | **+$57.70** |
| **blended dollar ROI** | **+0.54%** |

**+0.54% is statistically indistinguishable from zero** — and it's the *zero-slippage* number.
Decisive tell: **WR 53.6% < avg price 55.9%.** If markets were efficiently priced you'd win at the
implied rate (≈55.9%); we win *below* that. The copies carry, if anything, slightly **negative** gross
skill. Prediction markets are efficiently priced and the whale alpha does not survive into the copy.

## The +$57 is an artifact of the less-trustworthy path

| source | n | WR | notional | P&L | ROI |
|---|---|---|---|---|---|
| **settle-derived** (resolution ground truth, 90% of capital) | 5,726 | 55.7% | $9,424.84 | **−$61.77** | **−0.66%** |
| sell-paired (the 99.86%-skip contaminated path) | 548 | 31.9% | $1,222.99 | +$119.47 | +9.77% |

The trustworthy resolution-grounded majority is **negative (−0.66%)**. The whole division only looks
positive because a small, less-reliable sell-paired bucket adds +$119.

## It is not stable — May carried a losing streak (settle-only)

| month | n | notional | P&L | ROI |
|---|---|---|---|---|
| 2026-05 | 3,541 | $5,986 | +$817.47 | **+13.66%** |
| 2026-06 | 1,987 | $3,241 | −$862.95 | **−26.63%** |
| 2026-07 | 198 | $198 | −$16.30 | **−8.23%** |

The ~flat lifetime figure is **May's luck almost exactly cancelling June's losses.** The last two
months lose money at scale. This is a coin flip, not an edge.

## Per-whale is noise, not signal

Top whales by capital show enormous two-sided dispersion at tiny real sample sizes (real bets ≈ n/4.27):
- Big "winners": TimmyTurner123 +90.8% (but only **8 distinct markets**), superbeter007 +50.2% (41 mkts,
  n=47), ddssaaas6 +51.0% (37 mkts), AdrianCronauer +13.3% (**flagged `window_truncated` / unreliable**).
- Big losers of comparable size: damed21 **−100%** (1 market, total wipeout), jtwyslljy −82.5%,
  Talvez10 −51.2%, slimjoe −26.8%, scubacat −9.1%.

No whale has a large-sample, stable, positive track record in the copies. The current roster
(superbeter007, TimmyTurner123, AdrianCronauer + others, all manual `dashboard_button` pins from
mid-May) is a handful of concentrated bets, not a diversified edge.

**Baseline sanity:** `polymarket_arbitrage` paper = −4.24% over 388 RTs — confirms the method isn't
inflating anything.

---

## Why this matches the prior warning

The 2026-05/06 reports already flagged the tell: both **auto-paused whales were profitable on their own
realized P&L but our copies of them lost money.** This analysis generalizes that across the whole
division: **whale edge is real and dollar-verified (option-c reconciles to Polymarket to the dollar), but
it does not survive copying.** Efficient pricing + entry lag + the copy filters leave ~zero.

## Recommendation

**Do NOT stand up EU egress or fund the wallet.** The blocker to go-live was never infrastructure — the
live plumbing (E1 broker, wallet 119.98 USDC.e provisioned) is largely built. The blocker is that **there
is no edge to capture.** Spending on EU infra + real capital now is spending to run a live experiment
whose paper analog already reads flat-to-negative *before* slippage.

If the operator wants to keep the idea alive rather than shelve it, the only data-justified next steps are
cheap and paper-side:
1. **Log `current_price` on taken entries** (1-line change in `_emit_entry`) so real slippage becomes
   measurable — turns the optimistic bound into a true copy-edge number over the next few weeks.
2. **Re-screen the roster on the corrected option-c realized scorer** (Phase 1 is merged but never run on
   prod; the algo's top-12 share zero overlap with the current manual pins) and see whether a
   *properly-selected* roster produces a positive settle-derived ROI in a forward paper window.
   Only if that forward window is clearly positive does the live/EU question reopen.

**Data pulled read-only via Azure Run Command** (`pm_copy_edge.sh`, `pm_copy_edge2.sh`), prod
`polymarket_round_trips`, 2026-07-07.
