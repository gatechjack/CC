# Prediction Markets — Phase 2 Plan (Standalone Web App + Farm-League Lifecycle)

**Status:** ✅ BOARD-APPROVED FINAL 2026-08-23 (Jack). **Execution NOT started** — nothing built, deployed, or mutated. A fresh build agent executes from the branch copy `reports/prediction_markets/P2_PLAN.md`.
**Shape:** mirrors `P1_PLAN.md` (context → architecture → page/data spec → DDL → jobs → deploy → tests → acceptance → locked decisions → open questions).
**Branch discipline (inherited, §4/§13-9):** phase branch `prediction-markets-p2-2026-08-23` → durable `prediction-markets`; push early/continuously; **NO merge to `main` until the single P3 cutover**; `prod-live` advances for deployed artifacts only; **confirm the current prod-live tip with Jack before branching — never a remembered SHA.**

---

## 1. Context

P1 is DEPLOYED + LIVE: `data/prediction_markets.db` holds ~28.3k resolved rows across 12 whales, cost-basis ROI on two ranking routines, zero engine coupling. It has **no front end** — the only interface is `pm_cli report` (text) and raw SQL. Every P1 defect (the PK collapse, the notional-denominator error, the clause-(a) misfire, the AIisTheNewWD mirage) was found by drilling from a summary to the underlying rows by hand.

P2 puts a **separate website** on top of that standalone system — the farm-league product (search → watchlist → analyze → pin → paper) plus a diagnostics surface — so the farm can be reviewed, pinned, and paper-observed through a UI instead of the CLI, and so the caveats that make a number trustworthy (two-sided share, single-game share, chalk/contested, $-weighted data-quality) sit on the product page next to the number. P2 owns everything up to but **not including** promote-to-live (P3).

The intended outcome: the weekly evaluation loop (review the farm, decide pins, decide promotions) runs in a browser, every ranked number drills through to its rows, and the store's freshness is never misread.

---

## 2. Rulings that frame P2 (locked — do not relitigate)

From the planning brief: **(R1)** separate website, own process/hostname, touches only `prediction_markets.db`, no engine imports, additive + restart-free, execution stays shared (P3). **(R2)** two clocks kept separate. **(R3)** real product tabs + a diagnostics tab. **(R4)** caveats on the product page, mechanics in diagnostics. **(R5)** every aggregate drills through to its rows. **(R6)** fancy backlogged (no sparklines/animations/notifications).

**The four board rulings of 2026-08-23 (this session):**

- **RULING A — WEEKLY full refresh, not nightly; do NOT build incremental; do NOT touch `ingest.py`.** Resolved history is immutable; re-pulling ~28k settled rows nightly for a ~+16-row delta is waste, and the evaluation loop is weekly. This *dissolves* the incremental-refresh question. The 03:20 UTC **daily** cron line is replaced by a **weekly** schedule. Paper-trade **entry capture** is carved out as a separate, faster job (§7.2).
- **RULING B — MIGRATE the roster into `prediction_markets.db`.** New `pm_roster`/`pm_watchlist` are source of truth; the site owns pin/unpin/watchlist writes. The one-time import from legacy `agent_state` is a **convenience seed, not a link** — no reads back after. Post-import drift between the legacy PCT farm and the PM farm is **expected and fine** (two independent systems until cutover). The site **never writes the legacy DB**. Legacy code may be **copied/reused** (reuse ≠ coupling).
- **RULING C — AUTHELIA (already exists), not in-app auth.** `trading.jacksumner.com` is already behind Authelia; `predictions.jacksumner.com` goes behind the same instance with per-domain rules. **Do NOT build `pm_user`/`pm_role`/`pm_grant` tables** — superseded; Authelia owns identity. `pm_user` (Authelia login) ≠ `pm_account` (Kalshi API, P3); the mapping between them is the future access model.
- **RULING D — paper trades HOLD TO RESOLUTION; STALE if the whale exits early.** STALE is **visible and counted** (`n_stale` beside `n_resolved` on the product page) — an unseen exclusion is survivorship you can't audit.

---

## 3. Architecture

### 3.1 A second, fully-decoupled process (R1)
The existing dashboard is an asyncio task **inside the engine process** (`trading_corp/web/app.py` built with live `WebDeps`; its Polymarket-analyze and Kalshi-discover jobs `asyncio.create_task` on the **engine loop**). P2 must not do that — a search firing thousands of API calls on the engine loop, or a UI restart, would blip MACE/PEAD/bitunix/poly_kalshi.

**`pm_web`** = a standalone ASGI app under `trading_corp/prediction_markets/web/`, launched by `trading_corp/scripts/pm_web.py` (uvicorn). Constructed with **no `WebDeps`, no agent handles, no `main.py` wiring** — its only inputs are `PM_DB_PATH` and (for search/analyze) an `httpx` `PolymarketDataAPIClient`. It reuses the web *idioms* (Jinja2, HTMX, the `db.connect` + `asyncio.to_thread` read pattern, the numbered-migration discipline) but not the process. P1's `db._assert_not_legacy` already hard-fails if anything points the PM layer at `trading_corp.db`; P2 extends that isolation to the web tier.

### 3.2 Three cadences, three data jobs (R2, generalized by Ruling A)
R2's "two clocks" becomes **three distinct jobs, three sources, three cadences** — kept separate:

| Job | Cadence | Source | Writes | Purpose |
|---|---|---|---|---|
| **Weekly refresh** (evaluation) | **weekly** (Sun 09:00 UTC, §7.1) | `/closed-positions` (all roster) | pm_closed_position, rollup → pm_category_stats + onesided + paper_category_stats, scores | the scoreboard; immutable resolved history |
| **Paper-entry capture** | **~30 min** (§7.2) | `/activity` (PINNED whales only) | pm_paper_trade (open rows, real entry_ts) | catch entries while the market is open |
| **Live marks** | seconds (P3, not built) | Kalshi marks on OUR open positions | — | live trade cockpit; plan the poller slot only |

The scoreboard is on the **weekly** clock; the UI shows the refresh timestamp prominently everywhere scoreboard data appears (§6.0). Paper-entry capture is the only sub-weekly writer P2 ships.

```
Engine process (UNTOUCHED)            pm_web process (NEW, azureuser)         data/prediction_markets.db
  trading_corp/web  (trading.…)         uvicorn …web.app:app  (predictions.…)   (the ONLY DB pm_web touches)
  reads/writes trading_corp.db          reads mostly; small writes ───────────►  pm_watchlist/pm_roster/
                                        in-proc search worker (own loop)          pm_paper_trade/pm_analysis_cache
                                        shells `pm_cli backfill --only-wallets`
  Authelia (proxy) ── per-domain gate ── predictions.jacksumner.com → :8081
  weekly cron (Sun) ─ pm_cli refresh ───────────────────────────────────────►  (writer #2)
  30-min cron ─────── pm_cli paper-poll ─────────────────────────────────────►  (writer #3, pinned only)
```

---

## 4. Data contract (what the UI consumes)

