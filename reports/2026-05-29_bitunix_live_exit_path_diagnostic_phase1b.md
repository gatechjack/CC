# BitUnix Stage-1 Session N+2 — Phase 1b sub-diagnostic + A/B/C scope

**Date:** 2026-05-29 · **Type:** read-only structural audit (no code/config/deploy changes) · **Branch:** `bitunix-live-exit-path-2026-05-29` off `main` · **Status:** Phase 1b COMPLETE — covers structural questions #4 (reconciliation), #5 (cost accrual), #3 (restart-resume — Stage-1 readiness audit numbering; this was called "#6" in the Phase 1a handoff prompt), and #7 (alerts). Closes with A/B/C scope recommendation. **STOP-AND-REPORT for operator confirmation before any Phase 3 implementation.**

**Companion:** `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md` (structural questions #1, #2, #3, #8, #9) — Phase 1a's structural decisions stand independently; Phase 1b refines scope around them.

**Pre-confirmed operator decisions (from session prompt, not re-litigated):**
- **Path C: CONFIRMED.** Live entries write `paper_trade_record` with `extra.execution_mode='live'` + `extra.broker_order_id`. Reverses N+1 commit 3's "no paper_trade_record on live path" decision via a small additive commit folded into the N+2 implementation.
- **HITL on exits: CONFIRMED no HITL.** Elevated `(live, exit)` telegram suffix + first-N counter (counter primitive already exists on entry-path branch, `live_orders_placed` agent_state key).
- **Phase 1a structural recommendations stand:** canonical `_record_exit_outcome` helper; in-place fork (not parallel executor); async `_execute_live_exits` follow-up step.

---

## TL;DR for Phase 1b

1. **Premise correction (LARGE).** The Phase 1a handoff prompt and the next-session prompt both assumed the broker-write branch had implemented lumibot's `do_polling` 5s reconciliation loop. **It has not.** Our broker-write branch (`87dac50`) implements EVENT-DRIVEN fill observation only (`_observe_fill` called once after `place_order`), no background sweep, no diff engine, no `get_history_positions`. This makes #4 a meaningful "build the loop" task — about 30% smaller than lumibot's because our state model is simpler (single open position per division), but unquestionably new code.
2. **Premise correction (SMALL, high-leverage).** Broker-write's `_observe_fill` already retrieves fee per fill via `get_history_trades`, sums it correctly in `_fill_price_from_history` (`brokers/bitunix.py:795-808` on broker-write branch), and then **DISCARDS the fee value at the call site** (`:598`, the `_fee` underscore). `FillEvent` (`persistence/models.py:71-79` on main) has no `fee` field. **Per-trade entry+exit fee capture is a one-line plumbing fix** (add `fee: float = 0.0` to FillEvent + drop the underscore at `:598`). #5 entry/exit fee accrual becomes nearly-free; funding remains the real cost-accrual work.
3. **Premise correction (MEDIUM).** N+1's `_place_live` (entry-path branch `bitunix_futures_observer.py:2466-…`) explicitly stamps `intent_payload["execution_mode"] = "live"` into the audit, but the comment at `:2422` confirms it still does NOT write `paper_trade_record`. **Path C revert is a concrete ~10-line additive commit**: after `data_exec.place(order)` returns successfully, also call `db.insert_paper_trade_record(record.to_db_row(), ...)` with `record.extra["execution_mode"] = "live"` + `record.extra["broker_order_id"] = fill_event.order_id` + `record.extra["broker_venue_order_id"] = venue_order_id`. Concretizes Phase 1a's recommendation; no design ambiguity left.
4. **Branch landscape (LARGER than next-session prompt suggested).** None of the four unmerged Session-29 feature branches stack on each other — entry-path, safety, broker-write, and N+2 are all off `main` directly. `brokers/bitunix.py` is 475 lines on main / entry-path / safety / N+2 but 969 lines on broker-write. **N+2's structural recommendations (canonical helper, in-place fork, `data_exec.place(reduce_only=True)`) all assume broker-write's `place_order` is present** — which is only true on the broker-write branch. **The merge-sequence prerequisite is non-optional**, not a "clean answer" — Phase 3 cannot start until broker-write is on the base N+2 builds on. Surfaced fully in §6.
5. **A/B/C recommendation: (B) Narrowed.** Full reasoning in §7. Net: live exit path + Path C revert + #7 alerts + minimal-shape #4 (event-driven divergence-check post-fill, no background poll) + minimal-shape #5 (per-fill fee plumbing only, funding deferred) + restart-resume #3 deferred via explicit "no restart during live position" operational constraint. Defers heavy infra (5s background poll loop, funding aggregation, restart-from-broker-truth) to N+3/N+4 once an unambiguous first-live-trade has happened.

---

## 1. Branch landscape + merge-sequence prerequisite (extends Phase 1a §1)

Verified via `git log --oneline main..<branch>` and `git show <branch>:trading_corp/brokers/bitunix.py | wc -l`:

| Branch | Parent | Commits | `brokers/bitunix.py` line count | Key contents |
|---|---|---|---|---|
| `main` | — | — | 475 | read-only stub; `place_order` raises `NotImplementedError` |
| `bitunix-live-engine-stage1-broker-write` | `500cc1e` (main) | 2 | **969** | `place_order` real; `_observe_fill`; `get_pending_positions`; `get_history_trades`; `cancel_all_orders`; `flash_close_position`; `flatten`; `BitunixPositionModeMismatch` (now in `bitunix_exceptions.py` post-`87dac50`) |
| `bitunix-orderpath-safety-2026-05-29` | (main-era) | 1 | 475 | mode-mismatch consumer + `flatten_division`; assumes broker-write's exception class |
| `bitunix-live-entry-path-2026-05-29` | `aa91d48` (main) | 11 | 475 | `execution_mode` YAML + observer kwarg; `_record_placement_outcome` helper; `_place_live`; HITL gate first N=10; `StrategyState.from_persistence`; safety_notifier slot; 17 swap sites |
| `bitunix-live-exit-path-2026-05-29` (THIS) | `aa91d48` (main) | 3 | 475 | Phase 1a sub-diagnostic + backlog + next-session prompt (docs only) |

**Critical implication.** Entry-path's `_place_live` (`bitunix_futures_observer.py:2466+`) calls `data_exec.place(order)` whose `place` ultimately hits `BitunixBroker.place_order` — which on the entry-path branch's checkout still raises `NotImplementedError` (entry-path doesn't include broker-write's commits). The entry-path branch is INTEGRATION-TESTED only against `PaperBroker` because `--live` mode would crash on first order. This is a known property (entry-path is paper-default; live mode requires broker-write merged first), but it makes the merge-sequence prerequisite for N+2 unavoidable, not optional:

**Required merge sequence before Phase 3 of N+2 can start:**

1. **C-1 credential rotation branches** (`c1-bitunix-cred-rotation`, `c1-apify-cred-rotation`) → main. *(Independent; can merge in any order.)*
2. **`bitunix-orderpath-safety-2026-05-29`** → main. *(Mode-mismatch consumer + flatten_division; depends only on broker-write's exception class identity, resolved per `4aa5e57` backlog note.)*
3. **`bitunix-live-engine-stage1-broker-write`** → main. *(Brings `place_order` live; required by everything below.)*
4. **`bitunix-live-entry-path-2026-05-29`** → main, ideally after a rebase onto merged broker-write+safety. *(Entry-path needs `place_order` to actually call data_exec.place from `_place_live`.)*
5. **N+2 implementation: rebase `bitunix-live-exit-path-2026-05-29` onto merged main**, then layer the Phase 3 commits on top. *(N+2 needs `paper_trade_record` writes to come from `_place_live` per Path C — that revert is itself folded into the N+2 commits, on top of merged entry-path.)*

Alternative considered: stack N+2 implementation on top of `bitunix-live-entry-path-2026-05-29` without going through main, then merge the stack later. **Rejected** — entry-path's `_place_live` can't reach a working broker on its own branch, so any N+2 test that touches the live path would be fictional (it would assert on a `NotImplementedError`-raising stub). The merge-sequence path keeps test reality honest.

**Operator decision required at Phase 2 boundary (before Phase 3):** confirm the merge sequence above OR identify an alternative branching strategy. Phase 3 is blocked until this is decided.

---

## 2. Phase 1b #4 — Post-trade reconciliation

### Lumibot reference (from reuse audit `runbooks/2026-05-29_bitunix_live_reuse_audit.md`)

**Reuse-audit § B (lumibot brokers/bitunix.py):**

> **`do_polling()` (`:544-605`) — the reconciliation engine.** Polls broker positions (`sync_positions`) + open orders every 5s, diffs against tracked state, dispatches `NEW/PARTIALLY_FILLED/FILLED/CANCELED/ERROR`. Orphan handling: tracked orders absent from the broker list → dispatch CANCELED (`:596-605`). **`_first_iteration` branch = restart-resume from broker truth.**
>
> **Partial-fill state machine (`_parse_broker_order` `:458-540`):** reads `qty` (total) vs `tradeQty` (executed) + `avgPrice`; status map includes `PARTIALLY_FILLED` (`:436-456`); `do_polling` fires `PARTIALLY_FILLED_ORDER` with `filled_quantity`. This is the partial-fill tracking the readiness audit flagged as absent.

### Our current state on broker-write branch (`bitunix-live-engine-stage1-broker-write`)

- ✅ **Event-driven fill observation present.** `_observe_fill` at `brokers/bitunix.py:766-793` polls `get_order_detail` until terminal/partial status (default 12 polls × 1.0s) then derives VWAP + fee + filled_qty from `_fill_price_from_history` (which calls `get_history_trades`).
- ✅ **Partial-fill awareness present.** `_observe_fill` returns `(status, filled_qty, avg_price, fee)`; status can be `PART_FILLED` (encoded in venue suffix `bitunix_futures:part_filled` at `:608`); `filled_qty` reflects `tradeQty` from order detail.
- ✅ **`get_pending_positions` present** at `:362-401`. Returns broker-truth list of open positions.
- ❌ **No background reconciliation loop.** No 5s sweep, no diff engine, no orphan dispatch. The only fill-observation is the one-shot call inside `place_order`.
- ❌ **No idempotency tracking across reconciliation.** Each `_observe_fill` is independent; there's no "have I already reconciled this order_id" check.

### Reconciliation policy recommendation

**Cadence: hybrid (HEAVILY event-driven, lightly polled).**

- **Event-driven (primary):** `_record_exit_outcome` calls the reconciler immediately after `data_exec.place(reduce_only=True)` returns its `FillEvent`. The reconciler:
  1. Reads the fresh `paper_trade_record` row (just stamped).
  2. Calls `bitunix_broker.get_history_trades(order_id=fill_event.order_id)` to retrieve broker-truth fills.
  3. Compares broker-truth (qty + price summed) against the row's stamped `result_price` + leg-fraction-implied qty.
  4. Writes `reconciliation_check` audit row with `divergence_pct_price`, `divergence_pct_qty`, `verdict ∈ {match, divergent, missing}`.
  5. On `divergent` or `missing`: write `reconciliation_divergence_detected` audit + push elevated telegram + set `bitunix_broker._halt_new_orders` latch (broker self-halt; existing primitive on broker-write `:551`).
- **Light background poll (secondary, Stage-1 minimal):** a single periodic check (e.g. every 60s, not lumibot's 5s) reads `get_pending_positions` and `paper_trade_record WHERE result IS NULL AND extra_json LIKE '%execution_mode%live%'` and asserts:
  - Every live-open paper_trade_record row has a matching broker position (by symbol + side + qty-within-tolerance) — else write `position_missing_on_broker` audit + halt.
  - Every broker position has a matching live-open paper_trade_record row — else write `orphan_broker_position_detected` audit + halt.
  - This is a CHEAP sanity check, NOT a full diff-engine; runs in `main.py`'s existing background task surface (alongside the existing reconciler primitives).
- **Restart-resume integration (see §4):** the first poll after process start runs the restart-resume logic; subsequent polls run the lighter sanity check above.

**Tolerance:**
- Price: ±0.1% (10 bps) — Stage-1 is single-position market reduce-only, slippage SHOULD be tiny; anything larger is a real divergence signal worth surfacing.
- Qty: exact match (per-fill granularity from `get_history_trades`) — partial-fill aware, but the SUM should equal the placed qty to 1e-6 precision.
- Fee: capture-only Stage-1 (don't tolerance-check; just record the real number per-fill).

**Divergence handling:** **AUDIT + TELEGRAM + HALT broker self-latch.** No auto-correct. The broker self-halt is the existing primitive at `brokers/bitunix.py:551` (`_halt_new_orders` raises on subsequent `place_order` calls). This puts the bot in observe-only mode until operator manually resolves; consistent with Phase 1a §9c StrategyState semantics ("halt does NOT block exits; flatten DOES force-close"). Auto-correct would mean the reconciler placing compensating orders — far beyond Stage-1 scope and a known foot-gun (corrects-the-symptom-not-the-cause).

**Idempotency:** dedup via the `broker_order_id` stamped in `paper_trade_record.extra_json` (Path C). The reconciler's first read of broker truth resolves to specific order_ids; subsequent reads for the same order are no-ops if the row's `result IS NOT NULL` (closed) or `extra.reconciled_at IS NOT NULL` (already checked, no divergence).

### Schema changes required

**None to the SQL DDL.** Path C already lands `extra_json["execution_mode"]` + `extra_json["broker_order_id"]` + `extra_json["broker_venue_order_id"]`. #4 adds:

- `extra_json["reconciled_at"]: ISO-8601 string` — written by the reconciler after a clean check.
- `extra_json["fill_count"]: int` and `extra_json["fill_aggregate_qty"]: float` and `extra_json["fill_aggregate_fee_usd"]: float` — from `get_history_trades` rollup.
- New `audit_event` kinds: `reconciliation_check`, `reconciliation_divergence_detected`, `orphan_broker_position_detected`, `position_missing_on_broker`. All written by `LoggerAgent.log_event` per CLAUDE.md § Adding a new audit event kind.

### Scope estimate

- **Event-driven reconciler inside `_record_exit_outcome`:** ~80 LOC (the helper + audit kinds + telegram extension).
- **Background sanity-check poll:** ~120 LOC (background task wiring + the diff function + halt hookup).
- **Tests:** ~150 LOC (unit tests for the reconciler with mocked broker; integration test for the divergence-halt path).
- **Total #4 estimate:** ~350 LOC + tests across 2-3 commits.

**Bespoke gap:** the comparison against `paper_trade_record` (polymorphic for paper+live via Path C tag) is YOURS — lumibot's diff is broker↔lumibot's own state model. Our schema fit is one-loop-one-table (Phase 1a §2/§9 Path C), which is structurally simpler than lumibot.

---

## 3. Phase 1b #5 — Cost accrual

### Lumibot reference (from reuse audit)

**Reuse-audit § A (BitunixOfficial open-api WS spec) and § B (lumibot `tools/bitunix_helpers.py`):**

> **Position-channel schema (WS `:131-150`) carries per-position `funding` AND `fee`** plus `realizedPNL/unrealizedPNL/qty/entryValue` — the cost-accrual data source.
>
> `get_history_trades` (`:562-597`, **fills**), `get_history_positions` (`:472-504`, **carries funding+fee per closed position**), `get_order_detail`, `get_pending_orders`, `get_position_tiers`, `batch_order`.
>
> **Fills + cost data:** `get_history_trades` (fills per order/position); `get_history_positions` and the WS `position` channel carry **`funding` and `fee` per position** → that's where live cost-accrual reads from (lumibot fetches but doesn't accrue — we build the accrual).

### Our current state on broker-write branch

- ✅ **Per-fill fee retrieved.** `get_history_trades` at `brokers/bitunix.py:749-764` returns `tradeList` with `qty`, `price`, `fee` per fill. `_fill_price_from_history` at `:795-808` sums them.
- ❌ **Per-fill fee DISCARDED.** `place_order` at `:598` writes `status, filled_qty, avg_price, _fee = await self._observe_fill(...)` — the fee is captured then thrown away. `FillEvent` at `persistence/models.py:71-79` has fields `(order_id, symbol, side, qty, price, ts, venue)` — **no `fee` field**.
- ❌ **No `get_history_positions` implemented.** Closed positions with `funding` + `fee` totals aren't reachable.
- ❌ **No WS position channel** (broker-write is REST-only; WS is Stage-3 per reuse audit § Stage-1 checklist).
- ⚠️ **Funding rate fetched but NOT accrued.** `BitunixBroker.get_funding_rate` exists on main (`brokers/bitunix.py:437-489` on broker-write) and is used by the HTF gate for trade decisions — but funding PAYMENTS are not booked to PnL.

### Booking approach recommendation

**Two layers, fundamentally different difficulty:**

**Layer 1 — Per-fill fee plumbing (Stage-1 MUST, near-free).**

1. Add `fee: float = 0.0` to `FillEvent` dataclass at `persistence/models.py:71-79`.
2. In broker-write's `place_order` (`:598`, `:609`), capture the `_fee` and pass it through: `fee=_fee`.
3. Inside `_record_exit_outcome` (and Path C's amendment to `_place_live`), stamp the fee into `paper_trade_record.extra_json["fee_usd"]` for entries; for multi-leg exits, sum per-leg into `extra_json["exit_fee_usd"]`.
4. Update `bitunix_lifecycle_notifier.notify_close_out` to render real fee numbers in the live branch (replace "Fees: not tracked in paper" with the actual number when prefix == LIVE).

**Estimate: ~30 LOC + ~30 LOC tests across 1 commit.** This is true low-hanging fruit; arguably worth merging even outside N+2 scope.

**Layer 2 — Funding accrual (Stage-2 work; DEFER from N+2).**

Funding requires either:
- Implementing `BitunixBroker.get_history_positions` (~50 LOC) + a periodic pull (~80 LOC) that books `funding_paid_usd` to `paper_trade_record.extra_json` on position close; OR
- Implementing WS position-channel subscription (Stage-3 work per reuse audit; ~250+ LOC for the WS layer plus reconnect, heartbeat, etc).

REST poll via `get_history_positions` is the smaller Stage-2 option. Funding accrual is a real ~200-300 LOC build with its own audit kind (`funding_accrual_recorded`), aggregate reconciliation vs broker statements, and tax-grade per-funding-interval records. **Defer from N+2 to N+3** per the original readiness-audit gap (`§6 Stage 2 (MEDIUM)`).

### Coupling to #4

Layer 1 (fee plumbing) couples ZERO additional work to #4 — fees ride the same `FillEvent` plumbing as the rest of the reconciliation data.

Layer 2 (funding accrual) couples partially to #4's background poll — same cadence, same `get_pending_positions` neighborhood, but requires the additional `get_history_positions` endpoint. **If #4 lands the background poll, Layer 2 becomes ~50% cheaper later** (poll wiring is reused). But the booking + reconciliation work is still Stage-2-scoped.

### Schema changes required

None to DDL. Layer 1 lives in `extra_json["fee_usd"]` + `extra_json["exit_fee_usd"]`. Layer 2 would add `extra_json["funding_paid_usd"]` + `extra_json["funding_intervals_count"]` (still no DDL change).

### Scope estimate

**Layer 1 (in N+2): ~60 LOC + ~30 LOC tests = 1 commit.**
**Layer 2 (DEFER to N+3): ~250 LOC + ~150 LOC tests = 3 commits.**

**Recommendation: Layer 1 IN N+2; Layer 2 DEFERRED.** Tax-grade records need real fees from trade #1 (per readiness audit §9) — Layer 1 gets fees in. Funding is per-interval and accrues slowly; deferring 1-2 weeks costs at most a handful of funding intervals while Stage-1 is at $10-$50 sizing.

---

## 4. Phase 1b #3 — Restart-resume from broker truth (Stage-1 readiness audit item #3)

### Lumibot reference (from reuse audit)

**Reuse-audit § B:**

> **`_first_iteration` branch = restart-resume from broker truth.**
>
> (Inside `do_polling` at `:544-605`; the first iteration takes a different branch that reads broker positions and reconciles to tracked state, vs subsequent iterations that diff incrementally.)

### Our current state

- ✅ **Halt state persists across restart.** Entry-path branch's `StrategyState.from_persistence` at `persistence/models.py:150-181` loads `halted` + `halt_reason` from `agent_state`. `realized_pnl` is summed from audit rows on each call (per the inline comment at `:144`), so it's restart-safe by construction.
- ✅ **HITL counter persists across restart.** Entry-path's `live_orders_placed` agent_state key (`bitunix_futures_observer.py:235`); the live-exits counter for the elevated-(live, exit) suffix would use the same primitive with a new key (e.g. `live_exits_executed`).
- ❌ **Open broker positions NOT reconciled on startup.** No code reads `get_pending_positions` at startup. If a process restarts while a live position is open, the bot will:
  - Not see the position (no row in `paper_trade_record` if pre-Path C, or a stale "result IS NULL" row post-Path C).
  - Not place a new exit (replay loop won't fire — pre-Path C, no row; post-Path C, row exists but no reconciliation to broker-truth state).
  - Potentially place a NEW ENTRY if a fresh signal arrives, doubling exposure.

### Recommended restart-resume policy

**On bot start, BEFORE the replay loop processes any record:**

1. Read `bitunix_broker.get_pending_positions()` → list of open broker positions.
2. Read `paper_trade_record WHERE result IS NULL AND extra_json LIKE '%"execution_mode":"live"%'` → list of tracked live open rows.
3. Three-case match by `(symbol, side)`:
   - **(a) Match (broker has it, row has it):** resume tracking. Verify `extra.broker_order_id` matches; if qty diverges, write `restart_resume_divergence` audit + halt. If clean, write `restart_resume_executed` audit row with the match details + push telegram.
   - **(b) Broker-only orphan (broker has it, no row):** ORPHAN — escalate. Path C should prevent this (every live entry writes a row), so this case is an integrity violation. Audit `orphan_broker_position_on_restart` + halt broker + telegram with elevated priority. Operator manually resolves (close on BitUnix UI + reconcile records, or stamp a row by hand).
   - **(c) Row-only (row exists, no broker position):** broker closed the position during downtime (TP/SL hit while bot was off; or operator manually closed; or liquidation). Use `get_history_trades(order_id=row.extra.broker_order_id)` + `get_history_positions` (Stage-2; OR fall back to "guess from `get_history_trades` last fill") to reconstitute the close ts + price + fee + funding. Write `restart_resume_position_closed_during_downtime` audit + populate the row's `result`, `result_ts`, `result_price`, `actual_pnl_dollars`, `actual_r_multiple` + telegram.

**Sequencing constraint:** restart-resume MUST run BEFORE the replay loop starts processing rows. Otherwise the replay loop sees `result IS NULL` for case (c) rows and tries to classify them via bar-walk, which would silently produce wrong results (the bars during downtime are real, but the broker closed at a price that may not match the classifier's projection).

**Implementation site:** new function `_resume_live_positions(deps)` in `bitunix_futures_observer.py` (or a small new module `live_resume.py`); called from `main.py` startup BEFORE the `paper_trade_replay.replay_pending_paper_trades` cron starts.

### Stage-2 case-(c) limitation

Case (c) requires `get_history_positions` which is NOT on broker-write. **N+2 minimum-viable restart-resume**: handle cases (a) and (b) only; for case (c), write `restart_resume_case_c_deferred` audit + halt + telegram operator to manually resolve via BitUnix UI. This is the "no restart during live position" operational constraint surfaced in the prompt — but PARTIAL: cases (a) and (b) work, only (c) needs operator intervention.

**Alternative N+2 scope:** include `get_history_positions` (+50 LOC) + minimal case-(c) close reconstitution (~80 LOC) to fully automate. This is the cheaper end of #5 Layer 2's "Funding accrual" work — funding + fee per closed position arrive together. So if #5 Layer 2 lands in N+2, case (c) becomes automatic. If #5 Layer 2 defers to N+3, case (c) defers to N+3 with case-(c) restart triggering the halt-and-operator-resolve path.

### Schema changes required

None to DDL. New audit kinds: `restart_resume_executed`, `restart_resume_divergence`, `orphan_broker_position_on_restart`, `restart_resume_position_closed_during_downtime`, `restart_resume_case_c_deferred`.

### Scope estimate

- **Cases (a) + (b) only (recommended for N+2):** ~120 LOC + ~80 LOC tests = 1-2 commits.
- **Cases (a) + (b) + (c) fully automated:** ~250 LOC + ~150 LOC tests = 2-3 commits (couples to #5 Layer 2).

**Recommendation: cases (a) + (b) in N+2; case (c) defers with operator-halt-and-resolve.** Stage-1 is single-position, $10-$50 sizing, very low probability of restart-during-open-position. The cheap-and-safe path is: handle the common cases automatically, halt-and-page on the uncommon one.

---

## 5. Phase 1b #7 — Operational alerts surface

### Current state — `bitunix_lifecycle_notifier.py` (read on main; 191 LOC, 2 public methods)

`BitunixLifecycleNotifier(channel, db_url, paper_mode=True)` at `comms/bitunix_lifecycle_notifier.py:17-191`:

- `__init__` sets `self._prefix = "📄 [PAPER]" if paper_mode else "💸 [LIVE]"` (`:41`). **Live prefix already exists, gated by constructor kwarg.**
- `notify_tp_fill(order_id, symbol, side, leg, entry_price, leg_price, r_so_far, old_sl, new_sl, new_sl_label, percent_closed)` at `:47-93` — TP1/TP2 partial fill, paper-only language.
- `notify_close_out(order_id, symbol, side, result, entry_price, exit_price, exit_reason, path, r_multiple, pnl_dollars, held_seconds)` at `:95-168` — close-out; renders "Fees: not tracked in paper / Funding: not tracked in paper" verbatim at `:158-159`.
- `_send(body, notification_type, order_id)` at `:174-191` — prepends prefix; calls `self._channel.push(full, audit_path=f"lifecycle_{notification_type}", audit_context={"order_id": order_id})`. **Audit + confirmed-delivery semantics live in `channel.push`** (per `[[telegram-audit-success-is-confirmed-delivery]]`).

### Recommended audit kinds + telegram methods for N+2

**Naming convention: extend the existing `notify_*` pattern; existing `notification_type` field becomes the audit kind suffix (`lifecycle_*` prefix is the audit_path).**

| Method (new) | Audit kind | Trigger site | Body content |
|---|---|---|---|
| `notify_exit_order_placed(order_id, symbol, side, exit_kind, qty, parent_order_id)` | `lifecycle_exit_order_placed` | `_execute_live_exits` BEFORE `data_exec.place(reduce_only=True)` | "SYMBOL side · exit:tp1 placed (live, exit)\nqty Y from parent ORDER_ID" |
| `notify_exit_order_filled(order_id, ..., real_fill_price, real_qty, real_fee_usd)` | `lifecycle_exit_order_filled` | `_execute_live_exits` AFTER `_record_exit_outcome` returns broker-confirmed | "SYMBOL side · exit:tp1 filled @$X (live, exit #N/10)\nqty Y, fee $Z, parent_order_id ORDER" |
| `notify_exit_order_rejected(order_id, ..., bitunix_code, bitunix_msg)` | `lifecycle_exit_order_rejected` | `_execute_live_exits` on `BitunixAPIError` non-idempotent | "SYMBOL · EXIT REJECTED (live)\ncode 30038: TP/SL amount > position size\nparent_order_id ORDER" |
| `notify_exit_partial_fill(order_id, ..., expected_qty, actual_qty)` | `lifecycle_exit_partial_fill` | `_record_exit_outcome` when `fill_event.venue.endswith(":part_filled")` | "SYMBOL · EXIT PARTIAL (live, exit)\nexpected Y, filled X. Pending continued fill." |
| `notify_position_closed_with_pnl(order_id, ..., total_fee_usd, total_funding_usd, net_pnl_usd)` | `lifecycle_position_closed_with_pnl` | After ALL legs closed; replaces paper `notify_close_out` on live branch | "SYMBOL · CLOSED · TP3 (WIN) [LIVE]\nGross PnL: +$X · Fees: -$Y · Funding: -$Z · Net: +$W" |
| `notify_reconciliation_divergence(order_id, ..., divergence_pct, kind)` | `lifecycle_reconciliation_divergence` | #4 reconciler on `divergent` verdict | "🚨 RECON DIVERGENCE (live)\nORDER_ID at SYMBOL\nprice divergence 0.34% > 0.10%\nbroker_halt_set=True" |
| `notify_cost_accrual_recorded(order_id, ..., fee_usd, funding_usd)` | `lifecycle_cost_accrual_recorded` | #5 Layer 2 on funding-interval pull (N+3 if deferred) | "SYMBOL · funding interval -$X (live)\ncumulative funding -$Y on parent ORDER" |
| `notify_restart_resume_executed(matched_count, orphan_count, case_c_count)` | `lifecycle_restart_resume_executed` | #3 restart-resume on completion | "🔄 RESTART RESUME (live)\nmatched N positions, M orphans escalated, K closed-during-downtime" |

**Notifier extension shape:** add `BitunixLifecycleNotifier` constructor kwarg `live_exit_counter_getter: Callable[[], int] | None = None` so `notify_exit_order_filled` can render `(live, exit #N/10)` vs `(live, exit, monitor-mode)` based on counter state (matching the Phase 1a recommendation in §8 + reusing the entry-path HITL-counter pattern).

**Reuse principle (Phase C, `[[telegram-audit-success-is-confirmed-delivery]]`):** all 8 new methods route through the existing `_send` helper → `channel.push` → confirmed-delivery audit semantics inherited for free. **No new auditing code; only new payload shapes.** This is the cheapest of the four Phase 1b items.

### Schema changes required

None to DDL. 8 new audit kinds (extends existing `lifecycle_*` convention).

### Scope estimate

- **8 new `notify_*` methods (~30 LOC each, mostly format strings):** ~250 LOC.
- **Wiring into #4 reconciler + #3 restart-resume + `_record_exit_outcome` + `_execute_live_exits`:** ~50 LOC across 4 sites.
- **Tests for the live branch render + counter elevation:** ~150 LOC.
- **Total #7 estimate:** ~450 LOC + tests across 1-2 commits.

**Recommendation: ALL 8 methods in N+2.** Telegram + audit kinds is the "operator can see what's happening" surface — under-providing it is unsafe in live mode. Cost is mostly formatting. The counter-aware `(live, exit #N/10)` suffix is the only behavioral subtlety.

---

## 6. Premise validation against Phase 1a

### Phase 1a check #1: `_record_exit_outcome` data shape supports reconciliation-ready output + cost-accrual hooks?

**YES with one adjustment.** Phase 1a's recommended signature was `_record_exit_outcome(row, resolved, leg=None, db_url=...) → None`. For #4 + #5 Layer 1, the signature needs to also accept and use the `FillEvent` returned by `data_exec.place(reduce_only=True)` so the helper can:

- Stamp `extra_json["broker_order_id_exit_<leg>"]` for #4 reconciler dedup.
- Stamp `extra_json["exit_fee_usd_<leg>"]` for #5 Layer 1 capture.
- Stamp `extra_json["reconciled_at"]` after the immediate reconciler check.

**Revised signature:** `_record_exit_outcome(row, resolved, fill_event, leg=None, db_url=...) → None`. Backward-compat note: paper-mode calls pass `fill_event=None`; the helper branches at the start.

### Phase 1a check #2: `_execute_live_exits` async follow-up has clean place to invoke reconciler, or is reconciliation a separate poll loop?

**Both, with clear separation.** Per §2 above:

- **Event-driven reconciliation (primary)** lives INSIDE `_execute_live_exits` — after `data_exec.place` returns `FillEvent`, call `_reconcile_single(row, fill_event)` BEFORE writing the final audit row. This is the "did the trade actually happen + at the right price + with the right fee" check, runs at the natural boundary.
- **Background sanity-check poll (secondary)** is a SEPARATE async task launched at `main.py` startup, runs every 60s, doesn't touch `_execute_live_exits`. Its job is catching divergences that develop AFTER the event-driven check passed (e.g. broker closed a position via liquidation while the bot was idle).

**Clean integration:** `_execute_live_exits` is the place for fast/blocking reconciliation; the background poll is for slow/background drift detection. Phase 1a's async follow-up step is the right home for the former.

### Phase 1a check #3: Path C broker_order_id captured at entry-place time, persists across replay-loop reads?

**YES, with the concrete revert shape.** N+1's `_place_live` at `bitunix_futures_observer.py:2466+` (entry-path branch) returns from `data_exec.place(order)` with a `FillEvent` whose `order_id` is OUR clientId-derived id (line `:614` on broker-write returns `order_id=order.id` not `venue_order_id`). The Path C revert:

1. After `_place_live`'s `data_exec.place` returns:
   ```python
   record = PaperTradeRecord.from_proposed_order(order, fill_event, …)
   record.extra["execution_mode"] = "live"
   record.extra["broker_order_id"] = fill_event.order_id  # our clientId-derived
   record.extra["broker_venue_order_id"] = fill_event.venue_order_id  # NEW field on FillEvent (TBD)
   record.extra["entry_fee_usd"] = fill_event.fee  # from #5 Layer 1
   db.insert_paper_trade_record(record.to_db_row(), db_url=self.db_url)
   ```
2. Replay loop reads `paper_trade_record WHERE result IS NULL`; `extra_json["execution_mode"]` is read in `_classify` and `_classify_v2_multi_leg` (which already accept `extra` per Phase 1a §1).
3. Exit helper `_record_exit_outcome` reads `row.extra["execution_mode"]` to fork paper/live, and `row.extra["broker_order_id"]` to populate `parent_order_id` in the exit's `ProposedOrder.extra` (for audit lineage).

**Note on `venue_order_id`:** broker-write's `place_order` at `:608` encodes `venue_order_id` only via `venue` string suffix (e.g. `bitunix_futures:part_filled`); the explicit BitUnix-side `orderId` is captured locally at `:592` but not exposed in `FillEvent`. **Recommended additive change** alongside #5 Layer 1: also add `venue_order_id: str | None = None` to `FillEvent` and return it from broker-write's `place_order`. This is the "is broker_order_id reachable from the row" question — answered with a small plumbing change, no design ambiguity.

**Premise check verdict:** ALL THREE Phase 1a structural decisions stand. Minor refinements (signature additions, FillEvent field additions) folded in cleanly.

---

## 7. A/B/C scope recommendation

### Scope options carried forward from Phase 1a handoff

- **(A) Full N+2:** live exit path (Phase 1a §1-§3) + Path C revert + #4 (event-driven + background poll) + #5 (Layer 1 fee plumbing + Layer 2 funding) + #3 (cases a+b+c) + #7 (all 8 alerts).
- **(B) Narrowed N+2:** live exit path + Path C revert + #4 (event-driven only) + #5 (Layer 1 only) + #3 (cases a+b; case c halts-and-pages) + #7 (all 8 alerts).
- **(C) Refactor blocker surfaced.** (Used if Phase 1b reveals a structural impossibility.)

### Recommendation: **(B) Narrowed N+2**

**No refactor blocker surfaced** (rejecting C). Phase 1b confirmed the Phase 1a structural decisions are sound; the only blockers are sequencing (merge order) and concrete schema choices, both resolvable.

**Why (B) over (A):**

1. **Stage-1 sizing makes the deferred items low-risk in the gap.** At $10-$50 risked per trade, a 2-week deferral of:
   - Funding accrual (couple of funding intervals on a tiny position) — at most pennies of unaccounted cost, fully recoverable from broker statements when Layer 2 lands.
   - Restart-resume case (c) — small position-by-position halt-and-resolve cost; operator-paging is acceptable for an uncommon scenario at the smallest sizing.
   - 5s background poll — replaced by 60s baseline + event-driven, which catches everything within an acceptable window for tiny sizing.
2. **Each deferred item has a clean follow-up landing pad in N+3.** Layer 2 funding + case (c) restart are coupled (both depend on `get_history_positions`); they can land as a single small N+3 branch. The lumibot 5s background poll is also N+3 scope.
3. **(B) keeps Phase 3 within one realistic session.** Estimate by item:
   - Live exit path (Phase 1a structural): ~400 LOC + ~250 LOC tests = 3-4 commits
   - Path C revert: ~30 LOC + ~30 LOC tests = 1 commit
   - #4 event-driven reconciler: ~80 LOC + ~80 LOC tests = 1 commit
   - #5 Layer 1 fee plumbing: ~60 LOC + ~30 LOC tests = 1 commit
   - #3 cases a+b restart-resume: ~120 LOC + ~80 LOC tests = 1-2 commits
   - #7 all 8 alerts: ~250 LOC + ~150 LOC tests = 1-2 commits
   - **Net (B): ~940 LOC + ~620 LOC tests across 8-11 commits.** Tight but tractable for one focused session if the merge sequence is clean.
4. **(A) adds an additional ~600 LOC + ~400 LOC tests (Layer 2 + case c + background poll) for ~15-18 total commits — likely a 2-session split anyway.** Stage-1 sizing economics don't justify the extra session-day to land Layer 2 in this round.

### Merge-sequence prerequisite (recurring surface)

**REPEAT, because this is load-bearing.** Phase 3 of N+2 CANNOT START until:

1. Broker-write + safety merged to main, AND
2. Entry-path rebased onto merged broker-write+safety, then merged to main, AND
3. N+2 rebased onto merged main with Path C revert folded in.

The operator MUST decide at Phase 2 boundary whether to: (a) execute the merge sequence first (recommended; clean integration test surface), or (b) stack N+2 implementation on top of entry-path branch (not recommended; integration tests against `NotImplementedError`-raising stub broker), or (c) some other strategy.

**Phase 3 estimated calendar:** if merge-sequence (a) takes ~1 day (4 PR reviews + main merges + rebase), Phase 3 is realistically a 2-day block. If sequencing is delayed by external blockers (e.g. C-1 rotation slots, broker-write code review), N+2 Phase 3 should not start until they're cleared.

---

## What this Phase 1b does NOT cover

Per Phase 1a handoff, this report COMPLETES the four Phase 1b items + A/B/C decision. Out of scope:

- **N+3 deferred items:** #5 Layer 2 funding accrual; #3 case (c) automatic restart-resume; lumibot 5s background reconciliation poll; WS position channel.
- **N+2 Phase 3 implementation:** code/test changes. (Operator gates per A/B/C.)
- **C-1 credential rotation execution** — separate track, gates Phase 3 start but isn't this branch's work.

---

## Recommended next operator decisions (before Phase 3)

1. **Confirm scope (B)** — or override to (A) full-scope, or request a different narrowing.
2. **Confirm merge-sequence (a)** — broker-write+safety+entry-path land on main BEFORE N+2 Phase 3 starts.
3. **Confirm timing of C-1 remaining slots** — if more credential rotations land in the merge window, sequence accordingly.

**STOP-AND-REPORT.** No further code changes from this session.

---

*Sources: code reads on `main`, `bitunix-live-engine-stage1-broker-write`, `bitunix-live-entry-path-2026-05-29`, `bitunix-orderpath-safety-2026-05-29`, `bitunix-live-exit-path-2026-05-29` (file:line cited inline). Lumibot reference quotes via `runbooks/2026-05-29_bitunix_live_reuse_audit.md` (the lumibot files themselves were shallow-cloned + deleted post-audit; the reuse-audit doc is the authoritative quote source for this session). Companion: `reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md`. Memories: `[[bitunix-live-exit-path-phase1a]]`, `[[bitunix-live-entry-path-pattern]]`, `[[bitunix-order-path-safety-pattern]]`, `[[telegram-audit-success-is-confirmed-delivery]]`, `[[verify-premises-against-ground-truth]]`. No code / config / deploy changes made.*
