# Net-verify target (Job 3, committed 2026-08-22) — QUEUED (runs at/after Step 3-4 backfill)

**Status: NOT run.** The manual net-verify requires ingested data; it executes at the Step-3 checkpoint
(and again after the full Step-4 run). This doc COMMITS the target + the exact method in advance so the
verification is decisive, not improvised.

## Primary target: Kickstand7 (Fed) — Jack's directed pick
- Wallet: **`0xd1acd3925d895de9aec98ff95f3a30c5279d08d5`**, name **Kickstand7**, seed-roster source
  `polymarket_copy_trader/selected_whales` (PCT-selected). One of the 12 roster wallets.
- Category: **fed**.
- **Why Fed (Jack's rationale):** the negRisk realizedPnl quarantine (§3A) is the load-bearing NEW
  logic in P1, and Fed is where it fires — Polymarket Fed band markets are `negRisk=true`
  (winner-take-all across rate bands), so the losing bands carry `total_bought≈0` with large negative
  `realized_pnl` (the exact shape that produced d1k21's spurious -$17M). Verifying the SCOREABLE net on
  a Fed whale tests the quarantine + rollup on the path that actually matters, not a path where nothing
  new happens. So far the quarantine is proven only on fixtures; this is its first check on live data.
- **Why Kickstand7 specifically:** largest Fed footprint in the roster (prior probe: Fed n ~= 79 —
  EMPIRICAL, not re-verified this session; see validation gap), and empirically clean-net (no -$17M
  blowup), which makes over-exclusion (false positives) easy to spot. **Not** an evanng UFC slice (per
  Jack — that slice is the unresolved §13A(a) three-way disagreement).

## Independent-sum method (genuine cross-check, not a tautology)
1. Read-only pull Kickstand7's raw `/closed-positions` (all pages) — the same source `ingest.py` uses,
   but pulled independently.
2. **Reimplement the §3A predicate from scratch in the verify script** (do NOT import `ingest.py` — a
   from-scratch reimplementation is what makes this an independent check of the logic, not a re-run of
   it). The predicate, per row:
   - `EPS = max(1.00, 0.01 * total_bought)`
   - `row_suspect = (realized_pnl < -(total_bought + EPS))  OR  (total_bought <= 0 AND realized_pnl != 0)`
   - **Event-group propagation:** if ANY row in a `(wallet, event_slug)` group is `row_suspect`, ALL
     rows in that group are suspect (the winner too — the survivorship guard). NULL `event_slug` =
     row-level only.
3. Sum `realized_pnl` over the SURVIVORS (non-suspect) restricted to `category == 'fed'`.
4. Compare to the DB after backfill: `SELECT net_realized_pnl, n_resolved, n_excluded, excluded_pnl,
   data_quality FROM pm_category_stats WHERE wallet='0xd1acd3...05' AND category='fed';`
   - **PASS** = independent survivor-sum == `net_realized_pnl` (to the cent), and independent
     suspect-count == `n_excluded`, and independent excluded-sum == `excluded_pnl`.
5. **Row-by-row spot audit** of 3-5 Fed events: confirm each event's classification is correct (a
   pure-win event keeps its scoreable rows; an event with a `total_bought≈0` losing band is quarantined
   as a whole group), so the aggregate match isn't masking two offsetting errors.

## Complementary check (satisfies P1_PLAN §12's literal "BINARY-market whale" item)
- Target: an MLB whale — **SDTrading `0x16bb9951a36fce71e2ef57890b786145e0ba8492`** (live-loop MLB),
  category **mlb**. MLB game markets are BINARY (two-outcome moneyline) -> `realized_pnl` is per-leg
  real, so the quarantine should exclude ~0 rows and the independent sum of ALL mlb rows should match
  `net_realized_pnl` EXACTLY. This proves the base parse->ingest->rollup arithmetic with no quarantine
  confounding, and — with a net-loser showing negative ROI — closes the §13A(a) UFC-reconciliation item
  positively (§12).
- Both checks run off the same Step-4 backfill; no extra pulls beyond the independent raw fetch.

## Validation gap (honest)
- Nothing here has run. Fed `n≈79` and "clean-net" are from the earlier realizedPnl probe, not
  re-measured this session. The actual Fed `n_resolved`, `n_excluded`, `excluded_pnl`, and
  `data_quality` for Kickstand7 are unknown until the Step-3 backfill — which is exactly what the
  Step-3 checkpoint surfaces for Jack's inspection.

Cross-ref: DEPLOY_SEQUENCE.md (Step 3 = Kickstand7 single-wallet), P1_PLAN.md §3A + §12 + §13A(a).
