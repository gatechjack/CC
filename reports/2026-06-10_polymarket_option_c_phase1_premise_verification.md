# Polymarket option (c) Phase 1 — premise verification + scope (STOP before code)

**Date:** 2026-06-10 11:03 UTC
**Branch:** `polymarket-option-c-phase1-2026-06-10` (dedicated worktree, base `origin/main` `32aa884`)
**Mode:** Phase A read-only premise verification. No code changed in this commit (findings artifact only).
**Predecessors (binding spec):** `reports/2026-06-09_polymarket_option_c_implementation_scoping.md`
(scoping, F-decisions §7); `reports/2026-06-09_polymarket_workflow_ground_truth_verification.md`
(workflow ground truth + F-correction §9). Corrections supersede the scoping doc where they conflict.

---

## 0. State verification

- `git rev-parse origin/main` = **`32aa884dcdbb5b7801a43bb7758a6672449ef490`**. Local `main` at same SHA.
- **ANOMALY (resolved):** the `claude --worktree` launch did **not** isolate — it landed in the
  main checkout `C:/Users/AA Incorporado/cc` on branch `main` (the silent-landing failure the prior
  session warned about). Remediated by creating the dedicated worktree
  `.claude/worktrees/polymarket-option-c-phase1-2026-06-10` + branch off `origin/main`, tracking
  origin/main, HEAD `32aa884`, clean status. All Phase B–E work happens here.
- **Documented test baseline** (latest deploy_log entry, 2026-06-09 ~03:50 UTC, full suite under
  `run_capped.ps1`, 3 collection-error files `--ignore`d): **2229 passed / 28 failed** (28 pre-existing
  in robinhood/tasty/IC/webhooks). Ignored files: `test_backtest_bitunix_confluence_five_factor.py`,
  `test_bitunix_confluence_gate.py`, `test_bitunix_gate_inputs.py`. Exact on-branch baseline will be
  re-captured at Phase C start.

---

## 1. Phase A premise checks

### A-1. Wilson input gap — CONFIRMED; resolution decided

The scoping doc §3 flagged that `build_audit_report` may not expose per-report win/loss decision
counts. Verified at `32aa884`:

- `WhaleAuditReport` (`polymarket_whale_audit.py:322-345`) exposes `n_resolved_decisions` (the Wilson
  **denominator**) but **NOT** the win count (Σ`is_winning_side`) and **NOT** the realized-ROI
  denominator (Σ`buy_usdc` over resolved decisions).
- `build_audit_report` (`:676-735`) returns **only** the composed `WhaleAuditReport` — it does **not**
  return the `decisions` dict. The per-decision `is_winning_side` / `sum_buy_usdc` live on
  `DecisionFills` (`:148-227`) inside `group_fills_by_decision`, which is local to `build_audit_report`.

**Therefore "derive in the scorer" is not feasible** for a `score_whale_from_audit(report)` that takes
only the report — the scorer cannot see the decisions. Deriving would require either changing the
scorer signature to also receive `decisions` (and having the refresh call `group_fills_by_decision`
itself, duplicating work) or having `build_audit_report` return decisions (wider surface change).

**Decision: add two additive fields to `WhaleAuditReport`** (computed in `build_audit_report` where
`decisions` is in scope), both with defaults so existing constructors/tests don't break:
- `n_winning_decisions: int = 0` — Σ`is_winning_side` over resolved decisions (Wilson numerator).
- `total_buy_usdc_resolved: float = 0.0` — Σ`sum_buy_usdc` over resolved decisions (realized-ROI denom).

This is the endorsed JSON/dataclass-extension pattern, **not** a DB schema change (CLAUDE.md §"Adding a
column": the data rides existing `agent_state` JSON; no `proposed_order`/`audit_event`/etc. column
added → no Board approval needed).

**Cache coupling (must ship together):** `polymarket_whale_audit_cache.py` serializes via `asdict`
(auto-includes new fields on write) but rehydrates top-level fields by an **explicit key tuple**
(`_dict_to_report`, cache `:204-213`). The two new keys must be added to that tuple, else cache hits
rehydrate them as defaults (0 / 0.0) — a silent correctness bug. Old cache entries written pre-change
lack the keys → `KeyError` → caught → treated as miss → recompute (the module's intended
"schema-drift safety valve", cache `:119-127`). Acceptable: the audit cache self-invalidates on new
activity and a one-time recompute is negligible.

### A-2. Audit-cache read-through for the screening loop — CONFIRMED works

- Cache is keyed on `(proxy_wallet, activity_max_ts)` under namespace `polymarket_whale_analyst`
  (cache `:55-63`), structurally isolated from all `polymarket_copy_trader` promotion slots (cache
  `:11-30`; test `test_namespace_constants_are_isolated_from_promotion_slots`). No collision risk with
  `selected_whales`/`pinned_whales`.
- Screening loop shape fits: fetch `/activity` per whale → `activity_max_ts = max(ts)` → `read_audit`;
  on miss, `build_audit_report` + `write_audit`. Self-invalidates when a whale has new fills.
