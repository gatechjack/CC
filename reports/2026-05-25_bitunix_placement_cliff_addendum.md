# Bitunix placement cliff — addendum (2026-05-25)

> Companion to `reports/2026-05-25_bitunix_placement_quietude_diagnosis.md` (commit `ee6533d`). The prior report's final verdict ("genuine regime — no emergency action") stands. The path through was wrong: it framed PA `structure_alignment` as the dominant blocker without surfacing the **PA-redeem mechanism** (commit `72bbbe4`, 2026-05-17 03:53 UTC) that was Board-approved specifically to solve the PA-structure-block problem. With redeem in the picture, the cliff attribution shifts **downstream of PA** to the **trade-plan `fees_too_high_for_risk` gate × low-ATR regime**. The 5/23 deploy is **NOT causal**, verified mechanically (ledger-window shrink cannot reach trade_plan inputs) and empirically (BTC ATR compressed independently).

## What the first report missed

The first report cited 1598 `pa_validation_decision` REJECTs (89.1% of 1793 verdicts) as the dominant kill factor and concluded "PA `structure_alignment` is the blocker." This is mathematically true but architecturally incomplete: the Board explicitly addressed the same problem on 2026-05-17 with the **deferred-fire PA mechanism** (deploy log entry 2026-05-17 03:53 UTC, commit `72bbbe4`). A PA REJECT no longer ends the trade — the payload is cached in observer process memory and re-evaluated every 60s through the full pipeline until PA passes or score decays.

**The redeem is firing.** Audit counts since 5/17:
- `pa_validation_redeem`: 46 (most recent 2026-05-25T03:49:47 UTC)
- `pa_validation_expired`: 159

So PA passes happen — just on a delayed cadence. The kill point shifts downstream.

## Cliff attribution — per-stage daily breakdown (corrected)

| date | redeems | expireds | htf decisions | **trade_plan pass** | **trade_plan reject (fees_too_high)** | would_have_placed |
|---|---:|---:|---:|---:|---:|---:|
| 5/17 | 1 | 17 | 9 | — | 3 | 0 |
| 5/18 | 11 | 18 | 19 | — | 5 | 2 |
| 5/19 | 4 | 22 | 9 | — | 3 | 0 |
| 5/20 | 3 | 14 | 5 | **1** | 0 | 1 |
| 5/21 | 6 | 17 | 15 | **2** | 3 | 2 |
| 5/22 | 11 | 19 | 20 | **1** | 4 | 1 |
| **5/23 (deploy)** | 3 | 18 | 5 | **0** | 4 | 0 |
| 5/24 | 4 | 21 | 8 | **0** | 4 | 0 |
| 5/25 | 3 | 13 | 3 | **0** | 2 | 0 |

The cliff is at `trade_plan_decision.should_trade`: 4 passes pre-deploy across 5/20-5/22, zero on 5/23-5/25. Every post-deploy trade_plan reject has `skip_reason="fees_too_high_for_risk"`.

## Root cause — BTC ATR compression, not deploy regression

`trade_plan_decision` payload `inputs.atr_used` distribution per day (at trade_plan stage, from 3m bar cache):

| date | min ATR | avg ATR | max ATR | n |
|---|---:|---:|---:|---:|
| 5/18 | 68.7 | 87.8 | 131.2 | 5 |
| 5/19 | 64.0 | 67.5 | 71.8 | 3 |
| 5/20 | 119.3 | 119.3 | 119.3 | 1 |
| 5/21 | 49.9 | 81.7 | 125.8 | 5 |
| 5/22 | 47.2 | 68.4 | 105.4 | 5 |
| **5/23** | **36.7** | **44.9** | **50.0** | 4 |
| 5/24 | 46.8 | 50.3 | 54.7 | 4 |
| 5/25 | 65.1 | 66.0 | 67.0 | 2 |

**Pre-deploy max ATR reached $105-131 — enough to clear the fee floor. Post-deploy max ATR is $50-67 — never enough.** ATR compressed ~50% on 5/23. Combined with tighter swing-structure ranges (swing_high−swing_low ~$95-125 post-deploy vs $200-400 on 5/22), swing-based SL distances now fall below the fee floor.

### Why the fee floor blocks

Trade-plan math (from `strategies.yaml:1296-1318`):
- `tp1_target = max(0.5R, tp1_min_profit_multiplier × round_trip_fee × entry)`
- `tp1_min_profit_multiplier = 2.0`, round-trip fee ≈ 0.09% (0.04% taker × 2 + 0.005% slippage × 2)
- At BTC = $77K: fee_floor = 2.0 × 0.0009 × 77000 ≈ **$138**
- `tp2_r_default = 1.0` → TP2 distance = 1R = risk_per_unit
- Skip if `tp1_distance ≥ tp2_distance`, i.e., if `risk_per_unit < $138`

With ATR $65 and ATR-based SL (1.5 × ATR ≈ $97), risk = $97 → TP2 = $97 < $138 → **reject**. With swing-based SL on tight $95-125 swing ranges, risk also ends up ~$85-110 → **reject**. The trade-plan gate is correctly identifying that 1R does not pay the round-trip fee × 2.

## The 5/23 deploy is mechanically not the cause

