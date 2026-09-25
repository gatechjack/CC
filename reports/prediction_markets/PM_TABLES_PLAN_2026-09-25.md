# PM UI — Three Table Passes: PLAN (accepted 2026-09-25)

**prod-live tip at planning:** `558fc143` — "leg-audit: ITF trailing-X surname pad rule -> ok:code_pad".
**Build branches (stacked):** `pm-tables-p1-2026-09-25` (off prod-live) → `pm-tables-p2-2026-09-25` (off p1) → `pm-tables-p3-2026-09-25` (off p2). pm_web-only; engine never restarted; each phase deploys 1→2→3, FF prod-live before the next ships.

---

## RULINGS (accepted by Jack 2026-09-25)

- **OQ-1** — Migrate BOTH the Watchlist and Prospects tables to the Deploy-12 server-side URL sort (`?wsort/?wdir`, `?psort/?pdir`, JS-off safe); retire `pm_sort.js` for those two tables; remove the JUDGE negation hack; a test asserts PROMOTE still floats to the top by default.
- **OQ-2** — (b) Refinement: the Prospects action-column button is state-aware — unscored → "Analyze"; scored → "View result" (cached, $0) AND "Re-analyze" (force), with the analysis age shown beside them. Keep the un-analyzed state readable in the JUDGE/WIN% cells; remove the duplicate Analyze controls there only if that state stays clear.
- **OQ-3** — (i) The new Active/Complete tables apply to the NON-MLB template only. MLB keeps its game cards and is OUT OF SCOPE.
- **OQ-4** — Active default sort = event date ascending; Complete default = settle date, newest first.
- **OQ-5** — Flat table on both tabs; game (matchup + event date) is a sortable column.
- **OQ-6** — Grid header W–L uses "—" for zero closed and THIN under 50, from `farm.farm_rows(PINNED)`.

---

## CONTEXT — why this work

Three independent, low-risk pm_web (read-only UI) passes:
- **Phase 1 — Farm category page:** the Watchlist table can't be sorted (Prospects can); the Analyze control's discoverability on Prospects needs work.
- **Phase 2 — Splits Whale-Grid:** enrich the grid header so a whale's live-copied status (with account) and paper W-L record are visible where the money split is shown.
- **Phase 3 — Live sub-division detail:** the Active/Complete tabs become real sortable tables with a mandatory placement timestamp; the Complete order looks random; the trade drawer leaks a raw Kalshi series tag for non-structural sports.

The engine (trading-corp) is armed and trades across four accounts; nothing here touches it.

---

## 1. REPO / BOX STATE (verified 2026-09-25, STEP 0)

| Item | Value |
|---|---|
| prod-live tip | `558fc143` (re-anchored, unchanged) |
| pm_web code root | `trading_corp/prediction_markets/web/` |
| Schema head | **24** (mig 024 = `pm_subdivision_attachment_event`); next free **025**; box tables present: `pm_whale_score`(023), `pm_subdivision_attachment_event`(024), `pm_subdivision_order`, `pm_paper_trade`, `pm_paper_category_stats` |
| box == prod-live | **48/48** tracked files match (CR-stripped sha16); box also carries 6 untracked `.bak_*`/`.orig` cruft files (not tracked, not in scope) |
| Baseline | **603 collected, 579 passed, 24 failed** (exact documented set: accounts_m2×3, ctx_pagination×1, live_r3×14, refresh_stagger×1, rung3×1, stage2_nav×2, stage2_phase3×2) |
| Baseline cmd | `python -m pytest tests/prediction_markets -q -p no:pytest_ethereum -p no:cacheprovider --continue-on-collection-errors` (via `.venv-webtest`) |
| Engine | `trading-corp` MainPID 534581, NRestarts 0, active |
| pm_web | `prediction-markets-web` MainPID 523799, NRestarts 0, active |

All file:line citations below are from `git show origin/prod-live:<path>` at `558fc143`.

---

## 2. PER-PHASE PLAN

### PHASE 1 — Farm category page: Watchlist sort + Analyze

