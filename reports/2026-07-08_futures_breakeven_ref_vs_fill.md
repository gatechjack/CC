# Bitunix futures breakeven SL: ref-vs-fill fix

**Date:** 2026-07-08
**Branch:** futures-be-ref-vs-fill-2026-07-08 (off origin/main) · commit `f4d1863`
**Status:** ✅ DEPLOYED + VERIFIED-BOOT LIVE 2026-07-08 (PID 108070); effect-verification pending next futures TP1 fill.

## Finding 1 — the feature was already live (earlier map was wrong)
The bitunix_futures "move SL to breakeven on TP1 fill" is **already implemented and running live** —
`move_bracket_sls` (`bitunix_position_reconciler.py`) runs every 60s (wired `main.py` ~2024-2037),
detects a TP fill (broker qty < recorded entry qty), computes the new SL via `decide_sl_move`
(`bitunix_bracket.py`), and calls the REAL broker method `modify_position_sl` → BitUnix
`/api/v1/futures/tpsl/position/modify_order`. 515 `position_sl_update` audit rows (`moved:true`,
`source:"bracket_sl_move"`), most recent today.

An earlier code map found only the **dormant** path — `decide_sl_action` + `_log_position_sl_update`
(`would_call_broker=False`) + the `modify_position_tp_sl_order` NotImplementedError stub — and wrongly
concluded "Phase-4 wiring unfinished." That path is dead scaffolding. (The 2026-07-02 SL-trail fix
`701a9fb` is about `move_bracket_sls` — the live path.)

## Finding 2 — the bug (operator-reported): breakeven used the reference, not the fill
`move_bracket_sls` fed `entry_reference_price` (the 3m-BOS signal price) into `decide_sl_move`'s
breakeven branch (`target = entry_price`), so the "breakeven" stop was set to the **reference**, off by
the entry slippage. Close P&L already books from the actual fill (`_resolve_entry_price`); only the live
SL placement was wrong.

**Prod evidence** (order `cb476516`, BTC long, breakeven move 01:06 2026-07-08):
- `entry_reference_price` = 63602.3
- `actual_entry_fill_price` = **63608.2** (the true fill)
- breakeven stop set (`current_sl` / audit `new_sl`) = **63602.3** ← the reference
→ 5.9 pts below the true long entry, so a stop-out after TP1 books a small **loss**, not breakeven.
The fill is available: 19/19 recent live futures records carry `actual_entry_fill_price`.

## The fix (one expression)
`bitunix_position_reconciler.py`, `move_bracket_sls`, the `entry_price` fed to `decide_sl_move`:
prefer `extra['actual_entry_fill_price']`, fall back to `entry_reference_price` (matches
`_resolve_entry_price`). SFP untouched (single-TP). The TP2 trail (`target = tp1_price`) is
venue-TP-based — unchanged.

## Behavior on deploy
- New TP1 fills → breakeven stop at the true fill.
- Any OPEN partial position on a reference-based breakeven stop auto-corrects on the next 60s tick
  (tighten-only → toward the true entry; safe direction). Idempotent thereafter.
- No P&L backfill (close P&L already used the fill).

## Deploy
Prod reconciler diverges from main (prod=701a9fb, 1638 lines). Applied the hunk onto prod's version
(drift-gated, anchor-asserted), `.bak-pre-befix-2026-07-08` backup, prod md5 `0cc06ab0`, flat-guarded
restart (0 open positions) → PID 108070, clean boot.

## Verification pending
On the next futures TP1 fill: `SELECT ... FROM audit_event WHERE kind='position_sl_update'
ORDER BY id DESC LIMIT 1` → confirm `new_sl` equals that record's `actual_entry_fill_price`, NOT
`entry_reference_price`. Also confirm the venue stop moved (journal "BitUnix SL moved ...").
