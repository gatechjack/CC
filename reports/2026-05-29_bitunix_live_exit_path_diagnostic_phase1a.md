# BitUnix Stage-1 Session N+2 — Phase 1a sub-diagnostic (PARTIAL)

**Date:** 2026-05-29 · **Type:** read-only structural audit · **Branch:** `bitunix-live-exit-path-2026-05-29` off `main` · **Status:** Phase 1a only — covers structural questions #1, #2, #3, #8, #9. Phase 1b (questions #4 reconciliation, #5 cost accrual, #6 restart-resume, #7 alerts, plus the A/B/C scope decision) deferred to a fresh session with a focused prompt at the end of this file. Operator was warned of context budget and approved the split.

**Premise correction up-front**: the operator brief said "7 unmerged branches" — actually **6 Session-29 feature branches** on origin (the broker-write follow-up `87dac50` is a commit on the existing `bitunix-live-engine-stage1-broker-write` branch, not a new branch). Also: `execution_mode` field does NOT exist in main's `config/strategies.yaml` — it's only on the unmerged N+1 entry-path branch. This N+2 branch off main inherits NEITHER N+1 nor the safety branch — both are required prerequisites and must merge in order before N+2 can land cleanly.

---

## TL;DR for Phase 1a

- **Detection surface** is two pure classifier functions: `_classify(row, bars) → _Resolved` (single-leg, `paper_trade_replay.py:149-270`) and `_classify_v2_multi_leg(row, bars, extra) → _Resolved` (multi-leg, `:401-662`). Both return a `_Resolved` dataclass; results sink into `_update_row(db_url, order_id, v) → None` at `:1220-1254` — a single SQL UPDATE on `paper_trade_record`. The classifiers are the natural "exit happened" event surface.
- **Wire point recommendation**: a parallel executor (`bitunix_live_exit_executor`) is **NOT** the cleanest fit — the existing classifiers already produce the right semantic events. **Recommend: in-place fork inside `_update_row` (or a small canonical helper called from there), gated on a per-row execution_mode flag.** Mirrors N+1's pattern (paper/live fork inside one canonical writer).
- **Exit-order construction**: `data_exec.place(order, division="bitunix_futures")` with `order.extra["reduce_only"] = True` and `order.side` INVERTED from the entry. Qty math per leg is already computed in the multi-leg classifier via `tp_plan[leg]["fraction"]`. SL hit closes 100% of remaining position. Expired = close remaining as market reduce-only.
- **HITL-on-exits recommendation**: **NO HITL on exits.** Exits are time-sensitive (TP/SL slippage); gating defeats the strategy. Surface elevated `(live, exit)` telegram alerts for visibility on first-N-exits without gating.
- **N+1 premise validation surfaces ONE BIG GAP**: N+1's live entries do NOT write `paper_trade_record` (per commit 3: "No paper_trade_record on the live path"). So `paper_trade_replay` — which walks `paper_trade_record WHERE result IS NULL` — would NEVER see live positions. The exit path is structurally invisible to the existing replay loop. **This reshapes N+2 scope.** Three resolution paths (see §5).

---

## 1. TP/SL/expired detection map

### Single-leg classifier — `_classify(row, bars)` at `paper_trade_replay.py:149-270`

```python
# :184-185 (long)                              :186-188 (short)
if side == "buy":                               elif side == "sell":
    tp_hit = high >= tp                            tp_hit = low <= tp
    sl_hit = low <= sl                             sl_hit = high >= sl

# :193-202 same-bar both → LOSS (conservative)
if tp_hit and sl_hit:
    return _Resolved(result="loss", result_ts=bar_ts_iso, result_price=sl, ...)

# :203-211 TP only
if tp_hit:
    return _Resolved(result="win", result_ts=bar_ts_iso, result_price=tp, ...)

# :212-220 SL only
if sl_hit:
    return _Resolved(result="loss", result_ts=bar_ts_iso, result_price=sl, ...)

# :250-261 end-of-bars + still inside max_hold → still_open (no row update)
# :263-270 end-of-bars + max_hold elapsed → expired (last_close as proxy)
```

### Multi-leg classifier — `_classify_v2_multi_leg(row, bars, extra)` at `:401-662`

