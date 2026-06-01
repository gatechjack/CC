# N+2 Phase 3 Session B — next-session handoff prompt

**Date this prompt was written:** 2026-06-01 · **Predecessor:** `reports/2026-06-01_n2_phase3_session_a_complete.md`. Session A landed 4 commits on `bitunix-live-exit-path-impl-2026-06-01` (HEAD `fadab6c`); not yet merged to main. Use as a paste-ready prompt for a fresh Claude Code session when ready to execute Session B.

---

```
N+2 Phase 3 Session B — wire live exit path into the replay loop + restart-resume + alerts + Decision 6.2 + FillEvent fields. Operator-supervised; ~3-4 hours target; 5-6 scoped commits.

Read first (in order):
- reports/2026-06-01_n2_phase3_session_a_complete.md — what landed, premise corrections, what's deferred to this session.
- reports/2026-06-01_n2_phase3_scoping.md §3 (Session B), §6 (decisions), §7 (the original next-session prompt).
- reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1b.md §2-§5 (reconciliation, cost accrual, restart-resume, alerts).
- CLAUDE.md §Session discipline, §Environment, §Testing discipline.

Memory anchors:
- [[2026-06-01-n2-phase3-session-a-complete]] — Session A outcomes + premise corrections.
- [[2026-06-01-n2-phase3-scoping]] — full Phase 3 scope.
- [[bitunix-live-exit-path-phase1a]], [[bitunix-live-exit-path-phase1b]] — structural decisions.
- [[bitunix-live-entry-path-pattern]] — entry-side patterns the exit side mirrors.
- [[branch-tests-must-cover-existing-fixtures]] — full pre-existing test suite must run against branch state.

Branch + worktree setup:
- Pre-condition: confirm Session A merge status. Two paths:
  (a) Session A already merged to main → start a fresh worktree off origin/main:
      git worktree add ".claude/worktrees/n2-phase3-impl-b-<date>" -b "bitunix-live-exit-path-impl-b-<date>" origin/main
  (b) Session A still unmerged → branch Session B on top:
      git worktree add ".claude/worktrees/n2-phase3-impl-b-<date>" -b "bitunix-live-exit-path-impl-b-<date>" bitunix-live-exit-path-impl-2026-06-01

State verification before starting:
- git rev-parse origin/main — quote SHA.
- git rev-parse origin/bitunix-live-exit-path-impl-2026-06-01 — should be at fadab6c (Session A HEAD).
- ssh tc-prod-vm 'systemctl show trading-corp --property=MainPID,ActiveState,SubState,NRestarts; curl -sS -o /dev/null -w "healthz=%%{http_code}\n" https://trading.jacksumner.com/healthz' — confirm prod stable.
- Baseline pytest on impl-b branch: expect 2241/28/3 (+ ~52 Session A tests on top of 2139/28/3 main baseline).

Operator decisions pre-resolved (Session A reaffirms; do not re-litigate):
- 6.1 stamp: extra["result_source"] = "paper_replay_bars" | "live_broker_truth" — helper exists from Session A Commit 2. Consumers wire in this session.
- 6.2 db-lock retry: (a) extend insert_paper_trade_record with the existing _DB_LOCK_RETRY_DELAYS_SEC schedule from agents/logger.py — DO IT THIS SESSION (deferred from Session A per user's spec).
- 6.3 split (B): confirmed. This session is the second half.
- 6.4 get_pending_positions extraction: (a) DONE in Session A Commit 3.
- 6.5 docs merge: (a) RESOLVED 2026-05-31 via merge 90ae0e4.

Session B scope (5-6 commits, ~600-800 source LOC + ~800-1100 test LOC):

Commit 5 — FillEvent additive fields + broker_write plumbing.
  Add fee: float = 0.0 and venue_order_id: str | None = None to FillEvent
  (persistence/models.py). Drop the _fee discard at broker `place_order`
  (~5 LOC), return fee=_fee + venue_order_id from _observe_fill. Update
  Path C's _place_live row write (observer ~line 2728-ish) to ALSO stamp
  extra["broker_venue_order_id"] = fill.venue_order_id + extra["entry_fee_usd"]
  = fill.fee. Update _record_exit_outcome to ALSO stamp extra["exit_fee_usd"].
  Tests: tests/test_fill_event_fields.py.
  Source: ~30 LOC. Tests: ~50 LOC.

Commit 6 — insert_paper_trade_record DB-lock retry (Decision 6.2).
  Extend persistence/db.py:494-511 with retry-on-DB-lock per the
  _DB_LOCK_RETRY_DELAYS_SEC = (0.1, 0.3, 0.7) schedule reused from
  agents/logger.py. Mirror logger.py's retry-loop shape; expose via a
  `retry_db_locks: bool = False` kwarg to avoid breaking existing callers.
  Path C write site + _record_exit_outcome (both swallowed in Session A)
  may opt in to the retry by passing retry_db_locks=True.
  Tests: tests/test_db_insert_retry.py.
  Source: ~15 LOC. Tests: ~40 LOC.

Commit 7 — wire _record_exit_outcome into paper_trade_replay loop.
  Refactor _update_row call sites in _classify / _classify_v2_multi_leg
  (paper_trade_replay.py) to go through a callback that delegates to
  _record_exit_outcome OR _execute_live_exits based on row.extra.execution_mode.
  Paper-mode path stays byte-identical (the helper's paper-mode branch
  produces the same _update_row write); load-bearing test is a
  byte-identity pin per the N+1 commit-1 pattern.
  Live-mode path calls _execute_live_exits per leg (or for SL close-remainder).
  Tests: tests/test_paper_trade_replay_record_exit_outcome.py.
  Source: ~80 LOC (callback wiring). Tests: ~150 LOC.

Commit 8 — wire reconcile_position_state into main.py startup.
  Call reconcile_position_state(broker, db_url) at main.py startup BEFORE
  paper_trade_replay.replay_pending_paper_trades cron starts. If the
  result has divergences, the broker is already halt-latched by the
  reconciler — main.py just logs the result and proceeds (entries are
  blocked by the latch; exits keep working).
  Tests: tests/test_main_startup_reconciler.py.
  Source: ~30 LOC. Tests: ~80 LOC.

Commit 9 — Restart-resume cases (a) + (b) per Phase 1b §4.
  New async def _resume_live_positions(deps) called from main.py startup
  AFTER reconcile_position_state. Walk tracked rows + broker positions:
    - case (a) match: write restart_resume_executed audit + telegram
    - case (b) broker-orphan: write orphan_broker_position_on_restart
      audit + halt + elevated telegram
    - case (c) row-only: defer with restart_resume_case_c_deferred
      audit + halt + operator-page telegram
  Source: ~120 LOC. Tests: ~80 LOC.

Commit 10 — 8 operational alerts on BitunixLifecycleNotifier (Phase 1b §5).
  Add notify_exit_order_placed, notify_exit_order_filled,
  notify_exit_order_rejected, notify_exit_partial_fill,
  notify_position_closed_with_pnl (real fee on live branch),
  notify_reconciliation_divergence, notify_cost_accrual_recorded (stub),
  notify_restart_resume_executed. Constructor kwarg
  live_exit_counter_getter: Callable | None for the (live, exit #N/10)
  suffix; counter in agent_state key 'live_exits_executed'.
  Replace the free-form telegram strings _execute_live_exits emits today
  with calls to the canonical notifier methods.
  Source: ~250 LOC. Tests: ~150 LOC.

Test gate before pushing: full pre-existing test suite green per
[[branch-tests-must-cover-existing-fixtures]]; expected ~2400/28/3
after Session B's additions on top of Session A's baseline.

Output expected at EOS:
- 5-6 commits on bitunix-live-exit-path-impl-b-<date>; pushed; test gate green; STOP for operator merge approval.
- Memory: [[bitunix-live-exit-path-pattern]] update capturing Phase 3 patterns + premise corrections from both sessions.
- BACKLOG: P1 "N+2 Phase 3 implementation" — Session B complete; surface any new architectural questions.

Hard stops (carried from Session A):
- No prod-touching writes. Source + tests only.
- execution_mode stays "paper" in any committed config. Live-flip is operator-gated post-merge of Session A + Session B.
- No auto_execute flips on tasty or any other division.
- No refactoring of existing real-money pipelines (data_exec.place / risk gate / broker_fallback_to_paper) without explicit in-session operator approval.

Discipline standards (carried from Session A):
- Delegate mechanical tasks to Sonnet sub-agents.
- Stop-and-report at semantic forks.
- Surface anomalies with diagnostic detail.
- No scope expansion mid-task.
- Tighter commits than feels normal — ship per-commit, not at end.
- Worktree isolation.

Out of scope (deferred to N+3):
- Layer 2 funding accrual (get_history_positions + funding_paid_usd accrual)
- Restart-resume case (c) auto-resolve (covered in Commit 9 as halt-and-page only)
- 5s background sanity poll (60s Commit 8 startup-only check is the Stage-1 baseline)
- WS position channel (Stage-3 reuse audit)
```

