# PEAD Flag 2 — deferred-fill reconcile — SCOPE (build NOT started)

Status: **scope only.** Discovery DONE (empirical, traced against the live engine
code on branch `robinhood-pead-2026-06-20`, no orders placed). Decisions LOCKED by
operator 2026-06-24. Proposed shapes below are **PENDING REVIEW — do NOT build until
operator go.** This doc is the artifact the build is reviewed against; it must not
drift from the locked decisions.

Flag 2 is the **pre-go-live blocker**: a green during-hours lifecycle test proves
MECHANICS, not the production pre-open flow. Flag 2 is that production flow.

## THE PROBLEM — PROVEN (traced against real engine code, not assumed)

The production pre-open flow **places-immediately-then-cancels — entry silently
lost.** Chain (frac build, branch `robinhood-pead-2026-06-20`):

- Scan fires **pre-open**, weekdays ET **[08:30, 09:25]**, once/day, deduped on date
  — `_scheduled_pead_scan_loop` (`main.py:2432-2452`).
- Placement is **synchronous inside `scan()`**: `scan → _place_or_paper →
  data_exec.place → broker.place_order → _place_fractional_stock_order`
  (`pead_strategy.py:218-224` → `data_exec.py:188` → `robinhood.py:715`).
- The buy is **GFD** (`order_buy_fractional_by_price(..., timeInForce="gfd")`,
  `robinhood.py:847-849`) → RH **queues it to the 09:30 open**.
- But `_place_fractional_stock_order` immediately runs `_poll_fractional_fill`
  (90s, `robinhood.py:799-837`), which on timeout calls `cancel_stock_order(rh_id)`
  (`:827`), returns `None`, and the order **RAISES** (`:869-871`) → `_place_or_paper`
  returns False → **no record, no position.** The cancel fires ~90s after placement,
  still pre-open, before the 09:30 open the GFD order was waiting for.

The during-hours probe worked only because RTH fills the GFD order at the touch
*inside* the 90s window. Pre-open it cannot. **Do not let the lifecycle-test green
imply the production flow works.**

**State today (relevant gaps):** `broker_order_id` is **not persisted** (lives only
on transient `FillEvent`, `models.py`); a `paper_trade_record` is written **only
after** a confirmed fill; there is **no PENDING-order concept** surviving loop
iterations; and there is **no market-open guard** in the production path (it lives
only in the gate34 harness).

## LOCKED DECISIONS (operator-ruled 2026-06-24)

1. **Collar >5% no-fill → (a) ACCEPT THE MISS / skip + LOG.** A fractional buy is a
   ~5% collared limit (client-side, ask-referenced); if the name gaps >5% above the
   reference at the open it stays unfilled → **write no record, the name just isn't
   entered that day; LOG it** so we can see if it ever fires. Rationale: (c) "collar
   as a config knob" is NOT a config knob — the 5% is baked into RH's fractional API;
   tunable = leaving fractional for an explicit limit path = a bigger change than
   Flag 2. (b) "re-place wider/at-market" adds a second order at a price different
   from the signal. For PEAD (1-2 td post-announcement, price settled) a >5% open gap
   is rare, so the miss is an edge case. Accept it, log it, **revisit only if logs
   show it firing often.**

2. **Reconcile hook = DEDICATED RECONCILE LOOP, sibling to the scan loop — NOT
   extending `manage()`.** `manage()` is tested, idle-gated, and reads open records;
   bolting reconcile onto it entangles it with new responsibilities (polling order
   state) and cadence changes. A small additive reconcile loop that wakes around the
   open, drains the PENDING store, and writes realized records is cleaner separation
   and matches the #3-isolation discipline.

3. **PENDING-store INVARIANT (NON-NEGOTIABLE): a PENDING order is NOT an open
   position until the fill is confirmed.** PENDING is its own state, **NOT counted in
   the book**, and transitions to a real open record **ONLY when reconcile confirms
   `state=="filled"` AND reads realized qty.** This is the phantom-position risk in a
   new form — the same realized-qty discipline as the frac build applies: **no
   confirmed fill = no position.** The build must make this invariant explicit and
   un-leakable.

4. **#3 discipline holds.** place-without-poll + reconcile are **additive**;
   `_place_fractional_stock_order` (the polling path), whole-share, limit, and option
   paths stay **byte-for-byte untouched**; other divisions untouched. PEAD adversarial
   suite + scoped regression (the other-division placement baseline) green **before
   any deploy**. Ships inert (standby) — go-live is the separate 4-flip gate.

## PROPOSED DESIGN — PENDING OPERATOR REVIEW (not built; shown before it lands)

### A. PENDING-store shape — new `pending_order` table (recommended)
A **separate table**, NOT a marked row in `paper_trade_record`. Rationale: the book
is `paper_trade_record WHERE result IS NULL`; a PENDING marker there would require
every book/dashboard/manage/reconcile query to remember `AND not pending` — one
missed filter = phantom position. A separate table makes the invariant **structural**:
PENDING physically cannot be counted in the book because it isn't in the book table.
The reconcile loop is the ONLY reader; on confirmed fill it calls the existing
`strat._write_record(...)` with realized qty (the row enters the book there, exactly
once) and clears the PENDING row.

