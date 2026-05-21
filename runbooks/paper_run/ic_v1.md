# Runbook: Robinhood Joint Iron Condor v1 — Paper Run

**Scope.** This runbook is the operator playbook for the Iron Condor v1 paper run. Paper-mode-as-validation is the chosen path for this strategy: Backtester registration is permanently out of scope (see `planning/iron_condor_v1_plan.md` Section 6). This runbook governs the 30-day tuning checkpoint and the 90-day live-discussion readiness checkpoint.

**Archive.** `runbooks/paper_run/` is the archive directory for all paper-run runbooks. IC v1 is the first; future strategies that go to paper get their own file here.

**Start date.** 2026-05-21 (first prod-live scan window: 09:45–09:50 ET on a US market day). 30-day tuning checkpoint: 2026-06-20. 90-day live-discussion readiness checkpoint: 2026-08-19.

---

## Operator Daily Routine

Two surfaces cover the daily review:

1. **`/telemetry/iron_condor`** (web app) — live open ICs, session P&L, circuit-breaker status, recent combo lifecycle events. Check this at any point during market hours.
2. **`python -m trading_corp.scripts.ic_daily_digest`** — cron-able end-of-day digest covering combo activity, closed P&L, scan filter counters, slippage, and recent errors. Pipe to a daily file or review on stdout.

**Watch for each day:**

- Combos proposed today (scan fired, candidates found, passed risk)
- Combos approved / rejected by Board (web app approval cards)
- Fills (paper mode: synthetic; check for `combo_unfilled` audit events indicating the GFD expired)
- Slippage vs entry credit (compare `paper_combo_actual_vs_limit_slippage` audit field against `paper_simulation.per_leg_slippage_dollars: 0.03` baseline)
- Any circuit-breaker firings (`circuit_breaker_triggered` or `circuit_breaker_auto_repause` in audit log)
- Any `startup_catchup` events from overnight (strategy re-ran manage on startup and proposed exits on positions opened in a prior session)
- Any `agent_error` or `catastrophic_stop` audit events — these warrant same-day investigation

**Quiet days are expected.** Empty scan output is a valid outcome (IVR below 30, VIX above 30, macro event within 5 days, ex-div window, backwardated term structure). The `scan_telemetry` counters in the daily digest surface which filter is responsible. Do not interpret two or three quiet days in a row as a fault.

---

## 30-Day Checkpoint — Tuning Evaluation

**Purpose.** At 30 days the paper sample is large enough to evaluate the strategy's front-end filters (scan criteria and entry conditions) but not large enough to draw statistically reliable conclusions about back-end management parameters (profit target, hard stop, adjustment thresholds). The 30-day checkpoint is a tuning gate, not a live-migration gate.

### Prerequisite — State-consistency check (gates everything below)

Before evaluating any tuning signal, verify the state-consistency badge on `/telemetry/iron_condor` (Section 5 — `strategy_health`, comparing `agent_state.open_ics` count against the count of distinct open combos in the `position` table) has been green every day for 30 consecutive days.

- **One day of red is a real bug requiring investigation** — agent_state and the position table disagreeing means at least one combo write half-landed or a callback failed.
- **If the badge has been red across multiple days**, the strategy has been operating on stale state and any P&L data from that window is suspect. **Pause tuning evaluation, fix the consistency bug, restart the 30-day clock.**

This check gates the rest of the 30-day checkpoint. Tuning on inconsistent state is worse than not tuning.

### What to evaluate at 30 days

**1. Are filters too tight or too loose?**
Pull scan-filter counters from the telemetry CLI:
```
python -m trading_corp.scripts.ic_telemetry_cli scan --start <start_date> --end <today>
```
- Too many `ivr_below_30` rejections: the IVR threshold may be too high for the current regime. Compare against realized 30-day HV to check if the threshold is filtering out reasonable setups.
- Too many `term_structure_backwardated` rejections: check whether the `term_structure_max_diff: 0.05` is appropriate for the current vol surface.
- Zero proposals over 2+ consecutive weeks: investigate whether stacked filters are over-restrictive, or whether the market regime genuinely does not meet entry criteria.

