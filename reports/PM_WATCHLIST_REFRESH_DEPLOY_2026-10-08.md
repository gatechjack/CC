# WATCHLIST REFRESH BUTTON -- PHASE 0b + BUILD + DELIVER (2026-10-08)

Branch **pm-watchlist-refresh-2026-10-08 @ 47a9af49** off verified BASE **origin/prod-live 2532eee6**
(re-verified at build time; the brief's 403d774a was already two folds stale). pm_web ONLY. Nothing applied,
restarted, pushed. Phase 0 trace: PM_WATCHLIST_REFRESH_PHASE0_2026-10-08.md (Q3 clearance = engine reads none of
the refresh-written tables; copy path runs off the whales' /positions feed).

## PHASE 0b -- measured BEFORE building (the gate: would a refresh return more than 12?)  ANSWER: (a) STALE -> BUILD
READ-ONLY, called the SAME endpoint refresh_one uses (ingest._pull_closed -> /closed-positions), wrote nothing:
- wallet 0x27f738fe...44b0: **upstream /closed-positions = 3593 rows, complete=True** (the API's FULL set, short
  final page -- NOT cap-truncated) vs **stored pm_closed_position = 3082**, newest stored row **~29-30 days old**.
- Tier-1 category bucket of the fresh 3593: **cfb = 245** vs **cfb stored now = 12**.
=> The basis is STALE, not API-capped. The 12W/0L case-(b) tell is DISPROVEN by measurement: the panel shows 12
   because the nightly `refresh` cron does not cover this wallet (29d stale) AND cfb is a small slice of the book.
   A refresh brings cfb 12 -> ~245 (well past the 30-position Analyze floor). The button delivers real data.
   (NB: "332" in the brief is the PAPER-trade count, a different basis; the completed-cfb basis lands ~245.)

## WHAT WAS BUILT (Option B -- mirror refresh_one exactly, per Watchlist row)
3 pm_web files. No engine / gate / matcher / order-path / settlement / paper-cron change. No migration. No CSS.
- **app.py** (BASE e49e337148b41bbc -> TARGET 6693cb1e19ce8139): ONE shared handler now serves both tables --
  `POST /farm/{cat}/refresh/{wallet}?from=watchlist` re-renders the Watchlist fragment (the pinned whale is not in
  Prospects, so re-rendering Prospects would omit it); no `?from` keeps the Prospects path byte-for-byte. Added a
  SERVER-SIDE single-flight (`_refresh_inflight`) so a double-POST never starts a second ~30-call pull -- a SHARED
  fix that also closes the Prospects path's prior UI-disable-only gap. `_load_farm_category` now enriches each
  Watchlist row with the COMPLETED-basis resolved count (pm_category_stats) + its last-refresh age
  (pm_whale.last_refresh_ts) -- READ-ONLY; farm.py's shared PINNED query is untouched.
- **pm_farm_category.html** (fbfd37909cc5e51d -> 63e39e433ee97255): wrap the Watchlist include in the htmx swap
  target `#pm-watchlist-rows` (mirrors `#pm-prospects-rows`).
- **pm_watchlist_rows.html** (a94440f4e3dfc1b4 -> 8fc2f9cc24b2de96): the per-row Refresh button in ACTIONS
  (mirrors the Prospects form: same route, admin-only, POST-only, hx-disabled-elt, JS-off 303) targeting the
  Watchlist fragment with ?from=watchlist; a labelled COMPLETED-basis cell (n + age, guarded with `is defined` so
  a bare-Jinja render degrades to "--" instead of raising); and the refresh-outcome notice. ★ does NOT chain
  Re-analyze (Analyze spends the $20/day cap -- refresh, look, then Analyze). An inline "refreshing..." indicator
  shows the ~1-min work in progress (the one deliberate add over the Prospects button, which disables alone).

The paper columns (open/closed/win%/ROI/net pnl/cost) are a SEPARATE basis (pm_paper_category_stats + pm_paper_trade,
30-min poll + 05:50Z daily rollup) and are NOT touched by this button; the completed cell is explicitly labelled so
the two bases are never confused on one row.

## TESTS -- actual counts (box venv, pytest -p no:pytest_ethereum [a broken venv plugin, unrelated])
tests/prediction_markets/test_watchlist_refresh.py: **9 passed** (0 failed). Real handler via TestClient + real
ingest/rollup with a FAKE Polymarket client (no network):
  1 render (button on every row + completed cell, nowhere it shouldn't)  2 ★ ACCEPTANCE: refresh raises stored
  resolved 3 -> 8 and the new count shows on screen  3 ★ fail-safe (429 + timeout): stored UNCHANGED + visible error
  4 double-POST single-flight (server-side, not the UI disable)  5 paper tables BYTE-identical after a refresh
  6 Prospects refresh unchanged (no-?from still renders the Prospects fragment)  7 roster snapshot identical before/
  after + a static engine-source guard (driver_roster/execution reference NONE of the 6 refresh-written tables).
REGRESSION -- every test that renders the watchlist template / farm page: test_farm_sort, test_farm_live_whale_state,
test_splits_grid_enrich, test_watchlist_splits, test_stage2_nav, test_stage2_phase3, test_prospects_score ->
**all pass EXCEPT 4 pre-existing stale-UI shell-nav asserts** (test_stage2_nav::{dashboard_route_resolves,
farm_league_tiles_are_the_allowlist}, test_stage2_phase3::{root_serves_dashboard, whale_pages_render_under_one_shell}).
PROVEN pre-existing: the SAME 4 fail IDENTICALLY on the untouched BASE 2532eee6 (ran base + branch side by side) --
they assert ">Accounts</a>" in a shell nav this build never touches. Not my regression.

## RESTART? -- YES, a pm_web restart (establish, don't assume)
The change touches app.py (new handler + enrichment bytecode). pm_web runs single-worker uvicorn with NO autoreload
-> the cached bytecode will NOT pick up the new handler until the process restarts. (Templates would hot-reload on
their own via Jinja auto_reload, but they depend on the new handler context, so they go live WITH the restart.)
SERVICE: `prediction-markets-web.service` (currently PID 586747, boot 2026-09-27T23:44:22Z). User-visible downtime:
the PM web UI is unavailable for the ~1-2s restart; the trading ENGINE (trading-corp, PID 678426) is NOT touched ->
zero trading impact. Box is at BASE now (3 files CR-sha == BASE verified), so the drift-gate will pass clean.

## FOUR-LEVEL "DEPLOYED" (only the last counts for a UI change; it is the one that gets skipped)
1. code exists        -- YES: branch 47a9af49 (3 files + tests + reports) off 2532eee6.
2. on the box         -- NO (nothing applied). The staged drift-gated runner (cc/pm_wlrefresh_deploy.sh) applies it.
3. in the running proc-- NO until Jack restarts prediction-markets-web (single-worker, no autoreload).
4. reachable+clickable-- NO until (2)+(3): then GET /farm/<cat> -> the Watchlist ACTIONS column shows Refresh on
   every row + the completed cell; clicking re-pulls + re-renders in place. THIS is the only level that counts.

## ACCEPTANCE STEP FOR JACK (predicted IN WRITING, before the click)
After deploy + restart, click **Refresh** on `0x27f738fe203827445690339104aae35b20bc44b0` in **cfb** (Watchlist row):
- PREDICTED: the completed cell's **n goes 12 -> ~245** (the measured tier-1 cfb count; low-to-mid 200s -- a few
  more if tier-2 reclassifies some "unknown" into cfb, a few fewer if any cfb rows are cost_basis<=0 quarantined).
  Definitively >> 12 and past the 30-position floor. The freshness dot flips from ~29d to 0d. The success notice
  reads "Refreshed -- ~245 resolved position(s) now on the COMPLETED basis...". The paper columns do NOT change.
- THEN (separate click, Jack's choice -- not chained): **Analyze** re-judges on the fresh basis; N RESOLVED in the
  Analyze panel rises 12 -> ~245 and the "well short of the 30 needed" verdict is replaced by a real judgement.

## DEPLOY (Jack authorizes; I apply nothing)
Stage: scp the branch git-archive to /home/azureuser/cc_wlrefresh_src.tar, then run cc/pm_wlrefresh_deploy.sh
(drift-gate box==BASE/staged==TARGET -> backup /home/azureuser/pm_wlrefresh_backup_<ts> -> apply -> verify
live==TARGET -> py_compile+import -> rollback-all on any failure). Then Jack restarts prediction-markets-web.
prod-live FF (deploy not complete until prod-live carries it): origin/prod-live 2532eee6 -> 47a9af49 (straight FF).
