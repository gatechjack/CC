# PEAD snapshot-guard — STAGED, NOT DEPLOYED (2026-09-01)

Robustness fix for the shared-RH-auth-outage found in the 2026-08-31 read-only audit:
`pead_strategy.py` `manage()` :646 and `scan()` :426 called `broker.snapshot()` UNGUARDED
(the first RH call in each, above the per-symbol loop). During the 2026-08-31 16:38–17:26Z
shared-session outage the dead session threw straight out of `manage()` at :646 ~10× (every
cadence) until recovery. Blast radius that day was log-noise-only (no crash/half-state/phantom/
missed exit), so this is a robustness fix, not a risk closure.

## The fix (PEAD-ONLY; shared robinhood.py NOT touched)
Wrap each `snapshot()` in `try/except Exception` and, on failure, log ONE line and skip:
- `manage()` :646 → `return [], cadence` (skip the exit tick, resume next cadence) — mirrors the
  existing `window == "closed"` early-return two lines above.
- `scan()` :426 → `return []` (skip the scan, resume next cycle).
Sibling of the Part-3 `QuoteSymbolUnresolved` skip, NOT a literal shared handler: a dead session
fails the WHOLE tick (before the loop) so it RETURNS; `QuoteSymbolUnresolved` skips ONE symbol
(`continue`). The per-symbol `quote()` guard below :646 is UNCHANGED.

## Chain of custody (Gate-A)
- Base (live box) `pead_strategy.py`: sha256 `28eb62be5e58…c505`, git-blob `8153ea25`, 1221 lines.
  Reconfirmed live on the box 2026-09-01 00:07Z and 02:04Z (unchanged; the 21:33Z restart was the
  PM division deploy and did not touch PEAD).
- Patched `pead_strategy.py`: sha256 `909d3f56d5b0…4231`, git-blob `996b3c3`, +15 lines.
- Shared broker `robinhood.py` remains `ecf5457e…` — NOT modified. No new imports added, so the
  import closure is unchanged (Gate-A stays green over the same transitive set).

## Files here
- `pead_strategy.py`            — the PATCHED deploy candidate (full file).
- `pead_strategy.py.boxorig_28eb62be` — the pristine live base (Gate-A reference).
- `pead_authguard.patch`        — the 2-hunk unified diff (repo-relative paths; `patch -p1` / `git apply`).
- `test_pead_snapshot_guard.py` — the guard test suite (also ship to `tests/` on deploy if desired).

## Verification (box venv, pytest 9.0.3, py3.12, ISOLATED tree copy — live tree never touched)
- Gate-A pre-hash matched `28eb62be`; patched→`909d3f56`; `py_compile` OK.
- Diff on-box = EXACTLY the 2 hunks (19 changed lines; no encoding drift).
- NEW guard tests — ALL PASS on the patched tree:
  - `test_manage_dead_snapshot_skips_tick_no_throw_no_place` — throwing `snapshot()` → `manage()`
    returns `([], 300)`, does NOT raise, `quote()` never called (whole tick skipped), nothing
    placed, ledger byte-identical before/after (NO half-state), position still open.
  - `test_manage_quote_unresolved_still_skips_one_symbol` — Part-3 per-symbol skip intact
    (`snapshot()` OK, `quote()` raises `QuoteSymbolUnresolved` → symbol flagged + skipped, no exit,
    no throw).
  - `test_scan_dead_snapshot_returns_empty_no_throw` — throwing `snapshot()` → `scan()` returns `[]`,
    no throw, nothing placed, ledger unchanged.
- REGRESSION (box PEAD suite, same isolated harness):
  - PRISTINE (unpatched): **70 passed, 2 failed**.
  - PATCHED + my 3 tests: **73 passed, 2 failed**.
  - Delta = +3 (my new tests), the SAME 2 failures on both → **ZERO regression from this patch**.
- Half-state guarantee: the guard only prevents the throw; on `snapshot()` failure NO order path is
  reached (it fails before the per-symbol loop and any `_place_or_paper`). Test asserts the ledger
  is byte-identical and no pending/phantom row is created.

## PRE-EXISTING baseline failures (NOT this patch, NOT in scope — flagged)
`tests/test_pead_offhours_single_outcome.py::test_deferred_is_not_terminal_then_exactly_one_exit_at_open`
and `::test_drift_marker_not_consumed_pre_open_fires_once_at_open` FAIL on the pristine deployed
code today (2026-09-01) — date-sensitive drift/deferred-exit timing, unrelated to `snapshot()`.
They fail identically with and without this patch. Worth a separate look; out of scope here.

## DEPLOY (DO NOT DO NOW — held for the next PEAD restart window)
- One engine process runs all SIX divisions sharing one RH session, so activating this needs an
  ALL-DIVISIONS engine restart. BUNDLE with the next scheduled PEAD restart; do not restart solely
  for this robustness fix.
- Method: replace live `pead_strategy.py` gated pre-hash `28eb62be` → post-hash `909d3f56` (or
  `cd /home/azureuser/trading_corp && patch -p1 < pead_authguard.patch`), then Gate-A the import
  closure (unchanged) BEFORE the restart. robinhood.py must NOT change (MACE co-develops it; any
  future shared change Gate-A's against box `ecf5457e`, not prod-live).
- Restart via the canonical `restart_tc.ps1` (az-root `systemctl restart trading-corp`).
- NOT deployed, NOT restarted by this staging session.

## DEPLOYED LIVE 2026-09-01 (with the off-hours test-fixture fix)
Both shipped in ONE az-root all-divisions restart. Base was current box `28eb62be` (unchanged from
staging) -> `909d3f56`; robinhood.py unchanged `ecf5457e`. Engine PID 127578 -> **132470**,
NRestarts=0, 0 tracebacks, all 6 core divisions paper=False, PEAD 33-book intact, guard live in
running code. The 2 previously-red single-outcome tests PASS on the deployed tree (fixture fix =
`test_pead_offhours_single_outcome.fixed.py`, a 1-line `quote(self, symbol, *, strict=False)`).
Backups on box: `pead_strategy.py.bak_authguard_20260901T035943Z`,
`tests/test_pead_offhours_single_outcome.py.bak_fixture_20260901T035943Z`. Full record in
`runbooks/deploy_log.md` (2026-09-01 entry, main-wip @ `8fd95d1`).
