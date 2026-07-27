# S2 fix (b) — whale_handle → extra_json + backfill — DEPLOY RUNBOOK (STAGED, NOT DEPLOYED)

**Status:** PACKAGE STAGED on `claude-2026-07-26`. **Awaiting operator deploy-go — do NOT deploy without it.**
**Why standalone (split from S2 a/c):** a live copy division must not run with a blind autopause. This closes a real safety gap — autopause has been blind to **all** market-settlement rows since go-live (it keys on `extra_json.$.whale_handle`, which the settlement path never wrote). Fixes (a) copyability-counts-live and (c) panel-epoch-scope are deferred (metrics-display only).

## What ships
1. **Code (core, committed):** `trading_corp/agents/kalshi_resolver.py` · `_compute_round_trip_row` — one line added to the `extra_json` dict: `"whale_handle": row.get("whale_handle")`. Safe for all actors (None for non-copy). Makes settlement-path round-trips visible to autopause **going forward**.
2. **Backfill (committed):** `scripts/backfill_s2b_kalshi_copy_whale_handle.py` — one-time, parses the handle from `extra_json.rationale` for the ~15 pre-fix live rows. Dry-run by default; `--apply` to commit. Idempotent.
3. **Test (apply at build — spec below, NOT yet edited to keep suite green pre-build):**
   In `tests/test_kalshi_resolver.py::test_resolve_books_live_copy_placed_live_row`:
   - add `"whale_handle": "testwhale",` to the `live-win` `_insert_audit_event` payload;
   - add `extra_json` to the `SELECT` column list;
   - after the `win["strategy"]` assertion add: `assert "testwhale" in win["extra_json"]`.
   Also add a positive-path assertion in `test_compute_carries_kalshi_specific_fields` if desired.

## Deploy sequence (operator-gated)
1. **Pre-check:** confirm quiet window (copy idle / no PMCC burst); `git` clean on prod-reconcile path.
2. **Ship the resolver change** via the established prod deploy mechanism (prod-direct patch or git deploy), preserving backups (`.bak_s2b_<date>`).
3. **Restart** the engine (core change → requires restart). Confirm `NRestarts` clean, `0 tracebacks`, `auto_execute`/live-divisions unchanged, PID bump.
4. **Run backfill dry-run** on prod: `python3 scripts/backfill_s2b_kalshi_copy_whale_handle.py` → verify it lists ~15 rows with correct parsed handles.
5. **Apply backfill:** `... --apply` → confirm "APPLIED — N rows updated."
6. **Verify (all three):**
   - New settlement round-trip (post-restart) has `json_extract(extra_json,'$.whale_handle')` populated.
   - Backfill: `SELECT count(*) FROM kalshi_round_trips WHERE division='kalshi_copy_trading' AND entry_ts>='2026-07-01' AND json_extract(extra_json,'$.whale_handle') IS NOT NULL` = 15 (was 0).
   - Autopause query now returns per-whale rows for live history (previously 0): `SELECT json_extract(extra_json,'$.whale_handle') h, count(*) FROM kalshi_round_trips WHERE division='kalshi_copy_trading' AND entry_ts>='2026-07-01' GROUP BY h`.

## Rollback
- Code: restore `.bak_s2b_<date>` resolver + restart.
- Backfill is additive (only sets a previously-null field); to reverse, null the field on the affected ids (the script prints them) — but there is no functional need to (the handle is correct).

## Post-deploy note
- **Autopause stays in SHADOW** until (b) is verified populating handles live AND the operator explicitly flips it. Fix (b) makes Kalshi autopause *functional*; flipping shadow→active is a separate decision.
- Fixes (a)/(c) remain deferred pending the roster re-selection decision.
- ⚠️ Autopause visibility ≠ autopause *usefulness*: it will now see live rows, but the live sample is tiny (15 RT) and the epoch-scoped forward window is thin — expect it to have little to act on until a broader sample accrues.
