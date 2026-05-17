# Backtester result: PA validator structure-TF change (4h → 1h)

**Date:** 2026-05-18
**Author:** Drafted by Claude Code during the 2026-05-18 session
**Status:** Backtester result for [2026-05-18_pa_structure_tf_change.md](./2026-05-18_pa_structure_tf_change.md)
**Decision:** **DO NOT DEPLOY.** The proposed change fails the memo's own §6 fire-count criterion by a wide margin. Recommend Board reject or revisit with a different structural alternative.

---

## TL;DR

The 17-day backtest (2026-04-30 → 2026-05-17, 1,796 alerts, 4h vs 1h-structure-with-4h-bonus arms) shows the two configurations are **statistically equivalent on trade outcomes**, with the proposed arm slightly worse on raw counts:

| metric | 4h baseline | 1h + 4h-bonus | delta |
|---|---|---|---|
| Score-PASS evals | 606 | 623 | +17 |
| PA rejected | 551 (90.9%) | 575 (92.3%) | +24 |
| PA passed | 55 | 48 | **−7** |
| Fires (trades opened) | 26 | 23 | **−3** |
| Round-trips | 26 | 23 | −3 |
| Win rate | 50.0% | 52.2% | +2.2pp |
| Avg R / trade | +0.574 | +0.621 | +0.047 |
| **Total R** | **+14.92** | **+14.28** | **−0.64** |
| Return % | +0.80% | +0.55% | −0.25pp |
| Max DD | 0.10% | 0.10% | 0.00 |

The §6 decision criteria check:

| §6 criterion | Required | 4h | 1h | pass? |
|---|---|---|---|---|
| Per-fire E[R] ≥ baseline | yes | +0.574 | +0.621 | ✅ |
| Fire count ≥ 5× baseline | yes | 26 | 23 | ❌ (0.88×, far short) |
| Max DD ≤ baseline × 1.2 | yes | 0.10% | 0.10% | ✅ |

**Fire count criterion fails badly.** The memo's premise — that 1h structure would unlock significantly more fires — is not supported by the data.

## Where the proposal's premise broke

The proposal was motivated by the prod observation that **11 of 11 expired deferred-fire waits never crossed a 4h boundary**, so the `structure_alignment` validator was mechanically frozen across each wait. The inferred next step was: switch to 1h, get more responsiveness, more fires.

The backtest shows that's only half-true:

- **The 4h validator IS frozen across single-bucket windows.** That part of the original analysis is correct.
- **But over a longer horizon, the 4h `structure_alignment` aligns with the trade side about as often as the 1h does.** Looking at where the two arms disagreed on PA decisions:

  - 4h REJECTED but 1h PASSED: **3 alerts**
  - 4h PASSED but 1h REJECTED: **7 alerts**

  The proposal expected the first column to dominate. It doesn't. The 1h structure is busier (it flips often), which makes it slightly *more* likely to be unaligned at any random moment than the 4h structure.

## Why this happened

Two reinforcing effects:

1. **The 4h validator reflects macro regime.** When TV scores stack up on a side, they tend to do so during persistent moves that have already shifted the 4h structure in that direction. The "stuck failed" cases in the 9.5h prod observation are a low-percentage tail, not the modal behavior.
2. **The 1h validator adds noise.** Hourly HH/LL flips reflect intra-day chop more than directional structure. On 4 out of 7 lost trades, the 1h structure was unaligned even though the 4h structure was aligned with the side that ultimately won.

## What the data is NOT telling us

- **Whether the deferred-fire mechanism itself is meaningfully improved.** The backtest replays score-engine output but doesn't model the deferred-fire cache + 60s re-eval loop. The 11 prod expired-waits showed a real defect in observability and responsiveness on short timescales, but the 17-day window doesn't isolate that effect from cumulative trade performance.
- **Whether the 4h-as-size-bonus alone (without the structure-TF change) would help.** The two arms bundled both changes. To isolate the bonus, we'd need a 3rd arm: `4h structure + 4h-bonus=1.25x`. Not in this run.
- **Whether 15m or some structural-break detection would be better than either.** Not tested.

## Recommendations

1. **Reject the proposal as currently scoped.** §6 fire-count criterion fails; the proposed change does not meaningfully improve outcomes and slightly worsens total R.
2. **Don't conclude "the 4h validator is fine."** The prod-observed 11/11 freezing behavior IS a real defect on the deferred-fire mechanism's short-timescale operation. The backtest just shows that switching to 1h isn't the right fix.
3. **Consider one more backtest arm:** `4h structure + 4h-bonus=1.25× on top of 4h alignment` (i.e., the bonus alone, without the TF change). If that arm has positive R lift on the 26 baseline fires, the bonus is shippable independently.
4. **Alternative directions worth exploring** (separate memos, separate backtests):
   - Structural-break detection (Lopez-de-Prado style or simpler ATR-based break) instead of HH/LL.
   - `require_all=false` with `min_validators_passed=2` for STANDARD tier only — keeping `require_all=true` for PREMIUM. (Goes against the "don't loosen PA" guidance from `feedback_pa_gate_well_calibrated.md`, but if the backtest supports it on tier-conditional logic, the conversation is at least worth having.)
   - Re-examining the deferred-fire mechanism's TTL relative to ledger TTL: maybe the prod-observed expirations indicate the cache TTL should be longer, not the structure check more responsive.

## Methodology notes

- Backtest harness: `scripts/backtest_bitunix_confluence.py` extended for this session.
- Alert data: 1,796 webhook_received rows pulled from prod via `az vm run-command invoke` (SSH blocked from current network).
- OHLCV data: Coinbase BTC/USD 1m bars (24,077 candles). Known fidelity gap vs prod's BitUnix BTCUSDT live data — same gap that the original 4h-structure Phase 3.2 backtest used, so apples-to-apples for relative comparison.
- PA validator code: reused unchanged from `trading_corp/agents/strategies/bitunix_pa_validation.py`. For the 1h-arm, the harness swaps `higher_highs_1h` / `lower_lows_1h` into the 4h fields on a ctx copy before calling `evaluate_pa_validation`, so the validator's logic is identical between arms — only the structure data source differs.
- Both arms identical config except `--structure-tf` (`4h`|`1h`) and `--pa-4h-bonus` (`1.0`|`1.25`).
- TF filter (PR 3a `score_timeframes`) wired through: AlertEvent gains a `tf: str | None` field; the cache carries it; 1,696 of 1,796 alerts are 3m, the rest 15m / 30m / 4h / 1d, matching prod distribution.

## Reproducibility

```bash
# Arm A (4h baseline — matches current prod)
python scripts/backtest_bitunix_confluence.py \
  --start 2026-04-30 --end 2026-05-17 \
  --structure-tf 4h --pa-4h-bonus 1.0 \
  --arm-name 4h_baseline \
  --output-dir data/backtest_runs/bitunix_pa_arm_4h_baseline

# Arm B (1h + 4h-bonus — proposal)
python scripts/backtest_bitunix_confluence.py \
  --start 2026-04-30 --end 2026-05-17 \
  --structure-tf 1h --pa-4h-bonus 1.25 \
  --arm-name 1h_with_4h_bonus \
  --output-dir data/backtest_runs/bitunix_pa_arm_1h_with_4h_bonus
```

Alert cache + OHLCV cache are pre-populated under `data/historical_alerts/`.

Full per-arm output: `data/backtest_runs/bitunix_pa_arm_*/summary.md` + `ledger.json` + `trades.json`.
