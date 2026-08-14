# CHECKPOINT 4 — MACE OQ-2 + 3-active + halt button: full-suite verification

**Branch:** `claude-2026-08-13b` @ base `b11af9b` (= origin/prod-live tip).
**Commits under test:** ee9cfd5 (OQ-2 code), 66cad59 (OQ-2 tests), 7300985 (halt
button), 3210a4a (halt tests), 80802a2 (config package + test updates), b10c59a
(Board memo + deploy_log draft).
**Gate:** baseline 88f/12e held (+3 intended joint-IC deltas → 91f/12e), **0 new
MACE failures**. Env: p2venv, `-p no:pytest_ethereum`,
`--continue-on-collection-errors` (baseline convention — the 3 research-module
collection errors are pre-existing, dating to the 2026-05-30 reports),
`scripts/run_capped.ps1` (procgov 25GB).

## 1. Full-suite result — GATE PASS

**3436 tests: 3333 passed / 91 failed / 12 errors** (484s, procgov 25GB cap,
exit 1 == failures-present as expected).

- Junit artifact: `C:\Users\AA Incorporado\cc\mace_oq2_phase4_junit.xml`
- Name-level diff vs baseline junit `cc\mace_golive_preflight_junit.xml`
  (the 2026-08-11 91f/12e go-live preflight), via `cc\_junit_diff_oq2.py`:
  **EMPTY in BOTH directions** — the failure set and the error set are
  IDENTICAL test-name-for-test-name to the go-live baseline. Not just the
  counts: the same 91 failures and the same 12 errors, nothing added,
  nothing masked.
- **New failures touching MACE: `[]` (zero).**
- Suite growth 3370 → 3436 = +66 tests, all from this build
  (test_mace_manager_window NEW, test_mace_halt_button NEW 13,
  test_mace_execution deadline additions, config/calendar additions).
- 91 = 88 pre-existing + 3 intended joint-IC deltas (table D), exactly as at
  go-live; 12 errors = the pre-existing research-module set incl. the 3
  collection errors.

## 2. Targeted MACE coverage (pre-full-suite, all green)

- 14-file targeted suite: **202 tests green** (config, strategy_entry,
  strategy_manage, execution, overflow_dup_entry, manager_window, halt_button,
  loops, web_wiring, risk_adapter, risk_chokepoint, breakers, exdiv,
  ex_dividend_calendar).
- The 9 remaining `test_mace_*` files: **77 tests green** (calendar,
  division_shell, domain_condor, import_boundary, ivr, rh_broker,
  robinhood_golden_payload, robinhood_twin_builder, sizing) — grep-verified
  non-consumers of the shipped yaml, then run anyway.
- Total MACE-adjacent: **279 green, 0 failures** across all 22 `test_mace_*`
  files + `test_ex_dividend_calendar.py`.

## 3. Per-test-name delta disposition (Board ruling 5)

### A. Inherited red at session start → now GREEN (3)

The prior session rewrote `config/mace.yaml` before the matching test edits
landed; these three were red on inheritance and are fixed by the 80802a2 test
updates:

| Test | Disposition |
|---|---|
| `test_mace_config.py::test_shipped_config_loads` | rewritten to assert the shipped 3-active universe (IBIT/XLE/GDX), band-max 260, weekly 1, rung_risk 0.10, deployment 0.95, entry 2×30, IBIT enabled+not-overflow_only, FXI fallback None, SPY disabled | 
| `test_mace_strategy_entry.py::test_capacity_skip` | 5 live rungs == shipped max_rungs 5 (was 4) |
| `test_mace_strategy_entry.py::test_weekly_budget_skip` | green after the helper's explicit both-ways enable (shipped-yaml flip no longer leaks into tmp configs) |

### B. Updated in 80802a2 for the new shipped yaml (would have broken; all green)

