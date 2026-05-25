# Board memo — BitUnix fee-floor tuning decision (DRAFT, no changes proposed)

**Date:** 2026-05-25
**Status:** DRAFT for Board review. No code/config changes proposed or pre-staged. All candidate levers below require Backtester approval per CLAUDE.md §4 + PROJECT_CONTEXT.md §11 before any implementation work.
**Companion reports:** `reports/2026-05-25_bitunix_placement_quietude_diagnosis.md` (`ee6533d`), `reports/2026-05-25_bitunix_placement_cliff_addendum.md` (`95f31ef`), `reports/bitunix_confound_and_fee_floor_2026-05-20.md` (prior fee-floor diagnostic).

---

## 1. Honest finding — the floor is working, not failing

BitUnix futures placed zero paper trades 2026-05-23 → 2026-05-25 (last fire `e6f437e3`, 5/22 15:33 UTC, result=win). 100% of `trade_plan_decision` rows post-5/23 reject with `skip_reason="fees_too_high_for_risk"`. The cause is fully explained by external market conditions:

- BTC ATR(14, 3m) compressed ~50% on 5/23 (max ATR $105-131 pre → max $50-67 post)
- Swing structure tightened in parallel (swing-range $200-400 pre → $95-125 post)
- 1R falls below the 0.18%-of-entry fee floor (~$138 at BTC $77K)

The 2026-05-23 deploy (`6073480`, bias TTL 90→30 + observe-only flip detector) is mechanically non-causal: `_build_proposal_v2` reads `bar_cache.bars` (3m OHLCV) and `atr_3m`, NOT `_load_live_alerts_in_window`. The deploy's ledger-window shrink cannot reach trade_plan inputs. Confirmed by code-read + ATR-compression timing.

The fee floor is not over-killing legitimate edge — it is correctly reporting that swing-based SL placement in low-volatility BTC produces R below the round-trip cost threshold. This is the third independent confirmation of the same structural finding:

| date | report | conclusion |
|---|---|---|
| 2026-05-20 | `bitunix_confound_and_fee_floor_2026-05-20.md` §2.6 | "Fee floor is not over-killing legitimate edge. Real question is structural: tradeable edge after costs in low-vol BTC?" |
| 2026-05-25 | placement quietude diagnosis | "Three stacked gates working as designed" (corrected by addendum) |
| 2026-05-25 | placement cliff addendum | "Trade_plan fee floor × low-vol ATR is the primary cliff. Regime-blocked, not config-blocked." |

**The Board should NOT treat low fire-rate in low vol as a problem to tune away.** The strategy declining unprofitable scalps in this regime IS the system working.

## 2. Legitimate Board question (not the same as "we want more trades")

Is the current `tp1_min_profit_multiplier = 2.0` (TP1 must be ≥2× round-trip fee) the right floor, or is 2.0 conservative? This is a question the Backtester can answer **against outcomes** — net-of-cost expectancy of trades fired at lower multipliers in a mixed-regime corpus, not just fire-rate.

It is NOT the same question as "we want more trades to happen." Fire-rate alone is the wrong success metric: a multiplier change that doubles fire-rate while halving net-of-cost expectancy per trade is a worse strategy. The decision criterion must be **expected PnL per fire after fees, not fires per day**.

## 3. Explicitly out of scope — loosening to chase fire-rate

The session that produced this memo will NOT propose lowering `tp1_min_profit_multiplier` from 2.0 to 1.5 or 1.0 as a "fix" for the placement cliff. Reasons:

1. **No demonstrated edge.** The bitunix paper-track record on the v2 placement path is n=11 trade_plan_decisions with 2 fires through 5/19, both losses. The 5/22 fire was a win, but n=1. There is no body of paper data that says "if we lower the floor, the additional fires would be net-positive." A multiplier change without backtest evidence is gambling, not tuning.

2. **Low-vol regime is exactly when fee floors should bind harder, not less.** In a regime where 1R doesn't pay the round-trip fee × 2, lowering the multiplier means accepting trades whose expected reward is less than the cost of taking them. That's manufacturing marginal/negative-expectancy trades to satisfy a paper-clock observation rate, not capturing edge.

3. **The PA-redeem mechanism is the system's pre-Board-approved response to "valid setups blocked by gates."** It re-evaluates PA on each new bar until PA passes or score decays — by design, it accumulates the *correct* set of trades through patience, not by relaxing thresholds.

4. **CLAUDE.md § 1.** "Risk caps are deterministic Python; LLMs may narrate verdicts, they may not produce them." The fee floor is part of that determinism. A multiplier change without a Board-recorded justification + a backtest-validated alternative crosses the line from tuning to narrative-driven loosening.

