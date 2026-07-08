# PMCC `risk_approved` lifecycle leak — STEP 1 forensic report

**Date:** 2026-07-08
**Branch:** `pmcc-lifecycle-forensic-2026-07-08` (off `origin/main` `40cb16d`)
**Scope:** `robinhood_pmcc` only. Read-only forensics (prod DB `sqlite3 -readonly` + code on the branch).
**Status:** STEP 1 complete — root cause identified with evidence. **No fix designed yet (gate).**
**Engine at start:** PID 108070, NRestarts=0, active/running, up since 2026-07-08 02:10:35 UTC.

Supersedes the hedged hypothesis in `reports/2026-07-07_approvals_count_split_brain.md`
("any path that places/decides without writing that status back … plus any row from a
prior process lifetime"). That report was correct that the count reads DB residue; this
report pins the *specific* PMCC mechanism with per-row audit evidence.

---

## 0. Executive summary

The **59** stuck rows are verified exactly (`proposed_order WHERE strategy='robinhood_pmcc'
AND status='risk_approved'` = 59). They are **paper**, harmless to trading (no position, no
order ever reached a broker), and accumulate at **~0.8/day, still growing** (newest 2026-07-07).

**Root cause (single defect, two surface manifestations):** a PMCC order's `proposed_order.status`
is advanced out of `risk_approved` **only** as a live side-effect of the in-process
`_run_order` orchestration loop completing a `graph.ainvoke(Command(resume=…))` that runs a
board node. **Nothing else ever writes a terminal status back to the row** — no reconciler,
no boot recovery, no sweep (confirmed: zero `UPDATE proposed_order` statements exist in the
codebase). That coupling is non-durable and breaks in two ways, **both** stranding the row:

- **(A) Resume-after-decision fails — 42 rows.** `registry.wait` returns a decision (a
  1-hour timeout-reject, or a real web/Telegram decision) and writes `board_decision_received`
  to `audit_event`, but the subsequent graph resume never persists the terminal status
  (checkpointer `database is locked` collision — documented at `main.py:1097` — or the process
  dying/erroring in the window after the decision-audit but before the resume write). The
  decision exists in the audit log; the row never learns of it.
- **(B) Wait cancelled by restart, no recovery — 17 rows.** The engine restarts during the
  up-to-1-hour approval wait; the awaiting coroutine dies (`CancelledError`, so no timeout
  audit); the suspended LangGraph thread persists in the checkpointer but nothing on the next
  boot re-resumes or expires it (the acknowledged "B-v2 recovery gap", `pending_registry.py:20-25`).

Evidence for both: **`board`-actor board-node write count for the 59 = 0** (no `execute_node`/
`end_rejected_node` ever ran to completion for any of them).

---

## 1. Characterization of the 59 (STEP 1.1)

### 1.1 Count — verified
`SELECT COUNT(*) … strategy='robinhood_pmcc' AND status='risk_approved'` → **59**.

### 1.2 `robinhood_pmcc` status matrix (context — the lifecycle DOES work for most rows)
| status | n | oldest | newest |
|---|---:|---|---|
| board_rejected | 115 | 2026-05-08 | 2026-07-07 |
| **risk_approved (stuck)** | **59** | **2026-05-01** | **2026-07-07** |
| risk_rejected | 15 | 2026-05-02 | 2026-07-07 |
| filled | 11 | 2026-05-01 | 2026-07-07 |
| board_approved | 3 | 2026-05-08 | 2026-05-21 |

Rows *do* advance — 115 reach `board_rejected`, 11 `filled`. The 59 are a leak interspersed
across the whole window, **not** a one-time incident.

### 1.3 Distribution by symbol/side/type
Spread across ~15 symbols, both legs, all `limit`: ASTS 16 (11 sell / 5 buy), CIFR 11, STRC 6,
OPEN 6, HOOD 3, RIOT 5, RKLB 3, plus BLSH/IREN/BULL/MSTR/SMR/TSLA. No single-symbol clustering
→ consistent with a steady per-scan lifecycle leak, not a bad symbol.

### 1.4 Temporal distribution — steady drip, still growing
Present on ~37 distinct trading days from 2026-05-01 → 2026-07-07, ~1–4/day. Not bimodal, not
restart-clustered. `ts` is the single row timestamp (schema has **no** `updated_at`); `fill_ts`
is NULL on all 59.

### 1.5 State fields on the 59
| field | value on all 59 |
|---|---|
| execution_mode | `paper` (59/59) |
| risk_reason | **set** (59/59) — they passed risk |
| board_reason | **NULL** (59/59) — never got a board decision written to the row |
| fill_price / fill_ts | **NULL** (59/59) — never filled, never submitted |

→ Definitively: they die **after** `risk_node` (which wrote `risk_approved` + `risk_reason`)
and **before** any board-decision node.

---

## 2. Schema & lifecycle intent (STEP 1.2)

### 2.1 Schema
`proposed_order` (prod): `id, ts, strategy, symbol, side, qty, order_type, limit_price,
rationale, status, risk_reason, board_reason, fill_price, fill_ts, extra_json, execution_mode`.
PK `id`; index `ix_proposed_order_status(status)`. **No `division` column** — `strategy` holds
`robinhood_pmcc`. **No `created_at`/`updated_at`** — only `ts`.

### 2.2 Status enum
Schema comment (`db.py:59`) + Python `OrderStatus` literal (`models.py:12-20`):
`proposed | risk_approved | risk_rejected | board_approved | board_rejected | filled | cancelled`.
No `expired`/`superseded`/`stale` exist. (The DB does contain non-PMCC extra values written by
other strategies — `would_have_placed`, `placing`, `proposed`, and `dry_run_skipped` from
`data_exec.py:155` — but PMCC only ever uses the 7 enum values.)

### 2.3 The state machine (`trading_corp/graph/ceo_graph.py`, compiled with a checkpointer)
```
START → risk → {approve|resize → approval ; reject → end_rejected}
approval → {approve → execute ; reject/other → end_rejected ; modify → modify_then_risk}
execute → END        end_rejected → END
```
The **only** persistence writer is `LoggerAgent.log_proposed_order` (INSERT-OR-REPLACE, keyed
on `id`). Status transitions (all full-row rewrites of the same `id`):
- `risk_node` (`ceo_graph.py:361-362`) → writes `risk_approved` (or `risk_rejected`).
- `execute_node` (`ceo_graph.py:499`) → `board_approved`; then `data_exec.place()` → `filled`
  (`data_exec.py:213`); on exception → `cancelled` (`ceo_graph.py:517`).
- `end_rejected_node` (`ceo_graph.py:529`) → `board_rejected`.

### 2.4 What SHOULD advance a `risk_approved` PMCC row
The PMCC scheduled scan (`main.py:787-826`) routes each order through `_run_order`
(`main.py:4327`):
```python
result = await graph.ainvoke(state, config={thread_id: order.id})  # risk→approval→interrupt() suspends; row=risk_approved
while interrupts:
    decision = await channel.request_approval(req)                 # BLOCKS up to 3600s
    result   = await graph.ainvoke(Command(resume={…decision…}))   # resume → execute|end_rejected → terminal status
```
`channel.request_approval` (`telegram_bot.py:377`) delegates to `pending_registry.wait(req,
timeout_s=3600)` (`pending_registry.py:86`). On a **1-hour timeout** it writes
`board_decision_received` (source=`timeout`) and returns a synthetic **reject**
(`pending_registry.py:135-147`) → the resume runs `end_rejected_node` → `board_rejected`.

**So the normal fate of an un-approved PMCC order is `board_rejected` after ~1h — that is the
115.** A row can only remain `risk_approved` if the loop above fails to complete the resume.

**Terminal states at rest:** completed = `filled` (fill_price/fill_ts set); board-declined =
`board_rejected` (board_reason set); risk-declined = `risk_rejected`. **There is no timeout/
expiry/sweep path that advances a row independently of the live `_run_order` coroutine.**

---

## 3. Stuck vs healthy (STEP 1.3) + failed-advancement artifacts (STEP 1.4)

### 3.1 Audit correlation of the 59 (via `audit_event`, `actor='hitl'`/`'board'`)
| metric | value |
|---|---:|
| board-node write (`execute`/`end_rejected`) for any of the 59 | **0 / 59** |
| have `board_decision_received` (source=`timeout`, decision=`reject`) | 40 |
| have `board_decision_received` (source ≠ timeout: web/telegram/cli) | 2 |
| have **no** `board_decision_received` audit | 17 |
| (40 + 2 + 17 = 59 ✓) | |

Per-row trail (sample, newest first): `po_ts` → `decided_ts` gap is ~1h to ~10h, always
`source=timeout, decision=reject, board-node=0`. E.g. `23c66330` proposed 2026-07-07 12:35:29,
decided (timeout-reject) 13:35:38, **board-node writes: 0**. The variable >1h gaps fit
event-loop-freeze / checkpointer contention delaying `asyncio.wait_for`.

### 3.2 Healthy path, by contrast
- **115 `board_rejected`**: same interrupt, but the resume *completed* — mostly the 1h
  timeout-reject firing cleanly (`hitl board_decision_received` source=`timeout` →
  `board end_rejected` → row `board_rejected`).
- **11 `filled` / 3 `board_approved`**: operator/auto approved and the resume executed.
The ONLY difference between a healthy `board_rejected` and a stuck `risk_approved` is **whether
the post-decision `graph.ainvoke(Command(resume=…))` persisted the terminal status.**

### 3.3 Did any reach the broker?
No. All 59 are `paper`, `fill_price`/`fill_ts` NULL, and no `board_approved`/`filled`/broker
audit exists for any of them. They died in the graph before submission.

### 3.4 Confirmed: nothing sweeps `risk_approved`
`grep -rn "UPDATE proposed_order" trading_corp/` → **NONE**. `risk_approved` is written only at
`ceo_graph.py:361` and is otherwise **read-only** in code — by the counters (`web/data.py:1172`,
`telegram_commands.py:337`) and an in-flight panel (`web/data.py:1162`,
`status IN ('proposed','risk_approved','board_approved')`). No code advances it.

---

## 4. Recurrence (STEP 1.5)
- Rows created in the window are steady (~1/day) through 2026-07-07 (yesterday).
- 40 of the 59 have a **timeout-reject decision already recorded** whose write-back never
  happened → the resume-failure path (manifestation A) is **ongoing**, not historical.
- Count trend: **growing** (~0.8/day, matches BACKLOG). This is an active leak, not a
  stopped one-time incident. It will keep accruing until the lifecycle is fixed.

---

## 5. Root cause (definitive)

**`proposed_order.status` for HITL-gated (PMCC) orders has no durable reconciliation.** The
terminal disposition is *derivable* — from the recorded `board_decision_received` audit
(manifestation A, 42 rows) or from the fact that the approval window has long expired so the
intended outcome is the timeout-reject (manifestation B, 17 rows) — but **no code closes that
loop and writes the terminal status back to the row.** The status is only ever advanced as a
transient side-effect of the live `_run_order` coroutine, which is lost on restart with no
recovery (B) and can fail after the decision is recorded with no reconciler to fix it up (A).

Precise loci:
- The fragile advance: `main.py:4344-4366` (`_run_order` interrupt/resume loop).
- The decision that is recorded but not written back: `pending_registry.py:135-147` (timeout
  audit) with no corresponding row update.
- The known resume-failure trigger: `main.py:1094-1103` (checkpointer write-transaction /
  `database is locked` collision during HITL `interrupt()` waits).
- The acknowledged recovery gap: `pending_registry.py:20-25` ("recovery is a B-v2 polish item").
- The absent transition: no `UPDATE proposed_order` / no reconciler / no boot-recovery anywhere.

---

## 6. Evidence-based observations for the STEP 2 design gate (NOT a design)

Surfaced so the design decision starts from evidence; **no fix proposed here.**
- **Correct terminal status** for the 59 appears to be **`board_rejected`** (an existing enum
  value, board_reason e.g. "approval timeout / stale — never resumed"): it is exactly what
  `end_rejected_node` would have written for the 42 timeout-decided rows, and the intended
  timeout outcome for the 17. `cancelled` is the alternative if the design prefers to signal
  "never decided" vs "declined". No new status is needed or available.
- **Two failure modes may want different handling:** (A) 42 rows *have* a recorded decision to
  replay onto the row; (B) 17 rows have no decision and would need an age/window-based expiry.
- **Counter surfaces** that still read the residue: `_query_pending_approvals` (`web/data.py:1172`
  → Telegram `/pending`), `telegram_commands.py:337`, and the in-flight panel `web/data.py:1162`.
  The Overview stat card was already switched to the registry (`7f641d8`); these DB-count
  surfaces were not. Whether any need a query change vs. being resolved by the backfill is a
  STEP 2.3 question.
- **Blast radius of any fix is strictly PMCC/HITL-graph.** The same `_run_order`/registry path
  is shared by `fidelity_joint` (rare scans) and the demo order — scope discipline required so
  a lifecycle fix doesn't perturb them.

---

## 7. What I did NOT do (gate discipline)
- No fix designed, no code written, no DB writes. All prod access was `sqlite3 -readonly` +
  `systemctl show`. Investigation branch created off `origin/main`; only this report committed.
- Two-manifestation split (A resume-fail vs B restart-cancel) is evidenced at the aggregate +
  sampled-row level; if the design wants the *exact* per-row A/B label for all 59 (e.g. to drive
  a decision-replay vs expiry backfill), that is a small additional read-only query — flag at
  the design gate.
