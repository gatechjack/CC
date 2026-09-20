# PM /live TILES — STANDARD SIZE, MONEY-FIRST, COMPACT GAME LINES (BUILD + DEPLOY 13) — 2026-09-20

**STATUS: DEPLOYED LIVE (DEPLOY 13) 2026-09-20 — after ONE rolled-back attempt (a nested-anchor defect) + a fix.
prod-live `9ff6060c -> 0023e5a7` (FF), tag `pm-live-tile-simplify-deploy13-2026-09-20`. pm_web-only; engine
`trading-corp` 491380 / NRestarts 0 NEVER touched. box == prod-live 47/47. Full deploy record + the rollback: §9
below.** (Build status was: BUILT + TESTED + RENDERED + BOX-TIED + COMMITTED — preserved below.)
Branch `pm-live-tile-simplify-2026-09-20` off `origin/prod-live` @ **`9ff6060c`** (git truth = box), worktree
`C:\Users\AA Incorporado\cc-pm-live-tile-wt`. DEPLOY / PUSH-to-prod-live / RESTART are Jack's reserved actions.

Replaces the Deploy-10 Item-1 LIVE tile (a 2×2 featured-scoreboard block that spilled on an NFL Sunday, stretched the
tile, detached the money block, and let a "JUST CLOSED" banner displace the content). Every tile is now the STANDARD
size, money-first, with at most ONE compact game line (or a single summary line for a football weekend).

--------------------------------------------------------------------------------
## 1. TRUTH + BASELINE

- `origin/prod-live` tip at start = **`9ff6060c`** (the roster-table Deploy-12 docs commit). Branched off it.
- **box == prod-live: 47/47 deploy-surface files byte-identical (CR-stripped)** — `cc/pm_roster_boxshas_ro` vs
  `git show origin/prod-live:<f>`. Zero mismatches. No reconcile finding.
- **Baseline (the handoff/jacks-log command on `.venv-webtest` at the tip):** **24 FAILED** (test_live_r3 ×14,
  test_accounts_m2 ×3, test_stage2_nav ×2, test_stage2_phase3 ×2, test_ctx_pagination_fix ×1,
  test_refresh_stagger_and_lookback ×1, test_rung3_observability ×1) — all cross-tree / env-gap, none this UI. The
  differential is the gate.

--------------------------------------------------------------------------------
## 2. THE DEFECT + THE RULINGS

Jack's screenshot (Jack tab, NFL Sunday 2026-09-20): six underway NFL games → the LIVE tile's event block spilled,
the tile stretched, the money block detached and floated below the grid, and the "JUST CLOSED" banner displaced the
tile content. Ruling: tracking live scores on the tile page is no longer the priority; **money is.** R1–R8 supersede
Deploy-10 Item 1: uniform standard-size tiles (no 2×2), money-first content order, ONE compact game line (≤2 games)
or a single summary line (>2), the LIVE border stays, the just-closed banner is removed (flash + LAST line only),
scores live only on the detail page, and the 2×2 code path is deleted (not hidden).

--------------------------------------------------------------------------------
## 3. WHAT CHANGED

- **`live_view._live_event` (rewritten).** Was `{featured (scoreboard + ≤3 position rows), others (≤3 chips), more,
  n_live}`. Now `{games: [{matchup, positions_shorthand}] (only when ≤2 underway games), game_count, position_count,
  summary_only}`. Same underway grouping as before (MLB via game_key → feed is_live; other categories via the
  ticker/milestone start-time, not future, not finalized). **NO score/inning/clock** (R7). The matchup is
  ticker-derived (`_ordered_teams`, team map) → feed-independent + FAIL-CLOSED: a game whose ticker yields no matchup
  (tennis/ufc/fed/unmapped) is COUNTED but never labelled with a ticker (R2/R3). Deleted the now-dead `_score_detail`
  + `_event_rows` helpers (R8).
- **`pm_subs_liveline.html` (NEW partial).** ≤2 games → one fixed-height line per game `AWAY @ HOME · <shorthand> ·
  <shorthand>` (the existing signed shorthand); >2 games → one `N games live · M positions ›` link to the detail
  page. `white-space:nowrap; text-overflow:ellipsis` → truncates with "…", never wraps, tile never grows (R3).