---

## Notes for the operator before invoking the Session B prompt

1. **Re-verify §4 of Session A complete report** — Decision 6.2 (DB-lock retry) was deferred specifically because Session A's swallow paths didn't surface the issue. Session B's wiring (Commit 7) may exercise the helper at higher volume; the retry becomes load-bearing then.

2. **Decision: which `result_source` value applies when the replay-loop classifier produces a `still_open` verdict?** Session A's helper only handles the 3 terminal verdicts (win/loss/expired); `still_open` updates extra_json mid-walk (lifecycle SL transitions) without the helper. Session B's Commit 7 needs to decide whether the still_open path stamps a `result_source` or leaves it absent. Recommend: leave absent on still_open (the row hasn't resolved yet; the source applies only to the final resolution). Document in Commit 7.

3. **Premise check before starting Commit 8** — confirm `main.py` startup ordering: replay-loop cron and reconciler scheduling. If the existing cron starts inline at main, the reconciler must precede it. If startup uses a TaskGroup, the reconciler should be an explicit await BEFORE the TaskGroup. Quote the file:line evidence before scoping the Commit 8 hookup.

4. **If prod state is unstable at session start** (any MainPID restart > 0 in the last 24h, or healthz != 200), defer the merge of Session A + Session B until prod is healthy. The merge sequence is non-urgent; prod stability is the gating constraint.
