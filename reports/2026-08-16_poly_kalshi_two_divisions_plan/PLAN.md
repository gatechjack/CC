# Plan — Split Poly→Kalshi copy into TWO divisions, TWO dashboards

> **Planning artifact for operator review. No build. Live division stays ARMED and untouched.**
> Shared files `kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py` stay byte-unchanged.
> All prod mutations are operator-run (hand runners; agent does not execute).

## Context — why this change

The repurpose left one division doing two jobs and one dashboard showing the wrong thing:

- **Double-state bug (live today):** the paper sim `polymarket_copy_trader` (`enabled:true, auto_execute:false`, strategies.yaml:1-3) and the live `poly_kalshi_mlb` (`auto_execute:true`, strategies.yaml:1785) **both read the same roster** `agent_state[polymarket_copy_trader/selected_whales]` (poly_kalshi_copy_trader `_load_roster`, roster_key=selected_whales strategies.yaml:1799). So the 4 live whales are being **paper-traded on Polymarket AND live-traded on Kalshi simultaneously** — exactly the "one whale in two states" failure the operator wants gone.
- **No live visibility:** `poly_kalshi_mlb` writes `audit_event(actor='poly_kalshi_mlb', kind='poly_kalshi_order')` only (executor `_record`, poly_kalshi_executor.py:300-318) and computes realized P&L via the settlement sweep into `StrategyState.realized_pnl` (poly_kalshi_copy_trader `run_settlement_sweep`/`record_realized`). It has **no round_trips, is not a registered division, and appears nowhere in the web layer** (grep of `trading_corp/web/` for `poly_kalshi` = empty). The operator cannot see the first live fill or live P&L.
- **Dashboard shows stale paper:** the "poly copy" dashboard (`polymarket_copy_trading` division) is a still-active paper sim writing `polymarket_round_trips` (156 resolved + 97 entered in the last 24h; on-disk 9,701 rows / +$205.31; an epoch is already set at `2026-07-07`).

**Outcome wanted:** two clean divisions — **A: PCT (paper) proving ground** (watchlist + paper P&L) and **B: Poly Kalshi Copy (live)** (live roster + live P&L only) — two dashboards, one whale in exactly one state at a time, both dashboards reset to a fresh split-date epoch (reversible, history retained on disk).

---

## Scoping findings (read-only, with evidence)

### How the Prediction Markets dashboard sources data
- Divisions are classified `prediction_markets` if `broker ∈ {polymarket, kalshi}` **or** slug starts with `polymarket_`/`kalshi_` (`trading_corp/utils/divisions.py:106-113`). **A new division with `broker: kalshi` auto-classifies — no prefix-tuple edit needed** (corrects an Explore over-claim).
- View builder `build_prediction_market_view` (`trading_corp/web/data.py:5813`) selects divisions via `_pm_divisions_all` (`data.py:4134`) and fans out to per-division queries. Routes: `/prediction-markets/` and `/prediction-markets/{division}` (`trading_corp/web/routes.py:~321-372`); template `prediction_markets_dashboard.html`.
- Cards read **two tables**: `polymarket_round_trips` and `kalshi_round_trips`, filtered `WHERE division IN (...)`:
  - resolved tiles `_query_pm_resolved_stats` (`data.py:4683`), history `_query_pm_round_trips` (`data.py:4143`), open `_query_pm_open_trades` (`data.py:4391`), equity `_query_pm_equity_curve` (`data.py:4321`, reads `kalshi_equity_history` for kalshi slugs).

### What `poly_kalshi_mlb` already computes (do NOT rebuild — surface it)
- **Realized P&L:** settlement sweep reads Kalshi `get_settlements().pnl_dollars`, feeds `record_realized` → `StrategyState.realized_pnl` / `realized_pnl_day` (persisted, drives the $100 halt). This is the source of truth for live realized P&L.
- **Fills/journal:** every order writes `audit_event(actor='poly_kalshi_mlb', kind='poly_kalshi_order')` with `ticker, side(=v2 yes/no), count, stake_usd, price, order_id, whale, whale_wallet, confidence, deployed_usd_after, orders_today_after` (poly_kalshi_executor.py:300-309).
- **Config drift to fix:** strategies.yaml:1788 says `division: polymarket_copy_trading`, but the field is **inert** — main.py constructs the executor with `strategy="poly_kalshi_mlb"` hardcoded (main.py:1498), so audit actor + halt key are `poly_kalshi_mlb`. The yaml field should be corrected to `poly_kalshi_mlb` (documentation-only today; load-bearing once a resolver keys off it).