**Existing tables (read):** `pm_category_stats` (n_resolved, wins, win_rate, net_realized_pnl, cost_basis, `roi` [cost, RANKED], roi_notional, avg_bet, avg_win_price, n_excluded, n_anomaly, dq_count_pct, **dq_dollar_pct**, data_quality), `pm_score_snapshot` (score, wilson_lcb, edge_factor, params_json w/ `recency_basis`), `pm_closed_position` (per-row incl `outcome_index`, slug, event_slug, `category_source`, pnl_suspect, suspect_reason, pnl_anomaly), `pm_whale` (backfill_complete, last_pulled, last_stored, last_refresh_ts), `pm_open_position`.

**The read entry point is `stats.query_scoreboard(conn, category, routine, min_resolved)`** → returns those columns joined to the routine's snapshot + `backfill_complete`, sorted `score DESC, roi DESC`, with `chalk`/`contested` derived. Flags (canonical in `format_report`, mirrored as badges): CHALK (avg_win_price≥0.85), CONTESTED (<0.70), CONTAMINATED (dq>10%), INCOMPLETE-NOT-RANKED (backfill_complete≠1), ANOM:n. The HTML view renders **the same flag set** as the CLI so product and CLI never diverge.

---

## 5. New tables (additive migrations 004–007) + where each analytic computes

Follows `db.py`'s exact numbered/idempotent pattern (`MIGRATIONS += [(4,…),(5,…),(6,…),(7,…)]`, `IF NOT EXISTS`, one txn each, `schema_version` bump). **No existing table is rebuilt** (no PK change needed). Test literals in `test_db.py` (`count/maxv == 3`) bump to 7. **Auth tables are NOT created (Ruling C).**

### 5.1 Migration 004 — caveat analytics (all computed in `stats.rollup()`, the one existing GROUP BY pass; NOT query-time)
```sql
-- 004: caveat analytics as first-class rollup columns (Jack: "if it's analytics the app should compute,
-- it belongs IN THE APP"). Additive; next rollup() backfills every row.
ALTER TABLE pm_category_stats ADD COLUMN n_condition_ids   INTEGER NOT NULL DEFAULT 0; -- COUNT(DISTINCT condition_id), ALL rows
ALTER TABLE pm_category_stats ADD COLUMN n_two_sided       INTEGER NOT NULL DEFAULT 0; -- condition_ids held on >1 outcome_index
ALTER TABLE pm_category_stats ADD COLUMN two_sided_pct     REAL    NOT NULL DEFAULT 0; -- n_two_sided/n_condition_ids (hedging/MM tell, §13A(j))
ALTER TABLE pm_category_stats ADD COLUMN n_single_game     INTEGER NOT NULL DEFAULT 0; -- rows dated \d{4}-\d{2}-\d{2} AND not futures
ALTER TABLE pm_category_stats ADD COLUMN n_futures_like    INTEGER NOT NULL DEFAULT 0; -- rows w/ champion|mvp|winner|to-win|title|division|conference|season|playoff|...
ALTER TABLE pm_category_stats ADD COLUMN single_game_pct   REAL;                        -- n_single_game/total; HEURISTIC; NULL for non-sports (Fed) — see OQ-2
ALTER TABLE pm_category_stats ADD COLUMN market_type_source TEXT DEFAULT 'slug_heuristic'; -- seam: 'slug_heuristic'(P2) | 'gamma_market_type'(later)

-- one-sided directional slice = the copyable signal (upper bound, survivorship-caveated). Companion table,
-- keyed 1:1, so query_scoreboard LEFT JOINs it (mirrors the stats/score layering; avoids doubling core width).
CREATE TABLE IF NOT EXISTS pm_category_onesided_stats (
    wallet TEXT NOT NULL, category TEXT NOT NULL,
    n_resolved INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
    win_rate REAL, net_realized_pnl REAL NOT NULL DEFAULT 0, total_bought REAL NOT NULL DEFAULT 0,
    cost_basis REAL NOT NULL DEFAULT 0, roi REAL, avg_bet REAL, avg_win_price REAL, last_resolved_ts INTEGER,
    is_upper_bound INTEGER NOT NULL DEFAULT 1,   -- ALWAYS 1: excludes hedged markets => optimistic, survivorship-caveated
    updated_ts INTEGER, PRIMARY KEY (wallet, category));
CREATE INDEX IF NOT EXISTS ix_pm_cos_category_roi ON pm_category_onesided_stats(category, roi DESC);
```
- **two_sided_pct**: extra grouped subquery `COUNT(DISTINCT outcome_index) per (wallet,category,condition_id)` → fraction >1, over ALL rows.
- **single_game_pct**: a pure helper `category.classify_market_shape(slug,event_slug,title)` (regex, offline-testable) applied in rollup()'s row loop; **bias-down** = `ambiguous` counts as NOT single-game (a floor). NULL for non-sports categories (Fed has no single-game notion).
- **one-sided slice**: second aggregate over condition_ids with a single outcome_index, scoreable rows only (`db.scoreable_where()`), P1 stat family → `pm_category_onesided_stats`.
- **market_type**: only the `market_type_source='slug_heuristic'` seam ships in P2; the reliable discriminator (`sportsMarketType=='moneyline'` + `gameStartTime`) is market-level, absent from `/closed-positions`, and capturing it means a gamma call per market — deferred (§13A(c)/(d)); the seam lets a later additive migration add a real `pm_closed_position.market_type` and flip the flag.

### 5.2 Migration 005 — paper trading (Ruling D)

> **AMENDMENT 2026-08-24 (CP3a build, branch `prediction-markets-cp3a-2026-08-24`; see
> `CP3A_CONTAMINATION_GATE.md`).** This section's DDL predates the CP3a rulings and is superseded on three
> points; the shipped `db.py` `MIGRATION_005` is authoritative:
> 1. **Entry is OBSERVATION-provenance, not a fill.** The poller reads `/positions` (which carries **no
>    fill timestamp**), not `/activity`. The entry-time column is **`entry_observed_ts`** (observation time
>    +/- the poll interval); there is **no `entry_ts`** column or alias. Recency basis is observation-time.
> 2. **The lifecycle gained `pending_adjudication`** (a vanished position is not classified on the
>    disappearance) plus `whale_size_at_observation` (display-only), `close_source`, and scale-in/reduction
>    observation columns (`n_observed_adds`/`n_observed_reductions`, diagnostic-only).
> 3. **`pm_paper_category_stats` and `pm_paper_score_snapshot` are DEFERRED to CP3b**, not migration 005.
>    Migration 005 ships `pm_paper_trade` + `pm_paper_config` only (never a stats column ahead of its
>    deriver — the inverse of the `_STATS_COLS` trap). The paper-stats DDL below is CP3b design reference.