| Test | Disposition |
|---|---|
| `test_mace_config.py::test_overflow_only_in_universe_rejected` | IBIT is now IN the universe — test pins `overflow_only:true` via mutate to keep exercising the validator reject-path |
| `test_mace_config.py::test_universe_symbol_needs_enabled_block` | now disables GDX (SPY no longer in universe) |
| `test_mace_config.py::test_shipped_config_passes_exdiv_gate` | asserts XLE+GDX enabled+guarded, IBIT enabled+guard-off (the §4-memo deviation, pinned by test) |
| `test_mace_config.py::test_enabled_guard_{without_dates_fails,off_without_dates_ok,with_dates_ok}` | tmp-calendar helper now seeds XLE/GDX (the enabled+guarded set) |
| `test_mace_strategy_entry.py::test_credit_floor_skip` | comment-only: band cap 250→260 |
| `test_mace_strategy_entry.py::test_reserve_skip` | max_risk 47_400 pins the 0.95-deployment reserve math (47_400+200 > 47_500) |
| `test_mace_strategy_entry.py::test_fxi_fallback_width_viable_with_riskband` | `dataclasses.replace` pins FXI w2→w1-fallback (shipped FXI is w1 NO-fallback per validator; the fallback MECHANISM stays covered) |
| `test_mace_strategy_entry.py::test_overflow_inert_at_launch` | now builds a tmp SPY-only yaml (the shipped yaml is no longer overflow-inert by design) |
| `test_mace_strategy_entry.py::test_overflow_{routes_to_ibit_first,exempts_weekly_budget,does_not_reroute_to_entered_primary,excludes_forfeiting_symbol}` | `_cfg_overflow`/mutators: explicit both-ways enables + IBIT `overflow_only:true` + **`width_dollars: 2` pin** (fixtures build w2 chains; shipped w1 has no wings in them → silent `no_wing` drop from routing — found by the first targeted run, 3 failures, fixed with the pins) |
| `test_mace_overflow_dup_entry.py::test_router_still_routes_to_eligible_ibit` | same overflow_only + w2 pin (the plan's named breaking test) |
| `test_mace_overflow_dup_entry.py::{test_router_does_not_reroute_to_entered_primary,test_manager_places_once_when_second_symbol_forfeits}` | helper explicit both-ways enable (survives shipped-yaml flips) |
| `test_mace_manager_window.py::test_reserve_binds_after_first_fill_second_superseded` | pins `deployment_target_pct 0.80` (shipped 0.95 would fit USO and defeat the scenario) |
| `test_mace_execution.py` (module-level) | CFG pinned to the launch 5×60 ladder via `dataclasses.replace` — ladder-MECHANISM tests (walk-down/exhaustion/cancel-race) stay byte-identical; shipped 2×30 is asserted in test_mace_config. Zero assertion changes |
| `test_mace_web_wiring.py::test_mace_page_shows_config_hash_when_manager_present` | asserts IBIT/XLE/GDX rows render + retired SPY row still renders (template iterates all defined symbols) |
| `test_ex_dividend_calendar.py::test_production_yaml_loads_and_has_expected_universe` | counts: IWM 4→5 (excise), +XLE 2, +GDX 1, +FXI 2; +IWM 9/15 corrected-date sanity |
| `test_mace_exdiv.py::test_shipped_yaml_uso_ewz_inert_spy_live` | FXI removed from the inert list (it has confirmed dates now); +XLE 9/21, GDX 12/21, FXI 12/15 positive asserts |

### C. New tests this build (Phases 1-2, commits 66cad59/7300985/3210a4a)

- `test_mace_manager_window.py` — NEW: OQ-2 budget math, donation, window_skip,
  mid-ladder `window_budget`, cutoff precedence, mid-flight failure
  continuation, risk-reject on 2-of-3, reserve-binds-mid-eval,
  fill-at-cutoff, attempt-1 floor, N≤2 fallback equivalence.
- `test_mace_execution.py` additions — executor `deadline=` seam
  (cancel-404/unconfirmed-terminal re-run under deadline).
- `test_mace_halt_button.py` — NEW: 13-test matrix incl. halt-mid-round,
  per-attempt `operator_halt`, **manage-runs-while-halted** (Board-required),
  audit-before-state, fail-safe read, tri-state render.

### D. Expected non-MACE baseline deltas (intended, NOT regressions)

| Test | Disposition |
|---|---|
| `test_iron_condor_config` ×2 | joint-IC exclusivity disable (2026-08-11 go-live) — joint-IC migration workstream, open-item (e) |
| `test_paper_run_tooling::test_readiness_check_handles_db_path_override` | same workstream |

### Count reconciliation (Board-requested: 3 inherited reds vs the plan's 4 breaking tests)

Both numbers were undercounts, differently scoped. The plan's "4 breaking
tests" was a pre-build estimate naming 2 sites (test_shipped_config_loads,
test_router_still_routes_to_eligible_ibit). The "3 inherited reds" (table A)
were the subset already failing at session start — red because the prior
session rewrote mace.yaml before its test edits landed, not because of code.
The exhaustive consumer sweep this session (`grep MACE_YAML|load_mace_config|
mace.yaml` + `ex_dividend_calendar` across tests/) found the TRUE breaking set:
tables A+B above (17 named tests + 2 module-level fixture pins), of which 3
surfaced as live failures on the first targeted run (the IBIT w1/w2 fixture
mismatch) and the rest were caught pre-run. This table is authoritative.

### E. Mandated-matrix case → test-name map (CP4-acceptance closure)

All PASS in the targeted run AND inside the 3436 full suite (0 MACE failures):