Route `GET /farm/{category}` → `farm_league_category()` `app.py:820`; loader `_load_farm_category()` `app.py:522-594`. Template `pm_farm_category.html` includes `partials/pm_watchlist_rows.html` + `partials/pm_prospects_rows.html`; loads `/static/pm_sort.js`.

**1a — server-side URL sort for BOTH tables (OQ-1):**
- Today: Watchlist `<table class="pm-table pm-farm-table">` (`pm_watchlist_rows.html:10`) is NOT sortable. Prospects sorts CLIENT-SIDE via `pm_sort.js` (ascending-first: `asc = dir !== "asc"`), not URL/JS-off-safe; the JUDGE cell has a load-bearing `data-sort-value="{{ -r.score.sort_value }}"` negation to counter that.
- Build: add a pure sort helper in `live_view.py` keyed to each table's columns; `_load_farm_category` reads `?wsort/?wdir` + `?psort/?pdir` and sorts server-side; headers become `<a href>` links carrying the params; defaults unchanged (Watchlist as today; Prospects tier-first PROMOTE-at-top). Drop `pm-sortable`/`data-sort-value`/the JUDGE negation; drop the `pm_sort.js` include. Files: `app.py`, `live_view.py`, `pm_watchlist_rows.html`, `pm_prospects_rows.html`, `pm_farm_category.html`, `pm_desk.css`, tests.

**1b — state-aware Analyze button (OQ-2):**
- Today: the Analyze button is already in the Prospects action cell on every row (shipped "item 6, 2026-09-15"), posting to `POST /farm/analyze/{wallet}/{category}` `app.py:474` → `#pm-analyze-panel`; cache HIT = $0; `?force=1` re-runs; deterministic score in `pm_whale_score` (mig 023) with `computed_ts`→age. Un-analyzed state also shown in JUDGE (`score_cell` `pm_macros.html:106`) + WIN% (`omission_cell` `pm_macros.html:82`).
- Build: action button is state-aware — unscored → "Analyze"; scored → "View result" ($0, no force) + "Re-analyze" (`?force=1`) + visible age. Thread `age_days` into the prospect row context. Keep the JUDGE/WIN% un-analyzed [Analyze] state readable (remove duplicate controls only if clarity holds). Files: `pm_prospects_rows.html`, `pm_macros.html`, `app.py`, tests.

Risk: touches the working Prospects table (JUDGE negation removal; tri-state Analyze). No migration, no shared engine file, no app.py structural change beyond query-param reads + row context.

### PHASE 2 — Splits Whale-Grid enrichment (OQ-6)

Route `GET /farm/{category}/splits` → `farm_category_splits()` `app.py:833`; loader `_load_watchlist_splits()` `app.py:595-632`; builder `live_view.build_watchlist_splits()` `live_view.py:1656`. Grid header ~`pm_farm_splits.html:194`.

- Data already on the loader: `_load_watchlist_splits` calls `farm.farm_rows(PINNED)` (`app.py:604`, the SAME reader the Watchlist uses → carries `n_closed/wins/losses/win_rate/user_name`), and computes `trusted_by_wallet={wallet:[account_label]}` for `active=1` (`app.py:615-619`). The per-whale dict (`live_view.py:1705-1710`) already has `trusted`+`copied_by`, but omits paper W-L.
- Build: (a) grid header — add `copied_by` accounts to the `<th title>` (hover) + stronger highlight for `trusted`; (b) thread `n_closed/wins/losses/win_rate` from the pinned rows into the per-whale map + position dict, render `W–L` + `win%` with "—" at zero closed and THIN under 50. Files: `app.py` (loader map), `live_view.py` (builder dict+signature), `pm_farm_splits.html` (header), tests.

Risk: `build_watchlist_splits` is pm_web-only; no shared engine file; no migration; grid cells unchanged. Size: small.

### PHASE 3 — non-MLB live detail tables + drawer floor (OQ-3/4/5, 3c)