```sql
-- 005: FORWARD paper-trading of pinned whales (§6 non-preclusion). Grain = one paper POSITION per copied
-- directional entry. Carries REAL entry_ts (distinct from resolved_ts) => entry-basis recency is first-class.
CREATE TABLE IF NOT EXISTS pm_paper_trade (
    wallet TEXT NOT NULL, category TEXT NOT NULL, condition_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL DEFAULT 0,     -- in PK: two-sided legs preserved (parity w/ dec 12)
    slug TEXT, event_slug TEXT, title TEXT, outcome TEXT,
    side TEXT NOT NULL DEFAULT 'BUY',             -- the whale action copied (direction)
    entry_ts INTEGER NOT NULL,                    -- REAL entry unix ts (from /activity fill) => recency_basis=entry_ts
    entry_price REAL, size_basis REAL,            -- paper stake: FIXED UNIT (Ruling OQ-1) so whales are COMPARABLE;
                                                  -- the whale's own bet size is a DATA POINT TO DISPLAY, not a sizing input
                                                  -- (mirroring it imports the whale's bankroll into the signal). Per-sub-division sizing = P3.
    cost_basis REAL,                              -- size_basis*entry_price (ROI denom parity, dec 11)
    mark_price REAL, mark_pnl REAL, mark_ts INTEGER, -- weekly mark (informational)
    resolved_ts INTEGER, realized_pnl REAL, won INTEGER,
    status TEXT NOT NULL DEFAULT 'open',          -- 'open'|'closed'|'stale'(whale exited pre-resolution)|'void'
    pnl_suspect INTEGER NOT NULL DEFAULT 0, suspect_reason TEXT, -- §3A parity (row_invariant|no_cost_basis; event-group N/A)
    source TEXT, pinned_ts INTEGER, opened_ts INTEGER NOT NULL, updated_ts INTEGER,
    PRIMARY KEY (wallet, category, condition_id, outcome_index));
CREATE INDEX IF NOT EXISTS ix_pm_pt_wallet_cat ON pm_paper_trade(wallet, category);
CREATE INDEX IF NOT EXISTS ix_pm_pt_status     ON pm_paper_trade(status);
CREATE INDEX IF NOT EXISTS ix_pm_pt_entry      ON pm_paper_trade(wallet, category, entry_ts DESC);

-- paper SCOREBOARD (pm_category_stats analogue), keyed 1:1. n_stale is FIRST-CLASS (Ruling D: visible+counted).
CREATE TABLE IF NOT EXISTS pm_paper_category_stats (
    wallet TEXT NOT NULL, category TEXT NOT NULL,
    n_closed INTEGER NOT NULL DEFAULT 0,          -- scoreable CLOSED (held-to-resolution) paper trades
    n_open INTEGER NOT NULL DEFAULT 0,            -- current open exposure
    n_stale INTEGER NOT NULL DEFAULT 0,           -- whale-exited-early, EXCLUDED from realized but SHOWN (survivorship audit)
    wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0, win_rate REAL,
    net_realized_pnl REAL NOT NULL DEFAULT 0, open_mark_pnl REAL NOT NULL DEFAULT 0,
    total_bought REAL NOT NULL DEFAULT 0, cost_basis REAL NOT NULL DEFAULT 0, roi REAL, avg_bet REAL, avg_win_price REAL,
    first_entry_ts INTEGER, last_entry_ts INTEGER, last_resolved_ts INTEGER,
    n_excluded INTEGER NOT NULL DEFAULT 0, excluded_pnl REAL NOT NULL DEFAULT 0, data_quality TEXT,
    updated_ts INTEGER, PRIMARY KEY (wallet, category));
CREATE INDEX IF NOT EXISTS ix_pm_pcs_category_roi ON pm_paper_category_stats(category, roi DESC);

-- OQ-5 (Ruling): paper scores go to a SEPARATE table, NOT pm_score_snapshot with a source tag. Paper is
-- ENTRY-BASIS + FORWARD-ONLY; external is RESOLUTION-BASIS + HISTORICAL. One table with a tag invites exactly
-- the conflation P1 spent the whole build fighting. Keep them physically separate.
CREATE TABLE IF NOT EXISTS pm_paper_score_snapshot (
    wallet TEXT NOT NULL, category TEXT NOT NULL, routine TEXT NOT NULL, -- net_roi | recency_weighted
    score REAL, wilson_lcb REAL, edge_factor REAL,
    params_json TEXT,                             -- recency_basis='entry_ts', half_life_days, n_eff, min_resolved
    computed_ts INTEGER, PRIMARY KEY (wallet, category, routine));
CREATE INDEX IF NOT EXISTS ix_pm_pss_cat_routine_score ON pm_paper_score_snapshot(category, routine, score DESC);
```
Lifecycle in a **new** `prediction_markets/paper.py` (additive; imports §3A helpers from `ingest.py`, does NOT edit it): **OPEN** = paper-entry job (§7.2) inserts on a pinned whale's new `/activity` BUY in the pinned category; **MARK/CLOSE/STALE** = weekly refresh (resolution is not time-critical; a whale with an open paper position that no longer appears in `/positions` and has not resolved → `status='stale'`); **paper_rollup** mirrors `stats.rollup()` over `status='closed'` scoreable rows → `pm_paper_category_stats`. Entry-basis recency becomes real: `compute_scores(..., recency_basis='entry_ts')` over `(won, entry_ts)`.

### 5.3 Migration 006 — roster + watchlist + search-run (Ruling B)

> **AMENDMENT 2026-08-24 (CP3a build).** Two changes; the shipped `db.py` `MIGRATION_006` is authoritative:
> 1. **Migration 006 = `pm_roster` + `pm_watchlist` ONLY.** `pm_search_run` is deferred to its own LATER
>    migration (CP3b search) and does NOT land in 006. The `pm_search_run` DDL below is CP3b design
>    reference only.
> 2. **Category attribution — RULED 2026-08-24 by Jack (advisor ruling C2.4 REVERSED).** The watchlist
>    (paper-traded set) is **EVERY `(wallet, category)` pair in `pm_category_stats` for the migrated legacy
>    whale set** — with **NO minimum-resolved floor** (an n=3 pair is a watchlist pair; curation is the
>    board's job at pin time, not the migration's), **`'unknown'`-category pairs INCLUDED** (they paper-trade
>    like any other until Jack refines them), and **ALL categories live for paper** (MLB/UFC/NBA/Fed do NOT
>    restrict the farm; cs2/ucl/soccer/epl/nhl/fifwc/nfl/unknown all paper-trade — real money still needs a
>    P3 account-category attachment, so paper breadth costs nothing). This is BROADER than Ruling B's original
>    wording just below ("the whale's **dominant** `pm_category_stats` category") — it is **every combo, not
>    the dominant one**. `paper.seed_farm_roster` generates the pairs from `pm_category_stats`; nothing is
>    "unresolved" (every pair exists by definition). The earlier advisor ruling **C2.4** (seed from scout
>    provenance, halt-on-unresolved) was made without having read this plan, conflicted with Ruling B, was
>    partly justified by an invented example, and has been **REVERSED** — see `CP3A_CONTAMINATION_GATE.md`.
>    `config/pm_farm_pin_provenance.yaml` stays on disk as the historical scout attribution but is **NOT read
>    at seed time**.

