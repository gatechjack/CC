# S2 BUNDLE (a + b + c) — DEPLOY RUNBOOK — ✅ DEPLOYED + VERIFIED LIVE 2026-07-27 ~02:33 UTC

**Status:** **DEPLOYED** to prod 2026-07-27 ~02:33 UTC (PID 404132→424692); drift-gate PASS, all 4 verify checks PASS, backfill 15/15 applied. Autopause now **functional on Kalshi but STAYS SHADOW** (NOT flipped — separate operator decision). Full record in `runbooks/deploy_log.md` (2026-07-27 entry). Runbook retained as the executed procedure.
**Scope reversed 2026-07-26:** deploy all three S2 fixes as ONE bundle (they share a restart). Rationale: the per-whale copy-**profitability** analysis that gates the roster re-selection decision needs all three metrics trustworthy — (a) live copies counted, (b) autopause not blind, (c) panel epoch-scoped — so (a)+(c) are prerequisites, not polish.

## What ships (all committed to the branch; NOT applied to prod)
| fix | file | change | layer |
|---|---|---|---|
| **(a)** copyability counts live | `trading_corp/web/data.py` `_query_kalshi_whale_intel` (copies query) | `kind IN ('would_have_placed','kalshi_copy_placed_live')` (no_fill excluded) | web |
| **(b)** whale_handle → extra_json | `trading_corp/agents/kalshi_resolver.py` `_compute_round_trip_row` | `"whale_handle": row.get("whale_handle")` in extra_json | **core** |
| **(b)** backfill | `scripts/backfill_s2b_kalshi_copy_whale_handle.py` | one-time; ~15 pre-fix live rows; dry-run default | script |
| **(c)** panel epoch-scope | `trading_corp/web/data.py` `_query_pm_whales` (+ call site) | thread `kalshi_copy_mode/epoch`, apply `_kalshi_copy_mode_clause` to round_trips (entry_ts) + opens (ts) | web |

Syntax-checked (`ast.parse` OK on all three files). Autopause code untouched — it already keys on `extra_json.$.whale_handle`; fix (b) is what feeds it.

## Tests to apply/run at build (NOT yet edited — keeps suite green pre-build)
- `tests/test_kalshi_resolver.py::test_resolve_books_live_copy_placed_live_row`: add `"whale_handle": "testwhale"` to the `live-win` payload; add `extra_json` to the SELECT; assert `"testwhale" in win["extra_json"]`.
- `tests/test_kalshi_whale_intel.py`:
  - fix (a): `test_copies_counted_from_would_have_placed` — also seed a `kalshi_copy_placed_live` BUY row and assert it counts toward `copies`; keep the sell-side exclusion test valid across both kinds.
  - fix (c): add `test_pm_whales_epoch_scopes_kalshi_round_trips` — seed round-trips across the epoch boundary; assert `mode='live'` sees only post-epoch rows (mirrors the tile test).
- Run full `tests/test_kalshi_resolver.py` + `tests/test_kalshi_whale_intel.py` (+ related) before deploy.

## Deploy sequence (operator-gated, ONE restart)
1. **Pre-check:** quiet window (copy idle / no PMCC burst); prod-reconcile path clean; run the test suite (above) green.
2. **Ship** `web/data.py` (a+c) and `agents/kalshi_resolver.py` (b) via the established deploy mechanism; back up (`.bak_s2_abc_<date>`).
3. **Restart** the engine (core change in b → restart; a+c are web-layer but bounce with the restart). Confirm `NRestarts` clean, 0 tracebacks, `auto_execute`/`--live-divisions` unchanged, PID bump.
4. **Backfill dry-run:** `python3 scripts/backfill_s2b_kalshi_copy_whale_handle.py` → verify ~15 rows w/ correct parsed handles.
5. **Backfill apply:** `... --apply` → "APPLIED — N rows updated."
6. **Verify (all four):**
   - (b) new settlement round-trip has `extra_json.$.whale_handle` populated.
   - (b-backfill) `SELECT count(*) FROM kalshi_round_trips WHERE division='kalshi_copy_trading' AND entry_ts>='2026-07-01' AND json_extract(extra_json,'$.whale_handle') IS NOT NULL` = 15 (was 0).
   - (a) dashboard Kalshi per-whale copyability now advances past go-live (live copies counted) — spot-check a whale with live `placed_live` events shows copies > its paper count.
   - (c) the per-whale panel in **Live** mode matches the tile's epoch-scoped totals (panel `n`/pnl summed = tile `n_resolved`/`total_realized_pnl` for the live slice).

## Rollback
- Code: restore `.bak_s2_abc_<date>` (both files) + restart. (a)+(c) are pure read-query changes; (b) is additive to extra_json.
- Backfill is additive (sets a previously-null field); no functional need to reverse.

## Post-deploy
- **Autopause stays SHADOW** until (b) is verified populating handles live AND the operator explicitly flips it. This bundle makes Kalshi autopause *functional*; flipping shadow→active is a separate decision.
- ⚠️ Metrics become *trustworthy*, not *rich*: the live sample is tiny (15 RT) and epoch-scoped forward window thin — the profitability analysis (next step) will be sample-limited until more accrues.
- **Next (post-deploy, separate):** per-whale copy-**profitability** analysis (net-of-fee P&L + hit rate + sample + copyability + recency) → the copyable∩profitable∩recent intersection = candidate roster. **Copyability alone must not drive roster changes** (e.g. the.hoff.85 is 99.4% copyable but was net −$31.60/733 RT in the 2026-06-21 analysis — readable ≠ profitable). Persistence (year-round activity) remains unresolved (3-month window).
