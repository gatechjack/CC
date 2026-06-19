# P2 classifier fix + maker/taker recording + yellow_x declassification — BUILD (Phase 2)

**Date:** 2026-06-19 · **Branch:** `bitunix-tpsl-rebuild-2026-06-18` · §4 build+test, NO deploy, NO record write.
Follows the approved Phase-1 diagnosis (report `fa4eece`). Operator decisions baked in.

## Fix 1 — P2 classifier (the actual bug)
`bitunix_position_reconciler.py` both auto-book paths now derive the labels instead of hard-coding them:
- **result** ← `classify_result(net_pnl, gross_pnl)` (new, pure, in `bitunix_bracket.py`): NET basis when
  available, else gross; never a literal. (Was `result='loss'`.)
- **exit_kind** ← `classify_exit_kind(...)` (new, pure): order-id match (close fill ∈ `bracket_tp_order_ids`
  → tp / == `bracket_position_sl_order_id` → stop), else price-vs-levels, else **`'unknown'` — never
  defaults to `'stop'`**. (Was `autobook_level_type='stop'`.)
- **mirrored to BOTH** `$.autobook_level_type` AND `$.exit_kind` (unified field). The audit rows now carry
  the derived `result`/`exit_kind` too.
- **Reader-audit (done, clean):** `autobook_level_type` has zero readers; `exit_kind` has no `extra_json`
  reader anywhere (web/stats/templates) — so writing it live disrupts nothing.
- Estimate path: result derived from gross sign; `exit_kind='stop'` retained (it books AT the stop level
  by construction).

## Fix 2 — `mc_a_yellow_x` declassification (NOT a sell-flip)
It is a **non-directional** whale/manipulation flag. The config only supports `buy`/`sell`/`directional`
sides (no explicit context value); the scorer (`btc_accumulator.evaluate_confluence`) gives a signal
**absent from `factors:` 0 directional points** (line ~249, "unknown signal — silently ignore") while it
still flows through the alert ledger — exactly how the other non-directional signals are handled. So
`mc_a_yellow_x` was **removed from `factors:`** (replaced by a doc comment). NOT flipped to `sell` (the same
error inverted), NOT a new mechanism. Live scoring effect: it no longer adds spurious bull points.

## Fix 3 — maker/taker recording (additive, forward-only)
Venue role field CONFIRMED from the BitUnix trade-history doc: **`roleType` ∈ {MAKER, TAKER}**.
- `get_recent_close_fills` now keeps `role` + `order_id` per fill (were discarded).
- `_aggregate_close_fills` produces `exit_role` (maker/taker/mixed/unknown) + `maker_taker_mix`
  (maker/taker qty + fraction) + `close_order_ids` → persisted as `$.exit_role` / `$.maker_taker_mix`.
- Entry side: `FillEvent.role` (new, default `''`) threaded `_fill_price_from_history` → `_observe_fill`
  → `place_order` → observer stamps `$.entry_role`. (Always 'taker' while B2 maker is OFF; ready for B2.)

## Tests / regression
- New `tests/test_bitunix_p2_classifier.py` (+ 1 updated assertion in the auto-book test): classify_result
  (net basis, zero→loss, the 2 records→win); classify_exit_kind (order-id, price short/long, the 5 live
  records, ambiguous→unknown, stop-without-tps); role mix; the end-to-end real auto-book (positive PnL →
  result=win/exit_kind=tp/exit_role recorded, PnL value unchanged; negative → loss/stop); entry role
  threading; `mc_a_yellow_x` absent from every config `factors:` block.
- **Full suite 28F + 3E == known baseline** (iron-condor/tasty/robinhood/paper-tooling/webhooks + 3
  collection errors). **Zero new regressions.**

## Historical correction — `deploy/2026-06-19_p2_record_correction/` (DRY-RUN ONLY, Board-gated)
Read-only dry-run confirmed the scope is **exactly 2 live records** (`e1758fc9`, `7d1a78dc`). Key finding:
a blanket sign-flip would wrongly convert **3 paper `'expired'`** rows (0 PnL) to loss — so the correction
is scoped to the 2 explicit order_ids, guarded by `result='loss' AND actual_pnl_dollars>0`, LABEL-ONLY,
with a full-table backup + idempotent apply + rollback. **Not applied** — operator reviews dryrun → backup
→ apply.

## Status / next
Code committed on branch, pushed, unmerged. The **classifier + yellow_x changes need a redeploy**
(separate, drift-gated, like the tpsl legfix); maker/taker is forward-only (takes effect on that redeploy);
the **record correction is operator-gated** (run the package's scripts after review). Flagged (NOT changed,
out of scope): `exit_method='server_side_sl_B1'` is also hard-coded in the auto-book — same family, operator
decision whether to derive it too.