```sql
-- 006: FARM ROSTER as PM-DB source of truth (Ruling B). Decouples site + weekly refresh from legacy
-- agent_state. One-time convenience-seed import from legacy; NO reads back after. Site NEVER writes legacy DB.
CREATE TABLE IF NOT EXISTS pm_roster (
    wallet TEXT NOT NULL, category TEXT NOT NULL, user_name TEXT,
    source TEXT,                                  -- 'legacy_seed'|'search'|'manual'|'seed_yaml'|'cli'
    added_ts INTEGER, active INTEGER NOT NULL DEFAULT 1, notes TEXT,
    PRIMARY KEY (wallet, category));              -- one row per (wallet,category); universal farm key
CREATE INDEX IF NOT EXISTS ix_pm_roster_category ON pm_roster(category);

-- 006: per-category WATCHLIST. status='watchlist' = candidate (Analyze-able, NOT paper). status='pinned' =
-- forward paper-trading (this row IS the "one paper record per whale-category"; pm_paper_trade holds detail).
CREATE TABLE IF NOT EXISTS pm_watchlist (
    wallet TEXT NOT NULL, category TEXT NOT NULL, added_ts INTEGER, source TEXT,
    status TEXT NOT NULL DEFAULT 'watchlist',     -- 'watchlist'|'pinned'
    pinned_ts INTEGER, search_run_id INTEGER, updated_ts INTEGER,
    PRIMARY KEY (wallet, category));
CREATE INDEX IF NOT EXISTS ix_pm_watchlist_cat_status ON pm_watchlist(category, status);

-- 006: SEARCH RUN ledger (productized scout). Background job progress + API-budget visibility.
CREATE TABLE IF NOT EXISTS pm_search_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, filters_json TEXT,
    status TEXT NOT NULL DEFAULT 'running',        -- 'running'|'backfilling'|'finished'|'failed'|'cancelled'|'rate_limited'
    started_ts INTEGER NOT NULL, finished_ts INTEGER,
    n_api_calls INTEGER NOT NULL DEFAULT 0, n_found INTEGER NOT NULL DEFAULT 0, n_added INTEGER NOT NULL DEFAULT 0,
    est_cost_usd REAL NOT NULL DEFAULT 0, error TEXT, updated_ts INTEGER);
CREATE INDEX IF NOT EXISTS ix_pm_search_run_cat_status ON pm_search_run(category, status);
```
**Roster-source transition (Ruling B, additive + reversible):** a one-time idempotent `pm_cli migrate-roster` reads legacy `agent_state` (existing read-only `read_agent_state`) + seed yaml → `INSERT OR IGNORE` into `pm_roster` (`source='legacy_seed'`). **After that, ingest's roster source flips to `pm_roster WHERE active=1`; there are NO reads back to legacy.** Category attribution: derive from the legacy source slot (`poly_kalshi_mlb`→mlb) or the whale's dominant `pm_category_stats` category; multi-category whales get one row per category. **Documented, not solved (Ruling B second-order effect):** the legacy PCT paper farm and the new PM paper farm accrue **separate** records from the import forward — at cutover there will be two partial paper records per whale, not one continuous one. Expected; nobody should be surprised the new farm shows less history.

### 5.4 Migration 007 — analyze cache
```sql
-- 007: ANALYZE cache (LLM costs money/call). Mirrors legacy polymarket_whale_audit_cache; first-class table
-- w/ cost/token accounting so the daily cost strip + cost-guard are a query, not a side channel.
CREATE TABLE IF NOT EXISTS pm_analysis_cache (
    wallet TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', -- per whale-category; '' = whale-wide
    input_hash TEXT NOT NULL,                     -- hash(max(resolved_ts) | model_id | prompt_version | params)
    activity_max_ts INTEGER, narration TEXT, verdict TEXT, null_reason TEXT, -- null_reason: never silent
    llm_cost_usd REAL NOT NULL DEFAULT 0, tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
    model_id TEXT, computed_ts INTEGER NOT NULL, PRIMARY KEY (wallet, category, input_hash));
CREATE INDEX IF NOT EXISTS ix_pm_analysis_wallet_cat ON pm_analysis_cache(wallet, category, computed_ts DESC);
```

---

## 6. Page-by-page spec

### 6.0 Shell + the freshness contract
Own `pm_base.html` (trimmed copy of `web/templates/base.html`: Tailwind Play CDN or **vendored** — see §10; HTMX **vendored** to `/static`; dark palette; money/pct/compact filters re-registered locally). Nav: **Scoreboard · Farm League · Whale (drill target) · Search · Diagnostics**. Read pattern reused verbatim from `mace_view`: every handler `await asyncio.to_thread(fn)` opening a short-lived read connection, honest-empty ("—") for anything it can't show. A **`refresh_band()` Jinja macro** at the top of the content block renders `MAX(last_refresh_ts)` as "Scoreboard as of <ts> (<age>) · weekly refresh" — **green < 8 days, amber ≥ 8 days (missed a week), red ≥ 15 days** (thresholds moved from P1's daily assumption to Ruling A's weekly). Impossible to render a scoreboard number without the stamp beside it.

### 6.1 Scoreboard — `GET /`
`stats.query_scoreboard(category, routine, min_resolved)` rendered. Controls (plain `hx-get` re-render of `#scoreboard-table`, no client sort lib): category dropdown, routine toggle (net_roi | recency_weighted), min_resolved. **Columns** (caveats inline, R4): rank · wallet/name (→ whale detail) · category · score · wilson_lcb · edge · n_resolved (→ rows) · win% ("chalk indicator, NOT the rank key") · **ROI (cost)** the ranked metric · **ROI (notional)** muted "comparison only" (R3 side-by-side) · net_pnl · **avg_win_price** w/ CHALK/CONTESTED badge · **two_sided_pct** · **single_game_pct** · **$-wt data_quality** CONTAMINATED badge · flags (INCOMPLETE-NOT-RANKED, ANOM:n). **Drill-throughs (R5):** row → whale detail "why ranked"; `n_resolved` → the scoreable `pm_closed_position` rows; **each caveat cell → its rows** (avg_win_price → won rows by avg_price; two_sided → the both-outcome condition_ids; single_game → single-game rows; data_quality → the quarantined rows w/ suspect_reason, which deep-links into the diagnostics quarantine view — one shared row renderer `partials/pm_position_rows.html`).

### 6.2 Farm League — `GET /farm` (+ `/farm/{category}`)
Category sub-tabs (from `pm_roster` ∪ live categories), each with **two lists**:
- **WATCHLIST** (`pm_watchlist.status='watchlist'` ⋈ stats): the scoreboard product columns + per-row **[Analyze] [Pin]**. `[Analyze]`→ POST `/analyze/{wallet}` swaps a sibling `<tr>` (the ANALYZE precedent). `[Pin]`→ POST `/farm/{category}/pin/{wallet}` flips `pm_watchlist` to `pinned` + seeds the paper record.
- **PINNED PAPER LIST** (`pm_paper_category_stats` ⋈ pm_roster): product columns **+ paper columns** — entry-basis recency (real `entry_ts`), forward win%, forward net, **`n_stale` beside `n_resolved` (Ruling D, hard requirement)**, `n_open`. Per-row **[Analyze] [Unpin]**; a disabled **[Promote]** slot with a "P3" tooltip (lifecycle: pin mandatory; promote asks which sub-division — P3).