**Recommended verdict on this lever: do not lower `tp1_min_profit_multiplier` to manufacture fires in low-vol BTC.**

## 4. The genuinely better lever — `tp_is_maker: true` (cost-side reduction, contingent)

The cost-side alternative to weakening the profit multiplier is to **reduce the actual fee burden** rather than tolerate worse-trade economics. From the 2026-05-20 confound report §2.5:

- Current: `entry_is_taker: true`, `tp_is_maker: false` (MVP — market entry, market exit)
- Round-trip cost: 2 × 0.0004 (taker) + 2 × 0.00005 (slippage) = **0.090%** of entry
- Fee floor: 2.0 × 0.090% = **0.180%** of entry
- BitUnix maker_pct = 0.00014 (3× cheaper than taker)

Flipping `tp_is_maker: false → true` (taker entry, maker exits) reduces round-trip cost to ~0.064% and the fee floor to ~0.128% of entry. Per the 5/20 sim, 5 of 9 then-skipped trade_plan decisions would have fired under the lower floor. Critically, **this is a real cost reduction, not a relaxation of the edge requirement** — the trades that fire after the change still must clear the (lower) floor, which still corresponds to a positive-expectancy threshold.

### Why this is contingent — the fill-rate prerequisite

Maker exits depend on price coming to your limit. Fill rate is not 100%. The 5/20 report's recommendation §4 explicitly listed this as a Phase 2 conversation:

> "Re-evaluate maker exits (`tp_is_maker: true`) as a Phase 2 conversation with a fill-rate model. Memo's MVP scope explicitly deferred this."

Required before the Board can sign off:
1. **Maker-fill-rate model** — historical fill rate for limit orders placed at the v2-plan's TP1/TP2/TP3 levels in BitUnix futures, by symbol and ATR regime. Without this, the fee-saving is theoretical.
2. **Slippage-equivalent simulation** — un-filled maker exits revert to taker exits at end-of-bar or at a stop-out, which can be MORE expensive than the original taker-exit baseline if the maker order doesn't fill before adverse motion. The model must capture this.
3. **Per-leg vs partial-fill semantics** — TP1 (25% qty) maker-filling but TP2 (50% qty) timing-out is the common case; the model must reproduce the v2 reconciler behavior on partial fills.

If the fill-rate model produces a credible "maker-exit fee savings net of un-fill cost ≥ 0.03% per round-trip," this lever is a strict Pareto improvement over loosening the profit multiplier: same edge requirement, lower realized cost.

**Recommended verdict on this lever: queue the fill-rate model as a Backtester deliverable. Do not flip `tp_is_maker` until the model returns a net-positive verdict.**

## 5. Other Backtester-gated levers (for completeness, not recommended without backtest)

Each below requires the same standard: improve **net-of-cost expectancy**, not just fire-rate.

### 5.1 `tp2_r_default: 1.0 → 1.5 or 2.0`

