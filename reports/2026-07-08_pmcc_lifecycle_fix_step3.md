# PMCC `risk_approved` lifecycle — STEP 3 implementation + tests + deploy plan

**Date:** 2026-07-08
**Branch:** `pmcc-lifecycle-forensic-2026-07-08` (off `origin/main` `40cb16d`); impl commit `7bf6964`.
**Status:** Built + tested. **Gate: awaiting deploy authorization (STEP 4).** No prod writes;
all prod access read-only (`md5sum`, `sqlite3 -readonly`, `systemctl show`).
**Depends on:** STEP 1 forensic + STEP 2 design (same branch). Fork calls #1–#4 + the audit-kind
refinements implemented as locked.

---

## 1. What was built (all scoped strictly to `strategy='robinhood_pmcc'`)

**New — `trading_corp/agents/pmcc_approval_reconciler.py`** (the external recovery path):
- `expire_pmcc_approval()` — shared idempotent writer: loads the row, guards
  `status=='risk_approved'` (no-op otherwise), writes `board_rejected` + `board_reason`
  (mirrors `end_rejected_node`), emits a parity `board/board_rejected` audit **and** a
  manifestation-specific recovery audit (distinct kind + cause). Refuses any non-pmcc row.
- **Fix A** `reconcile_pmcc_approvals()` — periodic audit-triggered sweep for manifestation A
  (a `board_decision_received` **reject** the resume never wrote back) →
  `pmcc_orphan_reconciler_recovered` / cause `decision_recorded_status_stuck`. Reject-only
  (never auto-board_rejects a recorded *approve*); cutoff-scoped (`RECONCILE_MIN_TS`) so it
  can't pre-empt the authorized backfill.
- **Fix B** `recover_orphaned_pmcc_threads_on_boot()` — boot checkpointer-thread recovery for
  manifestation B (suspended thread, no recorded decision) → direct-write `board_rejected` +
  `adelete_thread` → `pmcc_orphan_boot_recovered` / cause `wait_cancelled_by_restart`. Logs
  id + strategy + wait-elapsed + thread-cleared per row (Board steer). No cutoff (must clear
  pre-existing suspended threads). Excludes decision-recorded rows (label integrity).
- **Canary** `pmcc_orphan_canary()` — emits `pmcc_orphan_detected` iff any post-cutoff row is
  still `risk_approved` past 180 min (a healthy, reconciled-at-90 system always reads 0).
- `run_pmcc_approval_reconcile_loop()` — reconcile → canary loop (PEAD-loop shape).

**Edited (minimal):**
- `trading_corp/main.py` — boot-recovery one-shot + reconcile-loop task, wired **inside the
  `make_checkpointer` block, before the scheduler starts new approval waits** (so every
  suspended thread it sees is provably orphaned). Additive; wrapped so it never crashes boot.
- `trading_corp/comms/telegram_commands.py` — **S2:** `/pending` now reads
  `deps.pending_registry.list_pending()` (live waits) instead of counting `risk_approved`
  DB residue. Mirrors the `7f641d8` stat-card fix.
- `trading_corp/web/data.py` — **S1+S3:** deleted the dead `_query_pending_approvals` (0
  callers post-`7f641d8`) and `_query_open_orders` (result was discarded) + its gather call.

**New — `deploy/2026-07-08_pmcc_lifecycle_fix/backfill_pmcc_risk_approved.py`** — standalone
backfill: dry-run (default) → commit (`--commit`); per-row A/B cause; idempotent; reversible
(writes touched ids + prints the marker-guarded inverse UPDATE). Not engine-loaded; parity
(not reuse) with `expire_pmcc_approval`, asserted by a test.

**New — `tests/test_pmcc_approval_reconciler.py`** — 13 tests (below).

---

## 2. Tests — GREEN

