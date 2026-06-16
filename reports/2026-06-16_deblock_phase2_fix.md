# A / Phase 2 — fix: REMOVE the polymarket RiskAgent audit_event-scan caps

§4 build+test, NO deploy. Branch `bitunix-deblock-eventloop-2026-06-16`. Bundles with C.
**STOP for operator review.**

## What changed (and why "remove", not "optimize")

Phase 1c proved the H3 freeze: `RiskAgent` full-scanned the 1.19M-row `audit_event`
synchronously on the event-loop thread, per emitted polymarket order, inside
`_evaluate_polymarket()`. **Operator decision:** prediction-market group caps aren't needed
(small bets, spread across high volume — not the stocks/futures concentration profile the
cap machinery exists for), so the work is **removed**, not indexed/offloaded. Removing the
work is strictly better than making the work fast.

`agents/risk.py` (shared layer):
- `_evaluate_polymarket()` — deleted the `if db_url:` **aggregate-cap block** (daily-spend,
  total-open-notional, max-open-COUNT). **No `audit_event` scan remains in the
  order-emission path.** The **atomic** caps stay: implied-probability bounds, per-position
  % of division equity, single-market notional (all O(1), no DB). `db_url` kept for
  signature stability (unused).
- Deleted the 3 now-dead scan helpers: `_sum_polymarket_today`, `_polymarket_open_positions`,
  `_sum_polymarket_open` (only the removed block called them).

`tests/test_polymarket_arbitrage.py` — removed the 5 tests of the deleted helpers/caps;
added `test_aggregate_caps_removed_no_audit_scan` (asserts the helpers are gone + an order
that would have tripped the old max-open/aggregate caps now approves → no scan).

## PRESERVED — the hard line (verified)

ALL global + per-account risk controls in `evaluate()` are **untouched**: strategy/account
halts, the side-flip backstop, per-strategy daily-loss cap, and — critically — the
**per-account max-drawdown breaker + flatten** (step 4, `per_account_max_drawdown_pct`; the
bitunix DD-cap 0.99 path). Kalshi was already routed to the generic path (excluded from
`_evaluate_polymarket`). Stocks/futures/bitunix risk management is unchanged.

## Tests (local 3.14 env, `PYTHONPATH=worktree`)

- **Full suite vs clean `b3d1f08`: IDENTICAL 61 FAILED + 3 ERROR sets** (`Compare-Object`
  empty) → **ZERO new regressions**. The 5 atomic-cap-reject failures
  (`test_polymarket_reject_*`) are **pre-existing** — the `risk_agent` fixture's yaml lacks
  a `polymarket:` section in this env (`poly_cfg` empty → returns None before the atomic
  caps), present identically in the baseline; not touched by this change.
- **Preserved controls + new test: 31/31 pass** — `test_risk_gates.py` (DD/flatten, halts,
  daily-loss, per-trade), `test_bitunix_drawdown_flatten.py`,
  `test_bitunix_breaker_abstain_partial_equity.py`, and the new no-scan test.

## SECONDARY scan — FLAGGED, NOT touched (operator decision)

`polymarket_arbitrage._count_open_entries_by_condition_id` (:69 / called :291) is another
`audit_event` `json_extract` scan, BUT: (a) it runs **once per cycle** (not per-order) — a
minor freeze contributor vs the per-order `risk.py` scans now removed; (b) it's a
**Board-approved trading dedup cap** (`max_open_per_condition_id`, "prevent stacking",
2026-05-21) living in the polymarket **division** module — removing it changes trading
behavior and collides with the "don't touch division trading branches" stop. Left intact;
operator to decide: leave (minor) / make cheap (index) / remove the dedup.

## Bundle-compat with C
Disjoint files — A touches `agents/risk.py` (+ this report + the arb test); C touches the
bitunix observer / `main.py` / `strategies.yaml`. No overlap → they compose for the one
deploy window.
