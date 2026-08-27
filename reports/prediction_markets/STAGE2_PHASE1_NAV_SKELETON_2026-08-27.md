# Stage 2 · Phase 1 — Navigation Skeleton (BUILD ONLY, box-scratch PENDING, NOTHING LIVE)

**Date:** 2026-08-27 · **Branch:** `prediction-markets-stage2-phase1-2026-08-27` (worktree `cc-pm-stage2-phase1-wt`,
off feature tip `8fd247d`) · **Mode:** BUILD ONLY. No live deploy, no pm_web restart, no DB write, no migration.
**Restate the three lists + three bases from memory (anti-drift first line):** **Prospect** = completed trades
(`pm_closed_position` → `pm_category_stats`, code `candidate`); **Watchlist** = our paper trades
(`pm_paper_trade` → `pm_paper_category_stats`, code `pinned`); **Live** = live trades (P3 tables, not built).

Stage 2 is split into three reviewable passes (recorded in `PM_REBUILD_PLAN` Stage 2). **This is pass 1 of 3 —
routing + structure only.** Phase 2 fills per-category content; phase 3 retires `/scoreboard` + the flat `/farm`
and repoints the new hierarchy onto the good URLs (the load-bearing phase-3 deliverable, now in the plan).

---

## 1. WHAT WAS BUILT

