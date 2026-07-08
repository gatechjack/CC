# PMCC `risk_approved` lifecycle — STEP 2 fix design

**Date:** 2026-07-08
**Branch:** `pmcc-lifecycle-forensic-2026-07-08` (off `origin/main` `40cb16d`)
**Depends on:** `reports/2026-07-08_pmcc_risk_approved_forensic.md` (STEP 1 root cause).
**Status:** DESIGN ONLY — no code written, no tests run, no DB writes. **Gate: awaiting review.**
**Scope:** `robinhood_pmcc` only. Fence held (see §9 Adjacent findings).

---

## 0. Design at a glance

Root cause (STEP 1): `proposed_order.status` for HITL-gated PMCC orders is advanced out of
`risk_approved` **only** as a live side-effect of `_run_order`'s in-process resume loop, with no
external recovery. Two manifestations: **(A)** decision recorded in `audit_event` but the resume
never wrote it back (42 rows); **(B)** the up-to-1h wait was killed by a restart, thread left
suspended in the checkpointer with no boot recovery (17 rows).

The fix adds the missing **external recovery path** — one small new module,
`trading_corp/agents/pmcc_approval_reconciler.py`, exposing:
- **Fix A** — a periodic **audit-triggered reconciler** (recovers manifestation-A orphans).
- **Fix B** — a boot-time **checkpointer-thread recovery** (recovers manifestation-B orphans).
- **Canary** — a periodic self-check emitting `pmcc_orphan_detected` if either fix regresses.
- A shared idempotent writer `expire_pmcc_approval(...)` used by A, B, and (by documented parity) the backfill.

Plus: one live counter surface fixed (Telegram `/pending` → registry), two dead counter queries
deleted, and a standalone operator-authorized backfill for the existing residue.

### Shared thresholds (module constants, tunable, with rationale)
| constant | value | why |
|---|---|---|
| `APPROVAL_TIMEOUT_S` | 3600 | existing `registry.wait` timeout — the intended auto-reject deadline |
| `RECONCILE_GRACE_MIN` | 90 | timeout (60) + 30 buffer. A PMCC row still `risk_approved` past this is **definitively** orphaned — a legitimately-pending order would already have timed-out→`board_rejected` at 60 min, so recovery past 90 min can never race a live approval. |
| `RECONCILE_INTERVAL_S` | 300 | 5-min loop cadence (matches `_scheduled_pead_reconcile_loop`) |
| `CANARY_DETECT_MIN` | 180 | alarm threshold, set **above** grace + interval so a *healthy* (reconciled) system always reads zero; a row surviving to 3 h means the reconciler itself regressed |
| `RECONCILE_MIN_TS` | deploy date (e.g. `2026-07-08T00:00:00Z`) | cutoff so the **periodic** reconciler + canary act only on **post-deploy** orphans, leaving the pre-existing residue to the operator-authorized backfill (see §3 + §6) |

---

## ★ Open decisions for your review (forks — I recommend, you steer)

1. **Fix B write mechanism** — *recommend* **direct-write `board_rejected` + `adelete_thread`**
   (via the shared `expire_pmcc_approval`), over the literal "resume-to-rejected via
   `ainvoke(Command(resume=…))`" you sketched. Rationale in §2.3 — more robust (no dependency on
   the very resume that fails in manifestation A), DRY (one tested writer shared with Fix A),
   and testable without standing up a full graph resume. Same end-state + same invariant.
2. **Who cleans the existing 59** — *recommend*: boot-recovery (Fix B) resolves the **~17
   thread-carrying** orphans on the first post-deploy restart (it must run anyway to clear their
   suspended threads); the **~42 decision-recorded** orphans are owned by the operator-authorized
   **backfill**. Net still 59; the backfill dry-run will show ~42, not 59 (explained in §6). The
   periodic reconciler/canary use `RECONCILE_MIN_TS` so they don't pre-empt the backfill.
3. **Canary threshold** — *recommend* **180 min** (not the literal 90 you named), so a healthy
   system reads zero and the canary is a true regression tripwire, not a self-race with the
   90-min reconciler. Your "90 min" is honored as the *reconciler* action threshold.
4. **Optional checkpointer hardening** — adding `busy_timeout` to the `AsyncSqliteSaver`
   connection would *reduce* the db-lock race at its source (prevention, complementing recovery).
   It touches **shared** checkpointer infra used by all graph divisions → *recommend deferring*
   it out of this PMCC-scoped change (flagged in §9). The reconciler is the guarantee regardless.

---

## 1. Fix A — resume durability (periodic audit-triggered reconciler)

