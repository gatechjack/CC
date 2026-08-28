# Stage 2 · Phase 3 — REPOINT AND RETIRE (BUILD ONLY, box-scratch PENDING, NOTHING LIVE)

**Date:** 2026-08-28 · **Branch:** `prediction-markets-stage2-phase3-2026-08-28` (worktree `cc-pm-stage2-phase3-wt`,
off phase-2 record tip `2d6e00f`) · **Mode:** BUILD ONLY. No live deploy, no restart, no DB write, no
poller/adjudicate/rollup, no migration. Stage 3 NOT authorized.
**Three lists / three bases (anti-drift):** Prospect = completed (`pm_category_stats`, code `candidate`); Watchlist
= our paper (`pm_paper_category_stats`, code `pinned`); Live = P3.

**Branch choice:** a NEW branch off `2d6e00f` — phase 2 is deployed (prod-live ledger `2916f44`), so the phase-3
diff stays isolated. **Ledger-chain shape (stated before creating it):** the phase-3 prod-live ledger will commit
ON TOP of `2916f44` → `8563c62` → `7ca932a` → `2916f44` → `<phase-3 ledger>`, one linear line, not pushed.

---

## THE 6-ITEM PHASE-3 CHECKLIST — ALL DONE

1. **Repointed** `/dashboard`→`/`, `/farm-league`→`/farm`, `/farm-league/{category}`→`/farm/{category}` (the same
   handlers, moved to the good URLs; the temp routes no longer exist).
2. **Retired** the old pages + handlers: `scoreboard_page` (`/`) + `scoreboard_table` (`/scoreboard`); the flat
   `farm_page` (`/farm`) + `farm_list` (`/farm/list`); their loaders (`_clamp_params`, `_load_scoreboard`,
   `_context`, `_clamp_farm_category`, `_load_farm`, `_farm_context`) + `ALLOWED_ROUTINES`. **`/scoreboard` the PAGE
   is gone; `query_scoreboard` the FUNCTION stays** (it is the Prospects ranker — different thing, similar name).
3. **NO permanent aliases** — `/dashboard`, `/farm-league`, `/farm-league/{category}` are removed (they 404), not
   redirected. (No redirect was warranted; the nav + breadcrumbs already point at the good URLs.)
4. **Fixed internal links** — `pm_shell.html` nav (brand→`/`, Dashboard→`/`, Farm League→`/farm`); the breadcrumbs
   in `pm_dashboard`/`pm_farm_league`/`pm_farm_category`/`pm_watchlist_whale`/`pm_category_404`; **and the whale
   pages' stale `← scoreboard` crumb** (→ `← {CATEGORY}`/`← Farm League`). Swept templates AND Python: no
   `/dashboard` or `/farm-league` reference remains except retirement-documenting comments.
5. **Base-template consolidation (MINIMAL, per the provisional-theme ruling):** `pm_whale.html` +
   `pm_whale_overview.html` now `{% extends "pm_shell.html" %}` (one-line each); **`pm_base.html` DELETED**. There is
   now **ONE shell** (`pm_shell.html`), not two. Retired flat templates: `pm_scoreboard.html`,
   `partials/pm_scoreboard_table.html`, `pm_farm.html`, `partials/pm_farm_lists.html`.
6. **Nothing points at a retired route** — verified by grep (templates + Python) and by the render tests (a dangling
   `extends`/`include` would 500).

## DEPLOY ARTIFACT SET — 9 MODIFIED + 5 DELETED (no new files)