### The reuse gap (the real cost — corrects the "~20 LOC" Explore estimate)
The `kalshi_round_trips` path is reusable **but poly_kalshi_order does not conform** to what the shared queries expect:
- Resolver `kalshi_resolver.py` reads `audit_event WHERE actor=? AND kind IN ('would_have_placed','kalshi_copy_placed_live')` (line 157) and `_compose_round_trip` expects `fill_qty/fill_price/leg_priced` or `qty/limit_price` (lines 206-219). poly_kalshi_order carries `count`/`price`/`side(yes/no)` under `kind='poly_kalshi_order'` — **different kind and field names**.
- Open-trades query (`data.py:4508-4534`) hard-codes `actor IN (6 kalshi actors)`, `kind='would_have_placed'`, `$.side='buy'`, `$.division`, `$.qty` — poly_kalshi_order has **none of $.division/$.qty and `$.side` is yes/no not buy**.
- **P&L model aligns:** the resolver books on settlement (`realized = qty*(1-price)` won / `-qty*price` lost, line 230), which matches poly_kalshi_mlb's hold-to-resolution copy (exit-copy is wired but whales hold, so it ~never fires). So a resolver adapter is dimensionally correct.

### Epoch mechanism
- Polymarket (PCT paper) epoch is **runtime + reversible via agent_state**: `_get_polymarket_metrics_epoch` reads `agent_state[polymarket_copy_trader/metrics_epoch]` (`data.py:4062`), applied by `_polymarket_cutoff_clause` (`data.py:4098`, div-scoped, `''`=no-op).
- Kalshi divisions epoch is a **hardcoded dict** `DASHBOARD_RT_CUTOFFS` (`data.py:3963`) via `_kalshi_cutoff_clause` (`data.py:3974`) — needs a code change + restart, **not** the agent_state mechanism the operator asked to mirror.

### Roster + atomicity
- Keys under actor `polymarket_copy_trader`: `selected_whales` (papered **and** live today — shared), `pinned_whales` (eviction-exempt), `watch_only_whales` (observation). Promote/demote endpoints in `routes.py` (~2943-3086) do **2-3 separate `set_agent_state` calls**; `set_agent_state` is a single-row autocommit upsert (`db.py:641-662`). A crash between calls leaves a whale half-moved.
- **Atomic primitive available:** `connect()` opens sqlite with `isolation_level=None` (autocommit, `db.py:611`), so an explicit `BEGIN IMMEDIATE … COMMIT` wrapping multiple upserts is a valid single transaction. No such multi-key helper exists yet.

---

## Recommendation: REUSE `kalshi_round_trips`, with an honest scoped adapter

Reuse the existing prediction-markets dashboard (register `poly_kalshi_mlb` as its own `broker: kalshi` division → new tab/page) rather than build a bespoke live view.

| Approach | What it costs | Gets |
|---|---|---|
| **Reuse `kalshi_round_trips` (RECOMMENDED)** | Division reg (yaml, ~6 lines, auto-classifies) + resolver adapter for `kind='poly_kalshi_order'` field-mapping (`count→qty`, `price→entry_price`, `side`, `order_id`, `_ACTOR_TO_*` entries) ~40-60 LOC + open-query additive branch ~20 LOC + agent_state epoch for kalshi ~25 LOC + optional equity snapshot loop + tests. **~120-160 LOC, one restart.** | Full reuse of tiles / history / open / equity UI. Consistent with the paper dashboard. |
| Bespoke live view | New route + template + query reading `poly_kalshi_order` + `StrategyState` directly, own normalizer ~200-280 LOC. | Self-contained (zero risk to the live paper dashboard) but duplicates UI and diverges. |

