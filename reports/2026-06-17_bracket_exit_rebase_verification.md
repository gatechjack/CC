# Exit-redesign rebase + verification — 2026-06-17 (PREPARE-only, pre-review)

## Rebase
- Original (untouched): `bitunix-bracket-exit-build-2026-06-16` = `d32e8ad`, the **superset** (bracket stacked on fillreg `4a795ee`; 2 commits).
- Rebased onto live base `a64a42f` (= current prod: staleness gate in observer, deblock-A in risk, polymarket-superset main/db) on a **fresh worktree** (`cc-bracket-rebase-wt`), new branch `bitunix-bracket-exit-rebased-2026-06-17`.
- **Rebased SHA = `b077b66`.** Original branch + its worktree untouched. No `git stash` used.

## Conflict resolution
- **None required.** Git's 3-way merge applied both commits cleanly (EXIT 0, zero conflicts) — the staleness-gate region and the exit-guard/bracket entry-path edits in `observer.py` are non-overlapping. Clean merge was **verified semantically** (below), not merely trusted.

## All-three-intact proof
1. **Staleness gate (freeze mitigation):** `entry_rejected_stale_bar` present at `observer.py:1446` on the rebased tree; `tests/test_bitunix_staleness_reject_gate.py` **passes on the rebased observer** (part of 40/40 targeted pass). This is the specific near-regression — proven intact.
2. **#5-B/#5-C exit-guard exemptions:** `data_exec.py:180-192` — `_is_exit = reduce_only`; freshness re-check gated `if not _is_exit` (entries gated, exits exempt). `brokers/bitunix.py:1053-1060` — `if self._halt_new_orders and not reduce_only` (halt blocks entries only).
3. **Bracket placement:** wired at `observer.py:3237`, `if _registered:`, fail-soft (try/except; B1 SL still protects on TP-leg failure).

## Deploy file set (authored vs live base a64a42f) — TARGETED overwrites only
- `trading_corp/agents/data_exec.py` (M)
- `trading_corp/agents/divisions/bitunix_bracket.py` (NEW)
- `trading_corp/agents/divisions/bitunix_futures_observer.py` (M)
- `trading_corp/agents/divisions/bitunix_position_reconciler.py` (M)
- `trading_corp/agents/logger.py` (M)
- `trading_corp/brokers/bitunix.py` (M)
- **NOT** `main.py`, **NOT** `db.py` (cutover's shared files — untouched). **No `strategies.yaml` change** (bracket has no yaml flag — see below).

## Tests
- Targeted: **40/40 pass** (staleness gate + fillreg-exitguard + bracket logic + bracket integration).
- Full suite: **zero new code regressions.** Baseline (a64a42f) = 26 pre-existing failures (iron_condor / robinhood_multi_leg / tasty_options / webhooks_return_fast — unrelated divisions). Rebased delta = 2 `test_paper_run_tooling` readiness tests, **proven to be the empty-DB fresh-worktree artifact** (default db_url `sqlite:///data/trading_corp.db` hits the worktree's auto-created empty DB; rebased code + populated DB → 11/11 pass). 3 collection errors (`bitunix_confluence_gate` module absent) are pre-existing on baseline too.

## Flags for operator review
- **Bracket ships ON, no kill-switch.** `_place_bracket_exits` is called unconditionally on every registered live entry; only "disable" path is rollback. Fail-soft (B1 SL always protects) bounds the risk.

## DD-cap PRE-VALIDATION CHECK — CLEARED (validation is DD-safe)
- The effective bitunix cap is **NOT** the 15% default. `config/risk.yaml` (the file `risk.py:48` reads — NOT `strategies.yaml`) carries a live per-strategy override: `overrides → bitunix_futures → per_account_max_drawdown_pct: 0.99` (risk.yaml:75-76). Applied to prod **2026-06-15 23:05:08 UTC** (mtime + backup `risk.yaml.bak.20260615T230508Z` confirm); hot-reloaded. `risk.py:71-76` `_params()` merges `{**global, **override}` → **bitunix `max_dd = 0.99`**.
- Static DD = (peak 329.77 − equity 265.53)/329.77 = **19.48%**, well under the 99% cap → breaker does **not** trip. This explains the observed live behavior (bitunix scoring normally, no halt).
- A ~$79 stop-out is nowhere near 99% account DD. **Validation is DD-safe.**
- ⚠ Correction: an earlier delegated analysis concluded "AT-RISK / already tripped" by checking only `strategies.yaml` + the `risk.py` *default* (0.15) and missing `config/risk.yaml`, the actual cap source. Verified against the live file here.
- Note: 0.99 is a TEMP low-balance testing override; reverts to 15% global when the account is funded ~$15k (separate operator decision; not a blocker now).
