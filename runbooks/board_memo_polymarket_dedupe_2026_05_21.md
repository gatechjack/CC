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

---

## Addendum (added 2026-05-21 after initial submission)

Two findings from a read-only audit of unresolved `polymarket_arbitrage` positions, performed after the main memo was drafted. Neither changes the §4A request; both clarify scope.

### 1. In-flight stack overhang

There are ~99 stacked entries across 12 open `condition_id`s, resolving over the next ~10 days. That is ~1.8× the 54-entry resolved stack volume the main analysis covered. These positions will continue to flatter or damage reported PnL before the proposed cap can affect anything — **material uncertain PnL remains in-flight and direction is unknown.**

**Clarification to §4B:** the "≥50 clean (non-stacked) resolved trades" gate must mean **50 trades placed AFTER the cap ships**, not 50 resolutions that happen to include these in-flight stacked positions. Resolutions of pre-cap stacks are part of the same artifact §3 describes and do not count toward the clean sample.

### 2. Known limitation of the per-`condition_id` cap

The cap is necessary but not sufficient. It does NOT catch correlated-underlying stacks — cases where the strategy emits entries on multiple distinct `condition_id`s that are effectively one bet:

- **WTI cluster (5 `condition_id`s, 44 entries, $44 notional):** HIGH $110 / $115 / $120 NO + LOW $90 / $95 NO are all bets that May WTI stays within a $95–$110 band. The cap treats these as five independent markets.
- **Iran peace-deal cluster (2 `condition_id`s, 22 entries):** "US × Iran permanent peace deal by May 31" and "…by June 30" are the same event with two deadlines. The cap treats them as independent.

Flag as a known limitation to address in a follow-up (e.g. per-`series` or per-underlying cap, or correlation-aware sizing). **NOT a reason to block §4A** — the per-`condition_id` cap still eliminates the single-market 10× stacking pattern that drove the 05-20 loss. The Board should know the proposed cap is not a complete concentration solution.

---

## Approval — 2026-05-21

**§4A: APPROVED** by Board/Backtester 2026-05-21. Per-`condition_id` cap to be implemented as described — strategy-internal pre-emission check inside `agents/strategies/polymarket_arbitrage.py`. **Must NOT** modify `RiskAgent.evaluate()` or the risk gate. **Must NOT** touch `enabled` or `auto_execute`. Strategy remains paper-only. **Diff review by Board required BEFORE commit/deploy** — the work is approved, not the resulting code.

**§4B: ENDORSED.** Strategy continues paper-only to gather clean post-cap data. No Phase 3 live-execution work authorized. The ≥50-clean-post-cap-trade threshold is a **floor before edge is even evaluated**, not a trigger.

**Known-limitation follow-up (Addendum §2):** Underlying/series-level cap to address correlated-underlying stacks tracked as separate BACKLOG P2 follow-up. Not in scope for this approval.

**Post-cap clean-data tracker:** Count only trades placed AFTER the cap paper-deploys; report resolved n / WR / PnL by `llm_prob` bucket; flag when n hits 50. Do NOT characterize edge as established before n=50 regardless of interim PnL direction. Tracked as separate BACKLOG P1 entry; activates after cap ship.

---

## Closure — 2026-08-06

**Division `polymarket_arbitrage` CLOSED by Board decision, 2026-08-06.** Basis of record: the clean-data edge evaluation — `reports/2026-08-06_polymarket_arb_edge_eval/ASSESSMENT.md`.

**The §4B gate was reached and the strategy failed it — the premise is refuted, not merely unproven.** Clean cohort n=272 (well past the ≥50 floor): **+$6.30 over 272 trades (+$0.023/trade, WR 45.96%), statistically indistinguishable from zero** (t = 0.25, p ≈ 0.80; 95% CI on total PnL ≈ [−$43, +$56]). Decisive finding: the LLM signal is **worse-calibrated than the market it bets against** — Brier LLM **0.254** vs market-implied **0.185** vs coin-flip **0.250**. On the exact markets the strategy selects (|LLM − market| ≥ 10% divergence), the market price is the better estimator, so there is no edge mechanism — and more paper data would only tighten the CI around ~zero, not rescue a model worse than the market. No positive n≥30 category/band constitutes edge; the one coherent directional category signal is negative (geopolitics −$5.99). Full method, slices, and calibration table in the assessment.

**Action taken (2026-08-06), scope-limited per Board direction:** `config/strategies.yaml` → `polymarket_arbitrage.enabled: false`. This stops the scan loop and its per-cycle LLM/Anthropic spend within ≤30s via the mtime-triggered `_reload()` (no restart required).
- `auto_execute` **UNCHANGED** (`false`) — never flipped; there was never a basis for it.
- **No code changes.** Strategy logic, the per-`condition_id` cap, and the risk gate are untouched.
- **No credential change.** `ANTHROPIC_API_KEY` is a single shared key (also used live by `kalshi_llm_arbitrage`, `pmcc_robinhood`, the `risk` agent, `ceo`, and the research firm) — it was **NOT** revoked. `enabled: false` is the surgical action that stops only this strategy's spend.
- No other strategy or division touched. The separate `polymarket_copy_trading` division is out of scope and unaffected.

**Correlated-underlying follow-up (Addendum §2 / BACKLOG P2):** closed as obsolete-by-closure — a risk-control refinement with no edge left to protect (assessment §6).

**Reopening requires a NEW Board memo with a NEW thesis.** This closure is on the LLM-divergence arbitrage premise as evaluated; a materially different (non-LLM, or demonstrably better-calibrated) signal would be a new proposal, not a resumption of this division.
