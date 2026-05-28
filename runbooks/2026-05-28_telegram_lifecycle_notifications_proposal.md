# Telegram lifecycle notifications for bitunix paper trades — proposal

**Status:** Design + plan only. No code, no template, no observer/lifecycle touch this commit.
**Author:** session 2026-05-28
**Companion docs:** [`runbooks/2026-05-27_bitunix_dashboard_consolidation_proposal.md`](2026-05-27_bitunix_dashboard_consolidation_proposal.md) (V1 of which surfaced the `actual_pnl_dollars` persistence gap that gates §D here).
**Decision deadline:** before next implementation session is opened on this thread.

## Why now

Operator currently gets one Telegram message when a bitunix paper trade OPENS. Nothing fires on TP1 / TP2 / TP3 fills, SL ratchet moves, or final close-out. The bitunix PA 2-of-3 observation window (closes 2026-06-03 ~23:18 UTC, per `[[bitunix-pa-2of3-deploy]]`) is the first window where placement rate may exceed ~1/day, so the operator wants to follow the lifecycle telemetrically rather than via web-dashboard polling.

Scope of THIS proposal: design only. A separate scoped session implements after operator reviews the recommendations + decision checkpoint in §H.

Scope guards (non-negotiable):
- Notifications are **observability-only.** They never block, modify, or delay the trade lifecycle. Failures log, never propagate.
- The existing entry-notification code path is **not touched** this PR. Future harmonization is fine; this PR is purely additive.
- The existing `TelegramChannel` infrastructure is **reused.** Do NOT build a parallel telegram client.
- Every notification template carries a clear paper-mode prefix; format degrades cleanly when live mode lights up later.
- Tripwire / gate / PA-2of3 logic is untouched. Out of scope here.

---

## §A — State audit: existing telegram infrastructure

### A.1 API layer

- Library: **`python-telegram-bot` v20+** (lazy-imported in `TelegramChannel.start()`).
- Class: `TelegramChannel(BoardChannel)` at `trading_corp/comms/telegram_bot.py:28-83`.
- Send call: `self._app.bot.send_message(chat_id, text, parse_mode="Markdown")` (`telegram_bot.py:181, 250, 272`).
- Max length: 4096-char API limit; truncation enforced at `telegram_bot.py:184`.
- Conflict / exception handling: catches `Conflict` (another bot polling same token, lines 124-141) — signals shutdown; catches generic exceptions and logs warning only.

### A.2 Secrets

- `TELEGRAM_BOT_TOKEN` — read via `utils/secrets.py:283` (`_env`).
- `TELEGRAM_CHAT_ID` — read via `utils/secrets.py:284`.
- Token is in `_SECRET_KEY_NAMES` (`secrets.py:22`) → redaction filter on root logger covers any accidental log mention.
- **C-1 rotation deferred** for `TELEGRAM_BOT_TOKEN` per BACKLOG.md line 157 (per-portal session; no in-code change required).
- New code MUST continue to read from `secrets.telegram_bot_token` + `secrets.telegram_chat_id` — never inline secret strings.

### A.3 Batcher

- `trading_corp/comms/telegram_batcher.py` provides `TelegramBatcher` (window-based coalescing with bypass-tag escape hatch).
- Currently used **only by IC strategy** (`main.py:1129`).
- Bitunix is **direct-push** (does NOT route through the batcher).

### A.4 Bitunix entry notification — current state

Two distinct paths, **both direct-push (NOT batcher), NO bypass tags, errors caught + logged + dropped:**

**Path A — traditional trigger flow** (`bitunix_futures_observer.py:2429-2446`):

```text
BTC-PERP {tier} {side_arrow} (paper)
trigger: {verdict.trigger_signal}
entry: ${entry_price:,.2f}  qty: {qty:.4f}
stop: ${stop:,.2f}  tp: ${tp:,.2f}
size: {size_pct}%  lev: {lev}x  eff_risk: {risk_pct}%
bias_4h={…}  bias_1d={…}  cvd={…}
```

**Path B — score-based flow** (`bitunix_futures_observer.py:1557-1573`):

```text
BTC-PERP {tier} {LONG|SHORT} (paper, score)
net_score: {net} (buy={buy}, sell={sell})
entry: ${entry_price:,.2f}  qty: {qty:.4f}
stop: ${stop:,.2f}  tp: ${tp:,.2f}
size: {size_pct}%  lev: {lev}x  eff_risk: {risk_pct}%
```

