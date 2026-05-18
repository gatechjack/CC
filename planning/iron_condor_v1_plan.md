# Iron Condor v1 — Design and Build Plan

Successor to `planning/we-will-be-working-async-lerdorf.md`, which was lost to a BSOD at 2026-05-17 20:51 UTC before commit. This is not a faithful reconstruction; it is a transcription of architectural decisions already shipped in code as of 2026-05-18.

**Date:** 2026-05-18

---

## Section 1 — What Shipped

**Test gate:** 271/271 IC test cases pass; boot smoke 9/9 passes.

### File inventory (untracked as of 2026-05-18)

**Planning (492 lines)**

| File | Lines | Role |
|------|-------|------|
| `planning/broker_multi_leg_interface_design.md` | 492 | Locks broker-layer ABC additions and PaperExecutionBroker simulator; resolves all 6 open items the lost lerdorf plan flagged |

**Code (12,881 lines total, ~5.9k tests)**

| File | Lines |
|------|-------|
| `trading_corp/agents/divisions/robinhood_joint.py` | 190 |
| `trading_corp/agents/strategies/robinhood_joint_iron_condor.py` | 1686 |
| `trading_corp/agents/strategies/_ic_orchestration.py` | 492 |
| `trading_corp/agents/ic_live_view.py` | 828 |
| `trading_corp/agents/ic_telemetry.py` | 492 |
| `trading_corp/comms/pending_combo_registry.py` | 178 |
| `trading_corp/comms/telegram_batcher.py` | 141 |
| `trading_corp/web/combo_approval_view.py` | 168 |
| `trading_corp/utils/iv.py` | 131 |
| `trading_corp/data/ex_dividend_calendar.py` | 201 |
| `trading_corp/scripts/ic_daily_digest.py` | 379 |
| `trading_corp/scripts/ic_paper_run_readiness.py` | 323 |
| `trading_corp/scripts/ic_telemetry_cli.py` | 283 |

**Config**

| File | Notes |
|------|-------|
| `config/ex_dividend_calendar.yaml` | 169 lines |
| `config/strategies.yaml:1617-1679` | `robinhood_joint_iron_condor:` block |
| `config/divisions.yaml:49-57` | `robinhood_joint` slug |

**Templates**

| File | Lines |
|------|-------|
| `trading_corp/web/templates/approval_combo_detail.html` | 85 |
| `trading_corp/web/templates/iron_condor_live.html` | 34 |
| `trading_corp/web/templates/partials/iron_condor_live_sections.html` | 245 |
| `trading_corp/web/templates/partials/iron_condor_static_sections.html` | 149 |

**Tests (271 cases, all pass)**

| File | Lines |
|------|-------|
| `tests/test_robinhood_joint_division.py` | 237 |
| `tests/test_robinhood_multi_leg.py` | 518 |
| `tests/test_iron_condor_config.py` | 228 |
| `tests/test_iron_condor_strategy.py` | 932 |
| `tests/test_ic_orchestration.py` | 532 |
| `tests/test_ic_live_view.py` | 651 |
| `tests/test_ic_telemetry.py` | 531 |
| `tests/test_combo_approval.py` | 486 |
| `tests/test_ex_dividend_calendar.py` | 270 |
| `tests/test_iv_rank.py` | 260 |
| `tests/test_paper_multi_leg.py` | 526 |
| `tests/test_paper_run_tooling.py` | 308 |
| `tests/test_place_combo.py` | 364 |
| `tests/test_telegram_batcher.py` | 182 |

### Note on three contradictions found in the parent session's priming

1. `config/divisions.yaml:56` DOES have `strategy: robinhood_joint_iron_condor`. An earlier priming note claimed it didn't. The on-disk state is authoritative.
2. `trading_corp/agents/strategies/_ta_helpers.py` is BitUnix infrastructure (re-exports from `bitunix_htf_regime`), not iron condor — though it appeared in one version of the IC file list. It is not an IC file and is not included above.
3. `planning/we-will-be-working-async-lerdorf.md` does not exist on disk — it was the BSOD victim. This plan is its successor.

### 14-step build sequence