**The hierarchy (Jack's, verbatim) → routes:**

| Screen | Route (temporary, phase-1) | Phase-3 target |
|---|---|---|
| Main Predictions-Market Dashboard | `GET /dashboard` | → `/` |
| Farm League — category tiles | `GET /farm-league` | → `/farm` |
| Per-category page (Watchlist top / Prospects bottom) | `GET /farm-league/{category}` | → `/farm/{category}` |

Built **alongside** the still-live legacy pages — `/` (scoreboard), `/scoreboard`, `/farm`, `/farm/list`,
`/whale/...` are **unmodified**. The new hierarchy uses an **isolated base** `pm_shell.html` so `pm_base.html`
and every legacy page render **byte-for-byte unchanged**.

**Files changed (deploy artifact set = 7 PM-package files; the box==branch gate at deploy re-hashes these):**

| sha256 (branch) | path (under `trading_corp/prediction_markets/`) | change |
|---|---|---|
| `a1d4cc28eec92a1e8a13a4f043cbbaca0228590ee615f2c5bfd5fdda1050db69` | `web/app.py` | +3 routes + 3 read-only loaders (appended; existing handlers untouched) |
| `905ce2958ececace66f144489468f53970c25e9b1a87d196c3fb96dc6ebd8ee8` | `web/templates/pm_shell.html` | NEW isolated base (target nav) |
| `8dadcc458fbbc7b021e4edf11501c762ec8e430565670eae4fb079b8c88fc20c` | `web/templates/pm_dashboard.html` | NEW dashboard shell |
| `a8b671998003a9931f639058ef891e832d92cc6b75bb6cb401cd07bb0dbe5011` | `web/templates/pm_farm_league.html` | NEW tile grid (category display `\|upper`) |
| `115160aa655e10357ece9acbc4c2e5eeddd871848d24b26452e29d4d8a3e6fc0` | `web/templates/pm_farm_category.html` | NEW per-category page (category display `\|upper`) |
| `22551ff0e530ce4041f27d54fa064dfd8a1888afebd0b06e61cd41facc5cb15b` | `web/templates/pm_category_404.html` | NEW styled 404 (category display `\|upper`) |
| `f059504fa3fd0b3005e5234351b5944a640ff9ad709dbb47958f49c33192e38d` | `web/static/pm.css` | APPENDED Stage-2 block (additive); tile-name casing now template-driven |

**NOT deployed (branch-only):** `tests/prediction_markets/test_stage2_nav.py`, this report, the `PM_REBUILD_PLAN`
edit. **NOT touched:** `paper.py`, `stats.py`, `db.py`, `farm.py` (queries), any data-layer/query/rollup code,
`persistence/db.py`, the engine, `poly_kalshi_mlb`, MACE, PEAD, bitunix.

## 2. WHAT THE PAGES LOOK LIKE (so Jack knows what he is authorizing)

- **`/dashboard`** — heading "Predictions Market Dashboard" + a two-card menu grid: **(a)** a **visibly disabled**
  card "Live sub-divisions — coming in P3" (dimmed, `aria-disabled`, honest subtext, **no fabricated data**);
  **(b)** an active **"Farm League →"** card whose subtext shows the **live category count** (data-driven) and
  links to `/farm-league`. Nav bar: Dashboard · Farm League · (disabled) Live sub-divisions.
- **`/farm-league`** — breadcrumb "Dashboard › Farm League", heading "Farm League", a subtitle stating the live
  count, then a **responsive grid of category tiles** — one per **active** Kalshi-copyable category (today the 15:
  mlb, nba, nfl, nhl, wnba, epl, ucl, soccer, atp, wta, tennis, cs2, golf, ufc, fed). Each tile is a card
  (category name in **UPPERCASE** — e.g. MLB — + "Watchlist · Prospects" hint) linking to its page (the href
  stays lowercase). **No cbb/fifwc/unknown tile** (deactivated).
- **`/farm-league/{category}`** — breadcrumb "Dashboard › Farm League › {CATEGORY}", heading = the category name
  in **UPPERCASE** (e.g. ATP) — casing is consistent everywhere a category name is displayed (tiles, heading,
  breadcrumb, title, 404); URLs stay lowercase — then
  **two stacked, structurally-distinct panels**: **Watchlist** (top; "our paper trades · N whale(s)"; a
  "phase-2" placeholder; reads the **paper** basis) and **Prospects** (bottom; "completed-trade screening · N
  prospect(s)"; honest-empty "no prospects yet — populated by Search" today; reads the **completed** basis).
- **Invalid/removed category** (`/farm-league/cbb`, `/farm-league/unknown`, `/farm-league/banana`) — a styled
  **HTTP 404** page ("… is not a Farm League category" + link back), never a fabricated category page.

**Verdict for Jack:** this is a **usable, navigable skeleton** — you can click Dashboard → Farm League → a
category → land on the two labelled regions, and back up via breadcrumbs — with the section bodies intentionally
near-empty (phase-2 fills them). It is NOT a set of empty routes.

**★ Visual treatment is PROVISIONAL (deliberate deferral, not a gap).** The dark scaffold is functional
beginning-screen styling; once the division is live, Jack commissions a proper UI build (Claude Design) that
replaces it wholesale — so phase-3's base-template consolidation stays MINIMAL. Recorded in `PM_REBUILD_PLAN`
Stage 2 ("go with the planned dark theme" until then).

## 3. WHY IT IS CORRECT (the phase-1 must-haves)

- **Routes resolve** — 3 new routes; the legacy pages still resolve (test `test_legacy_routes_still_resolve`).
- **Tiles are ACTIVE-driven, never hardcoded** — the tile set = `farm.farm_categories(conn, PINNED)`, which gates
  `active=1`. Re-admitting a category (flip `active=1`) makes its tile appear with **zero code change**; a
  hardcoded 15 would silently diverge. (test `test_farm_league_tiles_are_data_driven`.)
- **The page knows its category** — heading + breadcrumb render `{category}` (test `test_category_page_knows_its_category`).
- **Deactivated category is NOT reachable** — the page-validity gate is the **same** active-tile source; a category
  not in it → **404**. cbb/unknown/nonexistent all 404 (test `test_deactivated_category_not_reachable_by_url`).
- **Separate bases, even empty** — the per-category loader makes **two distinct calls**:
  `farm.farm_rows(status=PINNED)` (paper) and `farm.farm_rows(status=CANDIDATE)` (completed). The template exposes
  `data-basis`/`data-count` per region; a BASIS test seeds Watchlist=2 / Prospects=1 and asserts the counts DIFFER
  and each region declares its own basis — a cross-wire (one shared path) would make them equal and FAIL
  (test `test_watchlist_and_prospects_read_separate_bases`).
- **F-3 vocabulary** — the screen renders **Watchlist** / **Prospects** / **Live sub-divisions**; the code words
  `pinned`/`candidate` do **not** leak to the UI (asserted `"pinned" not in body and "candidate" not in body`).

## 4. §H CHECKPOINT ANSWER

*Which of the three lists did this change touch, and did it keep their three data bases separate?* It laid the
**presentation shells** for **Watchlist** (paper) and **Prospects** (completed) on the per-category page, and the
**Live** menu option as a disabled P3 placeholder. It **wired no stats** (phase 2), but it **established two
separate data paths** — Watchlist via `farm_rows(PINNED)`→`pm_paper_category_stats`, Prospects via
`farm_rows(CANDIDATE)`→`pm_category_stats` — that **never share a query**. The BASIS test proves they are distinct.
**Live** stays P3 with no data path. The three bases stay separate.

## 5. ACTIVATION PATH (per the deploy rule)

**All 7 phase-1 artifacts activate on a pm_web restart.** `web/app.py` is the pm_web entrypoint; the 5 templates
are rendered by pm_web (Jinja loads from the templates dir at request time); `pm.css` is served by pm_web's
`/static` mount. **No `pm_cli`-loaded file changed** — `paper.py`, the data-api client, and `db.py` are untouched,
so no `pm_cli` run is needed to pick anything up, and there is **no migration** (schema stays 9). The pm_web
import chain (`web → stats/positions/names/farm/analyze`) is unchanged; `app.py` only ADDS handlers that call the
already-imported `farm.*` readers.

## 6. PHASE-1 RUNG LADDER (Stage-0/1 shape; NOTHING below is authorized — HALT here)

- **Rung 0 — BOX-SCRATCH GREEN (the gate).** Copy the branch PM package + `pyproject.toml` + `tests/conftest.py` +
  `tests/prediction_markets/` to a box `/tmp` scratch; run
  `venv/bin/python -m pytest tests/prediction_markets/ -p no:pytest_ethereum -q` (the broken `pytest_ethereum`
  plugin MUST be disabled; `pyproject.toml`'s `asyncio_mode=auto` is required for the async suite). GREEN required,
  with the new `test_stage2_nav.py` passing. Runner: `cc\pm_stage2_boxscratch.*` (READ/COPY-ONLY, live DB untouched,
  engine/pm_web PIDs unchanged). **STOP if any test fails.** *(Status: PENDING Jack's run — no local Python here.)*
- **Rung 1 — deploy the 7 code files + restart pm_web** (root `az vm run-command`). PRE: box==branch re-hash of the
  7 files (the `95e78c4`/prod-live baseline for the 6 pre-existing? — only `app.py` + `pm.css` pre-exist; the 5
  templates are NEW files) + per-file CODE backup of `app.py` + `pm.css` + baseline (engine/pm_web PID, `/farm` +
  `/scoreboard` + `/` byte lengths, schema 9, watchlist 114/92/22, paper count). **★ tar lands files 664 — force
  `chmod 644` and ASSERT `stat -c '%a' == 644` in the re-hash gate (standing procedure, recurs every deploy).**
  GATE: each deployed file sha256 == the manifest above AND perms 644 AND owner azureuser. Restart
  `prediction-markets-web` ONLY (never the engine). POST: `/healthz` 200 schema 9; `/dashboard` + `/farm-league` +
  `/farm-league/mlb` render 200; `/farm-league/cbb` → 404; **legacy `/` + `/scoreboard` + `/farm` byte-IDENTICAL to
  baseline** (the new routes are additive — legacy pages must not move); engine PID unchanged; pm_web PID changed,
  NRestarts still 0, active/running; schema still 9; no DB write. **prod-live ledger** advances by a fast-forward
  additive commit recording the 7 artifacts (`95e78c4` stays an ancestor — MACE fork base). STOP + rollback
  (restore the 2 pre-existing files' backup + remove the 5 new templates + restart) on any failure.

**No rung beyond deploy** — phase 1 has no DB write, no poller/cron, no migration. Phase 2 is a separate build.

## 7. BOX / SAFETY NOTES

- Cadence is LIVE (`*/30` poller etc.). Rung 1's deploy window must be clear of 05:00 refresh and not straddle a
  `*/30` poll — but phase-1 has **no** DB dependency, so the only timing concern is the byte-identical legacy-page
  post-check (a refresh moving `pm_category_stats` would shift `/` + `/farm`; capture the baseline same-session).
- One benign box cruft noted in orientation: `prediction_markets/db.py.pre_cp3a_20260825T140605Z.bak` (a stale
  `.bak`, never imported). Not touched here; flagged for a later housekeeping pass.