```python
# :503-506 SL hit (checked first for conservative tie-handling)
sl_hit_this_bar = (
    (side == "buy" and low <= current_sl) or
    (side == "sell" and high >= current_sl)
)

# :508-521 per-leg TP detection (ordered tp1 → tp2 → tp3)
for leg_name in ("tp1", "tp2", "tp3"):
    if leg_name in filled_legs:
        continue
    target = leg_targets[leg_name]
    hit = (side == "buy" and high >= target) or (side == "sell" and low <= target)
    if hit:
        legs_filled_this_bar.append(leg_name)
    else:
        break  # legs are ordered

# :526-559 SL hit closes remainder at current_sl with aggregated R math
#         (legs filled THIS bar don't count — conservative)
# :562-574 leg fills trigger _decide_lifecycle_sl (BE after TP1, TP1-floor after TP2)
#         + _emit_audit (writes position_sl_update audit + queues telegram)
# :577-605 tp3 in filled_legs → trade fully closed (win)
# :635-644 still_open (with extra_json_updates carrying filled_legs + current_sl)
# :654-662 expired (with R aggregated on unfilled remainder at last_close proxy)
```

### Exit-event shape (what's known at hit-time)

Both classifiers produce `_Resolved` (`paper_trade_replay.py:67-93`):

```python
@dataclass
class _Resolved:
    result: str          # 'win' | 'loss' | 'expired' | 'still_open' | 'pre_phase_a'
    result_ts: str | None
    result_price: float | None
    actual_pnl_dollars: float | None
    actual_r_multiple: float | None
    bars_to_resolution: int | None
    extra_json_updates: dict | None  # multi-leg: filled_legs + current_sl
```

`_PendingRow` carries the entry context (`:46-65`): `order_id`, `ts`, `strategy`, `division`, `symbol`, `side`, `qty`, `stop_price`, `tp_price`, `tp_r_multiple`, `entry_reference_price`, `expected_loss`, `expected_gain`, `max_hold_seconds`, `extra_json`.

**Canonical "exit happened" event surface**: there isn't one explicit event today — the classifiers return resolutions and `_update_row` writes them. But the natural event surface for live wiring is at the BOUNDARY between classifier return and `_update_row` call. The classifier already produces the semantically clean event; the sink is a single function.

**Multi-leg mid-walk audit (already emitted)**: `_emit_audit(bar_ts_iso, new_sl, lifecycle_state, reason)` at `:573` fires for SL transitions during the walk (BE after TP1, TP1-floor after TP2). This is the existing observability surface for partial fills. Each call ALSO queues a TP-fill telegram via `_queue_tp_fill_notification` (`:476-481`). **Important**: TP fills are written audit-by-audit DURING the walk; only the FINAL resolution writes via `_update_row`. So live wiring has to think about leg fills as separate exit events, not as a single resolution.

### Recommendation: canonical helper

**`_record_exit_outcome(row, resolved, leg=None, db_url=...)`** parallel to N+1's `_record_placement_outcome`. Called from:
- single-leg: just before `_update_row` for TP/SL/expired resolutions (3 sites collapse to 1)
- multi-leg: once per leg fill (inside `_emit_audit` for TP1/TP2, separately for tp3 in `:577-605`) + once for SL hit closing remainder + once for expired

Inside the helper: paper-mode does the existing `_update_row` write. Live-mode constructs the reduce_only order + calls `data_exec.place()` + records the broker fill back into the row. **Same pattern as N+1.**

---

## 2. Wire-point recommendation: in-place fork (NOT parallel executor)

The readiness audit's reuse note suggested "a new `bitunix_live_executor` that subscribes to the same TP/SL events." **Refute this against the actual code structure:**

- The existing classifiers are pure functions emitting clean semantic events. They're already the "subscribe to TP/SL events" surface.
- A parallel executor would need to RE-IMPLEMENT the bar-walk + lifecycle SL transitions + tie-handling + still-open vs expired distinction. That's the heaviest logic in the module; duplicating it for live is high cost / high regression risk.
- Worse, a parallel executor would have to coordinate with the replay loop (which row is "yours" vs "mine"), reintroducing the race conditions the single-loop design avoids.

**Recommendation: in-place fork via canonical helper.** The replay loop stays the SINGLE source of truth for "exit happened"; the helper forks paper/live on the way out. Drops complexity vs the parallel executor by ~50%.