**Step 1 — Broker multi-leg interface design**
`planning/broker_multi_leg_interface_design.md` (492 lines). Locked the broker-layer ABC additions (`place_multi_leg`, `get_option_greeks`) and PaperExecutionBroker simulator contract. Resolved 6 open design items: atomic POST semantics via `robin_stocks.order_option_spread`, mixed-position-effect support, leg dict shape, per-leg fill price fallback chain, RiskAgent unchanged (per-leg), and HITL coalescing deferred to step 12.

**Step 2 — IV rank / ATM IV utility**
`trading_corp/utils/iv.py` (131 lines). Provides `calc_iv_rank(history, current)` and `calc_atm_iv(chain, underlying_price)`. Used by the scan loop to gate on IVR ≥ 30 and IVP ≥ 50 before constructing a candidate.

**Step 3 — Ex-dividend calendar**
`trading_corp/data/ex_dividend_calendar.py` (201 lines) and `config/ex_dividend_calendar.yaml` (169 lines). Provides `ExDividendCalendar.is_within_window(symbol, date, trading_days=3)`. Used by branch 4 of the decision tree to close an IC when ex-div is within 3 trading days and the short call delta has risen above 0.25.

**Step 4 — Macro calendar reuse**
Reuses the existing `trading_corp/data/macro_calendar.py` and `config/macro_calendar.yaml`. The scan loop checks `MacroCalendar.has_high_impact_event_within(trading_days=5)` before proposing a new IC. No new files were added for this step.

**Step 5 — Broker ABC additions + Robinhood place_multi_leg + get_option_greeks**
`trading_corp/brokers/base.py:79-201` (abstract methods `place_multi_leg` and `get_option_greeks` on the `Broker` ABC) and `trading_corp/brokers/robinhood.py:726-859` (concrete implementation calling `robin_stocks.order_option_spread` as an atomic 4-leg POST). Other broker adapters keep the `NotImplementedError` default; they never receive multi-leg traffic.

**Step 6 — PaperExecutionBroker combo simulator**
`trading_corp/brokers/paper.py:245-356`. Implements `place_multi_leg` for paper mode: applies configured per-leg slippage (`paper_simulation.per_leg_slippage_dollars`), synthesises `FillEvent` objects, and records a signed combo cashflow for P&L tracking. The `gfd`-order expiry semantics and per-leg fill price fallback chain (legs[i].price → executions[0].price → order.limit_price) are implemented here.

**Step 7 — data_exec.place_combo**
`trading_corp/agents/data_exec.py:188-380`. Routes a list of `ProposedOrder` objects sharing a `combo_id` to `broker.place_multi_leg`, writes `combo_filled` audit events, persists position rows, and computes the signed cashflow for the combo. Dry-run mode synthesises fills and writes `dry_run_skip_combo` audit events rather than hitting the broker.

**Step 8 — Division shell**
`trading_corp/agents/divisions/robinhood_joint.py` (190 lines). Thin portfolio-manager wrapper: reads `config/divisions.yaml` for the `robinhood_joint` slug, holds account equity, and provides the interface that `main.py` binds to the IC strategy instance. No order-placement logic at this layer.

**Step 9 — Primary strategy module**
`trading_corp/agents/strategies/robinhood_joint_iron_condor.py` (1686 lines, PRIMARY). Contains `RobinhoodJointIronCondorAgent` with `scan()`, `manage()`, `on_combo_filled()`, and `startup_catchup()`. The full 10-branch decision tree, the `OptionBroker` protocol, the `_default_state()` shape, cadence constants (300/900/1800 s), and all sizing helpers live here. See Section 4 for the decision tree.

**Step 10 — Orchestration loops**
`trading_corp/agents/strategies/_ic_orchestration.py` (492 lines). Houses `run_signal_scanner_loop`, `run_position_manager_loop`, `propose_ic_combo`, and `dispatch_approved_ic_combo`. These are the two asyncio tasks that `main.py` spawns; `dispatch_approved_ic_combo` enforces the state-consistency contract (see Section 2).

