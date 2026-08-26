# PEAD durable rename-defense (Part 3) — STAGED for review 2026-08-26

Branch `pead-rename-defense-2026-08-26` off prod-live `95e78c4`. NOT deployed, NOT restarted.
Base commit `388bfff` syncs robinhood.py to the DEPLOYED `230e7807` (MACE gross-BP snapshot; prod-live
`c86c0e42` was behind) so hook 1 is additive over what's actually running. Code = `da12242`.

## What it does (the ISSC class, auto-healed at the root)
- **Hook 1 — robinhood.py (SHARED, additive over 230e7807):** new `QuoteSymbolUnresolved` sentinel;
  `quote(symbol, *, strict=False)` — when the equity path yields no price AND `strict=True`, it checks
  `get_instruments_by_symbols`; if the symbol resolves to NO instrument (renamed/delisted) it RAISES
  `QuoteSymbolUnresolved` instead of returning 0.0. `strict=False` (default) is byte-for-byte the old
  behavior for every other division. Also two additive helpers: `instrument_id_for(symbol)` and
  `symbol_for_instrument_id(instrument_id)` (identity lookups for hook 3).
- **Hook 2 — pead_strategy.manage() (PEAD-only):** calls `quote(strict=True)` and catches
  `QuoteSymbolUnresolved` -> `_flag_symbol_unresolved` (durable `extra.symbol_unresolved=1`) + a
  `pead_symbol_unresolved` audit event + `continue`. **A not-found/$0 symbol can NEVER derive a phantom
  stop or sell.** This is the root fix for the ISSC bug.
- **Hook 3 — pead_strategy (PEAD-only):** (a) persists `extra.instrument_id` at entry (live entries);
  (b) `_reresolve_unresolved_symbols(broker)` runs at the top of every manage() tick (any window):
  for flagged rows carrying an instrument_id it asks the broker for the CURRENT ticker of that stable
  instrument (RH keeps the instrument across a rename; CUSIP unchanged) and rewrites the ledger
  `symbol` + `name`, clears the flag, emits `pead_symbol_reresolved`. **Auto-heals the next ISSC->IA
  with no manual edit.**

## Tests (box venv, scratch tree; engine + prod DB untouched) — 15 passed
- `test_notfound_symbol_skips_exit_never_sells` — a not-found ticker SKIPS exit eval + flags the row;
  NO exit fired, row NOT closed (proves no phantom stop sell).
- `test_identity_reresolution_auto_rewrites_issc_to_ia` — instrument_id 6a465f9b -> 'IA' rewrites the
  ledger symbol ISSC->IA + name, clears the flag (automated ISSC->IA).
- Regression: the 2 off-hours single-outcome + 11 gate tests still pass. Evidence: pead_test_run_out.txt.

## Shared / cross-module touches (flagged, as requested)
1. **robinhood.py** — SHARED across all divisions. Change is additive; `strict` defaults False so
   bitunix/kalshi/tasty/pmcc/mace/joint quote paths are unchanged. Verified: the only non-additive line
   is the `quote()` signature (backward-compatible kwarg).
2. **tests/test_pead_offhours_single_outcome.py** — TEST-ONLY fixture: `_FakeBroker.quote` now accepts
   `*, strict=False` (required because manage() now passes strict=True). No prod impact.
3. **pead_strategy.py imports `QuoteSymbolUnresolved` from trading_corp.brokers.robinhood** — a new
   strategy->concrete-broker import (minor layering coupling; the alternative was a new shared base.py
   touch, which is worse). Flagged.

## Deploy notes (for when you approve — NOT done here)
- Deploying Part 3 replaces the box's **robinhood.py** (shared) + **pead_strategy.py** and REQUIRES A
  FULL-ENGINE RESTART (both are import-time modules; restart affects ALL divisions). This is a bigger
  blast radius than Parts 1-2 -- review accordingly.
- robinhood.py to deploy = exactly `230e7807` + hook 1 (additive); pead_strategy.py = deployed 9b9cfdad
  + hooks 2-3. Both built on the deployed versions, so a clean md5-verified file swap.
- **Backfill option (optional):** hook 3 stores instrument_id only for FUTURE entries. The current 34
  open rows lack it -> on a future rename they'd be flagged+skipped (safe, protected) but not
  auto-healed (manual fix, like ISSC today). A one-time backfill of instrument_id for the 34 open rows
  would extend auto-heal to them. Recommend as a follow-up, your call.
