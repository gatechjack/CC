# SETTLEMENT-CLOSE PATH -- PHASE 0 (current state) + PHASE 1 (design). READ-ONLY. HALT for Jack.

Fixes the defect behind TWO total outages (46 scopes 2026-09-22, all 92 on 2026-10-06): book_settlements
silently SKIPS any market_result outside {yes,no,void} (settlement.py:169) -> journal held open -> next restart
(any division) latches the account. The fix is "NEVER skip a class silently", not "handle scalar".

## PHASE 0 -- CURRENT STATE (verified, not inherited)
1. **Running engine == origin/prod-live (074ef365).** Canonical `tr -d '\r' | sha256sum` on BOTH sides matches on
   all key PM files: settlement.py a1abe0e3, execution.py a8a03b59, live_driver.py 4bbe3023, boot_reconcile.py
   ecce7777, arm.py b542e9ff. (Changed from last night: box ran b4b2c089 ahead of prod-live; the 10-02 props+MACE
   folds advanced prod-live to 074ef365, byte-equal to the box for these files.) Engine PID 664274, NRestarts 0,
   boot 2026-10-07 23:30:18Z. **BUILD BASE = origin/prod-live (074ef365).**
2. **Schema head 24** (contiguous 1..24). No migration ran today. Next free number = **025** (023=pm_whale_score,
   024=pm_subdivision_attachment_event).
3. **settlement.py (204 lines, a1abe0e3)** read in full. Conventions it uses for the classes it handles:
   - `avg_cost = entry_cost/entered`, `entry_cost = SUM(fill_count*fill_price + fee)` -> **FEE-INCLUSIVE** (_HELD_SQL).
   - `realized = round(net_open*settled_value - net_open*avg_cost, 6)`.
   - yes/no: `settled_value = 1.0 if won else 0.0`, `close_source='settlement'`, `won=_won(leg,result)` (1/0).
   - void: `settled_value = avg_cost` (refund -> pnl 0), `close_source='settlement_void'`, `won=None`.
   - INSERT: is_exit=1, outcome_status='filled', fee=0 (entry fee booked at entry), submitted_ts=response_ts=now_ts,
     settled_ts=rec.settled_ts. Idempotency gate: `net_open = entered - exited; if net_open<=EPS: skipped_flat`.
4. **The 4 hand-booked rows (4619-4622) are idempotent-safe:** KXUFCFIGHT net_open=0 on all four (entered==exited,
   4 is_exit=1 rows) -> book_settlements hits `skipped_flat` -> would NOT re-book. (Confirmed on the box.)

## PHASE 1 -- DESIGN (from venue + code, not inference)

### Q1 -- market_result values Kalshi actually emits (enumerated, ~2085 settlements, 4 accounts, real history)
   yes 1051 | no 1028 | scalar 6 | (NO void, NO refund, NO unknown observed in-window).
   So empirically only {yes, no, scalar}; void/refund are designed-for but unobserved; no surprise class seen.

### Q2 -- what the venue gives us to book from (per-class field inspection)
   Every settlement record carries **`value` = per-contract settled value in CENTS**:
   - yes example (we held 2 yes, won): value=**100** (=$1.00/ct), yes_total_cost=1.70.
   - no example (we held 15 yes, result=no -> lost): value=**0** (=$0.00/ct), yes_total_cost=9.15.
   - scalar (held 5 yes): value=**45** (=$0.45/ct), yes_total_cost=2.30, revenue=225 (=5x45c).
   **`value/100` is the universal, class-agnostic per-contract settled value** (yes->1.0, no->0.0, scalar->0.45).
   ⚠️ **`revenue` is UNRELIABLE** -- the `yes` win showed revenue=0 (should be 200c); only scalar's revenue was
   consistent. Book from `value`, NOT revenue. (This is also why the cross-check WARNs 22x/day -- separate backlog.)
   Refund `value` is UNOBSERVED -> for void/refund we keep the proven avg_cost refund (do NOT assume), see Q5.

### Q3 -- won per class
   yes/no (binary): `won=_won(leg,result)` (1/0) -- UNCHANGED. scalar / void / any non-binary / unknown: **won=NULL**
   (not a win, not a loss). 348 existing is_exit=1 rows already carry won=NULL; consumers guard it.

### Q4 -- close_source per class
   yes/no -> `settlement` (unchanged). void -> `settlement_void` (unchanged). **scalar -> `settlement_scalar`**
   (SAME class as last night's 4 hand-booked rows -> automated + hand rows are one queryable set). Unknown ->
   `settlement_<sanitized_raw>` (e.g. a future refund -> `settlement_refund`), else `settlement_unknown`.

### Q5 -- UNKNOWN-class behaviour (the heart): BOOK FLAT + FAIL LOUD (do NOT refuse)
   When market_result is NOT a known handled class, the path **BOOKS a terminal close that nets the position
   flat** (is_exit=1, fill_count=net_open) so boot_reconcile stays CLEAN -- and emits a loud WARNING + a queryable
   row. It does NOT refuse.
   - settled_value = `value/100` leg-adjusted: `yes_val=value/100; settled_value = yes_val if leg=='yes' else
     (1-yes_val)`. If `value` is ABSENT (a future class without it) -> settled_value=avg_cost (conservative
     refund-equivalent, pnl~0) + a louder WARN.
   - won=NULL; close_source=`settlement_<raw>`; realized = the value-based estimate (flagged for human review).
   - `log.warning("pm settlement: NON-STANDARD market_result=%r on %s (acct=%s) -> booked flat at %.4f
     (close_source=%s, won=NULL); HUMAN REVIEW", ...)` + add to the return summary's `nonstandard` list so the
     boot-scan logs it.
   - **WHY booking (not refusing) does not reintroduce the latch:** REFUSING leaves the journal open -> the exact
     boot_reconcile mismatch that caused both outages -> latch. BOOKING nets the journal flat -> reconcile clean
     -> no latch. The WARN + the non-standard close_source are the fail-loud surface, so a novel class is reviewed
     by a human WITHOUT the system silently degrading. Fail-loud != fail-open; we never leave it silently open.
   - Visible surface: engine-side = the WARNING (grepable) + the queryable `close_source LIKE 'settlement_%'` +
     the return `nonstandard` summary. A `/live` "non-standard settlement" strip is a pm_web FOLLOW-UP (separate
     from this engine build); until it exists, the standing watch (Phase 4) is the surface.

### Q6 -- migration?
   **NO migration.** close_source is TEXT (accepts settlement_scalar / settlement_refund / settlement_unknown);
   won/realized_pnl/settled_ts already exist. No DDL -> no 025 -> **avoids the pm_cli-cron migration race entirely.**

### Q7 -- out of scope (noted, NOT bundled -- one engine change at a time)
   (a) the account-wide latch blast radius (one stuck ticker latches every category on the account); (b) the latch
   destroying prior armed state. Both real, both separate rungs. This build only stops the SILENT SKIP.

## THE CHANGE (additive, Phase 2 preview -- settlement.py ONLY)
   - SettlementRecord + parse_settlements: ADD `value` (cents) from the raw record.
   - book_settlements result branch (replaces the :169 skip): keep yes/no + void EXACTLY as-is; ADD scalar +
     catch-all unknown -> book-flat-at-`value` + won=NULL + close_source + WARN + `nonstandard` summary. Idempotency
     (net_open<=0 skip) is unchanged and already covers re-booking. ZERO change to the gate stack / matchers /
     order path (settlement.py imports no broker; PM-only; no shared-trio exposure).

## HALT -- awaiting Jack's approval of this design before any code (Phase 2).