**Step 11 — main.py wiring**
`trading_corp/main.py:1125-1213` — division + strategy + telegram_batcher + combo_registry + account_factory + two asyncio tasks (`ic-signal-scanner`, `ic-position-manager`). `trading_corp/main.py:1773-1799` — WebDeps wiring (`ic_division`, `ic_strategy`, `pending_combo_registry`).

**Step 12 — HITL combo coalescing**
`trading_corp/comms/pending_combo_registry.py` (178 lines) — in-process registry; `trading_corp/web/routes.py:1595-1704` — GET + POST `/approvals/combos/{combo_id}`; `trading_corp/web/combo_approval_view.py` (168 lines) — approval card renderer; `trading_corp/web/templates/approval_combo_detail.html` (85 lines) — template. One approval card per 4-leg combo; Board approves or rejects the entire combo atomically.

**Step 13 — Telemetry**
`trading_corp/agents/ic_telemetry.py` (492 lines) and `trading_corp/agents/ic_live_view.py` (828 lines) — backend query layer and live-view model; `trading_corp/scripts/ic_telemetry_cli.py` (283 lines), `trading_corp/scripts/ic_daily_digest.py` (379 lines), `trading_corp/scripts/ic_paper_run_readiness.py` (323 lines) — operator CLIs; `trading_corp/web/templates/iron_condor_live.html` (34 lines) and partials (245 + 149 lines) — dashboard pages at `/telemetry/iron_condor` (wired in `trading_corp/web/routes.py:1434-1530`).

**Step 14 — Paper-run runbook**
This plan's Section 8 (operational artifacts pointer) and `runbooks/paper_run/ic_v1.md` (new file, authored 2026-05-18). The runbook covers the operator daily routine, 30-day tuning checkpoint, 90-day live-discussion readiness checkpoint, and the kill switch.

---

## Section 2 — Architectural Decisions

### HITL on every action

`auto_execute: false` in `config/strategies.yaml` (line 1619). Every new IC open, close, partial close, and adjustment routes through `/approvals/combos/{combo_id}` in the web app before any order reaches the broker. This is unconditional for v1; the `auto_execute_caps` block in the yaml is pre-structured for a future earn-auto-execute conversation but is dormant while `auto_execute=false`.

CLAUDE.md §1 establishes HITL as the default for any new division. For IC specifically, the defined-risk structure means the worst case is deterministic and bounded — but that is an argument for not blocking on a Backtester (see Section 6), not an argument for removing HITL. HITL stays on every action even after a live-migration conversation (see `runbooks/paper_run/ic_v1.md`).

### RiskAgent unchanged — per-leg evaluation

The RiskAgent is not modified. Each leg of the iron condor is evaluated independently as a `ProposedOrder`. The strategy sizes each leg so that the most expensive single leg (the long wing, the highest absolute premium) passes the per-trade risk cap. Per-leg evaluation is enforced in `_ic_orchestration.propose_ic_combo`, which iterates over the combo and calls `risk_agent.evaluate(leg, account, strategy_state)` for each leg, aborting the entire combo if any leg is rejected.

This design avoids any schema change to `ProposedOrder` or `RiskAgent`. The escape hatch for strategy-specific data (combo_id, direction, net_limit_price, ratio_quantity) is `ProposedOrder.extra`, per the CLAUDE.md sharp-edges guidance.

### Multi-leg native via robin_stocks atomic 4-leg POST

`trading_corp/brokers/robinhood.py:726-859`. The implementation calls `robin_stocks.orders.order_option_spread` which issues a single POST to Robinhood's option orders endpoint with one `ref_id = str(uuid4())`. Robinhood's exchange-side combo engine fills all legs together or none. Mixed `position_effect` is supported at the leg level — meaning an adjustment (close 2 legs, open 2 new legs at shifted strikes) is a single atomic POST, not two sequenced combos. This was a meaningful design finding over the parent plan's default assumption of two-combo sequencing; it is documented in `planning/broker_multi_leg_interface_design.md`.

### Combo aggregation via combo_id in extra_json