**2. Is the bot trading often enough?**
Expected cadence: roughly one new IC per qualifying-IVR day across the 5-symbol universe. Realistic range: 1–8 new ICs per month. Rationale: 1/month is realistic in sustained low-IVR regimes; 8/month is realistic during volatility expansions where multiple symbols qualify simultaneously. The wide range avoids false-positive "bot not trading enough" alerts on routine quiet weeks. If the bot proposes nothing for more than 2 consecutive weeks, investigate scan logs for the dominant filter reason.

**3. Is slippage in expected range?**
Compare the `paper_combo_actual_vs_limit_slippage` field in audit events against the configured `per_leg_slippage_dollars: 0.03` ($0.12/combo aggregate across 4 legs). p90 ≤ $0.30/combo is the sanity-check ceiling on the simulated values.

Important framing — what this measurement is and is not:

- **During paper mode, fills are simulated with $0.03/leg configured slippage.** Observed paper slippage validates that the simulation produces sane numbers — it is not a measurement of real-world execution quality.
- **Real-fill slippage measurement only becomes available after live migration.** The p90 ≤ $0.30 ceiling applies to the simulated values during paper and is a sanity check, not a tuning signal.
- **The first real-broker fill in live mode is the actionable measurement.** If real-fill p90 materially exceeds the $0.12/combo configured simulation, the simulation parameters need updating before any further live deployment.

**4. Is the macro/ex-div halt logic firing correctly?**
Spot-check 3–5 audit rows with `macro_halt` or `ex_dividend_window` filter reasons against `config/macro_calendar.yaml` and `config/ex_dividend_calendar.yaml`. Both are manually maintained; confirm they are up to date.

**5. Are HITL approval cards rendering correctly?**
Manual smoke check: propose a paper IC (or simulate one in a dev environment), confirm the `/approvals/combos/{combo_id}` card shows all 4 legs with correct strikes, expiry, direction, and net limit price.

### What tuning IS appropriate at 30 days

- Filter thresholds: `min_ivr`, `min_ivp`, `term_structure_max_diff`
- Wing width per symbol (if fills are consistently far from limit)
- Scan time-of-day (`DEFAULT_SCAN_TIME_ET` in `_ic_orchestration.py`, or a config override)
- Telegram batch window (`telegram_batch_window_sec`)
- Telegram bypass tags (add/remove high-severity intent tags)

### What tuning IS NOT appropriate at 30 days

The sample size is too small to reliably evaluate back-end management parameters. Do not change at 30 days:

- `profit_target_pct` (50%)
- `hard_stop_credit_mult` (200%)
- `tested_delta_warn`, `tested_delta_adjust`, `tested_delta_close_side`
- Circuit-breaker thresholds (`consecutive_loss_pause`, `drawdown_pct_pause`)
- `max_concurrent`, `max_correlated`

The rule: **30 days tunes the front end; back-end parameters wait for 90+ days of data.**

### 30-day checkpoint output

Append a "30-day review" section to this runbook with:
- Date of the review
- Scan filter counter summary (dominant reasons)
- Proposal rate (combos proposed / market days in window)
- Slippage observations
- Tuning decisions made or explicitly deferred
- Any anomalies observed

No config changes without this written note. Code changes (other than filter threshold adjustments in strategies.yaml) require the standard Board memo per CLAUDE.md §4.

---

## 90-Day Checkpoint — Live-Discussion Readiness

**Purpose.** At 90 days the paper run has produced enough closed combos and lifecycle observations to evaluate whether the strategy is behaving as designed and whether a live-migration conversation is appropriate. Reaching this checkpoint authorizes that conversation; it does not authorize going live.

**Backtester registration is permanently out of scope. Paper-mode-as-validation is the chosen path. The 30-day and 90-day checkpoints are the validation milestones. "Ready to discuss live" also does not mean Backtester has approved this strategy — it never will, by design.**

### Graduation criteria

All of the following must be met before the live-migration conversation opens:

1. **Minimum closed combos: ≥ 30** fully closed ICs (all 4 legs closed, realized P&L settled). Rationale: at an expected 75–80% win rate, n=30 gives ±7.5 percentage points standard error — the floor for distinguishing a 75% win rate from a 65% win rate with reasonable confidence.

2. **Win rate ≥ 65%** over closed combos in the window. Rationale: literature expects 75–80% at 16-delta with 50% management; ≥65% is the floor where the strategy can be said to be working roughly as designed. A 60–65% observed win rate signals parameters/regime/execution issues that need addressing before any live discussion.

