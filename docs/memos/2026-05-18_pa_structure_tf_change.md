# Memo: PA validator `structure_alignment` — change reference timeframe from 4h to 1h; demote 4h to sizing multiplier

**Date:** 2026-05-18
**Author:** Drafted by Claude Code at Jack's request, 2026-05-18 session
**Status:** DRAFT — awaiting Board review
**Affects:** `bitunix_futures` division only (3m scalping)
**Approval gates per repo rules:**
- CLAUDE.md §4 — score-path change requires explicit Board approval
- PROJECT_CONTEXT.md §11 — strategy parameter change requires Backtester pass before deploy

---

## 1. Problem

The BitUnix Futures division (3m scalping) uses a PA validator gate with `require_all=true` over three checks: `vwap_alignment`, `volume_confirmation`, `structure_alignment`. The structure check compares the **last-completed 4h bucket's** high/low against the **prior completed 4h bucket's** high/low. `require_all=true` means any one failing validator blocks the trade.

Empirical observation across 2026-05-17 (~9.5h of post-Phase-1E-flip traffic, all paper-mode):

- **205 PA rejects** on STANDARD + PREMIUM tier score-wins → **100% reject rate**
- **11 deferred-fire signals expired** without redeeming; **0 redeemed**
- **0 of 11 expired waits crossed a 4h UTC boundary** (the 4h close events at 00/04/08/12/16/20 UTC). The longest wait was 4156s (~69 min) entirely inside the 12:00-16:00 bucket.
- Within each wait window: `vwap_alignment` passed throughout; `volume_confirmation` flipped pass/fail bar-to-bar (confirming the bar cache IS refreshing); `structure_alignment` **stuck failed across the full wait in every single case**.

Net effect: on 3m scalping timeframes, the 4h structure validator is mechanically frozen for the duration of any realistic wait window. The gate functions as a near-100% reject regardless of trade merit, because the in-progress 4h bucket — which contains real closed-3m-bar data and is non-look-ahead — is excluded from the HH/LL comparison.

Key file:line: `trading_corp/data/bitunix_price_context.py:143-165` (`higher_highs_lower_lows_4h`).

## 2. What the 4h reference was originally for

(*Section to be filled in by the Board if a prior backtest rationale exists.*) Best read from the code is that 4h structure stood in for "is the broader regime supportive of this direction?" — i.e., a multi-bar swing-context filter against scoring fires that line up with momentum but face a contrary 4h regime.

## 3. Why 4h is wrong-fit for a 3m scalp

- **4h is 80× the trade timeframe.** A typical scalp completes inside a single 4h bucket.
- **HH/LL state can only change at 4h boundary closes.** On a scalp horizon, that's effectively never.
- **`require_all=true` then ties every trade decision to the 4h regime,** not the 3m setup the strategy is actually trading.
- **The chart-vs-code mismatch is structural, not calibration.** Chart users see the in-progress 4h candle setting new highs / lows; the code sees only the last two completed 4h candles. The PA gate was correctly flagging hostile regimes when Jack chart-reviewed individual rejections (see `feedback_pa_gate_well_calibrated.md`) — this proposal does not contradict that. It changes the structure check's reference timeframe so the validator can resolve on the scalp's actual horizon, not the 4h horizon.

## 4. Proposal

### 4a. Primary `structure_alignment`: 4h → 1h

Add `higher_highs_1h` / `lower_lows_1h` to `PriceContext`, computed by resampling the existing 3m bar cache to 1h buckets using the same logic as `_resample_to_4h`. Same exclude-in-progress-bucket rule (kept for parity; can be revisited separately).

Change `_structure_alignment` in `bitunix_pa_validation.py`:

```python
def _structure_alignment(side: str, ctx: PriceContext) -> bool:
    if side == "buy":
        return bool(ctx.higher_highs_1h)
    if side == "sell":
        return bool(ctx.lower_lows_1h)
    return False
```

**Rationale:** 1h is 20× the 3m trade timeframe — a standard "next TF up for scalp context" ratio. 1h HH/LL state can flip multiple times per 4h window vs the current 0×, so wait windows will see real validator updates. 15m was considered and rejected as a first move: 16× more sensitive than 1h, likely noisy without signal. Could revisit if 1h backtest underperforms.

### 4b. Demote 4h alignment to a sizing multiplier

Compute `pa_4h_aligned: bool` on the side of the proposed trade. When True, multiply position size by `pa_4h_size_multiplier` (default 1.25); when False, multiplier is 1.0. **Never blocks a trade.**

This preserves the "higher TF agreement = higher conviction" intuition: when 4h agrees with the 1h-confirmed setup, take a larger size. Slots cleanly alongside the existing HTF gate `size_multiplier`; same composition rule (multipliers chain).

