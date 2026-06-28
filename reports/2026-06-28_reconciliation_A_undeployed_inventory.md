# Reconciliation A (prod-snapshot) — candidate + UNDEPLOYED-WORK INVENTORY (2026-06-28)

**Strategy chosen: A — bless prod as source-of-truth.** `main` will == prod's deployed code, so deploys
become clean diffs. This doc is the deliberate re-integration map for everything A drops from `main`.
**Phase 2 (merge to main) and Phase 3 (prune) are HELD for explicit sign-off — not done.**

## A candidate
- Branch **`prod-reconcile-2026-06-28` @ `3f60f9d`** = the SFP base (`16f2985`) with prod's 39 deployed
  files overlaid → `main`-runtime == prod (content) for every deployed file.
- **Config snapshotted VERBATIM from prod** (confirmed md5-equal): `strategies.yaml` `0cd6e45d`,
  `divisions.yaml` `6dcbe16f`, `risk.yaml` `02874fb2` (tuned values + `mode:`/`execution_mode:live` keys;
  no secrets — those live separately).
- Only non-byte-exact file: `_observer_test.py` (a test file, CRLF on prod vs repo-LF — content-identical;
  not a deployed runtime file).

## ★ THE SAFETY NET
**Nothing below is lost — the full branch state is preserved in `bitunix-sfp-division-2026-06-25` @
`16f2985` (the pre-reconcile HEAD)**, plus the per-feature originating commits cited. Re-integration =
`git checkout 16f2985 -- <file>` (or cherry-pick the origin commit) onto a future branch, then deploy
properly. **Phase 3 pruning must NEVER remove `16f2985`/the SFP branch or any research branch that is the
sole home of these.**

## UNDEPLOYED WORK A DROPS FROM `main` (re-integrable)

### Feature 1 — Five-factor confluence backtest gate (origin `2659c81`)
Backtest-only tooling for the bitunix confluence gate; NOT in the live order path.
| file | state in A | what's dropped |
|---|---|---|
| `trading_corp/data/bitunix_price_context.py` | reverted to prod | `build_gate_inputs`, `cvd_from_bars_tick_rule`, `prior_day_session_vwap`, `_atr_series_from_bars`, `_slope`, `_snap`, `log_gate_cache_warmup_status` |
| `trading_corp/agents/strategies/bitunix_confluence_gate.py` | retained (branch-only) | whole module (`ConfluenceGateConfig`, `GateDecision`, gate eval) — now orphaned (its `price_context` helpers reverted) |
| `trading_corp/agents/strategies/_ta_helpers.py` | retained (branch-only) | TA helpers (transitive dep) |
Re-integrate: restore all three from `16f2985` together (they're one feature). Tests: `test_bitunix_gate_inputs.py`, `test_backtest_bitunix_confluence_five_factor.py`.

### Feature 2 — Polymarket whale selection/scoring/screening (origins `f448c93`, `41ca5b9`, `a6d8c30`)
Whale roster tooling: realized-ROI scoring, window-truncation gating, shared /activity-fetch.
| file | state in A | what's dropped |
|---|---|---|
| `trading_corp/data/polymarket_whale_stats.py` | reverted to prod | `score_whale_from_audit` (+ behavioral: audit-report fields/gates) |
| `trading_corp/scripts/refresh_polymarket_whales.py` | reverted to prod | `_select_rule_b`, `_print_dry_run_diff`, `_print_gated_out`, `_print_unrankable` |
| `trading_corp/scripts/seed_polymarket_watchlist_deep.py` | reverted to prod | `_record_termination`, `_rows_for_window` |
| `trading_corp/data/polymarket_whale_audit.py`, `..._audit_cache.py` | reverted to prod | behavioral (content differs; no new symbols) |
| `trading_corp/data/whale_screening.py` | retained (branch-only) | shared activity-fetch loop — orphaned (its `whale_stats`/`refresh`/`seed` callers reverted) |
Re-integrate: restore the whale set from `16f2985` together (interconnected). Tests: `test_polymarket_whale_stats_audit_scorer.py`, `test_refresh_polymarket_whales.py`, `test_polymarket_watchlist_seed.py`, `test_whale_screening_equivalence.py`, `test_polymarket_broker_config_plumbing.py`.

### Feature 3 — PEAD earnings adapter + backtest engine (origins `8307ade`, `c6d1a9f`, `759dc03`)
NOTE: the PEAD *division* (`robinhood_pead.py`, `pead_strategy.py`) IS deployed on prod (kept in A). What's
branch-ahead is the earnings *adapter methods* + the backtest tooling.
| file | state in A | what's dropped |
|---|---|---|
| `trading_corp/data/market_data_provider.py` | reverted to prod | `get_quarterly_eps`, `get_recent_announcements` (earnings ABC methods) |
| `trading_corp/agents/strategies/pead_backtest.py` | retained (branch-only) | pure backtest engine |
| `trading_corp/agents/strategies/pead_backtest_driver.py` | retained (branch-only) | backtest driver + CLI |
Re-integrate: restore `market_data_provider.py` from `16f2985` (keeps the adapter methods); the backtest files are already retained. Tests: `test_earnings_provider.py`, `pead_backtest*`.

## Residual / follow-ups (NOT blockers for A)
- **Test-reconciliation pass:** with A's prod code + the SFP test suite, the suite shows **66F (38 new)** —
  the new failures are these dropped features' tests + behavioral test-lag where prod's deployed engine
  (D3/ref-vs-fill/etc.) outran the SFP branch's tests. After merging A, bring the test suite in line with
  prod's code (update/skip lagging tests). Tracked separately.
- The `__init__.py` "prod-only" set (10) were empty-file false-positives (identical) — no action.
- The 5 retained branch-only files above are **orphaned** in A (their sister files reverted) → harmless
  (unimported) but should be re-integrated *with* their feature, not alone.

## Phase 2 / Phase 3 (HELD for sign-off)
- **Phase 2 — merge `prod-reconcile-2026-06-28` → `main`.** Operator-gated.
- **Phase 3 — prune 142 branches / 86 worktrees.** Candidate list first, never blind. **Protected (sole home
  of dropped work): `bitunix-sfp-division-2026-06-25` (`16f2985`) + the five-factor/whale/PEAD research
  branches.** Nothing that is the only home of undeployed tooling or research gets pruned.
