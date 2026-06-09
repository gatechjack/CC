# Polymarket option (c) — net-position whale P&L: implementation scoping

**Date:** 2026-06-09
**Branch:** `polymarket-option-c-scoping-2026-06-09` (base `origin/main` `2ed3998`)
**Mode:** read-only research + design. No code changes, no schema changes, no deploys.
**Framing:** (ii) operationalize the existing REDEEM-grounded compute into the
watchlist screening pass — **not** greenfield. **Scope A:** copy roster first, then
observation roster, then unification.
**Predecessor:** investigation `reports/2026-06-09_polymarket_sell_pairing_investigation.md`
(BACKLOG P1, commit `2ed3998`) selected option (c).

---

## 1. Executive summary

Option (c)'s hard part is already built. `build_audit_report`
(`data/polymarket_whale_audit.py`, commit `df3e48b`, **deployed on prod**) computes
per-whale **REDEEM-grounded realized P&L** from the `/activity` feed — it aggregates
partial fills into `(condition_id, outcome_index)` decisions (the 4.6× clustering fix)
and accounts for sells + on-chain redemption, and quantifies the gap to the naive
model as `pnl_inflation_ratio`. It is a **pure function** and is currently wired only
into the on-demand "Analyze Whale" review, **not** the watchlist screening. So option
(c) is an *operationalization*, not a greenfield algorithm: route the watchlist
screening through this compute and define what score it produces. **Revised effort:
~1–2 days for Phase 1** (copy roster) vs. the original 3–5d greenfield estimate. The
copy roster (`selected_whales`, written by `refresh_polymarket_whales.py`) is the P1
target — it gates real copy decisions and today uses the naive per-fill,
held-to-resolution model with neither the clustering fix nor REDEEM-grounding, and is
not even scheduled. Two coexistence constraints shape the work: `_whale_autopause`
shares the `selected_whales` key and will fight a rebuild-from-scratch refresh, and the
heavier compute needs a stated performance/cadence budget.

---

## 2. Goal context

Operator goal: *"identify winning traders and copy them for profit."* That is
**whale-attribution P&L** (whose own bets actually made money), which gates watchlist
promote/demote. The 2026-06-09 investigation found the copy-trader's whale P&L was
unreliable due to partial-fill duplication (4.6×) + settle-path contention; option (c)
sidesteps both by computing from the whale's own activity, not from our copy
round-trips.

**Two P&L concerns must stay separated** (conflating them caused the original
confusion):
- **Goal #1 — whale-attribution P&L** (this doc): the whale's own realized P&L from
  `/activity`. Feeds watchlist selection.
- **Goal #2 — our copy paper-P&L**: how our $5 copies performed, from
  `polymarket_round_trips`. Feeds the dashboard *and* `_whale_autopause`. Out of scope
  here, but see §6 (autopause coexistence) — its input table is the inflated one the
  investigation flagged.

---

## 3. Algorithm specification — reference, do not re-spec

The canonical implementation is **`build_audit_report`** and its pure helpers in
`data/polymarket_whale_audit.py` (`df3e48b`). Do not re-derive; cite and reuse:

- `group_fills_by_decision(rows, resolutions)` — buckets `/activity` rows into
  `(cid, oi)` `DecisionFills`; extracts REDEEM rows (`outcomeIndex=999` sentinel) as
  the winning-side redemption payout (`polymarket_whale_audit.py:351`).
- `DecisionFills.realized_pnl` (`:209`) — `Σsell_usdc + redeem_payout − Σbuy_usdc`.
  The REDEEM-grounded realized number.
- `DecisionFills.held_to_resolution_pnl` (`:199`) — the naive watchlist number, kept
  for the inflation comparison.
- `compute_realized_pnl(...)` (`:625`) → `RealizedPnLReport`: `realized_pnl_usdc`,
  `held_to_resolution_pnl_usdc`, `pnl_inflation_ratio`, clean-holds vs partial-sells
  split.
- `compute_clustering` / `compute_sell_footprint` / `compute_edge_profile` /
  `compute_category_concentration` — the supporting signals.

