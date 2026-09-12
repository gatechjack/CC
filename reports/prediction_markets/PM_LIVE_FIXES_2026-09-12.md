# PM /live — LIVE FIXES (Items 3 -> 1 -> 2) — 2026-09-12

**STATUS: Item 3 + Item 1 BUILT + TESTED + RENDERED + COMMITTED (local). Item 2 SCOPED — a decode finding needs a
ruling before it is built safely (see §5).** NOT deployed / NOT pushed. pm_web-only. Engine untouched.
Branch `pm-live-fixes-2026-09-12` rebased onto `origin/prod-live`.

--------------------------------------------------------------------------------
## 1. REBASE + TRUTH + BASELINE

- `origin/prod-live` tip = **`8fdded34`** (Deploy 9: roster + detach; code `afbcfbea`). Recorded.
- **Rebase result:** the branch (`231dea8a` WIP ui_cache + 2 doc commits, on the OLD tip `b1c552b1`) rebased onto
  `8fdded34` in a fresh worktree `cc-pm-live-fixes-wt2`. **ONE conflict — ui_cache.py** — NOT trivial as the prompt
  expected, because the **milestone deploy** (which also advanced prod-live) added the `starts*` fields to
  `CacheSnapshot`/`update()`, and the WIP added `titles` + the marks-merge to the SAME dataclass/method. Resolved by
  COMBINING both (CacheSnapshot carries `titles` AND `starts*`; `update()` merges marks + accumulates titles AND
  forwards the `starts*`). The 2 doc commits applied clean. Rebased tips: `a7b51b0f`(WIP resolved) + 2 docs.
- **box == prod-live 42/42 web files (CR-stripped)** + subdivision.py (confirmed post-Deploy-9, nothing deployed
  since). No reconcile finding.
- **Baseline (transition §1 command, `.venv-webtest` locally; the box venv gives the same FAILED SET):** **22
  pre-existing UI failures** (`test_live_r3` ×14, `test_accounts_m2` ×3, `test_ctx_pagination_fix` ×1 [env-dependent],
  `test_stage2_nav` ×2, `test_stage2_phase3` ×2). NOT ours to fix. Differential gate = these 22 unchanged.
- The Item-3/Item-1 changes to `pm_live_subdivision.html` did NOT disturb the Deploy 9 roster include line.

--------------------------------------------------------------------------------
## 2. ITEM 3 — MARK CACHE RENDER SIDE (DONE, commit `f4b67d37`)

The WIP (persist titles + merge marks in ui_cache) is now CONSUMED by the render:
- `live_view._base_label` = NEVER a raw ticker (the `describe_market` `<type>:<ticker>` leak is gone; floor
  `<CATEGORY> <MARKET-TYPE>`). `_positions_view` reads `desc` from the PERSISTED titles map (survives a failed/partial
  poll), carries the mark's own `as_of`/`age_sec` + `ever_priced`. `build_live_context` threads `titles` + a
  `mark_status` (ok/error/refresh-age); `build_from_cache` derives them off the snapshot. Poller UNCHANGED (already
  passes the raw per-poll `mk`; the merge lives in `update()`).
- `pm_live_subdivision.html`: an open value shows the last bid banded by its OWN mark age (amber past stale via the
  existing `.chip.stale`); "marks loading" on the first cycle; "no mark" ONLY for a ticker that never returned a bid;
  the coverage caveat adds "refresh failed Nm ago - showing last mark" on a failed/partial poll (existing `.wsm`).
  NO CSS/?v= change.
- **Tests: 9** (all pass). **Render viewed:** `cc/renders/item3_nfl_failed.png` — a simulated failed NFL poll: the
  total shows its last value + amber stale chip, the "refresh failed - showing last mark" note, the never-priced
  spread reads "no mark", labels are "NFL SPR" / "Over 48.5 points scored" (zero raw tickers).

--------------------------------------------------------------------------------
## 3. ITEM 1 — FIXED-HEIGHT LIVE TILE (DONE, commit `f31d3c3c`)

- `live_view._live_event` restructured from N per-game rows -> ONE FEATURED game (closest to SETTLING, DETERMINISTIC:
  baseball latest inning, then most outs, then most held, then away code A->Z) with the full scoreboard + its held
  positions (<=3, "+N more held"), plus every OTHER underway game as a single compact chip (matchup + up to 2
  shorthand+value pairs, "..." overflow), capped at 3 with "+N more live". Returns `{featured, others, more, n_live}`.
