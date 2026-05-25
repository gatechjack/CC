# Bitunix placement quietude — diagnostic (2026-05-25)

> **TL;DR.** 586 PREMIUM `bitunix_score_decided` rows post-2026-05-23 15:52 UTC → **0 paper trades.** Three stacked gates (PA `structure_alignment` → HTF `proximity_to_support` → trade-plan `fees_too_high_for_risk`) kill every fire. The audit trail is **not silent** — `pa_validation_decision` writes every rejection — but the kills happen upstream of `would_have_placed`, so the dashboard activity rail (which watches the placement-stage kinds) shows nothing. **The 5/23 deploy is coincident, not causal**: the decline trajectory (17 trades/day on 5/15 → 1 on 5/22 → 0 since) predates the deploy by ~7 days. Hypothesis (a) confirmed; (b) and (c) ruled out.

**Window:** 2026-05-23 15:52:00 UTC → 2026-05-25 ~14:00 UTC. ~46 h.
**Source:** read-only `sqlite3` against prod `/home/azureuser/trading_corp/data/trading_corp.db` via `az vm run-command invoke` + observer source read at `trading_corp/agents/divisions/bitunix_futures_observer.py`.
**Scope:** trace where 586 PREMIUM verdicts die. No code or config changes.

---

## A. Trail-go-silent point

`pa_validation_decision` (decision=reject, mode=enforce) is the **last** audit kind that fires for a post-deploy PREMIUM bitunix verdict before the trail goes quiet. The follow-up `bitunix_score_decided` row has `outcome=skipped_pa_validation`. No `htf_gate_decision`, `trade_plan_decision`, or `would_have_placed` fires.

Specific example (2026-05-23 18:39:01 UTC):
```
kind: pa_validation_decision
payload: {
  "trigger_signal": "otter_sell", "trigger_source": "lord_otter",
  "score_side": "sell", "score_tier": "PREMIUM",
  "decision": "reject", "mode": "enforce",
  "passed": ["volume_confirmation"],
  "failed": ["vwap_alignment", "structure_alignment"]
}
```

## B. Hypothesis verdict — (a) silently rejected downstream, audited

| Hypothesis | Status | Evidence |
|---|---|---|
| (a) Gate kills, audited | **CONFIRMED** | `pa_validation_decision` reject = 1598/1609 evals (99.3%). Audited per-rejection. |
| (b) Placing invisibly | **RULED OUT** | No `position_sl_update` rows, no `position` table inserts, no reconciler audit rows for bitunix_futures since 2026-05-22. |
| (c) Placement genuinely off | **RULED OUT** | `config/strategies.yaml` prod mtime = 2026-05-24 21:45:54 UTC (post-deploy YAML edit was the UI cleanup pass, unrelated). `bitunix_futures.enabled=true` confirmed. No flag toggle ~5/22. |

## C. Per-stage breakdown — where the 1793 non-zero-tier verdicts go

Post-deploy `bitunix_score_decided.outcome` distribution (n=1793):

| outcome | n | % |
|---|---:|---:|
| `skipped_pa_validation` | 1598 | 89.1% |
| `skipped_score` (cooldown / other early) | 184 | 10.3% |
| `skipped_trade_plan` | 6 | 0.3% |
| `skipped_htf_gate` | 5 | 0.3% |
| `placed` | **0** | 0.0% |

PA evaluations (n=1609): 1598 REJECT, 11 PASS. Of the 11 PA passes:
- 8 killed by `skipped_htf_gate` (proximity_to_support — BTC hugging $76.1-76.3K)
- 1 killed by `skipped_trade_plan` (`fees_too_high_for_risk`)
- 1 killed by `skipped_score` (concurrent cooldown)
- 1 — unexplained from outcome counts; would need per-row trace

PA reject composition (n=1598) — `structure_alignment` is the dominant kill:
- `[vwap, volume, structure]` all fail: 630
- `[vwap, structure]`: 290
- `[volume, structure]`: 225
- `[structure]` alone: 175
- `[vwap, volume]` (structure passes): 258

`structure_alignment` failures = 1320/1598 (82.6%). For sell-side, `structure_alignment` requires `lower_lows_4h` (4h-resampled lows trending down) — BTC has been in a $75K-$77K range since ~5/19, not establishing clean lower 4h lows.

## D. 5/22 known-good vs post-deploy trail diff

Trail for last successful trade `e6f437e3-80a1-49fd-a974-98d158c210f9` (sell, 2026-05-22T15:33:04+00:00, result=win):
```
pa_validation_decision (pass) → htf_gate_decision → trade_plan_decision → would_have_placed → bitunix_score_decided(outcome=placed)
```