**Why reuse wins:** the settlement-based P&L already matches the resolver; the UI (tiles, history, open tab, equity curve) is fully reused; changes to the shared queries are **additive** (new actor/kind branches guarded by division slug). The corrected cost (~120-160 LOC, not ~20) is still well under bespoke and yields a consistent two-dashboard experience.

**One deliberate deviation for symmetry:** give the live kalshi division an **agent_state-driven epoch** (mirror `_get_polymarket_metrics_epoch` as `_get_kalshi_division_epoch(slug)`), so BOTH dashboards use the same reversible metrics_epoch mechanism the operator asked for — rather than the hardcoded `DASHBOARD_RT_CUTOFFS`.

---

## Phase 1 — Live dashboard + both epochs (SHIPPABLE TODAY, the urgent piece)

Goal: operator can SEE the first live fill when it lands + running live P&L; both dashboards reset to the split-date epoch. Independently shippable.

- **CP1 — Scoping sign-off (this doc).** Operator ratifies reuse + epoch approach.
- **CP2 — Register division B.** Add `poly_kalshi_mlb` to `config/divisions.yaml` (`broker: kalshi`, `secret_ref` = KAREN pair, `enabled:true`, `standby:false`); fix strategies.yaml:1788 `division: poly_kalshi_mlb`. Verify it appears on `/prediction-markets/` (empty tiles). No executor/loop change → live loop undisturbed.
- **CP3 — Surface OPEN live fills.** Add `division` to the poly_kalshi_order payload (executor `_record`, a NEW file — allowed) and extend `_query_pm_open_trades` kalshi branch to recognize `actor='poly_kalshi_mlb'` + `kind='poly_kalshi_order'` with field COALESCE (`count`→qty, `side` yes/no→outcome). Prove: a shadow/paper poly_kalshi_order row renders on the OPEN tab. **Gate: this is the "see the first fill" piece.**
- **CP4 — Surface RESOLVED live P&L.** Resolver adapter: recognize `kind='poly_kalshi_order'`, map fields, `_ACTOR_TO_DIVISION['poly_kalshi_mlb']='poly_kalshi_mlb'`; compose `kalshi_round_trips` on settlement. Reconcile the resolver's computed realized vs `StrategyState.realized_pnl` (both settlement-based — assert they agree in a test). Tiles (resolved/winrate/realized) + History populate.
- **CP5 — Agent_state epoch for kalshi divisions.** Add `_get_kalshi_division_epoch(slug)` (agent_state `[poly_kalshi_mlb/metrics_epoch]`) wired into the kalshi cutoff clause alongside `DASHBOARD_RT_CUTOFFS`. Reversible, runtime.
- **CP6 — Reset BOTH epochs to split date (operator-run).** Runner sets `agent_state[polymarket_copy_trader/metrics_epoch]=<split>` (PCT paper) and `agent_state[poly_kalshi_mlb/metrics_epoch]=<split>` (live). Verify: both dashboards read 0 from the epoch; on-disk rows retained (9,701 poly / N kalshi still present); reversible by deleting the keys.
- **CP7 — Deploy + verify.** Drift-gate + prod-live advance (operator-run runner). Verify live loop still ARMED/unhalted post-restart; first real fill renders on B's OPEN tab; A shows only post-split paper.

Deploy touches `divisions.yaml`, `strategies.yaml`, `data.py`, `kalshi_resolver.py`, `poly_kalshi_executor.py` (NEW file) + tests. **Shared files untouched.** One restart (resolver/dashboard/yaml are import/disk-read).

Optional within Phase 1 or fast-follow: wire `write_equity_snapshot(db_url,'poly_kalshi_mlb',broker)` into the snapshot loop for B's equity curve.

---

## Phase 2 — Atomic tier model + PCT-as-pure-paper (fast-follow)

Goal: one status per whale (`watched → papered ⇄ live`), enforced by an atomic cross-roster move; PCT stops papering live whales.

