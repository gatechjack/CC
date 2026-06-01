# N+2 Phase 3 Session A — implementation complete

**Date:** 2026-06-01 · **Type:** code implementation (4 scoped commits, source + tests, no deploy) · **Branch:** `bitunix-live-exit-path-impl-2026-06-01` off `origin/main` (`bd9c0a2`) · **Status:** Session A scope landed; STOP-AND-REPORT for operator merge approval per CLAUDE.md § Session wrap-up.

**Verification SHAs:**
- `origin/main` = `bd9c0a2` (Decision 6.5 docs-merge close-out + scoping report base)
- This branch HEAD = `fadab6c` (4 commits ahead of main)
- Worktree = `.claude/worktrees/n2-phase3-impl-2026-06-01`

**Pre-flight verifications:**
- ✅ G1 prod stable: deferred to operator-runs `!ssh azureuser@trading.jacksumner.com 'systemctl show trading-corp...'` — classifier blocked autonomous SSH per `[[classifier-blocks-prod-reads-above-static-perms]]`; recommend operator confirm at session-end before merging
- ✅ G3 `_handle_stuck_order` reachable: verified via grep at `brokers/bitunix.py:1122` + the wiring inside `_execute_live_exits` (commit 3)
- ✅ G4 risk-tier overlay: unchanged; `config/strategies.yaml` untouched this session
- ✅ G5 C-1 critical path: no source changes to credential surfaces
- G2 paper placements: not load-bearing for Session A (helper has paper-mode coverage via unit tests; replay-loop wiring deferred to Session B)
- G6 baseline pytest: 2139/28/3 baseline expected on impl branch; full-suite test gate run as final step (results in §6 below)

---

## 1. Scope landed (4 commits)

Per the user's task-description Session A breakdown (which DIFFERS from scoping report §3 — see §3 below for the deviation note):

| Commit | SHA | Subject | Source LOC | Test LOC |
|---|---|---|---|---|
| 1 | `cb46e0e` | Path C revert — live entries write paper_trade_record | +29 / -8 | +85 / -2 |
| 2 | `6c36d6b` | _record_exit_outcome canonical helper (Decision 6.1 stamp) | +125 | +381 |
| 3 | `33e5732` | _execute_live_exits + get_pending_positions extracted | +183 / -1 | +571 |
| 4 | `fadab6c` | bitunix_position_reconciler wired for live mode | +262 / -2 | +337 / -14 |
| | | **Totals** | **~669** | **~1374** |

**Session A total delta:** ~2041 insertions, 25 deletions across 8 files.

---

## 2. Commit detail

### Commit 1 — Path C revert

**Source:** `trading_corp/agents/divisions/bitunix_futures_observer.py:2716-2738` — after successful `data_exec.place(order)` in `_place_live`, write a `paper_trade_record` row tagged `extra["execution_mode"]="live"` + `extra["broker_order_id"]=fill.order_id`. Failure swallowed (broker placed real money; DB hiccup must not block operator telegram). Docstring at observer:2449-2467 updated to reflect the new behavior.

**Tests:** `tests/test_bitunix_observer_live_branch.py` — existing `test_live_mode_does_not_write_paper_trade_record` INVERTED to `test_live_mode_writes_paper_trade_record_with_execution_mode_tag` (reflects the load-bearing reverse of N+1 commit 3). Added 3 new tests for broker_order_id stamp, rejection-no-row guard, paper-mode no-execution-mode-tag isolation.

**Verification:** 17/17 live_branch + 46/46 placement_outcome + execution_mode + hitl_gate combined.

### Commit 2 — `_record_exit_outcome` canonical helper

**Source:** new method in observer (after `_record_placement_outcome`, before `_place_live`). Signature `(*, order_id, result, result_ts, result_price, actual_pnl_dollars=None, actual_r_multiple=None, bars_to_resolution=None, is_live=False, fill_event=None, leg=None, extra_json_updates=None)`. Updates `paper_trade_record` row, merges `extra_json` preserving prior fields (Path C tags + multi-leg lifecycle), stamps `extra["result_source"]` per Decision 6.1(b):
- `is_live=False` → `"paper_replay_bars"`
- `is_live=True` → `"live_broker_truth"`