**Chosen mechanism:** an **audit-triggered sweep** run as a periodic background loop. It finds
PMCC rows stuck at `risk_approved` past `RECONCILE_GRACE_MIN` that **have a recorded
`board_decision_received` audit**, and writes the recorded terminal decision back to the row
(idempotent). This is the third of the three options you listed.

### Options considered + why this one
| option | verdict |
|---|---|
| **(3) audit-triggered sweep** ✅ chosen | **Most robust** — external recovery path (exactly what the root cause said was missing); survives restart, db-lock, and *any* resume failure; idempotent. **Most testable** — the test reproduces the post-race *state* (a `risk_approved` row + a `board_decision_received` audit), which is deterministic; it does **not** need to reproduce the non-deterministic asyncio+SQLite db-lock timing (flaky, a poor test target). Matches existing pattern (`bitunix_position_reconciler`, `_scheduled_pead_reconcile_loop`). Recovers-not-prevents (acceptable — the reconciler is the durability guarantee). |
| (1) retry-with-backoff on the resume `ainvoke` | Reduces but does **not** eliminate the race — a restart mid-retry still orphans; holds the sequential PMCC scan loop longer (blocks later orders in the batch); hard to test deterministically (must reproduce the lock). Rejected. |
| (2) write-status-before-`ainvoke` | Works cleanly only for the **reject** decision; unsafe for **approve** (can't pre-write `board_approved`/`filled` without actually executing); duplicates `end_rejected_node`'s write in the hot path; leaves manifestation **B** (restart-cancel) entirely unaddressed. Rejected. |

### Mechanism
`reconcile_pmcc_approvals(db_url, logger, saver, now, min_ts=RECONCILE_MIN_TS) -> int`:
1. `SELECT … FROM proposed_order WHERE strategy='robinhood_pmcc' AND status='risk_approved'
   AND ts >= :min_ts AND ts < :now - RECONCILE_GRACE_MIN`.
2. For each row, look up the latest `board_decision_received` audit for `order_id`
   (`audit_event WHERE actor='hitl' AND kind='board_decision_received' AND
   json_extract(payload_json,'$.order_id')=?`). If present → `expire_pmcc_approval(order_id,
   decision=<from audit>, reason="recovered from recorded board decision (…source)",
   cause="A_resume_failed", …)`.
3. Rows with **no** decision audit are skipped here (they belong to Fix B / the canary), unless
   age > `APPROVAL_TIMEOUT_S` with no thread — see §2.4 for that belt-and-suspenders overlap.

Runs each `RECONCILE_INTERVAL_S` via `run_pmcc_approval_reconcile_loop` (PEAD loop shape:
`while True: try: reconcile → canary → sleep(interval); except CancelledError: return;
except Exception: log.exception + sleep(interval)`). Audit-on-action only.

### Tests (to be written in STEP 3 — described, not written)
- **Reproduce the orphan:** seed a `robinhood_pmcc` `risk_approved` row (ts old enough,
  ≥`RECONCILE_MIN_TS`) + a `board_decision_received` audit (source=`timeout`, decision=`reject`);
  run `reconcile_pmcc_approvals`; **assert** row → `board_rejected` with `board_reason` set, a
  `board`/`board_rejected` audit and a `pmcc_orphan_recovered` (cause=A) audit written.
- **Idempotent:** re-run → 0 changed (guarded by `WHERE status='risk_approved'`).
- **No false positives:** a fresh row (age < grace), a non-pmcc row, and a row with no decision
  audit are all left untouched.
- (Note: reproducing the literal checkpointer db-lock race deterministically is infeasible; the
  test targets the *state* the race leaves, which is what the fix consumes.)

---

## 2. Fix B — boot recovery (checkpointer-thread orphans)

### Invariant (stated plainly)
> **After boot recovery completes, no LangGraph thread for a `robinhood_pmcc` order whose
> elapsed wait exceeds `APPROVAL_TIMEOUT_S` remains suspended: every such thread is expired to a
> terminal status and its `proposed_order` row is `board_rejected` (and its checkpointer thread
> cleared).**

### Mechanism
`recover_orphaned_pmcc_threads_on_boot(db_url, logger, saver, now) -> int`, run **once on boot,
inside the `async with make_checkpointer(...)` block, after the graph is built and BEFORE the
scheduler starts new approval waits** (so it never races a fresh interrupt):
1. `async for tup in saver.alist(None)`: collect `thread_id`s whose `tup.pending_writes` contains
   a `channel == "__interrupt__"` entry (i.e. suspended at the approval gate). `thread_id ==
   order.id`.
2. Cross-reference each against `proposed_order WHERE id=thread_id AND
   strategy='robinhood_pmcc' AND status='risk_approved'` and age > `APPROVAL_TIMEOUT_S`.
3. For each match → `expire_pmcc_approval(order_id, decision=BoardDecision(reject, "approval
   window expired — boot recovery"), cause="B_boot_expiry", saver=saver)` which writes
   `board_rejected` + audits **and** `await saver.adelete_thread(order_id)` to clear the thread.
4. Return the count; wrap the whole call in try/except at the boot site (a recovery failure must
   not crash boot).

**No `RECONCILE_MIN_TS` cutoff on boot-recovery** — it must clear *all* suspended-past-timeout
threads, including the pre-existing 17, to satisfy the invariant (they'd otherwise linger in the
checkpointer forever; the standalone SQL backfill cannot clear checkpointer threads).

### 2.3 Why direct-write + `adelete_thread` over resume-to-rejected (fork #1)
- **Robust:** doesn't depend on `ainvoke(Command(resume=…))` succeeding — the same resume that
  fails under contention in manifestation A. (On boot it's contention-free so resume *would*
  likely work, but not depending on it is strictly safer.)
- **DRY / one writer:** shares `expire_pmcc_approval` with Fix A → a single tested status-write
  path, identical row state from every recovery source.
- **Testable:** the test seeds a suspended thread + row and asserts recovery, with no need to
  drive a full compiled-graph resume.
- **Alternative (resume-to-rejected)** reuses `end_rejected_node` as the single status writer
  (a real virtue) but adds the compiled-graph dependency to the boot path and is harder to test.
  End-state + invariant are identical. **I'll implement resume-to-rejected instead if you prefer.**

### 2.4 A/B overlap (deliberate, safe)
Both A and B funnel through `expire_pmcc_approval`, which no-ops if `status != 'risk_approved'`.
Boot-recovery may catch some manifestation-A rows too (if their threads are still suspended); the
periodic reconciler may catch a B-row that has a late decision audit. Order-independent and
idempotent by the status guard — no double-write.

### Tests
- **Reproduce restart-during-wait:** seed an `AsyncSqliteSaver` (temp file) with a thread
  suspended at `__interrupt__` (thread_id=order.id) + a matching `risk_approved` row aged past
  timeout, with **no** decision audit (the B signature); open a *fresh* saver over the same file
  (simulating restart); run boot-recovery; **assert** row → `board_rejected` + audit, and the
  thread is gone (`aget_tuple` returns None / no `__interrupt__`).
- **Invariant:** after recovery, enumerate threads → none suspended past timeout for pmcc.
- **No false positive:** a thread whose wait < timeout is left suspended; a non-pmcc thread untouched.
- **Idempotent:** second boot-recovery → 0.

---

## 3. Backfill script (existing residue, operator-authorized)

**File:** `deploy/2026-07-08_pmcc_lifecycle_fix/backfill_pmcc_risk_approved.py` — **standalone**
(not part of the lifecycle-fix deploy, not imported by the engine), `argparse`.

- **Dry-run (default, no flag):** `SELECT id, ts, symbol, side, strategy, status FROM
  proposed_order WHERE strategy='robinhood_pmcc' AND status='risk_approved' AND ts < :threshold`
  → prints the exact rows + per-row **cause label** (A if a `board_decision_received` audit
  exists for the id, else B) + the count. **Changes nothing.**
- **Commit (`--commit`, operator authorizes):** per row, `UPDATE proposed_order SET
  status='board_rejected', board_reason='orphan backfill 2026-07-08 (cause=<A|B>) — approval
  never resumed' WHERE id=? AND status='risk_approved'` (idempotent guard), then a
  `pmcc_orphan_backfilled` audit per row (cause + evidence: the decision-audit id for A, or
  "no decision recorded" for B). Terminal status = **`board_rejected`** for all (your steer #1).
- **Threshold:** `ts < now - RECONCILE_GRACE_MIN` (never touches a legitimately-pending fresh
  order; consistent with the reconciler). Passable as `--before` for determinism.
- **Idempotent:** re-run → 0 rows (all now `board_rejected`; the `status='risk_approved'` guard).
- **Reversible:** the commit run writes the changed ids to
  `deploy/2026-07-08_pmcc_lifecycle_fix/backfilled_ids.txt`; rollback = `UPDATE … SET
  status='risk_approved', board_reason=NULL WHERE id IN (<those ids>) AND status='board_rejected'
  AND board_reason LIKE 'orphan backfill 2026-07-08%'` (marker-guarded, so it can only revert
  rows this backfill touched).
- **Parity, not reuse:** the UPDATE reproduces `expire_pmcc_approval`'s row state (same status +
  board_reason shape + audit) but is **self-contained SQL** so the operator can read exactly what
  runs before authorizing. Parity is a documented requirement + a test asserts it.

### Test
Seed 3 pmcc `risk_approved` rows (2 with decision audit = A, 1 without = B) + 1 fresh (age <
threshold) + 1 non-pmcc; dry-run → reports the 3 with correct A/B labels, changes nothing; commit
→ the 3 → `board_rejected` with correct labels, fresh + non-pmcc untouched; re-run → 0.

---

## 4. Counter fixes (three named surfaces — evidence changed the shape)

Following the `7f641d8` pattern (source the registry, don't invent a query mechanism). The
mapping found that two of the three surfaces are **already-orphaned dead code** post-`7f641d8`:

| surface | current | change | note |
|---|---|---|---|
| **S1** `_query_pending_approvals` (`web/data.py:1167`) | DB `WHERE status='risk_approved'` | **DELETE the function** | `7f641d8` replaced its only caller with `hitl_activity_24h(…, pending_registry=…)`; zero runtime callers remain. Removing it is the correct-by-construction defense (no query → no misread). |
| **S2** Telegram `/pending` (`comms/telegram_commands.py:327`) | DB `WHERE status='risk_approved' LIMIT 25` | **registry-source:** replace the DB block with `entries = self.deps.pending_registry.list_pending()`; render from `entry.request.summary` + `entry.added_at`; count = `len(entries)`; drop the `logger_agent is None` guard + the `db` import | `self.deps.pending_registry` is wired (main.py:881 → `TelegramCommands`). Same shape as the stat-card fix. The **only live misreading surface.** |
| **S3** `_query_open_orders` (`web/data.py:1157`) | DB `WHERE status IN ('proposed','risk_approved','board_approved')` | **DELETE the function + its `build_command_center` gather call + the discarded local** | Its result is computed then **thrown away** (never stored on the snapshot). It is a **3-status in-flight** query, **not** registry-equivalent — converting to `list_pending()` would be semantically wrong (drops `proposed`+`board_approved`). Since it's dead, delete it. |

**Verification guard (STEP 3):** re-grep to confirm S1/S3 have no dynamic callers before deleting;
a small test asserts `/pending` returns the registry list (empty even when DB holds
`risk_approved` residue).

Defense-in-depth: after the backfill the DB residue is gone, but deleting the residue-reading
queries makes the counters correct-by-construction against any *future* state-lag too.

---

## 5. Canary / self-monitor

`pmcc_orphan_canary(db_url, logger, now, min_ts=RECONCILE_MIN_TS) -> int`, run each loop tick
(after the reconcile step, in its own try/except) **and** once on boot:
- `SELECT COUNT(*) FROM proposed_order WHERE strategy='robinhood_pmcc' AND status='risk_approved'
  AND ts >= :min_ts AND ts < :now - CANARY_DETECT_MIN`.
- If `> 0` → `logger.log_event(actor="pmcc_reconciler", kind="pmcc_orphan_detected",
  payload={"count": n, "oldest_ts": …, "threshold_min": CANARY_DETECT_MIN, "strategy":
  "robinhood_pmcc", "division": "robinhood_pmcc"})` — mirrors the `sfp_skip_regime_warmup`
  `_audit` shape (actor+kind+enriched payload, try/except swallow, never raises).
- It **detects/alarms only** (the reconciler actuates). In a healthy system it always reads 0
  (reconciler acts at 90 min; canary alarms at 180 min → nothing survives). A non-zero
  `pmcc_orphan_detected` means the reconciler regressed or a new failure mode appeared — the
  signal that turns "we fixed it" into "we know it stays fixed."
- `min_ts` scoping prevents false alarms on the pre-existing residue during the fix→backfill window.

### Test
Seed a pmcc `risk_approved` row older than `CANARY_DETECT_MIN` (ts ≥ min_ts) → assert
`pmcc_orphan_detected` emitted with the right count; seed none → assert no emission.

---

## 6. Blast radius, restart, deploy order

### Files
**New:**
- `trading_corp/agents/pmcc_approval_reconciler.py` — Fix A + Fix B + canary + `expire_pmcc_approval` + loop.
- `deploy/2026-07-08_pmcc_lifecycle_fix/backfill_pmcc_risk_approved.py` — standalone backfill.
- `tests/test_pmcc_approval_reconciler.py` (+ backfill/counter tests per repo test layout).

**Edited (minimal, additive/removal):**
- `trading_corp/main.py` — (a) `await recover_orphaned_pmcc_threads_on_boot(...)` inside the
  checkpointer block (~after 1091, before scheduler ~1162); (b)
  `asyncio.create_task(run_pmcc_approval_reconcile_loop(...), name="pmcc-approval-reconcile")`
  alongside the other loop tasks. Both additive hunks.
- `trading_corp/comms/telegram_commands.py` — S2 `/pending` → registry.
- `trading_corp/web/data.py` — delete S1 `_query_pending_approvals` + S3 `_query_open_orders` + its gather call/local.

### Restart
**Required.** The reconciler/boot-recovery/canary are new code wired in `main.py`, and the
`telegram_commands`/`data.py` edits are Python (not templates). Per the guardrail, the fix is
**not preconditioned on the RH pickle**; flag the restart, operator schedules it (flat-guard the
live divisions — PMCC is paper, but the restart affects the whole engine). The two standing
passive verifications (futures BE, SFP A2, SL-trail) are bitunix-only and unaffected — this
change touches no bitunix code and only `robinhood_pmcc` rows.

### Order of operations (STEP 4)
1. Targeted-hunk deploy the 3 edited files + new module (drift-gate vs post-reconciliation main
   baseline; per-file md5; `.bak-pre-pmcc-fix-2026-07-08`).
2. Operator-timed restart. **Boot-recovery runs → resolves the ~17 thread-carrying (B) orphans**
   (row `board_rejected` + threads cleared). Verify: reconcile-loop task present; boot-recovery
   audit(s); `/pending` reads registry; live divisions healthy; no boot errors.
3. **Backfill dry-run** (SELECT) → shows the remaining **~42** (A) orphans → operator authorizes
   → `--commit` → they → `board_rejected`.
4. Verify: `COUNT(*) risk_approved pmcc` → **0**; `/pending` → 0; `pmcc_orphan_canary` → 0; PMCC
   still scans + proposes normally (healthy path intact).

> The backfill dry-run showing ~42 (not 59) is **expected** — boot-recovery already cleaned the
> ~17 thread-carrying orphans (fork #2). The exact split is empirical; the dry-run reports the
> true remainder. `17 + 42 = 59`.

---

## 7. Rollback
- **Code:** `.bak-pre-pmcc-fix-2026-07-08` per edited file (main.py, telegram_commands.py,
  data.py) on prod; the new module is deleted on rollback; restart restores prior behavior.
- **Backfill:** reversible via the marker-guarded inverse UPDATE over `backfilled_ids.txt` (§3).
- Boot-recovery's row writes are also `board_rejected` with a distinct `board_reason` marker, so
  they're identifiable and reversible by the same inverse pattern if ever needed.

---

## 8. Scope discipline check
**Touches ONLY:** the new `pmcc_approval_reconciler` module, additive `main.py` wiring, the
Telegram `/pending` handler (S2), dead-query removal in `web/data.py` (S1+S3), the standalone
backfill, and tests. **Every query is filtered `strategy='robinhood_pmcc'`.**

**Does NOT touch:** `ceo_graph.py`, `interrupts.py`, `pending_registry.py`, `_run_order`, the
shared `checkpointer.py`, or any other division's lifecycle/reconciler. No bitunix/kalshi/
polymarket/PEAD/fidelity code. The three standing passive verifications are undisturbed.

---

## 9. Adjacent findings (flagged, NOT acted on — fence held)
1. **The coupling is division-agnostic** (the "bigger than PMCC" finding). `fidelity_joint` and
   the demo order share the same `_run_order` → `interrupt()` path and orphan identically; only
   PMCC exposes it at volume via the daily scan + 1h wait. The reconciler filters to
   `robinhood_pmcc`; widening the filter (or lifting it to a graph-wide approval reconciler)
   would cover them. **Future work — to be captured as an end-of-session memory entry per your
   instruction; not built this session.**
2. **`pending_registry.py:6-8` docstring is stale** — it claims `_run_order` calls
   `registry.wait`; the live code calls `channel.request_approval` (which *then* calls
   `registry.wait`). Doc-only; not touched.
3. **Checkpointer hardening** (`busy_timeout` / a separate saver DB file) would reduce the
   db-lock race at its source — shared infra, deferred (fork #4).
4. **`dry_run_skipped`** and other non-enum statuses (`would_have_placed`, `placing`) exist in the
   DB from *other* strategies — out of scope, noted for schema-comment accuracy only.
