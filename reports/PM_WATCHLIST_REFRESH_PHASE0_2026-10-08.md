# WATCHLIST REFRESH BUTTON -- PHASE 0 (trace) + DESIGN HALT (2026-10-08)

Branch pm-watchlist-refresh-2026-10-08 off origin/prod-live **2532eee6** (verified; the brief's "403d774a" was
already stale -- prod-live moved again today with the MACE exit-work fold). Scope: pm_web only. READ-ONLY so far;
nothing built/applied/pushed.

## Q1 -- route / handler / auth / template (the existing Prospects Refresh)
- Route: `POST /farm/{category}/refresh/{wallet}` -> `refresh_action` (web/app.py:1138).
- Auth: ADMIN-ONLY (`_forbid_if_not_admin`, app.py:1148; M4 ruling 2026-09-01 -- a ~30-call shared-budget pull is
  a data-operator action). No CSRF token; defence is POST-only + same-origin admin session (Authelia) + R6 "no GET
  mutates".
- Template: button lives in `partials/pm_prospects_rows.html:119-123`; htmx `hx-post ... hx-target="#pm-prospects-rows"
  hx-swap="innerHTML" hx-disabled-elt="find button"`; JS-off falls back to the native form POST -> 303.

## Q2 -- synchronous, not enqueued
`refresh_action` -> `await _refresh_whale(wallet, now_ts)` (app.py:1104). SYNCHRONOUS inside the request: the pull
(`search_run.refresh_one` -> `ingest.refresh_wallet`) runs ON the loop (network yields), then `stats.rollup` runs
OFF the loop (asyncio.to_thread). ~30 Polymarket calls, up to ~1 min; htmx disables the button while it runs.

## Q3 -- DOES ANYTHING THE REFRESH WRITES FEED LIVE TRADING?  **NO.** (the explicit HALT trigger -- not tripped)
What refresh writes:
- `ingest.refresh_wallet`: `pm_closed_position` (ingest.py:193), `pm_whale` (204/211), `pm_open_position` (305 DEL /
  329 INS).  -- the FARM-ingest completed/open snapshot + the whale record (user_name, backfill_complete, last_refresh_ts).
- `stats.rollup`: `pm_category_stats` (stats.py:138), `pm_category_onesided_stats` (185), `pm_score_snapshot` (251).
  -- the COMPLETED-basis scoreboard (the Prospects basis).
- It does NOT write `pm_whale_score` (that is Analyze-only: analyze.py:719 is the sole writer; read only by app.py:429
  for display + a comment in scoring.py:32).
What the LIVE ENGINE reads to decide copies:
- The whales' live Polymarket `/positions` feed, polled directly every ~7s (live_driver.py:7, :602
  "signal source: attached whales' /positions -> entry CopySignals"). NOT a DB table.
- Boot roster `active_driver_subdivisions` (driver_roster.py:43): only `pm_subdivision` + `pm_account` +
  `pm_subdivision_attachment` (attachment-gated). Per-cycle live loop `FROM`: pm_subdivision, pm_subdivision_attachment,
  pm_subdivision_order -- no stats/ingest table.
- Grepped every engine file (live_driver, driver_roster, execution, heartbeat, rosters) for all five
  refresh-written tables + pm_whale_score: **ZERO references.** No gate in `execution.evaluate` reads any of them.
=> Refreshing a whale (prospect OR watchlist) cannot move live roster/copy/gate state. The engine copies from the
   live venue feed; refresh only rewrites display/screening tables. HALT trigger NOT tripped.

## Q4 -- idempotency / failure
- Failure errs SAFE: a raised pull -> whale UNCHANGED (outcome 'failed', prior complete data intact); a cap-truncated
  pull -> marked partial (backfill_complete=0) -> DROPPED from the ranker, never half-populated-and-ranked
  (app.py:1109-1134, `_REFRESH_NOTICE`). The row shows a visible notice.
- Writes are row-idempotent (INSERT OR REPLACE / DELETE+INSERT) -> a double-submit does not DOUBLE-WRITE. But refresh
  is NOT single-flighted server-side (unlike SEARCH, app.py:1167) -- a double-POST DOES double-CALL Polymarket
  (~30 -> ~60). The only double-fire guard is the UI `hx-disabled-elt`.

## Q5 -- section-level control
There is NO section-level refresh BUTTON. `refresh_band(refresh)` (pm_farm_category.html:36, macro in pm_macros.html)
is a READOUT only -- a data-freshness dot ("Xd old / last refresh <iso>", weekly green/amber/red). It sits in the
PROSPECTS region only; `refresh` = `stats.refresh_band_state(stats.max_refresh_ts(conn))` = the COMPLETED-basis max
`pm_whale.last_refresh_ts`. The Watchlist region has no freshness control at all.