| sha256 (branch) | MODIFIED file (under `trading_corp/prediction_markets/`) |
|---|---|
| `1b7b2534afca9caff0f2ff18cf80f8b679af7d06fb8d0c211909014ab30c0cc9` | `web/app.py` (routes repointed; legacy handlers/loaders removed) |
| `d9d82f074bb384bf3968731fa0aa4dd3e84ac192f2dabd43f0fd1226df99a1f8` | `web/templates/pm_shell.html` (nav → `/` + `/farm`) |
| `90fcad6634332d5ab0ea7eaacd6e3e75886604019ae6be319d4bb729ce9ebfea` | `web/templates/pm_dashboard.html` |
| `5509651cf47d6fa525024e455601107e18bf550535b80511b0d8b5fab8b53ef5` | `web/templates/pm_farm_league.html` |
| `bab07b6a0745f4a066ddc3499d2751c2b9108e4a9395521ad0211531d830cf8d` | `web/templates/pm_farm_category.html` |
| `9c8cb7dbe84bd4b4a2a0427ed57f00df6f2e36f29af686bea76f93e82889747a` | `web/templates/pm_watchlist_whale.html` |
| `77e421936d2f5f44f8f89be660b82a7dd62765f889741801d56b650c04b63c15` | `web/templates/pm_category_404.html` |
| `a51032997eff9c98426f05486009e271b87a79398f943a0b4f25933f455f5b01` | `web/templates/pm_whale.html` (extends pm_shell; crumb fixed) |
| `4bf7a49288cec5c6eed8ea0949b5cd1dae48da9d068dc75d07d008187eac6d77` | `web/templates/pm_whale_overview.html` (extends pm_shell; crumb fixed) |

**DELETED (5, must be removed from the box):** `web/templates/pm_scoreboard.html`,
`web/templates/partials/pm_scoreboard_table.html`, `web/templates/pm_farm.html`,
`web/templates/partials/pm_farm_lists.html`, `web/templates/pm_base.html`.
**NOT touched:** `stats.py`/`query_scoreboard`, `farm.py`, `positions.py`, `paper.py`, `db.py`, `pm.css`, any
migration, the engine, poly_kalshi_mlb, MACE, PEAD, bitunix.

## ★ WHAT JACK SEES AT predictions.jacksumner.com AFTER THIS DEPLOYS

- **Opening the site lands on `/` = the DASHBOARD** (it was the flat scoreboard). Heading "Predictions Market
  Dashboard"; a **visibly disabled** "Live sub-divisions — coming in P3" card; an active **"Farm League →"** card
  showing the live category count.
- **The nav bar offers exactly:** **Dashboard** (`/`) · **Farm League** (`/farm`) · **Live sub-divisions**
  (disabled, P3). The old **Scoreboard / Farm league / Search / Paper** nav is **gone** — that legacy nav is what
  sent Jack to the wrong page; it no longer exists.
- **Farm League → `/farm`** = the 15 category tiles. **A category → `/farm/{cat}`** = the per-category page. **ufc**
  shows the Watchlist with **Kh4mz4t (100% / +90)** and **evanng (100% / +11)** and the live open counts — the real
  paper content, now on the good URL. Prospects: honest-empty.
- **The old URLs are gone:** typing `/scoreboard`, `/dashboard`, `/farm-league`, `/farm-league/ufc`, `/farm/list`
  all return **404** — no page nobody visits, no alias.
- **Whale + watchlist detail pages** render under the **same one shell** (same nav, same theme).

## §H CHECKPOINT ANSWER

*Which of the three lists did this touch, and did the three bases stay separate?* NONE of the three lists' DATA
changed — phase 3 is pure presentation plumbing (routes + templates). Watchlist still reads paper, Prospects still
reads completed (`query_scoreboard` untouched), Live is P3. The bases stay separate; this phase only moved the
screens onto the good URLs and deleted the flat ones.

## THE PROOF INVERTS (phases 1-2 proved byte-IDENTICAL; this DESTROYS the legacy pages)

- `/` and `/farm` must **CHANGE** (they now serve the dashboard + tiles). Byte-identical would mean the repoint
  didn't take. The box-scratch render smoke shows the new content (`pm-menu-card` on `/`, `pm-tilegrid` on `/farm`).
