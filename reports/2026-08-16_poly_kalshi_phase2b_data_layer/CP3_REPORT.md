# Phase 2b CP3 report — broker-free dashboard read + HTMX live refresh + copy-moment feed

**Status: BUILT, NOT DEPLOYED. Checkpoint STOP.** Phase 2b data layer (CP1+CP2+CP3) is complete.
Branch `poly-kalshi-phase2b-cp3-2026-08-16` (off CP2); `data.py`/`routes.py`/templates matched
`origin/prod-live` at branch creation (tracing live code).

## Live-money / live-loop status (lead)
- **Zero live activity.** No order placed, no prod mutation, no restart. Branch-only; the running
  engine (PID 756639) is untouched.
- **Shared files byte-unchanged** — empty diff vs `origin/prod-live` on the 3 shared files.

## What CP3 delivers (all additive, all broker-free)
The live poly_kalshi data is now surfaced on the EXISTING prediction-markets dashboard (functional,
not the fancy UI — that's the later Claude Design step binding to this same contract). Every read is
**SELECT-only over the audit rows + the CP2 volatile tables** — the dashboard NEVER calls the Kalshi
client.

1. **Broker-free live view** — `data.build_poly_kalshi_live_view(db_url)` (`data.py:6041`) + 3
   dataclasses (`PolyKalshiLivePosition` / `PolyKalshiCopyMoment` / `PolyKalshiLiveView`, `:6032`) +
   a `_sparkline_text` helper (`:5982`). Per open position it joins:
   - CP3-original fields (order_id/ticker/outcome/entry_ts/fill_price/contracts/cost_basis/whale) from the audit row;
   - **CP1 trigger** (poly_slug/outcome/side/market_type) — LEFT-JOIN semantics: `None` on pre-CP1 rows;
   - **CP2 marks** (yes_mid/unrealized/unrealized_pct/mark_ts + `stale`) from `poly_kalshi_mark_live` — `None`/`stale` until the poller marks;
   - **CP2 sparkline** (`sparkline` raw series + a unicode `sparkline_text` preview) from `poly_kalshi_mark_history`.
   Uses the CP3(phase1) OPEN gate (placed ENTRY, order_id present, not resolved). Division-level
   **live total unrealized** = Σ open marks (`None` if unmarked — never a fabricated 0).
2. **Copy-moment feed** — recent placements (bounded 25, newest first) with the CP1 trigger, + a
   `latest_order_id`/`latest_ts` exposed for later **client-side** new-row (sound/flash) detection.
3. **Live refresh** — a partial route `GET /partials/prediction-markets/poly_kalshi_mlb/live`
   (`routes.py:516`, SELECT-only via `asyncio.to_thread`) + two templates: `poly_kalshi_live.html`
   (shell, included in `pm_dashboard_body.html:426` only when `view.selected == 'poly_kalshi_mlb'`)
   with `hx-trigger="load, every 60s"`, and `poly_kalshi_live_inner.html` (the content it refreshes).
   **"as of {mark_ts}" + STALE** rendering mirrors MACE's precedent; unmarked → "marking…"; missing
   trigger → "—".

## Proofs (the checkpoint's asks)
- **Broker-free:** (a) the builder's signature is `(db_url)` — it structurally cannot reach a broker
  (`test_live_view_is_broker_free`); (b) grep of the whole builder body for
  `broker|.quote|_client|snapshot|.post(|kalshi_live|deps` → **NONE**; (c) the partial route only does
  `asyncio.to_thread(data.build_poly_kalshi_live_view, deps.db_url)` — no Kalshi client.
- **Arb/other paths untouched:** the change is **purely additive — 0 deletions across all 6 files**;
  no existing PM query fn (`_query_pm_open_trades`/`_query_pm_pending_count`/`_kalshi_cutoff_clause`/
  `_query_pm_resolved_stats`/`build_prediction_market_view`) has a single `-` line; the full
  `test_prediction_markets_dashboard.py` suite is green (resolved tiles / open / badge / epoch cutoff
  behavior unchanged).
- **Graceful rendering:** `test_live_view_graceful_when_trigger_and_mark_absent` (pre-CP1 row +
  unmarked → `None`/stale, `total_unrealized=None`); template render tests assert "Miami Marlins"
  (trigger), "marking…" (unmarked), "as of" (stale label), "Recent copies" (feed) all render.

## Evidence
- **125 passed / 0 failed** (8 new CP3 tests incl. 2 template-render tests + dashboard + marks +
  executor + copy_trader + reconciliation). `py_compile` OK on `data.py`/`routes.py`.
- Additive `+331 / −0` across 6 files (data.py +167, routes.py +9, pm_dashboard_body +5, 2 templates
  +105, test +145). Shared files byte-unchanged.

## Data-contract impact — CONTRACT NOW FULLY SATISFIED
Every NEEDS-BUILD field from the plan is now **AVAILABLE** and surfaced: `poly_slug/outcome/side/
market_type` (CP1), `current_yes_mid`/`unrealized_pnl`/`unrealized_pct`/`mark_ts`+`stale`/
`price_sparkline` (CP2 → this view), copy-moment feed + `latest_order_id`/`ts` for new-row detection
(CP3). The later Claude Design UI binds to exactly this contract.

## NOT done / next
- **Nothing else in the data layer** — Phase 2b (CP1+CP2+CP3) is complete.
- **Batched deploy** (operator-run, ONE restart): the DB migration for the volatile tables + the
  CP1+CP2+CP3 file changes + the poller spawn + the dashboard read; verify the live loop re-arms
  ARMED/unhalted, the poller ticks, and marks appear. (Plan to follow on your go.)
- **Claude Design UI** — the separate later step, binding to this contract.
- Nothing deployed.
