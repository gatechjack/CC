# Reconciliation A — test-suite classification & reconcile (2026-06-28)

**Branch:** `prod-reconcile-2026-06-28` (the A candidate; `main`-runtime == prod).
**Goal (operator):** "dead-feature tests get removed, real regressions get flagged, then show me a
green (or fully-explained) suite and I'll sign off the merge."

## Result

| | before | after |
|---|---|---|
| **Failed** | 52 | **28** (all pre-existing baseline) |
| **Collection errors** | 2 | **0** |
| **Passed** | 2862 | **2726** |
| **New (reconciliation-caused) failures** | 26 | **0** |

**Every new failure is resolved. 0 real regressions found. The 28 remaining failures are the documented
pre-existing baseline, untouched by A and out of scope for this merge.**

## How the 54 failing items reconcile (52F + 2 collection-E)

### Bucket 1 — BASELINE (28F, pre-existing, NOT touched by A) → leave as-is
These live in files A never modified. **Proven pre-existing:** each test file is byte-identical between the
pre-reconcile SFP HEAD (`16f2985`) and the A candidate (`git diff 16f2985 HEAD -- <file>` == empty), so the
failures predate the reconciliation and are unrelated to the merge.

| file | n | nature |
|---|---|---|
| `test_robinhood_multi_leg.py` | 15 | pre-existing (RH multi-leg fixture/env) |
| `test_webhooks_return_fast.py` | 5 | pre-existing (`webhooks.py:785` AttributeError fixture) |
| `test_tasty_options_iron_condor.py` | 3 | pre-existing |
| `test_iron_condor_strategy.py` | 3 | pre-existing |
| `test_paper_run_tooling.py` | 2 | pre-existing (readiness-check fixture) |

### Bucket 2 — DEAD-FEATURE for `main` (24 items: 22F + 2 collection-E) → REMOVED
Tests for undeployed branch-ahead work that A deliberately drops from `main` (prod never had it). **All
preserved in `bitunix-sfp-division-2026-06-25` @ `16f2985`** + the per-feature origin commits — re-integrable
via `git checkout 16f2985 -- <file>` per the inventory. Removing the *test* only; the orphan source files are
left in place (Phase-3 candidates, not pruned here).

| removed test file | n | feature (inventory) | origin |
|---|---|---|---|
| `test_bitunix_gate_inputs.py` | E | Five-factor confluence gate | `2659c81` |
| `test_backtest_bitunix_confluence_five_factor.py` | 2 | Five-factor confluence gate | `2659c81` |
| `test_polymarket_whale_stats_audit_scorer.py` | E | Polymarket whale tooling | `f448c93` |
| `test_refresh_polymarket_whales.py` | 12 | Polymarket whale tooling | `41ca5b9` |
| `test_polymarket_watchlist_seed.py` | 4 | Polymarket whale tooling | `a6d8c30` |
| `test_whale_screening_equivalence.py` | 1 | Polymarket whale tooling | `f448c93` |
| `test_polymarket_broker_config_plumbing.py` | 1 | Polymarket E5a broker-config plumbing | (E-series) |
| `test_earnings_provider.py` | 2 | PEAD earnings adapter | `8307ade` |

*broker_config_plumbing verification:* prod's `config/divisions.yaml` `polymarket_copy_trading` block carries
**no** `order_type`/`fak_poll_seconds` keys → the E5a Division plumbing is genuinely undeployed (test asserts
`order_type == 'fak_synth'`; prod yields `None`). Polymarket is geoblocked/inert on prod regardless.

### Bucket 3 — TEST-LAG on deployed, MORE-CORRECT code (2F) → FIXED (assertions updated)
`tests/test_bitunix_signed_fetch_autobook.py` — the `#1` signed-fetch auto-book feature **is deployed and
live**; these two assertions predate two *later* deployed fixes and asserted the pre-fix shape. **Root-caused,
not guessed.** Not regressions (prod is the more-correct behavior). Fixes preserve each test's original intent.

1. **`test_aggregate_single_fill`** — failed only because the deployed **D3 role-fix** ADDED 5 keys to the
   aggregate (`close_order_ids`, `exit_role`, `fee_implied_role`, `role_fee_mismatch`, `maker_taker_mix`).
   pytest itself reported the 4 original economic keys as *"4 identical items"*. **Fix:** assert the 4 economic
   keys (`vwap_price/total_fee/total_qty/n_fills`) instead of exact-dict equality; the role keys are covered by
   the D3 tests.
2. **`test_real_per_fill_fee_is_summed_not_assumed_rate`** — the deployed **D1 netted-close guard** prorates
   `exit_fee = Σfee × (closed_qty / q_close)`. The test's close fills summed to 0.0008 while the seeded
   position is 0.0007528, so prod booked `0.006 × 0.0007528/0.0008 = 0.005646` — *exactly* prod's value (the
   sibling tests `test_real_book_single_fill`/`_multi_fill_vwap` pass because their fills sum exactly to the
   position qty, ratio 1.0). **Fix:** set the two fills' qtys to `0.0003764` each (sum == position qty) so D1's
   ratio is 1.0, isolating THIS test's concern (real summed fee, not assumed rate → 0.006). D1 proration has
   its own coverage.

## Sign-off posture
- The suite is now **green except the 28 documented baseline failures**, each in an A-untouched file and
  proven pre-existing. This satisfies "green (or fully-explained)."
- **No real regressions** were introduced by A.
- The dead-feature removals lose nothing — every dropped test + its feature is preserved at `16f2985` and the
  cited origin commits (re-integration map: `reports/2026-06-28_reconciliation_A_undeployed_inventory.md`).
- **Phase 2 (merge `prod-reconcile-2026-06-28` → `main`) and Phase 3 (prune) remain HELD for explicit
  sign-off.** This pass only modified the A candidate branch (reversible, non-prod).