## Q6 -- the Watchlist's own data path, and how it is refreshed TODAY  ★ THE FINDING
- Watchlist = `farm.farm_rows(status=PINNED)` -> `_ROWS_SQL_PINNED` (farm.py:123-148). EVERY performance column is
  PAPER basis from `pm_paper_category_stats` (n_closed/wins/losses/win_rate/net_paper_pnl/cost_basis/roi/n_stale/n_void)
  + an OPEN count from `pm_paper_trade` (status='open'). user_name/backfill from pm_whale; judge badge from
  pm_whale_score (Analyze). The per-row timestamp it carries is `pm_roster.last_polled_ts` -- NOT last_refresh_ts.
- Refreshed today by the PAPER CRONS (crontab on the box, verified 2026-10-08T23:20Z):
    `*/30 * * * *  paper-poll`     -> poll_pinned: re-pulls each pinned whale's /positions -> OPEN counts +
                                      pm_roster.last_polled_ts.  NEWEST last_polled_ts = **20.9 min** ago.
    `50 5 * * *    paper-rollup`   -> paper_rollup: recomputes pm_paper_category_stats (the perf columns). ONCE DAILY.
                                      every category's updated_ts = one 05:50Z stamp, **~17.5 h** old right now.
    `40 5 * * *    paper-adjudicate`-> resolves paper trades off GAMMA.
    `0 5 * * *     refresh`        -> the COMPLETED-history re-pull = what refresh_one does ad-hoc; feeds
                                      pm_whale.last_refresh_ts (the Prospects band), max ~0.76 d old.
  => The Watchlist is ALREADY auto-refreshed: OPEN every 30 min, PERFORMANCE once daily. `refresh_one` is a
     DIFFERENT basis and feeds NONE of it.

## THE DEFECT THAT OUTRANKS A COPY-PASTE
`refresh_one` writes the COMPLETED basis (pm_category_stats + pm_closed/open_position + pm_whale). The Watchlist
displays the PAPER basis (pm_paper_category_stats + pm_paper_trade). The ONLY overlap on a Watchlist row is
pm_whale.user_name/backfill (the whale's display NAME). So a Watchlist Refresh button that MIRRORS the Prospects
button would:
  - fire ~30 Polymarket calls (shared prod IP, mid-429-storm),
  - change NO performance column, NO open count, NO freshness the Watchlist shows (at most the whale's name),
  - and there is no last_refresh cell on the Watchlist to move; its real freshness is last_polled_ts, which
    refresh_one does not write.
That is precisely the brief's "a button that looks like it worked when it didn't is worse than no button."
Mirroring here is semantically WRONG, not merely cosmetic.

## THE FORK (Jack's call -- no safe default; I did not build)
- **A. Wire the button to the PAPER job for that one whale** (Q6's "manual trigger for an existing job"): poll_pinned
  + adjudicate + paper_rollup, scoped to the wallet. This is the ONLY option that makes the button refresh what the
  Watchlist shows (+ advances last_polled_ts for the freshness stamp). COST: 1 /positions fetch for that whale
  (lighter than refresh_one's ~30) + a rollup. REQUIRES new per-whale scoping: poll_pinned (paper.py:121) and
  paper_rollup (paper.py:436) are batch jobs with NO wallet filter today -- a small, migration-FREE code add
  (add a `wallet=` param + WHERE clause). Still re-pulls /positions from the prod IP, so admin-only + per-row only
  (no bulk) as the brief requires.
- **B. Mirror Prospects refresh_one exactly** -- REJECTED: misleading per above.
- **C. No refresh button; add only a per-row freshness READOUT** (last_polled_ts) to the Watchlist for transparency
  parity, since the data is already on a schedule. Smallest honest change; gives Jack the "age of what it shows"
  readout the platform standard calls for, without a control that re-pulls.

RECOMMENDATION: **A** if the Watchlist genuinely needs an on-demand refresh (it makes the button honest), else **C**
(the numbers already auto-refresh; a readout is the minimal honest surface). NOT B.

## Deploy posture (unchanged by this halt)
pm_web templates are Jinja; a template-only change needs NO restart (Jinja reloads per-render under the single uvicorn
worker only if auto-reload is on -- to be CONFIRMED on the box before any deploy). A route/handler change (Option A,
which touches app.py + paper.py) DOES need a pm_web restart to load new bytecode (single-worker, no autoreload --
the leg-audit precedent). Four-level "deployed" (code / on box / in process / reachable in browser) to be proven at
deploy, per the brief.