Every stat cell drills to `pm_position_rows.html` filtered to that whale-category (R5 applies to the farm page). Writes are single-row transactional (§9).

### 6.3 Whale detail — `GET /whale/{wallet}[/{category}]`
The "why is this whale ranked here" destination. **Header:** wallet/name, `backfill_complete` badge, per-whale freshness (`last_backfill_ts`/`last_refresh_ts`, `last_pulled` vs `last_stored`). **Score decomposition (on the product page, not diagnostics):** both routines side-by-side — `score = wilson_lcb × edge_factor` with `params_json` unpacked (recency_basis, half_life_days, n_eff, n_excluded) — so a rank is auditable. **Profile (all caveats, R4):** both scores; the one-sided directional slice (`pm_category_onesided_stats`) labeled UPPER BOUND; two_sided_pct; single_game_pct; avg_win_price chalk/contested; cost-ROI & notional-ROI side by side; data_quality; n_resolved/n_excluded/n_anomaly. **Position table** (`pm_position_rows.html`): resolved_ts, title/slug/event_slug, category + **category_source** (a mis-categorized row explains a weird stat), outcome/outcome_index, avg_price, total_bought, cost_basis, realized_pnl, won, pnl_suspect/suspect_reason + pnl_anomaly/anomaly_reason; default filter scoreable, a toggle reveals quarantined inline. Filterable target `GET /whale/{wallet}/{category}/positions?scoreable=&won=&two_sided=&suspect=`.

### 6.4 Search — `GET /search`
Form: category + basic filters (min_resolved, min cost-ROI, routine to rank previews, max candidates). Submit → `POST /search` launches the background job (§7.3), returns the "running" fragment. Progress panel `#search-status` polls `GET /search/status/{run_id}` (`hx-trigger="load, every 3s"`) reading `pm_search_run` (n_api_calls, n_found, status, est_cost); surfaces `rate_limited` distinctly (amber, "backing off — 429"); on completion, links to the category's watchlist. Two-phase status: `searching → backfilling → finished`.

### 6.5 Diagnostics — `GET /diagnostics` (R3; read-only, honest-empty, manual refresh not timer)
Sections: **(1) refresh health** — last weekly run (ts/duration/status) + per-wallet `backfill_complete` (complete|partial|failed) + `last_pulled` vs `last_stored` mismatch flag (cross-links to INCOMPLETE-NOT-RANKED on the product page). **(2) quarantined + flagged rows** — `pnl_suspect=1 OR pnl_anomaly=1` grouped by reason + $ excluded (same `pm_position_rows.html`, flags forced visible; the product-page data_quality drill-through lands here). **(3) category_source / coverage** — `GROUP BY category, category_source` (unknown fraction; tier-1 vs tier-2 split). **(4) cost-ROI vs notional-ROI side by side** — the measured clip-saturation caveat made observable. **(5) ingestion counts over time** — day buckets by resolved_ts/ingested_ts (CSS-width bars, no sparkline per R6). **(6) data-quality detail** — dq_count_pct vs dq_dollar_pct divergence.

---

## 7. Background jobs

### 7.1 Weekly full refresh (Ruling A) — replaces the 03:20 daily cron
`pm_cli refresh` (existing, unchanged — no `ingest.py` edit) + paper mark/close + `paper_rollup`, run **weekly**. **Proposed slot: `0 9 * * 0` (Sunday 09:00 UTC)** — justified against the box's live schedule captured this session (azureuser crontab 08:30 divergence + hourly :00 audit-replay; systemd timers cluster at 06:00–07:01 and 11:00–14:11; Sunday cron.weekly run-parts 06:47): 09:00 UTC is clear of the 06:xx maintenance burst, past the 08:30 divergence check, and well before the 11:00 PM-timer cluster; weekend low-load; a ~1 h run (est. ~1.5 min/wallet ⇒ ~18 min at 12, ~1 h at 40) finishes ~10:00 colliding with nothing. **MANDATORY at install (P1 discipline): re-verify the slot live (`crontab -u azureuser -l` + `systemctl list-timers --all`) immediately before installing — do not trust this remembered map.** Runtime scales with roster; note it, no cap needed (weekly + off-peak). Deploy: append-only crontab replace, backup first, azureuser (Ruling C/GOTCHA-1).

### 7.2 Paper-entry capture (the carve-out from Ruling A) — NEW, ~30 min
A small job — proposed **`*/30 * * * *` crontab line** (azureuser) running `pm_cli paper-poll` — that, for **PINNED whales ONLY**, pulls `/activity` since a per-whale high-water-mark and inserts `pm_paper_trade` open rows (entry_ts, entry_price from the fill; fixed `size_basis`; §3A row-level parity via imported helpers). **Why separate + faster than weekly:** an entry must be captured while the whale's record still surfaces it; `/activity` rows persist until the 5000-row truncation, so 30 min is safely ahead of truncation for pinned whales while keeping the paper record feeling live during a session. **Recommended interval 30 min (tunable, OQ-3).** Marking/closing/STALE ride the weekly job (resolution isn't time-critical). This is NOT the P3 live-marks poller (different source, different purpose).

### 7.3 Search — in-process asyncio worker on `pm_web` (NOT synchronous, NOT a separate process)
Replicates the proven Kalshi-discover pattern against `pm_search_run`: mark `running` before launch (single-flight per category — a second launch returns the running fragment), `asyncio.create_task` on `pm_web`'s **own** loop (disposable; cannot blip the engine), a process-wide `Semaphore` caps total concurrent searches. The task runs a category leaderboard sweep, ranks candidates with the **two existing routines on cost-ROI (win% is chalk, never the rank key)**, applies filters, lands survivors in `pm_watchlist` + `pm_roster`. **429-aware** (client backoff → transient `rate_limited` status, never a silent hang). **On `pm_web` startup, stale `running` rows older than a threshold → `error(interrupted)`** so a restart can't wedge single-flight. **Getting stats for found candidates (critical under weekly refresh):** the worker then shells `pm_cli backfill --only-wallets <found…>` as a **detached subprocess** (status `backfilling`), which runs the tested ingest+rollup+score path in its own process and per-wallet commits. **Why subprocess, not inline or wait-for-refresh:** inline bulk writes would break `pm_web`'s read-mostly property; wait-for-refresh is now up to a **week** away (Ruling A) and the lifecycle needs stats *now* for the Analyze/review step. This keeps `ingest.py` untouched (we call `pm_cli`, we don't edit it).

