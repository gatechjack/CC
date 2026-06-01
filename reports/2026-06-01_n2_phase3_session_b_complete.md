# N+2 Phase 3 Session B — implementation complete

**Date:** 2026-06-01 · **Type:** code implementation (5 scoped commits, source + tests, no deploy) · **Branch:** `bitunix-live-exit-path-impl-session-b-2026-06-01` off `origin/main` (`1162273`, after the 28/3 baseline-correction docs commit) · **Status:** Session B scope landed; STOP-AND-REPORT for operator merge approval per CLAUDE.md § Session wrap-up.

**Verification SHAs:**
- `origin/main` = `1162273` (pre-Session-B base after 28/3 baseline-correction docs commit)
- This branch HEAD = `a5a5c51` (5 commits ahead of main)
- Worktree = `.claude/worktrees/n2-phase3-impl-b-2026-06-01`

**Pre-flight verifications:**
- ✅ Prod stability: operator-confirmed pre-Session-B: `MainPID=1961197, NRestarts=0, ActiveState=active, SubState=running, healthz=200` (per Session B prompt opener; same prod state as Session A merge).
- ✅ Baseline test gate on fresh worktree: 28/3 — exact match to corrected canonical baseline (per `runbooks/deploy_log.md` 2026-06-01 ~18:50 UTC entry post-correction; P3 `0b8419a` refiled with corrected DB-fixture-coupling diagnosis).
- ✅ Worktree isolation: dedicated `n2-phase3-impl-b-2026-06-01` worktree per CLAUDE.md § Session discipline.

---

## 1. Scope landed (5 commits)

Per operator's task-description Session B breakdown:

| Commit | SHA | Subject | Source LOC | Test LOC |
|---|---|---|---|---|
| 1 | `f66722e` | Layer 1 fee plumbing — FillEvent.fee + broker extraction + downstream stamps | +22 / -1 | +291 |
| 2 | `b5278c5` | Decision 6.2 — db-lock retry on insert_paper_trade_record | +68 / -3 | +213 |
| 3 | `6982008` | main.py startup wires bitunix_position_reconciler for live-mode state reconciliation | +47 | +214 |
| 4 | `5edd438` | wire `_record_exit_outcome` + `_execute_live_exits` into paper_trade_replay live-mode walk | +98 / -5 | +296 |
| 5 | `a5a5c51` | `_resume_live_positions` + 60s sanity poll + 8 operational alerts on BitunixLifecycleNotifier | +697 | +284 |
| | | **Totals** | **~932** | **~1298** |

**Session B total delta:** ~2186 insertions, 29 deletions across 14 files (6 new test files + 8 source files modified).

---

## 2. Commit detail

### Commit 1 — Layer 1 fee plumbing (`f66722e`)

**Source:**
- `persistence/models.py:79-87` — `FillEvent.fee: float = 0.0` field added with default. In-memory dataclass only (verified via grep: no `CREATE TABLE` for fill_event; not persisted as own table). All existing constructors (paper, robinhood, tasty, coinbase, fidelity) continue working unchanged.
- `brokers/bitunix.py:892` — `_fee` → `fee` rename (no longer discarded). Line 910 `FillEvent(..., fee=fee)` passes through.
- `agents/divisions/bitunix_futures_observer.py:2872-2875` — Path C `_place_live` stamps `record.extra["entry_fee_usd"] = float(fill.fee or 0.0)`.
- `agents/divisions/bitunix_futures_observer.py:2574-2576` — `_record_exit_outcome` extra_json merge: `merged["exit_fee_usd"] = float(fill_event.fee or 0.0)` when fill_event present.
- `agents/divisions/bitunix_futures_observer.py:2613` — `_record_exit_outcome` audit payload: `"fee": float(fill_event.fee or 0.0)` in the `fill_event` sub-block.

**Tests:** `tests/test_fill_event_fee_plumbing.py` (10 new) cover field default + explicit value, Path C entry_fee_usd stamp (broker reports fee + reports zero), `_record_exit_outcome` exit_fee_usd stamp in extra + audit fill_event.fee + paper-mode no-stamp isolation.