- `/scoreboard` → **404** (retired; considered choice — no redirect, per "no aliases").
- The temp `/dashboard`, `/farm-league`, `/farm-league/{category}` → **404** (removed; leaving them = the forbidden
  alias). Plus `/farm/list` → 404.
- `/farm/{category}` renders the **same filled content** the temp path did — the ufc closed whales survive the move.
- `/healthz` keeps working.

## PHASE-3 RUNG LADDER (Stage-0/1/2 shape; NOTHING below is authorized — HALT here)

- **Rung 0 — BOX-SCRATCH GREEN (the gate).** `cc\pm_stage2_p3_boxscratch.ps1` (READ-ONLY): full pytest suite
  (`-p no:pytest_ethereum`) incl. `test_stage2_phase3.py` + the updated nav/phase-2/farm suites; an **inverted**
  render smoke vs a `.backup` copy of live (`/` = dashboard, `/farm` = tiles, `/farm/ufc` = the real paper content
  with Kh4mz4t/evanng surviving, the old + temp paths 404, whale pages under pm_shell, vocab clean). Live DB
  byte-untouched; engine/pm_web PIDs asserted unchanged. **STOP if any test fails.** *(Status: PENDING Jack's run.)*
- **Rung 1 — deploy 9 MODIFIED + REMOVE 5 DELETED + restart pm_web.** ★ **Custody covers modified / new / DELETED:**
  - **9 MODIFIED:** pre-place `box==BASE 2916f44` custody + per-file CODE backup; place NEW `<phase-3 tip>` blob;
    **force `chmod 644` + assert perms==644** (standing tar-664 drift).
  - **5 DELETED:** back up the box copy of EACH to the backup dir FIRST (rollback material), then `rm` it, then
    **assert absent**. (No new files this phase.)
  - **★ ROLLBACK IS DIFFERENT — it must restore DELETED pages:** the backup dir holds ALL 14 (9 modified + 5
    deleted). Rollback = copy the 9 modified back **and copy the 5 deleted back into place**, then restart pm_web.
    A per-file backup of only the modified files would be insufficient — a rolled-back app.py would reference
    `pm_scoreboard.html`/`pm_farm.html` which no longer exist → 500. The deleted files are ALSO recoverable from git
    `prod-live @ 2916f44`. **Cost/time:** ~14 file copies + one pm_web restart (seconds); no DB involvement.
  - **Restart pm_web ONLY** (engine never referenced). **Inverted POST:** `/` + `/farm` CHANGED (dashboard + tiles;
    capture pre/post content signature); `/scoreboard` + `/dashboard` + `/farm-league` + `/farm-league/{cat}` +
    `/farm/list` → 404; `/farm/ufc` renders Kh4mz4t/evanng; `/healthz` 200; whale pages under pm_shell; DB untouched
    (schema 9); engine PID unchanged; the 5 deleted files ABSENT on the box.
  - prod-live ledger advances by a fast-forward additive commit ON `2916f44` (records the 9 modified + the 5
    deletions); `95e78c4` stays reachable.
- **Activation path:** all changes activate on the **single pm_web restart** — `app.py` is the entrypoint, the
  templates are pm_web-rendered; the deletions take effect because the running pm_web is replaced by one whose
  routes/templates no longer reference them. **No `pm_cli`-loaded file changed**; **no migration** (schema 9).

## Note — deferred test coverage (stated, not hidden)
The retired `test_scoreboard_render.py` verified caveat RENDERING (upper-bound label, two-sided grain, single-game
n/a, flag parity). That markup now lives in the Prospects partial (`pm_prospects_rows.html`), but Prospects is
**empty today** (no candidates until Stage 4 Search), so those caveats cannot be exercised with real content yet.
Re-creating those tests against the Prospects section is a **Stage-4 task** (when candidates exist). The one
basis-relevant pure-function test (`refresh_band_state` thresholds) was **preserved** in `test_stage2_phase3.py`.

**HALT. Stage 3 is not authorized.** Runner: `cc\pm_stage2_p3_boxscratch.*`.
