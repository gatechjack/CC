# Orphan / managed-exit workstream — scope (bugs #3–#5)

Read-only investigation (82fda13). **ANALYSIS + PLAN ONLY — no code, no deploy.** Branch
`bitunix-orphan-managed-exit-scope-2026-06-16`. Companion to the freeze fix already deployed
(PID 2851141) and to memory `bitunix-orphan-managed-exit-bug`.

All file:line refs are against the deployed code (`cc-deploy-prep-wt`, == prod for these files).
Three subagents traced each bug; the load-bearing claims were re-verified by direct read.

## Why this matters (the real remaining live risk)
The bot can **open a live position and lose track of it on a transient DB error** (#3); when that
happens the reconciler **halts the entire bitunix strategy and never recovers** the position (#4);
and even a *tracked* position's **managed take-profit has never worked** — partly because exits are
wrongly blocked by the halt/staleness guards, and partly because **partial TP scale-out is not even
implemented in the live path** (#5). Net: today the only thing that reliably closes a live position
is the server-side attached B1 catastrophic stop. **Not live-profit-trustworthy until #3 + #5 land.**

---

## #3 — fill-registration db-lock → orphan  (SEVERITY: high; root cause of the 2026-06-16 orphan)

### Root cause (verified)
The order-placement path treats a **post-fill DB-write failure** as an **order rejection**:
- `bitunix_futures_observer.py:_place_live()` wraps the whole `data_exec.place()` call in one broad
  `try/except Exception` (~3119-3161). On *any* exception it stamps `order.status =
  "live_order_rejected"`, emits the `live_order_rejected` audit, and `return`s — **never reaching**
  the `paper_trade_record` write (~3175-3194) that is the bot's *only* tracked-position state.
- Inside `data_exec.place()`: the broker fill is obtained at `data_exec.py:188`; then at **line 205
  `self.logger.log_proposed_order(order)`** runs — a bare `db.connect()` write with **no retry**
  (`logger.py`). On a locked DB it raises `sqlite3.OperationalError("database is locked")`, which
  propagates out of `place()` (it is *after* the inner placement try at 180-200) into `_place_live`'s
  broad `except` → `live_order_rejected`.
- DB is WAL with `PRAGMA busy_timeout=5000` (`db.py:connect`), so a >5 s contention window →
  `OperationalError`. `log_event` (audit) has a 4-attempt retry+swallow and
  `insert_paper_trade_record` has retry+re-raise — **but `log_proposed_order` has neither**, and it
  runs *before* the (retry-protected) `paper_trade_record` write is even reached.
- The broker position is **real on the venue** (place_order accepted, B1 SL attached). Losing the
  bot's tracking of it is the bug.

### Fix approach (options → recommendation)
1. **(Recommended) Make registration atomic + lock-resilient, and never let a post-fill DB error
   masquerade as a rejection.** Concretely:
   - Add db-lock retry-with-backoff to `log_proposed_order` (mirror the `_DB_LOCK_RETRY_DELAYS_SEC`
     schedule already used by `log_event`/`insert_paper_trade_record`).
   - **Split the try in `_place_live`**: once `data_exec.place()` returns a fill (broker confirmed),
     a subsequent DB-write failure must route to a **"filled-but-unpersisted"** handler that
     *retries the `paper_trade_record` write* (the registration) — NOT to `live_order_rejected`.
     A confirmed fill must always become a tracked position.
   - Reorder so the **authoritative tracked-state write (`paper_trade_record`) happens first / with
     guaranteed retry**, and treat `log_proposed_order` as best-effort (its failure must not discard
     a real position).
2. (Defense-in-depth) Tune `busy_timeout` higher (e.g. 5 s→15 s) and/or ensure the order path holds
   no long read txn. *Helped indirectly by the freeze fix already shipped* (the removed polymarket
   `audit_event` scans were a lock-contention source). Necessary but **not sufficient** alone.
3. (Last resort) Wrap the whole place+register in a DB transaction. Rejected: the broker side is not
   transactional — the venue fill already happened; you cannot roll it back, so the bot must reconcile
   to the venue truth, not pretend the fill didn't occur.

**Recommendation: option 1.** Invariant to encode: *a broker-confirmed fill is ALWAYS registered as
a tracked position; a DB hiccup may delay persistence (retry) but must never convert a fill into a
rejection.* Smallest blast radius, directly kills the primary orphan source.

### Validation
Unit: simulate `log_proposed_order` raising `OperationalError` after a successful `place_order` →
assert the position is still registered (paper_trade_record written) and **no** `live_order_rejected`
is emitted. Regression: existing place/reject tests stay green.

---

## #4 — no orphan auto-recovery  (SEVERITY: high; safety-critical design)

### Root cause (verified)
`bitunix_position_reconciler.py:reconcile_position_state`:
- Matches tracked vs broker positions by **`(canonical_symbol, side)` only** (~836-914) — no qty,
  no order id, no client id.
- `missing_on_broker` (bot has it, broker flat) → P2 `_autobook_missing_close_real` after 2 ticks
  (line 898-908). **`orphan_on_broker` (broker has it, bot doesn't) → NO recovery action at all.**
- Any divergence (incl. orphan) sets `broker._halt_new_orders = True`
  (reason `position_state_reconciler_divergence`, line 946-955); released only on **2 consecutive
  clean ticks** (956-991). So a persistent orphan **halts the entire bitunix strategy** until it
  disappears — exactly the 7-hour halt observed 2026-06-16 (new entries at 18:06/18:33 were
  halt-rejected). Cost of the orphan = unmanaged position **and** strategy-wide halt.

### The hard part — distinguishing bot-orphan from a genuine MANUAL position (NON-NEGOTIABLE guard)
**Today there is zero positive signal.** `get_pending_positions` (`bitunix.py:1296-1355`) builds
`Position` with `extra = {leverage, marginMode, unrealizedPNL, liqPrice, side}` — it **discards**
`clientId`, `orderId`, `positionId` from the raw API row. So the reconciler cannot currently tell a
failed-to-register bot entry from the operator's manual short.

Signals a fix *can* use to POSITIVELY identify a bot order (allowlist approach):
- **Deterministic:** the bot's client-id convention is `tc-<order_uuid>` (`bitunix.py:_client_id`).
  The fix can call (signed, read-only — the reconciler already does signed reads)
  `get_order_detail(clientId="tc-…")` / `get_history_trades(symbol)` to confirm the venue knows that
  client order, it filled, and its fill price/qty match the orphan. This is a *deterministic*
  bot-match.
- **Corroborating:** a recent `live_order_rejected` audit with `error_type="OperationalError"` /
  "database is locked" whose `(symbol, side, qty≈, entry_price≈, ts within minutes)` matches the
  orphan — i.e. "I tried to place exactly this and got a DB error; the venue may have filled it."
- (Optional) extract `positionId`/`orderId` in `get_pending_positions` if the raw API returns them,
  for a direct cross-reference.

### Fix approach (options → recommendation)
- **ADOPT** (register the orphan into tracked state, attach/verify the B1 stop, resume management) —
  correct for a confirmed bot entry that failed to register (the #3 case).
- **FLATTEN** (close the unmanaged position) — safer for a genuinely unrecognized position, but
  **must never be applied to a manual position.**
- **Recommendation: ADOPT-IF-POSITIVELY-IDENTIFIED, else LEAVE ALONE + ALERT.** Decision rule:
  1. If the orphan **positively matches a known bot order** (clientId `tc-…` confirmed at venue OR a
     matching recent db-lock `live_order_rejected`) → **ADOPT**: write the `paper_trade_record` from
     the known order's plan (entry/stop/TP), verify the attached B1 stop still rests (re-place if
     missing), hand it to the exit monitor. Then clear the halt.
  2. If the orphan does **NOT** positively match any bot order → **DO NOTHING automated**: keep the
     existing halt + a loud operator alert ("unrecognized venue position — manual"). **Never
     auto-flatten an unidentified position.** (This is the safety guard — default to leave-alone.)
  - Auto-FLATTEN should be **opt-in / config-gated and off by default**, and even then only for
    positively-identified bot orphans where adoption isn't possible (e.g. plan unknown).

**SAFETY GUARD (front and center, non-negotiable):** automated recovery acts **only** on a
positively-identified bot order (allowlist). An operator MANUAL position has no `tc-` client order /
no matching bot audit → it is never adopted and never flattened. Default for "unknown" = leave it
exactly as today (halt + alert), so the manual-short case the operator hawk-watches stays untouched.

### Validation
Unit: (a) orphan matching a db-lock `live_order_rejected` → adopted, halt cleared, exit monitor sees
it; (b) orphan with NO bot match (simulated manual) → **not adopted, not flattened**, halt stays,
alert fired. Test the allowlist match is exact (symbol+side+qty±tol+recency).

---

## #5 — managed virtual TP/SL exit path: validate end-to-end  (SEVERITY: high; partly unbuilt)

### Findings (verified) — it is BOTH pre-empted AND structurally incomplete
1. **Mechanism:** exits are driven by a **900 s (15-min) bar-replay classifier**
   (`paper_trade_replay.py:start_replay_loop` / `_replay_tick_async`), **not a real-time price
   monitor**. Each tick loads `paper_trade_record WHERE result IS NULL`, pulls historical 1m bars,
   classifies, and on a *terminal* verdict for a `execution_mode="live"` row calls
   `observer._execute_live_exits()`. `exit_kind`: win→`tp`, loss→`sl`.
2. **Exit-exec code is structurally correct** where it exists: side inversion (close short = buy),
   `reduce_only=True`, qty plumbed (`observer.py:_execute_live_exits` ~3344-3380).
3. **BUG B (real): the halt blocks exits.** `bitunix.py:1053-1056` raises on `_halt_new_orders`
   **before** `reduce_only` is read (1059) → reduce-only exits are halted too, contradicting the
   reconciler's own comment "Exits are NOT halted (Phase 1a §9c)" (reconciler line 943). The first
   wave of the 9 historical `live_exit_order_rejected` (2026-06-14) = this halt.
4. **BUG C (real): stale-snapshot blocks exits.** `data_exec.py:186-188` calls
   `_assert_snapshot_fresh()` for **every** order (no reduce_only exemption); `bitunix.py:744-769`
   latches `_halt_new_orders=True` then raises `BitunixStaleSnapshot`. Second wave of rejections =
   this. (The entry-side staleness gate C we just shipped *is* exit-exempt; the **broker-level** gate
   is not.)
5. **GAP A (design, bigger than a bug): partial TP scale-out is NOT implemented in the live path.**
   `_classify_v2_multi_leg` only updates `filled_legs` in `extra_json` (paper accounting) when TP1/TP2
   cross; it calls `_execute_live_exits` **only on the final terminal verdict**, with **full
   `row.qty`**. So TP1/TP2 partial reduce-only closes are *never placed live*; the SL-advance
   (→ breakeven after TP1) is never sent to the venue. "Taking TP1/2/3" as the operator expects
   (scaling out) **does not exist** in the live path — only one terminal close. Currently deferred to
   "Phase 4."
6. **Orphan ⇒ zero exit monitoring:** the replay loop only sees rows in `paper_trade_record`; an
   untracked orphan (#3) gets no exit attempts at all.
7. **Has it ever run uninterrupted?** No — the one live trade (2026-06-14) reached
   `_execute_live_exits` 9× (15-min retries), all pre-empted by the halt then stale snapshot; no
   `exit_kind="tp"` exists in all history.

### Fix approach (options → recommendation)
- **B/C (small, high-leverage, do first):** exempt `reduce_only=True` orders from the halt
  (`bitunix.py:1053`) and from `_assert_snapshot_fresh` (`data_exec.py:186` / the broker guard) — let
  positions always be *closed* even when entries are halted/stale. This makes the existing terminal
  close actually fire. **Guard:** keep the exemption strictly reduce-only (never let it open/increase).
- **A (the real build):** implement live partial TP-leg closes — when the monitor detects TP1/TP2
  crossed, place a reduce-only close for that leg's fraction, then advance the SL per `stop_action`.
  This is the actual "take TP1/2/3." Non-trivial: per-leg qty accounting, idempotency (don't double-
  close a leg across replay ticks), reconcile with the venue.
- **Architecture note (flag, operator decision):** the 900 s replay cadence means TP detection lags
  up to 15 min and uses 1m historical bars — for a 3m-bar scalp with tight TPs, a TP can be hit and
  round-trip *within one interval* (exactly the 2026-06-16 shape: hit TP3, reversed, before any
  action). Reliable profit-taking likely needs **real-time (per-snapshot/per-bar) TP monitoring**,
  not 900 s replay. Bigger than a bug fix; scope separately.

### Validation requirement (define "validated")
- **Paper/replay first:** a simulated/paper position crosses TP1 → assert a reduce-only close for the
  TP1 leg fraction is generated with correct side/qty; crosses SL → terminal close; partial then SL →
  residual handled. Prove the path end-to-end with no live venue.
- **Controlled live next:** a single small real position, operator-watched, confirm **TP1 actually
  books** a reduce-only fill at the venue (the first-ever live `exit_kind="tp"`), SL-advance applied,
  residual legs correct. "Validated" = at least one live TP leg and one live SL exit observed booking
  correctly, with the reconciler staying clean.

---

## Recommended sequence (build order)
1. **#3 first — stop creating orphans.** Make fill-registration lock-resilient; a confirmed fill is
   never converted to a rejection. Eliminates the primary orphan source. Smallest, safest, highest
   urgency.
2. **#5-B/C alongside/just after #3 — make exits able to fire.** Exempt reduce-only exits from the
   halt + staleness guards. Tiny change, unblocks the existing terminal close; without it even a
   well-tracked position can't exit during a divergence/freeze.
3. **#5-A + validation — make TP scale-out actually exist, then prove it.** Build live partial-leg
   closes; validate in paper/replay then a watched live trade (first real live TP).
4. **#4 last — the safety net.** Orphan auto-recovery (adopt-if-positively-bot-identified, else
   leave-alone+alert) for anything that still slips through. Built last because #3 should make orphans
   rare, and #4's safety guard (don't-touch-manual) is the most delicate — do it carefully, not under
   pressure.
- **Cross-cutting decision for the operator:** the 900 s-replay vs real-time TP monitoring
  architecture question (#5 note) — decide before investing heavily in #5-A, since real-time
  monitoring would reshape the exit path.

## Out of scope / do-not-touch
- The operator's **manual** position management (hawk-watching) — the #4 guard must protect it.
- The freeze fix (done). Polymarket. Any live position/order change (operator's hands only).

## Status
Scope only — no code, no deploy. Operator stays LIVE + manually manages missed exits as the stopgap
until #3 + #5-B/C land.