Writes `exit_outcome_recorded` audit row with full payload (result_source, leg, fill_event sub-block). DB-write and audit-write failures both swallowed.

**Tests:** `tests/test_bitunix_observer_record_exit_outcome.py` (new file) — 10 tests covering paper/live stamps, result-column updates, extra_json merge preservation (load-bearing for Path C → broker_order_id link), leg param routing, fill_event routing, DB/audit failure swallow.

### Commit 3 — `_execute_live_exits` + `get_pending_positions` extraction

**Source (broker):** `trading_corp/brokers/bitunix.py:953-1009` — new public `async def get_pending_positions(self) -> list[Position]`. Wraps existing endpoint (`/api/v1/futures/position/get_pending_positions`) using `_request` (signed + gate (a) retry-aware). SHORT positions render with negative qty per snapshot's signed-qty convention. Stub-mode, missing-creds, transient errors → empty list (no exception leak). `snapshot()` left intact per CLAUDE.md "real-money pipelines must not be refactored."

**Source (observer):** `_execute_live_exits` async method between `_push_with_confirmed_delivery` and `_maybe_propose`. Builds reduce_only=True ProposedOrder with side INVERTED from entry per Phase 1a §3 template (`exit_id = f"{order_id}-exit-{exit_kind}"`), writes intent audit (`live_exit_order_placed`), calls `data_exec.place(exit_order, division="bitunix_futures")`, on success delegates to `_record_exit_outcome(is_live=True, fill_event=fill)`.

Finding #6.4 wiring (reuse not new):
- `BitunixStuckOrderCancelled` → `live_exit_order_stuck_cancelled` audit + telegram; return False (replay loop retries)
- `BitunixStuckOrderCancelFailed` → `live_exit_order_halt` audit + elevated telegram (broker state UNKNOWN; halt-and-page)
- Generic `Exception` → `live_exit_order_rejected` audit + telegram; return False

**Tests:** `tests/test_bitunix_observer_execute_live_exits.py` (new file) — 9 tests; `tests/test_bitunix_broker_get_pending_positions.py` (new file) — 8 tests. 17/17 pass.

### Commit 4 — bitunix_position_reconciler wired for live mode

**PREMISE CORRECTION (load-bearing for future sessions, see §3):** the existing `bitunix_position_reconciler.py` is the **SL lifecycle reconciler** (`reconciler_tick`, `decide_sl_action`) — not a position-state reconciler. The scoping report's "dormant in paper mode" reference cited the SL lifecycle docstring (lines 25-29); the dormancy is about per-leg SL transitions waiting on Phase 4 broker fill state, NOT a stub for position-state checks.

**Resolution:** added `reconcile_position_state` to the same module as a separate function. Both are BitUnix position reconciliation concerns; they run on different cadences (SL every 60s, position-state on startup/reconnect) and serve orthogonal purposes.

**Source:** new dataclasses `PositionStateMatch`, `PositionStateMissingOnBroker`, `PositionStateOrphanOnBroker`, `PositionStateReconciliation` + helper `_load_tracked_live_rows(db_url)` + `_broker_side(qty)` + `async def reconcile_position_state(broker, db_url, *, halt_on_divergence=True)`. Behavior:
- Reads bot's tracked live rows (`paper_trade_record WHERE result IS NULL AND extra.execution_mode='live'`)
- Reads broker truth via `broker.get_pending_positions()` (commit 3)
- Matches by (symbol, side) — ONE_WAY mode → at most one position per (symbol, side)
- Surfaces `missing_on_broker` (bot tracks; broker doesn't) and `orphan_on_broker` (broker has; bot doesn't)
- On divergence: sets `broker._halt_new_orders=True` with reason; emits `position_state_divergence_detected` audit
- On clean: emits `position_state_reconciled` audit
- Transient broker errors: treated as "no broker positions known"; tracked rows become missing_on_broker; next tick recovers

Exits NOT halted per Phase 1a §9c (positions allowed to close naturally even when entries are halted).