Paper-mode marker in templates today is the parenthetical suffix `(paper)` or `(paper, score)`. There is no `[PAPER]` bracket convention or 📄 icon convention in the existing template.

### A.5 Channel wiring

- `main.py:791` attaches the `TelegramChannel` instance directly to `bitunix_observer.telegram_channel`.
- Lifecycle notifications need the same channel reference. Two reuse patterns are workable; the implementation session picks:
  - Pass `TelegramChannel` into the replay tick via a deps dict (mirrors `_REPLAY_DB_URL_CTX` at `paper_trade_replay.py:699`).
  - Construct a small `BitunixLifecycleNotifier` at startup, wire alongside the existing channel attach, hand into the replay closure.

### A.6 Bypass tags

Defined in `config/strategies.yaml:1766-1770` for IC strategy:
```yaml
telegram_bypass_tags:
  - circuit_breaker_auto_repause
  - catastrophic_stop
  - startup_catchup
  - late_dte_force_close
```
Bitunix lifecycle notifications do NOT need bypass tags — they are routine observability, not approval-gated. Direct-push (matches entry-path pattern) is the recommended default.

### A.7 Gotchas worth carrying forward

- `parse_mode="Markdown"` — escape `*`, `_`, `` ` `` in price/qty fields if free-form.
- No retry logic. Single failure = single drop. Operator gets no indication unless `telegram_notification_failed` audit kind is added (see §F).
- No rate-limiting; rely on Telegram's ~30 msgs/sec per-chat ceiling. Lifecycle rate is far below this.

---

## §B — Event mapping: what writes the lifecycle audit

### B.1 `position_sl_update` is the canonical lifecycle audit kind

Both TP fills AND SL moves are merged into one audit kind, distinguished by `lifecycle_state`. Two writers exist:

**Writer 1 — `bitunix_position_reconciler._log_position_sl_update`**
- File: `trading_corp/agents/divisions/bitunix_position_reconciler.py:207-237`
- **Dormant in paper mode today.** Fires only when Phase 4 wires real broker fill state.

**Writer 2 — `paper_trade_replay._v2_audit_writer`** (the ACTIVE paper-mode writer)
- File: `trading_corp/agents/paper_trade_replay.py:654-693`
- Called from `_classify_v2_multi_leg._emit_audit` (`paper_trade_replay.py:447-472`)
- Payload (verbatim `paper_trade_replay.py:665-676`):
  ```python
  payload = {
      "order_id": order_id,
      "symbol": symbol,
      "side": side,
      "lifecycle_state": lifecycle_state,   # see §B.2
      "current_sl": current_sl,             # OLD SL (pre-move)
      "new_sl": new_sl,                     # NEW SL (post-move)
      "reason": reason,
      "filled_legs": list(filled_legs or []),
      "would_call_broker": False,
      "source": "paper_trade_replay",
  }
  ```

### B.2 `lifecycle_state` semantics

From `_decide_lifecycle_sl` (reconciler) + observed v2 classifier behavior:

| `lifecycle_state` | Meaning | Notification |
| --- | --- | --- |
| `post_tp1` | TP1 just filled; SL moves entry → breakeven | TP1-fill bundled message |
| `post_tp2_floor` | TP2 just filled; SL moves breakeven → TP1 price | TP2-fill bundled message |
| `post_tp2_trail` | Chandelier trail active (no TP fill, pure SL ratchet) | Trail-only message (or skip first pass — see §C.4) |

A single `position_sl_update` row = one bundled state transition. **The TP fill and its corresponding SL move are ALWAYS in the same row** — the SL move IS the post-fill state. This matches the task brief's "bundle TP fill + SL move into one message" exactly; no extra coalescing logic is needed in the notifier.

For TP3 there is NO `position_sl_update` row — the trade closes via the `_Resolved` verdict path. TP3 fill = the close-out message in §C.3.

### B.3 Close-out signal

`_classify_v2_multi_leg` (`paper_trade_replay.py:401`) returns `_Resolved`:
- Line 533 — `result="win"` (partial-TP banked then SL hit) or `"loss"` (SL hit, no fills)
- Line 572 — `result="win"` (TP3 hit)
- Line 632 — `result="expired"` (max_hold elapsed with partial fills)

`_update_row` (`paper_trade_replay.py:1051-1086`, called at `paper_trade_replay.py:778`) writes `result` / `actual_pnl_dollars` / `actual_r_multiple` / `bars_to_resolution`. **This is the natural hook for the close-out notification.**

### B.4 Routing guard

`_replay_tick_async` (`paper_trade_replay.py:705-784`) routes only when:
```python
is_v2 = (
    row.division == "bitunix_futures"
    and bool(extra.get("tp_plan"))
    and extra.get("tp_plan_version") == "v2"
)
```
Notifier MUST guard on the same `(division, is_v2)` predicate so legacy single-leg paper trades don't accidentally fire bitunix-shaped messages.

### B.5 Sample audit rows — NOT retrieved this session

Per `[[verify-premises-against-ground-truth]]`, sample rows should ground the design. Sample row contents were not pulled this session because (a) plan-mode is read-only and (b) the operator runs prod queries. The implementation session opens with the prod query in §D.1 to confirm both the audit-row payload AND the `expected_gain` hypothesis simultaneously.

Operator query to capture rows during implementation kickoff:
```sql
SELECT ts, payload_json
FROM audit_event
WHERE kind = 'position_sl_update'
ORDER BY ts DESC
LIMIT 20;
```

---

## §C — Message templates

### C.1 Paper-mode convention (proposed)

- Prefix: `📄 [PAPER]` on the FIRST line of every lifecycle message.
- The prefix string is a **per-mode template variable**, not hard-coded — when live mode lights up later, flip the variable to `💸 [LIVE]` (or empty string + a different opening icon). Existing entry-notification templates stay as-is for this PR; harmonize on the next touch of the entry path.
- This convention makes paper vs live unmistakable in Telegram scrollback and `grep`able in any future Telegram-export tooling.

### C.2 TP1 / TP2 fill (bundled with SL move)

TP1 (after `lifecycle_state=post_tp1` audit fires):
```text
📄 [PAPER] BTC sell · TP1 filled
Entry: $75189.80 → TP1: $75054.46 (-0.18%)
R so far: +0.5R
SL moved: $75444.69 → $75189.80 (breakeven)
Position: 50% closed
```

TP2 (after `lifecycle_state=post_tp2_floor` audit fires):
```text
📄 [PAPER] BTC sell · TP2 filled
TP2: $74934.91 (-0.34%)
R so far: +1.0R
SL moved: $75189.80 → $75054.46 (post-TP1 floor)
Position: 75% closed
```

Variables sourced from the audit payload + the joined `_PendingRow`:
- `side` ← payload `side`
- `entry` ← row.entry_reference_price
- `leg target price` ← `tp_plan[leg]` (already in extra)
- `pct_move` ← (leg - entry) / entry × 100 with sign flip on sell
- `R so far` ← `_aggregate_multi_leg_r(...)` (existing helper in replay.py)
- `current_sl` / `new_sl` ← payload `current_sl` / `new_sl`
- `position % closed` ← static mapping (post_tp1 = 50%, post_tp2_floor = 75%) — accurate for the current v2 trade plan; if the trade plan ever supports non-equal leg sizes, derive from `tp_plan` weights instead.

### C.3 Close-out (TP3 fill OR SL hit OR expired)

TP3 hit (full lifecycle path):
```text
📄 [PAPER] BTC sell · CLOSED · TP3 filled (WIN)

