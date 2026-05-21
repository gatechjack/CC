# Board memo — Polymarket Arbitrage: position-stacking defect + scope reduction

**Re:** Position-stacking defect, revealed strategy performance, and proposed scope reduction
**Date:** 2026-05-21
**Approval gate:** CLAUDE.md § 4 (Backtester sign-off — signal-suppression / risk-cap changes; no `auto_execute` flip requested)
**Status:** Proposal for review. No strategy-logic or live-config changes have been made.

---

## TL;DR

A position-stacking defect — the strategy does not deduplicate by `condition_id` — caused it to place up to 10× correlated notional on single markets. This both flattered and damaged reported PnL, depending on which stacks resolved on which day. Removing the stacking artifact reveals that the underlying strategy has no demonstrated edge over the available sample.

We request approval for one correctness fix now, and propose deferring all strategy-edge filters until clean post-fix data exists. The strategy is and remains paper-only (`ReadOnlyBroker`, no `place_order`); we recommend it continue in paper-only mode with the fix to accumulate a clean sample, and that no Phase 3 live-execution work be approved until edge is demonstrated.

---

## 1. What happened

Reported performance over the full post window (2026-05-16 → 05-21, n=88) was **−$5.67**. Within that window an apparent "cliff" stood out: strong early days, then a −$22 single-day loss on 05-20 (the −$26.20 figure circulated earlier was the narrower 05-18 → 05-21 sub-window). Investigation established:

- Strategy code is unchanged since 2026-05-09. No logic regression; no LLM prompt or model change. Ruled out drift as a cause.
- The 05-20 loss was a concentration event. **20 of 22 losing trades that day were repeated entries on just two markets** (Arsenal PL win — 10× NO; Massie KY-04 — 10× YES), all resolving against the model the same day.
- Root cause: no `condition_id` deduplication. The strategy re-evaluated the same markets across the week and stacked notional on each.

---

## 2. The deeper finding

Removing all four identified stacks (two winning, two losing) from the 88-trade post window changes the picture entirely:

- The apparent "edge in extreme-confidence calls" was the two winning stacks. Stripped, the 0–20% bucket falls to −$2.73 and the 80–100% bucket collapses to n=1.
- The apparent "catastrophic middle" was the two losing stacks. Stripped, the middle band is only mildly negative.
- De-stacked, the strategy is **−$17.12 over 34 trades (~35% win rate)**.

The barbell calibration pattern that earlier analysis flagged was an artifact of four markets, not a property of the model's calibration.

---

## 3. Honest characterization of current performance

We have no evidence of positive edge and mild evidence of negative edge. The −$17.12 / 34-trade figure is directional, not conclusive — 34 trades is a small sample, and we are deliberately not over-claiming structural unprofitability from it. The correct statement is: **the strategy has not demonstrated edge, and the cleanest available data leans negative.**

**Anticipated effect of the fix:** once dedupe ships, reported PnL will look worse, because the flattering stack wins disappear. This is the truth surfacing, not a regression. The Board should expect this.

**Pre-window baseline:** the pre-cliff sample (+$1.52, n=6) was too small to characterize anything. That degenerate baseline contributed to the false confidence that surfaced in the initial cliff analysis — a six-trade prior is not a reference point, and we are not treating it as one.

---

## 4. Requested actions

### A. Approve now — per-`condition_id` position cap

Correctness and risk fix, justified independent of edge. Before emitting a `would_have_placed` audit, the strategy checks `agent_state` / `polymarket_round_trips` for open (unresolved) entries on the candidate `condition_id` and caps how many it will stack. This is a **strategy-internal pre-emission check** inside `agents/strategies/polymarket_arbitrage.py` — it does not modify `RiskAgent.evaluate()` or the risk gate logic.

**Retrospective effect, stated honestly:** the cap would have flattened all four stacks — preventing ~$20 of correlated losses on 05-20 and ~$31 of correlated wins on 05-16/17. Net retrospective PnL effect: **roughly +$11**. The fix's value is primarily forward-looking — it eliminates single-market concentration risk regardless of direction. (Consistent with §3: reported PnL will look worse after the fix because the flattering stack wins go away.)

### B. Recommended — continue paper-only with the fix; gate Phase 3 live-execution on demonstrated edge

The strategy is already paper-only and there is no live execution to pause. The actionable lever is `enabled: true/false` in `config/strategies.yaml` — setting it `false` stops the strategy from emitting `would_have_placed` audits at all. We recommend **against** that, because it would prevent accumulating the clean sample we are gating on.

Instead: keep the strategy running paper-only with the dedupe fix, and approve **no Phase 3 live-execution work** until ≥50 clean (non-stacked) resolved trades demonstrate a defensible edge. Continuing toward live execution is the action that requires justification — not the decision to keep gathering paper data.

---

## 5. Explicitly deferred (NOT requested today)

These were considered and are being deferred pending ≥50 clean resolved trades — not forgotten, not quietly dropped:

- **LLM-probability gate** (`[0.20, 0.80]` rejection): **Withdrawn.** The signal it relied on was the stacks. No statistically supportable case in clean data.
- **Category whitelist** (entertainment/health): **Withdrawn.** Both "winning" categories were stacks; health has zero non-stack trades.
- **Sports blacklist:** **Deferred.** Sports is 0/10 in clean data — the cleanest single category signal — but n=10 is the same small-sample inference that produced the gates we just retracted. Not proposing it today on that basis.

---

## 6. What we are NOT doing

No changes to `polymarket_arbitrage.py` logic, no `auto_execute` flip, no live-config changes (including the `enabled` flag) have been made in producing this memo. Item A is submitted for Backtester sign-off before any deploy.
