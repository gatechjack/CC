# CP4 report — resolver adapter + realized-P&L reconciliation HARD GATE

**Status: BUILT, NOT DEPLOYED. Checkpoint STOP — awaiting operator review before CP5.**
Branch `poly-kalshi-mlb-phase1-2026-08-15`, built on the CP3 tip.

## Live-money / live-loop status (lead)
- **Zero live activity.** No order placed, no prod mutation, no restart. All changes are branch-only; the running engine (PID 753629 per handoff) sees none of it until CP7.
- **Live loop UNDISTURBED** — I have no prod shell and did not touch it; nothing deployed.
- **Shared files byte-unchanged** — `git diff origin/prod-live` on `kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py` is **empty** (verified this checkpoint).

## What CP4 delivers

### 1. Resolver adapter (`kalshi_resolver.py`, +62 / −0 — purely additive)
- `_ACTOR_TO_DIVISION['poly_kalshi_mlb']='poly_kalshi_mlb'` + `_ACTOR_TO_ARB_TYPE_DEFAULT['poly_kalshi_mlb']='poly_kalshi_copy'` (`:73`, `:82`) — new keys, existing keys unchanged.
- `_fetch_unresolved_poly_kalshi` (`:189`) — a SEPARATE fetch (the 6-actor `_fetch_unresolved_orders` query is byte-unchanged). Reads `kind='poly_kalshi_order'` PLACED ENTRY rows with a persisted `order_id` + fill data, and **normalizes** them (`fill_count→fill_qty`, `fill_price`, `leg_priced=True`) onto the shared `_compute_round_trip_row` contract — so the **core realized formula stays byte-identical** (the same branch the live-copy `kalshi_copy_placed_live` rows already use). Maps the REAL fill (not the limit price), per spec.
- `resolve_pending_round_trips` (`:534`) — appends poly_kalshi fills on TOP of the arb budget (own actor/kind, low-volume → never starved). The resolution loop (`get_market_resolution` → compose → INSERT OR IGNORE) is unchanged.
- **Realized model:** `qty×(1−fill_price)` won / `−qty×fill_price` lost / 0 void — the existing settlement-based gross model (`_compute_round_trip_row:230`), booked on Kalshi settlement.
- **Honest exclusion:** the 3 real live fills (MIA/CIN/AZ) were journaled **PRE-CP3** — no `order_id`/`fill_price` in their audit payload — so the resolver **excludes them** rather than book off the limit price. Proven by `test_resolver_skips_prefix_rows_without_fill_data` (0 booked, ticker never even looked up).

### 2. THE HARD GATE — reconciliation (the checkpoint's reason to exist)
**The two numbers, precisely:**
- **Resolver realized** (what the dashboard shows) = Σ `kalshi_round_trips.realized_pnl WHERE division='poly_kalshi_mlb'`.
- **Halt number** = `PolyKalshiCopyTrader._realized_pnl_day` (in-memory), fed by `run_settlement_sweep` summing Kalshi `get_settlements().pnl_dollars` (main.py:5173).
  - **Precision correction:** the handoff calls this "StrategyState.realized_pnl", but that field is **transient** — `StrategyState.from_persistence` loads only `halted`/`halt_reason` and returns `realized_pnl=0.0` (models.py:216-218: "persisting it would diverge from the audit source of truth"). The number actually driving the $100 halt is the sweep's `_realized_pnl_day`. I reconciled against THAT.