Path:
  Entry  → $75189.80
  TP1    → $75054.46  (+0.18%)
  TP2    → $74934.91  (+0.34%)
  TP3    → $74552.56  (+0.85%)
  Exit   → TP3 hit

R-multiple: +1.62R
PnL: +$XX.XX                          ← see §D for prereq
Fees: not tracked in paper            ← see §E
Funding: not tracked in paper         ← see §E

Held: Xh Ym
```

SL hit (same shape, different label):
```text
📄 [PAPER] BTC sell · CLOSED · STOPPED OUT (LOSS)

Path:
  Entry  → $75189.80
  TP1    → $75054.46  (+0.18%)
  Exit   → SL hit at $75189.80

R-multiple: -0.5R
PnL: -$XX.XX
Fees: not tracked in paper
Funding: not tracked in paper

Held: Xh Ym
```

Expired (max_hold elapsed):
```text
📄 [PAPER] BTC sell · CLOSED · EXPIRED (max_hold)

Path:
  Entry  → $75189.80
  Exit   → last bar close $75100.00

R-multiple: +0.1R (partial TP1)
PnL: pending persistence              ← if §D not yet fixed
…
```

Variables for close-out (sourced from `row` + `verdict` at the `_update_row` hook):
- `result` ← verdict.result (`win`/`loss`/`expired`) → drives label + WIN/LOSS suffix
- `actual_r_multiple` ← verdict.actual_r_multiple
- `actual_pnl_dollars` ← verdict.actual_pnl_dollars (or "pending persistence" if 0.0; see §D)
- `entry / exit price` ← row.entry_reference_price / verdict.result_price
- Lifecycle `Path:` block ← reconstructed from `filled_legs` in extra_json + `tp_plan`
- `Held` ← (verdict.result_ts - row.ts) formatted

### C.4 Edge case: cluster-tick burst

Replay runs every ~900s. If TP1 → TP2 → TP3 all hit within one inter-tick window, the notifier emits 3 messages in rapid succession (TP1-fill, TP2-fill, close-out). Acceptable on first pass: each carries its own R-multiple progress and is grep-friendly. If the burst becomes noisy in practice, file a follow-up to swap the lifecycle path onto the existing `TelegramBatcher` with a ~30s window — the batcher's coalesce-to-deeplink semantics handle it for free.

`post_tp2_trail` audits (pure SL ratchet, no TP fill) MAY also fire multiple times per tick. **Recommendation:** suppress `post_tp2_trail` lifecycle notifications by default (notify only on `post_tp1`, `post_tp2_floor`, and close-out). Trail moves are visible on the dashboard and don't change R-multiple; pinging every trail step would dilute the signal. Operator decision in §H.

### C.5 Markdown safety

Templates use `parse_mode="Markdown"`. The prices and figures in §C.2 / §C.3 don't contain Markdown special chars, but if a future template ever interpolates a free-form `reason` string from the audit payload, the implementation must escape `*`, `_`, `` ` ``, `[`. Use a small helper.

