# C staleness-reject gate — test evidence (2026-06-16)

§4 build+test (no deploy). Branch `bitunix-staleness-reject-gate-2026-06-16` off
`b3d1f08`. Run env: local Python 3.14 base (the env that holds the trading_corp
deps + the regression baseline), `PYTHONPATH=<worktree>` so imports resolve to
the worktree (verified: `trading_corp.__file__` → the worktree, no editable
shadow). Brokers/risk/telegram mocked; fundless; no signed/public-API calls.

## Targeted — `tests/test_bitunix_staleness_reject_gate.py`: 13/13 PASS
Covers the operator's required cases:
- `_interval_to_seconds` scales with the bar (3m→180s, 15m→900s, 1D→86400s; fail-open on junk).
- fresh 3m (age 60s) → ALLOWED.
- stale 3m (age 692s, the real frozen-loop case) → REJECTED; `entry_rejected_stale_bar`
  audit emitted (interval=180s, threshold=300s, age≈692s); `bitunix_score_decided`
  row outcome=`skipped_stale_bar`; short-circuits BEFORE snapshot/propose/place.
- interval-aware: 692s is STALE on 3m (thr 300s) but FRESH on 15m (thr 1020s) — NOT a
  fixed constant; 15m rejects past its own larger threshold.
- margin configurable (margin=300 → 3m threshold 480s).
- config OFF → never gates (even a 1e6-second-old bar).
- default observer (no staleness args) → gate OFF (backward-compat / backtest path).
- fail-open on unparseable time/interval.
- EXIT never gated: `_execute_live_exits` with gate ON AND `_staleness_verdict`
  forced-stale still places the reduce_only inverted-side close (exit acts).
- webhook `_REPLAY_WINDOW_SEC` unchanged (== 1200) — anti-replay window untouched.

## Regression — existing observer suites: 80/80 PASS
`test_bitunix_futures_observer.py`, `test_bitunix_observer_execute_live_exits.py`,
`test_bitunix_breaker_abstain_partial_equity.py`, `test_bitunix_drawdown_flatten.py`.

## Full suite — ZERO new regressions (same-env baseline compare; no git stash)
Identical command on this branch vs a fresh clean `b3d1f08` worktree
(`--continue-on-collection-errors`, no stash per the stash-race incident rule):

| | FAILED | ERROR (collection) |
|---|---|---|
| branch (gate) | 61 | 3 |
| clean b3d1f08 | 61 | 3 |

`Compare-Object` of the FAILED+ERROR test-ID sets (path-normalized): **IDENTICAL,
64 entries each → zero new failures introduced.** The 64 pre-existing
failures/errors are credential/network/env-dependent (research_*, polymarket_*,
robinhood_*, tasty_*, kalshi_weather, ex_dividend, iron_condor,
webhooks_return_fast) + 3 collection errors from a pre-existing missing module
`trading_corp.agents.strategies.bitunix_confluence_gate` — all present
identically on clean main, none touched by this change. (NB: this local 3.14
baseline is 64, not the "28" from a prior/other env — the comparison that
matters is branch == same-env clean-base, which holds exactly.)

Change is purely additive (529 insertions, 0 deletions); gate defaults OFF in
`__init__` and ships ON only via `strategies.yaml`. NOT deployed (§4).