`PYTHONPATH=. python -m pytest tests/test_pmcc_approval_reconciler.py` → **13 passed.**
- **Fix A:** reproduces the post-race state (row `risk_approved` + a `board_decision_received`
  reject audit) → reconciler writes `board_rejected` + `pmcc_orphan_reconciler_recovered`;
  idempotent; **no false positives** (fresh / no-decision / non-pmcc / recorded-approve all
  left untouched); respects the cutoff.
- **Fix B:** drives the REAL graph (`build_trade_graph` + `AsyncSqliteSaver`) to the approval
  interrupt, simulates a restart (fresh saver over the same checkpoint file), runs
  boot-recovery → `board_rejected` + thread cleared (`aget_tuple` → None) +
  `pmcc_orphan_boot_recovered`; idempotent; skips decision-recorded rows (label integrity).
- **Canary:** emits `pmcc_orphan_detected` past 180 min; silent within grace.
- **Shared writer:** scope guard (refuses non-pmcc); idempotent on a terminal row.
- **Backfill:** dry-run reports 3 (2 A / 1 B), changes nothing → commit updates exactly those
  (fresh + non-pmcc untouched) with `pmcc_orphan_backfilled` → idempotent re-commit; +
  constants-parity with the module.
- **S2 counter:** `/pending` reads the registry — reports "no pending" even with DB residue
  present, and renders a live registry entry.

**No regressions from this change:** adjacent suites green — `test_graph_hitl`,
`test_pending_registry`, `test_approvals_routes`, `test_combo_approval`,
`test_slim_approval_notification`, `test_pmcc_logic`, `test_polymarket_analyze_route`
(**165 passed**); `test_boot_smoke` **7 passed** apart from ONE pre-existing stale assertion
(`test_two_state_sfp_comes_up_trading_and_replay_disabled` expects
`bitunix_futures.mode==halted`, but origin/main ships it `execution_mode: live` since the
2026-06-30 futures go-live). **That file is untouched by this branch** (`git status` confirms
strategies.yaml is not in the changeset) — flagged as adjacent (§6), not fixed here.

---

## 3. Blast radius

| file | change | prod today | deploy target (HEAD blob) | EOL |
|---|---|---|---|---|
| `trading_corp/main.py` | +boot-recovery + loop wiring (additive) | `d0d382cb` (== origin/main) | `5a5eb7b5` | **CRLF** |
| `trading_corp/comms/telegram_commands.py` | `/pending` → registry | `3b31baba` (== origin/main) | `654c218a` | LF |
| `trading_corp/web/data.py` | delete 2 dead queries | `6eeda43b` (== origin/main) | `bac9fe54` | LF |
| `trading_corp/agents/pmcc_approval_reconciler.py` | NEW | ABSENT | `e8d8fc7c` | LF |
| `deploy/2026-07-08_pmcc_lifecycle_fix/backfill_pmcc_risk_approved.py` | NEW (standalone) | ABSENT | `182d4f08` | LF |

**Baseline verified (read-only):** prod == origin/main for all 3 edited files (main.py raw
CRLF blob `d0d382cb` matches prod; the other two LF blobs match). New files are pure adds.
So my additive hunks apply to a known-clean, post-reconciliation baseline — no drift
reintroduced. My committed main.py blob is **CRLF** (4886 CRLF / 0 bare-LF), preserving the
reconciled prod EOL convention (no autocrlf de-CRLF slipped in).

---

## 4. Deploy plan (STEP 4 — operator runs the writes)

**Restart REQUIRED** (new code + main.py wiring; the `telegram`/`data.py` edits are Python,
not templates). Flagged, operator-timed, **NOT preconditioned on the RH pickle**. PMCC is
paper, but the restart affects the whole engine → flat-guard the live divisions as usual.

Order (fix live + verified FIRST, then backfill — so new orphans stop before old ones clear):
1. On prod, `.bak-pre-pmcc-fix-2026-07-08` for the 3 edited files.
2. **Gate A** — re-confirm prod md5 == baseline (`d0d382cb` / `3b31baba` / `6eeda43b`).
3. Stage the 3 edited + the new module via **tar-over-ssh** (correct EOL: main.py CRLF,
   others LF — NOT scp-of-smudged, per the reconciliation autocrlf note).
