# P2 auto-book + latch-release — engine self-recovery after a server-side close

- **Branch:** `bitunix-p2-autobook-latch-release-2026-06-14` (off main **`299b40c`** — NB the task said `be86888`; main advanced E2.5/E2.6 since the consolidation, reconciler files untouched, so the fix lands cleanly on latest main). **§4-gated: BUILD + TEST only — NOT deployed; deploy is a separate operator-gated step.**
- **Fixes:** the two coupled auto-recovery gaps seen live on trades 1 & 2 (manual book + restart after every bot stop-out). BACKLOG P2 updated.

## The problem
A server-side stop/TP closes the position broker-side; the bot's replay/exit path (which expects to PLACE the close itself) never books it → `paper_trade_record.result` stays NULL → reconciler `missing_on_broker` → `_halt_new_orders` latched. And the latch **never cleared on a clean tick** (only a broker re-init/restart cleared it). So each bot stop-out forced a manual book + restart.

## The fix (both in `reconcile_position_state`, `bitunix_position_reconciler.py`)

### 1. Auto-book at the KNOWN stop level (quick-fix estimate)
A `missing_on_broker` bot row (it's a tracked `result IS NULL` live row) is auto-booked at its known stop level:
- **Close-reason determination (stored state only — NO price fetch):** `filled_legs` empty ⇒ no TP was reached ⇒ the broker-side close can only be the **server-side stop** (TPs are bot-side reactive closes that would already be booked) ⇒ book at `stop_price`, `result='loss'`. If a TP leg WAS reached (`filled_legs` non-empty) the close is ambiguous (deeper TP vs ratcheted stop) ⇒ **DEFER** (leave NULL, flag `autobook_deferred`). Also defer if the stop level / entry is missing. **Don't guess.**
- **PnL:** `(entry − level) × qty` for a short / `(level − entry) × qty` for a long — sign-correct, always a loss for a stop-out.
- **Marked as an ESTIMATE, never authoritative:** `result_source='auto_booked_from_stop_level'` (NOT `operator_manual_booking`), `pnl_basis='known_level_estimate'`, `slippage_unreconciled=true`, `exit_method='server_side_sl_B1'`, `exit_side` (buy-to-close a short). Exit fee is left unset (unknown without a fill fetch). Writes an `auto_book_server_side_close` audit.

### 2. Latch-release on a clean tick
On a clean reconcile (no missing, no orphan) the latch `_halt_new_orders` is cleared so the engine self-resumes **without a restart** (writes a `position_state_halt_released` audit). The existing set-on-divergence path is unchanged.

### Safety guards (the load-bearing part)
- **2-consecutive-tick confirmation** for BOTH auto-book and release. A single empty `get_pending_positions` can be a transient API error (it returns `[]` on error *and* on a real flat — indistinguishable from one call). So a missing row is booked only if it was ALSO missing the prior tick, and the latch releases only if the prior tick was ALSO clean. The cross-tick memory is read from the audit trail → the reconciler stays **stateless**.
- **Never release into a genuine orphan:** an unowned position (the manual-short case) surfaces as `orphan_on_broker` ⇒ divergence ⇒ the release branch never runs. Tested explicitly.
- **Gated** on `halt_on_divergence=True` (the sanity-poll path) + a non-stub broker — so the separate `resume_live_positions` startup path and paper mode are unaffected.

## Tests (`tests/test_bitunix_reconciler_autobook_latch.py`, 11)
Confirmed-stop-close → auto-booked at stop level (loss, sign-correct, flagged estimate, NOT authoritative); long-stop PnL sign; **unconfirmed (single-tick) missing → NOT booked**; partial-TP → DEFERRED (NULL + flag); two consecutive clean → halt released; single clean tick → stays latched; **full close→auto-book→release self-recovery**; **genuine orphan → stays halted, no release**; orphan + bookable bot row coexist correctly. Plus 72 existing reconciler-related tests pass; full same-env gate vs main `299b40c` = zero new regressions.

## Accuracy caveat → permanent fix (BACKLOG)
The known-level estimate WILL differ from the real fill: **trade 2's recorded `stop_price` was 65004.48 but it filled 65142.3 (~138pt / 0.52% slippage)** — the estimate books ≈−0.107 vs the real −0.134, and leaves the exit fee unset. The `slippage_unreconciled` flag makes every estimate-booked row visible for later true-up. The **permanent fix** (BACKLOG, filed) replaces the estimate with a **signed broker trade-history query** for the exact fill price/PnL/fee — a signed/public-API call, outside this §4 known-level scope, lands separately.

## Disclosure (82fda13)
Local source review + edits + local pytest (build+test) on a branch; read-only SSH was used only to ground the live trade-2 `extra_json` schema. **No deploy, no restart, no prod write, no signed/public-API call.** Branch pushed, UNMERGED — for operator review.