Two side-effects of commit `6073480` were documented in the deploy log as "intended":
1. `bias_bull/bias_bear ttl_minutes` 90→30 — bias factors expire faster
2. `_max_ttl_minutes` (observer.py:478, 487) ceiling shrinks from 90→30, which shrinks `_load_live_alerts_in_window` lookback (observer.py:634)

**The trade_plan path does not consume the alert ledger.** `_build_proposal_v2` (observer.py:2035-2114) reads structural inputs (`swing_low`, `swing_high`, `resistance`, `support`) exclusively from `self.bar_cache.bars` (3m OHLCV bars) and `atr_3m` (separately computed). These are price/OHLCV data, independent of the signal ledger. The 90→30 ledger window shrink **cannot mechanically reach `inputs.atr_used`, `inputs.swing_low/high`, or any other trade_plan input.**

Marginal effect on scoring: the bias TTL shrink may have reduced the rate of redeems (10-11/day on 5/18/5/21/5/22 → 3-4/day on 5/23-5/25). However, this is a ~3× reduction in redeem volume, not the cause of zero placements — because of the 11 PA passes that DID survive post-deploy, the trade-plan fee floor would have rejected them under any redeem volume.

## Verdict

| Hypothesis | Status |
|---|---|
| (a) 5/23 deploy regressed placement via ledger-window starving confluence | **RULED OUT.** Trade_plan inputs (atr, swing, S/R) are bar-cache-derived, not ledger-derived. |
| (b) Different downstream cause unrelated to 5/23 | **PARTIALLY** — bias-TTL marginally reduced redeem rate, but cannot explain zero placements |
| (c) **Genuine regime: low ATR + tight swing structure × fee floor** | **CONFIRMED** |

## Operator's "real scalp setups missed" — reconciliation

The operator's lived observation that scalp setups are being missed is **consistent with** the gates rejecting them — but reflects a **tuning gap, not a bug**:

- The fee floor (`tp1_min_profit_multiplier = 2.0`) assumes a vol regime where 1R exceeds $138
- Current BTC vol regime ($46-67 ATR) puts 1R below the fee floor
- Chart-readable setups exist, but the math says the round-trip fee × 2 eats more than the expected reward
- Either the strategy is correctly declining unprofitable scalps, OR the parameters are too conservative for low-vol regimes

This is a **tuning decision for the Board**, not a deploy bug to fix. Three candidate parameter changes:
- `tp1_min_profit_multiplier`: 2.0 → 1.5 or 1.0
- `tp2_r_default`: 1.0 → 1.5 or 2.0
- `swing_max_lookback`: 30 → 60+ bars (find wider swing extremes)

All three are strategy parameter changes requiring **Backtester approval per CLAUDE.md §4 + PROJECT_CONTEXT.md §11**.

## Paper-clock implication (corrected)

The paper-clock memory `[[bitunix-paper-clock]]` is **regime-blocked, not config-blocked**. The first report's "config-blocked, fixable" framing in the alternative-hypothesis section is incorrect. The strategy is doing exactly what it was designed to do — declining trades whose expected reward doesn't pay the round-trip fee. The 60-day observation clock will accrue at the rate BTC volatility allows.

The Board's decision space:
1. **Wait for BTC vol to return** (the strategy will start trading again automatically when ATR + swing structure widen)
2. **Backtester-approved re-tune** of fee floor parameters for low-vol regimes
3. **Accept low paper-clock accrual rate** as the strategy's honest assessment of tradeability

## What this changes about prior diagnosis

The first report's section F ("Root cause + proposed fix") cited three stacked gates with PA `structure_alignment` as primary. With PA-redeem in the picture, the per-stage attribution corrects to:

| Stage | Pre-redeem framing (first report) | Post-redeem framing (corrected) |
|---|---|---|
| PA `structure_alignment` | "Dominant kill, 82.6% of rejects" | "Handled by PA-redeem (46 successful redeems since 5/17); not a placement blocker" |
| HTF proximity_to_support | "Secondary kill, 73% of PA passes hard-zeroed" | "Hard-zeros 36-79% of HTF decisions depending on day; not the cliff (sm=0.5/1.0 passes exist)" |
| Trade_plan fees_too_high_for_risk | "Tertiary, 1/11 PA passes" | **"Primary cliff post-5/23: 100% of trade_plan decisions reject. Caused by ATR compression to $46-67 vs $138 fee floor"** |

The first report's overall verdict (genuine regime, no emergency action, threshold change requires Backtester) **stands** — but the diagnostic path needed the redeem mechanism to be correctly attributed.

## What this rules out, definitively

- The 5/23 deploy (`6073480`) is non-causal for the placement cliff. Confirmed by:
  - Code: `_build_proposal_v2` does not read `_load_live_alerts_in_window`
  - Data: ATR compression on 5/23 was external (market vol), and the deploy did not change ATR computation
- The "HTF moved to 15m/30m" hypothesis is unsupported by config. HTF authority (1H/4H/1D, weights 0.5/0.3/0.2) and PA `structure_alignment` (4h) are deliberate PR-3c architecture, documented in `strategies.yaml:1062-1066`.
- The PA gate is not silently swallowing — it audits every rejection, and the redeem mechanism replays the trade until PA passes or score decays.
