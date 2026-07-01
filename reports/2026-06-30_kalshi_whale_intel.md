# Kalshi Whale Copy-Intelligence Dashboard — Build Report

**Date:** 2026-06-30
**Branch:** `kalshi-k5-dashboard-2026-06-30`
**Commit prefix:** `dashboard(kalshi):`
**Files touched:** `trading_corp/web/data.py`, `trading_corp/web/routes.py`,
`trading_corp/web/templates/partials/pm_dashboard_body.html`,
`tests/test_kalshi_whale_intel.py`

---

## What was built

Per-whale copy-quality intel ("Copy Intel") panels were added to both the
Selected Whales panel and the Kalshi Watch List panel on the prediction-markets
dashboard. These surface 8–10 per-whale metrics derived entirely from existing
data in `audit_event` and `kalshi_round_trips` — no new instrumentation, no
schema changes.

---

## Motivating examples

| Whale | Observation |
|-------|-------------|
| `the.hoff.85` | 733 copies, −$31.60 net-of-fee P&L (net-losing, visible at a glance) |
| `lengthy.starfish` | 1,845 no_side skips vs 4 copies → copyability ≈ 0.2% (structurally uncopyable) |

---

## Data sources

All metrics are computed from existing DB tables with SELECT-only queries in
`_query_kalshi_whale_intel()` (new function, ~175 lines including fee model and
all four SQL statements).

| Metric | Source |
|--------|--------|
| Copies | `audit_event` WHERE `actor='kalshi_copy_trader' AND kind='would_have_placed' AND side='buy'` grouped by `payload_json.whale_handle` |
| No-side skips | `audit_event` WHERE `kind='kalshi_copy_entry_skipped_no_side'`, COALESCE(`whale_handle`, `whale`) |
| Sports skips | `audit_event` WHERE `kind='kalshi_copy_entry_skipped_sports'`, COALESCE(`whale`, `whale_handle`) |
| Detections | copies + no_side + sports (derived) |
| Copyability% | 100 × copies / detections (derived, NULL when detections=0) |
| Net PnL | `kalshi_round_trips` WHERE `division='kalshi_copy_trading' AND whale_handle IS NOT NULL`, with fee + slip applied in Python |
| Hit rate | wins / n_resolved from the same rows |
| Days since last copy | (NOW − MAX(ts)) / 86400 from copies audit rows |
| Crypto% | ticker prefix KXBTC/KXETH/KXSOL/KXDOGE/KXXRP/KXBNB/KXHYPE |

---

## Fee model (mirrors kanalysis.py 2026-06-21)

```
fee = ceil(0.07 × C × P × (1−P)) per traded side
slip = $0.01 / contract per traded side
```

Entry: fee + slip always counted.
Exit: fee + slip counted only when `0 < exit_price < 1` (pre-resolution exit).
Settled (exit_price NULL / 0 / 1): exit fee+slip = $0.

---

## Changes summary

### `trading_corp/web/data.py`

1. **`PMWhaleRow`** — added 8 intel fields with `= 0 / = None` defaults after
   `is_pinned` (Kalshi rows only; Polymarket rows stay at defaults):
   `intel_copies`, `intel_detections`, `intel_no_side`, `intel_sports`,
   `intel_copyability_pct`, `intel_net_pnl`, `intel_days_since_last_copy`,
   `intel_crypto_pct`.

2. **`KalshiWatchOnlyRow`** — added 10 intel fields after `last_refresh_iso`
   (same 8 as PMWhaleRow + `intel_n_resolved`, `intel_hit_rate_pct`).

3. **`PMDashboardView`** — added `kalshi_hide_uncopyable: bool = False` and
   `kalshi_hide_net_neg: bool = False` filter-state fields.

4. **`_KALSHI_WATCH_SORT_KEYS`** — extended with 12 new sort-key aliases covering
   all 10 intel fields.

5. **`_query_kalshi_whale_intel()`** — new function. Four SQL queries; fee+slip
   applied in Python. Returns `{handle: {copies, detections, …}}`. READ-ONLY.

6. **`_query_kalshi_watch_only_rows()`** — added `hide_uncopyable` and
   `hide_net_neg` params; intel merge before sort (so intel-keyed sorts work);
   filter after sort (hide_uncopyable: copyability < 5% with detections > 0;
   hide_net_neg: net_pnl < 0 at n_resolved ≥ 30).

7. **`_query_pm_whales()`** — added intel merge for Kalshi selected-whale rows
   (8 intel fields, skips Polymarket rows).

8. **`build_prediction_market_view()`** — added `kalshi_hide_uncopyable` and
   `kalshi_hide_net_neg` params; threaded to `_query_kalshi_watch_only_rows` and
   returned `PMDashboardView`.

### `trading_corp/web/routes.py`

All 4 route handlers (`prediction_markets_all`, `prediction_markets_one`,
`prediction_markets_partial_all`, `prediction_markets_partial_one`) and both
render helpers (`_render_pm_dashboard`, `_render_pm_partial`) updated with:
- `kalshi_hide_uncopyable: int = 0` / `kalshi_hide_net_neg: int = 0` query params
- Converted to `bool()` and threaded through to `build_prediction_market_view`

### `trading_corp/web/templates/partials/pm_dashboard_body.html`

1. **`kalshi_watch_sort_link` macro** — updated to preserve filter state in every
   sort URL (`&kalshi_hide_uncopyable=0/1&kalshi_hide_net_neg=0/1`).

2. **Filter toggle links** — added "Hide uncopyable" and "Hide net-neg" HTMX
   toggle links in the Kalshi Watch List panel header. Active state shown with
   `bg-pane-2/60` highlight. Each toggle preserves the current sort state via
   `sort_qs` variable.

3. **Kalshi Watch List table** — 10 new sortable columns added between "Last
   Refresh" and "Action": Copies, Detected, No-side, Sports, Copy%, Net PnL,
   Res (us), HR%, Days ago, Crypto%.

4. **Selected Whales table** — 8 new static columns added between "Last entry"
   and "Action": Copies, Detected, No-side, Sports, Copy%, Net PnL, Days ago,
   Crypto%. Polymarket rows show `—` in all intel cells.

---

## Test file

`tests/test_kalshi_whale_intel.py` — 23 tests covering:

- Empty handles → empty dict
- Unknown handle → all defaults (zeros/None)
- Copies counted from `would_have_placed` (side=buy only)
- Sell-side and wrong-actor entries excluded
- No-side skips via `whale_handle` primary and `whale` fallback
- Sports skips via `whale` primary and `whale_handle` fallback
- `lengthy.starfish` case: 4 copies / 1845 no_side → copyability < 5%
- Copyability = None when no detections; 100% when all-copies
- Settled exit fee model (entry fee + 1-side slip only)
- Pre-resolution exit fee model (entry + exit fee + 2-side slip)
- exit_price=0 treated as settled
- Multiple trade accumulation and hit rate
- `the.hoff.85` net-negative case
- Crypto% classification (BTC/ETH/SOL vs non-crypto)
- All-crypto and all-non-crypto extremes
- Multiple whales isolated from each other
- Wrong division excluded
- Missing `whale_handle` in extra_json excluded

All 23 pass. Zero regressions in existing Kalshi test suite (56 tests).

---

## Guardrails compliance

- READ-ONLY queries only: no INSERT/UPDATE/DELETE in `_query_kalshi_whale_intel`
- No schema changes: no new tables, columns, or indices
- No new audit event instrumentation: metrics derived from existing event kinds
- Broker branch (`kalshi-k5-golive-2026-06-30`) untouched
- `brokers/`, `main.py`, strategy files untouched
- No deploy/merge performed