Currently TP2 is 1R. Raising it to 1.5R or 2R lifts the TP2 distance above the fee floor at lower ATR levels, allowing trades to fire. Tradeoffs:
- TP2 hit-rate drops (it's further from entry)
- TP1 still gets hit if any move develops, capturing ~25% of qty at 0.5R
- Net effect: more fires, lower per-fire TP2 conversion rate
- Backtest must show: expected PnL per fire (weighted by TP1/TP2/TP3 hit-rate probabilities) ≥ current. Pure fire-rate gain without conversion-rate maintenance is a loss.

### 5.2 `swing_max_lookback: 30 → 60+ bars`

Currently the swing lookback is 30 3m-bars (~90 min). Widening it to 60+ bars (~180+ min) finds wider swing extremes, producing larger swing-based SLs that may clear the fee floor more often. Tradeoffs:
- Wider absolute stops → larger absolute losses when hit
- Old swing extremes may be stale (price has moved through them; they don't reflect current structure)
- Conflict with the 30-min cooldown if the wider lookback finds extremes 60-90 min stale
- Backtest must show: dollar-loss-on-stop × loss-rate stays within risk budget while gaining enough fires to break-even

### 5.3 `max_stop_atr_mult: 2.5 → 4.0`

From 5/20 report §2.5: relaxing this cap lets 5 of 9 prior skips use their (wider) swing SLs instead of ATR-fallback. Same tradeoff as 5.2 — wider absolute stops, slower TP3 hits.

## 6. The genuinely correct option — wait for vol to return

The strategy is declining trades whose expected reward doesn't pay the round-trip fee × 2. When BTC ATR(14, 3m) returns to ≥~$90 (the 5/18 afternoon regime that produced fires per the 5/20 report §2.4), swing-based SLs clear the fee floor automatically and trades resume without any code/config change.

Tracking ATR regime is itself useful instrumentation: a public-facing audit kind or dashboard tile reporting "current 3m ATR vs fire-clearing threshold" would let the Board (and Claude) characterize "is the bitunix engine idle by design or by bug?" at a glance, without re-running this diagnosis from scratch each time.

**This is the option the session recommends.** The paper-clock window (2026-05-20 → ~2026-07-19) is 60 calendar days, not 60 trade-eligible days. It accrues at the rate BTC volatility allows. A clock that produces n=10 fires under honest gating is more diagnostic than a clock that produces n=40 fires under loosened gating — the first tells us whether the strategy has edge in the regimes it deigned to trade; the second tells us whether tuning manufactured fires.

## 7. Decision the Board is being asked to make (or not)

The Board can:

**The three options are not co-equal. They live on different risk axes — rank accordingly.**

(a) **Wait for vol to return** — no action. Strategy auto-resumes when BTC ATR(14, 3m) ≥ ~$90 sustained (≥ a full 4h window above threshold to avoid one-bar spikes triggering noisy re-entry). Paper-clock accrues at the rate the market allows. (Session recommendation.)

> **Dated revisit tripwire**: if ATR has not recovered to ≥ ~$90 by **2026-06-19 (paper-clock midpoint, day 30 of 60)**, the Board re-opens this memo for the narrower question — *"does the clock measure 60 calendar days, or 60 trade-eligible days?"* The wait itself is correct; the failure mode is an undated wait that drifts to expire 2026-07-19 with n≈0 and nobody having decided. Owning a revisit trigger is the Board's call to make now, not something to discover later.

(b) **Queue the `tp_is_maker` fill-rate model as a Backtester deliverable.** A net-positive verdict from the model would justify a Board-approved deploy of `tp_is_maker: true` independent of the placement cliff. This is a **strict-improvement** lever: it lowers real cost (0.180% → 0.128% fee floor) without changing trade selection. Every trade that fires under the new floor would also have fired under the old; the trades just get cheaper. Net-positive in every regime if the fill model supports it. **(b) ranks above (c) — different risk class.**

(c) **Open a separate backtest on `tp2_r_default` or `swing_max_lookback`** — but understand these are **trade-selection** changes, not cost reductions. They alter *which* trades qualify. This carries **overfit risk**: a relaxed `tp2_r_default = 1.5` will look net-positive in a backtest restricted to current low-vol BTC (where it's the only way to get fires), and may look strictly worse when the regime rotates. Same trap as adding signal factors at low n — local optimum on a stale window.
   - Decision criterion stays: "net PnL per fire, holding other gates fixed." That metric is overfit-resistant in a way that fire-rate and total-PnL are not. **Do not let it slip to fire-rate or total-PnL during Board discussion.**
   - Require the backtest corpus to cover at least one ATR-regime rotation (the 5/15-5/22 high-ATR window + the 5/23+ low-ATR window). A backtest that only sees low-vol BTC cannot tell you whether the change generalizes.

The Board should NOT:

- Lower `tp1_min_profit_multiplier` to chase fire-rate
- Loosen the fee floor "for the demo" or "to satisfy the paper-clock observation rate"
- Treat the current placement quietude as a bug to fix on the deploy timeline rather than a regime to wait through
- **Re-open this decision every time the paper-clock observation rate looks low.** Low fire-rate in low vol is the system working — confirmed three times now (5/20 confound report, 5/25 quietude diagnosis, 5/25 cliff addendum). The Board decides ONCE that it accepts regime-gated accrual as honest, and that decision sticks until something *new* shows up (a new diagnostic finding, a new cost-side lever, a regime that produces fires but bad outcomes). Monthly re-litigation of "are the gates too tight" is itself a failure mode — it converts "wait for vol" into a soft loosening pressure that compounds across review cycles.

## 8. What the session will not do

This session has touched **no thresholds, no config, no code**. The 2026-05-25 work product is:
- Two diagnostic reports (`ee6533d`, `95f31ef`)
- Two memory updates (`bitunix-paper-clock` regime-blocked framing + `pa-redeem-check-before-quietude-attribution` class learning)
- This memo (DRAFT, awaiting Board)

The close-on-opposite-PREMIUM build (~250 LOC, scoped per Vortex) remains correctly NOT built — its precondition (open paper position) is now structurally unmet because placement itself is regime-blocked.

The PA-redeem mechanism continues operating; nothing about this diagnosis changes its design.

The 60-day paper clock continues at its anchor (2026-05-20).

---

## 9. Board Decision (2026-05-25)

The operator IS the Board (no external routing). Decisions recorded directly here. Memo status: **DRAFT → DECIDED**.

### (a) Wait for vol to return — APPROVED

- Strategy auto-resumes on regime change. No code/config action required.
- **Tripwire**: revisit if BTC ATR(14, 3m) has not reached ~$90 sustained (≥ one 4h window above threshold) by the paper-clock midpoint **2026-06-19** (day 30 of 60).
- At the tripwire, the re-decision is narrow: *"does the 60-day clock measure elapsed time, or trade-eligible time?"* — NOT a re-litigation of gate tightness.
- Per the §7 meta-rule: **do not re-litigate gate-tightness on low fire-rate before the 2026-06-19 tripwire.** Low fire-rate in low vol is the system working; re-opening on observation-rate alone is itself a failure mode.

### (b) `tp_is_maker` fill-rate model — APPROVED as next bitunix build deliverable

- Strict-improvement cost lever (drops round-trip cost 0.090% → 0.064%, fee floor 0.180% → 0.128%). Net-positive in every regime if the fill model supports it.
- **Deliverable**: build a maker-fill-rate model — historical fill rate for limit orders placed at v2-plan TP1/TP2/TP3 levels, by symbol × ATR regime, including un-filled-revert-to-taker semantics and partial-fill behavior on the v2 reconciler.
- **Deploy `tp_is_maker: false → true` only on a net-positive model verdict** (maker-savings net of un-fill cost ≥ 0.03% per round-trip).
- The model itself is its own future session — **NOT started this session**.

### (c) `swing_max_lookback` change — BACKTEST APPROVED; parameter change NOT approved

- Run the backtest. Decision criterion locked: **net PnL per fire, holding all other gates fixed**, across a corpus that includes an ATR-regime rotation (the 5/15-5/22 high-ATR window + the 5/23-5/25 low-ATR window, at minimum).
- Corroborated by live evidence (2026-05-25 fee-floor verification): 3h range $407 ≈ 5/22 successful-fire swing range $394 — chart-readable structure exists outside the strategy's 90-min lookback. The operator's "setups exist" instinct is real at the 3h level.
- **Parameter change requires a separate Board decision after the backtest holds across regimes.** Overfit-risk per §5.2 remains — the parameter is approved-to-test, not approved-to-ship.
- Backtest is its own future session — **NOT started this session**.

### What was NOT approved

- **Lowering `tp1_min_profit_multiplier`** to chase fire-rate. Explicitly rejected per §3 and §7. The fee floor is working; lowering it manufactures negative-expectancy trades in a no-edge regime.
- **Treating current quietude as a deploy-timeline bug.** The 5/23 deploy is mechanically non-causal (verified twice — addendum `95f31ef` + 2026-05-25 live-ATR verification). Regime is the cause; wait it out.

### Observability filed separately

The 2026-05-25 live-ATR verification surfaced a **12-14h silent window** in the diagnostic feedback loop: `trade_plan_decision` only fires after PA passes, so there's no recent ATR-input ground-truth in the audit log when the strategy is most idle (exactly when an observability question gets asked). A `bitunix_atr_snapshot` audit kind (periodic, e.g. alongside the 60s redeem loop, payload = `{atr_3m, last_close, swing_low, swing_high, fee_floor_pct × entry, would_clear_floor: bool}`) would let future Claude or the Board read *"is the engine idle by design or by bug?"* without a live kline probe.

**Filed P2 MEDIUM in `BACKLOG.md` (current EOS snapshot).** Not urgent — current workflow works, just adds friction.

### Session-close confirmations

- **No threshold, config, or code changes made this session.** No edits to `tp1_min_profit_multiplier`, `tp_is_maker`, `tp2_r_default`, `swing_max_lookback`, `max_stop_atr_mult`, `proximity_block_pct`, or any other strategy parameter.
- **(b) and (c) are queued deliverables**, each its own future session — NOT started now. The maker fill-rate model and the swing backtest both have to be designed and run before any parameter flip.
- **Close-on-opposite build remains correctly NOT built** — its precondition (open paper position) is structurally unmet because placement itself is regime-blocked.
- **Paper-clock memory `[[bitunix-paper-clock]]`** already updated with regime-blocked framing; the 2026-06-19 tripwire is now load-bearing for the next bitunix session.