---

## §D — $PnL prereq (the gating dependency)

> **RESOLVED 2026-05-28 (Phase 1 shipped).** The §D.1 premise below was REFUTED by a full prod diagnostic and the gap is now fixed. Summary: the "all 76 rows = 0.00" claim was wrong (generalized from a 3-row sample). Reality: 10/78 zero, of which 3 are correctly-zero `expired` and 7 are partial-win SCORE-path rows. Root cause = `_build_proposal_v2` omitted `expected_gain_if_tp_hit` + `tp_r_multiple` from `order.extra` (oversight vs. legacy `_build_proposal`), so `paper_trade_replay.py:526-531/569-571` fell to $0 only on partial-win closes. Fixed in the v2 builder (commit + deploy_log 2026-05-28); 7 rows backfilled; defensive `log.warning` added to replay when `expected_gain` is null at PnL-compute time. The original problem-statement and hypothesis below are preserved for the audit trail.

### D.1 Problem statement (AS ORIGINALLY WRITTEN — see correction above)

V1 of [`runbooks/2026-05-27_bitunix_dashboard_consolidation_proposal.md`](2026-05-27_bitunix_dashboard_consolidation_proposal.md) surfaced: all 76 bitunix `paper_trade_record` rows show `actual_pnl_dollars = 0.00`, while `result` + `actual_r_multiple` are correctly populated. BACKLOG.md line 123 carries the existing P2 item.

### D.2 Root-cause hypothesis (high confidence, not yet verified)

V2 multi-leg PnL computation at `paper_trade_replay.py:526, 569-571`:

```python
# line 569-571 (TP3 fill):
actual_pnl = float(row.expected_gain or 0.0) * (
    actual_r / max(1e-9, float(row.tp_r_multiple or 1.0))
) if row.expected_gain else 0.0

# line 526 (SL hit):
actual_pnl = expected_loss * abs(actual_r) if actual_r < 0 else 0.0
```

Both formulas fall to 0 when `row.expected_gain` / `row.expected_loss` is null/0. `expected_gain` is set at INSERT time (the `would_have_placed` audit, when the proposal is queued); grep across `bitunix_futures_observer.py` returns zero matches for `expected_gain =`, strongly suggesting these fields are NOT populated for bitunix paper rows.

### D.3 Single diagnostic query (operator runs on prod)

```sql
SELECT order_id, ts, result,
       expected_gain, expected_loss,
       actual_r_multiple, actual_pnl_dollars,
       tp_r_multiple
FROM paper_trade_record
WHERE division='bitunix_futures'
ORDER BY ts DESC
LIMIT 10;
```