- **Roster split:** live roster = new key `agent_state[poly_kalshi_mlb/live_whales]` (point poly_kalshi's `roster_actor/roster_key` at it via strategies.yaml — config-only, no shared-file change). Paper roster = `polymarket_copy_trader/selected_whales`, and the paper sim must **exclude** any whale currently in `live_whales` (read-time subtract, or the move removes it). This ends the double-state.
- **Atomic move helper:** add `set_agent_state_multi(updates, db_url)` to `db.py` — one `BEGIN IMMEDIATE … upsert(k1) … upsert(k2) … COMMIT` (isolation_level=None already supports it). Promote(paper→live) and Demote(live→paper) each = a SINGLE `set_agent_state_multi([(…,paper_roster,after),(…,live_whales,after)])`. On failure → ROLLBACK → whale stays in exactly one state.
- **Verify atomicity:** unit test that a forced exception mid-move leaves the whale in exactly ONE roster (not both, not neither); an invariant check `live ∩ paper == ∅` after every move.
- **Semantics:** Promote → whale joins live, stops papering, live P&L starts fresh at $0 (its own division has no prior rows); paper history stays in `polymarket_round_trips` (behind the epoch, not moved/deleted). Demote → leaves live (live history retained under `poly_kalshi_mlb`), resumes papering.
- **Endpoints:** update `routes.py` promote/demote handlers to call `set_agent_state_multi` across the two rosters (currently 2-3 separate calls). Keep watchlist→paper promotion (operator action) in PCT.

---

## Epoch-reset mechanism (both dashboards, reversible)

Same primitive both sides: **agent_state metrics_epoch** (display cutoff, `''`/absent = no-op, history retained on disk).
- PCT paper: `agent_state[polymarket_copy_trader/metrics_epoch]` (exists today, `_polymarket_cutoff_clause`).
- Live: `agent_state[poly_kalshi_mlb/metrics_epoch]` (new `_get_kalshi_division_epoch`, CP5).
- Set both to the split date via an operator-run runner; reverse by deleting the keys.

## Atomic-move design (the core Phase-2 guarantee)

Failure mode to prevent: promote = "remove from paper" + "add to live" as two autocommit writes; a crash between them leaves the whale papering AND live (or neither). Fix: one transaction.
```
set_agent_state_multi([(actor,'selected_whales',paper_after),
                       ('poly_kalshi_mlb','live_whales',live_after)])  # BEGIN IMMEDIATE … COMMIT / ROLLBACK
```
Invariant asserted after every move and on boot: `set(live_whales) ∩ set(paper_roster) == ∅`.

## Constraints & operator boundary
- Shared files `kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py` — byte-unchanged (diff-verified each checkpoint).
- Live division stays ARMED/undisturbed during planning; Phase-1 CP2 is config-only; the loop restarts only at the CP7 deploy.
- Every prod mutation (epoch writes, deploy, restart) is handed as an az `@file` runner (RG-SHARED-PROD/tc-prod-vm) for the operator to run; agent verifies read-only.

## Backlog (capture, do not build)
- Rethink the watchlist + a multi-category Kalshi-matchable taxonomy (today B stays MLB-only / `KXMLBGAME`).
- Optional: generalize the kalshi agent_state epoch to all kalshi divisions (retire `DASHBOARD_RT_CUTOFFS`).

## Verification (end-to-end)
- **Phase 1:** `/prediction-markets/poly_kalshi_mlb` renders; a shadow poly_kalshi_order shows on OPEN; after a settled fill, resolved tiles + realized match `StrategyState.realized_pnl` (unit-asserted); both dashboards read 0 from the split epoch with on-disk rows intact; live loop ARMED/unhalted post-deploy; shared-file diff empty.
- **Phase 2:** promote moves a whale paper→live atomically (invariant `live ∩ paper == ∅`); forced-crash test leaves exactly one state; PCT no longer papers live whales; demote round-trips cleanly.
- Test suite green (poly_kalshi 62/62 + new resolver/open/epoch/atomic tests); `-p no:pytest_ethereum`.
