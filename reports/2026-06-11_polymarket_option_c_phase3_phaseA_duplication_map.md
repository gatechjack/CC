# Polymarket option (c) Phase 3 — Phase A duplication map + STOP finding

**Date:** 2026-06-11
**Branch:** `polymarket-option-c-phase3-unify-2026-06-11` (base `main` `b1e4150`; Phase 1 `b137c03` + Phase 2 `1c0b52e` both merged)
**Mode:** read-only mapping + design. No code changed. No prod access, no SSH, no DB writes (disclosure per `82fda13`).
**Outcome:** **STOP — hard-stop #4 triggered** ("Phase A finds the two pipelines are LESS duplicated than expected"). Awaiting operator scope confirm before any refactor.

---

## 1. Headline

The Phase-3 framing (scoping doc §5, lines 188–191) says *"Both scripts duplicate the
leaderboard→activity→resolution→score pipeline. Extract a shared `whale_screening`
module … with one REDEEM-grounded compute + scorer."*

Read against the post-Phase-1/2 code, that overstates the duplication. **The compute,
the scorer, and the activity-walk are already single-source shared functions that both
scripts import.** What remains genuinely duplicated is a single ~30-line
activity-fetch *loop wrapper* (plus a 1-line resolution fetch). The leaderboard fetch,
the audit-input prep, the scoring *invocation*, and the selection/output are
**legitimately different** per roster, and unifying them would either break the
byte-identical contract or produce a heavily-parameterized mega-function that is *more*
drift-prone than the two clear scripts — a net loss against the stated goal.

A small, safe, genuinely-useful extraction does exist (§4). It is hours, not the
scoped 1–2 days, and it explicitly does **not** touch the legitimately-different stages.

---

## 2. Already shared — single source, both scripts import (NOT duplicated)

| Asset | Home | Used by |
|---|---|---|
| `build_audit_report` (the REDEEM-grounded compute) | `data/polymarket_whale_audit.py` | refresh:80, seed:110 |
| `score_whale_from_audit` (the realized-basis scorer, F-1) | `data/polymarket_whale_stats.py:249` | refresh:84, seed:111 |
| `_fetch_wallet_activity_windowed` (the paginated walk: exhaustion + ceiling) | **defined** `seed_*_deep.py:120` | **imported by** refresh:90 |
| `read_audit` / `write_audit` (audit cache) | `agents/research/polymarket_whale_audit_cache.py` | refresh only (seed deliberately skips — see §3) |
| `fetch_leaderboard` / `fetch_activity` / `fetch_market_resolutions` | `data/polymarket_data_api_client.py` | both |
| `wilson_lcb_95`, `_edge_factor`, `_category_bonus` | `kalshi_whale_stats.py` | both (via the scorer) |

The two scripts already share the entire **compute + scorer + pagination walk**. The
walk even creates a current cross-script coupling: `refresh` imports
`_fetch_wallet_activity_windowed` *from* `seed_*_deep` (a script-imports-script smell).

---

## 3. Genuinely duplicated — the actual remaining drift surface

### 3a. Activity-fetch loop wrapper  (the one real multi-line dup, ~30 lines)
- **refresh** `lines 218–258`: `eff_target` calc (229–232) → `for wallet in candidates`
  loop calling the shared walk (233–246) → `n_truncated` log (247–253).
- **seed** `lines 514–567`: `eff_target` calc (524–527) → loop (531–552) → `n_truncated`
  log (553–561).

Shared exactly across both: the `eff_target` formula (`max_pages*activity_limit+1`), the
truncation-flag derivation (`reason in ("max_pages_hit","fetch_error")`), the
`condition_id` accumulation over BUY rows, the `n_truncated` count + warn.