### 7.4 ANALYZE rewire
Reuses the live analyze route's shape (cache → miss → build audit → narrate → cache → render partial swapped into a sibling row), with three changes: **(1)** read the already-ingested `pm_closed_position` rows (untruncated) instead of `/activity` (network fallback only if the wallet isn't ingested yet); **(2)** cache in `pm_analysis_cache` keyed on `input_hash(max(resolved_ts)|model|prompt|params)` — invalidates exactly when new resolutions land; `?force=1` evicts; **(3)** visible cost — per-call fields already exist; add a daily strip `SUM(llm_cost_usd) WHERE computed_ts≥midnight` and a **cost-guard**: over `PM_ANALYZE_DAILY_USD` (**default $2**, Ruling OQ-6 — $5/day ≈ $150/mo would exceed the entire current ~$60/mo platform spend after Jack's Anthropic cost workstream; $2/day ≈ $60/mo ceiling), skip the LLM narration and render the free computed audit only (reasoned-null, `null_reason` set — never silent). Runs on watchlist AND pinned. Runs in `pm_web`, off the engine; no `audit_event` write (that's the engine's table; `pm_analysis_cache` is the PM ledger).

---

## 8. (folded into §7)

## 9. Concurrency
Writers on `prediction_markets.db`: the weekly cron, the 30-min paper-poll (pinned only), and `pm_web`'s small writes (pin/unpin, watchlist adds, analyze cache). **The site is read-mostly with single-row writes** — no bulk write in the request cycle (search's bulk write is the detached `pm_cli` subprocess). Safe via the P1 pragmas already in `db.py`: **WAL** (readers never block the writer), **busy_timeout=5000**, **synchronous=NORMAL** (short lock holds), autocommit + short transactions (multi-row search inserts wrapped in one `BEGIN IMMEDIATE`). Site write helpers wrap statements in the jittered DB-lock retry (`persistence/db._DB_LOCK_RETRY_DELAYS_SEC`) run inside `asyncio.to_thread` (never touches either event loop). The weekly writer commits per wallet, releasing the lock 12–40× — a site write lands in a gap within busy_timeout. Different processes, one WAL file; no fight.

## 10. Tech stack — KEEP server-rendered Jinja2 + HTMX + Tailwind, no build step
The P2 feature set is tables, tabs, cards, badges, buttons, drill-throughs, background-job progress — every one a first-class HTMX pattern already proven in-repo (mace tables, poly_kalshi `load, every Ns` polling, discover status-poll, analyze POST→partial-swap). **Sortable rankings** = a server round-trip (`hx-get sort=`), keeping the subtle score/roi tie-break ordering in one place (Python, matching `query_scoreboard`) — no client/server sort divergence. **Drill-throughs (R5)** are the strongest argument FOR this stack: the *same* server partial serves the product drill-through and the diagnostics view — one renderer, guaranteed consistency; an SPA would build that table twice. **No build step** keeps the deploy a tar + chown + `systemctl restart` (no npm/toolchain, no supply-chain surface). R6 rules out exactly what an SPA would buy. **Recommendation on CDNs (network-exposed site):** **vendor `htmx.min.js` and a prebuilt Tailwind CSS into `/static`** (committed artifacts — still no *deploy-time* build) rather than CDN, for a hardened, self-contained, offline-capable site. Verdict: the stack fits the workload, team size, deploy constraints, and rulings; challenging it adds cost with no board-visible benefit.

## 11. Auth — Authelia (Ruling C) + non-preclusion for family owner-filtering
`predictions.jacksumner.com` sits behind the **same Authelia** as `trading.jacksumner.com`, with per-domain rules: **Karen** = her own login, access to `predictions` and **not** `trading`; **Jack** = full access. **No Basic Auth, no in-app login, no sessions/passwords in `pm_web`** — the gate is enforced at the proxy **before** the request reaches the app (the strongest boundary, matching the separate-site rationale). **No `pm_user`/`pm_role`/`pm_grant` tables** (superseded — a parallel user table would be a second place defining access). The `pm_user`(Authelia login) ≠ `pm_account`(Kalshi API) discipline holds; the mapping between them is the future access model; `pm_account` (P3) is unaffected.

**DECIDED (not assumed): P2 does NOT need the user's identity.** The pages are identical for Jack and Karen (both see ALL PM sub-divisions; per-account filtering is not built in P2). Authelia's decision is pure allow/deny at the proxy; `pm_web` reads no identity headers. Caddy already forwards `Remote-User`/`Remote-Groups`/`Remote-Name`/`Remote-Email` downstream (`copy_headers` in the trading block) — **reserved for P3** when per-owner views arrive.