No schema change. Every `ProposedOrder` in a combo carries `extra={"combo_id": <uuid>, "combo_direction": "credit"|"debit", "net_limit_price": float, ...}`. The `combo_id` is the grouping key used by `data_exec.place_combo` (validates that all legs share one id), by `pending_combo_registry` (one entry per combo_id), by the web approval route (one card per combo_id), and by the telemetry layer (P&L aggregation by combo_id). The position-row writeback on fill also carries combo_id so the audit log is queryable. This uses the `ProposedOrder.extra` → `extra_json` escape hatch explicitly per CLAUDE.md's "use extra_json before proposing a new column" rule.

### State-consistency contract via synchronous on_combo_filled

`_ic_orchestration.dispatch_approved_ic_combo` enforces the following chain synchronously: `data_exec.place_combo(combo, division=division)` → `strategy.on_combo_filled(combo_id, fills, intent)`. The state update (registering a new open IC, updating credit_at_entry, marking an IC as closed, updating circuit-breaker counters) happens inside the same code path as the action that triggered it. It is never deferred to the next `manage()` tick. If `place_combo` returns empty fills (`combo_unfilled`), `on_combo_filled` is not called and the `_pending` entry remains for the next manage tick.

### Dynamic Position Manager cadence

`trading_corp/agents/strategies/robinhood_joint_iron_condor.py:_compute_cadence`. The position manager sleep duration is determined by the most-stressed open IC:

- 300 s — any IC has |short delta| ≥ `tested_delta_adjust` (0.30)
- 900 s — any IC has |short delta| in the warn band [0.25, 0.30)
- 1800 s — all ICs healthy, or no positions open

The cadence is returned from `manage()` as a hint to `run_position_manager_loop` and re-computed on every tick.

### HITL approval coalescing by combo_id

`trading_corp/web/routes.py:1595-1704` and `trading_corp/web/combo_approval_view.py`. The `/approvals/combos/{combo_id}` GET route renders a single card showing all 4 legs of the combo (direction, strikes, expiry, net limit price, per-leg details). The POST route resolves the combo to Board-approved or Board-rejected, which calls `dispatch_approved_ic_combo` on approve or writes a `board_combo_rejected` audit event on reject. The existing single-leg `/approvals` flow is unchanged.

---

## Section 3 — Strategy Parameters

Canonical values are in `config/strategies.yaml:1617-1679` and `config/risk.yaml`. The table below is a summary reference; the yaml is authoritative.

| Parameter | Value | Notes |
|-----------|-------|-------|
| Universe | SPY, QQQ, IWM, GLD, TLT | Tier 1 + 2 ETFs only; no individual stocks in v1 |
| Target DTE at entry | 45 | 7-day tolerance for expiry selection |
| Short delta | 0.16 | Closest available strike |
| Min credit pct of width | 33% | Per vertical spread |
| IVR threshold | ≥ 30 | As percentage (0–100 scale) |
| IVP threshold | ≥ 50 | Sibling filter to IVR |
| Term structure max diff | 0.05 | Front-month minus 60-90 DTE ATM IV (absolute) |
| Wing widths | SPY 3 / QQQ 4 / IWM 2 / GLD 2 / TLT 2 | Dollar width per vertical |
| Profit target | 50% of credit at entry | Branch 1 |
| Force close DTE | 21 DTE | Branch 2 |
| Late-DTE force close | < 7 DTE | Branch 3 (gamma-risk override) |
| Hard stop multiplier | 200% of credit at entry | Branch 4.5 |
| Catastrophic stop | 10% of account equity session loss | Branch 0 (portfolio-level) |
| Tested delta (warn) | 0.25 | Branch 6 |
| Tested delta (adjust) | 0.30 | Branch 7 |
| Tested delta (close side) | 0.35 | Branch 9 |
| Max adjustments per IC | 1 | Branch 7 guard |
| Min DTE for adjustment | 14 | Branch 7 guard |
| Ex-div window | 3 trading days + short call > 0.25 | Branch 4 |
| Max concurrent ICs | 3 | Portfolio cap |
| Max correlated ICs | 2 | SPY/QQQ/IWM group |
| Max per-trade BP | 5% of equity | Per-trade risk cap |
| Circuit breaker | 3 consecutive losses → 5-day pause | |
| Circuit breaker | 15% drawdown → 5-day pause | |
| Paper slippage | $0.03 per leg | Simulation only |