Routes to one of two fix paths:

| Diagnostic result | Fix scope | Cost estimate |
| --- | --- | --- |
| `expected_gain` / `expected_loss` are NULL or 0 on all rows | Patch the observer's proposal-construction to set both at queue time | ~3-line patch + replay re-tick across the 76 historic rows |
| `expected_gain` / `expected_loss` ARE populated, value is sensible | Deeper bug in `_classify_v2_multi_leg` — needs separate read-only investigation | Unknown until investigated |

### D.4 Recommendation: fix-first

Per the task brief's preference (option a):

1. Operator runs the diagnostic above (~10 min).
2. If the hypothesis holds, ship the observer patch in a small standalone session (~30-60 min).
3. Re-run replay to backfill the 76 historic rows (`actual_pnl_dollars` updates without re-INSERT).
4. THEN start the lifecycle notification implementation session.

Why fix-first over compute-at-notification-time:
- Dashboard rebuild (P2, `runbooks/2026-05-27_bitunix_dashboard_consolidation_proposal.md`) also wants the field. Fixing once unblocks both surfaces.
- Notifier-computed values would disagree with the dashboard's persisted-value tile during the window between PRs — confusing and harder to debug if mismatched.
- The replay's formula is the authoritative one; the bug is in upstream INPUT, not the formula. Fixing the input is the cleanest mental model.

Fallback (option b — compute-at-notification-time) is acceptable if §D.3 surfaces a deeper bug that makes the proper fix multi-day. In that mode, the notifier computes `qty × (exit_price - entry_price) × signed_dir` (no fees) and labels the figure `(notifier-side estimate)` so it doesn't look authoritative.

If §D is still unfixed at the time of the implementation session, the close-out message shows literal text `PnL: pending persistence` for that field — operator decision in §H.

---

## §E — Funding and fee data question

### E.1 Current state

`grep -i "funding|fee|taker|maker|commission"` across `paper_trade_replay.py` and `bitunix_position_reconciler.py` returns **zero matches**. `BitunixBroker.get_funding_rate()` exists at `trading_corp/brokers/bitunix.py:319` but is NOT called from any paper-mode path. **Neither fees nor funding are tracked in paper mode today.**

### E.2 Decision for this PR

Do NOT fake the numbers. The lifecycle messages show literal strings:

```text
Fees: not tracked in paper
Funding: not tracked in paper
```

A future paper-mode cost-accrual workstream batches both. Smallest-hook sketch (for the BACKLOG entry, not for this PR):
- At trade open in `bitunix_futures_observer`: snapshot `BitunixBroker.get_funding_rate(symbol)` and write to `extra_json.funding_rate_entry_pct`.
- At close in `_classify_v2_multi_leg`: compute `funding_fee_usdt = notional × funding_rate × (hold_minutes / (8 * 60))`. Subtract from `actual_pnl_dollars`.
- Fees: assume taker on entry + SL exit; assume maker on each TP fill. Apply per-leg rate × per-leg notional. Sum.
- Gate everything on `extra_json.funding_rate_entry_pct is not None` so legacy paper rows (and live mode) skip the synthetic-accrual path.

This sketch lands as a new BACKLOG P2 MEDIUM item; see §G.4.

---

## §F — Failure-mode handling

Observability-only — failures MUST NOT block trade lifecycle. Match the entry-path pattern at `bitunix_futures_observer.py:2434-2435` (catch + log warning + continue).

### F.1 Failure modes documented

1. **Telegram API down / rate-limited / token mid-rotation (C-1).** Catch, log warning, drop the message. Trade lifecycle continues. Operator can re-run replay manually if a critical close-out was missed (the dashboard reflects truth either way).
2. **Audit-row payload missing a field.** E.g. `new_sl` is None due to upstream weirdness. Notifier degrades gracefully: render `SL: —` instead of crashing. Never raise from inside the notifier.
3. **Async/sync mismatch.** Replay is sync code inside an async tick. The notifier's send is async (Telegram lib). Options:
   - Fire-and-forget via `asyncio.create_task(notifier.send(...))` from inside `_replay_tick_async` after the audit-write — this is the natural pattern for the close-out hook because we're already in async context there.
   - For the `position_sl_update` hook (which executes inside a sync function called from async): wrap via `asyncio.create_task` queued on the running loop, or accumulate messages in a small thread-safe queue drained by an async task at the end of the tick.
   - Both patterns are correct; implementation session picks based on minimum-touch.