**⚠ DISCOVERY FINDING (2026-08-23, DESIGN-AFFECTING — Jack's call/hands, NOT changed here).** The live proxy is **Caddy** (`/etc/caddy/Caddyfile`), Authelia on `127.0.0.1:9091` via `forward_auth … uri /api/authz/forward-auth`. Authelia's `access_control` is `default_policy: deny` with **ONE rule: `trading.jacksumner.com → two_factor` and NO `subject` restriction** — i.e. *any* authenticated 2FA user reaches trading. Users DB = file backend (`/etc/authelia/users_database.yml`), **single user `jack` [admins]**; session cookie scoped to apex `jacksumner.com` (SSO). **Consequence:** adding `predictions.jacksumner.com` does NOT scope Karen out of trading — because giving Karen an Authelia login makes her satisfy the unrestricted trading rule too. **Delivering Ruling C REQUIRES tightening the `trading` rule** (add `subject: 'group:admins'` or `'user:jack'`) — an edit to the config protecting the LIVE TRADING STACK. That, plus adding Karen + a `predictions` rule + a `pm_viewers` group, is **Jack-authorized live-stack work, flagged not performed** (§16 new open item). The good news: `default_policy: deny` means the PM site is fail-closed until its rule exists, and the apex SSO cookie means one login covers both domains — the entire access model is the `access_control` rules + the users DB, no app code.

**Non-preclusion for the stated family direction (do NOT build — two cheap requirements only):** when `pm_account` is created in **P3**, (a) it carries a **nullable owner-identity** field (empty until family logins arrive; a login maps to account(s), and a sub-division is (account, category), so owner-filtering is later a WHERE clause on an existing column — no new access model); and (b) **all sub-division reads route through ONE function** (the shared-predicate discipline P1 got right), so filtering is one place to change. **Open for later, do not decide now:** whether an owner sees only their own sub-divisions or all with theirs highlighted — do not bake in "own only."

## 12. Deploy / runbook (additive, restart-free, ownership-correct)
**Package:** all new code under `trading_corp/prediction_markets/web/` + `paper.py` + `scripts/pm_web.py` + the systemd unit + the two cron lines. A deploy tar ships **only PM paths + unit/cron files — zero engine files** (so the engine cannot be touched). **Process (matches `trading-corp.service` house style, confirmed by discovery):** a systemd unit `prediction-markets-web.service` — `Type=simple`, `User=azureuser`, `Group=azureuser`, `WorkingDirectory=/home/azureuser/trading_corp`, `Restart=on-failure` + `RestartSec=10` + `StartLimitIntervalSec=60`/`StartLimitBurst=5`, `StandardOutput=journal`/`StandardError=journal`, hardening `NoNewPrivileges=true` + `ProtectSystem=strict` + `PrivateTmp=true` + `ReadWritePaths=/home/azureuser/trading_corp/data /home/azureuser/trading_corp/logs`, env `KEY_VAULT_URI=<same KV as engine>` (for the Anthropic key ANALYZE uses) + `PM_DB_PATH` + `PYTHONPATH` + `PYTHONUNBUFFERED=1`, `ExecStart=/home/azureuser/trading_corp/venv/bin/python -m uvicorn trading_corp.prediction_markets.web.app:app --host 127.0.0.1 --port 8081`. **NO `xvfb-run`** (unlike the engine — no headless browser needed). Deliberately **NOT** `After=trading-corp.service` (total decoupling). **GOTCHA-1 handling:** the root-context deploy `chown -R azureuser:azureuser` the copied PM paths + `data/prediction_markets.db*` afterward, then `systemctl daemon-reload && systemctl restart prediction-markets-web` — which blips **only** `pm_web` (R1). **Proxy (Caddy, confirmed):** `pm_web` binds **`127.0.0.1:8081` (verified free; loopback-only** so it is reachable ONLY via Caddy+Authelia — unlike the engine's `0.0.0.0:8000`). Add a `predictions.jacksumner.com` block to `/etc/caddy/Caddyfile` mirroring the trading block's `forward_auth localhost:9091 { uri /api/authz/forward-auth; copy_headers Remote-User Remote-Groups Remote-Name Remote-Email }` → `reverse_proxy localhost:8081` (+ a `@public /static/*` handle if PWA paths are wanted; `/healthz` can be a public matcher for uptime). `caddy reload`. **DNS/TLS:** add an A record `predictions.jacksumner.com → 172.171.189.116` (none exists today); Caddy **auto-provisions** the Let's Encrypt cert on first request (no explicit `tls` directive = automatic HTTPS, per-site — no SAN/manual cert). **Authelia:** add the `predictions` access_control rule + Karen user + tighten the trading rule (§11 finding / §16) — **Jack-authorized, before go-live.** **Weekly cron** replaces the daily line (§7.1, slot re-verified at install); **30-min paper-poll** cron added; both azureuser. **Restart-free for the engine:** the tar names no engine file; the restart names only the PM unit; `trading-corp.service` is never referenced. **Rollback** (extend `pk_pm_rollback.ps1`): `systemctl stop && disable prediction-markets-web`, remove the unit + `web/` subtree (P1 package + DB left inert), restore the prior crontab, `daemon-reload` — no engine restart, no sudo beyond unit ops, engine provably untouched. **Ordering:** serialize any `prod-live` git advance behind engine/MACE advances (no PM-web restart-window constraint of its own). All box ops via the `.ps1`-file runner pattern (command-paste-rule); the migration + first refresh **run as azureuser** (`runuser`), never root (GOTCHA-1).

## 13. Test plan (offline, `tests/prediction_markets/`, injectable client/DB — P1 posture)
- **Migrations 004–007** apply idempotently on a fresh DB and on a P1 DB; re-run is a no-op; `schema_version` → 7.
- **rollup analytics:** two_sided_pct / single_game_pct (incl. `ambiguous`→floor and NULL-for-Fed) / one-sided slice match hand-computed fixtures; existing `test_stats` stays green (additive columns w/ defaults).
- **Paper:** open (from a fake `/activity` BUY), mark, close-on-resolution, and **STALE** (whale exits) each transition correctly; `paper_rollup` counts `n_closed`/`n_open`/**`n_stale`** correctly; entry-basis recency uses `entry_ts`.
- **Roster migration:** legacy `agent_state` → `pm_roster` `INSERT OR IGNORE` idempotent; after flip, ingest reads `pm_roster` and makes **no legacy read** (assert).
- **Every route:** 200 + honest-empty on an empty DB; the refresh band color thresholds at 8d/15d; `query_scoreboard` HTML flags == `format_report` flags for a fixture (product/CLI parity).
- **Drill-through filters** return the expected row subsets; the caveat cells link to the right filtered rows.
- **ANALYZE:** cache hit ($0/0 tokens), miss under cap (reasoned-null, audit-only), force-evict; daily-cost aggregation.
- **Search worker:** single-flight; rate_limited transition then resolve; monotonic progress; restart-survivor → `error(interrupted)`; on subprocess exit-0 the found wallets have `pm_category_stats` rows (integration, temp DB, injected client).
- **Concurrency (integration):** a pin/unpin writer loop vs `stats.rollup` on a temp DB — no `database is locked` escapes the retry.
- **Deploy (dry, CI):** tar manifest contains ONLY PM paths + unit/cron (assert no engine paths); rollback removes exactly the web subtree + units, leaves P1 package + DB.
- **Manual smoke (post-deploy):** Authelia — Karen reaches `predictions`, is denied `trading`; Jack reaches both. Each tab loads; a small search runs `searching→backfilling→finished`; Analyze shows per-call + daily cost; `systemctl status trading-corp.service` unchanged across the PM deploy; `ls -l data/prediction_markets.db*` all azureuser.

## 14. Acceptance checklist (measurable bars)
- [ ] `pm_web` serves on `predictions.jacksumner.com` **behind Authelia**; Karen's login IN, denied `trading`; Jack IN both. No in-app auth code; no `pm_user` tables exist.
- [ ] Every scoreboard-bearing page renders the freshness band from `MAX(last_refresh_ts)`; green/amber/red at 8d/15d.
- [ ] Product tabs (scoreboard; farm league w/ watchlist + pinned-paper per category; whale detail; search) + diagnostics all return 200 and honest-empty on an empty DB.
- [ ] Caveat columns (two_sided_pct, single_game_pct, avg_win_price chalk/contested, $-weighted data_quality) present on the product scoreboard + farm lists — **not** only in diagnostics (R4).
- [ ] **Every aggregate drills through to its rows** (scoreboard row → why-ranked; n_resolved → rows; each caveat → its rows) (R5).
- [ ] **`n_stale` shown beside `n_resolved`** on the pinned-paper list (Ruling D).
- [ ] Search is a background job (never synchronous); `pm_search_run` progress polled via HTMX; a found candidate is rankable (has `pm_category_stats`) after its targeted backfill subprocess; concurrent same-category search is single-flighted.
- [ ] ANALYZE reads `pm_closed_position` (untruncated), caches in `pm_analysis_cache`, shows per-call + daily cost, hard-skips narration over `PM_ANALYZE_DAILY_USD`.
- [ ] **Weekly** full refresh installed (proposed Sun 09:00 UTC, **slot re-verified live at install**); one run marks `backfill_complete` + `pulled==stored`; paper mark/close + rollup + scores run after. **No `ingest.py` edit; no incremental refresh.**
- [ ] Paper-entry capture job (~30 min, pinned-only `/activity`) inserts `pm_paper_trade` open rows with real `entry_ts`.
- [ ] Migrations 004–007 apply idempotently on the live P1 DB; re-run no-op; existing tests green.
- [ ] Deploy additive (tar = PM paths + unit/cron only, **no engine files**); `pm_web` restart does not touch `trading-corp.service`; every written file azureuser-owned; rollback removes only PM web subtree + units.
- [ ] **No legacy-DB write by the site** (assert); roster read from `pm_roster` after the one-time seed.

## 15. Locked decisions (P2)
1. Separate `pm_web` process, own hostname, `prediction_markets.db` only, no engine imports (R1).
2. **Weekly** full refresh (Ruling A); no incremental; `ingest.py` untouched; paper-entry capture is a separate ~30-min job.
3. Roster migrated INTO `prediction_markets.db` (Ruling B); legacy import is a one-time convenience seed, no reads back, drift expected, site never writes legacy; legacy code may be copied.
4. Auth = **Authelia** at the proxy (Ruling C); no in-app auth; no `pm_user`/`role`/`grant` tables; `pm_user`≠`pm_account`; P2 needs no identity; owner-filtering non-preclusion = nullable owner field on P3's `pm_account` + one sub-division read function.
5. Paper trades **hold to resolution; STALE if the whale exits, visible + counted** (Ruling D).
6. Caveat analytics computed in `rollup()` as first-class columns/companion (not query-time, not probes) (R4).
7. Server-rendered Jinja2 + HTMX + Tailwind, no build step, HTMX/Tailwind vendored (§10).
8. Search = in-process asyncio worker + `pm_search_run` + detached `pm_cli --only-wallets` backfill (§7.3).
9. ANALYZE reads `pm_closed_position`, caches with cost-guard (§7.4).
10. market_type: slug-heuristic `single_game_pct` now; true gamma market_type deferred (seam reserved).
11. Branch: `prediction-markets-p2-2026-08-23` → durable `prediction-markets`; no `main` merge; confirm prod-live tip before branching.
12. Paper sizing = **fixed unit** (OQ-1); paper scores in a **separate `pm_paper_score_snapshot`** (OQ-5); ANALYZE daily cap **$2** (OQ-6). Accepted defaults (OQ-2/3/4/8/9) are tunable after first real data; OQ-4's weekly slot keeps the mandatory live re-verify.
13. **Infra facts (§7.1/§11/§12) VERIFIED 2026-08-23** (authorized read-only discovery, §16 OQ-7) and folded. Plan is BOARD-APPROVED FINAL. The one remaining live-stack action (tighten the Authelia trading rule before Karen's login) is **Jack's, tracked separately in `AUTHELIA_TRADING_RULE_FINDING.md`, and does NOT block the P2 build.**

## 16. Open questions — board-ruling status (2026-08-23)

**RULED (folded into the plan):**
- **OQ-1 (paper sizing) — FIXED UNIT.** Fixed sizing makes whales comparable; mirroring imports the whale's bankroll into the signal; the whale's own size is a data point to DISPLAY, not a sizing input. Per-sub-division sizing = P3. (§5.2)
- **OQ-5 (paper scores destination) — SEPARATE `pm_paper_score_snapshot`.** Paper = entry-basis + forward-only; external = resolution-basis + historical; one table with a tag invites the conflation P1 fought. (§5.2)
- **OQ-6 (ANALYZE daily cap) — $2/day, not $5.** $5/day ≈ $150/mo would exceed the whole current ~$60/mo platform spend after the Anthropic cost workstream. Hard-skip-narration (audit-only, reasoned-null) on breach. (§7.4)

**ACCEPTED DEFAULTS (Jack's call: tunable after first real data, same as the clip bounds — recorded with rationale, expected to be revisited once the site is in use):**
- **OQ-2 (single_game_pct for non-sports):** renders NULL/"n-a" for Fed (no single-game notion); futures regex is the starting set.
- **OQ-3 (paper-poll interval):** **30 min** for `/activity` entry capture — only needs to stay ahead of the 5000-row `/activity` truncation for pinned whales.
- **OQ-4 (weekly slot):** **Sunday 09:00 UTC** — with the **MANDATORY live re-verify at install** (`crontab -u azureuser -l` + `systemctl list-timers --all`), never a remembered map.
- **OQ-8 (recency default):** pinned-paper lists default to **entry_ts**; external watchlist/scoreboard to **resolved_ts**; each labeled.
- **OQ-9 (roster category attribution):** one `pm_roster` row per (wallet, category), derived from the whale's categories.

**OQ-7 (proxy / Authelia / port / DNS) — DISCOVERY DONE 2026-08-23 (authorized read).** Facts folded into §11/§12: Caddy proxy; Authelia `forward_auth` on :9091; PM binds verified-free `127.0.0.1:8081`; Caddy auto-TLS; `predictions` DNS A record needed; systemd house style captured.

**⚠ NEW OPEN ITEM — DESIGN-AFFECTING, JACK-AUTHORIZED LIVE-STACK WORK (do NOT perform in the build):** delivering Ruling C requires changes to the config protecting the **live trading stack**, discovered 2026-08-23:
1. **Tighten `trading.jacksumner.com`** — its Authelia rule is `two_factor` with **no `subject`**; add `subject: 'group:admins'` (or `'user:jack'`) so a new user (Karen) does NOT inherit trading access. **This is the linchpin — without it the auth ruling is not delivered.**
2. **Add Karen** to `/etc/authelia/users_database.yml` (file backend; `watch:false` → restart Authelia after), in a `pm_viewers` group (add jack too, or use subject lists).
3. **Add a `predictions.jacksumner.com` access_control rule** (`default_policy: deny` means no rule = nobody reaches it, including Jack): `policy: two_factor`, `subject: 'group:pm_viewers'`.
4. **Add the Caddy `predictions` site block** + the **DNS A record** (§12).
Items 1–3 touch the live trading stack's auth → **Jack's hands, Jack's call, before go-live.** The build agent builds `pm_web` + its systemd unit + the migrations/UI; it does NOT edit Caddy/Authelia. Flagged, not assumed. **This is a Trading-Corp infra change (same category as the VM geo-migration), NOT a P2 task — tracked standalone in `AUTHELIA_TRADING_RULE_FINDING.md`. It does NOT block the P2 build:** Authelia's existing `two_factor` rule already covers a new `predictions` vhost (Jack reaches it, nobody unauthenticated does), so the site can be built, deployed, and used by Jack long before Karen's login exists. What the finding blocks is the **Karen viewer feature**, not the build.

---

### Critical files (reuse; do not edit the engine's)
- `trading_corp/prediction_markets/{db,stats,category,ingest,rosters}.py`, `scripts/pm_cli.py` — the data contract; extend `db.py` (migrations), `stats.py` (rollup analytics + paper_rollup), `category.py` (classify_market_shape); **do NOT edit `ingest.py`** (call it).
- `trading_corp/web/mace_view.py` — the off-loop, honest-empty read-model + HTMX-partial pattern to mirror.
- `trading_corp/web/routes.py` (~2838–2950 discover job; ~3155–3376 ANALYZE) — templates for §7.3/§7.4.
- `trading_corp/web/templates/base.html`, `partials/poly_kalshi_live.html` — shell + polling patterns to copy.
- `reports/prediction_markets/{DEPLOY_COMPLETE,OPS_GOTCHAS,DEPLOY_SEQUENCE}.md` — additive-deploy + ownership conventions.