**Important refinement**: the fork can't be all-or-nothing inside one function — multi-leg fires multiple exit events per row (one per leg, plus closing-remainder events). Each leg fill needs to await its own `data_exec.place()`. So the multi-leg classifier becomes async OR factors out the broker-call to a follow-up sweep (sync classifier + async live-execute step).

**Recommended factoring**: classifier returns enriched `_Resolved` with a `leg_fills: list[LegFillEvent]` field; a follow-up async `_execute_live_exits(_Resolved, row)` walks the leg events. Paper-mode: `_execute_live_exits` is a no-op (existing `_update_row` handles it). Live-mode: each leg fills via `data_exec.place(reduce_only=True)`, attribution waits on broker, results merged back into the row's lineage.

---

## 3. Exit-order construction

### Broker signature for exits (broker-write `87dac50`)

`BitunixBroker.place_order(order: ProposedOrder)` at `brokers/bitunix.py` (broker-write branch):

- Reads `extra["reduce_only"]: bool` at line 575.
- For `reduce_only=True`: skips `_assert_position_mode_one_way` write (only verifies); skips `_ensure_leverage` (entries only).
- `_build_order_body(order, wire, reduce_only)` produces JSON with `reduceOnly: true` (NO `tradeSide: OPEN`).
- Returns `FillEvent(order_id, symbol, side, qty, price, ts, venue)` — observes fill via `_observe_fill` (poll order detail + VWAP from trade history).
- **Side convention**: exits invert side. For a long entry (`side=buy`), the exit order is `side=sell, reduce_only=True`. For a short entry, exit is `side=buy, reduce_only=True`.

### Qty per leg (multi-leg)

Already computed in the classifier via `tp_plan`:

```python
# _leg_fraction(tp_plan, leg) at :297-304 — returns float fraction
# e.g. tp_plan = [
#     {"leg": "tp1", "fraction": 0.25, "target_r": 1.0, "price": ..., "stop_action": "move_to_breakeven"},
#     {"leg": "tp2", "fraction": 0.50, "target_r": 2.0, "price": ..., "stop_action": "move_to_tp1"},
#     {"leg": "tp3", "fraction": 0.25, "target_r": 3.0, "price": ..., "stop_action": "trail_atr"},
# ]
# Exit qty for tp1 = entry_qty * 0.25
# Exit qty for tp2 = entry_qty * 0.50
# Exit qty for tp3 = entry_qty * 0.25
```

### SL hit closing remainder

For multi-leg SL: `remaining_qty = entry_qty * (1 - sum(fraction[l] for l in filled_legs))`. Existing code computes the R aggregation already at `_aggregate_multi_leg_r(:371-398)`; the qty math is the same arithmetic.

**SL is a single 100%-of-remainder close.** No leg-specific behavior on SL (verified across `_classify_v2_multi_leg:526-559`). The reuse audit's "kill switch primitives" — `cancel_all_orders`, `flash_close_position`, `close_all_position` — are alternatives to `place_order(reduce_only=True)` for SL closes. **Recommendation: use `place_order` for normal SL exits (full audit trail per order); reserve `flash_close_position` for the emergency kill-switch path (separate from N+2).**

### Expired

Exists today: single-leg `:263-270`, multi-leg `:654-662`. For live: close remaining qty as a market `reduce_only=True` order. Same construction as SL but with a different audit kind (`live_exit_expired` vs `live_exit_sl`).

### Single-leg qty

Full `entry_qty`. Simple.

### Construction template (recommended)

```python
def _build_exit_order(
    row: _PendingRow,
    exit_kind: Literal["tp1", "tp2", "tp3", "sl", "expired"],
    fill_fraction: float,  # 1.0 for single-leg; tp_plan fraction for multi-leg legs; remainder for SL/expired
) -> ProposedOrder:
    entry_side = (row.side or "").lower()
    exit_side = "sell" if entry_side == "buy" else "buy"
    return ProposedOrder(
        strategy=row.strategy,
        symbol=row.symbol,
        side=exit_side,
        qty=row.qty * fill_fraction,
        order_type="market",
        rationale=f"exit:{exit_kind} (from entry {row.order_id})",
        extra={
            "reduce_only": True,
            "exit_kind": exit_kind,
            "parent_order_id": row.order_id,
            "fill_fraction": fill_fraction,
        },
    )
```