---

## Section 4 — Decision Tree

Branch order is load-bearing — first match wins, evaluated per open IC except branch 0 which is portfolio-level. Source: `trading_corp/agents/strategies/robinhood_joint_iron_condor.py` module docstring (lines 15–41); the wording below mirrors that text.

**Branch 0 — Portfolio-level catastrophic stop.** Close ALL open ICs at once. Triggered when session P&L across all open ICs exceeds `catastrophic_stop_account_pct` (10%) of account equity. Evaluated once per manage tick before per-IC branches.

**Branch 1 — 50% profit target.** Close that IC. Triggered when the cost-to-close is ≤ 50% of credit_at_entry.

**Branch 2 — 21 DTE.** Close that IC. Triggered when DTE ≤ `force_close_dte` (21).

**Branch 3 — DTE < 7.** Close that IC. Gamma-risk override of all later branches. Triggered when DTE < `short_dte_force_close` (7).

**Branch 4 — Ex-dividend within 3 trading days AND short call delta > 0.25.** Close that IC. Put-side ex-div drop risk is not handled in v1: ETF dividends in the universe (SPY/QQQ/IWM/GLD/TLT) are <0.5% of price per ex-date, within normal daily noise. Re-evaluate if the universe expands to higher-yield names.

**Branch 4.5 — Per-position hard stop.** Close that IC. Triggered when combo P&L ≤ −200% × credit_at_entry. Defense in depth against IV-spike scenarios where both sides expand without either short crossing 0.35 delta.

**Branch 5 — Tested-side identification.** Not an action branch; output feeds branches 6–9. Determines which side (call or put) has the higher absolute delta, and whether that delta falls in the warn, adjust, or close-side band.

**Branch 6 — |tested_delta| ∈ [0.25, 0.30).** Log "warn"; no order. Cadence tightens to 15 min via the cadence-hint return tuple.

**Branch 7 — |tested_delta| ∈ [0.30, 0.35) AND DTE > 14 AND adjustment_count == 0 AND untested side has remaining mark > $0.10.** Adjustment 1: atomic 4-leg combo closing the untested side and opening a new untested side at delta 0.30, shifted toward the tested direction.

**Branch 8 — |tested_delta| ∈ [0.30, 0.35) AND (adjustment exhausted OR untested side dead OR DTE ≤ 14).** Close tested side only.

**Branch 9 — |tested_delta| ≥ 0.35 OR underlying through short strike.** Close tested side only. (The hard stop has already been evaluated at branch 4.5; reaching here means the combo is tested but not at the hard stop.)

---

## Section 5 — v1 Documented Simplifications

These are visible debts — properties of the shipped code that are intentional scope decisions for v1 and will need revisiting for v1.5 or live migration.

**1. `_preflight_with_size` is a no-op.**
`trading_corp/agents/strategies/robinhood_joint_iron_condor.py:1488-1505`. The BP-pct cap (`max_bp_pct: 0.40` in strategies.yaml) is plumbing only. The actual operating constraint is `max_concurrent × max_per_trade_pct` (3 × 5% = 15% worst-case deployed). The code comment reads: "Skip BP-pct enforcement here; the risk gate's per-trade cap is the load-bearing check. This is documented as a v1 simplification."

**2. No `cancel_combo_order`.**
Combos use `timeInForce="gfd"` (good-for-day) and expire at session close. There is no cancel path. An unfilled combo proposed in the morning is cleaned up at EOD by the GFD expiry; the pending_combo_registry entry times out and the strategy re-proposes on the next manage tick if conditions hold.

**3. `_pending` registry is in-process memory only.**
`trading_corp/comms/pending_combo_registry.py:20-23` (module docstring). Lost on restart. A missed approval means the strategy re-proposes on the next manage tick if conditions still hold. Persistent recovery is a post-v1 polish item.

**4. Per-leg fill prices on Robinhood combos are best-effort.**
Three fallbacks in priority order: `legs[i].price` → `executions[0].price` → `order.limit_price`. The combo-level net cashflow (computed by `data_exec.place_combo`) is authoritative for P&L. Per-leg fill attribution is informational.