- **`pm_live_list.html`.** Dropped the `pm_subs_event.html` include; the compact game line renders AFTER the money +
  open block (R2 money-first — money is never displaced). Content order (all states): header → REALIZED TODAY hero →
  booked · W–L · unbooked → OPEN (N of M priced + age) → [LIVE compact line] → LAST close line → week/month/all-time
  foot → state corner tab.
- **`pm_subs_event.html` DELETED** (the 2×2 featured-scoreboard/chips block, R8).
- **`pm_desk.css`.** Removed the 2×2 span (`grid-column/row:span 2`), the fixed `height:480px`, the whole event-block
  CSS (`.evt/.scr/.plist/.pr/.evchip/.evmore/.pr-more`), and the `.ev.placed/.ev.closed` pulse-banner rules. KEPT the
  blue LIVE border + corner tab (R4) and the flash keyframes/classes (R5). Added `.liveln`. ?v `b07fce48 → 585ea101`.
- **`pm_live_subs.js`.** Removed the JUST-PLACED / JUST-CLOSED **banner** (it inserted a `.ev.pulse-banner` before
  `.mny`, displacing the money block — R5) + the now-unused `money`/`esc` helpers. KEPT the brief tile flash
  (`fx-placed` / `fx-closed-won` / `fx-closed-lost`); the tile's LAST line updates via the normal per-cycle server
  re-render (`swap()` runs before `applyEvents()` each cycle, so the flash fires on the already-refreshed tile).
- **`pm_shell.html`.** `pm_desk.css?v` bumped to `585ea101`. (pm_live_subs.js is versioned at runtime, `subs_js_v`.)

--------------------------------------------------------------------------------
## 4. FILE DIFF vs prod-live (CR-stripped sha16 BEFORE → AFTER)

**★ NEITHER app.py NOR subdivision.py is touched.** No engine-shared file (subdivision.py/db.py untouched), no
migration. Pure pm_web (live_view is pm_web-only; templates/static/shell).

| file | BEFORE | AFTER |
|---|---|---|
| `web/live_view.py` | `233ed9a28fd325f4` | `5763057e2e757841` |
| `web/templates/pm_live_list.html` | `92693d07936187d4` | `af8e32102b44e0ff` |
| `web/templates/partials/pm_subs_liveline.html` | ABSENT (new) | `9b83b4a78793b219` |
| `web/templates/partials/pm_subs_event.html` | `51ebf8ce7e18378e` | **DELETED** |
| `web/static/pm_desk.css` | `b07fce488bb0634c` | `585ea1011a67f695` |
| `web/static/pm_live_subs.js` | `fe29f6e59d972a16` | `87888c85eb7b84c7` |
| `web/templates/pm_shell.html` | `ec3a5519f3c0991e` | `3abb631977586ba8` |

Git-only (NOT deployed to the box): `tests/prediction_markets/test_live_fixes_item1.py` (rewritten),
`test_live_tile_simplify.py` (new), `test_milestones.py` (2 atp tests updated), this report.

Commits on `pm-live-tile-simplify-2026-09-20`: `0f1be25e` (live_view + tests), `c8f06fdc` (templates/css/js), + this
report.

--------------------------------------------------------------------------------
## 5. DEPLOY-10 TESTS REPLACED (not deleted silently)

`test_live_fixes_item1.py` was the Deploy-10 Item-1 suite for the featured/chips structure. Rewritten (module docstring
records this):

| old assertion | disposition | why |
|---|---|---|
| `test_featured_is_closest_to_settling`, `test_tie_breaks_most_held_then_away_code` | **DELETED** | there is no "featured" game any more — no scoreboard, no deterministic settling pick; every underway game is just a line. |
| `test_others_capped_at_three_with_more` | **REPLACED** → `test_three/six_live_games_summary_line` | >2 underway games now collapse to ONE summary line (game_count / position_count), not 1 featured + 3 chips + "+N more live". |
| `test_featured_positions_capped_at_three` | **DELETED** | no per-game position rows on the tile any more (scores + positions live on the detail page, R7); the tile shows the signed shorthand joined on one line, truncated by CSS. |
| `test_none_when_no_underway_game` | **KEPT** (unchanged) | same behaviour — None when no underway game. |
| — | **ADDED** | line count at 0/1/2/3/6 games; the summary line; unjoinable-counted-not-labelled (R2/R3); mixed joinable+unjoinable labels only the joinable. |