---

## 8. HITL on exits — **NO HITL, use elevated telegram**

### Operator instinct: skip HITL. Validated.

Exits represent the strategy's pre-decided plan executed at price levels chosen at entry time. Gating on operator approval at TP/SL hit:
- **Defeats time-sensitivity**: a 10-minute HITL timeout (per N+1 entries) means TP can slip several %; SL can blow through into significant slippage if the operator is asleep.
- **Adds no information**: the exit price was decided at entry. Approving "yes close at TP1 at $85k" doesn't add operator judgment — they already approved that plan when they approved the entry (or the first-10 HITL surfaced it for the entry).
- **Creates a fight**: if the operator rejects the exit, the position stays open past the planned exit. That's a strategy hold, not a strategy execution — operator-as-trader rather than operator-as-supervisor.

### Counterargument considered: first-N-exits visibility

**Risk**: operator may want to see first-N exits to validate the execution surface (broker accepted, fill within expected slippage, fees match expectations). HITL would block until they confirm.

**Resolution**: replace blocking HITL with **elevated `(live, exit)` telegram alerts** for the first N exits — same info, no blocking. Operator sees:
- "BTC-PERP TP1 fill at $X (live, exit #1/10): qty Y, fee Z, parent_order=..."
- "BTC-PERP SL fill at $X (live, exit #3/10): ..."

After N exits the alert tone normalizes (drop the count). Counter via `agent_state` (same primitive as N+1's HITL counter, separate key).

### Counterargument considered: manual close

Operator wants to flatten a position outside of TP/SL plan. This is NOT a paper_trade_replay-triggered exit — it's a separate user-initiated flow (a "flatten this position now" button on the dashboard, or a Telegram command). Out of scope for N+2; this exists as the `flatten_division` consumer on the safety branch already and the dashboard wire is its own work.

### Recommendation summary

- **No HITL gate on exits.** Document rationale in the canonical helper (immortalize the operator decision).
- **Elevated `(live, exit)` telegram suffix on first N exits** (counter in `agent_state`, key `live_exits_executed`).
- After N: normal `(live)` suffix.
- Manual close = separate flow (not N+2).

---

## 9. Premise validation against N+1 — ONE LARGE GAP + small notes

### Gap: N+1's live entries do NOT write `paper_trade_record`

N+1 commit 3 explicitly states "**No `paper_trade_record` on the live path** — fill tracking happens in `proposed_order` and `filled` audit." Source: `[[bitunix-live-entry-path-pattern]]` § "What's wired" point 3, and the commit message at SHA `e04b192`.

**Implication**: `paper_trade_replay.replay_pending_paper_trades` walks `SELECT * FROM paper_trade_record WHERE result IS NULL` (verified at `paper_trade_replay.py:~1180-1217`). Live positions don't HAVE a row in that table. So the existing TP/SL detection loop **never sees live positions**.

**This reshapes N+2 scope.** Three resolution paths to surface (operator-gated):

**Path A — Tag-and-share**: Live entries ALSO write `paper_trade_record` rows with a `execution_mode` field in `extra_json` (e.g. `extra["execution_mode"] = "live"`). The replay loop continues walking the same table; the canonical exit helper forks paper vs live based on the tag. **Pros**: single table, single loop, minimal infra. **Cons**: `paper_trade_record` becomes ambiguous (some rows are paper, some are live); the `result_*` columns conflate paper-replay verdicts with broker-confirmed fills. Backtest queries get more complex.

**Path B — Parallel table**: New `live_position_record` table with broker-truth fields (broker_order_id, real_fill_price, real_qty, real_fee, real_funding). New loop `live_position_executor` walks it. **Pros**: clean semantic separation; backtest queries stay clean; broker-truth fields don't pollute paper schema. **Cons**: more infra (new table + new loop + new schema); duplicates the lifecycle classifier logic OR has to abstract it (which §2 said we should AVOID).

**Path C — Hybrid (recommended)**: Live entries write `paper_trade_record` with `extra["execution_mode"] = "live"` AND a `extra["broker_order_id"]` to link broker truth. The replay classifier walks both kinds (paper + live). The exit helper forks: paper rows take the `_update_row` paper path; live rows take the `data_exec.place(reduce_only=True)` path AND get their `result_*` columns populated from broker-confirmed fills (not the classifier's projected exit_price). **Pros**: single loop, single table, semantic clarity via `execution_mode` tag, broker-truth in `result_*` for live. **Cons**: needs a small schema additive ("broker_order_id_exit_*" columns in `extra_json` is enough — no migration). Slight backtest-query complexity (filter on `extra_json` execution_mode), already a pattern.

**Recommended: Path C.** Operator decision required before Phase 3 implementation.

**Required pre-work** for Path C: N+1 commit 3's "no `paper_trade_record` on live path" decision needs to be **reversed** for live entries before exits can be tracked. That's a small follow-up commit on the N+1 entry-path branch (or merged into the N+2 branch). The change: live `_place_live` ALSO calls `db.insert_paper_trade_record` with `extra["execution_mode"] = "live"` + `extra["broker_order_id"]` after a successful fill. Minor scope addition; the row is needed for exit tracking and for the dashboard to render live positions consistently with paper positions.

### Small notes

**a) Canonical helper `_record_placement_outcome` generalizes to exits?** Pattern generalizes (paper/live fork inside ONE writer is the right shape), but the helper itself lives in the observer and is called from entry sites. Exits have a different call site (`paper_trade_replay`) and different state. **Recommend: parallel helper `_record_exit_outcome(row, _Resolved, ...)` in `paper_trade_replay` or a new module.**

**b) `execution_mode` read site for exits.** The observer reads `self.execution_mode` (set at construction) + fresh YAML for `auto_execute` per placement. Exit-side: `paper_trade_replay` doesn't have a strategy-level context — it walks rows strategy-agnostically. **Recommend: stamp `execution_mode` into `paper_trade_record.extra_json` at entry time (per Path C above), and exit-side reads from the row.** Avoids YAML-per-row in the replay loop AND ties exit mode to the actual entry mode (semantically correct — a position entered in live mode must exit in live mode, even if YAML was edited mid-lifetime).