**Fixture reconciliation — PROVEN (`test_HARD_GATE_resolver_realized_equals_sweep_realized`):**
Four settled positions (the 3 real fills' shape + a void): MIA 9@0.54 won `+4.14`, CIN 10@0.48 lost `−4.80`, AZ 10@0.47 won `+5.30`, void `0.00` → first-principles total **+4.64**.
```
resolver realized (dashboard side) = +4.64
sweep _realized_pnl_day (halt side) = +4.64
first-principles expectation        = +4.64   -> ALL AGREE
```
The resolver computes from its OWN inputs (fill_price + market resolution); the sweep accumulates the Kalshi settlement P&L. They agree to the cent — the two code paths implement the identical settlement model (no unit/sign/void/fee drift in the resolver).

**The gate has TEETH (`test_HARD_GATE_has_teeth_detects_net_of_fee_drift`):** feeding the sweep a NET-of-fee settlement while the resolver books GROSS makes the two numbers **diverge** — proving the gate is not a rubber stamp; it detects exactly the dashboard-vs-halt drift it exists to catch.

**The 3 REAL orders (MIA/CIN/AZ) — honest status, NO reconciliation claimed:**
- They are **pre-CP3 vintage** (placed before Flag-1 persistence deployed), so the resolver **cannot** book them (no persisted fill price / order_id) — resolver side is definitionally $0 for them. A resolver-vs-halt-vs-KAREN 3-way on these is therefore **not possible** and I do **not** claim it. This is the gate working correctly, not a failure — it refuses to mis-book off the limit price.
- I have **no prod shell** to read the live `_realized_pnl_day` or KAREN settlements. If you want the sweep-vs-KAREN half for these 3 (independent of the resolver), I can hand you a read-only `az @file` runner — say the word.

**The ONE residual real-world assumption (flagged, not papered over):**
The resolver books **GROSS** (per spec + the existing model + the resolver docstring "Fees are NOT modeled"). The halt sums Kalshi `pnl_dollars`. They agree **iff Kalshi's settlement `pnl_dollars` is gross-of-fee** (payout − cost, fee charged separately at trade time). If Kalshi instead reports **net-of-fee**, the dashboard (gross) would exceed the halt (net) by cumulative fees — the drift the teeth-test demonstrates. **This can only be confirmed on a POST-CP3 settled fill against the real KAREN account (i.e., at/after CP7)**, since pre-CP3 fills carry no basis. CP3 already persists `fill_fee`, so IF real data shows Kalshi nets fees, the fix is a one-line `− fill_fee` in the resolver. **No disagreement is detected today; this is a named item for CP7 real-data verification.**

### 3. Resolved tiles + History populate (`data.py`, +13 / −3)
Broadened the `kalshi_round_trips`-table read buckets to include the `poly_kalshi_` prefix in the **three** places that power tiles + History: `_query_pm_round_trips` (`:4254`, History), `_query_pm_resolved_stats` (`:4831`, per-emission tiles), `_query_kalshi_distinct_market_stats` (`:4887`, distinct-market tiles). Composed poly_kalshi round-trips live in `kalshi_round_trips` with identical schema, so this is the whole change — cutoff/copy-mode clauses are no-ops for poly_kalshi_mlb. The audit_event OPEN/badge buckets keep CP3's separate blocks (untouched). Proven by `test_resolved_tiles_and_history_populate_poly_kalshi`:
```
n_resolved=2, n_wins=1, total_realized_pnl=-0.66 (4.14 - 4.80); History lists {mia, cin} with venue=kalshi, division=poly_kalshi_mlb, real qty/price/realized.
```

## Evidence
- **Reconciliation suite** `tests/test_poly_kalshi_reconciliation.py` → **8 passed** (resolver booking, pre-CP3 skip, arb-untouched coexistence, the HARD GATE, gate-has-teeth).
- **Existing resolver suite** `tests/test_kalshi_resolver.py` → **17 passed** (existing kinds untouched).
- **Dashboard** tiles/history + CP3 poly tests pass; full file failures **10 (unchanged pre-existing)** — my additions add zero.
- **poly_kalshi suite (3 files):** 0 failures.
- **Existing-kind-untouched:** resolver diff `+62 / −0` (no existing line deleted → `_compute_round_trip_row` + `_fetch_unresolved_orders` byte-unchanged) + 17 existing tests green.
- **Shared files:** `git diff --stat origin/prod-live` empty.

## Notes / deliberate boundaries
- **Equity curve** (`_query_pm_equity_curve`) and the **whale panel** (`_query_pm_whales`) were NOT broadened — out of the "tiles + history" scope. Equity needs the (plan-optional) snapshot loop; the whale panel keys on `extra_json.$.whale_handle`, which the poly_kalshi payload doesn't populate (it uses `whale`). Both are fast-follow, not CP4.
- **View-layer copy-mode/epoch:** I tested the query functions with defaults (`mode='all'` → no-op). If the poly_kalshi_mlb page passes a kalshi copy epoch, its (recent) entries still show under 'live'/'all'; 'paper' would hide them — a CP5/view-wiring nuance, flagged.

## NOT done (do not proceed without your go)
- **CP5** (agent_state epoch for the kalshi division; must not worsen the 10 pre-existing failures), **CP6** (epoch reset, operator-run), **CP7** (deploy + real-data gross-vs-net confirmation, operator-run) — not started.
- **Phase 2** — not started.

## Next
Your review of this diff. Open items for your call: (a) whether to net `fill_fee` now vs confirm gross at CP7; (b) whether you want the read-only KAREN/sweep runner for the 3 pre-CP3 orders.