- **Nuance (Phase B impl note, not a blocker):** read-through saves the *compute*, not the activity
  fetch (you need activity to know `activity_max_ts`). The current refresh batch-fetches resolutions
  for *all* whales' condition_ids at once (`refresh_polymarket_whales.py:135-136`); to also save the
  resolution fetch on cache hits, the loop should check the cache first and only include *miss* whales'
  cids in the resolution batch. For Phase-1 correctness the simplest order (fetch activity → check
  cache → batch resolutions for misses → build+write) is sufficient; full fetch-savings is an
  optimization.

### A-3. `selected_whales` record shape — CONFIRMED additive-safe

Consumer audit (verified across copy trader, promote/demote endpoints, dashboard readers):
- **Execution** (`polymarket_copy_trader.py`): `_load_selected_whales` (`:611-635`) gates on truthy
  `wallet`; `run_scan_cycle` (`:197-198`) reads `wallet` (required — falsy → whale silently skipped)
  and `user_name` (`.get(..., "")`). `_apply_autopause_filter` reads `user_name`/`wallet`/`category`/
  `rank` only via `.get()` in the audit payload (`:594-597`) — none can KeyError.
- **Promote** (`web/routes.py` `polymarket_watchlist_promote`): appends
  `{wallet, user_name, category, promoted_iso, source:"dashboard_button"}` — note it writes **no**
  `rank`/`composite_score` (those are refresh-only).
- **Demote** (`web/routes.py` `polymarket_whales_demote`): filters by `wallet`/`proxy_wallet` only;
  passes all other record fields through untouched.
- **Dashboard** (`web/data.py` 4 readers): read only `user_name` and `wallet`/`proxy_wallet`.

**Minimal required shape = `{wallet, user_name}`.** `{category, rank, composite_score, source}` are
read only in the autopause audit log (optional). **No schema validation, no fixed-key iteration, no
dataclass coercion** anywhere → **adding new fields is safe across all consumers.** Phase 1 keeps the
existing keys and adds realized metrics.

### A-4. Old-function consumers — old functions STAY

`compute_polymarket_stats` / `score_polymarket_whale` (`polymarket_whale_stats.py:92,198`) are also
consumed by `seed_polymarket_watchlist_deep.py` (`:105,538` — the **Phase 2** observation roster, out
of scope now) and by tests (`test_polymarket_copy_trader.py`, `test_polymarket_watchlist_seed.py`).
Phase 1 only stops the **refresh** script from calling them; the functions remain defined and importable
(deletion is Phase 4). This matches the task's "keep old functions importable."

---

## 2. Scope — exact functions added/changed + file list

### ADD
1. `trading_corp/data/polymarket_whale_audit.py`
   - `WhaleAuditReport`: `+ n_winning_decisions: int = 0`, `+ total_buy_usdc_resolved: float = 0.0`.
   - `build_audit_report`: compute + populate the two new fields from `decisions`.
2. `trading_corp/agents/research/polymarket_whale_audit_cache.py`
   - `_dict_to_report`: add the two new keys to the top-level rehydration tuple.
3. `trading_corp/data/polymarket_whale_stats.py`
   - **NEW** `score_whale_from_audit(report, *, target_category=None, whale_categories=(),
     min_resolved=DEFAULT_MIN_RESOLVED, inflation_threshold=DEFAULT_INFLATION_RATIO_THRESHOLD)
     -> ScoredWhale`.
   - **NEW** module constant `DEFAULT_INFLATION_RATIO_THRESHOLD = 0.5`.
   - Composite (F-1): `wilson_lcb_95(n_winning_decisions, n_resolved_decisions)` ×
     `_edge_factor(realized_roi)` × `_category_bonus(whale_categories, target_category)`, where
     `realized_roi = realized_pnl_usdc / total_buy_usdc_resolved`. Exclusion gates:
     `n_resolved_decisions < min_resolved`, **and** `pnl_inflation_ratio > inflation_threshold`.
     Helpers (`wilson_lcb_95`, `_edge_factor`, `_category_bonus`, `ScoredWhale`, `WhaleStats`) reused
     as-is from `kalshi_whale_stats` — composite *shape* preserved.
4. **NEW** `tests/test_polymarket_whale_stats_audit_scorer.py` — scorer units: decision-unit Wilson,
   realized-ROI edge factor, inflation-gate boundary (ratio exactly at threshold), min-resolved gate,
   category bonus, zero-decision/zero-cost-basis degenerate cases.
5. **NEW** `tests/test_refresh_polymarket_whales.py` — refresh-path test with fixture activity proving
   (a) the new compute drives selection and (b) the **pinned-whales merge survives unchanged**.