**Two behavior deltas that MUST be preserved** (a unification has to parameterize, not
erase, these):
1. `refresh` wraps the walk call in `try/except Exception → ([], "fetch_error")`
   (broader than the walk's own internal `PolymarketDataAPIError` catch); `seed` does not.
2. `seed` accumulates `summary["termination_reasons"]` + `summary["with_activity"]`
   telemetry; `refresh` does not.

### 3b. Resolution fetch  (1 identical line)
`refresh:261` and `seed:570` are byte-identical:
`resolutions = await client.fetch_market_resolutions(list(all_condition_ids))`.

---

## 4. Legitimately different — must stay in callers to preserve byte-identical output

| Stage | refresh (copy roster) | seed (observation roster) | Why not unifiable cleanly |
|---|---|---|---|
| **Leaderboard fetch + dedup** | single `fetch_leaderboard(limit=N)` call, no pagination (192–216); record = `{entry, categories_seen:set, ranks_by_category:dict}` | paginated offset loop `_LEADERBOARD_PAGE=50` to N (459–507); record = `{entry, best_category, best_rank, lifetime_pnl/vol}` | Different fetch strategy **and** different candidate record shape, each consumed differently downstream (refresh needs `categories_seen` for per-category scoring; seed needs `best_*`/`lifetime_*` for its output rows). Forcing one shape changes output. |
| **Audit-input prep** | FULL activity → `build_audit_report` + read/write **cache** (285–293) | WINDOWED raw rows via `_select_resolved_buys_window`→`_aggregate_window_to_decisions`→`_rows_for_window`, **no cache** (589–621) | Full-window vs last-100-decision window is the core copy-vs-observation difference. Cache divergence is **deliberate** (§ below). |
| **Scoring invocation** | global (`target_category=None`) + per-category loop, inflation **GATE** (298–313) | single call, **flag-only** (632–634) | Same shared function; different call pattern. Gate-vs-flag is settled Phase-1/2 (F-1 / observation-is-flag-only). |
| **Selection / output** | Rule B (top-N/cat + global), pinned-merge, `--algo-select`, gated-out, unrankable, dry-run cause-attribution → `selected_whales` | floors (n/recency/wr/pnl), rank by realized PnL, provisional, weekly `--merge` → `watch_only_whales` | Task explicitly scopes these to the callers. Entirely disjoint. |

### The audit cache cannot be unified inside a byte-identical refactor
seed's own comment (lines 623–631) and scoping §8 flag "cache unification is Phase 3."
But the cache key is `(wallet, activity_max_ts)` — **scope-blind**. seed's windowed
report and refresh's full-window report for the *same wallet at the same max_ts* are
**different reports**; letting seed read-through the existing cache would collide them.
Unifying requires adding a scope discriminator to the key → **new key shape, new cache
entries = a behavior change, not byte-identical.** It therefore does **not** belong in
this "refactor, same behavior" mandate; it is separate behavior-affecting work.

---

## 5. The small safe extraction that IS available (if operator confirms)

New module `data/whale_screening.py` (or agreed name) holding:
- `_fetch_wallet_activity_windowed` **moved** here from `seed_*_deep` → removes the
  current `refresh`→`seed` script-imports-script coupling; both import from the module.
- `fetch_activity_window_for_candidates(client, wallets, *, activity_limit, max_pages,
  target_buy_rows, broad_catch=False, on_termination=None)` → `(activity_by_wallet,
  truncated_by_wallet, all_condition_ids)` — captures §3a, with the two deltas
  parameterized (`broad_catch` for refresh's `except Exception`; `on_termination`
  callback for seed's telemetry).

Both callers then: build candidates (their own leaderboard logic) → call the helper →
fetch resolutions → their own audit-input/scoring/selection. **Byte-identical** if the
helper reproduces each caller's path exactly.

**Explicitly out of this extraction:** leaderboard fetch/dedup, audit-input prep,
scoring invocation, selection/output, and the audit cache (§4).

---

## 6. The fork for the operator (hard-stop #4)

- **(A) Small extraction** — §5 only. Removes the real drift vectors (`eff_target`
  formula, truncation-flag derivation, condition-id glue, script→script import) with a
  byte-identical guarantee. ~hours.
- **(B) Wider re-scope** — also parameterize/unify leaderboard fetch + candidate dedup
  into the module (forces a common candidate record shape). Higher byte-identical risk;
  needs the §C equivalence proof to carry real weight. Closer to the scoped 1–2 days.
- **(C) Defer / cancel** — the genuine drift surface is small enough that the
  cost/risk may not clear the bar now; this map is the artifact; stop.

Cache unification is recommended **excluded from all three** (not byte-identical;
separate behavior change) — file it as its own follow-up if wanted.

---

*Phase A artifact — committed unmerged on `polymarket-option-c-phase3-unify-2026-06-11`.
No code, schema, prod, or DB touched. Phase B (implementation) NOT started — gated on
the §6 decision.*