**c) StrategyState halt semantics for exits.** Halt protects future ENTRIES from breaching cap further. Existing positions should be allowed to close NATURALLY (their fate was determined at entry). **`flatten_account=True` is a different verdict** — that DOES want force-close, and the safety branch's `flatten_division` consumer already exists for that. **Recommend: halt does NOT block exits; flatten DOES force-close.** Tie into existing safety consumer; no new code path needed.

---

## What this Phase 1a does NOT cover (handed off)

- **#4 Post-trade reconciliation** — broker fills vs system records, lumibot's `do_polling` diff-engine reference, tolerance + divergence handling.
- **#5 Cost accrual** — fees + funding bookkeeping, `get_history_positions` integration, schema additions or `extra_json` storage decision, scope-fold vs defer to N+3.
- **#6 Restart-resume from broker truth** — Stage-1 item #3, lumibot's `_first_iteration` sync pattern, how it interacts with partial-fill state.
- **#7 Operational alerts surface** — beyond the elevated-exit suffix, what `exit_order_placed/filled/rejected/partial` + `position_closed_with_pnl` look like as audit kinds + telegram shapes.
- **Phase 2 scope decision (A/B/C)** — depends on #4-#7 answers; recommend operator + a fresh Claude session decide together.

These five items are NOT phase-1a-blocking for the structural exit-wire decisions. The Phase 1a recommendations stand independently (canonical helper, in-place fork, no HITL, Path C for `paper_trade_record` tagging). Phase 1b answers shape the SCOPE around those load-bearing decisions.

---

## Phase 1b handoff prompt

Use this in a fresh Claude session to complete Phase 1 before Phase 2 scope decision:

