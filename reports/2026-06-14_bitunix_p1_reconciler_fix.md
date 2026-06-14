# P1 fix — Bitunix reconciler false-divergence (symbol + side match)

- **Branch:** `bitunix-p1-reconciler-fix-2026-06-14` (off main `32e7fb4`). **§4-gated: BUILD + TEST only — NOT deployed, NO restart, NO prod write.**
- **Fixes:** BACKLOG P1 ([[bitunix-first-fill-closeout]]) — the reconciler false-fired `position_state_divergence_detected` every ~60s on the bot's OWN live position, latching `_halt_new_orders=True` and blocking new live entries.

## Root causes (both in the bot↔broker `(symbol, side)` match)
1. **Symbol:** bot stores the internal form `BTC/USDT.P`; `get_pending_positions` returns the BitUnix wire form `BTCUSDT` → never equal.
2. **Side:** `get_pending_positions` (and `snapshot`) negated qty only when `side == "SHORT"`. A SELL-opened short is NOT labelled `"SHORT"`, so it read back as **positive** qty → `_broker_side` = `"buy"` ≠ the bot's `"sell"` → the phantom orphan.

Either failure alone makes the reconciler unable to match the bot's own position → false missing + orphan → halt-latch.

## The fix (comparison fixed, check NOT weakened)
- **Symbol** — `bitunix_position_reconciler._match_symbol_key()` normalizes BOTH sides to the wire form via the symbol **SSOT** (`bitunix_symbols.to_wire_format`), applied at all three match sites. Generalizes across every *traded* symbol via the registry (not a hardcoded pair) and avoids the ad-hoc string-slicing that `bitunix_symbols` forbids. Unmapped symbols fall back to the upper-cased raw string → still compare, still surface as genuine divergence, never crash.
- **Side** — new shared `bitunix._signed_position_qty(side, qty)` used by BOTH `snapshot()` and `get_pending_positions()`. Negates for `{SELL, SHORT}`, positive for `{BUY, LONG}`, case-insensitive (`abs()` makes it idempotent). An **unrecognized** non-empty label is logged LOUDLY and left positive — **fail-loud, never silently mis-signed** (so a third convention surfaces instead of silently re-creating the divergence).
- Genuine divergence **preserved**: a real orphan, a real missing, a real side-flip, and an orphan in an unmapped symbol all still fire AND still latch the halt (tested).

## ⚠️ Residual grounding item — confirm at deploy
The exact BitUnix position-side label for a short is **`"SELL"` by strong inference**, NOT a captured exact string:
- **Grounded from captured data:** orders are placed with `side="BUY"/"SELL"`; the live short (2026-06-14) read back with *positive* qty under the old check ⇒ the label is **NOT `"SHORT"`**.
- The raw position payload is not logged at INFO and the position is now closed, so the exact string could not be captured read-only (a signed probe is outside the read-only policy).
- **Mitigation:** the fix handles `SELL` + `SHORT` + `BUY` + `LONG` case-insensitively and **fail-louds on anything else**, so it is safe whichever of these BitUnix uses. **Operator: confirm the exact `side` value from the BitUnix position payload at the (operator-gated) deploy** — if it is `"SELL"`/`"BUY"`, no change needed; if it is a third convention, the warning will surface it and the label sets get one line extended.

## Tests
- New `tests/test_bitunix_reconciler_symbol_side_match.py` (14 tests): parse-layer SELL→negative / BUY→positive / case-insensitive / legacy SHORT-LONG / unknown→warn; symbol normalization (internal↔wire collapse, unmapped fallback); **end-to-end regression** — bot `BTC/USDT.P`/`sell` vs broker `BTCUSDT`/`SELL` → ONE match, NO divergence, **NO halt latch**; genuine missing / orphan / side-flip / unmapped-orphan all still fire + halt.
- Existing tests for the changed modules: 59 pass (back-compat: `"SHORT"`→negative, `"LONG"`→positive unchanged).
- **Full same-env gate vs baseline (main `32e7fb4` code): zero new regressions** — identical 31 pre-existing FAILED/ERROR (the known `bitunix_confluence_gate` collection errors + unrelated robinhood/iron-condor/`webhooks_return_fast` fixture-gap / `paper_run_tooling` db-dep), diff of failure sets = NONE.

## Resume-to-live (NOT done here — operator-gated)
Per the close-out: **resuming live = deploy THIS fix THEN restart — NOT a restart alone.** A bare restart clears the halt but the next live fill re-triggers the same false divergence and re-latches. Deploy + restart are separate operator steps.

## Disclosure (82fda13)
Agent: local source review, code edits, local pytest (build+test), local git commit + push of the branch. Read-only SSH used only to confirm the engine state (no restart) and to attempt grounding the side label (journal had no raw payload). **No deploy, no restart, no prod write, no signed/public-API call.** Branch pushed, UNMERGED — for operator review.