**5. Half-close does not compute realized P&L on the half-close.**
Branches 8 and 9 close the tested side only. The realized P&L for that partial close is not computed until the remaining side closes. The `on_combo_filled` callback updates the IC state to reflect the partial close but does not settle P&L until the full close.

**6. BP-pct cap not enforced.**
See item 1. Repeated here for clarity: `max_bp_pct: 0.40` in strategies.yaml is labeled "v1.5 plumbing — not enforced today" in the yaml comment.

---

## Section 6 — Validation Approach

**Backtester is permanently out of scope for Iron Condor v1 per Board decision 2026-05-18. This is a deliberate scope decision, not a deferral.**

The CLAUDE.md §4 rule that "new strategies need Backtester approval" is exempted for IC v1. Rationale: the strategy's defined-risk structure means the long wings cap the maximum loss per contract at `(wing_width − net_credit) × 100` deterministically. The historical-volatility-regime validation a Backtester provides is load-bearing for strategies with unbounded or hard-to-model tail exposure; it is not load-bearing for a defined-risk structure where the worst case is a closed-form expression of the entered strikes. Paper-mode-as-validation is the chosen path.

**Paper-mode-as-validation milestones:**

- **≥30 days of paper data** is the gate for tuning decisions (see `runbooks/paper_run/ic_v1.md` Section 30-day checkpoint for what is and is not appropriate to tune at that milestone).
- **≥90 days of paper data** meeting the criteria in `runbooks/paper_run/ic_v1.md` Section 90-day checkpoint is the gate for opening a live-migration discussion. Reaching the 90-day checkpoint is not a decision to go live; it is authorization to have the conversation.

**Level 3 options approval prerequisite.** Joint-account Level 3 options approval (brokerage-side, operational) is a separate prerequisite before any live conversation. This is an external dependency with its own timeline; verify before the 90-day mark, not on it.

---

## Section 7 — Out of Scope for v1

The following are explicitly out of scope and should not be scaffolded, designed, or worked on until a v1.5 or live-migration conversation opens them:

- Convert-to-butterfly adjustment
- Roll-out-in-time adjustment
- Tier-3 individual stocks (universe is ETF-only: SPY/QQQ/IWM/GLD/TLT)
- Daily IV snapshot table
- FRED/BLS macro auto-fetch (manual `config/macro_calendar.yaml` only)
- VIX-scaled position sizing
- Fully-autonomous mode (`auto_execute: true`) — HITL on every action even after a live-migration conversation
- GTC closing orders (GFD only in v1)
- RiskAgent multi-leg / combo extension (per-leg evaluation only)
- Central scheduler (two asyncio tasks wired in main.py is the architecture)
- **Backtester registration — permanently out of scope by Board decision 2026-05-18**

---

## Section 8 — Operational Artifacts

- **`trading_corp/scripts/ic_paper_run_readiness.py`** — Pre-run wiring check: walks the step-1..13 build and verifies every load-bearing config/import/DB wiring point before the paper run starts; exit 0 = green, exit 1 = at least one blocking check failed.
- **`trading_corp/scripts/ic_daily_digest.py`** — Cron-able daily summary: combo activity, open ICs, closed P&L, scan filter counters, slippage, circuit-breaker status, recent errors; outputs markdown to stdout or file.
- **`trading_corp/scripts/ic_telemetry_cli.py`** — Interactive telemetry queries: combo P&L by date range, win rate by IVR bucket, adjustment outcome stats, scan filter counters, slippage distribution; plain-text tables or JSON.
- **`/telemetry/iron_condor` route** (`trading_corp/web/routes.py:1434-1530`) — Dashboard page surfacing live open ICs, session P&L, scan telemetry, and recent combo lifecycle events via the web app.
- **`runbooks/paper_run/ic_v1.md`** — Operator playbook for the 30-day and 90-day paper-run checkpoints; daily routine; kill switch; archive directory for future paper-run runbooks.
- **This file** (`planning/iron_condor_v1_plan.md`) — Design reference and architectural decision record.