Proposed columns: `id` (engine `ProposedOrder.id`, PK) · `division` · `symbol` ·
`side` · `broker_order_id` (rh_id — what reconcile polls) · `account` ·
`notional_usd` (requested) · `trading_date` (the session whose open reconciles it) ·
`placed_ts` · `extra_json` (the 6 PEAD keys + `entry_reference_price` + `stop_price`
+ `source_signal` — everything `_write_record` needs) · `state`
(PENDING → terminal) · `last_poll_ts`/`attempts` (observability).

Crash-safety bonus: PENDING is persisted, so a restart between place and reconcile
loses nothing — the reconcile loop drains any open PENDING rows on boot.

### B. place-without-poll shape — new additive broker method
Add `_place_fractional_pending(order) -> str` (returns the rh_id) that does what
`_place_fractional_stock_order` does **except** the `_poll_fractional_fill` call: it
submits the GFD buy, enforces the Bug-1 id-check (raise on no id / RH reject), and
returns the rh_id immediately. **The existing `_place_fractional_stock_order` (the
90s-polling path) is left byte-untouched** (#3). PEAD's entry path uses the deferred
method and INSERTs the PENDING row; no record is written at placement.

Open Q for review: **deferred-always-for-PEAD** (recommended — PEAD always scans
pre-open; the reconcile loop also cleanly handles the rare already-open case, so no
market-state branch at placement) **vs. market-state-gated** (synchronous when
`is_open_at`, deferred when pre-open). I lean deferred-always for simplicity.

### C. Reconcile-loop shape — new `_scheduled_pead_reconcile_loop` (sibling to scan)
Wakes around the open; while `default_calendar().is_open_at(now)` (holiday/half-day
aware — reuse `utils/market_hours.py`, the util the gate34 harness already uses) and
PENDING rows exist for today, drains them. Per PENDING row, poll
`get_stock_order_info(rh_id)`:
- **`state=="filled"` & cum>0** → read realized `cumulative_quantity` /
  `average_price` / `executed_notional` → `_write_record(realized qty/price/notional)`
  → clear PENDING. The row is now a real open position; `manage()` takes over.
- **terminal partial** (cum>0, non-"filled" terminal) → record realized partial
  (consistent with frac build decision #2: accept the realized partial). *Confirm.*
- **still unfilled past a post-open deadline** (the collar >5% miss) → **CANCEL the
  resting GFD order** (critical: GFD rests ALL DAY — an un-cancelled order could fill
  unwatched at 2pm = the phantom position the invariant forbids), **LOG the skip**,
  clear PENDING, write no record.
- **terminal reject/cancel, cum=0** → log, clear PENDING.

Note the key reframing: we still *cancel*, but **only post-open, post-deadline** —
never pre-open. The original bug was cancelling before the 09:30 open; the fix lets
the order ride to the open, reconciles the fill, and cancels only the genuine
collar-miss.

Broker resolved fresh each tick (None-safe), no-op while standby/disabled, mirroring
the scan/manage loops. New additive `strategies.yaml` keys (default-safe):
`reconcile_poll_interval_sec`, `reconcile_deadline_after_open_sec` (how long to wait
for the open fill before declaring collar-miss + cancel — propose ~300s), and a
`reconcile_window_end_et`.

### D. main.py wiring
Register `_scheduled_pead_reconcile_loop` as an asyncio task alongside
`_scheduled_pead_scan_loop` / `_scheduled_pead_manage_loop`, gated by standby/enabled.
Additive; boot-smoke confirms no effect on Bitunix or other divisions.

## PROOF PLAN (this is what actually gates go-live)
A during-hours green run does NOT prove this — by construction. Proof must span the
09:30 open with eyes-on, in a **live pre-open window**:
- **Phase A (pre-open, ~09:20 ET):** place the GFD fractional buy via the deferred
  path, INSERT PENDING, **do not cancel**.
- **Phase B (post-open, ~09:31 ET):** reconcile — poll `get_stock_order_info`,
  confirm `state=filled` at the open, write the realized record; verify
  `/telemetry/pead` renders it; exit via `manage()`.
- **Collar branch:** exercise the still-unfilled case (a name expected to gap >5%, or
  a deliberately tight collar) → confirm cancel-resting-order + log-skip + no record.
- Requires operator eyes-on + explicit go; cannot be faked during RTH.

## #3 / RISKS
1. **Shared broker (HIGHEST):** the deferred method sits next to the live
   PMCC/joint/IC placement code. Additive + isolated; prove the polling/whole-share/
   limit/option paths byte-identical and other-division placement unchanged.
2. **Un-cancelled GFD = unwatched intraday fill = phantom position.** The collar-miss
   branch MUST cancel the resting order. Tested explicitly.
3. **PENDING invariant leak:** any path that counts a PENDING row as open is a
   phantom position. Separate table makes it structural; test that the book/dashboard
   never see PENDING.
4. **Reconcile must be idempotent + crash-safe:** drain PENDING on boot; never
   double-write a record for the same rh_id.

## BUILD STATUS
**NOT started.** Decisions locked (above). Shapes A-D PENDING operator review. On go
(with any edits), build to the reviewed shapes; PEAD suite + scoped regression green
before any deploy; ships inert.
