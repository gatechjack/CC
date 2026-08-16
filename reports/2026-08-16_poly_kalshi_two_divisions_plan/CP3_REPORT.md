# CP3 report — surface OPEN live fills + Flag-1 fill persistence

**Status: BUILT, NOT DEPLOYED. Checkpoint STOP — awaiting operator review before CP4.**
Branch `poly-kalshi-mlb-phase1-2026-08-15`, built on tip `2f11ffa` (== origin, tree was clean).

## Live-money / live-loop status (lead)
- **Zero live activity from this work.** No order placed, no prod mutation, no restart. Every change is branch-only; the running engine (PID 753629 per handoff) will not see any of it until the CP7 deploy.
- **Live loop UNDISTURBED** — I did not touch, restart, or re-arm it, and I have no prod shell (read-only prod is via your `pk_*.ps1` runners). I therefore do **not** assert its current runtime state from my own checks; nothing I did could affect it because nothing was deployed.
- **Shared files byte-unchanged** — `git diff origin/prod-live` on `kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py` is **empty** (verified this checkpoint).

## What CP3 delivers
Two-part change (Flag-1 folded in as the CP4 prerequisite), confined to 4 files (+322/−6):

### 1. Flag 1 — persist the REAL fill (`poly_kalshi_executor.py`, this division's own file)
- **Root cause (verified at source):** the live path captured `resp` at `submit()` then set `rec["resp"] = resp` **after** `_record` had already journaled the row, so only the limit price persisted.
- **Fix:**
  - New pure extractor `_fill_fields_from_v2_resp(resp, *, outcome)` — `poly_kalshi_executor.py:128`. Returns `{order_id, fill_count, fill_price, fill_fee}`. It reads the **same top-level keys** the canonical parser `kalshi_live.fill_event_from_v2_response` reads (`order_id` / `fill_count` / `average_fill_price` / `average_fee_paid`, `kalshi_live.py:200-218`) — I confirmed `place_order` makes the identical `client.post(_V2_ORDERS_PATH, body)` call and passes `resp` straight to that parser (`kalshi_live.py:366,382`), so the executor's `resp` is that exact flat dict. NO-leg price is `1 - yes` (parity w/ `kalshi_live.py:211`); this strategy is always-YES so it equals `average_fill_price`. Never raises (keeps the audit row even on a 0-fill, unlike the parser's `KalshiNoFill`).
  - `submit()` now parses the fill immediately after the POST and hands it to `_record(..., fill=fill)` — `poly_kalshi_executor.py:338`. The post-hoc `rec["resp"]` mutation is deleted (the caller only reads `res["status"]`, `poly_kalshi_copy_trader.py:197`, so nothing breaks).
  - `_record` adds `division` unconditionally and merges the fill when present — `poly_kalshi_executor.py:350,359`.
- **Deliberately NOT changed (scope):** the guardrail counters (`deployed_usd`, `orders_today`, journal `count`) still reflect the **requested** order, not the filled qty. Flag 1 only *adds* the real fill fields; it does not re-key guardrail accounting to filled qty. The requested-vs-filled delta is now **visible** in the journal (useful for CP4 reconciliation) without altering live guardrail behavior.

### 2. Surface OPEN — `_query_pm_open_trades` kalshi-side branch (`data.py`)
- New prefix `_POLY_KALSHI_PREFIX = "poly_kalshi_"` — `data.py:3955`. `poly_kalshi_mlb` is **disjoint** from both existing buckets (`polymarket_` / `kalshi_`), so before CP3 it fell into neither and rendered nothing.
- New additive branch — `data.py:4612` — bucketing `poly_kalshi_*` slugs, `WHERE kind='poly_kalshi_order'` (`data.py:4621`), `status IN ('placed','DRY_RUN_would_place')`, `action='entry'`, `$.division IN (slugs)`, `LEFT JOIN kalshi_round_trips ... r.order_id IS NULL`. Field mapping: `fill_count`→qty (falls back to requested `count`), `fill_price`→entry_price (falls back to limit `price`), `outcome`→outcome_bet, `whale`→whale_handle. The 6-actor arb query above is left **byte-identical**.
- **Why Flag 1 must land here:** the open/resolved gate is the `order_id` LEFT JOIN. Once CP4's resolver composes a `kalshi_round_trips` row keyed by `order_id`, the entry drops off OPEN. Without Flag-1's persisted `order_id`, a placed row has no join key. Proven by `test_open_trades_poly_kalshi_resolved_drops_off_open`.

## Evidence (empirical)
- **Executor suite:** `tests/test_poly_kalshi_executor.py` → **25 passed** (20 existing + 5 new Flag-1: pure extractor incl. NO-leg + zero-fill, live-submit-journals-real-fill, dry-run-has-division-no-fill).
- **Open-query:** 6 new tests in `tests/test_prediction_markets_dashboard.py` (modeled on the 3 real 2026-08-16 fills MIA/CIN/AZ incl. the opposite-side same-game pair) → **6 passed**.
- **poly_kalshi suite:** 3 files direct → **56 passed / 0 failed**; `-k poly_kalshi` across `tests/` → **69 passed / 0 failed** (the only 3 errors are a pre-existing `FakeMacroExpert` ImportError in unrelated research files — not touched by me).
- **No regression on the 10 known dashboard failures:** with-changes run of the full dashboard file = **exactly those 10** (all cutoff-fixture family: `round_trips`/`build_view`/`all_mode`/`extra_json`), my 6 pass. **Stash-proof:** stashing my 4 files → pristine `2f11ffa` still fails the same family → the 10 are pre-existing (CP5's job per handoff), not introduced here.
- **Shared files:** `git diff --stat origin/prod-live` on the 3 shared files → empty.

## Decisions & forks for your review
1. **FORK — OPEN badge count (`_query_pm_pending_count`) NOT updated.** CP3 as scoped names only `_query_pm_open_trades`, so I did not touch the sibling `_query_pm_pending_count` (`data.py:~4603`). But the codebase treats them as a matched pair (explicit "badge == list" comments), so the poly_kalshi OPEN tab will list N rows while the summary badge shows 0. I did **not** auto-expand scope. **Your call:** fold the parallel `COUNT(*)` block into CP3 (same additive pattern, ~15 LOC, ready to apply) or defer. I recommend folding it in for a coherent "surface OPEN" checkpoint.
2. **status filter accepts `placed` + `DRY_RUN_would_place`** — the live division emits `placed`; `DRY_RUN_would_place` is included so a paper/shadow run also renders (matches the plan's "shadow/paper row renders" wording). `blocked_*`/`skip`/`suppressed` and `action='exit'` are excluded (not open positions).
3. **Flag-1 extraction is inline, not an import of `fill_event_from_v2_response`** — preserves the executor's deliberate "independent placement path" design and avoids importing the parser's `KalshiNoFill`-on-zero-fill control flow (which would expand 0-fill semantics). Values are provably identical to the canonical parser for this always-YES strategy (cross-referenced in-code).

## NOT done (per checkpoint discipline — do not proceed without your go)
- **CP4** (resolver adapter for `kind='poly_kalshi_order'` + the realized-P&L reconciliation HARD GATE) — not started. Flag 1 now provides the real fill price/qty the gate needs.
- **CP5** (kalshi agent_state epoch; must also fix/avoid worsening the 10 pre-existing failures), **CP6** (epoch reset, operator-run), **CP7** (deploy, operator-run) — not started.
- **Phase 2** — not started.

## Next
Your review of this diff. On go: I fold in the badge-count block if you want it, then start CP4.