4. **Notifier exception inside the create_task.** Wrap the inner coroutine in try/except → log warning. `asyncio` exceptions in fire-and-forget tasks are silent by default and we don't want that.

### F.2 New audit kind: `telegram_notification_failed`

Ship with the notifier. Schema:
```python
LoggerAgent.log_event(
    actor="bitunix_lifecycle_notifier",
    kind="telegram_notification_failed",
    payload={
        "order_id": order_id,
        "notification_type": "tp1_fill" | "tp2_fill" | "close_out",
        "failure_reason": str(exception),
        "ts": now_iso(),
    },
)
```
Lets operator query the audit table for silent drops instead of grepping logs:
```sql
SELECT * FROM audit_event WHERE kind = 'telegram_notification_failed';
```
Cost: ~5 LOC. Filed in the implementation, not a separate prereq.

---

## §G — Implementation notes for the next session

### G.1 Hook architecture

**Hook 1 — TP1 / TP2 fill + SL move:** in `paper_trade_replay._classify_v2_multi_leg._emit_audit` (`paper_trade_replay.py:447-472`), after the existing audit-write try-block, add a `notifier.notify_lifecycle_transition(...)` call with `order_id, symbol, side, lifecycle_state, current_sl, new_sl, filled_legs, entry_price, leg_price`. Guard with `if row.division == "bitunix_futures"` AND a default-on config switch.

**Hook 2 — Close-out (TP3 fill, SL hit, expired):** in `paper_trade_replay._replay_tick_async` (`paper_trade_replay.py:705-784`), after `_update_row(...)` at line 778:
```python
if (
    verdict.result in {"win", "loss", "expired"}
    and is_v2
    and row.division == "bitunix_futures"
):
    asyncio.create_task(notifier.notify_close_out(row, verdict))
```

**Hook 3 — Reconciler path (Phase 4-ready, dormant in paper):** in `bitunix_position_reconciler._log_position_sl_update` (`bitunix_position_reconciler.py:207-237`), mirror the Hook-1 call. Lights up automatically when Phase 4 wires real broker fills. Costs ~3 LOC now, zero ongoing maintenance.

### G.2 New module

Create `trading_corp/comms/bitunix_lifecycle_notifier.py` (~80 LOC). Suggested API:

```python
class BitunixLifecycleNotifier:
    def __init__(self, channel: TelegramChannel, *, paper_mode: bool = True):
        self._channel = channel
        self._prefix = "📄 [PAPER]" if paper_mode else "💸 [LIVE]"

    async def notify_lifecycle_transition(self, *, order_id, symbol, side,
                                          lifecycle_state, current_sl, new_sl,
                                          filled_legs, entry_price, leg_price,
                                          r_so_far, percent_closed): ...

    async def notify_close_out(self, row, verdict): ...
```

No batcher. Direct-push (matches entry-path pattern). Module is pure-Python with one channel dependency.

### G.3 Wiring

- Instantiate in `main.py` next to the existing channel attach at line 791.
- Pass the notifier into `paper_trade_replay` via a deps dict (mirror `_REPLAY_DB_URL_CTX` at line 699).
- Reconciler hook receives the notifier via constructor or module-level deps.

### G.4 Tests

- Unit-test the notifier with a stub channel; assert formatted text for each lifecycle state and each close-out result.
- Integration test on the replay tick: fixture trade resolves to TP3-win; assert stub-channel `pushed` list contains the expected sequence (TP1 fill + TP2 fill + close-out).
- Stub the channel — never make live Telegram calls in tests. Pattern at `tests/test_telegram_batcher.py` is the template.
- Mock `actual_pnl_dollars` value on the fixture row to validate both "populated" and "pending persistence" rendering paths.

### G.5 Estimated build cost

~3-4 hours, single PR: 1 file added (`bitunix_lifecycle_notifier.py`) + 3 files lightly touched (`paper_trade_replay.py`, `bitunix_position_reconciler.py`, `main.py`) + 1 new test file. Pure addition; no refactor of entry path; no template changes to existing entry messages.

### G.6 Sequencing

