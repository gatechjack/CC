# ITEM 1 — first-side-wins conflict gate: BUILT (checkpoint, awaiting review)

Ratified 2026-08-18: scope + **Option B (durable journal query at placement)**. Built on branch
`poly-kalshi-item12-scope-2026-08-18`. Live loop untouched (nothing deployed). Tests green.

## What changed (2 runtime files + 2 test files; NO byte-locked file)
- `trading_corp/data/mlb_poly_kalshi_match.py` — new pure helper `game_key_and_side(ticker)` →
  `(game_key, side_code, date_str) | None`. `game_key = (date_iso, HHMM, game_no, frozenset{team_names})`
  is identical for BOTH side tickers of a game; `side_code` = YES team code distinguishes the sides;
  doubleheaders stay distinct via game_no/HHMM. Reuses `parse_kalshi_mlb_ticker`.
- `trading_corp/agents/strategies/poly_kalshi_executor.py`:
  - New `[G-conflict]` gate in `submit()`, placed **after `[G-idem]`** (so an exact re-fire is still
    `suppressed_duplicate`) and before `[G-daily]`. On an opposite-side conflict it returns
    `_record("skip_conflict", …)` — mutating NO state (no budget, no count, no coid burned), same as
    the other skips.
  - New `_opposite_side_on_game(order)` (Option B, durable): reads the audit journal
    (`actor=self._strategy`, `kind=poly_kalshi_order`, `status IN ('placed','DRY_RUN_would_place')`,
    `ticker LIKE 'KXMLBGAME-<date>%'`), derives each row's `(game_key, side)`, and returns the held
    opposite side if any. Survives an engine restart because it reads the journal, not in-process state
    (a same-cycle placement is already committed by `_record`'s synchronous `log_event`).
  - `_record` gained an `extra` param; `skip_conflict` rows carry `conflict_held_side` +
    `conflict_held_ticker` (feeds a future dashboard conflict state) alongside the `trigger` (the poly bet).

## Semantics (verified by tests)
- **Opposite side, same game → BLOCK** (`skip_conflict`), no state consumed, key not burned.
- **Same side, different whale → ALLOW** (stacking).
- **Same whale re-fire → `suppressed_duplicate`** ([G-idem], unchanged).
- **Doubleheader G1 vs G2 → NOT cross-blocked** (distinct game_key).
- **Restart durability (Option B) → a fresh executor still blocks** the opposite side from the journal.
- **Unparseable / non-KXMLBGAME ticker → fail-OPEN** (can't have a conflicting sibling; idempotency +
  slippage still apply). Any lookup error also fails OPEN (additive protection; never worse than pre-gate).

### ★ One noted decision (fail-OPEN on lookup error)
On a DB/lookup error the gate **fails open** (allows), consistent with the executor's gate idiom
(halt-read failure → not-halted, etc.) and the "additive protection" framing: a transient error reverts
to the pre-gate behavior, and blocking would also drop the legitimate FIRST side. If you'd rather it
fail **closed** (skip on uncertainty) for maximum money-safety, that's a one-line change — flag it.

## Tests (9 new, all green; full poly_kalshi + matcher suite 103 pass)
- `test_mlb_poly_kalshi_match.py`: `game_key_and_side` both-sides-share-key / DH-distinct / non-MLB-None.
- `test_poly_kalshi_executor.py`: opposite-blocked (+ no state consumed), same-side-stacking-allowed,
  **restart-durable**, DH-not-cross-blocked, unparseable-fails-open, journal-row-has-conflict-detail+trigger.
- `git diff --stat`: only the 2 runtime + 2 test files. All 3 byte-locked files byte-unchanged.

## Deploy plan (when ratified — SEPARATE from Item 2)
File-overwrite the 2 runtime files per the prod-live-advance rule (LF-md5 vs box), restart, advance
`prod-live` same session. `mlb_poly_kalshi_match.py` is imported by the shared boot index refresh — the
added function is additive (no signature change), so the boot path is unaffected. Post-deploy: a live
opposite-side signal on a held game logs `skip_conflict` (no order).