`test_milestones.py` — the 2 atp tests (`test_live_event_block_non_mlb_via_milestone`,
`test_build_subdivisions_context_classifies_atp_live_via_milestone`) asserted the `featured` structure; updated to the
new structure. atp/tennis is unjoinable (no team map), so the new expectation is game_count=1 / position_count=1 /
`games`=[] (counted, never labelled) — the honest non-MLB behaviour.

--------------------------------------------------------------------------------
## 6. VERIFICATION

- **Tests (`.venv-webtest`):** full-suite differential = **24 FAILED after == 24 baseline, 0 new failures**. The
  new/affected files: **51 tests pass** (test_live_fixes_item1 8 + test_live_tile_simplify 7 + test_milestones 36).
- **Renders (`cc/pm_tile_render.py`, viewed):** `cc/renders_tile/` — `tiles_1600` / `tiles_1280` (Jack tab: LIVE
  section with a 1-game tile [NBA `LAL @ BOS`], a 2-game tile [CFB, two lines], a 6-game tile [NFL, summary
  `6 games live · 9 positions ›`]; UPCOMING [NHL]; SETTLED [MLB] — every tile standard-size, grid intact, money block
  in place, no 2×2). **Measured heights:** the three LIVE tiles are all **373px** (uniform within the LIVE grid row —
  CSS stretch); the old 2×2/480px tile is gone (max tile on the page is a 391px UPCOMING tile, content-sized like
  every other state). `grid overflow @1600: scrollWidth==clientWidth` (no overflow). `tiles_phone` (390px): **the
  tile grid is one column with NO overflow** (`grid OK`); `tiles_justclosed_flash` (the MLB tile mid-flash — money
  block + LAST line intact, **NO banner**).
- **★ HONEST FINDING (pre-existing, NOT this change):** at a phone width the PAGE overflows (~908px) from the SHELL
  HEADER nav + poll/arm chips + the account TABS (`nav`/`badge`/`B`/`EM`) — the tile grid itself is 1-column and does
  not overflow. Same shell/header phone-overflow filed at Deploy 12; unchanged here (only the tiles were touched).
- **Box RO (`cc/pm_tile_boxdump_ro` dumps jack's live open positions; MY `_live_event` run on that real data —
  `cc/renders_tile/box_tieout.txt`):** on the REAL NFL Sunday, **jack/nfl holds positions on 7 underway games →
  `game_count=7 position_count=7 summary_only=True`** = ONE summary line `7 games live · 7 positions ›`, and
  **position_count (7) TIES to the detail-page open count (`len(live_positions)`=7)** — exactly the defect scenario
  (six-plus underway games), now a single compact line whose count ties to the detail page. jack/wta (tennis,
  unjoinable) → counted (1/1) but not labelled. **Tile-height uniformity against real data is proven by construction**
  (the 2×2/480px path is deleted — CSS/template tests + the seeded render on the identical template+CSS): no tile can
  exceed the standard height because the oversized code path no longer exists.

--------------------------------------------------------------------------------
## 7. DEPLOY SHAPE (for Jack — reserved)

**pm_web-only. NO engine-shared file, NO migration. Engine `trading-corp` NEVER restarted / NEVER touched.**
- **Model:** branch off `origin/prod-live` = box = truth, edit directly. 7 deploy files (§4): 6 modified + 1 deleted
  (`pm_subs_event.html`) + 1 new (`pm_subs_liveline.html`).
- **Deploy:** plain scp+tar diff onto prod-live, drift-gated (box == BEFORE for all, CR-sha16), **backup-is-a-gate**
  (dated dir outside the service path, each sha verified == box before any write; INCLUDE the delete — back up
  `pm_subs_event.html` before removing it), post-write CR-sha16 == AFTER (+ absence of the deleted file) +
  `py_compile live_view.py` + import/standalone gate (`/pm/arm`=0, 0 engine/broker modules), roll back all on any
  mismatch.