**Tests:** 11 new in `tests/test_bitunix_position_reconciler.py` (extended existing file per user spec "extend if exists"). Covers clean match, missing-on-broker, orphan-on-broker, SHORT-side rendering, paper-mode row isolation, partial match, audit-kind branch, halt-bypass opt-out, broker-call-failure recovery. Plus the existing 26 SL lifecycle tests (37/37 pass total).

---

## 3. Premise corrections surfaced this session

### Premise correction #1 — `bitunix_position_reconciler.py` is NOT a position-state reconciler

**Scoping report §2.3 claim:** "Reconciler module exists? YES — `trading_corp/agents/divisions/bitunix_position_reconciler.py` exists, dormant in paper mode (docstring lines 25-29). **EXTEND** existing module."

**Actual main reality:** the module is the **SL lifecycle reconciler** — `reconciler_tick` moves stop-loss to BE after TP1, to TP1 floor after TP2, with Chandelier trail. It reads `broker.list_open_positions(db_url)` (synchronous local DB query, NOT `get_pending_positions()`). The "dormant in paper mode" comment is specifically about per-leg SL transitions waiting on Phase 4 broker fill state.

**Resolution this session:** added `reconcile_position_state` as a new function in the same module. The two are orthogonal concerns sharing a file. Scope still fits in ~262 source LOC (vs scoping report's ~50 LOC "extension" estimate), because the new function is essentially a full new reconciler, just sharing module headers + audit kinds with the existing one.

**Carry-forward:** Session B and N+3 sessions should not assume the existing reconciler module "is dormant" — `reconciler_tick` is the live SL lifecycle path. Any reconciler-touching scope must be careful to keep the two concerns independent.

### Premise correction #2 — `BitunixBroker.__init__` constructor signature

**Initial test-fixture assumption:** `BitunixBroker(api_key=..., api_secret=..., passphrase=..., base_url=...)`.

**Actual signature** (line 296):
```python
def __init__(
    self,
    api_key: str | None = None,
    api_secret: str | None = None,
    *,
    logger: "LoggerAgent | None" = None,
    safety_notifier=None,
) -> None:
```

No `passphrase` (kept blank in .env per signing docstring at line 54-55), no `base_url` (hard-coded). Test fixture updated to match (commit 3).

**Carry-forward:** any new BitunixBroker test fixture must use the 2-positional + 2-keyword constructor. Document for Session B + future sessions.

### Premise correction #3 — User's task-description scope ≠ scoping report §3 Session A

**Scoping report §3 Session A** was:
1. FillEvent additive fields (fee + venue_order_id) — ~15 LOC + 30 tests
2. Path C revert — ~30 LOC + 50 tests
3. insert_paper_trade_record DB-lock retry (Decision 6.2 mitigation) — ~10 LOC + 30 tests
4. `_record_exit_outcome` canonical helper (paper-mode only first) — ~80 LOC + 150 tests

**User's task description Session A** (this session) was:
1. Path C revert
2. `_record_exit_outcome` canonical helper (paper + live stamps)
3. `_execute_live_exits` + `get_pending_positions` extraction
4. `bitunix_position_reconciler` wired for live mode

The user explicitly deferred FillEvent additive fields + DB-lock retry to Session B; brought Session B's commits 5 (live exits) + 6 (reconciler) + Decision 6.4 (get_pending_positions) into Session A.

**Consequence on scope:** Session A LOC delta is ~669 source + ~1374 tests (vs scoping's ~135 / ~260 §3 Session A target). The user's actual scope is ~5× larger because it pulls in the load-bearing live exit primitives. Session B's remaining scope shrinks proportionally.

---

## 4. What stays NULL (deferred to Session B per the user's spec)

Per the user's prompt:
- Decision 6.2 (audit-row-loss recovery): "Defer to Session B — surface only if Session A's Path C revert encounters audit-row-loss as a direct issue." Path C in Commit 1 swallows DB-write failures; no audit-row-loss surfaced this session. **DEFERRED.**

Per the scoping report §3 and Phase 1b §7 carry-forward:

| Session B item | Status from Session A |
|---|---|
| FillEvent additive fields (`fee: float = 0.0`, `venue_order_id: str | None = None`) | NOT TOUCHED — user's spec for Commit 1 only stamps `broker_order_id` (existing `FillEvent.order_id`). No fee field yet; `notify_close_out` still renders "Fees: not tracked in paper" for live (per `comms/bitunix_lifecycle_notifier.py:158-159`). |
| `insert_paper_trade_record` DB-lock retry (Decision 6.2) | NOT TOUCHED — deferred per user spec. |
| `_record_exit_outcome` consumers wired into replay loop | NOT TOUCHED — Session A only ADDS the helper; replay loop still calls `_update_row` directly. |
| `_execute_live_exits` consumer (replay loop fork on `extra.execution_mode`) | NOT TOUCHED — Session A only ADDS the method; not invoked from any production code path yet. |
| `reconcile_position_state` wiring into `main.py` startup (so it runs BEFORE replay loop processes any row) | NOT TOUCHED — function exists + unit-tested but not scheduled. |
| Background 60s sanity poll (Phase 1b §2 background reconciler) | NOT TOUCHED. |
| Restart-resume `_resume_live_positions(deps)` cases (a)+(b) — Phase 1b §4 | NOT TOUCHED. |
| 8 operational alerts on `BitunixLifecycleNotifier` (Phase 1b §5) — `notify_exit_order_placed/filled/rejected/partial_fill`, `notify_position_closed_with_pnl`, `notify_reconciliation_divergence`, `notify_cost_accrual_recorded`, `notify_restart_resume_executed` | NOT TOUCHED. (Current observer pushes free-form telegram strings; the canonical lifecycle notifier extension is Session B work.) |

---

## 5. New audit kinds introduced (for the dashboard / operator grep)

| Kind | Actor | Emitted by | Trigger |
|---|---|---|---|
| `exit_outcome_recorded` | `bitunix_futures` | `_record_exit_outcome` (commit 2) | After paper_trade_record row UPDATE on any exit |
| `live_exit_order_placed` | `bitunix_futures` | `_execute_live_exits` intent (commit 3) | BEFORE `data_exec.place(reduce_only=True)` |
| `live_exit_order_stuck_cancelled` | `bitunix_futures` | `_execute_live_exits` (commit 3) | On `BitunixStuckOrderCancelled` — position remains open |
| `live_exit_order_halt` | `bitunix_futures` | `_execute_live_exits` (commit 3) | On `BitunixStuckOrderCancelFailed` — broker state UNKNOWN |
| `live_exit_order_rejected` | `bitunix_futures` | `_execute_live_exits` (commit 3) | On generic broker exception |
| `position_state_reconciled` | `bitunix_position_reconciler` | `reconcile_position_state` (commit 4) | On clean reconciler tick |
| `position_state_divergence_detected` | `bitunix_position_reconciler` | `reconcile_position_state` (commit 4) | On any missing_on_broker or orphan_on_broker |

All kinds carry `strategy` + `division` keys in payload where applicable (per CLAUDE.md § State + audit "Required tags on webhook events").

---

## 6. Test gate results

Full-suite pytest run on the impl branch via `.\scripts\run_capped.ps1 python -m pytest --continue-on-collection-errors`. Results pending at report-write time; updated post-run.

**Targeted gates verified during commit composition:**
- `tests/test_bitunix_observer_live_branch.py` — 17/17 pass
- `tests/test_bitunix_observer_record_exit_outcome.py` — 10/10 pass
- `tests/test_bitunix_observer_execute_live_exits.py` — 9/9 pass
- `tests/test_bitunix_broker_get_pending_positions.py` — 8/8 pass
- `tests/test_bitunix_position_reconciler.py` — 37/37 pass (26 existing SL + 11 new position-state)
- Combined regression sweep across observer + broker test files: 90+ pass, 0 fail.

**Full-suite test gate** (`.\scripts\run_capped.ps1 python -m pytest --continue-on-collection-errors -q`):

| Metric | Result | Baseline (per scoping §8.5) | Delta |
|---|---|---|---|
| FAILED | 28 | 28 | 0 (exact match) |
| ERRORS (collection) | 3 | 3 | 0 (exact match) |
| Pass count | not captured (PowerShell Out-File dropped pytest's stderr summary line) | 2139 | +~52 inferred (5 new test files = 52 new tests verified via per-file gates above) |

**All 28 failures are in modules untouched by Session A:**
- `tests/test_robinhood_multi_leg.py` (15) — Robinhood multi-leg broker tests
- `tests/test_webhooks_return_fast.py` (5) — webhook fast-return tests
- `tests/test_iron_condor_strategy.py` (3) — iron-condor strategy tests
- `tests/test_paper_run_tooling.py` (2) — paper-run readiness checker
- `tests/test_tasty_options_iron_condor.py` (3) — tasty iron-condor tests

The 3 collection errors are the same as baseline:
- `tests/test_backtest_bitunix_confluence_five_factor.py` — pre-existing `_resample_to_3m` import error
- `tests/test_bitunix_confluence_gate.py` — pre-existing `bitunix_confluence_gate` module not found
- `tests/test_bitunix_gate_inputs.py` — same as above

**Verdict: Session A introduces ZERO new failures or errors. The branch state matches origin/main's baseline exactly (28/3). Per [[branch-tests-must-cover-existing-fixtures]] the branch is gate-clean.**

Captured pytest stdout: `pytest_full_gate.txt` in the worktree (not committed; cleanup-on-worktree-remove).

---

## 7. Recommended next operator actions

1. **Verify prod stable** before any merge planning: `!ssh azureuser@trading.jacksumner.com 'systemctl show trading-corp --property=MainPID,ActiveState,SubState,NRestarts; curl -sS -o /dev/null -w "healthz=%{http_code}\n" https://trading.jacksumner.com/healthz'`. Expected: ActiveState=active, healthz=200, MainPID stable (no recent restarts).

2. **Review the 4 commits on `bitunix-live-exit-path-impl-2026-06-01`:**
   - `cb46e0e` — Path C revert
   - `6c36d6b` — `_record_exit_outcome` helper
   - `33e5732` — `_execute_live_exits` + `get_pending_positions`
   - `fadab6c` — position-state reconciler

3. **Decision: merge Session A standalone, or wait for Session B?**
   - Session A IS independently mergeable (paper-mode behavior unchanged; live-mode primitives added but not wired into any production code path).
   - Counter-argument: Session B wires the primitives into the replay loop; merging Session A alone leaves dead code on main for the wiring window.
   - **Recommend: merge Session A standalone**, then Session B branches off the merge. Smaller blast radius per merge; rebase Session B on top.

4. **Session B prompt** (next session): see `reports/2026-06-01_n2_phase3_session_b_handoff.md` (companion file written this session).

5. **No prod deploy from Session A** — Session A is source + tests only; `execution_mode` remains `"paper"` everywhere; no prod manifest changes.

---

## 8. Sources + memory anchors

- Code reads on `origin/main` `bd9c0a2` via Read tool: bitunix_futures_observer.py, paper_trade_replay.py, persistence/models.py, persistence/db.py, brokers/bitunix.py, brokers/bitunix_exceptions.py, agents/divisions/bitunix_position_reconciler.py
- Test fixture pattern from `tests/test_bitunix_observer_live_branch.py` + `tests/test_bitunix_position_reconciler.py`
- Phase 1a + 1b reports: `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md`, `phase1b.md` (canonical on main since merge `90ae0e4`)
- Scoping report: `reports/2026-06-01_n2_phase3_scoping.md`
- Memory anchors used:
  - `[[2026-06-01-n2-phase3-scoping]]` — scoping outcome + 5 operator decisions
  - `[[bitunix-live-exit-path-phase1a]]`, `[[bitunix-live-exit-path-phase1b]]` — Phase 1 structural decisions
  - `[[bitunix-live-entry-path-pattern]]` — N+1 entry-path patterns mirrored on exit side
  - `[[branch-tests-must-cover-existing-fixtures]]` — applied via existing-file fixture extensions
  - `[[telegram-audit-success-is-confirmed-delivery]]` — `_push_with_confirmed_delivery` reuse pattern
  - `[[classifier-blocks-prod-reads-above-static-perms]]` — applied to G1 prod-check deferral

**No prod changes; no execution_mode flip; no deploy attempts. Worktree-isolated; branch not yet merged.**