3. **Average realized P&L per combo is positive** over the 90-day window. Pull from the telemetry CLI:
   ```
   python -m trading_corp.scripts.ic_telemetry_cli pnl --start <start_date> --end <today>
   ```

4. **No circuit-breaker firings in the trailing 30 days** (the final month of the paper run).

5. **Lifecycle coverage — all five must have been observed at least once before live discussion is on the table:**
   - **≥1 Adjustment 1 executed end-to-end** — open → tested-side delta hits the adjustment threshold → adjustment proposed → HITL approved → 4-leg atomic combo fills → `on_combo_filled` increments `adjustment_count` → eventual close.
   - **≥1 21-DTE forced close** that fired correctly (proves the time-based exit path works).
   - **≥1 50% profit-target close** that fired correctly (proves the profit exit path works).
   - **≥1 combo unfilled at venue** (proves the unfilled audit path works without leaving state in a bad place).
   - **≥5 manual verifications of combo coalescing** in the web approval card (UX verification — operator confirms the approval card renders 4 legs as one card).

   If any of these haven't happened by day 90, extend the paper run until all five are observed. Most will happen organically; Adjustment 1 is the one most likely to require waiting longer if no tested-side event occurs in 90 days.

6. **Level 3 options approval confirmed** (Robinhood Joint account, brokerage-side). This is an external dependency. Verify before the 90-day mark, not on it. If approval is not in place at 90 days, the live-migration conversation is blocked regardless of paper performance.

### 90-day checkpoint output

Append a "90-day review" section to this runbook with:
- Date of the review
- Closed-combo count and win rate
- Average realized P&L per combo
- Circuit-breaker history (any firings, dates, resolution)
- Lifecycle coverage (which branches were observed)
- Level 3 approval status
- Board decision: open live-migration conversation / extend paper run / close strategy

---

## What "Ready to Discuss Live" Does Not Mean

Reaching the 90-day graduation criteria does NOT mean:

- **`auto_execute` flips to true.** HITL is on every action even in live mode. The `auto_execute_caps` block in `config/strategies.yaml` is pre-structured for a future earn-auto-execute conversation, but that conversation is separate from the live-migration conversation and requires its own CLAUDE.md §4 memo.
- **The strategy is profitable in real money.** Paper slippage is modeled ($0.03/leg), but Robinhood-side fill quirks (bid-ask spread at execution, partial fills on combo orders) are not captured. Paper P&L overstates real-world performance to an unknown degree.
- **The universe expands beyond the 5 ETFs.** Tier-3 individual stocks remain out of scope until a v1.5 conversation explicitly opens them.
- **Additional adjustment types are available.** Convert-to-butterfly and roll-out-in-time adjustments remain out of scope. Adjustment-1 (shift untested side) is the only adjustment in v1.
- **Backtester has approved this strategy.** By design, it never will. Paper-mode-as-validation is the chosen path per the Board decision documented in `planning/iron_condor_v1_plan.md` Section 6.

---

## Kill Switch

If anything looks structurally wrong — `catastrophic_stop` fires, repeated `agent_error` audit events on consecutive ticks, broker connect failures cascading (observe `broker_fallback_to_paper` audit events with `starting_equity=0.0`), or any other anomaly that suggests the strategy is in a bad state — the operator's action is:

1. Set `enabled: false` in `config/strategies.yaml` under `robinhood_joint_iron_condor`.
2. Restart `trading-corp.service` on the VM:
   ```
   systemctl restart trading-corp
   ```
   (If SSH is blocked, use `az vm run-command invoke` per the pattern in MEMORY's prod ops facts.)
3. Review the audit log before re-enabling. Use the daily digest script to surface recent errors:
   ```
   python -m trading_corp.scripts.ic_daily_digest --date <today>
   ```
4. Do not re-enable without understanding the root cause. Document the incident and the resolution in the next deploy_log.md entry.

The `catastrophic_stop` branch (branch 0) closes all open ICs automatically when session P&L exceeds 10% of account equity. If this fires in paper mode, treat it as a real signal — investigate whether the underlying conditions would have caused the same outcome in live trading before re-enabling.

---

## 30-Day Review

*(Append here when the 30-day checkpoint is reached.)*

---

## 90-Day Review

*(Append here when the 90-day checkpoint is reached.)*