- **ONE `az vm run-command … 'systemctl restart prediction-markets-web'`** (a ~2s UI blip). Confirm by MainPID change
  + ActiveEnterTimestamp (never the az exit code / empty stdout). Verify `trading-corp` PID + NRestarts UNCHANGED
  before and after every step.
- **Cache-bust:** `pm_desk.css?v=585ea101` — verify the served file sha8 + the shell `?v=` match after deploy.
- **prod-live advance (SAME session, after post-check green):** fast-forward `origin/prod-live` to the deployed commit
  + tag; re-verify box == prod-live. FF-only; non-FF → STOP. main untouched. **Report the FF push command with the
  deploy:** `git push origin pm-live-tile-simplify-2026-09-20:prod-live` (FF-only) + `git push origin <tag>`.

--------------------------------------------------------------------------------
## 8. RUNNERS (cc/, read-only)
`pm_roster_boxshas_ro` (box == prod-live 47/47), `pm_tile_render.py` (the seeded 1/2/6-game + just-closed + phone
renders in `cc/renders_tile/`, with measured heights + overflow diagnostics), `pm_tile_boxdump_ro.{ps1,py}` (dumps
jack's live open positions RO) + the local `_live_event` tie-out (`cc/renders_tile/box_tieout.txt`). Deploy 13 runners:
`pm_deploy13_precheck_ro`, `pm_deploy13_graft` (backup+write6+delete1+grep+rollback), `pm_deploy13_restart_az`
(az-root, Jack-run), `pm_deploy13_postrestart_ro`, `pm_deploy13_postcheck_ro`, `pm_deploy13_journal_ro`,
`pm_deploy13_fetch_served`, and the rollback runners `pm_deploy13_restore` + `pm_deploy13_cleanup_leftover`.

--------------------------------------------------------------------------------
## 9. DEPLOY 13 — LIVE ON PROD 2026-09-20 (board-authorized; ONE rolled-back attempt + a fix)

**SHIPPED `0023e5a7` (branch pm-live-tile-simplify-2026-09-20) off prod-live `9ff6060c`. prod-live FF `9ff6060c ->
0023e5a7`, tag `pm-live-tile-simplify-deploy13-2026-09-20`. pm_web-only (6 files written + 1 deleted); engine
`trading-corp` 491380 / NRestarts 0 NEVER touched. box == prod-live 47/47 before AND after.** Deploy target was
recorded as `1d39a1bf` (§4 shas) but the FIRST graft's summary line escaped its tile (see below) -> rolled back ->
fixed -> the DEPLOYED code is `0023e5a7` (the fix commit); the only sha that changed from §4 is
`pm_subs_liveline.html` `9b83b4a78793b219 -> 5809621dc39e9d4e`.

**Pre-deploy (RO, green — `pm_deploy13_precheck_ro`, `pm_roster_boxshas_ro`):** engine `trading-corp` MainPID **491380**
NRestarts 0 active; pm_web **494253**; schema head **24**; heartbeats fresh; pm_live arm rows 69; prod-live `9ff6060c`;
box == prod-live **47/47**; served pm_desk.css `b07fce48`. Before /live: jack/nfl **7 open / 7 games**, jack/wta 1/1.

**★ ATTEMPT 1 (rolled back) — a NESTED-ANCHOR defect.** The graft (backup `/home/azureuser/pm_deploy13_backup_
20260920T221244Z`) applied 6 + deleted the partial cleanly; the first `pm_subs_event` grep FALSE-aborted on
`pm_subs_eventline.html` (a substring match; the runner rolled back cleanly) -> tightened to `pm_subs_event\.html`
and the rollback to also `rm` the new file (a new file has no backup to restore); a leftover `pm_subs_liveline.html`
from that first rollback was removed by `pm_deploy13_cleanup_leftover` (box re-verified 47/47). The 2nd graft succeeded;
pm_web restart **494253 -> 496311** (ActiveEnter 22:37:44Z), engine unchanged. **Post-check (86 OK) then caught the
real defect:** the `>2-games` summary was `<a class="liveln more" href>` nested inside the tile's own
`<a class="t live" href>`. Nested `<a>` is invalid HTML -> the browser closes the tile early -> on jack/nfl the
summary + LAST line + week/month foot + state tab **escaped the tile box** (Jack's screenshot confirmed it). Per the
brief (rollback on a 7-13 fail, never fix forward on prod): **ROLLED BACK** — `pm_deploy13_restore` restored the 6
files from backup + removed the new partial (each verified == prod-live sha), pm_web restart **496311 -> 497016**
(22:53:58Z), verified serving prod-live (my `liveln` absent, old event-block restored, pm_desk.css `b07fce48`, all
pages 200), engine 491380/0 unchanged. box == prod-live 47/47.

**THE FIX (commit `0023e5a7`):** the summary line is now a `<div class="liveln more">`, not a nested `<a>`. The whole
tile is already `<a class="t live" href="{{ t.href }}">`, so clicking the line still navigates to the detail page; the
`›` chevron signals more. Geometry re-check (seeded + the live served page): every `.liveln` (incl. the summary) sits
INSIDE its tile bbox (`liveln-inside-tile` = true). Full suite **24 == baseline, 0 new failures**; 51 tile tests pass.

**ATTEMPT 2 (LIVE) — the corrected build.** Graft (backup `/home/azureuser/pm_deploy13_backup_20260920T230612Z`):
GATE 1 staged == TARGET (6/6, liveline now `5809621d`); GATE 2 box == BEFORE (5 modified + new absent + delete file
present); GATE 3 backup verified; APPLY 6 + DELETE the partial; VERIFY 6 == TARGET + deleted absent; grep 0
`pm_subs_event.html` refs; py_compile OK; import gate routes=24 / pm_arm=0 / forbidden=[]. Restart
**497016 -> 497708** (ActiveEnter **2026-09-20 23:09:01Z**), engine **491380 / NRestarts 0 UNCHANGED**.

**Post-deploy (RO, `pm_deploy13_postcheck_ro` = 89 OK / 0 FAIL):** /live all 4 tabs (jack/karen/marc/trey) 200, no
2×2/scoreboard markup, no just-closed banner, `?v=585ea101`; **REGRESSION GUARD: 0 nested-anchor summaries
(`<a class="liveln more"`) anywhere**; jack/nfl `>2 games -> a single `<div class="liveln more">` summary` ("4 games
live · 4 positions"); the summary M (4, positions on LIVE games) `<=` the tile OPEN count (6) — a subset, because 2
of jack/nfl's 6 open positions are on games not currently underway (incl. a Sep-21 Monday-night game), which is
correct + honest (the earlier 7/7 all-underway snapshot is the M==open case). Detail `/live/kalshi_jack/nfl` unchanged
(Deploy-12 roster table + 5-cell strip intact); `/live/kalshi_jack/mlb` game-card page 200. All pages + 26
`/farm/{cat}` + all static 200; **served pm_desk.css sha8 == `585ea101`**, `pm_subs_event.html` 404 (deleted). 0 raw
tickers on /live, 0 double-escaped. **Geometry on the live served page:** NFL + WNBA `liveln-inside-tile` = true;
per-section heights uniform (LIVE 338, UPCOMING 314, SETTLED 275, INACTIVE 192 — no 2×2). pm_web `journalctl -p err`
since restart = **0**; engine `journalctl -p err` = 0; engine 491380/0 UNCHANGED.

**Wrap:** FF `origin/prod-live 9ff6060c -> 0023e5a7`; tag `pm-live-tile-simplify-deploy13-2026-09-20`; box ==
prod-live **47/47** re-verified (pm_subs_event.html gone, pm_subs_liveline.html present — net 47, NOT 46: the delete
is offset by the new partial). main untouched.

**LESSONS (two, worth keeping):** (1) a tile is an `<a>` — a link inside a tile must be a `<div>` (or the tile's own
link), never a nested `<a>`; the post-check now guards it. (2) a graft that CREATES a new file must, on rollback,
`rm` it (a new file has no backup to restore) — the first rollback left `pm_subs_liveline.html` behind; the runner
now removes new files on rollback.