4. **Gate B** — staged md5 == target (`5a5eb7b5` / `654c218a` / `bac9fe54` / `e8d8fc7c`);
   `py_compile` the 4 py files.
5. atomic-mv into place; restart (operator-timed, flat-guarded).
6. **Verify (STEP 5):** boot-recovery audit `pmcc_orphan_boot_recovered` for the thread-carrying
   orphans (~17); `pmcc-approval-reconcile` task present; canary reads 0; `/pending` +
   Overview stat card read the registry; no boot errors; **bitunix/kalshi/polymarket/PEAD
   divisions unaffected** and the 3 standing passive verifications undisturbed.
7. Stage the backfill on prod; **dry-run** (`--db-url sqlite:////home/azureuser/trading_corp/data/trading_corp.db`)
   → reports the remaining (~42) A-orphans → **operator authorizes** → `--commit` → they →
   `board_rejected` (+ `pmcc_orphan_backfilled` audits + `backfilled_ids.txt`).
8. **Final verify:** `COUNT(*) risk_approved robinhood_pmcc` → **0**; counters → 0; canary → 0;
   PMCC still scans/proposes normally.

> The backfill dry-run showing ~42 (not 59) is **expected**: boot-recovery already cleared the
> ~17 thread-carrying (B) orphans on the restart. `17 + 42 = 59`. Exact split is empirical —
> the dry-run is the source of truth.

---

## 5. Rollback
- **Code:** restore the 3 `.bak-pre-pmcc-fix-2026-07-08` files + delete the new module; restart
  → full revert (the recovery is external, so removing it simply stops recovering).
- **Backfill:** marker-guarded inverse (the script prints it):
  `UPDATE proposed_order SET status='risk_approved', board_reason=NULL WHERE
  strategy='robinhood_pmcc' AND status='board_rejected' AND board_reason LIKE
  'orphan backfill 2026-07-08%';` (boot-recovery's writes carry a distinct `board_reason`, so
  they are separately identifiable/reversible if ever needed).

---

## 6. Scope discipline check + adjacent findings
**Touches ONLY:** the new reconciler module, additive main.py wiring, `/pending` (S2), the
dead-query removal in `web/data.py` (S1+S3), the standalone backfill, and its tests. Every
query filters `strategy='robinhood_pmcc'`. **Untouched:** `ceo_graph.py`, `interrupts.py`,
`pending_registry.py`, `_run_order`, `checkpointer.py` (shared HITL infra), all config yamls,
and every non-PMCC division. The 3 standing passive verifications (futures BE, SFP A2,
SL-trail) are bitunix-only and undisturbed.

**Adjacent findings (flagged, NOT acted on — fence held):**
1. The coupling is **division-agnostic** — `fidelity_joint` + the demo order share the same
   `_run_order`/`interrupt()` path and would orphan identically. The reconciler filters to
   `robinhood_pmcc`. → end-of-session memory + future-work, not this session.
2. **Pre-existing stale boot-smoke test** `test_two_state_sfp_comes_up_trading_and_replay_disabled`
   asserts `bitunix_futures.mode==halted` but origin/main ships it live (2026-06-30). Separate
   cleanup; not in scope.
3. `pending_registry.py:6-8` docstring is stale (claims `_run_order` calls `registry.wait`).
   Doc-only; untouched.
4. Checkpointer `busy_timeout` / separate saver file (fork #4) — shared infra, deferred.

---

## 7. Expected prod state post-deploy
0 stale `risk_approved` PMCC rows; `/pending` + the stat card read 0; the reconcile loop +
canary run (canary always 0 when healthy); no live behavior change for active PMCC scanning
(the healthy propose→approve/reject path is untouched — tests confirm no false positives on
fresh / decisionless / non-pmcc / recorded-approve rows); new orphans self-heal (A within
~5 min, B at the next boot) and any regression trips `pmcc_orphan_detected`.