| # | Mandated case | Test(s) |
|---|---|---|
| 1 | Window-overflow (symbol's window exhausted → audited skip, no run_entry) | `test_mace_manager_window.py::test_window_exhausted_symbol_skipped_with_audit`; executor-side `test_mace_execution.py::test_entry_window_budget_exhausted_stands_down_before_placing` + `::test_entry_window_budget_mid_ladder_stands_down_clean` |
| 2 | Ladder fails mid-flight, others proceed | `test_mace_manager_window.py::test_ladder_exception_on_second_symbol_third_still_runs` |
| 3 | Cancel-404 under the serialized model (deadline set, behavior identical) | `test_mace_execution.py::test_entry_cancel_error_with_deadline_proceeds_next_attempt` + `::test_entry_unconfirmed_with_deadline_behavior_identical` |
| 4 | RiskAgent reject on symbol 2 of 3 → S3 still runs | `test_mace_manager_window.py::test_risk_reject_standdown_on_second_symbol_third_still_runs`; executor-side `test_mace_execution.py::test_run_entry_risk_reject_never_places_clean_standdown` |
| 5 | Dup-entry regression under the serialized model | `test_mace_overflow_dup_entry.py::test_router_does_not_reroute_to_entered_primary` + `::test_manager_places_once_when_second_symbol_forfeits` (e2e, exactly-one-placement); 3-symbol-universe form `test_mace_strategy_entry.py::test_overflow_does_not_reroute_to_entered_primary` + `::test_overflow_excludes_forfeiting_symbol` |
| 6 | Deployment-cap (reserve) binding mid-eval | `test_mace_manager_window.py::test_reserve_binds_after_first_fill_second_superseded` (S1 fills → fresh load_all → S2 superseded at 0.95×E) |
| 7 | Fill on last attempt at cutoff (in-flight fill past deadline still books, fake-fill path) | `test_mace_execution.py::test_entry_cancel_race_fill_past_deadline_still_books`; attempt-1 floor `::test_entry_thin_budget_attempt_one_fires_and_books` |
| 8a | Halt latch mid-round halts next symbol + audits | `test_mace_halt_button.py::test_latch_mid_round_halts_next_symbol_with_audit` |
| 8b | Per-attempt halt → `operator_halt` clean stand-down | `test_mace_halt_button.py::test_executor_halt_stands_down_before_placing` + `::test_mid_ladder_halt_stands_down_after_confirmed_dead`; precedence `::test_cutoff_wins_reason_over_operator_halt` + `::test_operator_halt_wins_reason_over_window_budget` |
| 8c | Manage/exits run normally while halted (Board-required proof) | `test_mace_halt_button.py::test_manage_exits_still_run_while_halted` |
| 8d | Endpoints write audit BEFORE state | `test_mace_halt_button.py::test_endpoints_audit_before_state` |
| 8e | Tri-state renders (+ latch durability, honest latency) | `test_mace_halt_button.py::test_tri_state_renders_and_latch_is_durable` + `::test_full_page_includes_halt_pill` + `::test_halt_never_recalls_inflight_fill_honest_latency` |

Supporting fail-safe/round-start halt coverage: `test_latch_at_round_start_no_placements_evals_still_audited`, `test_latch_read_error_fails_safe_not_halted`, `test_cleared_latch_runs_normally`, `test_halt_routes_registered`.

## 4. Drift-base evidence (Board ruling 5)

`git log --oneline e113843..b11af9b` (the span from the Board-named base to the
actual worktree base):

```
b11af9b docs(deploy): 2026-08-13 PMCC UX-truthfulness deploy (effective_status tile==panel + LLM-free stored-roll Approve; b10a010 kalshi reconciled first)
87ca17a feat(pmcc): effective-status truthfulness — tile==panel + LLM-free roll Approve (Issues 1+2)
5896c46 docs(pmcc): UX-truthfulness investigation (read-only) — Issue 1 stored-roll Approve + Issue 2 tile/panel desync
7a72203 Merge commit 'b10a010' into prodlive-reconcile-b10a010-2026-08-13
b10a010 feat(kalshi_llm): un-starve resolver (epoch-scope) + Option-B distinct-market view + backfill
4a65a74 docs(kalshi_llm_arbitrage): 2026-08-01 open-book forward-risk read (read-only)
f6e5d66 docs(kalshi_arbitrage): 2026-08-01 forward-edge review (read-only)
```

`git diff --stat e113843..b11af9b -- trading_corp/mace config/mace*
trading_corp/web/mace* trading_corp/web/templates/mace*
trading_corp/web/templates/partials/mace* config/ex_dividend_calendar.yaml`
→ **EMPTY. Zero MACE-runtime touches in the span** — the 7 commits are
PMCC (2 code+2 docs) and kalshi_llm (1 code+2 docs) only. `e113843` remains
the last MACE-touching prod-live commit; `b11af9b` is a strict superset and
the correct drift-gate base.

## 5. Non-negotiable-gate status at CP4

- Full mandated test matrix GREEN: ✅ (window-overflow, mid-flight failure,
  cancel-404 under deadline, risk-reject 2/3, dup-entry regression at 3
  symbols, reserve mid-eval, fill-at-cutoff, halt-mid-eval all 5 sub-cases,
  N≤2 fallback)
- Chokepoint + fake-fill + fake-cancel proven under the serialized model: ✅
  (risk_chokepoint AST/funnel + execution guard tests, unmodified, green)
- Baseline 0 new MACE failures: ✅ **91f/12e, name-identical to the go-live
  baseline; MACE delta = zero** (§1)
- Remaining before deploy (Phase 5): intraday credit-floor shadow-eval + Board
  roster pick → drift-gate vs b11af9b → paste-runners → restart ≤13:00 ET →
  boot verify (GLD 0 rungs, SPY 2 rungs managed) → prod-live advance +
  deploy_log.