**Data sources** (all live, free, unauthenticated; `data/polymarket_data_api_client.py`):
- **Primary:** `/activity?user=` → `ActivityRow` (BUY/SELL/REDEEM, price, size, usdc).
  This is what `build_audit_report` consumes. Already fetched by both screening scripts.
- **Cross-check / fallback (per Decision 1(iii)):** `/closed-positions?user=` →
  `ClosedPositionRow.realized_pnl` (Polymarket's *own* per-resolved-position realized
  P&L). Use as a reconciliation oracle in Phase-1 validation (does our REDEEM-grounded
  number track Polymarket's?). **Not** a primary source: `seed_*_deep`'s docstring
  (`:56-62`) already documents that `/closed-positions` only surfaces *positive*-PnL
  positions (losses settle to $0 and never appear), so a win-rate or profit-sum built
  from it is a one-sided upper bound. Good for spot-reconciliation, wrong for selection.

**The gap to close (the actual Phase-1 work):** `build_audit_report` emits realized
P&L + footprint signals but **not a selection score**. The watchlist needs a scalar to
rank/gate on. Today `score_polymarket_whale` (`whale_stats.py:198`) produces
`composite = wilson_lcb_95_weighted(WR) × edge_factor(avg_pnl_per_contract) ×
category_bonus`, where WR/edge are computed on the *naive held-to-resolution per-fill*
basis. Phase 1 introduces a `score_whale_from_audit(report)` that recomputes the same
composite shape but on the **decision unit** and **realized** basis:
- Wilson LCB over **resolved decisions** (`n_resolved_decisions`, wins =
  `Σ is_winning_side`) instead of per-fill samples — removes the clustering inflation
  at the score level.
- Edge factor from **realized ROI** (`realized_pnl_usdc / Σbuy_usdc`) instead of
  held-to-resolution avg-pnl-per-contract.
- Optional new gate: exclude/penalize whales with high `pnl_inflation_ratio` (headline
  P&L that's mostly churn, not held conviction).

`build_audit_report` does not currently expose per-report win/loss decision counts as a
top-level field; Phase 1 either adds them to `WhaleAuditReport` (small) or derives them
from the decisions dict inside the scorer. **Exact score definition is operator-resolved
(§6, F-1).**

---

## 4. Data model

`build_audit_report` is a **pure function** and both screening scripts **already fetch
`/activity` + resolutions per candidate**. Therefore:

- **Phase 1 requires NO new table and NO schema change.** `refresh_polymarket_whales`
  swaps `compute_polymarket_stats`→`build_audit_report` + `score_whale_from_audit`, and
  writes the realized metrics into the existing `selected_whales` agent_state records
  (and `selection_metadata`). This is the original brief's "Shape B (compute-on-read)",
  which fits because the screening pass *is* the compute pass.
- **Reuse the existing cache.** `agents/research/polymarket_whale_audit_cache.py` already
  TTL-caches `WhaleAuditReport` keyed on `(wallet, activity_max_ts)` in `agent_state`
  under the `polymarket_whale_analyst` namespace — self-invalidating on new fills. The
  screening pass can read-through this cache so a whale with no new activity isn't
  recomputed. (Namespace is distinct from the promotion-relevant
  `polymarket_copy_trader` slots — no collision; see cache module docstring.)
- **Deferred (Phase 3+):** a `whale_pnl_history` table only if trend/recency-decay
  across refreshes is wanted. Not needed for Phase 1; flagged so we don't add it
  speculatively (CLAUDE.md §"Adding a column": use `extra`/existing slots first).
- **`polymarket_round_trips` is untouched** — it serves goal #2 (our copy P&L) and the
  dashboard. Option (c) feeds watchlist selection only. The two stay decoupled.

`agent_state` keys in play (all under `agent='polymarket_copy_trader'` unless noted):
`selected_whales` (copy roster), `watch_only_whales` (observation), `pinned_whales`
(dashboard manual promotions, merged by refresh), `selection_metadata`,
`whale_state:<wallet>` (per-whale runtime), `metrics_epoch`. Audit cache lives under
`agent='polymarket_whale_analyst'`.

---

## 5. Implementation phasing

### Phase 0 — Prerequisites / drift state (no work; recorded for the record)

Resolved 2026-06-09 (probes pm1–pm5, read-only):
- The watchlist PnL-aggregation fix **is on main and deployed** (`seed_*_deep` carries
  `_aggregate_window_to_decisions`, commit `899821d`). Prod markers match main
  (md5 differs by CRLF only; line counts off-by-one; git tip-to-tip diff identical).
- The `pm-watchlist-pnl-aggregation-fix` branch is **superseded** — its content is in
  main (patch-equiv) and main is 15+ commits ahead. **Recommend deleting the branch**
  (operator decision). It is **not** a prerequisite for any phase here.
- **Phase 1 does not depend on the branch** — it modifies `refresh_polymarket_whales.py`,
  a different script. The branch only ever touched `seed_*_deep`, which already has the
  fix on main.

### Phase 1 — Copy roster operationalization (SHIPPABLE INDEPENDENTLY) — ~1–2 days

Replace `compute_polymarket_stats` + `score_polymarket_whale` in
`refresh_polymarket_whales.py:149-176` with `build_audit_report` +
`score_whale_from_audit`. Define the selection metric (F-1). Coexist with autopause
(§6). Validate against `/closed-positions` + operator spot-check on known whales.

- **Blast radius:** `selected_whales` only — the copy roster. Paper-mode division
  (`polymarket_copy_trading` is `broker: paper`), so no live-capital path. The copy
  trader (`polymarket_copy_trader.py`) consumes `selected_whales` unchanged (same record
  shape + new metric fields).
- **Validation criteria before Phase 2:** (a) new realized P&L per whale reconciles with
  `/closed-positions` within tolerance on a 5–10 known-whale sample (F-3); (b) operator
  spot-check: do the top-ranked whales match intuition; (c) the roster doesn't churn
  wildly vs. the prior naive roster without explainable cause (inflation-driven drops
  are *expected* and good).
- **Rollback:** revert the one-script change; `selected_whales` repopulates on the next
  refresh with the old compute. Keep the old functions importable during Phase 1 (don't
  delete until Phase 4). Paper-mode → zero capital risk during rollback.

### Phase 2 — Observation roster extension — ~0.5–1 day

Extend the same compute to `seed_polymarket_watchlist_deep.py` (writes
`watch_only_whales`, weekly timer `trading-corp-pm-watchlist-deep.timer`). It already has
the clustering fix; this swaps its held-to-resolution math for REDEEM-grounded realized.

- **Unmerged-branch question resolves HERE, not in Phase 1.** Since the branch is
  superseded (Phase 0), the disposition is: **supersede it** with (c)'s compute (no merge
  needed). Confirm at Phase 2 start that nothing on the branch is still wanted (Phase 0
  says no). Operator decision F-2.
- **Blast radius:** dashboard observation list only (read-only display). Different
  consumer, different validation than Phase 1 — which is *why* it's sequenced separately
  (don't conflate execution-gating with display).
- **Rollback:** revert the `seed_*_deep` change; weekly timer repopulates.

### Phase 3 — Consolidation (unification, "B") — ~1–2 days

Both scripts duplicate the leaderboard→activity→resolution→score pipeline. Extract a
shared `whale_screening` module that both call, with one REDEEM-grounded compute +
scorer. Eliminates the two-script drift class (the exact problem Phase 0 spent time
untangling).

- **Blast radius:** both rosters; a refactor across two scripts. Ship only after Phase 1
  + 2 have proven the compute in production. **Validation:** byte-equivalent roster
  output vs. Phase 1/2 scripts on a dry-run before cutover.
- **Rollback:** keep the two scripts until the shared module is proven; cut over last.

### Phase 4 — Cleanup — ~0.5 day

Remove the now-dead `compute_polymarket_stats`/`score_polymarket_whale` held-to-resolution
path and any dead imports, after a grace period with no rollbacks. Update tests.

---

## 6. Risk + edge cases

### Constraint 1 — `_whale_autopause` coexistence (ground truth, read 2026-06-09)

- **What it reads:** `should_autopause` (`_whale_autopause.py:69`) queries
  `polymarket_round_trips` filtered by `division` + `json_extract(extra_json,
  '$.whale_user_name')`, computing n_resolved, WR%, total realized_pnl. Triggers when
  n_resolved≥30 AND WR<40% AND total_pnl<-$5 (conjunctive).
- **What it writes / when:** `polymarket_copy_trader._apply_autopause_filter`
  (`polymarket_copy_trader.py:526`) runs **every copy cycle (~60s)**, BEFORE processing
  whales. On trigger it **destructively rewrites `selected_whales`** to the `keep` list
  (`:571`) and emits a `polymarket_whale_auto_paused` audit. **There is NO separate
  paused-set key** — the pause is expressed solely as absence from `selected_whales`.
- **The conflict (model i + iii):** `refresh_polymarket_whales` rebuilds `selected_whales`
  from scratch each run and merges only `pinned_whales` — it does **not** consult
  autopause state. So **a refresh silently re-adds an autopaused whale** if it still
  scores well; autopause then re-removes it next cycle. This flap **exists today** and
  option (c) does not create it — but (c) makes it more salient because the two systems
  now measure *different* things: autopause uses **our-copy round_trips P&L (goal #2,
  the inflated table)**, while the new refresh uses **whale-own realized P&L (goal #1)**.
  A whale can be a genuinely good trader (refresh selects) yet our copies lost money
  (autopause pauses), or vice versa.
- **Required design (model i, with iii as fallback):** Phase 1 must make pause **durable
  across refreshes**. Recommended: introduce a dedicated
  `agent_state(polymarket_copy_trader, auto_paused_whales)` set that `_apply_autopause_filter`
  writes to (in addition to filtering), and that `refresh_polymarket_whales` reads and
  **excludes** when rebuilding `selected_whales`. This is a small additive change (one new
  key, two read/write sites) and removes the pre-existing flap as a bonus. Ordering
  (iii) alone (refresh-then-autopause) is insufficient because the two run on independent
  schedules (refresh manual/off-cycle; autopause every 60s). **Operator decision F-4**
  confirms this model.
- **Out of scope but flagged:** autopause's *input* is the inflated `round_trips` table
  (goal #2). Re-grounding autopause on reliable data is a separate follow-up, not part of
  (c). Phase 1 only guarantees (c)'s refresh won't un-pause.

### Constraint 2 — manual-vs-scheduled refresh + performance budget

- **Today:** `refresh_polymarket_whales` is **manual / no timer** (only `seed_*_deep` has
  the weekly `trading-corp-pm-watchlist-deep.timer`, confirmed via prod `systemctl
  list-timers`). The copy roster is refreshed by the operator on demand.
- **New cost:** REDEEM-grounded compute needs the *full* activity window per whale
  (all fills + REDEEM events), i.e. paginated `/activity` walks like `seed_*_deep` does
  (`activity_limit=500`, up to `max_pages=10`), vs. the current single 200-row call.
  REDEEM rows are *in* the activity feed (no extra endpoint). Resolutions are one batched
  gamma-api fetch over all unique condition_ids (chunked 50, ×2 open/closed variants),
  with the documented Cloudflare-403 backoff tail risk. Concurrency capped at 5
  (`PolymarketDataAPIClient` semaphore).
- **Budget estimate:** the upgraded refresh's cost profile ≈ `seed_*_deep`'s current
  weekly job (same fetch shape, ~similar candidate count). Order-of-magnitude: ~tens of
  whales × a few activity pages ÷ 5 concurrency + one resolution sweep ≈ **single-digit
  minutes**, with Cloudflare backoff able to add minutes in the tail. **Confirm against
  `seed_*_deep`'s actual logged runtime before scheduling** (read the weekly job's
  journal, or a one-off `--dry-run` timed run — operator-run).
- **Decision F-5:** keep manual short-term (acceptable; operator already refreshes on
  demand), then move to a timer once Phase 1 is validated — recommend **weekly**, aligned
  with the existing deep-seed cadence (whale edge decays slowly; weekly matches the
  observation roster).

### Other edge cases

- **Unresolved markets:** `build_audit_report` only computes over *resolved* decisions
  (`is_resolved` gate) — open positions don't enter realized P&L. Same windowing
  semantics as today; no regression.
- **Numeric precision:** USDC is 6-decimal; sums are float. The audit module already
  rounds at report boundaries. Cross-check against `/closed-positions` catches gross
  drift (F-3).
- **Feed gaps / partial resolution coverage:** `fetch_market_resolutions` tolerates
  rate-limited chunks (returns `not_found`, logged). A whale with many unresolved/missing
  resolutions yields a smaller effective sample → the min-decision floor (`min_resolved`)
  already guards selection. No new failure mode.
- **REDEEM sentinel drift:** the `outcomeIndex=999` REDEEM convention is load-bearing
  (`polymarket_whale_audit.py:116`). If Polymarket changes it, realized P&L silently
  loses redemption payouts → would *understate* winners. Validation F-3 (vs
  `/closed-positions`) is the detector; worth a periodic re-check.
- **`/activity` pagination cap:** very high-volume whales may exceed the page ceiling,
  truncating the window. Same as `seed_*_deep` today; acceptable (window is by design).

---

## 7. Operator-resolved decisions

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **F-1** | **Selection metric** — what defines a "winning whale" on the new realized basis? | (a) realized P&L threshold; (b) decision-unit WR (Wilson LCB); (c) realized ROI; (d) composite (Wilson × realized-edge × category), optionally gated by `pnl_inflation_ratio`; (e) keep current composite shape but on realized inputs | **(e)+(d):** preserve the current composite *shape* (minimal behavior change, keeps Rule-B per-category selection intact) but feed it decision-unit WR + realized ROI, and **add a `pnl_inflation_ratio` exclusion gate** (e.g. drop whales >0.5 — headline is churn). Pin exact thresholds during Phase 1 against live data. |
| **F-2** | **Unmerged-branch disposition** | merge / supersede / coexist | **Supersede** (Phase 0: branch content already in main; delete the stale branch). |
| **F-3** | **Phase 1 → 2 validation gate** | parallel-run comparison period? operator spot-check? `/closed-positions` reconciliation tolerance? | **All three, lightweight:** one refresh dry-run diffed vs. the naive roster (explain the deltas), `/closed-positions` reconciliation within ~tolerance on 5–10 known whales, and operator eyeball of the top ranks. No long parallel period needed (paper-mode). |
| **F-4** | **Autopause coexistence model** | (i) refresh preserves pause state; (ii) separate keys / boundary note; (iii) ordering enforced | **(i) via a new `auto_paused_whales` key** that autopause writes and refresh excludes (also fixes the pre-existing flap). |
| **F-5** | **Refresh cadence / trigger** | manual / weekly / daily | **Manual now → weekly timer post-validation**, aligned with the deep-seed cadence; confirm runtime first. |

---

## 8. Open questions for follow-up sessions

- **Autopause re-grounding (separate work):** should `_whale_autopause` move off the
  inflated `polymarket_round_trips` onto a reliable signal (our-copy realized P&L done
  right, or the whale's own P&L)? Decoupled from (c); worth its own scoping.
- **`whale_pnl_history` table:** if recency-weighted trend across refreshes is wanted
  later, spec the table then (Phase 3+). Not now.
- **`score_whale_from_audit` home:** new function in `polymarket_whale_stats.py` (next to
  the scorer it replaces) vs. a new `whale_screening` module created at Phase 3. Lean:
  add to `polymarket_whale_stats.py` in Phase 1, migrate to the shared module in Phase 3.
- **Category bonus on realized basis:** confirm the category-bonus mechanism still makes
  sense once edge is realized-ROI (it multiplies the composite today). Revisit under F-1.
- **Backfill:** none required — the compute is forward-looking on each refresh and the
  audit cache self-invalidates. No historical migration.

---

*Planning artifact — committed unmerged on `polymarket-option-c-scoping-2026-06-09`,
mirroring the 2026-06-09 investigation pattern. No code or schema changed this session.*
