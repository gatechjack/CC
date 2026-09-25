# PM UI — HANDOFF for the next UI code agent

**STATUS: CURRENT — last updated 2026-09-25 (DEPLOY 19: tables Phase 3 — the three-phase table workstream is now
CLOSED, Deploys 17·18·19 all live). prod-live tip = `60bbe750`, tag `pm-tables-deploy19-p3-2026-09-25`.** (This handoff
lives ON prod-live. For the full table-pass record see `PM_TABLES_BUILD_2026-09-25.md` and the DEPLOY 17/18/19 sections
below.)

**★★ DEPLOY 13 (2026-09-20) — LIVE TILE SIMPLIFY. prod-live `9ff6060c -> 0023e5a7` (FF), after ONE rolled-back
attempt (a nested-anchor defect) + a fix.** The `/live` tile page's LIVE tile is no longer the Deploy-10 2×2
featured-scoreboard block (it spilled on an NFL Sunday, detaching the money block). **THE TILE RULE NOW:** every tile
is the STANDARD size (no 2×2 span, no fixed 480px); content is money-first (header → REALIZED hero → booked/W-L →
OPEN → [LIVE line] → LAST → week/month foot → state tab); a LIVE tile adds ONE compact game line — `AWAY @ HOME ·
<signed shorthand>` per game for **≤2 underway games**, else a single `N games live · M positions ›` summary — NO
score/inning (those live on the detail page). The blue LIVE border + corner tab stay; the just-closed BANNER is gone
(flash + LAST line only). 6 pm_web files written + 1 deleted (`partials/pm_subs_event.html`); NO app.py/subdivision.py,
NO migration. Engine 491380 NEVER touched. Full record: `PM_LIVE_TILE_SIMPLIFY_2026-09-20.md` §9.
  - **ARCHITECTURE.** `live_view._live_event` returns `{games:[{matchup, positions_shorthand}] (≤2 games only),
    game_count, position_count, summary_only}` (was `{featured, others, more, n_live}`); matchup is ticker-derived
    (`_ordered_teams`, fail-closed — tennis/ufc counted-not-labelled). New partial `partials/pm_subs_liveline.html`
    (the compact line / summary); `pm_live_list.html` includes it after the money+open block. Deleted `_score_detail`
    + `_event_rows`. `pm_desk.css` `?v=585ea101`; `pm_live_subs.js` banner removed (flash kept).
  - **★ DEPLOY 10 ITEM 1 IS SUPERSEDED** (the featured-scoreboard 2×2 fixed-height LIVE tile). Scores/innings live
    ONLY on the sub-division detail page now (unchanged there).
  - **★ TWO DEPLOY LESSONS (in the report §9, worth keeping):** (1) a tile is an `<a>` — any link inside it must be a
    `<div>` (the tile's own `<a href>` already navigates), NEVER a nested `<a>` (that closes the tile early → the line
    + foot escape the tile box; this was the rolled-back defect, caught only after the first restart). (2) a graft
    that CREATES a new file must `rm` it on rollback (no backup to restore) — the first rollback left the new partial
    behind. The post-check now guards the nested-anchor; the graft runner now removes new files on rollback.
  - **★ BACKLOG (new): SHELL-HEADER PHONE OVERFLOW.** At a phone width the whole page overflows (~900px) from the
    shell header nav + poll/arm chips + the account tabs row (NOT the tile grid, which is 1-column). Pre-existing
    (surfaced at Deploys 12 + 13); its own small pass — make the shell header/tabs wrap or scroll-contain on phone.

**★★ DEPLOY 12 (2026-09-20) — ROSTER TABLE + STRIP RE-LAYOUT. prod-live `e9769aa9 -> b9e1c215` (FF).** The flat
"Copies these whales" roster panel on `/live/{account}/{category}` is now a **sortable TABLE** (Farm-League watchlist
CSS family, sticky header): Whale · Farm verdict · Tenure · Copies · Booked · W · L · Win% · Realized $ · Cost $ ·
ROI% · Unbooked · Open · Today $ · Actions. The money strip moved directly under the header chips and gained a **5th
SIZING cell** (the standalone sizing line is gone); the roster table sits below the strip. 8 pm_web files (incl.
engine-shared **subdivision.py, ADDITIVE-ONLY +23/-0**); ONE pm_web restart (467489 -> 494253); engine `trading-corp`
491380 NEVER touched. box == prod-live 47/47. Full record: `PM_ROSTER_TABLE_2026-09-20.md` §11.
  - **ARCHITECTURE.** Reader `subdivision.booked_cost_by_whale(conn, account, category)` (NEW, additive) = the
    ROI(COST) denominator per whale, `SUM(fill_count*fill_price - realized_pnl)` over `close_source='settlement'`
    rows — invertible from the settlement row itself, self-consistent with the realized the roster shows
    (validated on the box by a cost-basis gate: it equals the entry-side Σ(fill*price+fee) of settled copies to the
    cent). Assembler `live_view.build_roster_table(whale_records, booked_cost, *, now_ts, sort, direction, show_all,
    thin_floor)` (PURE, pm-side): enriches each `whale_live_records` row with `win_pct` / `roi_cost` / `tenure_days` /
    `booked_cost_usd` (honest-None on zero-booked / zero-cost), keeps on-roster (default) or appends formerly-live
    BENEATH (`?whales=all`), sorts server-side (default Realized desc; every numeric column + whale; None sorts last),
    returns a footer-totals row. **URL-DRIVEN toggle + sort are server-rendered (JS-off safe)** — NOT the client-only
    `pm_sort.js` (which is ascending-first; a known trap). `pm_whale_roster.html` = the table + a drill-through JS
    (binds on **DOMContentLoaded** — the drawer is later in the DOM); `pm_sizing_control.html` repurposed into the 5th
    strip `.cell`; `pm_live_subdivision.html` re-laid-out. `app.py` loader + route read `?whales/?sort/?dir`.
  - **SCHEMA.** Head is **24** (NOT 23): migration 024 `pm_subdivision_attachment_event` (attachment-span-history)
    already landed via the engine agent; **the next free migration is 025.** Deploy 12 ships NO db.py / NO migration.
    The roster Tenure cell is structured (`.rt-spans` list) so those prior spans render as a list once a reader
    consumes `pm_subdivision_attachment_event` (today `whale_live_records` still returns the single current span).
  - **TEST BASELINE is now 24** (was documented as 22): the local `.venv-webtest` differential adds
    `test_refresh_stagger_and_lookback` + `test_rung3_observability` (engine env-gap, need `pykalshi`), on top of the
    prior 22 (test_live_r3 ×14, test_accounts_m2 ×3, test_stage2_nav ×2, test_stage2_phase3 ×2,
    test_ctx_pagination_fix ×1). New tests: `test_roster_table.py` (17) + `test_whale_roster.py` render test updated.
  - **KNOWN pre-existing phone-overflow** (surfaced at Deploy 12, NOT introduced): the roster table collapses to
    stacked cards with no overflow, but the SHELL HEADER nav + poll/arm chips + the MLB game-card scoreboard overflow
    a phone viewport (~895px). A shell/game-card follow-up, not a roster item.
  - **NOT EXERCISED ON PROD:** the first Detach / sizing change from the new table (GET confirms only in the deploy).