```
Stage-1 Session N+2 Phase 1b — complete the sub-diagnostic + scope decision

Phase 1a shipped in commit [TBD on bitunix-live-exit-path-2026-05-29] —
see reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md. That
report covers the structural questions #1-#3 + #8 + #9 with concrete file:line
references and a recommended structural shape:
  - canonical _record_exit_outcome helper, in-place fork (paper vs live)
  - exit-order construction via data_exec.place(reduce_only=True), side inverted
  - NO HITL on exits, elevated telegram for first N exits instead
  - Path C for paper_trade_record tagging (live entries write the row with
    extra.execution_mode='live') — REQUIRES a small additive change on the N+1
    entry-path branch (reverses N+1 commit 3's "no paper_trade_record on live")

Phase 1b complete the remaining four structural questions + the A/B/C scope
decision. Each is its own focused investigation; do them in any order:

#4 Post-trade reconciliation (Stage-1 readiness audit item #4)
  - Reuse audit references lumibot's do_polling diff-engine
    (runbooks/2026-05-29_bitunix_live_reuse_audit.md). Quote the relevant
    section + identify whether the polling cadence + fill-attribution logic
    already exists on broker-write branch (87dac50). If exists, where; if
    not, scope estimate.
  - Recommend reconciliation policy: tolerance threshold (e.g. ±0.1% on
    price, ±1% on qty), divergence handling (audit + telegram + halt vs
    audit only), idempotency.
  - Identify any state-table changes required (broker_fill_record? or
    paper_trade_record.extra_json fields? Recommend.).

#5 Cost accrual (Stage-1 readiness audit item #5)
  - get_history_positions + WS position channel — quote from reuse audit.
  - Where do fees + funding get booked? New funding_accrual audit? New
    columns on paper_trade_record? extra_json storage?
  - Scope decision: fold into N+2 or defer to N+3? Trade-off: tax records
    need real fees+funding from trade #1, so even minimal data capture is
    important. But aggregation + reconciliation can be deferred.

#6 Restart-resume from broker truth (Stage-1 readiness audit item #3)
  - lumibot's _first_iteration sync pattern. Quote.
  - On restart: bot reads broker positions, reconciles to paper_trade_record
    (Path C: live rows where result IS NULL). Match by broker_order_id stamped
    at entry time.
  - Edge case: restart between TP1 fill and TP2 — broker shows reduced
    position, paper_trade_record.extra_json shows filled_legs=['tp1'].
    Recommend reconciliation: trust broker truth for current open qty;
    extra_json filled_legs is authoritative for lifecycle state. Document.
  - Scope: fold into N+2 (needed for any live position to survive a restart)
    or defer (with explicit "no restart during live position" operational
    constraint). Recommend.

#7 Operational alerts surface
  - Beyond elevated-suffix on first-N (from Phase 1a #8), what additional
    audit kinds + telegram shapes for exits?
    - exit_order_placed (intent BEFORE data_exec.place)
    - exit_order_filled (broker fill confirmed)
    - exit_order_rejected (broker error)
    - exit_partial_fill (qty < requested)
    - position_closed_with_pnl (after ALL legs closed)
  - Reuse same TelegramChannel singleton (Phase C principle).
  - Existing bitunix_lifecycle_notifier.py is the natural home for some
    of these (already handles TP/SL/close-out for paper). Quote its current
    surface; recommend additions for live exits.

Phase 2 scope decision — A/B/C based on Phase 1a + Phase 1b findings
  (A) Full N+2: exit path + reconciliation + cost accrual + alerts + restart-resume
  (B) Narrowed N+2: exit path + alerts only; defer reconciliation/cost/restart to N+3/N+4
  (C) Refactor surfaced as blocker

Constraints carried forward from Phase 1a:
  - No code changes in Phase 1b (still read-only)
  - Stop-and-report at the end of Phase 1b
  - Branch bitunix-live-exit-path-2026-05-29 already exists (off main)
  - Default execution_mode: paper everywhere
  - Phase 1a's recommendations stand independently — Phase 1b refines scope,
    doesn't override structural decisions

Output: append to reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md
or write phase1b.md (your call). Recommend scope (A/B/C). STOP for operator
confirmation before Phase 3.
```

---

*Sources: code reads on main (`paper_trade_replay.py`); broker-write branch (`brokers/bitunix.py` post-`87dac50`); N+1 entry-path branch (`[[bitunix-live-entry-path-pattern]]` memory); planning docs (`runbooks/2026-05-29_bitunix_live_readiness_audit.md`). Memories: `[[bitunix-live-engine-build]]`, `[[bitunix-order-path-safety-pattern]]`, `[[telegram-audit-success-is-confirmed-delivery]]`, `[[verify-premises-against-ground-truth]]`. No code / config / deploy changes made.*