### CHANGE
6. `trading_corp/scripts/refresh_polymarket_whales.py`
   - Imports: drop `compute_polymarket_stats`/`score_polymarket_whale`; add `build_audit_report`
     (audit), `score_whale_from_audit` (stats), `read_audit`/`write_audit` (cache). Keep
     `DEFAULT_MIN_RESOLVED`.
   - Compute/score loop (currently `:146-176`): `compute_polymarket_stats` → `build_audit_report` via
     cache read-through; `score_polymarket_whale` → `score_whale_from_audit`. Rule-B per-category +
     global selection (`:178-202`) preserved — global uses `target_category=None`; per-category passes
     `target_category=cat, whale_categories=(cat,)` (reproduces today's flat 1.5× category bonus).
   - `selected_records` (`:210-216`): keep `{wallet, user_name, category, rank, composite_score}`;
     **add** `realized_pnl_usdc, realized_roi, pnl_inflation_ratio, n_resolved_decisions,
     n_winning_decisions, decision_win_rate` (additive).
   - `details` / `_print_human` (`:217-232, 297-328`): repoint column sources to the audit basis.
   - **UNTOUCHED:** pinned merge (`:250-282`), `--dry-run` write gating (`:284-292`).

### UNTOUCHED (hard stops)
- Autopause (`polymarket_copy_trader.py`, `_whale_autopause.py`) — **no edits** (F-4 corrected: no
  `auto_paused_whales` key; refresh stays unscheduled so no flap).
- Pinned-merge behavior, `polymarket_copy_trader.py`, `web/routes.py`, `seed_polymarket_watchlist_deep.py`,
  `agent_state` schema, any prod state.

**`--dry-run` already exists** (`refresh_polymarket_whales.py:32,68,340,362`) and gates both
`selected_whales` and `selection_metadata` writes — Phase E's "add `--dry-run` if missing" is a no-op.

---

## 3. Open design forks — need operator confirmation before Phase B

| # | Fork | Recommendation |
|---|------|----------------|
| **D1** | Wilson-input gap resolution (A-1) | **Add 2 fields to `WhaleAuditReport`** + update cache rehydration tuple. (Alt: pass `decisions` into the scorer — rejected: wider surface, dup work.) |
| **D2** | **Time-weighting.** Today's `score_polymarket_whale` time-weights Wilson (30d half-life, Kish n_eff). The decision-unit Wilson specced in §3 is **plain/unweighted**. | **Plain Wilson on decision counts for Phase 1** (matches §3; removes clustering inflation, the whole point). Consequence: the `--half-life-days` CLI flag becomes a **no-op** in the new path — keep it as accepted-but-inert (documented) for CLI compatibility, revisit time-weighting in Phase 3. This is a deliberate behavior change vs today — flagging explicitly. |
| **D3** | Return type of `score_whale_from_audit` | **Reuse `ScoredWhale`** with a synthesized `WhaleStats` (wins=`n_winning_decisions`, closed=`n_resolved_decisions`, so `win_rate`=decision WR) so the print/details path keeps working; realized metrics added to the `selected_records` dict. (Alt: new dataclass — more churn in refresh.) |
| **D4** | Inflation gate semantics + default | Exclude when `pnl_inflation_ratio > threshold` (**strictly greater**; ratio *exactly* at threshold is **kept** — this is the boundary the unit test pins). Default `0.5` per scoping doc; **calibrate against live data in Phase E** before any merge. |
| **D5** | Realized-ROI denominator edge | `realized_roi = realized_pnl_usdc / total_buy_usdc_resolved`; if denom ≤ 0 → ROI 0.0 (edge factor 1.0). Whales with 0 resolved decisions already excluded by min-resolved. |

Note on D2/edge scale: `_edge_factor(x) = 1 + clip(x, -0.5, +2.0)`. Realized ROI (e.g. +0.30 → 1.30×;
−0.10 → 0.90×; capped at +2.0 → 3.0×) sits in a comparable range to the old per-contract input, so the
helper is reusable without rescaling.

---

## 4. Phase C test plan + baseline

- New unit tests (item 4) + refresh-path test (item 5).
- Existing `test_polymarket_whale_audit.py` (constructs only via `build_audit_report`) and
  `test_polymarket_whale_audit_cache.py` (`_sample_report` uses kwargs) **stay green** under additive
  fields-with-defaults — verified by reading both.
- Full pytest gate via `.\scripts\run_capped.ps1 python -m pytest` (STOP-AND-READ #6 — never bare
  pytest), 3 collection-error files `--ignore`d. Capture exact on-branch baseline first, then post-change;
  quote the diff before each commit. Target: **2229 + new passed / 28 failed, zero new failures.**

## 5. Phase D commit plan (after operator confirmation)
1. scorer + audit fields + cache rehydration + scorer/audit-field tests.
2. refresh rewiring + refresh-path test.
3. docs (this report already committed; deploy_log untouched — no deploy this phase).
Push branch; **do NOT merge** (operator-gated).

---

*Phase A findings artifact — committed on `polymarket-option-c-phase1-2026-06-10`. STOP for operator
confirmation of §3 forks before any Phase B code.*