**★★ DEPLOY 9 (2026-09-12) — PER-WHALE LIVE RECORD + DETACH. prod-live `dfbb140a -> afbcfbea` (FF), tag
`pm-roster-deploy9-2026-09-12`.** The "Copies these whales" line on `/live/{account}/{category}` is now the whale
ROSTER: per-whale real-money live-copy record (journal, filtered — Ruling 1), grouped ON-ROSTER vs FORMERLY-LIVE
with attachment dates, dollars-first figures (placed · booked · W–L · unbooked · realized today/all-time · open
with N-of-M priced · THIN<50), a Detach control, and a click-to-filter drill-through into the trade drawer. Full
narrative + shas: `PM_LIVE_ROSTER_2026-09-12.md` §9. **9 files** (pm_web + one engine-SHARED but ADDITIVE-ONLY file,
`subdivision.py` — 112 insertions/0 deletions, 3 new functions; engine behavior-neutral, picks it up on its next
restart). ONE pm_web restart (360799->363574); engine `trading-corp` 351422 UNTOUCHED. Box == prod-live 42/42 web +
subdivision.py.
  - **ARCHITECTURE.** Reader `subdivision.whale_live_records(conn, account, category, *, marks, now_ts,
    today_start_ts, thin_floor)` (pure; reuses `live_copies_by_whale` + adds attachment dates/active, per-whale
    current value at held-leg bid with honest N-of-M, realized_today, unbooked=n_closed-n_settled; groups
    on-roster/formerly-live). New partial `partials/pm_whale_roster.html` (+ inline R4 drill-through JS) +
    `partials/pm_detach_confirm.html`; `pm_live_subdivision.html` = ONE include line; `pm_trade_drawer.html` rows
    carry `data-wallet`. Detach = `GET .../detach/{wallet}` (server-rendered confirm, JS-off safe) + `POST` (calls
    the SAME `farm_actions.detach_from_live` the CLI uses; the driver's per-cycle `WHERE active=1` roster query drops
    the whale within one ~7s cycle, no restart; open positions ride to settlement).
  - **`authz.can_act_on_account` (+ `_request`) is the OWNER-OR-ADMIN authorization primitive** (the first use of
    `owner_identity` to gate a WRITE, not just visibility): admin any account; owner own account; owner other -> 403;
    no identity / null owner -> 403. Detach uses it (Karen may detach her own; Jack all). **Promote/Attach remain
    ADMIN-ONLY** (`_forbid_if_not_admin`) — unchanged.
  - **BACKLOG — migration 022 (attachment span history):** the schema keeps ONE attachment row per (sub, whale), so
    a re-attached whale shows ONE span (original `added_ts`, `removed_ts` cleared on re-attach); explicit PRIOR-span
    date boundaries are unknowable. The RECORD (figures) is complete across spans via the journal, so 022 (an
    attach/detach event log) is deferred, not required — build only if Jack wants explicit prior-span dates.
  - **★ pm-live-fixes-2026-09-12 MUST be rebased onto the new prod-live tip (`afbcfbea`) before its Items 1–2
    resume.** It edits the positions table + event block on the SAME page/files (live_view.py, pm_position_rows.html,
    pm_subs_event.html, pm_live_subdivision.html, pm_desk.css, pm_shell.html). Deploy 9's overlap is minimal by
    design (pm_live_subdivision.html = one include line; the reader is in subdivision.py NOT live_view.py) but
    pm_desk.css (additive) + pm_shell.html (?v=) + the one include line will need a trivial rebase.
  - **★ FIRST REAL DETACH NOT YET EXERCISED on prod** (no POST shipped): Jack does the first one; expect that whale's
    next signal to show "placed 0" on the driver heartbeat within one cycle.

**STATUS (prior): last updated 2026-09-11 (DEPLOY 8: Farm live-whale badge + promote/demote 409 guards).**

**★★ DEPLOY MODEL — NEW as of DEPLOY 8 (2026-09-11). READ THIS; IT SUPERSEDES EVERYTHING BELOW ABOUT CAPTURE-AND-
GRAFT.** `origin/prod-live` on GitHub is TRUTH, and the box == prod-live byte-for-byte (CR-stripped) for the whole
pm_web package — proven 60/60 at Deploy 8 (pre and post). The old "the box is ahead of every branch, repo
reconciliation is deferred, capture the box copy and graft onto BOX-CURRENT" model is **RETIRED** — do NOT capture,
do NOT graft. The procedure now is:
  1. Branch off `origin/prod-live` tip.
  2. Make your change a real COMMIT on that branch (edit files directly; the branch base IS truth, so wholesale
     edits are correct — no box captures).
  3. Verify from truth: full `tests/prediction_markets/` suite on your worktree vs prod-live tip (a DIFFERENTIAL —
     regressions, i.e. pass-on-tip/fail-with-change, must be EMPTY); render the affected pages against a read-only
     snapshot of the box DB.
  4. Deploy that commit's files (pm_web-only), ONE pm_web restart via `az vm run-command`, backup-is-a-gate,
     engine NEVER touched (verify trading-corp PID + NRestarts unchanged before/after every step).
  5. Fast-forward `origin/prod-live` to the deployed commit (FF-only; non-FF → STOP) + tag; re-verify box ==
     prod-live 60/60. Truth stays true.
Worked example with every gate + output: `PM_FARM_DEPLOY8_2026-09-11.md`. ★ The `pm-tiles-redesign-2026-09-10` /
`pm-ui-rewrite` / `pm-tiles` branches are RETIRED — all future UI work branches off `origin/prod-live`.
TRUTH TEST BASELINE (next agent): the full 81-file suite against current prod-live = **40 failing**, all pre-existing
cross-tree / env-gap (pm_arm_view engine-web import; schema-head-15-vs-live-21; ingest/stats/ranking/cli/fixtures) —
NONE touch the farm UI; a differential is the real gate, not the absolute count.

Prior deploys (history preserved below for reference; their capture-and-graft mechanics are superseded by the model
above): DEPLOY 7.2 LIVE event-block fix, 7.1 sound-toggle, 7 the Live Sub-divisions REDESIGN, 6 the tile page,
5 settled-slot whale + bet-slot. The filename keeps its original date so existing references resolve.

Workstream: the Prediction Markets `pm_web` UI rewrite. This doc is the starting point for the next UI pass.
Branch `pm-ui-rewrite-2026-09-02` (pushed to origin), worktree `C:\Users\AA Incorporado\cc-pm-ui-rewrite-wt`.
Deployed code (live-copy UI) = **`8978a2c`** (DEPLOY 5, tag `pm-ui-deploy5-2026-09-04`). Full narrative:
`PM_UI_REWRITE_REPORT_2026-09-02.md` (PLAN / FIX PASS / DEPLOY 1-3 / CARD POLISH / MULTI-CATEGORY FIX / DEPLOY 4 /
BET-SLOT PASS / DEPLOY 5).
★ **DEPLOY 6 (2026-09-07): the Live Sub-divisions TILE page** (GET /live rebuilt) — branch `pm-tiles-2026-09-07`
(tag `pm-tiles-deploy6-2026-09-07`), reports `PM_TILES_PHASE2_BUILD_2026-09-07.md` (build+deploy) +
`PM_TILES_PHASE1_INVENTORY_2026-09-07.md` (inventory + box-drift map).

--------------------------------------------------------------------------------
## 1. WHAT IS LIVE ON PROD

Deployed code = **`8978a2c`** (DEPLOY 5, 2026-09-04). pm_web is a STANDALONE FastAPI+Jinja app: it imports only
`trading_corp.data.*` + the PM package + stdlib; NO engine/main/agents/brokers; holds NO Kalshi credentials; can
never place an order. SINGLE uvicorn worker (loopback :8081, behind Authelia which sets Remote-User). Restarts go
through `az vm run-command` (root); the engine `trading-corp` (ARMED, 8 sub-divisions across two accounts) is NEVER
touched.

Deploy history (all pm_web-only, engine never restarted):
  DEPLOY 1 `9c2eeb3` -> 2 `cafb132` -> 3 `431ec76` -> 4 `86744ac` (multi-category fix) -> 5 `8978a2c` (bet-slot +
  settled-slot whale) -> **6 (2026-09-07) the Live Sub-divisions TILE page** (branch pm-tiles-2026-09-07 @ df38514)
  -> **7 (2026-09-10) the Live Sub-divisions REDESIGN** (branch pm-tiles-redesign-2026-09-10 @ 5c047f4, tag
  pm-tiles-deploy7-2026-09-10; 8 pm_web files + 21 logos + app.py graft, ONE pm_web restart 235587->298063, engine
  292771 untouched; report PM_TILES_REDESIGN_BUILD_2026-09-10.md)
  -> **7.1 (2026-09-10) sound-toggle persistence** (pm_live_subs.js fe29f6e5, restart 298063->302553, tag
  pm-tiles-deploy7.1-2026-09-10)
  -> **7.2 (2026-09-11) LIVE event-block group-by-game + held-side** (4 files @ 97f9304, NO app.py change, restart
  302553->309331, engine 302180 untouched, tag pm-tiles-deploy7.2-2026-09-11).
  -> **8 (2026-09-11) FARM live-whale badge + promote/demote 409 guards** — FIRST deploy under the prod-live model
  (branch pm-farm-livewhale-2026-09-11 @ **b1c552b1** off prod-live 091b0e0; tag pm-farm-deploy8-2026-09-11;
  origin/prod-live FF-advanced 091b0e0->b1c552b1). 4 pm_web files (app.py 23ec41a->de5f39ed, pm.css 4a4b19df->
  d71151bd, pm_shell 6141979a->d7fae4fc [pm.css?v->d71151bd, retiring the stale 204d9051], pm_watchlist_rows
  ada2ffb3->3c110129). ONE pm_web restart 320362->332976; engine trading-corp 313359/0 UNTOUCHED. Backup
  /home/azureuser/pm_deploy8_backup_20260911T032739Z. Post-check: all 23 /farm/{cat} 200, badges tie 6/2/6
  (mlb/cs2/nfl), 0 raw tickers, 0 double-escaped, static all 200, journalctl 0 err. Report
  PM_FARM_DEPLOY8_2026-09-11.md.
★ NOTE: between DEPLOY 5 and 6 the box ALSO took non-UI-rewrite-branch deploys (driver-liveness incl. the heartbeat
tables + the account-page liveness panel; farm-search; remaining-categories), so the box pm_web is AHEAD of the
pm-ui-rewrite branch. Always graft onto BOX-CURRENT; do not wholesale-copy from a branch.

Post-DEPLOY-5 box shipped-file set (the 4 files that changed 86744ac->8978a2c; box == these, CR-stripped sha16 @
`8978a2c`):
    8fb7db158e4a5af8  web/live_view.py
    18454d5690a316ed  web/static/pm_desk.css
    db9cb08c3b831b35  web/templates/pm_live_subdivision.html
    934c258ce953b18d  web/templates/pm_shell.html