Route `GET /live/{account}/{category}` → `live_subdivision_page()` `app.py:1485`; loader `_load_live_subdivision()` `app.py:1372`; builders `_positions_view()` `live_view.py:730-793`, `_group_by_game()` `:796`. Mode `"mlb_cards" if is_mlb else "positions"`; `positions_view` built only in the non-MLB branch (`live_view.py:904`). Tables render via `postable(groups)` `pm_live_subdivision.html:217` / `posrow(p)` `:184`; non-MLB path `:323-335`; tabs switch via `?tab=complete` `:292-293`.

- 3a/3b: extend `_positions_view` rows with placed ET (`submitted_ts`), settled/closed ET (`settled_ts`/`response_ts`), order id (`id`); rebuild `postable`/`posrow` as FLAT sortable tables (game = a sortable column) with the §3 columns; add server-side `?sort/?dir` over the flattened rows. Defaults: Complete = settle date desc; Active = event date asc. Files: `live_view.py`, `pm_live_subdivision.html`, `app.py`, `pm_desk.css`, tests.
- 3c: `_trade_rows()` `live_view.py:557-613` builds `t.label` via `format_market_label(matchup, kind, short, None)` — no title, no `_base_label` floor → leaks raw series (KXATPMATCH) at drawer `pm_trade_drawer.html:21` (Type) + `:40` (Market/`t.desc`). Fix: thread `titles`+`category` into `_trade_rows`, apply the floor `primary if mu else (ptitle or _base_label(tk, kind, category))` to both `label` and `desc` (mirrors `_positions_view:754`). Template unchanged.
- MLB regression lock: MLB stays `mlb_cards`, untouched.

Risk: pm_web-only; no migration; no shared engine file; app.py gains query-param reads only. Flat-vs-grouped is a visible change (ruled: flat). Size: medium.

---

## 3. PHASE-3a COLUMN TABLE

| Column | Source | Direct/join | ET convert |
|---|---|---|---|
| placed (ET) | `pm_subdivision_order.submitted_ts` | DIRECT | yes |
| settled/closed (ET, Complete) | `settled_ts` else `response_ts` | DIRECT | yes |
| game (matchup + event date) | `market_matchup(ticker)` + `structural_game_key`/`parse_ticker_start` | COMPUTED (feed join MLB only — out of scope) | date fmt |
| bet (signed shorthand) | `_short_label(ticker,kind,leg)`→`format_market_label` primary | COMPUTED | — |
| contracts | `fill_count` | DIRECT | — |
| fill price | `fill_price` | DIRECT | — |
| cost | `cost_basis_usd` (open agg) | DIRECT | — |
| value now + mark age (Active) | `contracts × bid_for_leg(mark,leg)`; age = now − mark.as_of | COMPUTED (marks) | — |
| realized net of fees (Complete) | `realized_pnl` | DIRECT | — |
| status | `_pos_aggregates` from `is_exit`,`close_source`,`won` | COMPUTED | — |
| copied-from whale | `wallet` (+ `user_name` join) | DIRECT + JOIN | — |
| order id | order rowid `id` | DIRECT | — |

Columns confirmed: `pm_subdivision_order` `db.py:649-676` + mig 015 (`close_source`,`realized_pnl`,`won`,`settled_ts`) `db.py:787-790`. All ts are UTC epoch. **No migration needed.**

---

## 4. DEPLOY SHAPE (per phase, Board-run)

pm_web-only, one branch per phase. Steps: (1) engine+pm_web health, schema head, box==prod-live N/N, reconfirm baseline (differential empty). (2) real commit on the phase branch. (3) test differential + render vs RO box-DB snapshot. (4) staged scp+tar, drift-gated (box BEFORE==prod-live CR-sha16), backup-is-a-gate, post-write CR-sha==AFTER + `py_compile` + import gate; rollback `rm`s any created file. (5) ONE `systemctl restart prediction-markets-web` (verify MainPID+ActiveEnter, NOT az exit); engine PID+NRestarts unchanged throughout. (6) FF `origin/prod-live` to the deployed commit + tag; re-verify box==prod-live. Deploys/restarts are Board actions; the agent presents runners, Jack runs the restart.

No phase plans a migration, a shared engine-file edit (`subdivision.py`/`db.py` untouched), or an app.py structural change beyond query-param reads + row context.