### 4c. Config surface

`config/strategies.yaml`:

```yaml
bitunix_futures:
  pa_validation:
    structure_timeframe: "1h"      # was implicitly "4h"; one-line rollback
  pa_4h_size_bonus:
    enabled: true
    aligned_multiplier: 1.25
    misaligned_multiplier: 1.0
```

`structure_timeframe` is a string ("1h" | "4h") so rollback is a one-line YAML flip (+ service restart per BitUnix YAML hot-reload rules).

## 5. What this proposal is NOT

- **NOT loosening the PA gate.** The gate still requires `vwap_alignment AND volume_confirmation AND structure_alignment(1h)`. Rush/fall guards are untouched.
- **NOT removing 4h from the system.** 4h structure is still computed; it's promoted from binary gate to size multiplier.
- **NOT a hot-tunable.** BitUnix `.py` and YAML changes both require `systemctl restart trading-corp` (`feedback_bitunix_no_hot_reload.md`).

## 6. Backtester gate (per PROJECT_CONTEXT.md §11)

Required before deploy:

1. Replay the existing BitUnix signal ledger across both validator configs:
   - **Current:** 4h structure, `require_all=true`, no 4h-sizing-bonus
   - **Proposed:** 1h structure, `require_all=true`, 4h-as-size-multiplier
2. Decision criteria (any one failure → don't deploy):
   - Proposed config's per-fire expected R ≥ current config's per-fire expected R
   - Proposed config's fire count ≥ 5× current config's (the whole point — current gate fires ~never)
   - Proposed config's max-drawdown ≤ current × 1.2
3. If 1h fails any criterion, do NOT auto-promote to 15m. Open separate discussion (15m / structural-break detection / EMA-slope structure / other).

## 7. What observation tells us this change was wrong (post-deploy watch items)

Per CLAUDE.md §1 memo discipline — record what would falsify the change before flipping it. Watch the next 30 paper fires after deploy:

- **Win rate drops > 10pp** vs the pre-change baseline. → 1h gate is admitting trades 4h would have correctly filtered. Roll back.
- **5+ consecutive paper losers in the same direction inside an obvious counter-4h regime.** → Size multiplier alone isn't enough downweight in adverse 4h regimes; may need to re-promote 4h to a regime-conditional hard gate.
- **PA reject rate stays > 80% after 12h observation.** → The 4h TF wasn't the problem. Re-investigate.

## 8. Rollback

- `structure_timeframe: "1h" → "4h"` in `config/strategies.yaml` reverts the structure validator.
- `pa_4h_size_bonus.enabled: true → false` reverts the sizing multiplier.
- Both require service restart. ~30s rollback via `az vm run-command invoke`. Backup-tag the YAML before deploy.

## 9. Open questions for the Board

1. Stage the code first (forward-compat, 4h still active) and flip the YAML separately, or ship both in one PR?
2. Run 4h structure in shadow audit mode post-deploy for comparison data, or rely on the pre-deploy backtest?
3. Should the size multiplier be sticky (snapshot at trade open) or live (recomputed each tier/reconcile cycle)? Lean sticky for auditability.

---

## Appendix A — Session evidence

Source: 2026-05-18 session transcript Q1–Q7 verification + D1+D2+D4 deep dive.

**Q5 (score-engine activity, post 05:14 UTC flip):**

| tier | outcome | n |
|---|---|---|
| STANDARD | skipped_pa_validation | 178 |
| SKIP | skipped_score | 60 |
| PREMIUM | skipped_pa_validation | 27 |

**Q2a (`pa_validation_expired` reasons):** 11 score_decay, 0 opposite_side

**D1 wait windows (all 11):** zero crossed a 4h UTC boundary. Range: 0s instant-expiry → 4156s (69 min).

**D2 validator stability across 14:05–14:24 UTC wait window:**

| time | failed validators |
|---|---|
| 14:05:29 | `[structure_alignment]` |
| 14:06:29 | `[volume_confirmation, structure_alignment]` |
| 14:09:29 → 14:14:30 | `[structure_alignment]` |
| 14:15:31 → 14:17:31 | `[volume_confirmation, structure_alignment]` |
| 14:18:31 → 14:23:32 | `[structure_alignment]` |

VWAP never failed. Volume flipped. Structure stuck — confirming the bar cache IS refreshing (so D4 ruled out) but the 4h-frozen `structure_alignment` is the gating mechanism.

**D4 (bar cache health):**

```
LiveBarCache poll loop online: 40    (10 caches × 4 restarts; healthy)
LiveBarCache refresh failed:    0
poll loop cancelled:            0
compute_price_context failed:   0
```