```
1. Operator runs §D.3 diagnostic on prod                       (~10 min)
2. Fix actual_pnl_dollars persistence per §D.4                 (~30-60 min) ← prereq
3. Re-run replay to backfill 76 historic rows                  (~5 min)
4. Verify dashboard $PnL tile populates with non-zero values   (~5 min)
5. Build + ship telegram lifecycle notifier per §G.1-G.5       (~3-4 hours)
6. Operator confirms first real TP1 fill produces telegram     (event-driven)
7. File any post-deploy observation items
```

### G.7 What is NOT in scope (don't auto-expand)

- Editing existing entry-notification templates.
- Touching `TelegramChannel` or `TelegramBatcher`.
- Switching bitunix to use the batcher.
- Funding / fee accrual (separate BACKLOG MEDIUM).
- Dashboard rebuild (separate session, separate proposal).
- Live-mode work.
- Tripwire / gate / PA-2of3 logic.

---

## §H — Operator decision checkpoint

Six recommendations. Operator confirms or overrides before implementation session opens.

| # | Decision | Recommendation | Alternative |
| --- | --- | --- | --- |
| 1 | $PnL prereq sequencing | **Fix first.** Diagnostic on prod (§D.3) → patch observer (§D.4) → backfill replay → THEN build notifier. | Ship notifier with `(notifier-side estimate)` annotation; backfill persistence later. Cost: dashboard ↔ notifier $-value disagreement window. |
| 2 | PAPER prefix convention | **`📄 [PAPER]` prefix on every lifecycle message.** Prefix string is per-mode config variable, so live mode flips it cleanly. Don't retro-touch existing entry templates this PR. | Reuse the existing `(paper)` suffix convention. Cost: less visually unmistakable; matches existing entry. |
| 3 | Direct push vs batcher | **Direct push.** Consistent with entry-path; per-message clarity. File burst-noise observation. | Batcher with ~30s window. Cost: TP1/TP2/TP3 cluster collapses to deeplink (loses per-fill detail). |
| 4 | Funding / fee accrual | **Do not track in this PR.** Message shows `not tracked in paper`. File as MEDIUM follow-up. | Track now. Cost: doubles the PR size; couples lifecycle notifier to a non-trivial cost-accrual workstream. |
| 5 | `telegram_notification_failed` audit kind | **Yes — ship with notifier (~5 LOC).** Operator-queryable trail of silent drops. | Skip. Cost: silent drops invisible; only logs reveal them. |
| 6 | Channel wiring | **Reuse the existing `TelegramChannel` instance.** Pass into notifier via deps; no second channel. | Construct a second channel. Cost: bot-conflict risk; defeats the secret-redaction story. |
| 7 | `post_tp2_trail` lifecycle | **Suppress (notify only on `post_tp1`, `post_tp2_floor`, close-out).** Trail moves don't change R; visible on dashboard. | Notify on every trail. Cost: dilutes lifecycle signal; possible noise during sustained moves. |

(Yes, that's seven — #7 was added under §C.4 and merits an explicit answer.)

---

## Appendix — read-only audit provenance

This proposal is grounded in code reads at the following file:line citations (verified during session 2026-05-28):

- `trading_corp/comms/telegram_bot.py:28-83` (TelegramChannel impl)
- `trading_corp/comms/telegram_batcher.py:35-141` (TelegramBatcher impl)
- `trading_corp/agents/divisions/bitunix_futures_observer.py:2429-2446` (entry telegram path A)
- `trading_corp/agents/divisions/bitunix_futures_observer.py:1557-1573` (entry telegram path B)
- `trading_corp/agents/paper_trade_replay.py:401-640` (v2 multi-leg classifier)
- `trading_corp/agents/paper_trade_replay.py:654-693` (v2 audit writer)
- `trading_corp/agents/paper_trade_replay.py:705-784` (replay tick + close-out hook point)
- `trading_corp/agents/paper_trade_replay.py:1051-1086` (`_update_row`)
- `trading_corp/agents/divisions/bitunix_position_reconciler.py:207-237` (reconciler audit writer, dormant in paper)
- `config/strategies.yaml:1766-1770` (bypass-tag definition)
- `main.py:791, 1129` (channel + batcher wiring)
- `utils/secrets.py:283-284, 22` (token + chat-id + redaction)
- `trading_corp/persistence/db.py:118-144` (paper_trade_record schema)
- `BACKLOG.md:123-125` (existing $PnL persistence P2 item)

Sample audit rows + sample paper_trade_record rows were NOT pulled this session per plan-mode discipline. The implementation session opens with the §D.3 diagnostic to capture both simultaneously.