**Verification:** 34/34 (10 new + 24 regression on Session A's live_branch + record_exit_outcome tests).

### Commit 2 — Decision 6.2 db-lock retry on `insert_paper_trade_record` (`b5278c5`)

**Source:** `persistence/db.py` — added `_DB_LOCK_RETRY_DELAYS_SEC = (0.1, 0.3, 0.7)` (mirrors `agents/logger.py` schedule, duplicated to keep dependency arrow correct: persistence is foundational, agents/logger imports from it). `insert_paper_trade_record` wrapped in retry loop keyed on "database is locked" substring. Non-lock OperationalErrors propagate on first attempt. Retry exhaustion re-raises (caller's existing try/except handles). `INSERT OR IGNORE` makes retry idempotent.

**Tests:** `tests/test_insert_paper_trade_record_retry.py` (8 new) cover happy path (single attempt), INSERT OR IGNORE duplicate-no-error, 1-retry success, 3-retry success (full schedule consumed), retry exhaustion (4 total attempts → re-raise), non-lock OperationalError propagates immediately, retry counter isolation across calls. autouse fixture monkeypatches delays to 0.001 so the suite stays fast.

**Verification:** 31/31 (8 new + 23 regression on Session A's observer tests).

### Commit 3 — `main.py` startup wires `reconcile_position_state` for live mode (`6982008`)

**Source:** `trading_corp/main.py` — new 35-line block immediately before the existing trade-plan SL reconciler hookup at line 1560. Gated to: `_execution_mode == "live"` AND `_bx_broker is not None` AND `hasattr(_bx_broker, "get_pending_positions")`. Awaited (not background task) so the halt latch is set BEFORE downstream tasks start. On clean: log.info + `position_state_reconciled` audit. On divergence: log.warning + `position_state_divergence_detected` audit + `_bx_broker._halt_new_orders=True`. On exception: log.exception + continue.

**Tests:** `tests/test_main_startup_position_state_reconciler.py` (5 new) exercise the three gate conditions + call outcomes (clean / divergence) + paper-mode skip + reconciler-exception caught. Plus regression on `tests/test_bitunix_position_reconciler.py` (37/37 SL lifecycle + position-state).

### Commit 4 — Replay-loop wiring of Session A helpers (`5edd438`)

**Source:**
- `agents/paper_trade_replay.py` — new `_LIVE_EXIT_EXECUTOR` registry dict + `set_live_exit_executor(observer)` setter (mirrors `set_lifecycle_notifier` pattern). New `_verdict_to_exit_kind()` helper mapping win/loss/expired → "tp"/"sl"/"expired". `_replay_tick_async` at the resolution-write site forks on (`extra.execution_mode == "live"`) AND executor registered AND `hasattr(executor, "_execute_live_exits")`. Live path awaits `observer._execute_live_exits(...)` with full kwargs (order_id, symbol, entry_side, qty, exit_kind, parent_broker_order_id, result, result_ts, result_price, actual_pnl_dollars, actual_r_multiple, bars_to_resolution, extra_json_updates). Executor exceptions caught + `counts["errors"]++`. Backward-compat fallback: live-tagged row with no registered executor falls back to `_update_row` (Session A behavior; position-state reconciler at next startup catches stranded rows).
- `trading_corp/main.py` — `set_live_exit_executor(bitunix_observer)` called immediately after `set_lifecycle_notifier(...)` + before `start_replay_loop(...)`. try/except keeps startup robust.

**Tests:** `tests/test_paper_trade_replay_live_exit_fork.py` (12 new) cover verdict-to-exit-kind mapping (3 cases), register/None-disable, live-tagged row triggers `_execute_live_exits` with correct kwargs, live row skips `_update_row` (executor owns the write), paper row + explicit `execution_mode="paper"` take existing path, no-executor fallback, executor exception caught + errors++, short entry_side pass-through.

**Verification:** 39/39 (12 new + 27 regression on adjacent files including main dataclass completeness).

### Commit 5 — `_resume_live_positions` + 60s sanity poll + 8 operational alerts (`a5a5c51`)

**Source — `agents/divisions/bitunix_position_reconciler.py`:** new `resume_live_positions(broker, db_url, *, halt_on_orphan_or_case_c=True, notifier=None)` async function reusing `reconcile_position_state`'s match/missing/orphan output, adding case-specific audits + halt latch + notifier integration:
- Case (a) match: `restart_resume_executed` audit
- Case (b) orphan_on_broker: `orphan_broker_position_on_restart` audit + halt latch
- Case (c) deferred (= reconciler's `missing_on_broker`): `restart_resume_case_c_deferred` audit + halt latch (operator-resolve)
Per Phase 1a §9c exits NOT halted; only entries via `_halt_new_orders`.

New `run_position_state_sanity_poll_loop(broker, db_url, *, interval_s=60.0, notifier=None)` async forever-loop calling `reconcile_position_state` per interval. Floored at 0.001 for testability; prod callers pass 60.0. On divergence emits `notify_reconciliation_divergence` per missing/orphan row. Exception-tolerant: per-tick failures logged + swallowed; `asyncio.CancelledError` propagates.

**Source — `comms/bitunix_lifecycle_notifier.py`:** 8 new methods, all routing through existing `_send` → `_channel.push` → confirmed-delivery audit semantics. New audit_path tags: `lifecycle_exit_order_placed`, `lifecycle_exit_order_filled`, `lifecycle_exit_order_rejected`, `lifecycle_exit_partial_fill`, `lifecycle_position_closed_with_pnl`, `lifecycle_reconciliation_divergence`, `lifecycle_cost_accrual_recorded`, `lifecycle_restart_resume_executed`. `notify_exit_order_filled` accepts optional `live_exit_counter` + `live_exit_counter_total` for the `(exit #N/M)` suffix (Phase 1a §8 first-N elevated visibility).

**Source — `trading_corp/main.py`:**
- Lines added near Commit 3's reconciler hookup: `resume_live_positions(_bx_broker, secrets.db_url, notifier=None)` awaited at startup BEFORE Commit 3's reconciler one-shot.
- Lines added near the existing SL reconciler hookup: `asyncio.create_task(run_position_state_sanity_poll_loop(...))` background task gated to live mode.

**Tests:**
- `tests/test_resume_live_positions.py` (5) — case (a) clean + (b) orphan halt + (c) deferred halt + notifier integration + halt opt-out.
- `tests/test_bitunix_lifecycle_notifier_alerts.py` (9) — each of the 8 methods + counter-suffix variant on `notify_exit_order_filled`.
- `tests/test_position_state_sanity_poll.py` (3) — tick cadence, divergence-fires-notifier, multi-tick continuation.

**Verification:** 54/54 (17 new + 37 regression on bitunix_position_reconciler tests).

---

## 3. Activation ledger (Session A dormant → Session B active)

| Session A primitive | Session B caller wired | Reachability on current prod |
|---|---|---|
| Path C `_place_live` row write | Already active in Session A (gated by `execution_mode == "live"`) | INERT (paper-mode gate) |
| `_record_exit_outcome` canonical helper | Called via `_execute_live_exits` (Commit 4 wiring) | DORMANT until live mode |
| `_execute_live_exits` async method | Called from `_replay_tick_async` for live-tagged rows (Commit 4) | DORMANT (no live-tagged rows on prod) |
| `BitunixBroker.get_pending_positions` | Called from `reconcile_position_state`, `resume_live_positions`, `run_position_state_sanity_poll_loop` | DORMANT (gates short-circuit in paper mode) |
| `reconcile_position_state` | Called at main.py startup (Commit 3) + 60s background loop (Commit 5) + via `resume_live_positions` (Commit 5) | DORMANT (gated by `execution_mode == "live"`) |

**Behavioral net on prod after merge (with prod still at `execution_mode=paper`):**
- Path C continues to fire for the `_record_placement_outcome` paper-path (no behavior change; the paper block at observer:2470-2495 stays byte-identical).
- Live-mode startup hooks (restart-resume, position-state reconciler, sanity poll) ALL short-circuit on the `_execution_mode == "live"` check → no calls, no audits.
- `_record_exit_outcome` activates whenever the replay loop sees a live-tagged row — but Path C only writes `execution_mode=live` on actual live placements, which `auto_execute=false` + `execution_mode=paper` prevent. So in prod paper-mode: zero live-tagged rows → fork stays in paper branch → no behavior change.
- Decision 6.2 db-lock retry ACTIVE on every `insert_paper_trade_record` call (paper-mode + live-mode), but the actual retry logic only fires under contention. Today's prod lock contention is rare; the retry is silent until needed.
- Layer 1 fee plumbing ACTIVE — FillEvent.fee is now populated by BitunixBroker (default 0.0 if `_observe_fill` doesn't surface a fee). Default 0.0 keeps existing constructors functional.

**Paper-mode behavior on current prod: byte-identical pre/post Session B merge.** The fee field has a default; the db-lock retry only triggers under contention; the live-mode hooks all short-circuit. Verified via per-commit dormancy analysis + 28/3 baseline match throughout.

---

## 4. New audit kinds introduced (extends Session A's 7)

| Kind | Actor | Emitted by | Trigger |
|---|---|---|---|
| `restart_resume_executed` | `bitunix_position_reconciler` | `resume_live_positions` (Commit 5) | Per case-(a) match at startup |
| `orphan_broker_position_on_restart` | `bitunix_position_reconciler` | `resume_live_positions` (Commit 5) | Per case-(b) orphan at startup |
| `restart_resume_case_c_deferred` | `bitunix_position_reconciler` | `resume_live_positions` (Commit 5) | Per case-(c) deferred at startup |

Session A's 7 audit kinds (`exit_outcome_recorded`, `live_exit_order_placed`, `live_exit_order_stuck_cancelled`, `live_exit_order_halt`, `live_exit_order_rejected`, `position_state_reconciled`, `position_state_divergence_detected`) all still apply.

8 new telegram audit_path tags from Commit 5 (`lifecycle_exit_order_*`, `lifecycle_position_closed_with_pnl`, `lifecycle_reconciliation_divergence`, `lifecycle_cost_accrual_recorded`, `lifecycle_restart_resume_executed`).

---

## 5. Premise corrections surfaced this session

### Premise correction #1 — paper_run_tooling baseline coupling diagnosis (resolved pre-Commit-1)

The Session B pre-flight surfaced that the baseline test gate showed 28/3 in a fresh worktree, NOT the 26/3 the Session A merge close-out had documented. Investigation revealed:
- `test_paper_run_tooling.py::test_readiness_check_all_blocking_pass_on_production_config` + sibling fail with `no such table: agent_state` / `audit_event` / `position` when `data/trading_corp.db` doesn't exist at the default path.
- The `cc/` main worktree had this DB initialized from prior testing activity; fresh worktrees do not.
- The Session A merge close-out's "26/3 → updated baseline" framing AND the original P3 `0b8419a` framing ("BACKLOG.md doc-text coupling") were BOTH wrong.

**Resolution:** documented in `runbooks/deploy_log.md` 2026-06-01 ~18:50 UTC entry + memory `[[2026-06-01-n2-phase3-session-a-merged]]` + BACKLOG P3 entry refiled with corrected diagnosis. Canonical fresh-worktree baseline is **28/3**, applicable to any clean checkout including CI. Session A's merge introduced zero test regressions; the merge effect framing stands, only the post-merge count statement was misleading. Single docs commit `1162273` on origin/main captures the correction.

### Premise correction #2 — sanity poll's `interval_s` floor

Initial implementation of `run_position_state_sanity_poll_loop` floored `interval_s` at 1.0 (sensible for prod). Tests needed sub-second intervals to verify multi-tick cadence within reasonable test wallclock. Floored at 0.001s instead — prod callers pass 60.0; testability not compromised.

---

## 6. What stays NULL (deferred to N+3 per Phase 1b)

- **Layer 2 funding accrual** — `get_history_positions` + per-interval funding pulls + cumulative funding stamps. Phase 1b §3 Stage-2-or-later work; Session B added the `notify_cost_accrual_recorded` stub method so the future work has a notifier surface ready.
- **Restart-resume Case C automatic resolution** — currently halts + pages operator. Phase 1b §4 says "with operator-halt-and-resolve for case (c)"; Session B implements that conservative path. Full automatic resolution requires `get_history_positions` + close-time reconstitution.
- **Lumibot 5s background sanity poll** — 60s poll is the Stage-1 baseline. Stage-2 expansion may warrant tighter cadence; Stage-1 sizing economics don't justify it.
- **WebSocket position channel** — Stage-3 reuse audit; out of scope.

---

## 7. Test gate results

Targeted gates verified per-commit during composition:
- `tests/test_fill_event_fee_plumbing.py` — 10/10 pass (Commit 1)
- `tests/test_insert_paper_trade_record_retry.py` — 8/8 pass (Commit 2)
- `tests/test_main_startup_position_state_reconciler.py` — 5/5 pass (Commit 3)
- `tests/test_paper_trade_replay_live_exit_fork.py` — 12/12 pass (Commit 4)
- `tests/test_resume_live_positions.py` — 5/5 pass (Commit 5)
- `tests/test_bitunix_lifecycle_notifier_alerts.py` — 9/9 pass (Commit 5)
- `tests/test_position_state_sanity_poll.py` — 3/3 pass (Commit 5)

Regression on Session A's test files (all green throughout Session B): `test_bitunix_observer_live_branch.py` (17/17), `test_bitunix_observer_record_exit_outcome.py` (10/10), `test_bitunix_observer_execute_live_exits.py` (9/9), `test_bitunix_broker_get_pending_positions.py` (8/8), `test_bitunix_position_reconciler.py` (37/37 — 26 SL lifecycle + 11 Session A position-state). `test_main_dataclass_construction_completeness.py` 9/9 green at each commit.

**Full-suite final gate (on impl branch `673a909` post-EOS-commit)**: **28 failed + 3 errors — exact baseline match. Zero new failures from Session B.** Failure distribution identical to pre-flight: 3 `test_iron_condor_strategy.py` + 2 `test_paper_run_tooling.py` (DB-fixture-coupled per P3) + 15 `test_robinhood_multi_leg.py` + 3 `test_tasty_options_iron_condor.py` + 5 `test_webhooks_return_fast.py`. All 28 in modules untouched by Session B's commits. The 3 collection errors are the same pre-existing baseline. Per `[[branch-tests-must-cover-existing-fixtures]]` the branch is gate-clean.

---

## 8. Recommended next operator actions

1. **Re-verify prod stable** before any merge planning. Operator-runs:
   ```
   !ssh azureuser@trading.jacksumner.com 'systemctl show trading-corp --property=MainPID,ActiveState,SubState,NRestarts; curl -sS -o /dev/null -w "healthz=%{http_code}\n" https://trading.jacksumner.com/healthz'
   ```
   Expected: MainPID stable, NRestarts low single digits, ActiveState=active, healthz=200.

2. **Review the 5 commits on `bitunix-live-exit-path-impl-session-b-2026-06-01`:**
   - `f66722e` — Layer 1 fee plumbing
   - `b5278c5` — Decision 6.2 db-lock retry
   - `6982008` — main.py startup reconciler hookup
   - `5edd438` — replay-loop wiring of Session A helpers
   - `a5a5c51` — restart-resume + 60s sanity poll + 8 alerts

3. **Decision: merge Session B standalone, or bundle with a future N+3 batch?**
   - Session B IS independently mergeable (paper-mode behavior byte-identical on prod; live-mode primitives now activated but all gated by `execution_mode=paper`).
   - All Session A primitives now have production callers (dormancy ledger → activation ledger transition complete).
   - Recommend: **merge standalone** after operator audit, matching Session A's merge cadence. N+3 work (Layer 2 funding, full Case C auto-resolve) layers on top.

4. **No prod deploy from Session B** — Session B is source + tests only; `execution_mode` remains `paper` everywhere; no prod manifest changes. Deploy is a separate session.

---

## 9. Sources + memory anchors

- Code reads on `origin/main` `1162273` via Read tool: persistence/models.py, persistence/db.py, brokers/bitunix.py, agents/divisions/bitunix_futures_observer.py, agents/divisions/bitunix_position_reconciler.py, agents/paper_trade_replay.py, agents/logger.py, comms/bitunix_lifecycle_notifier.py, main.py.
- Session A complete report: `reports/2026-06-01_n2_phase3_session_a_complete.md`
- Session B handoff prompt: `reports/2026-06-01_n2_phase3_session_b_handoff.md`
- Scoping report: `reports/2026-06-01_n2_phase3_scoping.md`
- Memory anchors used:
  - `[[2026-06-01-n2-phase3-session-a-merged]]` — Session A merge outcome + corrected baseline
  - `[[2026-06-01-n2-phase3-scoping]]` — full Phase 3 scope
  - `[[bitunix-live-exit-path-phase1a]]`, `[[bitunix-live-exit-path-phase1b]]` — structural decisions
  - `[[bitunix-live-entry-path-pattern]]` — entry-side patterns mirrored
  - `[[branch-tests-must-cover-existing-fixtures]]` — full pre-existing test suite must run
  - `[[telegram-audit-success-is-confirmed-delivery]]` — channel.push reuse
  - `[[classifier-blocks-prod-reads-above-static-perms]]` — applied to prod check deferral

**No prod changes; no execution_mode flip; no deploy attempts. Worktree-isolated; branch not yet merged.**
