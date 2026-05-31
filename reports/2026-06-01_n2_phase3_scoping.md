# N+2 Phase 3 implementation scoping — refined brief for next-weekend implementation

**Date:** 2026-06-01 · **Type:** read-only + rebase scoping pass (no Phase 3 code implementation) · **Branch (this report):** `n2-phase3-scoping-2026-06-01` · **Rebased exit-path branch:** `bitunix-live-exit-path-2026-05-29-rebased` (was `e1d38f8`, now `3016053` rebased onto `origin/main` `f110c74`). **Status:** READY for next-session implementation pending operator sign-off on §6 decisions.

**Verification SHAs:**
- `origin/main` = `f110c74` (post-tastytrade-rotation, post-dashboard-merge, post-admin-closeout entry)
- `origin/bitunix-live-exit-path-2026-05-29` (pre-rebase) = `e1d38f8`
- `bitunix-live-exit-path-2026-05-29-rebased` (post-rebase, pushed by this session) = `3016053`
- This scoping branch HEAD = filled-in at commit time

**Read first (in order):**
- `git show 33da534:reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md` (Phase 1a; structural questions #1, #2, #3, #8, #9)
- `git show e1d38f8:reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1b.md` (Phase 1b; structural questions #4, #5, #3-restart, #7; A/B/C scope decision)
- `git show ade4dbc:reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md` Findings #6.1-#6.4 + #7 (Phase 3 scope validation; +60-90 LOC verdict)
- this file (refined plan; embeds the next-session implementation prompt at §7)

---

## TL;DR

1. **Rebase landed clean** with one mechanical skip. Commit 4a8b440 (BACKLOG.md "Phase 1a complete, Phase 1b pending" entry) is now stale and was --skip'd; new entry filed via this session's separate scoping branch. The 3 docs commits (33da534 phase1a, 2f3c4ee next-session prompt, e1d38f8 phase1b) rebased without conflict. Rebased branch HEAD `3016053`.

2. **Merge-sequence prerequisite is RESOLVED.** Phase 1b §1's "Phase 3 cannot start until broker-write + safety + entry-path are on main" — all four prerequisites have merged since 2026-05-29. `_record_placement_outcome` is on main at observer:2426; `_place_live` at observer:2493; `_observe_fill` at brokers/bitunix.py:1060; `execution_mode` field in `config/strategies.yaml:1022` (value `paper`); `live_orders_placed` counter wired; `BitunixPositionModeMismatch` in `brokers/bitunix_exceptions.py`. **The "operator decision at Phase 2 boundary" Phase 1b flagged is no longer required** — the recommended merge sequence happened de facto via the merges that landed during the 2026-05-30 to 2026-05-31 sessions.

3. **One Phase 1b premise correction confirmed (Path C still pending).** The N+1 commit-3 behavior (no `paper_trade_record` on live path) IS the current behavior on main. Phase 1b's recommendation to REVERSE it (so live entries write a row tagged `extra.execution_mode='live'`) is **STILL THE PHASE 3 SCOPE**. Verified at `observer:2449-2468` — docstring + code path explicit.

4. **Two Phase 1b code-surface assumptions partially mitigated since 2026-05-29 — scope narrows by ~80 LOC:**
   - **Finding #6.4 (timeout-and-halt on `_observe_fill`)** is **ALREADY DONE** via gate (a) sub-item 3 (`_handle_stuck_order` at `brokers/bitunix.py:1122` — cancel + audit + safety_notifier Telegram + raise `BitunixStuckOrderCancelled`/`BitunixStuckOrderCancelFailed`). Phase 3 only needs to ensure `_record_exit_outcome` calls this for exit-path stuck orders (~5 LOC reuse, not 30 LOC new). **Architectural review's "ADD ~30 LOC + tests" verdict reduces to ~5 LOC + a wiring test.**
   - **Finding #6.2 (audit-row-loss recovery)** is HALF DONE — `LoggerAgent.log_event` already retries (3, jittered) + JSONL fallback (`agents/logger.py:22-129`). `insert_paper_trade_record` (`persistence/db.py:494-511`) still has no retry. Phase 3 adds the missing half (~10 LOC + 1 test; architectural review estimate of ~10 LOC holds).

5. **Three Phase 1b code-surface assumptions need adjustment, not scope cuts:**
   - **`get_pending_positions` is NOT a named public method on the broker** — the BitUnix HTTP endpoint is inlined inside `snapshot()` (line 447) and `_position_mode_from_positions()` (line 960). Phase 1b's reconciler design ("60s sanity-check poll calls `bitunix_broker.get_pending_positions()`") needs to either: (a) extract a small public method (~15 LOC) wrapping the existing inline endpoint call, OR (b) call `snapshot()` and read `positions: list[Position]` from the returned `BrokerSnapshot`. **Recommend (a)** — cleaner separation, doesn't pull in the full snapshot cost for a position-list-only check.
   - **`safety_notifier` is on `DataExecAgent` and `BitunixBroker`, NOT on the observer.** Phase 1b §1's reference to "safety_notifier slot" on observer is incorrect — the observer uses `self.data_exec._handle_*` and `self.telegram_channel` directly. For Phase 3's exit-path Telegram pushes, the recommended pattern is the existing `_push_with_confirmed_delivery` on observer (mirrors `_place_live` Telegram pattern at observer:2718).
   - **`bitunix_position_reconciler.py` already exists** at `trading_corp/agents/divisions/bitunix_position_reconciler.py` — dormant in paper mode per its module docstring lines 25-29 ("runs cleanly but emits no audit rows until Phase 4 broker fill state lands"). Phase 3 **extends this existing module** rather than creating a new reconciler. ReconcilerConfig + SLDecision dataclasses are already there; `POSITION_SL_UPDATE_KIND` and `RECONCILER_ACTOR` constants too. Companion test file `tests/test_bitunix_position_reconciler.py` exists.

6. **Scope (B) HOLDS** per architectural review's Finding #7 verdict. Net LOC delta from Phase 1b's "~940 source + ~620 tests":
   - **Reductions:** Finding #6.4 down ~25 LOC; reconciler module-creation overhead down ~50 LOC (extend existing); `get_pending_positions` extraction trade-off neutral (~15 LOC).
   - **Confirmed additions:** Finding #6.1 result_source stamp ~20 LOC; Finding #6.2 insert-retry ~10 LOC; Finding #6.3 empty-list disambiguation ~20 LOC; Finding #6.4 wiring ~5 LOC. Total +55 LOC.
   - **Net revised estimate:** **~920 source LOC + ~640 test LOC across 8-11 commits**, distributed across 1-2 sessions per Phase 1b's split recommendation. Operator should re-evaluate the 1-vs-2 session split when the implementation session opens.

7. **Pre-implementation gates** at §5 — 5 verifiable conditions before Phase 3 work starts. None require code change; all read-only.

8. **The pre-written next-session prompt is at §7** — paste-ready when operator is ready to start implementation.

---

## 1. Rebase outcome

### What happened

The exit-path branch had 4 commits relative to main (per `git log origin/main..origin/bitunix-live-exit-path-2026-05-29`):

| SHA (pre-rebase) | Type | Files | Result |
|---|---|---|---|
| `33da534` | docs | `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md` (new) | Rebased clean as `126b73b` |
| `4a8b440` | docs (BACKLOG) | `BACKLOG.md` (top-section edits) | **Skipped** — stale; main has ~12 newer P1 entries; this session's separate scoping branch files the current entry |
| `2f3c4ee` | docs | `reports/2026-05-29_next_session_prompt.md` (new) | Rebased clean as `ba979d3` |
| `e1d38f8` | docs | `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1b.md` (new) | Rebased clean as `3016053` (new HEAD) |

77 commits on main not on exit-path. 3 conflict-free rebases + 1 mechanical skip.

### Why the BACKLOG skip is mechanical, not semantic

The skipped commit added two P2 entries to BACKLOG.md:
- "P2 — Stage-1 N+2 live exit-path: Phase 1a complete, Phase 1b + scope decision pending" — **stale** (Phase 1b is COMPLETE, shipped in `e1d38f8`; scope decision (B) is RECOMMENDED).
- "P3 — uncommitted strategies.yaml disable for kalshi_weather_arb + kalshi_crypto_arb" — **stale** (kalshi disable shipped commit `7e9d06c`, merged to main, sed-deployed to prod per memory `[[kalshi-scanners-disabled-prod-2026-05-29]]`).

Both entries are factually obsolete. Re-applying them would have been a regression. The new scoping branch `n2-phase3-scoping-2026-06-01` files the current P1 entry ("N+2 Phase 3 implementation ready") — see §8 below.

**Phase 3 scope is unaffected by this skip.** The Phase 1a and Phase 1b reports themselves rebased clean (commits `126b73b` and `3016053`) and are now reachable both via the new branch heritage AND via the original SHAs `33da534`/`e1d38f8` for `git show` per the Finding #1a `[on branch X]` discipline.

---

## 2. Scope validation against current main reality

For each Phase 1b scope item, the current state on main:

### 2.1 Path C revert (Phase 1b §6 check #3)

| Aspect | Phase 1b assumption | Current main reality | Phase 3 status |
|---|---|---|---|
| Live entries write `paper_trade_record`? | NO (per N+1 commit 3) — Phase 1b proposed REVERSING this | NO (verified at `observer:2449-2468`: docstring "No `paper_trade_record` on the live path" + code path `_place_live(); return`) | **REVERT STILL NEEDED.** ~30 LOC + tests, first commit per architectural review Finding #7 verdict. |
| `FillEvent.venue_order_id` field exists? | NO — Phase 1b said add | NO (verified at `persistence/models.py:71-79`: 7 fields, none is `venue_order_id`) | **ADD.** 1-line dataclass change + broker_write `place_order` returns it. |
| `FillEvent.fee` field exists? | NO — Phase 1b said add | NO (verified at `persistence/models.py:71-79`) | **ADD.** 1-line dataclass change + drop discard at broker_write `place_order` line ~1080 (currently `_fee` underscore). |
| `extra["execution_mode"]`+`extra["broker_order_id"]`+`extra["broker_venue_order_id"]` stamp on entry rows | not yet stamped (entry path doesn't write the row) | not yet stamped — depends on Path C revert | **STAMP.** Part of Path C revert commit. |
| Replay loop reads `execution_mode` from row? | classifiers accept `extra` per Phase 1a §1 — confirmed | `_classify` and `_classify_v2_multi_leg` accept extra (confirmed at `paper_trade_replay.py:149-270`, `:401-662`) | **NO CHANGE.** Already structured to pass extra through. |

### 2.2 `_record_exit_outcome` canonical helper (Phase 1a §1)

| Aspect | Phase 1b assumption | Current main reality | Phase 3 status |
|---|---|---|---|
| Helper exists? | NO — Phase 1a/1b said create new | NO (no `_record_exit_outcome` anywhere; verified via grep) | **CREATE.** Mirror `_record_placement_outcome` shape; ~80 LOC + ~150 LOC tests. |
| Entry-side mirror still at observer:2425? | YES, at observer:2425 | YES, at `observer:2426` (1-line drift; the constant LIVE_ORDERS_PLACED_AGENT_STATE_KEY at line 236 is also 1 line off Phase 1b's "235") | **NO CHANGE — pattern stable.** |
| Call sites in `paper_trade_replay`? | 3 single-leg + 4+ multi-leg sites | Single-leg classifier returns single resolution at `:149-270` (1 call site post-classifier); multi-leg has per-leg fill events at `_emit_audit` `:573-ish` + tp3 close `:577-605` + SL `:526-559` + expired `:654-662` (5+ call sites in multi-leg) | **WIRE.** Phase 3 implementation walks all sites. |
| Revised signature with `fill_event` param? | Per Phase 1b §6 check #1 | not yet implemented (helper doesn't exist) | **CONFIRM** the `(row, resolved, fill_event, leg=None, db_url=...)` signature recommendation. |

### 2.3 `_execute_live_exits` + event-driven reconciler (Phase 1b §2)

| Aspect | Phase 1b assumption | Current main reality | Phase 3 status |
|---|---|---|---|
| Reconciler module exists? | NO — Phase 1b said create new | YES — `trading_corp/agents/divisions/bitunix_position_reconciler.py` exists, dormant in paper mode (docstring lines 25-29) | **EXTEND** existing module. Add live-mode active branch reading `paper_trade_record WHERE extra_json LIKE '%"execution_mode":"live"%' AND result IS NULL`; call `get_history_trades(order_id=fill_event.order_id)`; write reconciliation audit kinds. Saves ~50 LOC vs new-module overhead. |
| `get_pending_positions` available as a method? | YES — Phase 1b §2 says "Calls `bitunix_broker.get_pending_positions()`" | **NO** — endpoint is inlined inside `snapshot()` (`brokers/bitunix.py:447`) and `_position_mode_from_positions()` (`:960`). No standalone public method. | **EXTRACT** `async def get_pending_positions(self) -> list[Position]` wrapping the existing inline endpoint call (~15 LOC). OR call `snapshot()` directly and read `positions` from the returned snapshot. Recommend the extraction. |
| `_observe_fill` location + timeout handling? | At broker-write `:766-793` with no halt | At `brokers/bitunix.py:1060`; **TIMEOUT-AND-HALT ALREADY DONE** via `_handle_stuck_order` at `:1122` (gate (a) sub-item 3 from 2026-05-30) — cancel + audit + safety_notifier Telegram + raises `BitunixStuckOrderCancelled`/`BitunixStuckOrderCancelFailed` | **REUSE** existing primitive. Finding #6.4's 30 LOC add reduces to ~5 LOC of wiring + a wiring test. |
| Empty-trade-list handling on `get_history_trades`? | Phase 1b says reconciler must distinguish empty from filled — Finding #6.3 added explicit need | Today: `get_history_trades` returns `[]` on unexpected response shapes; `_fill_price_from_history` falls through to `avg_price=0.0` silently. **No explicit guard.** | **ADD** explicit verdict-="missing" branch in the new reconciler logic + a guard at the broker-write `place_order` call site rejecting `avg_price == 0.0`. ~20 LOC + tests. |
| Background poll (60s)? | Phase 1b §2 recommends | NOT IMPLEMENTED | **ADD** as part of Phase 3 reconciler extension. ~80 LOC (background task wiring + diff function + halt). |

### 2.4 Layer 1 fee plumbing (Phase 1b §3)

| Aspect | Phase 1b assumption | Current main reality | Phase 3 status |
|---|---|---|---|
| `FillEvent.fee` field? | NO — must add | NO (verified) | **ADD** 1-line + drop `_fee` discard at `bitunix.py` `place_order` call site. ~5 LOC. |
| `_fill_price_from_history` sums fee? | YES — at broker-write `:795-808` | YES at `bitunix.py:1208-ish` (post-rename `_fill_price_from_history`) | **NO CHANGE** — already correct. |
| `notify_close_out` renders real fee on live? | Currently renders "Fees: not tracked in paper" at `:158-159` | Confirmed still present at `comms/bitunix_lifecycle_notifier.py:158-159` (verbatim verified) | **MODIFY** to render real fee when `paper_mode=False`. ~10 LOC + tests. |
| `paper_trade_record.extra["fee_usd"]` + `extra["exit_fee_usd"]` stamps? | Phase 1b says add | NOT YET — no field present | **ADD** stamps in `_record_exit_outcome` (per-leg) + Path C revert (entry fee). |

### 2.5 Restart-resume cases (a) + (b) (Phase 1b §4)

| Aspect | Phase 1b assumption | Current main reality | Phase 3 status |
|---|---|---|---|
| `StrategyState.from_persistence` halt + halt_reason load? | YES at `persistence/models.py:150-181` | YES at `:149-180` (1-line off; semantics same) | **NO CHANGE** — restart-safe primitive present. |
| Restart-resume function exists? | NO — Phase 1b said create new `_resume_live_positions(deps)` | NO — not yet implemented | **CREATE.** Per Phase 1b §4, cases (a) match + (b) broker-orphan-halt. Case (c) deferred to N+3. ~120 LOC + ~80 LOC tests. |
| `get_history_positions` available? | NO on broker-write — Phase 1b confirmed absent | NO on current main (verified — no method, no inline endpoint call) | **DEFER** with operator-halt-and-resolve for case (c). Same as Phase 1b §4 recommendation. |

### 2.6 8 operational alerts (Phase 1b §5)

| Aspect | Phase 1b assumption | Current main reality | Phase 3 status |
|---|---|---|---|
| `bitunix_lifecycle_notifier.py` shape? | 191 LOC, 2 public methods, `paper_mode` kwarg at `:41` | 191 LOC verbatim, `notify_tp_fill` `:47-93`, `notify_close_out` `:95-168`, `_send` `:174-191`, `paper_mode` kwarg at `:37` (assignment line 41 confirmed) | **EXTEND** with 8 new methods per Phase 1b §5. |
| `channel.push` already does confirmed-delivery audit semantics? | YES per `[[telegram-audit-success-is-confirmed-delivery]]` | YES (verified — `_send` at `:174-191` calls `channel.push(full, audit_path=..., audit_context=...)`) | **NO CHANGE** — reuse the pattern. |
| Counter-aware `(live, exit #N/10)` suffix? | Phase 1b says add `live_exit_counter_getter: Callable | None` ctor kwarg | NOT YET — no exit counter primitive | **ADD** new `live_exits_executed` agent_state key + read pattern mirroring `live_orders_placed`. |

### 2.7 Architectural review Findings #6.1-#6.4 (post-Phase-1b)

| Finding | Phase 1b status | Current main reality | Phase 3 status |
|---|---|---|---|
| **#6.1 result='win' semantic disambiguation** | Surfaced post-Phase-1b | NO `result_source` column or `extra["result_source"]` stamp anywhere (verified via grep across `trading_corp/`) | **ADD ~20 LOC** — `extra["result_source"]` stamp in `_record_exit_outcome` (option (b) per Finding #10 question 7); accept-as-known unless operator overrides. Cheapest mitigation. Operator decision deferred to §6 below. |
| **#6.2 audit-row-loss recovery** | Surfaced post-Phase-1b | Half done: `LoggerAgent.log_event` retries (3, jittered) + JSONL fallback at `agents/logger.py:22-129`; `insert_paper_trade_record` at `persistence/db.py:494-511` has NO retry | **ADD ~10 LOC** — `_insert_paper_trade_record_with_retry` wrapper or `insert_paper_trade_record(retry_db_locks=True)` flag. Reuses the `_DB_LOCK_RETRY_DELAYS_SEC` schedule from `logger.py`. |
| **#6.3 reconciler empty-trade-list** | Surfaced post-Phase-1b | NOT mitigated — `get_history_trades` returns `[]` silently; `_fill_price_from_history` produces `avg_price=0.0` | **ADD ~20 LOC** — explicit verdict="missing" branch in new reconciler; guard at broker_write `place_order` rejecting `avg_price == 0.0`. Combined with `get_order_detail` follow-up to distinguish pending vs rejected. |
| **#6.4 `_observe_fill` timeout-and-halt** | Surfaced post-Phase-1b | **ALREADY DONE** via gate (a) sub-item 3 — `_handle_stuck_order` at `brokers/bitunix.py:1122` cancels + emits audit + safety_notifier Telegram + raises typed exception | **WIRE ~5 LOC** — `_record_exit_outcome` catches `BitunixStuckOrderCancelled`/`BitunixStuckOrderCancelFailed` and routes to exit-side audit + telegram (mirrors entry path's try/except in `_place_live`). |

---

## 3. Refined LOC estimate (delta vs Phase 1b's "~940 + ~620")

| Item | Phase 1b est. | Architectural review delta | Current main reality adjustment | Phase 3 final est. |
|---|---|---|---|---|
| Path C revert + `venue_order_id` | ~30 LOC + ~30 tests | confirm + elevate to first commit | unchanged | ~30 + ~30 |
| `_record_exit_outcome` canonical helper | ~80 LOC + ~150 tests | confirm | unchanged | ~80 + ~150 |
| `_execute_live_exits` + event-driven reconciler | ~150 LOC + tests | +30 for #6.3/#6.4 | -50 (extend existing module) + -25 (#6.4 reuses primitive) - 25 (~) = -45 net from arch review's +30; **net -15** | ~135 + ~80 |
| Background sanity poll (60s) | ~120 LOC + ~80 tests | confirm | +15 (extract `get_pending_positions` method) | ~135 + ~80 |
| Layer 1 fee plumbing | ~60 LOC + ~30 tests | confirm | unchanged | ~60 + ~30 |
| Restart-resume cases (a)+(b) | ~120 LOC + ~80 tests | confirm | unchanged | ~120 + ~80 |
| 8 operational alerts | ~250 LOC + ~150 tests | confirm | unchanged | ~250 + ~150 |
| Finding #6.1 result_source stamp | — | +~20 LOC | unchanged | ~20 + ~10 |
| Finding #6.2 insert-retry | — | +~10 LOC | unchanged | ~10 + ~10 |
| Finding #6.3 (folded into reconciler row above) | — | (folded) | (folded) | — |
| Finding #6.4 wiring (already-done primitive) | — | +~30 LOC | -25 (already-done; only wiring) | ~5 + ~10 |
| **TOTAL** | ~810 + ~520 | +60-90 LOC | -85 LOC | **~845 + ~630** |

**Net refined estimate: ~845 source LOC + ~630 test LOC across 8-11 commits.** Slight reduction vs architectural review's ~1000-1030 source LOC total because of (a) `bitunix_position_reconciler` module exists (extend not create), (b) Finding #6.4 primitive exists (wire not new).

**Tractability check:** Phase 1b said "tight but tractable for one focused session." With the ~85 LOC reduction, single-session tractability is still tight but improved. The recommended split (Phase 1b §7 "items 1+3 first session, item 2 + Findings #6.1-#6.4 second session") still applies.

---

## 4. Refined implementation sequence

Phase 1b §7 recommended the natural split as a 1-2-session block. Sequence below adopts the Phase 1b split, with refinements from current-main reality:

### Session A — Foundation + entry-side row write + canonical helper (~1 day)

**Commit 1: FillEvent additive fields + broker_write plumbing.**
- Add `fee: float = 0.0` and `venue_order_id: str | None = None` to `FillEvent` (`persistence/models.py:71-79`).
- Drop `_fee` discard at broker `place_order` call site; return `fee=_fee` and `venue_order_id=venue_orderId` (from `_observe_fill`).
- Test: `tests/test_fill_event_fields.py` (or extend `tests/test_persistence_models.py`). ~15 LOC source + ~30 LOC tests.

**Commit 2: Path C revert — `_place_live` writes `paper_trade_record`.**
- After `data_exec.place(order)` returns in `_place_live`, also call `db.insert_paper_trade_record(record.to_db_row(), db_url=self.db_url)` with `record.extra["execution_mode"] = "live"` + `record.extra["broker_order_id"] = fill_event.order_id` + `record.extra["broker_venue_order_id"] = fill_event.venue_order_id` + `record.extra["entry_fee_usd"] = fill_event.fee`.
- Update `observer.py:2449-2468` docstring + comment.
- Test: `tests/test_bitunix_observer_path_c_live_writes_row.py`. ~30 LOC source + ~50 LOC tests.

**Commit 3: `insert_paper_trade_record` DB-lock retry (Finding #6.2).**
- Extend `persistence/db.py:494-511` `insert_paper_trade_record` with retry-on-DB-lock; reuse `_DB_LOCK_RETRY_DELAYS_SEC = (0.1, 0.3, 0.7)` schedule from `agents/logger.py`.
- Test: `tests/test_db_insert_retry.py`. ~10 LOC source + ~30 LOC tests.

**Commit 4: `_record_exit_outcome` canonical helper (paper-mode only first; live-mode in Commit 5).**
- New helper in `paper_trade_replay.py` parallel to `_record_placement_outcome`. Signature `(row, resolved, fill_event=None, leg=None, db_url=...)`. Paper branch calls existing `_update_row(...)`.
- Refactor `_update_row` call sites in `_classify`/`_classify_v2_multi_leg` to go through the helper (paper-mode byte-identity test like commit 1 of N+1 entry-path).
- Test: `tests/test_paper_trade_replay_record_exit_outcome.py` (byte-identity pin per N+1 commit-1 pattern). ~80 LOC source + ~150 LOC tests.

**Session A subtotal: ~135 LOC source + ~260 LOC tests, 4 commits. Test gate: full pre-existing test suite green; specifically `tests/test_bitunix_observer_*.py`, `tests/test_paper_trade_replay*.py`, `tests/test_paper_trade_record.py`.**

### Session B — Live exit path + reconciliation + alerts + restart-resume (~1 day)

**Commit 5: `_record_exit_outcome` live-mode fork (broker side).**
- Inside the helper, if `row.extra["execution_mode"] == "live"`, build the exit `ProposedOrder` (per Phase 1a §3 template) + await `data_exec.place(exit_order, division="bitunix_futures")` + populate `result_*` from the broker `FillEvent`.
- Stamp `extra["result_source"] = "live_broker_truth"` for live; `"paper_replay_bars"` for paper (Finding #6.1 mitigation).
- Catch `BitunixStuckOrderCancelled` / `BitunixStuckOrderCancelFailed` → write exit-side audit + telegram (Finding #6.4 wiring).
- Wire `_execute_live_exits` as the async follow-up step (per Phase 1a §2).
- Test: `tests/test_bitunix_exit_live_branch.py` (mirrors `test_bitunix_observer_live_branch.py` pattern). ~120 LOC source + ~150 LOC tests.

**Commit 6: extend `bitunix_position_reconciler.py` with live-mode active branch.**
- Add `paper_trade_record WHERE execution_mode="live" AND result IS NULL` query path.
- Inside `_record_exit_outcome`, call `_reconcile_single(row, fill_event)` post-place; reads `get_history_trades(order_id=fill_event.order_id)`.
- Compare broker-truth vs row-stamped; verdict ∈ {match, divergent, missing}. **Empty trade list → verdict="missing" explicitly** (Finding #6.3). Combine with `get_order_detail` for pending vs rejected disambiguation.
- On divergent/missing: write `reconciliation_divergence_detected` audit + push elevated telegram + set `bitunix_broker._halt_new_orders = True`.
- Test: `tests/test_bitunix_position_reconciler_live_mode.py`. ~60 LOC source + ~80 LOC tests.

**Commit 7: Background sanity poll (60s) + `get_pending_positions` method extraction.**
- Extract `async def get_pending_positions(self) -> list[Position]` on BitunixBroker (~15 LOC; wraps the existing inline endpoint call from `snapshot()`).
- New `main.py` background task scheduling `reconciler.sanity_check()` every 60s. Task reads `get_pending_positions()` + `paper_trade_record WHERE execution_mode=live AND result IS NULL`; asserts 1:1 match by (symbol, side, qty-within-tolerance).
- Divergence → write `position_missing_on_broker` / `orphan_broker_position_detected` audit + halt.
- Test: `tests/test_bitunix_background_sanity_poll.py`. ~135 LOC source + ~80 LOC tests.

**Commit 8: 8 operational alerts (Phase 1b §5).**
- 8 new methods on `BitunixLifecycleNotifier`: `notify_exit_order_placed`, `notify_exit_order_filled`, `notify_exit_order_rejected`, `notify_exit_partial_fill`, `notify_position_closed_with_pnl`, `notify_reconciliation_divergence`, `notify_cost_accrual_recorded` (stub for N+3 hook), `notify_restart_resume_executed`.
- Update `__init__` to accept `live_exit_counter_getter` kwarg.
- Real-fee rendering on `notify_close_out` for `paper_mode=False` (replace "Fees: not tracked in paper" with the actual number).
- Test: `tests/test_bitunix_lifecycle_notifier_exit_alerts.py`. ~250 LOC source + ~150 LOC tests.

**Commit 9: Restart-resume cases (a) + (b) (Phase 1b §4).**
- New `async def _resume_live_positions(deps)` called from `main.py` startup BEFORE `paper_trade_replay.replay_pending_paper_trades` cron starts.
- Cases (a) match: write `restart_resume_executed` audit + telegram.
- Case (b) broker-orphan: write `orphan_broker_position_on_restart` audit + halt + elevated telegram.
- Case (c): write `restart_resume_case_c_deferred` audit + halt + telegram operator (NOT auto-resolved).
- Test: `tests/test_bitunix_restart_resume.py`. ~120 LOC source + ~80 LOC tests.

**Session B subtotal: ~685 LOC source + ~540 LOC tests, 5 commits.**

### Total revised: **~820 source LOC + ~800 test LOC across 9 commits**

(Slightly different from §3's table because some test counts were rolled into source rows; reconciles within rounding.)

---

## 5. Pre-implementation gates

Before Session A starts, these conditions must hold. None require code changes; all read-only verifications. Architectural review Finding #8 lists 10 verifications; the 5 most load-bearing for Phase 3 are:

| # | Gate | How to verify | Status as of 2026-06-01 |
|---|---|---|---|
| G1 | Stage-1 paper-mode has run for ≥1 full operating day without crash | `ssh tc-prod-vm sudo journalctl -u trading-corp.service --since "2026-05-31 05:36" \| grep -iE "(error\|exception\|traceback)"` filter to relevant lines; check `[[stage1-first-17h-review-2026-05-31]]` memory | **PASSED.** Memory confirms 0 fires + 0 crash signatures in first 8h45m review post-redeploy3 (2026-05-31 05:36 UTC start). Continued operation verifiable via prod probe at implementation-session start. |
| G2 | `_record_placement_outcome` paper path has fired ≥1 real placement | `ssh tc-prod-vm sqlite3 .../trading_corp.db "SELECT COUNT(*) FROM audit_event WHERE kind='paper_trade_recorded' AND ts > '2026-05-31T05:36'"` | **Verify at session start.** Memory says 16/16 post-HTF signals fee-floor rejected in first window — no actual placements yet. **Acceptable** to start Phase 3 even without a fired placement (paper-replay tests cover the helper). Optional preference: run an integration test with synthetic record. |
| G3 | `_observe_fill` timeout-and-halt primitive (`_handle_stuck_order`) is reachable at runtime | grep `_handle_stuck_order` from `brokers/bitunix.py:1122`; trace to safety_notifier injection in `main.py:822-823` | **PASSED.** Wired per gate (a) sub-item 3 deploy (2026-05-30 commit `36a3749`). Verified at code-read this session. |
| G4 | Risk-tier overlay status on prod | Read `runbooks/deploy_log.md` 2026-05-31 redeploy3 entry; confirm `1fda7f608c1e74900b55eb77f0bb344f` md5 matches main's `config/strategies.yaml`; memory `[[bitunix-risk-tier-and-leverage-pre-live]]` says CANONICAL on main since 2026-05-30 05:56 UTC | **PASSED.** Risk-tier branch merged to main via `9fd9022` and matches prod (per memory). No overlay drift. |
| G5 | C-1 credential rotation — no critical-path BACKLOG P1 items blocking | Read BACKLOG.md current P1 items; check for any "blocks Stage-1" entries | **Verify at session start.** Most recent: tastytrade C-1 rotation COMPLETE 2026-05-31. Bitunix rotation done 2026-05-29. No active P1 C-1 items as of this session. |

**All 5 gates PASS or DEFER to session-start verification (none failing).** Phase 3 implementation can proceed.

---

## 6. Operator decisions surfaced (before next-session implementation)

Decisions Phase 3 implementation needs answers to. Surface here; do not auto-resolve.

### Decision 6.1 — Finding #6.1 `result_source` mitigation

Architectural review Finding #10 question 7 offers four options:
- (a) Schema: add `result_source` column (requires Board approval per CLAUDE.md § 6 + migration).
- (b) Convention: stamp `extra["result_source"]` in `_record_exit_outcome` (~20 LOC; per-row indicator).
- (c) Architectural: live-mode never sets `result` from classifier — only from broker truth (forces semantic split at the helper level).
- (d) Accept-as-known + document.

**Recommendation: (b)** per architectural review. Phase 3 stamps `extra["result_source"]` (values: `"paper_replay_bars"` or `"live_broker_truth"`). Downstream readers update opportunistically. Cheapest mitigation; reversible; no schema gate.

**Operator decision needed:** confirm (b) or override.

### Decision 6.2 — Finding #6.2 audit-row-loss recovery

Architectural review Finding #10 question 8 offers three options:
- (a) Extend db-lock retry to `insert_paper_trade_record`.
- (b) Add explicit "audit-row-lost-after-place" recovery handler.
- (c) Treat audit-row as denormalized view of audit_event (canonical).

**Recommendation: (a) as default**, per architectural review. ~10 LOC reuse of `_DB_LOCK_RETRY_DELAYS_SEC`. (c) is a longer-term refactor; not Phase 3.

**Operator decision needed:** confirm (a) or override.

### Decision 6.3 — Session A vs A+B split

Phase 1b recommended the natural split. Architectural review allowed scope (B) within "tight but tractable for one focused session." Current-main reality saves ~85 LOC.

**Two options for implementation pacing:**
- **(A) Single-session attempt:** ~820 LOC source + ~800 LOC tests in ONE session. Tight; high context cost; risk of late-session ramp on tests.
- **(B) Split per Phase 1b's recommendation:** Session A foundation + helper (4 commits, ~135 source / ~260 tests); Session B live + reconciler + alerts + restart (5 commits, ~685 source / ~540 tests).

**Recommendation: (B) — Session A + Session B split.** Lower per-session risk; Session A's output is independently mergeable to main (paper-mode byte-identity locked); Session B opens on a known-clean baseline.

**Operator decision needed:** confirm split (B) or override to single-session (A).

### Decision 6.4 — `get_pending_positions` extraction vs `snapshot()` call

Phase 1b's reconciler design assumed `get_pending_positions()` is a method; current main has the endpoint inlined. Two options:
- (a) Extract `async def get_pending_positions(self)` wrapping the existing endpoint call (~15 LOC).
- (b) Call existing `snapshot()` and read `positions` from the `BrokerSnapshot` result.

**Recommendation: (a).** Cleaner separation, faster (no equity refresh + symbol enumeration that `snapshot()` does), no semantic surprise about "snapshot vs position list."

**Operator decision needed:** confirm (a) or override to (b).

### Decision 6.5 — Whether to land the rebased exit-path branch as documentation pre-Phase-3 OR keep deferred

**RESOLVED 2026-05-31 ~21:47 UTC — operator overrode default (b) to option (a); 3 docs commits merged onto `origin/main` via `--no-ff` merge `90ae0e4`. See `runbooks/deploy_log.md` 2026-05-31 ~21:47 UTC entry for full detail. Phase 1a + 1b reports + 2026-05-29 next-session-prompt now reachable directly at `reports/...md` from main. Architectural review Finding #1a exit-path locus CLOSED.**

The rebased `bitunix-live-exit-path-2026-05-29-rebased` branch (HEAD `3016053`) carries the Phase 1a + 1b reports as canonical-on-rebased-branch artifacts. Architectural review Finding #1a recommended merging docs-only commits to main "for next session that does any merge." Two options:
- (a) **Merge `bitunix-live-exit-path-2026-05-29-rebased` to main** as docs-only before Phase 3 starts (3 commits land cleanly per the rebase outcome). Removes the `[on branch X]` citation cost. **← Operator override; executed 2026-05-31 ~21:47 UTC via merge `90ae0e4`.**
- (b) **Leave rebased branch unmerged** until Phase 3 implementation lands — fold the report-merge into the Phase 3 commit chain (cleaner single-merge story). (Original recommendation; not taken.)

---

## 7. Pre-written next-session implementation prompt

Paste this into a fresh Claude Code session when ready to execute Phase 3. Adjust dates as needed.

```
N+2 Phase 3 implementation session — Stage-1 BitUnix live exit path. ~1-2 day budget. Scope (B) per architectural review verdict; ~820 source LOC + ~800 test LOC across 9 commits split into Session A (foundation) + Session B (live + reconciler + alerts + restart).

Read first (in order):
- This session's scoping report: reports/2026-06-01_n2_phase3_scoping.md (REFINED scope, LOC estimates, commit sequence, operator decisions).
- Phase 1a sub-diagnostic: git show 33da534:reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md (or on the rebased branch HEAD, reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md).
- Phase 1b sub-diagnostic: git show e1d38f8:reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1b.md (or on rebased branch).
- Architectural review §6 + §7: git show ade4dbc:reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md.

Memory anchors:
- [[bitunix-live-entry-path-pattern]] — N+1 entry-path patterns to mirror.
- [[bitunix-live-engine-build]] — current Stage 1 state.
- [[telegram-audit-success-is-confirmed-delivery]] — channel.push reuse pattern.
- [[branch-tests-must-cover-existing-fixtures]] — full pre-existing test suite must run against branch state.
- [[2026-06-01-n2-phase3-scoping]] — this scoping session's outcome.

Branch + worktree setup:
- Start a fresh worktree off origin/bitunix-live-exit-path-2026-05-29-rebased (HEAD 3016053 at scoping time; reverify):
  git worktree add ".claude/worktrees/n2-phase3-impl-<date>" -b "bitunix-live-exit-path-2026-05-29-impl" origin/bitunix-live-exit-path-2026-05-29-rebased
- All code changes go on the impl branch; rebase on top of latest main before merging.

Operator decisions to confirm at session start:
- Decision 6.1 (Finding #6.1 mitigation): default = stamp extra["result_source"] in _record_exit_outcome.
- Decision 6.2 (Finding #6.2 fix): default = extend insert_paper_trade_record with DB-lock retry.
- Decision 6.3 (split): default = Session A + Session B (NOT single-session).
- Decision 6.4 (get_pending_positions): default = extract as a public broker method (NOT use snapshot()).
- Decision 6.5 (docs merge): default = bundle docs into Phase 3 implementation merge.

Pre-flight verifications (run before writing any code; all read-only):
- G1 prod stable: ssh tc-prod-vm sudo journalctl -u trading-corp.service --since "<24h ago>" | grep -iE "(error|exception|traceback)" → expect benign-only.
- G2 paper placements: sqlite3 .../trading_corp.db "SELECT COUNT(*) FROM audit_event WHERE kind='paper_trade_recorded' AND ts > '<48h ago>'" → record count; OK if 0.
- G3 _handle_stuck_order reachable: grep _handle_stuck_order in brokers/bitunix.py + safety_notifier wiring in main.py.
- G4 risk-tier overlay status: confirm config/strategies.yaml md5 on prod matches origin/main.
- G5 C-1 critical-path: read BACKLOG.md P1 entries; verify no "blocks Stage-1" items active.

Execution sequence (Session A first; Session B may be a separate session):

SESSION A (foundation + paper-mode helper byte-identity):
  Commit 1: FillEvent additive fields (fee + venue_order_id) + broker_write plumbing fix.
  Commit 2: Path C revert — _place_live writes paper_trade_record with execution_mode=live + broker_order_id + entry_fee_usd.
  Commit 3: insert_paper_trade_record DB-lock retry (Finding #6.2 mitigation).
  Commit 4: _record_exit_outcome canonical helper (paper-mode only first; byte-identity test pinned).
  Test gate before merging Session A: full pre-existing test suite green (per [[branch-tests-must-cover-existing-fixtures]]).
  STOP-AND-REPORT to operator for Session A merge approval.

SESSION B (live + reconciliation + alerts + restart):
  Commit 5: _record_exit_outcome live-mode fork + Finding #6.1 result_source stamp + Finding #6.4 wiring.
  Commit 6: bitunix_position_reconciler.py extended with live-mode + Finding #6.3 empty-list disambiguation.
  Commit 7: Background sanity poll + get_pending_positions method extraction.
  Commit 8: 8 operational alerts on BitunixLifecycleNotifier + real-fee rendering on notify_close_out.
  Commit 9: _resume_live_positions cases (a)+(b); case (c) halt-and-page.
  Test gate before merging Session B: full pre-existing test suite green; live-mode behavior tests; reconciler divergence tests; restart-resume case (a)+(b) tests.
  STOP-AND-REPORT to operator for Session B merge approval.

Out of scope (DEFERRED to N+3):
- Layer 2 funding accrual (get_history_positions + funding_paid_usd accrual).
- Restart-resume case (c) — broker-closed-during-downtime auto-resolve.
- Lumibot's 5s background poll (60s sanity poll is the Stage-1 baseline).
- WS position channel (Stage-3 reuse audit).

Hard stops:
- Any prod-touching write attempted in Phase 3 implementation. Phase 3 is source-and-test only; deploy is a separate session.
- execution_mode flip from "paper" to "live" in any committed config. Phase 3 lands paper-default; live-flip is operator-gated post-merge.
- Auto_execute flips on tasty or any other division as a side effect.

Discipline standards (carried from scoping session):
- Delegate mechanical tasks to Sonnet sub-agents.
- Stop-and-report at semantic forks.
- Surface anomalies with diagnostic detail.
- No scope expansion mid-task.
- Tighter commits than feels normal — ship per-commit, not at end.
- Worktree isolation.

Output expected:
- Session A: 4 commits on bitunix-live-exit-path-2026-05-29-impl; pushed; test gate green; STOP for operator merge approval.
- Session B (next session if split): 5 commits on top of Session A's merge; pushed; test gate green; STOP for operator merge approval.
- Two deploy_log entries: Session A merge + Session B merge.
- Memory: [[bitunix-live-exit-path-pattern]] capturing Phase 3 patterns + premise corrections.
- BACKLOG update: P1 "N+2 Phase 3 implementation" RESOLVED; surface any new architectural questions as new P-something items.
- Confirmation: no prod changes, no execution_mode flip, no deploy attempts.
```

---

## 8. Honest gaps

These are uncertainties remaining after this scoping session. Surface; do not auto-resolve.

### Gap 8.1 — `get_history_trades` empty-list ACTUAL response shape

This scoping pass relied on the architectural review's Finding #6.3 framing and the Sonnet agent's read of `_fill_price_from_history` for the "avg_price = 0.0 on empty list" pass-through. A live integration test against the real BitUnix endpoint to confirm the empty-list shape (vs `null`, vs missing `tradeList` key, vs an error response wrapping a 0-element array) has NOT been run this session. Phase 3 implementation should add a synthetic-mock test AND, before promoting to prod, a real-endpoint smoke check against testnet (per architectural review Finding #8.1 — testnet reachability).

### Gap 8.2 — Reconciler latency budget

Phase 1b's recommended cadence (60s background poll) was chosen against lumibot's 5s baseline. The choice was Stage-1-sizing safe per architectural review Finding #7 table ("SAFE at $10K + single-position"). But — for the multi-symbol or multi-position Stage-2 expansion, 60s is too slow. Phase 3 should land 60s without a knob; a knob makes the divergence latency a moving target. Stage-2 expansion will need to re-tune.

### Gap 8.3 — `_resume_live_positions` semantics for case (c) operator-resolve

Phase 1b §4 says case (c) (broker-closed-during-downtime) writes `restart_resume_case_c_deferred` audit + halt + telegram, expecting operator to manually resolve. The "manually resolve" UX is undefined — does operator close on BitUnix UI + stamp the row by hand via a TUI command? Add a row manually via SQL? This UX gap should be addressed BEFORE Session B's case-(c) commit lands, ideally as a small dashboard surface ("Restart-pending position N — Approve / Modify / Reject") in line with the HITL surface direction from CLAUDE.md § 1.

**Recommendation:** defer the UX design to operator; ship the audit + halt + telegram in Session B Commit 9 and let case (c) trigger surface the UX gap if/when it fires for real. Add a P2 BACKLOG entry for the dashboard surface.

### Gap 8.4 — `_record_exit_outcome` byte-identity test scope

Phase 1b structured the helper to mirror `_record_placement_outcome`'s "paper-mode byte-identity before/after refactor" test pattern (N+1 commit 1). For the exit-side, byte-identity means: existing single-leg + multi-leg paper-replay runs produce IDENTICAL `paper_trade_record` updates before and after introducing the helper. This is loadbearing for the refactor — but the exit side has MORE call sites than the entry side (per §2.2 table — 5+ multi-leg sites vs 1 entry-side helper call). Phase 3 must structure the test to cover all sites.

### Gap 8.5 — Test gate run on the rebased branch this session

The rebase landed clean and was docs-only. **No tests were run on the rebased branch this session** (docs-only rebases don't change test surface). The branch is therefore "compilation-clean by inference, not by run." Phase 3 implementation session opens with the explicit `[[branch-tests-must-cover-existing-fixtures]]` rule — first work item is `.\scripts\run_capped.ps1 python -m pytest` baseline against the impl branch state to confirm the inherited test surface is green.

---

## 9. What this scoping session does NOT cover

- Phase 3 implementation itself (code changes to any Stage 1 surface).
- Execution_mode flips.
- Auto_execute flips on tasty or other divisions.
- Prod deploys.
- Resolution of operator decisions §6.1-§6.5 (surfaced; not resolved).
- Real-endpoint integration tests against BitUnix testnet or prod (Gap 8.1).
- UX design for case-(c) restart-resume (Gap 8.3).
- N+3 deferred items: Layer 2 funding, case (c) auto-resolve, 5s background poll, WS position channel.

---

## 10. Sources + memory anchors

- **Code on `origin/main` `f110c74`** verified file:line via this session's Sonnet sub-agent (20-item validation report; full output in scoping session transcript).
- **Phase 1a + 1b reports** read via `git show 33da534:...` and `git show e1d38f8:...`.
- **Architectural review §6 + §7 + §10** read via `git show ade4dbc:reports/2026-05-30_stage1_bitunix_live_engine_architectural_review.md`.
- **Memory anchors used:**
  - `[[bitunix-live-exit-path-phase1b]]` — Phase 1b scope (B) recommendation.
  - `[[bitunix-live-engine-build]]` — current Stage 1 state.
  - `[[stage1-deployed-to-prod-paper-mode]]` — prod is at redeploy3.
  - `[[bitunix-live-entry-path-pattern]]` — N+1 entry-path patterns.
  - `[[bitunix-risk-tier-and-leverage-pre-live]]` — risk-tier CANONICAL on main.
  - `[[stage1-first-17h-review-2026-05-31]]` — first-window prod-stable confirmation.
  - `[[branch-tests-must-cover-existing-fixtures]]` — full-suite run discipline.
  - `[[telegram-audit-success-is-confirmed-delivery]]` — channel.push semantics.
- **Rebase:** branch `bitunix-live-exit-path-2026-05-29-rebased` pushed by this session; HEAD `3016053` (3 commits onto `origin/main` `f110c74`); 1 stale BACKLOG commit `4a8b440` mechanically skipped.

*No code, config, or deploy changes made this session beyond report committal + rebase push.*