Post-deploy PREMIUM trail:
```
pa_validation_decision (reject) → bitunix_score_decided(outcome=skipped_pa_validation) [END]
```

Missing post-deploy: `htf_gate_decision`, `trade_plan_decision`, `would_have_placed`. The 4 placement-stage kinds dropping out is what makes the dashboard activity rail show "silence" — but the upstream `pa_validation_decision` reject row IS written for every kill.

PA pass rate trajectory:
- 5/22: ~20/day
- 5/23 (post-deploy): 5/day
- 5/24: 8/day
→ **~60-70% drop in PA pass rate** coincident with the deploy date, but the structural cause (no clean 4h lower-lows) predates it.

## E. What changed ~5/22

| Date | Commit | Change | Causal? |
|---|---|---|---|
| 5/21 08:43 | `4f04fa6` | Funding rate ×100 scaling fix (`abs(funding) > 0.05`). Funding currently 0.002-0.007%, well below threshold. | No (funding never reaches threshold even after fix). |
| 5/23 11:32 | `6073480` | bias TTL 90→30 + observe-only flip detector. Doesn't touch PA, HTF, trade_plan, or placement. | **No** — decline started 5/16-5/19, before deploy. |
| 5/24 21:45 | (UI cleanup) | `config/strategies.yaml` prod mtime — UI cleanup pass `0a98bbf`, unrelated to bitunix scoring config. | No. |

Daily bitunix paper trade trajectory (5/15 → 5/25):
```
17 → 6 → (5/17 gap) → 2 → (5/19 gap) → 1 → 2 → 1 → 0 → 0 → 0
```
Decline starts ~5/16-5/17. **The deploy is coincident with the zero-trades floor, not the decline.**

## F. Root cause + proposed fix (diagnostic only — NOT applied)

**Root cause: market structure × PA strictness.** Three stacked gates, all working as designed, all becoming simultaneously hostile in current BTC conditions:

1. **PA `structure_alignment` (require_all=true)** — `lower_lows_4h` / `higher_highs_4h` not present in ranging $75-77K market. Kill rate 82.6% of PA rejects.
2. **HTF `proximity_to_support` (`proximity_block_pct=0.3`)** — BTC repeatedly within 0.3% of $76.1-76.3K support. Hard-zeros size multiplier on 73% (8/11) of PA passes.
3. **Trade-plan `fees_too_high_for_risk`** — at ATR ~$53 / BTC ~$76K, fee floor (2× round-trip = ~$136/unit) exceeds 1R TP2 distance (~$80). This was already documented at `reports/bitunix_confound_and_fee_floor_2026-05-20.md` Step 2; remains true.

**The deploy did not cause this.** The bias TTL change and flip-detection helper do not touch PA, HTF, or trade-plan logic. The 5/22 PA pass rate collapse is upstream of all 5/23 code changes.

**Proposed fixes — NOT APPLIED, all gated on backtest approval per CLAUDE.md §4 + PROJECT_CONTEXT.md §11:**
- Consider `require_all=true` → `min_validators_passed=2` for PA in ranging markets (would dramatically lift the 82.6% structure-kill)
- Consider `proximity_block_pct=0.3` → wider for HTF when BTC is in a known range
- Consider `tp2_r_default=1.0` → `2.0` to ensure 1R TP2 exceeds fee floor under low-ATR conditions

**Or accept that bitunix_futures should not be trading in this regime** — which is, after all, what the gates are saying. The paper-clock observation period ([[bitunix-paper-clock]]) needs to be re-scoped: a 60-day clock cannot accrue trades when the strategy is structurally inert against current market conditions.

## G. Implications

- **Close-on-opposite build remains correctly gated.** Detector preconditions cannot be met because no positions open. Building the ~250 LOC close-on-opposite logic against zero accruing rows is not justified.
- **Paper-clock memory `[[bitunix-paper-clock]]` needs re-scope.** The 60-day window 2026-05-20 → ~2026-07-19 is not accruing paper-trade observations — the strategy is gated-off-by-design against this BTC regime.
- **Audit-integrity status**: the trail is intact at `pa_validation_decision` granularity. The dashboard's `would_have_placed`-anchored activity rail is structurally blind to PA-stage rejections — that's a UI gap, not an integrity gap. **No kline-class silent loss.**
- **No emergency action.** All three gates are working as designed. The right move is a Board-level decision on whether to relax thresholds for the current regime, run a backtest before doing so, or accept the strategy's silence as the gates protecting against bad conditions.