- `pm_subs_event.html` renders featured + chips + "+N more live". `pm_desk.css`: `.subs .t.live{height:480px}` (fits
  featured + 3 chips + more; the tile's existing `overflow:hidden` clips) so the tile is a FIXED height regardless of
  game count; `height:auto` on phone (span-1). `pm_shell.html` pm_desk.css ?v= `c5efb60a -> 4102424b`.
- **Tests: 5** (all pass) + 2 milestone tests updated to the `featured` structure. **MEASURED constant height:** the
  LIVE tile is **480px at 1, 2, 4 and 6 games** (getBoundingClientRect). **Render viewed:**
  `cc/renders/item1_eventblock_6.png` — featured "TB 2 · ATL 4 · BOT 6 · 2 out" (ATL marked) with 3 position rows,
  then 3 chips (SD@CIN, HOU@PHI, NYY@BOS), then "+2 more live ›". Grid intact (`item1_live_{1,2,4,6}games.png`).

**Full suite after Items 3+1: 189 tests, 22 failures, 0 NEW failures.**

--------------------------------------------------------------------------------
## 4. FILE DIFF vs prod-live (Items 3+1; CR-sha16 BEFORE -> AFTER)

**app.py + subdivision.py NOT touched** (Item 3/1 are render/cache/template/CSS). No migration. No shared-trio.

| file | BEFORE | AFTER |
|---|---|---|
| `web/ui_cache.py` | `dee87281883f7036` | `1919ca1925ba316a` |
| `web/live_view.py` | `51f916960e7c1174` | `1f930df60b36e041` |
| `web/templates/pm_live_subdivision.html` | `cea30f343374b212` | `d1494f6f84e1ac9e` |
| `web/templates/partials/pm_subs_event.html` | `3dd74d8ee56d3ea5` | `51ebf8ce7e18378e` |
| `web/static/pm_desk.css` | `c5efb60aae99071d` | `4102424be0324829` |
| `web/templates/pm_shell.html` | `7e73fe614372fce5` | `d31e2b60570129b0` |

Git-only: `tests/prediction_markets/test_live_fixes_item{1,3}.py`, this report; `test_milestones.py` (2 tests).

**Deploy shape (Items 3+1):** plain diff of the 6 pm_web files onto prod-live; ONE `prediction-markets-web` restart;
backup-is-a-gate; engine untouched; advance prod-live after post-check. Same proven graft pattern as Deploy 9.

--------------------------------------------------------------------------------
## 5. ITEM 2 — NON-MLB GAME LABELS — SCOPED, NOT BUILT (a decode finding to rule on)

**The transition doc §3 assumed `_ordered_teams` decodes the cfb matchup ("MIZ @ KAN"). IT DOES NOT** —
`_ordered_teams` and `game_key_from_ticker` are MLB-centric and return `(None, None)`/`None` for CFB/NFL. `_short_label`
decodes the TOTAL line everywhere ("+51.5") and the NFL spread ("-3.5 BAL") but NOT the CFB spread (returns raw
"MIZZ7" — its parser assumes <=3-char team codes; CFB codes are 4-char, e.g. MIZZ).

**The decode IS available, standalone-safe** (the transition doc's intended tool): `trading_corp/data/
sports_structural_match.py` (imports only stdlib + data-side maps) + `trading_corp/data/cfb_teams.py`
(**CFB_TEAMS: 269 codes -> 151 schools**, `MIZZ->Missouri`, `KU->Kansas`) + `LEAGUES` (cfb/nfl/nba/nhl/wnba/mlb).
`parse_kalshi_ticker(ticker, cfg)` decodes a GAME ticker's two teams. BUT a **TOTAL/SPREAD ticker has no "yes" team**,
so it needs a GENERAL two-team blob split ("MIZZKU" -> MIZZ|KU) against the map, which carries two correctness
subtleties that must be handled fail-closed + validated against real box tickers (per 2.4) before shipping:
  1. **Blob-split ambiguity** — a split is only safe when EXACTLY ONE partition has BOTH codes in the map; otherwise
     fall back to an honest label (no invented matchup).
  2. **Away/home ORDER** — the ticker gives an unordered pair (the matcher keys on a frozenset); the "AWAY @ HOME"
     order must be confirmed against real tickers (a wrong order is a display nit, not a money error, but should be
     right). The transition doc itself flagged this ("confirm the away@home order matches the matcher's decode").
  3. **cfb 4-char spread shorthand** — `_short_label`'s spread branch needs a fix for 4-char codes.

**Why I stopped here rather than build it now:** Item 2 is a correctness-sensitive decode on REAL-money position
labels, and the transition doc under-specified it (its `_ordered_teams` assumption is wrong). Per the standing
discipline (stop-and-report at forks; surface anomalies; do not rush a fragile decoder), I am surfacing this with a
precise, ready-to-build plan rather than shipping an uncertain decoder at speed. **The build is straightforward once
ruled:** a `_structural_game(tk)` helper (category -> `LEAGUES[cat]`; blob-split fail-closed; away/home from the
ticker convention) + group `_positions_view` by game + shorthand-first rows (drop the SIDE column) + tennis/ufc/fed
single-row + the same NEXT-line label + tests from real cfb/nfl/atp box tickers.

**RECOMMENDATION:** ship Items 3+1 as their own deploy now (they are complete, safe, and independently valuable),
and build Item 2 next with the plan above (I can proceed immediately on your go). Items 3+1 do not depend on Item 2.

--------------------------------------------------------------------------------
## 6. RUNNERS / RENDERS
`cc/pm_fixes_item3_render.py`, `cc/pm_fixes_item1_render.py`; renders under `cc/renders/item3_*`, `item1_*`.