DEPLOY 4's other files remain at their `86744ac` shas (subdivision.py 863af1d1, marks.py 8cace4e7, poller.py
d9f9f4f5, ...).
Older UI-rewrite files still live at earlier shas (feed_mlb.py 467d5284, arm.py 60f44720, ui_cache.py e116ee8a,
pm.css 204d9051, pm_live.js b4c557fc, htmx.min.js 491955cd, pm_trade_drawer.html 48d579db, etc.). Reconcile every
deploy the same way: `git show <sha>:file | tr -d '\r' | sha256sum` vs `tr -d '\r' < boxfile | sha256sum`.

**Two standing deploy rules (unchanged):**
  * **app.py: edit on the prod-live branch, NO graft (model changed at DEPLOY 8).** Since `origin/prod-live` ==
    box, branch off prod-live and edit app.py directly — the branch base IS the deployed app.py, so there is no
    "graft onto box-current" step and no branch-carries-M5 hazard (prod-live has no /pm/arm route; M5 was never
    built). ★ Current prod-live app.py CR-stripped sha16 = **`de5f39ed21f301ef`** (DEPLOY 8; = the DEPLOY-7 base
    `23ec41a` from leg-audit + the farm live-whale hunks). Gate a deploy on **/pm/arm route decorators = 0** + the
    prod-live base sha (NOT a hardcoded is_admin count). If you ever find box != prod-live for any pm_web file, that
    is a RECONCILE FINDING — STOP and report; do not deploy over it.
  * **main.py NEVER ships from this branch** (it carries the engine's per-account driver wiring). UI deploys are
    pm_web-only. `git diff --name-only <deployed> <target> -- .../main.py` must be empty before any deploy.

--------------------------------------------------------------------------------
## 2. ARCHITECTURE (what was built)

* **Feed adapter** `web/feed_mlb.py` — StatsAPI primary, ESPN fallback (ESPN 403s a browser UA; use `curl/8.4.0`).
  Join key = (ET calendar date, doubleheader#, frozenset of canonical team NAMES) — never a raw abbr. Kalshi
  tickers encode ET; feed reports UTC -> converted to ET before keying. Fetch/parse failure -> ABSENT (card
  degrades to "feed unavailable"), never a wrong game. `match_in_slate` is tolerant (exact key, else lone game,
  else DH by game_no).

* **Mark poller** `web/poller.py` + `web/marks.py` + `web/ui_cache.py` — pm_web OWNS the marks (Kalshi's
  market-data endpoint is PUBLIC/unauth; read with stdlib, no creds/broker). One background task writes an
  in-process cache (`ui_cache`, single worker, no DB/schema) every ~60s; renders read the cache, never block on the
  network. Value = contracts x held-leg **BID**. **★ MULTI-CATEGORY (DEPLOY 4):** the poller no longer hardcodes
  the three MLB series — `poll_loop(..., series_provider=)` calls `app._held_series_provider` ->
  `subdivision.traded_series(conn)`, which derives the distinct Kalshi series from the tickers we CURRENTLY HOLD
  across all sub-divisions (both accounts). Fail-safe: any DB blip / empty result -> the MLB default (so a cold
  start still primes the slate). `marks.Mark` carries a `title` (the Kalshi market title) for the non-MLB rows.

* **live_view assembly** `web/live_view.py` — pure `build_live_context(orders, open_positions,
  open_positions_by_whale, slate, marks_result, now_ts, category=None)` (no DB/network; unit-testable). **★
  MULTI-CATEGORY (DEPLOY 4) — totals NEVER depend on the sport parser:**
    - **MLB** (`category=='mlb'` or detected from tickers) renders GAME CARDS with the EXACT original card-based
      summary (byte-identical; `realized_today`/`settled_today` keyed on the card's game date). Locked by
      `test_mlb_summary_strip_values_locked` + `test_mlb_context_byte_identical_with_and_without_category`.
    - **Non-MLB** (atp/ufc/wta/…) renders a POSITIONS TABLE (`_positions_view`) with a JOURNAL summary
      (`_journal_summary`): at-cost / count / value+coverage / realized-today all from `live_positions` /
      `value_positions`, never `game_key_from_ticker`. The "games held" cell reads "No game feed for <CAT> · N open
      positions". No diamond / inning / count / "game over" / baseball legend on a feed-less category.
  This fixed the DEPLOY-3 defect where a non-MLB sub-division showed 0 games / $0.00 while its drawer held a real
  trade (root cause: `game_key_from_ticker` returns None for non-MLB, so the card-derived strip went 0).

* **Bet slots + whale attribution** `_build_slot` / `_whale_tag` / `_entry_whales` — each MLB card has three fixed
  slots (ML/TOT/SPR). **★ BET-SLOT PASS + SETTLED-SLOT WHALE (DEPLOY 5, LIVE):** slots widened to 208px to fill the
  home-plate area (below the diamond), and each HELD slot -- **live OR settled** -- shows the whale it was copied from
  (first label + `+N` extras, right-truncated by CSS so the slot never changes shape). Source is journal-only:
  net-open holders (`open_positions_by_whale`) for a live slot, the ENTRY-fill copiers (`_entry_whales`) for a settled
  slot (whose net-open set is empty once closed). Every slot reserves the whale row so held/unheld slots keep
  identical height (card height constant). ONLY UNHELD slots show no whale. Same tag in the non-MLB positions table
  (active + settled rows). The full untruncated list stays in the trade drawer (and the tag's hover title).

* **Account pages** — `subdivision.account_pnl` iterates every active category; `_account_open_value` sums
  `live_positions` across all sub-divisions; rows list all four categories. Category-agnostic already; DEPLOY 4
  gave them non-MLB marks via the held-series poller.

* **Arm badge** — read-only from PERSISTED `agent_state` (legacy DB `data/trading_corp.db`, agent `pm_live`, keys
  `arm:global` / `arm:kalshi_<acct>:<cat>`). pm_web only DISPLAYS; the CLI is authoritative. Arming is restart-free
  (read each cycle) — a pm_web restart never changes arm state.

* **Shell + cache-busting** `web/templates/pm_shell.html` — loads `pm.css` then `pm_desk.css`. Static URLs carry
  `?v=<CR-stripped-content-sha8>`; `test_asset_cache_bust_hashes_match_files` fails CI if a CSS/JS change forgets to
  bump the shell hash. Current pm_desk.css shell tag = `?v=a40ab798` (branch; box serves `825861cd` until this pass
  deploys).

--------------------------------------------------------------------------------
## 3. HONESTY RULES (as implemented — preserve these)

No-mark -> `no mark` / `unavailable`, never $0 / never cost-as-value. Coverage label `N of M priced` UNCONDITIONAL
under every current-value figure (every category). Feed age bands (a final never goes stale). Arm states are
FOUR: ARMED / DISARMED / STATE UNAVAILABLE (indeterminate mode=ro read, never shown as disarm) / NEVER ARMED.
Opposed close = "— not booked" (engine books no realized there). Game states: pre-game "not started" (blank, not
0-0); postponed/suspended/delayed are their own amber states; final "game over"; inning break -> empty count pips +
cleared runners. Directional slots: TOTAL `+8.5/-8.5`, SPREAD `-1.5 TEAM/+1.5 OTHER`; settled slots keep the held
direction, never guessed. Retention 24h after game end OR last settlement. **Whale attribution now on the card
slots too** (Jack reversed the drawer-only ruling 2026-09-04) — journal-sourced, never inferred; full list in the
drawer.

--------------------------------------------------------------------------------
## 4. OPERATIONAL LESSONS

* **CR-strip BOTH sides before hashing** any box-vs-git comparison (box is LF; Windows checkout is CRLF). Unstripped
  hashes never match and every file looks drifted.
* **pm_web restarts via `az vm run-command`** (root): `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm
  --command-id RunShellScript --scripts 'systemctl restart prediction-markets-web'`. ssh+sudo has no TTY and
  fails-SAFE. NEVER restart/reload `trading-corp` (the engine).
* **Browser-cached CSS masquerades as a rendering bug** — always bump the `?v=` shell hash on a CSS/JS change and
  verify the served file's sha over HTTP + the page link's new `?v=`.
* **The box is NOT a git repo** — deploys are base64-embedded graft scripts streamed over ssh (`cc/gen_deploy*.py`,
  `cc/pm_deploy*_apply.sh`); write files as azureuser, restart as root via az. DEPLOY 4 pattern:
  `cc/pm_deploy4_precheck_ro.sh`, `cc/gen_deploy4.py` -> `cc/pm_deploy4_apply.sh` (temp -> CR-strip -> sha16 gate ->
  mv), `cc/deploy4_box_app.py` (the graft), `cc/pm_deploy4_restart_az.ps1`, `cc/pm_deploy4_postcheck_ro.sh`.
* **Test baseline: 16 pre-existing env-gap failures** in `tests/prediction_markets/` (offline, `.venv-webtest`,
  `-p no:pytest_ethereum`). All engine-dep, none from UI work; a green UI change leaves them at exactly 16:
  `test_kill_switch_r7d` x4, `test_live_driver_r7c` x7, `test_shard_gate_r2` x4 (`ModuleNotFoundError: pykalshi`) +
  `test_search_r1::test_schema_head_is_15` (stale assertion; schema head is 19). **Measure the baseline yourself by
  stashing your changes and running the committed base** — this number has drifted across sessions (earlier docs
  said 18/19; the authoritative current value on this venv is 16). The pm_web venv is
  `C:\Users\AA Incorporado\CC\.venv-webtest` (fastapi, jinja2, tzdata, playwright); use it for renders.
* **/farm/cs 404 is PRE-EXISTING**, not this branch — 'cs' appears as a non-tile tag on `/farm` but has no
  live-copyable category page. No farm code has shipped from this branch. Do not "fix" it as a regression.

--------------------------------------------------------------------------------
## 5. DELIBERATELY NOT BUILT / DESIGN DECISIONS OPEN

* **"Settled during a live game" note** — needs a settlement-ts <-> game-state-history join that does not exist.
* **Opposed-close realized P&L** — engine work, not UI; the UI shows "not booked".
* **Sport-specific non-MLB cards (court / octagon)** — DEFERRED as a DESIGN DECISION. Non-MLB currently renders an
  honest positions TABLE; whether ATP/UFC/WTA get bespoke scoreboards (and where the data would come from) is Jack's
  call, not assumed. Do not build sport cards without a ruling.
* **Farm League redesign** — the `/farm` + `/farm/{category}` pages are a DESIGN DECISION pending, not part of the
  live-copy UI rewrite. Leave them unless Jack scopes a redesign.

--------------------------------------------------------------------------------
## 6. BACKLOG — gated behind engine work

**Roster / DEMOTE panel** (the next UI pass): replace the flat "Copies these whales" line with an expandable
per-whale panel — per-whale LIVE real-money copy record (dollars first, thin-sample caveats), ON-ROSTER vs
FORMERLY-LIVE with dates, placed/booked/unbooked counts, drill-through to the drawer rows, and a per-sub-division
**DEMOTE** button (confirmation states OPEN copies RUN TO SETTLEMENT — demote stops new copies, does not flatten).
REJECTED by Jack: a paper-vs-live side-by-side per whale.

**SEQUENCING RULE (Jack, hard gate) — ROSTER-SOURCE QUESTION, being reconciled:** the DEMOTE button was gated on the
engine reading the live roster WITHOUT a restart ("today the engine loads the roster at BOOT"). ★ Phase-1 (item 6)
found the code says otherwise: the roster IS the DATABASE, not a yaml — `driver_roster.py:14` ("THE ROSTER IS THE
DATABASE, NOT CONFIG") and `live_driver.py:782` re-read the whale list from `pm_subdivision_attachment WHERE active=1`
EVERY ~7s cycle; only the SPAWN of a NEW (account,category) task is boot-time. So a whale added/removed on an
already-running sub is picked up live, no restart. IF Jack confirms DB-per-cycle with the engine chat, the DEMOTE
hot-reload gate is effectively already met and the item is UNGATED (a detach flips `active=0` and the next cycle
stops copying — `farm_actions.detach_from_live`, already used for the soccer mis-attach). Reconcile before building.

--------------------------------------------------------------------------------
## 7. NOT YET OBSERVED IN PROD

* **Non-MLB mark path with a LIVE non-MLB position.** DEPLOY 4's non-MLB positions view is proven on prod for the
  header/view change (all 6 non-MLB pages) and for a SETTLED position (the Halys loss on `/live/kalshi_jack/atp?tab=
  complete`), but at deploy time no non-MLB sub-division held an OPEN position, so the live current-value/coverage
  path for a non-MLB open position (contracts x bid + "N of M priced" from a KXATPMATCH/KXUFCFIGHT/KXWTAMATCH mark)
  has not been eyeballed on prod. Confirm when a whale next opens a non-MLB position: `traded_series` should include
  that series and the row should price (or read "no mark", never $0). The full value path IS proven off-prod
  (render harness `cc/pm_multicat_render.py` + `cc/pm_betslot_render.py`).
* **Inning-break hollow pips** (DEPLOY 3): proven by unit test + local render only; no game was at an inning break
  during a verification window. Confirm on the next live-baseball check (a MID/END card shows empty pips + cleared
  runners).
* **Multi-whale `+N` and wallet-only truncation on a REAL prod slot.** The settled-slot whale rule IS observed on
  prod (DEPLOY 5 post-check: jack/mlb Complete 7/7 + karen/mlb 5/5 settled slots + the settled ATP row all show a
  whale), but every prod position is currently copied from a SINGLE, short-named whale -- so the `+N` badge and the
  right-truncation ellipsis have not been eyeballed on a live slot. Proven off-prod (render harness
  `cc/pm_betslot_render.py`, `test_bet_slot_whales.py`). Confirm the first time a prod position is stacked by 2+
  whales, or copied from a wallet-only (no display name) whale.

--------------------------------------------------------------------------------
## 8. LIVE SUB-DIVISIONS TILES (DEPLOY 6, 2026-09-07) — cold-start for the next tiles pass

**Prod state.** The tile page (GET /live) is LIVE at branch `pm-tiles-2026-09-07` @ **`3db73c1`** (tag
`pm-tiles-deploy6-2026-09-07`). Box app.py reference = **`eeac337d17a84fc7`** (is_admin=14, /pm/arm=0). PM schema
head = **20**; migrations 018-020 are CLAIMED (018 opposed-marker, 019 multi_category_ok, 020 driver-liveness) — the
**next migration is 021** (db.py migrations are contiguous by a tested invariant; a colliding number silently skips
its DDL, so a deploy must drift-check the live head == 20 first). The tiles added NO migration (all reads are runtime
SQL over existing tables + the box's already-deployed heartbeat tables).

**★ RETIRED (was: "UI BRANCHES ARE A STALE SUBSET OF THE BOX / deferred reconciliation / box-capture procedure").**
This section described capturing box copies and grafting because the UI branches lagged the box. That is OBSOLETE:
as of DEPLOY 8, `origin/prod-live` == box (60/60), so branch off prod-live and edit directly — there is nothing to
capture and nothing to graft (see the DEPLOY MODEL banner at the top). Do NOT fetch box copies or make "box capture"
commits; do NOT graft app.py. The old drift map (`PM_TILES_PHASE1_INVENTORY_2026-09-07.md`) and
`cc/pm_tiles_boxfetch_ro.*` are historical only.

**★ DEPLOY DISCIPLINE — BACKUP IS A GATE (hard-won DEPLOY 6).** After backing up, VERIFY the backup dir exists and
every file's sha matches the box BEFORE any copy; a deploy runner must FAIL CLOSED if the backup is missing. DEPLOY
6's apply loop lacked a `mkdir -p "$BK"` before two root-level files, so `subdivision.py` + `arm.py` were overwritten
before the backup dir existed (remediated by backfilling the verified originals). It was recoverable only because the
pre-state == aad4dea; do not rely on that. Gate the backup like every other step.

**Tiles architecture (what is live).** Keyed off DATABASE STATE, not the engine's boot roster:
- Tiles = `subdivision.tiles_all` (ALL active subs, attached + unattached), segmented by account, sorted
  armed -> attached-disarmed -> unattached. Attached = >=1 active attachment (rich tile); unattached = compact
  "no whales attached".
- ARM = `arm.read_display_all` (batched 4-state ARMED/DISARMED/NEVER ARMED/UNAVAILABLE from `agent_state`) with its
  row-ts age. LIVENESS = `heartbeat.read_liveness` (RUNNING/IDLE/CATEGORY_STARVED/STALE/NEVER, banded by age) — shown
  SEPARATELY from arm (arm = should it trade; liveness = is the engine evaluating it).
- **ALARM STRIP (R1)** = page-top red banner listing subs that are effective-ARMED AND liveness STALE or NEVER (the
  28h divergence); CATEGORY_STARVED stays amber non-alarm; empty when nothing qualifies.
- REALIZED (R3) = `subdivision.subdivision_pnl_all` SUM(realized_pnl) over BOOKED terminal closes, labelled "net of
  fees" (entry fees in cost basis, settlement fee=0); count = "N booked closes . W-L from settlements"; UNBOOKED
  closes (opposed/exit, realized_pnl NULL) counted SEPARATELY; NEVER open value. LAST-24H = `realized_24h_all`
  (settlements with settled_ts in the last 24h). OPEN (R4) = three distinct figures (count / at-cost / current value
  + "N of M priced" via `live_view.value_positions`), no-mark honest. Assembler = pure `live_view.build_tiles_context`
  (unit-tested); the ONLY app.py change is the `_load_live_list` body. Thin caveat = booked closes <
  `search.DEFAULT_MIN_RESOLVED_FLOOR` (50).

**Not yet observed on prod.** A REAL armed+STALE/NEVER ALARM STRIP — harness/test-proven only (no armed sub has gone
STALE since deploy; the driver has been healthy). Predicate + render covered by
`test_tiles_view.py::test_alarm_strip_is_armed_and_stale_only` + the render harness (`cc/pm_tiles_render.py` seeds an
armed+STALE sub). Confirm the strip fires the first time an armed sub's heartbeat actually goes STALE/NEVER — that is
a real engine-outage finding for Jack + the engine chat, NOT a UI defect.

**Tiles backlog (Jack: "functional — a more elegant display later").**
- **Mid-size tile for attached-but-never-traded subs**: the ~20 attached-disarmed / never-armed subs with zero
  history render the full rich tile with an empty P&L block, competing visually with the 8 armed tiles. Give a
  zero-history sub (no booked close yet) a MID-SIZE tile — arm, liveness, whales; NO P&L block until a booked close
  exists — so the 8 armed tiles stand out among the 20 zero-history ones.
- **Roster / DEMOTE panel** — see §6; the boot-vs-per-cycle roster-source question is being reconciled (if
  DB-per-cycle is confirmed the DEMOTE gate is already met).
- **Sport-specific ATP/UFC/WTA cards**, **Farm League redesign** — see §5 (design decisions, unchanged).
- **/farm/cs 404** — pre-existing; owned by whoever created the `cs` category, not the tiles workstream.

--------------------------------------------------------------------------------
## 9. LIVE SUB-DIVISIONS REDESIGN (DEPLOY 7 / 7.1 / 7.2) — cold-start for the next pass

★ **DEPLOY 7.2 (2026-09-11) — LIVE event-block fix.** The LIVE tile's event block now shows ONE compact row PER
UNDERWAY game, each listing ONLY that game's held positions (it previously attached EVERY open position to a single
underway game — Jack's Jack/MLB screenshot showed a PHI position under the TB@ATL block). GROUPING RULE:
`live_view._live_event` buckets open positions by the card-page join (`game_key_from_ticker` for MLB, the match
stem for live-capable non-MLB), emits underway games most-recently-started-first capped at 3 (+N more live), and
OMITS a ticker that joins no game (never guesses it onto one). LEG-AWARE ML SHORTHAND: `_short_label`'s moneyline
branch now returns the team WE HOLD (YES club for a YES/absent leg, the OTHER club for a NO leg, via
`_held_team_code`) — this is the SAME shorthand the CARD PAGE renders, so a NO-leg ML on `/live/{acct}/{cat}` now
also shows the held team in its compact label (expected, matches its own describe_market desc). The held ML team is
marked in the score line (a `.mine` dot). Deployed 4 files (live_view a4f233fb, pm_subs_event 3dd74d8e, pm_desk
246a3fa9, pm_shell ?v=246a3fa9), NO app.py change, ONE pm_web restart 302553->309331, engine 302180 untouched.
Tag pm-tiles-deploy7.2-2026-09-11. Observed live on prod: HOU@PHI -> [ML PHI], TB@ATL -> [ML ATL], each held team
marked, PHI never under TB@ATL. Report: PM_TILES_REDESIGN_BUILD_2026-09-10.md (DEPLOY 7.2 section).
★ **DEPLOY 7.1 (2026-09-10) — sound-toggle persistence.** `pm_live_subs.js` persists the Sound on/off preference in
`localStorage` ('pmSubsSound') and restores it on load; it was resetting on every tab/tile navigation (the tabs are
real ?account= reloads). One JS file (fe29f6e5), one pm_web restart. Tag pm-tiles-deploy7.1-2026-09-10.


**Prod state.** GET /live is the Claude-Design redesign, LIVE at branch `pm-tiles-redesign-2026-09-10` @ `5c047f4`
(tag `pm-tiles-deploy7-2026-09-10`). Box app.py = `16caedfe6a193737` (is_admin=28, /pm/arm=0). Schema head 20 (no
migration). Full build+deploy narrative + field map + before/after shas: `PM_TILES_REDESIGN_BUILD_2026-09-10.md`.

**Architecture (what is live).** Server-rendered, pm_web-only, keyed off DB state; JS is enhancement-only.
- **Per-account TABS** via `?account=` in the URL (JS-off works). SCOPED (R6/M4): admin sees all tabs, a non-admin
  only accounts whose `owner_identity` == their identity; no identity -> nothing. The scoping (`authz.visible_account_ids`)
  is now applied to `_load_live_list` AND `live_subdivision_page` (403) AND `/live/events` — the whole /live surface,
  not just the account pages (this closed the add#5 unscoped-detail-route finding).
- **READERS** (`subdivision.py`): `realized_windows_all` (ET-calendar today/week/month + all-time; all-time == the
  account page, ties out), `last_events_all`, `events_since` (the pulse). **ASSEMBLER** (`live_view.build_subdivisions_context`,
  PURE): activity classifier LIVE/UPCOMING/SETTLED/INACTIVE/UNATTACHED via an explicit per-category feed hook
  `_event_underway` (MLB game feed + `parse_ticker_start` HHMM for cs2/nfl/nba/nhl/wnba/cfb; everything else
  unknown->UPCOMING); `name_market` R2 (feed -> Mark.title -> describe_market -> "<CAT> market", never a ticker);
  `SPORTS`/`LIVE_CAPABLE`/`RETIRED_CATEGORIES` consts. **EVENTS**: `GET /live/events?since=<id>` JSON + `pm_live_subs.js`
  (60s poll + placed/closed pulse + two close tones, sound off by default). **LOGOS** `static/logos/<CODE>.png` (21),
  runtime-versioned; monogram fallback (SOCCER).
- **The LIVE event block + the UPCOMING NEXT line are PARTIALS** (`partials/pm_subs_event.html`,
  `pm_subs_eventline.html`) — Jack may strip either with one `{% include %}` (his standing note: "may strip the LIVE
  event block / NEXT line later"). CSS is scoped under `.subs` in `pm_desk.css` (no collision; reuses the palette +
  shell header). Cache-bust: `pm_desk.css?v=80c88cc2`; logos + `pm_live_subs.js` runtime CR-stripped sha8.

**Backlog (redesign follow-ups).**
- **Per-sport feeds for tennis / UFC / soccer / Fed** — today only MLB has a game feed and only MLB + the
  ticker-HHMM sports can read LIVE; the rest stay UPCOMING even once underway. Adding a feed is ONE function
  (`_event_underway`'s else branch, which now also drives the per-game grouping) + a scoreboard partial.
  ★ CANDIDATE SOURCE (corrected 2026-09-11): Kalshi's **milestones / live_data endpoints** — these carry the event
  START TIME and live SCORES, which is what a real "underway + scoreboard" needs. Do NOT use the market object's
  `occurrence_datetime` for this — it is the RESOLUTION (expected-expiration) time, not the start (2026-09-09
  inventory item 12). The score line + true underway flag come from milestones/live_data, not the market's time
  fields.
- **Retire the SOCCER category engine-side** — the `kalshi_jack/soccer` orphan (att=0, no matcher) renders as the
  dashed orphan because pm_web can't do a data-driven "no matcher" check (the matcher registry is engine-side;
  importing it would break the standalone invariant). Retiring `soccer` engine-side (remove the sub-division / mark
  it retired) would drop the dead row cleanly.
- **LIVE event block / NEXT line** — Jack may strip either (each is one include). Non-MLB LIVE tiles show a market
  label + positions, no scoreboard (only MLB has a feed).

--------------------------------------------------------------------------------
## LIVE FIXES — DEPLOY 10 (2026-09-12): Items 3 + 1 + 2 LIVE on prod-live

**`origin/prod-live 8fdded34 -> c339437b`, tag `pm-livefix-deploy10-2026-09-12`. pm_web-only; the ARMED engine
(`trading-corp` PID 351422) was NOT restarted/touched. Full deploy record: PM_LIVE_FIXES_2026-09-12.md §7.** Cache
bust: `pm_desk.css?v=92a1ef2e` (this section supersedes the older `?v=80c88cc2` reference above).

**Item 3 — mark-cache render semantics (`ui_cache.py` + `live_view.py` + `pm_live_subdivision.html`).** The poller's
snapshot now: **titles PERSIST** (accumulated, never evicted — a market name survives a failed/partial poll);
**marks are MERGED, not replaced** (a series that fails THIS poll keeps its prior `Mark` WITH its own `as_of`, so the
value still shows, banded amber past the stale threshold, with an honest age); `ready` starts **False** and only
flips True after the first completed poll -> a cold cache renders **"marks loading"** (NOT "no mark"); **"no mark"**
shows ONLY for a ticker that has NEVER returned a bid; a failed poll adds **"refresh failed Nm ago - showing last
mark"** beside the coverage. Every non-MLB label is NEVER a raw ticker (the `describe_market` `<type>:<ticker>` floor
leak is gone; floor = `<CATEGORY> <MARKET-TYPE>`, and for a non-ML/TOT/SPR series the bare `<CATEGORY>`).

**Item 1 — fixed-height LIVE tile (`live_view._live_event` + `pm_subs_event.html`).** The LIVE tile is a **constant
480px** (measured at 1/2/4/6 underway games): ONE **FEATURED** game (closest to SETTLING, deterministic: latest
baseball inning -> most outs -> most positions held -> away code A->Z) with its scoreboard + up to 3 held positions
("+N more held"), then every OTHER underway game as a single compact **chip** (matchup + up to 2 shorthand/value
pairs), capped at 3 with **"+N more live"**. `_live_event` returns `{featured, others, more, n_live}`.

**Item 2 — the ONE shared market-label formatter, on THREE surfaces (`live_view.format_market_label`).** Every
non-MLB position/trade reads as **matchup + signed shorthand** (`ML MIZZ` / `SPR -6.5 MIZZ` / `TOT +51.5`), never a
raw ticker/slug, via ONE formatter that each surface feeds after decoding its own source: (1) the **/live positions
table** groups rows by game under an "AWAY @ HOME . date . start" header (`_group_by_game`), shorthand primary +
Kalshi title secondary, the bare SIDE column DROPPED; (2) the **trade drawer** Game column = matchup (feed OR ticker
decode), Type column = the tagged shorthand; (3) the **Farm whale paper-trade list** decodes the Polymarket slug via
the engine's canonical `parse_poly_bet` and keeps the slug beneath as provenance. Decode reuses the structural
matcher's DATA-side maps (`sports_team_mapping` + `cfb_teams` + `sports_structural_match.LEAGUES`/`parse_poly_bet`,
all standalone-safe), longest-prefix wins, fail-closed blob split. FAIL-CLOSED for tennis/ufc/fed/non-structural Poly
categories (no matchup -> honest single label). **Away/home convention CONFIRMED on live box data** (15/15 real
cfb/nfl/mlb tickers matched Kalshi's own event sub-title order; 5/5 with a Poly slug matched `{away}-{home}`).

**Backlog (Deploy-10 additions to the redesign follow-ups above).**
- **Per-sport LIVE feeds** — start times are now LANDED via the verified Kalshi **milestones** sweep (tennis/ufc/
  soccer read LIVE via clock-compare); SCORES remain out of scope. A real scoreboard for non-MLB still needs the
  live_data score line + a scoreboard partial (the milestones start-time infra is the proven half).
- **Non-structural label leak (found during Deploy 10, KX-neutral, pre-existing)** — the drawer **Type** column
  ("KXATPMATCH KHA") and drawer detail **Market** field (`describe_market` "kxatpmatch: KX...") for atp/ufc/cs2/fed/
  soccer still surface the raw series, because `format_market_label`'s tag + `describe_market` fall back to the raw
  `_kind` for non-structural categories (the SAME root as the `_base_label` KX-leak already fixed for the positions
  floor). Off-box fix: map non-structural kinds to a clean category label. NOT a Deploy-10 regression (total-KX
  identical before/after; positions labels are 0 KX).
- **Migration 022** — the attach/detach event-log (Deploy 9 kept ONE attachment row/whale, so prior live spans are
  unknowable; the journal makes the record complete, but a real event-log is filed).
- **Contract sizing from the UI**, **Farm Analyze button**, and the **22 stale UI tests** (`test_live_r3` x14 +
  `test_accounts_m2` x3 + `test_stage2_*` x4 + `test_ctx_pagination_fix` x1 — cross-tree/env pre-existing failures,
  the standing differential baseline, not introduced by any of these deploys).
- **Retire the SOCCER orphan** engine-side (as above).

--------------------------------------------------------------------------------
## DEPLOY 11 (2026-09-12): CONTRACT SIZING FROM THE UI + MIGRATION 022

**`origin/prod-live 78d90e54 -> ce52ef3c`, tag `pm-sizing-deploy11-2026-09-12`. pm_web + `sizing.py`(new pm-side) +
`db.py`(additive) + ONE pure-CREATE migration (022) + ONE pm_web restart. Engine `trading-corp` 351422 NOT touched.**
Full record: PM_SIZING_UI_2026-09-12.md §8. Cache-bust `pm_desk.css?v=422c45ec`.

- **What it does** — change a sub-division's contracts-per-copy from its `/live/{account}/{category}` header (e.g.
  5 -> 1), NO restart: the engine reads `pm_subdivision.contracts` PER CYCLE (`execution.py:544` via
  `live_driver.py:1080`, ~7s). A server-rendered confirm (GET, JS-off safe) then a POST writes the value + an audit
  row in one transaction. The header shows "N contracts / copy · set by `<who>` · `<age>`"; the drawer footer lists
  the last 5 changes.
- **`prediction_markets/sizing.py`** (NEW, pm-side, imported only by web/app.py — NOT engine-shared) — the reader,
  the writer `set_contracts`, and the **1-50 bounds** (`CONTRACTS_MIN/MAX`, a UI ruling defined once here, NOT engine
  config; the engine's per-order/daily USD caps are unrelated).
- **owner-lowers / admin-raises** is the **SECOND use of `authz.can_act_on_account`** (Detach/Deploy 9 was the first)
  — owner-or-admin may LOWER; only admin may RAISE; server-gated on both the GET confirm and the POST.
- **MIGRATION 022** = `pm_subdivision_sizing_audit` (pm_web-owned; the engine never reads/writes it). **THE NEXT
  MIGRATION IS 023** (this corrects the earlier note that reserved 022 for the attach/detach event-log — 022 is now
  the sizing audit; the attach/detach event-log, if built, takes 023). **Migration-apply mechanism (for any future
  pm_web-owned migration):** `db.init_db()` (db.py, version-gated) invoked by **`pm_cli`** (the box crons) OR a deploy
  runner calling the same `db.init_db` — NOT hand-run DDL. pm_web startup does NOT migrate (only starts the poller);
  the engine `main.py` connects the PM db but never runs `prediction_markets.db.init_db` (its init_db is the legacy
  `persistence.db`), so the engine accepts a head ahead of its code trivially (`init_db` only applies version>current).
  The DEPLOY GATE drift-checks box head == N-1 before applying and renumbers to box-head+1 on collision.
- **Backlog update** — Contract-sizing-from-the-UI is now DONE (was a follow-up). The attach/detach event-log
  (migration 023) remains a backlog item. The 22 stale UI tests remain the standing differential baseline.

--------------------------------------------------------------------------------
## DEPLOY 12-13 (2026-09-20): bridge (full records in their own reports)

- **DEPLOY 12 — ROSTER TABLE + STRIP RE-LAYOUT.** `/live/{account}/{category}` roster is now a sortable TABLE
  (watchlist CSS family) + a 5-cell money strip (adds a SIZING cell; the standalone sizing line is gone). Added
  `subdivision.booked_cost_by_whale` (ROI cost denom) + pure `live_view.build_roster_table`. `prod-live -> b9e1c215
  (code) -> 9ff6060c (docs)`, tag `pm-roster-table-deploy12-2026-09-20`. Full record: `PM_ROSTER_TABLE_2026-09-20.md`.
- **DEPLOY 13 — LIVE TILE SIMPLIFY.** `/live` tiles are all STANDARD size, money-first, ONE compact game line per
  game for <=2 underway games else a single "N games live . M positions" summary. `prod-live -> 0023e5a7 (code) ->
  d1b93ce7 (docs)`, tag `pm-live-tile-simplify-deploy13-2026-09-20`. Full record: `PM_LIVE_TILE_SIMPLIFY_2026-09-20.md`.
  **★ LESSON (carry forward):** inside a tile that is itself an `<a>`, a child link must be a `<div>` (or the tile's
  own href) — NEVER a nested `<a>` (that closes the tile early; caught only after restart -> rolled back -> fixed).
  The post-check now guards `a.liveln.more`.

--------------------------------------------------------------------------------
## DEPLOY 14 (2026-09-21): ROSTER TENURE — MULTI-SPAN FROM MIGRATION-024 EVENTS

**`origin/prod-live d1b93ce7 -> dc48d072` (code+report, FF) -> docs FF, tag `pm-roster-tenure-deploy14-2026-09-20`.**
pm_web-ONLY, **5 modified files** (app.py, live_view.py, pm_desk.css, partials/pm_whale_roster.html, pm_shell.html) —
NO new file, NO delete, **NO subdivision.py, NO migration, NO engine file**. `farm_actions.py` NOT shipped (box copy
`23f91d44` confirmed == prod-live; the new loader path reads it). ONE `prediction-markets-web` restart (497708 ->
499620); engine `trading-corp` **491380 / NRestarts 0 UNTOUCHED**. Post-check **130 OK / 0 FAIL**; box == prod-live
47/47. Cache-bust `pm_desk.css?v=0b50095b`. Full record: `PM_ROSTER_TENURE_2026-09-20.md` §8.

- **What it does** — the Deploy-12 roster **Tenure cell** now renders the FULL attach->detach->re-attach span history
  from the migration-024 event log (`pm_subdivision_attachment_event`), newest span first: the open span reads
  "attached `<date>` . N days"; earlier closed spans "`<start>` - `<end>`" render dimmed beneath; phone collapses the
  earlier spans to a "+N earlier spans" indicator. The ONE cell allowed to grow a line per span.
- **`live_view.build_spans(events, added_ts, removed_ts, active, now_ts)` (NEW, PURE)** — pairs each `attach` with the
  NEXT `detach` (sorted by ts); a trailing `attach` is the current OPEN span; returns spans NEWEST-FIRST. **Malformed
  input is handled WITHOUT inventing anything:** a second `attach` while already open is ignored + warned (never a
  fabricated detach); a `detach` with no open span is skipped + warned (never a fabricated attach). Dates come ONLY
  from events or the attachment row — never inferred from journal timestamps.
- **PERMANENT, INVISIBLE pre-024 FALLBACK (the rule to keep):** if the events yield NO span (empty log OR all
  malformed), `build_spans` returns the SINGLE span from the attachment row (`added_ts`, `removed_ts` if detached) —
  exactly what the pre-024 UI showed, indistinguishable on the page. `[]` only if there is no `added_ts`. This
  fallback is not a migration-window shim; it stays forever so a whale attached before 024 (no events) always renders
  its tenure. On live data today every key has <=1 event, so the fallback and the <=1-event path render identically.
- **TENURE SORT RULE** — `build_roster_table(..., events=)` sets `rec["tenure_sort"]`: **on-roster rows sort by the
  OPEN span's start; formerly-live rows sort by the LATEST span's end.** `_ROSTER_NUM_KEY["tenure"]` -> `tenure_sort`;
  URL `?sort=tenure&dir=` is server-rendered (JS-off safe), not pm_sort.js.
- **Loader** — `app.py` reads `farm_actions.read_attachment_events(conn, account, category)` ONCE (all whales) and
  passes `events=` to `build_roster_table`. app.py already imported farm_actions for the Detach route.
- **★ R3 STANDING NOTE** — multi-span is **fixture-proven only**; the live page shows ONE span per whale until a real
  detach-then-re-attach happens on the same sub-division. Not a defect.
- **Backlog update** — attach/detach event-log is LIVE (migration **024**, `pm_subdivision_attachment_event`; head is
  24, next free 025 — the older "023 reserved" note is superseded). The roster Tenure cell now consumes it.

--------------------------------------------------------------------------------
## DEPLOY 15 (2026-09-21): WATCHLIST SPLITS PAGE (per category)

**`origin/prod-live 5b8df49a -> f36f7ccc` (code+report, FF) -> docs FF, tag `pm-splits-deploy15-2026-09-21`.**
pm_web-ONLY, **4 files** (app.py, live_view.py, pm_farm_category.html modified + **pm_farm_splits.html new**). NO
subdivision.py, NO db.py, NO migration, NO cache-bust bump (splits CSS is inline-scoped in the template), NO engine
file. ONE `prediction-markets-web` restart (499620 -> 511353); engine `trading-corp` **503492 / NRestarts 0
UNTOUCHED**. Post-check **154 OK / 0 defect**. Full record: `PM_WATCHLIST_SPLITS_2026-09-21.md` §8.

- **What it is** — a **read-only** page `/farm/{category}/splits`, linked from every `/farm/{category}` page: the
  category's Watchlist whales' OPEN paper positions decoded into **game × market-type × side** and drawn as a
  **stake-vs-headcount split** (splits table [default], whale grid, heatmap treemap). Modes all / copied-only /
  compare, groups game / consensus / flat, sorts divergence / stake / most-split / most-agreed — **all in the URL,
  server-rendered, JS-off safe** (NOT pm_sort.js). Nothing places/sizes/cancels; the route never reaches the order path.
- **SPLITS ARCHITECTURE (the load-bearing decisions):**
  - **SOURCE = `pm_paper_trade` (status='open'), NOT `pm_open_position`.** `pm_paper_trade` is the pinned-whale paper
    poll (`paper.poll_pinned`, `pm_watchlist status='pinned' active=1`); `pm_open_position` is the daily whale-discovery
    pull and is often days-stale — do not use it for a live view.
  - **CADENCE 30 min:** the `paper-poll` crontab (`*/30`) writes `pm_paper_trade`; freshness = `last_observed_ts`
    (per-row, can lag); the page shows last-refresh **ET + real age**, STALE past 30 min.
  - **STAKE = whale_size_at_observation × entry_price_avg_at_observation** = the whale's dollars **at cost** (R4).
    NOT `cost_basis` (that is our fixed paper `size_basis`×px = notional). Box-proven ~70× apart.
  - **Grouping via the data-side `parse_poly_bet`** (`live_view._parse_poly_bet` / `_STRUCT_LEAGUES`) — **no broker
    import** (standalone invariant preserved). **Structural coverage = cfb / mlb / nba / nfl / nhl / wnba ONLY**
    (the `LEAGUES` keys). Other categories get an honest "no structural decode" state. A position that does not resolve
    to a two-side frame is **omitted + counted** ("N positions not grouped").
  - **Kalshi flag DROPPED (R8):** no pm_web index enumerates market existence (`milestones.py` = start-times keyed by
    event ticker; `marks.py` = held/open tickers only) — absence ≠ non-existence, so per R8 the flag is dropped rather
    than approximated. Start time: date from the slug + "start time unavailable" (a Poly slug is date-only; R6).
  - Trusted = live-attached active=1 on any account (R3, overrides tier); tiers `PROMOTE/WATCH/PASS/INSUFFICIENT_DATA`
    + "not analyzed" (no FADE/NEUTRAL). Reader `live_view.build_watchlist_splits` (pure) + `splits_frame` /
    `splits_ordered` / `splits_treemap`; loader `app._load_watchlist_splits`; route `GET /farm/{category}/splits`.
- **★ BACKLOG (next):** a **broker-free slug parse for soccer / tennis / UFC / CS2** so splits can cover those
  categories too. Today only the STRUCTURAL matcher (`data/sports_structural_match`) is importable by pm_web without
  the broker; the soccer (`soccer_poly_kalshi_match`) and tennis/UFC matchers live behind engine imports. The task:
  **extract each matcher's slug grammar (the Poly-slug → game/market/side decode) into a data-only module** pm_web can
  import, mirroring how `sports_structural_match` was carved out — then add those categories to the splits coverage.
  Until then, those categories honestly show "no structural decode".

--------------------------------------------------------------------------------
## STANDING NOTES FOR THE NEXT UI AGENT (as of 2026-09-21, through Deploy 15)

- **The pm_web RESTART is within atomic authority (standing since Deploy 14).** A board-authorized pm_web deploy brief
  that says "ONE prediction-markets-web restart" INCLUDES the restart — run `pm_deploy<N>_restart_az.ps1` yourself; do
  NOT hold it for a separate paste. Verify it by **MainPID + ActiveEnterTimestamp, never the az exit code** (az returns
  "Provisioning succeeded" / empty stdout whether it ran or not). Confirm the engine `trading-corp` PID + NRestarts are
  UNCHANGED immediately after. Scope = pm_web deploys ONLY (does not generalise az-root for engine restarts).
- **VOCABULARY RULE (this bit the Deploy-15 build).** `pinned` / `candidate` are CODE words and must NEVER appear in
  served HTML — the screen words are **Watchlist** (pinned) and **Prospects** (candidate). Tests enforce it
  (`test_stage2_nav::test_category_page_knows_its_category`, `test_stage2_phase3::test_vocab_still_clean`); a link that
  said "pinned whales" failed CI until reworded to "Watchlist whales". Grep any new served string for these words.
- **ITF TILE LOGO — PARKED (open, not blocking).** The `itf` category has no `static/logos/ITF.png`, so its tile
  renders no logo (the code degrades honestly: a logo shows only when the file exists, `live_view.py` / `app.py:1219`).
  Jack was to supply the source PNG (chose the compact ITF+ball mark); it had not arrived at session close. Wire-in =
  a ONE-FILE static add (`static/logos/ITF.png`) + a cache-bust (the tile img is `?v=<sha8>` from the file itself);
  the code auto-detects the new file. No template change needed.
- **WORKTREES LEFT IN PLACE (do not delete — each is at its deployed/pushed tip):**
  - `C:\Users\AA Incorporado\cc-roster-tenure-wt` — branch `pm-roster-tenure-2026-09-20` @ `4d9a2c8e` (Deploy 14).
  - `C:\Users\AA Incorporado\cc-watchlist-splits-wt` — branch `pm-watchlist-splits-2026-09-21` @ `8a23e1e8` (Deploy 15).
  - `C:\Users\AA Incorporado\CC\.claude\worktrees\jackslog-2026-09-17` — branch `pm-docs-jackslog-2026-09-12`
    (the running jacks-log; its own branch, deliberately NOT on prod-live).

## DEPLOY 17 (2026-09-25): TABLES — PHASE 1 (Farm sort + Analyze states) LIVE

First of three stacked "table passes" (full record: `PM_TABLES_BUILD_2026-09-25.md`; plan + rulings:
`PM_TABLES_PLAN_2026-09-25.md`). Deploy target `6eb797d0`; prod-live FF `558fc143 → 6eb797d0`; tag
`pm-tables-deploy17-p1-2026-09-25`. pm_web-only (5 files: app.py, live_view.py, pm_prospects_rows.html,
pm_watchlist_rows.html, pm_farm_category.html); engine `trading-corp` never restarted (534581/NRestarts 0 throughout);
pm_web MainPID 523799 → 569065; box == prod-live 48/48; backup `/home/azureuser/pm_d17_backup_20260925T201753Z`.

- **Both Farm tables now sort SERVER-SIDE via the URL** (Deploy-12 pattern), JS-off safe: the **Watchlist** table
  (`?wsort=<col>&wdir=<asc|desc>`) and the **Prospects** table (`?psort=<col>&pdir=…`). Namespaced so one table's sort
  never disturbs the other. The client `pm_sort.js` is **retired for these two tables** (the `<script>` include was
  removed from `pm_farm_category.html`); the file is left in place (still served 200, may load elsewhere). The JUDGE
  column's `-sort_value` negation hack is **gone** (server sort is desc-first → PROMOTE floats to the top). Defaults
  unchanged (Watchlist display-name asc; Prospects cost-ROI desc). **win% stays deliberately non-sortable on Prospects**
  (the completed-trade API under-reports losses — the honesty rule). Column keys live in `live_view.py`
  (`WATCHLIST_SORT_COLUMNS` / `PROSPECTS_SORT_COLUMNS`, `sort_watchlist` / `sort_prospects`); **note the net-pnl key is
  `netpnl` (no underscore)**.
- **Prospects Analyze button is state-aware:** unscored → **Analyze**; scored → **View result** ($0, cached) +
  **Re-analyze** (`?force=1`) + the analysis **age** ("scored Nd ago"). The JUDGE/win% cells keep their own
  un-analyzed [Analyze] readout (state stays clear). The Watchlist Analyze button is unchanged.
- **Phases 2 + 3 are BUILT + pushed, awaiting their deploys** (stacked on p1): `pm-tables-p2-2026-09-25` (splits
  Whale-Grid header — live highlight + accounts-on-hover + paper W-L) and `pm-tables-p3-2026-09-25` (non-MLB live
  Active/Complete flat sortable tables + trade-drawer series-tag floor). Deploys go 1 → 2 → 3, FF prod-live each time.

## DEPLOY 18 (2026-09-25): TABLES — PHASE 2 (splits Whale-Grid header) LIVE

Second table pass (full record: `PM_TABLES_BUILD_2026-09-25.md` §DEPLOY 18). Deploy target `a5db434b`; prod-live FF
`507053f6 → a5db434b`; tag `pm-tables-deploy18-p2-2026-09-25`. pm_web-only (3 files: app.py, live_view.py,
pm_farm_splits.html); engine `trading-corp` never restarted (534581/NRestarts 0 throughout); pm_web MainPID
569065 → 569992; box == prod-live 48/48; backup `/home/azureuser/pm_d18_backup_20260925T205926Z`.

- **The /farm/{category}/splits Whale-Grid header (`?view=grid`) now carries, per whale:** (a) a **LIVE highlight**
  (`th.live`, green top-accent) for a whale that is live-copied (attached active=1 on any account), with the copying
  **account(s) on hover** (the `<th title>`); (b) the whale's **paper W-L + win%** from the SAME reader the Farm
  Watchlist uses (`farm.farm_rows(PINNED)` → pm_paper_category_stats), **"—" at 0 closed, THIN under 50**. Grid CELLS
  are unchanged (header-only change). CSS lives in the template's inline `<style>` (no `pm_desk.css`, no cache-bust).
- Note: the highlight applies only to a live whale that is currently a **grid column** (has an open parsed position);
  a live whale with no open position isn't rendered in the grid, so it isn't highlighted (correct/honest).
- **Phase 3 remains BUILT + pushed, awaiting Deploy 19:** `pm-tables-p3-2026-09-25` @ `f84ec75c` (rebased; = a5db434b
  + phase-3) — non-MLB /live Active/Complete flat sortable tables + trade-drawer series-tag floor. FF prod-live next.
## DEPLOY 19 (2026-09-25): TABLES -- PHASE 3 (non-MLB live Active/Complete tables + drawer floor) LIVE

Third and final table pass -- **the three-phase table workstream is now CLOSED** (full record:
`PM_TABLES_BUILD_2026-09-25.md` §DEPLOY 19). Deploy target `60bbe750`; prod-live FF `8e450210 -> 60bbe750`; tag
`pm-tables-deploy19-p3-2026-09-25`. pm_web-only (3 files: app.py, live_view.py, pm_live_subdivision.html); engine
`trading-corp` never restarted (534581 / NRestarts 0 / boot 2026-09-22 21:11:47Z throughout); pm_web MainPID
569992 -> 571194; box == prod-live 0 mismatches; backup `/home/azureuser/pm_d19_backup_20260925T225031Z`.

- **Every NON-MLB `/live/{acct}/{cat}` detail page now renders Active/Complete as flat sortable tables** (the
  group-by-game layout is gone for non-MLB; game becomes a column with matchup + event date). Columns: Placed(ET),
  [Settled(ET) on Complete], Game, Bet, Contracts, [Fill/Cost/Value on Active | Realized on Complete], Status, Whale,
  Order. **Complete defaults to settled newest-first; Active defaults to event-date ascending** (OQ-4). Sort is
  server-side via the URL, **namespaced `?psort=<col>&pdir=<asc|desc>`** so it does not collide with the roster's
  `?sort/?dir`. A mandatory placement timestamp (`submitted_ts` of the earliest entry order) + the order id are on
  every row.
- **Trade-drawer series-tag FLOOR (3c):** for non-structural sports (tennis/UFC/fed) the drawer's Type + Market labels
  no longer leak the raw Kalshi series tag -- they fall back to the category floor ("ATP"/"TENNIS") via the same
  `_base_label` floor the positions table uses. The raw `KXATPMATCH...` string appears ONLY in the labelled **Ticker**
  provenance field (verified on atp: Type/Market KX hits 0; `<td>ATP</td>` x33; KX only in the 33 Ticker fields).
- **MLB is unchanged (OQ-3): MLB keeps its game cards, no flat positions table** -- Phase 3 is non-MLB only.
- **Realized tie-out honesty:** the Complete realized COLUMN sums to the journal's sum-of-per-row-rounded-to-2dp
  (NFL 9.87), while the journal's unrounded total is 9.9285 -- the visual delta is `_money` 2dp per-row display
  rounding, not a data error (each row ties to the cent). Same for atp (-6.72 col vs -6.7264 unrounded).
- Post-deploy sweep: **90 sub-divisions x 2 tabs = 180 pages all 200**; Phases 1 + 2 intact; vocab pinned/candidate 0;
  pm_desk.css sha unchanged (no CSS shipped); pm_web + engine journal errors 0; 0 template errors across the sweep.
- **Backlog (new):** (a) tennis/UFC/fed have no structural player-code map, so the drawer/floor shows the category
  floor instead of a human matchup -- add a data-only player-code -> full-name map (like `sports_structural_match`).
  (b) the roster sort (`?sort/?dir`) and the positions sort (`?psort/?pdir`) share the `/live` URL but do not compose --
  re-sorting one resets the other to its default (each works independently). Minor.

## GRID FOLLOW-UP (2026-09-25): Whale-Grid live-columns-first + brighter header/y-axis text

Post-Deploy-19 tweak (Jack, from the CFB/MLB grid). prod-live FF `30106ccf -> f454326b`; tag
`pm-grid-livecols-deploy-2026-09-25`. pm_web-only (2 files: live_view.py, templates/pm_farm_splits.html); engine
`trading-corp` never touched (534581 / NRestarts 0 / boot 21:11:47Z); pm_web MainPID 571194 -> 572117; backup
`/home/azureuser/pm_gridtb_backup_20260925T233946Z`.

- **Grid COLUMN default order = live-copied (trusted) whales FIRST**, then the rest, each first-seen. New pure
  `live_view.splits_ordered` -> `out["col_order"]` (computed for all three group modes: game/flat/shape); the template
  reads `O.col_order` instead of deriving column order from first-seen positions. Verified live-first on prod: CFB
  `[1,1,1,0...]`, MLB `[1,1,0...]`. **Columns are still positions-only** (a whale with no open decodable position is not
  a column -- Jack's explicit pick; the grid is a position-split matrix, empty columns were declined).
- **Text brightened** (the header was too faint): x-axis whale-name header -> `--wtext` white + weight 600 (bolder);
  tier/LIVE (`th2`), W-L (`th3`) sublines and the y-axis market label (`td.rowhd .m`) lifted `#42566d/#4a5d74 -> --wdim`;
  the y-axis matchup (`.g`) was already `--wtext` white. Live whales keep the green LIVE subline. CSS is inline in the
  template (no pm_desk.css, no cache-bust).
- 29 splits tests pass (incl. a new `test_col_order_live_whales_first`, all group modes); all grid views/modes 200.
