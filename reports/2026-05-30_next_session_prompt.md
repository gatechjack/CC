# Next session prompt — paste into a fresh Claude session

Use this as the opening message in a new Claude session. It carries
forward everything the next session needs without re-deriving prior
context. Equivalent to `reports/2026-05-29_next_session_prompt.md`
which was used to start the 2026-05-30 merge session.

---

## Prompt

```
Resume Stage-1 N+2 Phase 3 — implement live exit-path on rebased main

Prior sessions shipped:
* Stage-1 N (order-path safety scaffolding) — branch
  bitunix-orderpath-safety-2026-05-29 — MERGED to main 2026-05-30.
* Stage-1 N+1 (live entry-path: execution_mode + HITL + StrategyState
  persistence + 17 swap sites + safety_notifier wiring) — branch
  bitunix-live-entry-path-2026-05-29 — MERGED to main 2026-05-30.
* Stage-1 broker-write (Phase-4 place/cancel/fill/kill-switch) —
  branch bitunix-live-engine-stage1-broker-write — MERGED to main
  2026-05-30.
* C-1 credential rotations (bitunix + apify + tastytrade verify) —
  all MERGED to main 2026-05-30.
* Stage-1 N+2 Phase 1a + Phase 1b read-only diagnostics — branch
  bitunix-live-exit-path-2026-05-29 — PUSHED (e1d38f8) but UNMERGED.
  This branch is the target for Phase 3 implementation.

This session: START Stage-1 N+2 Phase 3 — implement live exit-path
per the Phase 1b (B) Narrowed scope. Operator approved (B) + merge-
sequence (a) on 2026-05-30; the merge sequence executed cleanly.

READ FIRST (in this order):

1. cd "C:\Users\AA Incorporado\cc"
2. git checkout bitunix-live-exit-path-2026-05-29 ; git pull
3. reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md
   — structural foundation (questions #1, #2, #3, #8, #9).
4. reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1b.md
   — completes #4 reconciliation + #5 cost accrual + #3 restart-resume
   + #7 alerts; A/B/C scope decision section (B = confirmed).
5. Memory [[bitunix-live-exit-path-phase1b]] — operator-decisions-
   resolved stamp.
6. Memory [[stage1-on-main-merge-session-2026-05-30]] — merge-session
   ground-truth: what's on main, what's NOT on prod, fixture-gap
   discipline lesson.
7. Memory [[bitunix-live-engine-build]] — Stage-1 overall status with
   2026-05-30 update header.
8. Memory [[branch-tests-must-cover-existing-fixtures-not-only-new-tests]]
   — discipline carries to Phase 3: run FULL pre-existing test suite,
   not just new exit-path test files, before declaring "tests green."
9. runbooks/deploy_log.md (top entry: 2026-05-30 merge session) —
   detailed conflict-resolution policy applied + test count history.
10. BACKLOG.md "P2 — BitUnix Stage-1 N+2 Phase 3 implementation (live
    exit-path) — queued for next session" — work shape inventory.

VERIFY CURRENT STATE before assuming:

* `main` HEAD should be `1926eb9` (post merge-session EOS commit). If
  newer, that's fine — pull and review intervening commits before
  rebasing N+2 onto it.
* `bitunix-live-exit-path-2026-05-29` should be at `e1d38f8` on origin
  (3 doc-only commits ahead of pre-merge main: Phase 1a report +
  BACKLOG annotations + Phase 1b report + next-session-prompt).
* The fixture-fix commits (4e75216, 7aca8dd, 2cbcc3b) are ALREADY on
  main from the merge session — Phase 3 inherits the fixture-gap
  resolution; new exit-path tests should be written with AsyncMock
  for data_exec.flatten_division + db_url on any _Deps fixtures from
  the start.
* Prod stays at `4985bbe` (pre-merge). No deploy this session unless
  operator explicitly requests.
* Default `execution_mode: paper` everywhere.

PHASE 3 SCOPE (B) — implement per Phase 1b recommendations:

Commit 1 — Path C revert (~30 LOC + tests)
  * Add `venue_order_id: str | None = None` to FillEvent dataclass at
    persistence/models.py.
  * In broker-write's place_order at brokers/bitunix.py:608, capture
    the BitUnix-side orderId into venue_order_id alongside the
    existing fields.
  * In _place_live at bitunix_futures_observer.py (post-N+1 line
    numbers — verify), after data_exec.place returns the FillEvent,
    also call db.insert_paper_trade_record(record.to_db_row(), ...)
    with record.extra["execution_mode"]="live" + record.extra[
    "broker_order_id"]=fill_event.order_id + record.extra[
    "broker_venue_order_id"]=fill_event.venue_order_id.
  * Reverses N+1 commit 3's "no paper_trade_record on live path"
    decision — required for paper_trade_replay to see live positions.
  * Tests: paper_trade_record presence + execution_mode="live" + both
    order_id fields stamped.

Commit 2-4 — _record_exit_outcome canonical helper (~400 LOC + 250
test LOC)
  * Revised signature: _record_exit_outcome(row, resolved,
    fill_event, leg=None, db_url=...)
  * In-place paper/live fork. Paper-mode = existing _update_row path.
    Live-mode = construct reduce_only=True ProposedOrder + call
    data_exec.place + reconcile broker truth back into the row.
  * Async _execute_live_exits follow-up step for multi-leg fills.
  * Tests: paper byte-identical, live places reduce_only + side
    inverted, multi-leg per-leg fraction qty, SL closes remainder,
    expired closes remainder.

Commit 5 — #4 event-driven reconciler inside _execute_live_exits
(~80 LOC + tests)
  * After data_exec.place(reduce_only=True) returns FillEvent, call
    _reconcile_single(row, fill_event) BEFORE writing final audit row.
  * Reads broker truth via bitunix_broker.get_history_trades(order_id
    =fill_event.order_id).
  * Compares broker-truth qty + price against row's stamped values.
  * Tolerance: ±0.1% price, exact qty match (per-fill granularity).
  * Divergence: audit reconciliation_divergence_detected + telegram +
    halt broker self-latch. No auto-correct.
  * Idempotency: dedup via broker_order_id; reconciler skips already-
    reconciled rows.
  * DEFER: 5s background poll loop (lumibot pattern) — N+3 work.

Commit 6 — #5 Layer 1 fee plumbing (~60 LOC + tests)
  * Add fee: float = 0.0 to FillEvent dataclass.
  * In broker-write place_order, drop the `_fee` discard at :598;
    return fee in FillEvent.
  * Stamp extra_json["fee_usd"] from entry fill; stamp
    extra_json["exit_fee_usd_<leg>"] from each exit fill.
  * Update notify_close_out live branch to render real fee numbers
    (drop "not tracked in paper" string when prefix == LIVE).
  * DEFER: Layer 2 funding accrual + get_history_positions — N+3 work.

Commit 7-8 — #3 restart-resume cases (a)+(b) (~120 LOC + 80 test LOC)
  * New function _resume_live_positions(deps) in observer or new
    module live_resume.py. Called from main.py startup BEFORE
    paper_trade_replay.replay_pending_paper_trades cron starts.
  * Reads bitunix_broker.get_pending_positions + paper_trade_record
    WHERE result IS NULL AND extra_json LIKE '%execution_mode%live%'.
  * Three-case match on (symbol, side):
    - (a) broker+row match → restart_resume_executed audit + push
      telegram + resume tracking.
    - (b) broker-only orphan → orphan_broker_position_on_restart
      audit + halt broker self-latch + elevated telegram. Path C
      should prevent this case.
    - (c) row-only (broker closed during downtime) → DEFER to N+3
      with operator-halt-and-resolve path: restart_resume_case_c_
      deferred audit + halt + telegram operator to resolve via
      BitUnix UI.
  * Tests: each case + sequencing (resume runs before replay loop).

Commit 9-11 — #7 all 8 alert methods (~250 LOC + 150 test LOC)
  Extend BitunixLifecycleNotifier (don't fork):
  * notify_exit_order_placed
  * notify_exit_order_filled (with counter-aware (live, exit #N/10)
    suffix via live_exit_counter_getter constructor kwarg, reusing
    entry-path HITL-counter pattern; new agent_state key
    "live_exits_executed")
  * notify_exit_order_rejected
  * notify_exit_partial_fill (when fill_event.venue ends with
    ":part_filled")
  * notify_position_closed_with_pnl (replaces paper notify_close_out
    on live branch; renders real fee + funding + net PnL)
  * notify_reconciliation_divergence (from commit 5's reconciler)
  * notify_cost_accrual_recorded (N+3 hook; placeholder for now)
  * notify_restart_resume_executed (from commit 7-8)
  All route through existing _send → channel.push → confirmed-delivery
  audit semantics inherited (no new audit code, only payload shapes).

HARD CONSTRAINTS carried forward:

* NO live order flow this session (default --paper, default
  execution_mode: paper).
* NO deploy this session unless operator explicitly requests.
* Test-first development — each commit lands with tests green.
* Run FULL pre-existing test suite after EACH commit, not just the
  new exit-path tests. Compare to baseline 26 failures (unchanged
  from merge-session EOS at HEAD 1926eb9).
* Standard scoped commits — Path C revert is commit 1, then canonical
  helper, then reconciler, then fee plumbing, then restart-resume,
  then alerts. 8-11 commits total.
* Confirmed-delivery semantics for telegram (per [[telegram-audit-
  success-is-confirmed-delivery]]).
* Stop-and-report at any fork: premise correction, scope creep,
  unexpected merge-state delta, test regression outside fixture-gap
  class.
* Memory updates as we go — file [[bitunix-live-exit-path-pattern]]
  when N+2 ships, mirroring the [[bitunix-live-entry-path-pattern]]
  structure.

PROCESS GATES still in play (don't pull forward):
* 60-day paper-eval clock (~2026-07-19, [[bitunix-paper-clock]]).
* Board sign-off for auto_execute per CLAUDE.md §1.
* Webhook ↔ LangGraph auto_execute_caps harmonization (CLAUDE.md §1).

OPERATOR PRE-CONFIRMATIONS (don't re-litigate):
* Scope (B) Narrowed.
* Merge-sequence (a) executed.
* Path C revert (live entries write paper_trade_record with
  execution_mode tag).
* NO HITL on exits (elevated telegram suffix instead).
* In-place fork (canonical helper, not parallel executor).

OUTPUT EXPECTED:

* Phase 3 implementation across 8-11 scoped commits on branch
  bitunix-live-exit-path-2026-05-29 (after rebase).
* Push branch after each verified commit (not batched).
* Full test suite green at HEAD baseline (26 unrelated failures
  unchanged) at every commit.
* deploy_log.md entry at end of Phase 3 marking source-shipped (NOT
  deployed to prod unless operator requests).
* BACKLOG.md update marking N+2 Phase 3 RESOLVED.
* Memory [[bitunix-live-exit-path-pattern]] filed at Phase 3 close
  with commit-by-commit breakdown.
* STOP-and-report at fork points; standard discipline.
```

---

## Operator quick-pass checklist before pasting

- [ ] Confirm working tree is clean + on `bitunix-live-exit-path-2026-05-29` after the rebase that the next session will perform.
- [ ] Confirm no intervening commits to `main` since `1926eb9` that would change the rebase target meaningfully (a quick `git log 1926eb9..main` will tell you).
- [ ] Decide if you want Phase 3 in ONE session (8-11 commits is ambitious for one session — expect 2 if interrupted) or pre-split (e.g., commits 1-5 this session, 6-11 next).
- [ ] Decide deploy timing — Phase 3 ships source-on-main, prod deploy is a separate operator-gated step requiring RH-pickle-aware restart coordination.

If you want to amend any of these, edit the prompt block above before pasting.
