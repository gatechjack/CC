# PM UI — LIVE FIXES — TRANSITION (2026-09-12, end of session)

You are a fresh code agent with none of the prior conversation. This hands you everything
needed to build the three LIVE-page fixes with nothing to rediscover. Full atomic authority
was granted to read (box read-only), edit, test, render, commit LOCALLY. NO deploy, NO push,
NO restart — Jack calls the deploy. Engine untouched. pm_web-only.

The three items, in the order Jack ruled to build them: **3 (mark cache) -> 1 (event block)
-> 2 (game labels)**, commit locally after each. Item 3's root cause was required-before-fix
and is fully established below; Items 1 and 2 are analysed + designed but NOT built.

---------------------------------------------------------------------------------------------
## 1. STATE OF THE REPO AND BOX

**prod-live is truth. The box IS truth-reconciled to prod-live for pm_web.** The old
"capture the box and graft" model is OBSOLETE — branch off `origin/prod-live`, build, and the
deploy is a plain diff back onto prod-live.

- `origin/prod-live` tip = **`b1c552b1`** ("PM Farm live-whale badge + promote/demote 409
  guards (Deploy 8 -> prod-live)"), full sha `b1c552b15005f2952a3a465e0a4894e7904f1993`.
- **box == prod-live: 39/39 pm_web files byte-identical, CR-stripped.** Verified this session
  by CR-stripped sha8 of every `.py/.html/.css/.js` under
  `trading_corp/prediction_markets/web` on the box vs `git show origin/prod-live:<path>`.
  Zero mismatches. (Method matters: `git show` under core.autocrlf emits CRLF while the box is
  LF — ALWAYS compare `tr -d '\r' | sha256sum`, never raw; a raw compare reports phantom
  drift. This has caused false alarms before.)
- Box layout is nested: repo root on the box is `/home/azureuser/trading_corp`, and the
  package is `/home/azureuser/trading_corp/trading_corp/prediction_markets/web/...`.
- pm_web runs as its own systemd service **`prediction-markets-web.service`** (single uvicorn
  worker, loopback), SEPARATE from the engine `trading-corp.service`. Same working directory
  (`/home/azureuser/trading_corp`), same DB. A pm_web deploy restarts ONLY pm_web (via
  `az vm run-command ... 'systemctl restart prediction-markets-web'`), never the engine.

### Worktree
- Path: `C:\Users\AA Incorporado\cc-pm-live-fixes-wt`
- Branch: `pm-live-fixes-2026-09-12`, based on `origin/prod-live` (`b1c552b1`).
- **Committed:** WIP **`231dea8a`** — `trading_corp/prediction_markets/web/ui_cache.py` ONLY
  (Item 3 foundation, described in §2). Plus this document (its own commit; see the final
  sha at the bottom). NOTHING else is committed.
- **NOT committed / NOT built:** the Item 3 render side, all of Items 1 and 2, all tests,
  all renders. The WIP is INERT — the render side does not consume it yet, so behaviour on
  the box is byte-identical to prod-live until the render wiring lands.

### Truth baseline (record this command in every run's report)
Run via the sanctioned runner `cc/pm_live_suite.ps1` (it tars the worktree tree to the box
`/tmp`, runs the box venv). The exact box command it executes:

    cd /tmp/scr_pllive && PYTHONPATH=/tmp/scr_pllive \
      /home/azureuser/trading_corp/venv/bin/python -m pytest tests/prediction_markets \
      -q -p no:pytest_ethereum -p no:cacheprovider --continue-on-collection-errors

(`-p no:pytest_ethereum` is MANDATORY on the box venv — a broken web3 plugin crashes
collection otherwise. The pytest final tally line does NOT get captured through the pipe — a
standing harness quirk — so compare the FAILED SET, which IS captured, not a pass count.)

- **RESULT on the tip: 22 FAILED.** The named set (all UI tests):
  `test_live_r3.py` ×15, `test_accounts_m2.py` ×3, `test_stage2_nav.py` ×2,
  `test_stage2_phase3.py` ×2.
- The previously-recorded baseline of **40** came from a DIFFERENT harness (whole-repo or a
  different scratch tree). Do not treat 40 as this harness's baseline; **compare later runs to
  22.** NEW-failures-on-your-branch must be empty; your new tests are additive.
- **These 22 are all UI tests that were green on the RETIRED UI branch** (the pm-ui-rewrite
  line) and never had their updates merged into prod-live — i.e. prod-live carries tests whose
  fixtures/expectations predate the shipped UI. **They are NOT this workstream's job to fix,
  and the next agent must NOT "fix" them without a Jack ruling.** They are the fixed backdrop:
  your differential is "these same 22, plus my new tests passing, and nothing else."

---------------------------------------------------------------------------------------------
## 2. ITEM 3 — MARK CACHE: ROOT CAUSE (fully established, read-only) + WHAT'S BUILT + WHAT REMAINS

### Defect
`/live/kalshi_jack/nfl` alternated across reloads between readable names WITH marks
("$5.25 · 2 of 2 priced") and RAW TICKERS with "no mark · partial: 0 of 2 priced".

### Evidence (pm_web journal, read-only, `journalctl -u prediction-markets-web.service`)
Over the last 6h, intermittent per-series mark-fetch failures (count × series):

    56  series KXNCAAFTOTAL fetch failed (HTTPError)
    35  series KXNFLTOTAL   fetch failed (HTTPError)
    32  series KXNFLSPREAD  fetch failed (HTTPError)
    14  series KXNCAAFSPREAD fetch failed (HTTPError)
     5  series KXNCAAFGAME  fetch failed (HTTPError)
     3  series KXMLBTOTAL   fetch failed (HTTPError)
     3  series KXLALIGAGAME fetch failed (HTTPError)
     2  series KXLIGAMXGAME fetch failed (HTTPError)
     1  series KXUFCFIGHT   fetch failed (HTTPError)

Raw lines clustered at e.g. `01:50:44-45`, `01:51:48`, `02:01:14` — SOME poll cycles fail
these series, others succeed => the alternation. A smoking-gun app line:
`pm_web /live: 14 held position(s) named by CATEGORY fallback (no feed/mark/describe)` listing
the exact NFL/CFB spread+total tickers (e.g. `KXNFLSPREAD-26SEP13BALIND-BAL4`).

- pm_web boot = **Fri 2026-09-11 03:28:56 UTC** (NOT restarted since) => rules OUT the
  post-restart empty-cycle explanation.
- The series list is STABLE (these series are consistently polled because they are held) =>
  rules OUT a series-list-change dropping NFL.
- => **Trigger = transient Kalshi HTTPError on the high-cardinality series.** The failing
  series are exactly the multi-PAGE catalogs (`KXNCAAFTOTAL` ~2008 open, `KXNCAAFSPREAD`
  ~2541, `KXNFLSPREAD` ~379, `KXNFLTOTAL` ~285). `fetch_series_marks` follows the cursor over
  many GETs for these; the small single-page series (tennis/ufc/fed) essentially never fail.
- **429 vs 5xx: UNDETERMINABLE from the current log** — the handler logs only
  `type(exc).__name__` ("HTTPError"), not `exc.code`. Given the pattern (only the big paginated
  catalogs, intermittent, single process), rate-limit (429) on the multi-page fetch is the
  strong hypothesis. FOLLOW-UP (not built, recommend): (a) log `exc.code` so 429 vs 5xx is
  visible; (b) per-series retry-with-backoff in `fetch_series_marks` (a couple of retries with
  jitter) so a transient blip doesn't drop the series for a whole cycle.

### Mechanism, with file:line (all in `trading_corp/prediction_markets/web/`)
1. `marks.py:47` — `Mark.title` — the Kalshi title is a FIELD ON THE MARK. So a series whose
   fetch fails contributes NO `Mark` for its tickers, hence NO title => the row falls to the
   describe/category fallback (raw-ticker-ish). Titles are coupled to marks.
2. `marks.py:115` `fetch_marks`, decision at `marks.py:129`:
   `ok = bool(merged) or not errors` — a PARTIAL fetch (one series HTTPErrors, others succeed)
   returns `ok=True` with `merged` containing ONLY the successful series' tickers. The failed
   series' tickers are simply absent.
3. `ui_cache.py` `update()` (pre-WIP) built `CacheSnapshot(marks=marks, ...)` — a WHOLESALE
   REPLACE. So the partial result REPLACED the prior snapshot => the failed series' prior marks
   (and their titles) were WIPED => "no mark / 0 priced" until the next good cycle.

That is the exact alternation: a cycle where `KXNFLSPREAD/TOTAL` succeeds shows names+marks; a
cycle where it HTTPErrors wipes them.

### What I built (WIP `231dea8a`, `ui_cache.py` ONLY) — and its invariants
- `CacheSnapshot` gains `titles: dict` — the ticker->title map, PERSISTED ACROSS POLLS,
  NEVER evicted. Accessor `UICache.title(ticker)`.
- `update()` now MERGES: `merged = dict(prior.marks.marks); merged.update(new.marks)`. A
  ticker the new poll returned gets the fresh `Mark` (fresh `as_of`); a ticker it did NOT
  return keeps its PRIOR `Mark` with its OLD `as_of`. Titles accumulate from every `Mark`
  ever seen. The merged `MarksResult` is built via a lazy `from . import marks` (import-cycle
  safe). `ok`/`error` on the snapshot reflect THIS poll (for the strip); each `Mark`'s `as_of`
  is its own.
- Invariants: a failed/partial poll never blanks a previously-known mark or title; a ticker
  that has ever resolved a title keeps it; growth of the merged dict is bounded by the finite
  open-market set + pm_web's own restart (volatile cache), and the render only ever reads HELD
  tickers.
- **The render side does NOT consume any of this yet.** `build_live_context` still reads
  `marks_result.marks` and titles still come off `Mark.title` (see below). So the WIP changes
  nothing user-visible until the render wiring lands. That is why Item 3 is NOT complete.

### What remains for Item 3 (precise)
- `poller.py` `refresh_once` (~line 87-94): it calls `cache.update(slates=..., marks=mk,
  refreshed_ts=..., last_error=...)`. Because the merge now lives IN `update()`, the poller
  needs NO merge change — but CONFIRM it still passes the raw per-poll `mk` (partial is fine)
  and a truthful `last_error` (it joins the per-series error strings). The "which series
  failed" set is available from `mk.error` / the poller's `errors`.
- `live_view.py`:
  - `_positions_view` (`live_view.py:535`): currently `title = getattr(mk, "title", None)` and
    `desc = (title or describe_market(tk, leg))` (`:550-552`). CHANGE: the title must come from
    the PERSISTED titles map (thread `titles` in), and the base label must be the ticker-derived
    matchup+shorthand (Item 2) with title as enrichment — NEVER `describe_market`'s
    `'<type>:<ticker>'` (that embeds the raw ticker; see TRAPS). Add per-position `as_of` +
    an `age_sec` so the template can band amber past the stale threshold.
  - `value_positions` (`live_view.py:677`) + `_journal_summary` (`:511`): coverage
    (`n_priced`/`n_total`) already computed; add the poll-status (`marks_ok`, `refreshed_ts`,
    `last_error`) surfacing so the strip can say "refresh failed Nm ago · showing last mark".
  - `build_live_context` (`live_view.py:585`, marks pulled at `:594`, return dict `:662-674`):
    thread the persisted `titles` map + poll status through; the route entry at
    `live_view.py:710` already does `... | {"warming": not snap.ready}` — extend that to pass
    `titles=snap.titles` and the poll error/age.
  - `_build_slot` (`live_view.py:304`): the MLB card slots also read `mark.title`/`bid`; same
    title-from-persisted + as_of treatment for consistency (MLB has a game feed so it's less
    affected, but keep it uniform).
- Templates: `templates/pm_live_subdivision.html` — the coverage/strip cell: add
  "refresh failed Nm ago · showing last mark" beside the "N of M priced" label when
  `marks_ok` is false / `last_error` is set; and the first-cycle `warming` state must read
  **"marks loading"** (3.3) NOT "no mark". `templates/partials/pm_position_rows.html` — the
  per-row value cell shows the value with its age, amber past stale, and "no mark" ONLY for a
  ticker that has never returned a bid.
- `static/pm_desk.css` — the amber "stale" band class for an aged mark; the "refresh failed"
  strip styling. Bump the `?v=` cache-bust on `pm_shell.html` if any CSS/JS changes (there is
  a `test_asset_cache_bust_hashes_match_files` that fails CI if you forget).
- Tests (3.4), all offline against `build_live_context` / `ui_cache.update` with a fake
  `MarksResult`: (a) failed fetch keeps prior marks WITH age; (b) partial fetch updates only
  the returned tickers; (c) titles survive an empty cycle; (d) first cycle (`ready=False`)
  labelled "marks loading". Plus R2: a COLD cache + a fetch failure still yields ZERO raw
  tickers on the rendered page (grep the HTML for `KX...` ticker substrings — must be none).

---------------------------------------------------------------------------------------------
## 3. ITEM 2 — GAME LABELS (analysis + design; NOT built)

### Defect
`/live/kalshi_jack/cfb` and `/nfl` rows read "Over 51.5 points scored · TOT" / "Will there be
over 49.5 points scored? · TOT" — NO game named. Kalshi's title carries no teams for
totals/most spreads, but the matchup IS in the ticker for every structural sport (both team
codes; the matcher decodes them).

### Jack's ruling (locked)
Ticker-derived **matchup + signed shorthand is the ALWAYS-PRESENT base label**; the Kalshi
title enriches the secondary line; **a raw ticker NEVER renders.** (This is also Item 3.1's
"never a raw ticker" floor — build the base label here so Item 3's fallback chain bottoms out
on it, not on `describe_market`.)

- **2.1 Group the non-MLB positions table BY GAME:** one header per event — "MIZ @ KAN"
  (away @ home decoded from the ticker via the existing team map), plus event date + start
  time where the ticker carries it — then that game's rows. **Tennis / UFC / Fed keep
  single-row labels** (no invented matchup — they have no two-team structural ticker).
- **2.2 Row label = SHORTHAND FIRST, title second:** `ML KAN`, `SPR -6.5 MIZ`, `TOT +51.5`
  from kind + held leg. Note a NO on "Missouri wins by over 6.5" IS the `-6.5` side (the
  held-leg sign flips the label). Kalshi's full title -> the secondary line. Drop the bare
  SIDE column unless a test depends on it.
- **2.3** The UPCOMING tile's NEXT line uses the same label: matchup, held shorthand, then
  start time or "start time unavailable" — never the raw question alone.
- **2.4 Tests from REAL box tickers held today** (pull them live, read-only): CFB total +
  spread resolve to the right matchup and SIGNED label; an ATP row unchanged.

### Functions that already do the pieces (all `live_view.py`)
- `game_key_from_ticker` (`:57`) — ticker -> game key (date, teams, ...).
- `_ordered_teams` (`:369`) — ticker -> ordered (away, home) team codes.
- `_short_label` (`:121`) — kind + held leg -> `ML X` / `TOT +N` / `SPR -N X` shorthand
  (this is the shorthand engine; extend/reuse it for the base label).
- `_held_team_code` (`:103`) — resolves the held club for a NO leg (spread sign logic).
- `_kind` (`:81`), `_spread_other` (`:92`), `_split_team_blob` (`:46`).
- The team map: the structural matcher's team map (data-side; `sports_structural_match` +
  the per-league code maps). `_ordered_teams` already uses the ticker decode; confirm the
  away@home order matches the matcher's decode (the matcher is the source of truth for which
  code is away vs home).
- Non-MLB positions table is built in `_positions_view` (`live_view.py:535`) and templated in
  `templates/partials/pm_position_rows.html` (rendered inside
  `templates/pm_live_subdivision.html`). This is the function to restructure into game groups.
- Real held tickers to test against today: `KXNCAAFSPREAD-26SEP11MIZZKU-MIZZ7`,
  `KXNCAAFTOTAL-26SEP11MIZZKU-52`, `KXNCAAFSPREAD-26SEP11RUTGBC-BC15/BC21`,
  `KXNCAAFTOTAL-26SEP11RUTGBC-48/56`, `KXNFLSPREAD-26SEP13BALIND-BAL4` (seen in the pm_web
  log). The four CFB rows group into TWO games (MIZ@KAN, RUT@BC).

---------------------------------------------------------------------------------------------
## 4. ITEM 1 — FIXED-HEIGHT EVENT BLOCK (analysis + design; NOT built)

### Defect
Jack/MLB held positions on four underway games; the LIVE tile's event block grew one row PER
GAME, the 2x2 tile stretched, the grid gave it the whole row, and the money block floated
right over an empty left half. Grouping/labels are CORRECT (the 2026-09-11 `_live_event` fix);
the missing thing is a LAYOUT/height rule.

### Where it lives
- Assembly: `_live_event` (`live_view.py:875`) — groups held positions by underway game
  (`game_key_from_ticker` for MLB; match-stem `tk.rsplit("-",1)[0]` for non-MLB), one row per
  game, ordered most-recently-started first, capped at 3 with a `more` overflow. It already
  returns the featured-ish rows; it does NOT enforce a tile height or single-featured-game.
- Template: `templates/partials/pm_subs_event.html` (the event block), rendered by the LIVE
  tile in `templates/pm_live_subdivision.html`. The tile grid is `.subs .grid`
  (`static/pm_desk.css:322`, `repeat(5,minmax(0,1fr)); gap:10px; grid-auto-flow:dense`, with
  responsive breakpoints at `:323-325`). The LIVE tile spans a 2x2 slot; there is NO
  max-height on the event block, so N games => N rows => the tile grows and the dense grid
  hands it the row.

### Jack's ruled design
- **1.1** LIVE tile has a FIXED height == its 2x2 slot, regardless of game count.
- **1.2** ONE game featured with the full scoreboard (held team marked; positions with
  ML/TOT/SPR shorthand + values, <=3 rows). Featured pick, DETERMINISTIC (comment + test it):
  the underway game whose held position is CLOSEST TO SETTLING — baseball: latest inning, then
  most outs; tie -> most held positions; tie -> away code A->Z.
- **1.3** Every OTHER underway game: ONE line beneath — `PHI@ATL · ML ATL $3.55` (matchup then
  compact shorthand+value pairs, "..." on overflow). Max 3 lines, then a single
  `+N more live >` linking to the detail page. Block height therefore bounded.
- **1.4** Phone: same rule, one column. NO change to any other tile type or the grid.

---------------------------------------------------------------------------------------------
## 5. RECOMMENDED BUILD ORDER AND DEPLOY SHAPE

- Build **3 -> 1 -> 2**, commit locally after each item (tests + renders green before each
  commit). Item 2's base-label work is what makes Item 3's "never a raw ticker" floor real, but
  Item 3 can bottom out on category+market-type in the interim; do Item 2 last as ruled and it
  upgrades the floor to matchup+shorthand.
- Expected file set (union): `web/ui_cache.py` (done, WIP), `web/poller.py` (confirm/none),
  `web/live_view.py`, `web/marks.py` (only if you add `exc.code` logging / retry — optional
  follow-up), `web/templates/partials/pm_position_rows.html`,
  `web/templates/partials/pm_subs_event.html`, `web/templates/pm_live_subdivision.html`,
  `web/static/pm_desk.css`, and `web/templates/pm_shell.html` for the `?v=` cache-bust.
- **app.py: NOT touched this session, and not expected to be** — the changes are render
  assembly (live_view), cache (ui_cache/poller), templates, and CSS. If you find you need an
  app.py route change, that's a scope surprise — flag it. (For reference: `/live` is
  `app.py:1119` -> `_load_live_list` -> `tiles_all` -> per-account `build_live_context`.)
- Deploy shape (Jack runs it): plain diff of the changed pm_web files from prod-live ->
  prod-live+change; **backup is a gate**; ONE pm_web restart via
  `az vm run-command ... 'systemctl restart prediction-markets-web'` (ssh+sudo has no TTY and
  fails-safe — use az); engine `trading-corp.service` NEVER touched; advance prod-live to the
  deployed commit AFTER the post-check is green. Box-is-truth reconciled, so this is a true
  fast-forward, not a graft.

---------------------------------------------------------------------------------------------
## 6. TRAPS (things hit or nearly hit)

- **Harness baseline difference:** this harness reports 22 FAILED, not the previously-recorded
  40. Compare the FAILED SET, not a count; the tally line is not captured through the pipe.
- **CR stripping:** always `tr -d '\r'` before sha-comparing box vs `git show` (autocrlf).
- **Retired UI branches:** do NOT build off `pm-ui-rewrite-*` or any older UI branch — they
  are retired and prod-live has moved past them. Branch off `origin/prod-live` only.
- **The box is truth now:** capture-and-graft is obsolete; box==prod-live for pm_web. Build on
  prod-live, deploy as a plain diff.
- **R2 "no tickers":** a raw Kalshi ticker must NEVER render as a user-facing label. The
  current `describe_market(tk, leg)` fallback (`live_view.py:552`, in `market_describe.py`)
  returns `'<type>:<ticker>'` which EMBEDS the ticker — that is the raw-ticker leak. Replace
  the fallback chain: persisted title -> ticker-derived matchup+shorthand (Item 2) ->
  category+market-type -> never the ticker. Add a test that greps the rendered HTML for `KX`
  substrings and asserts zero, even on a cold cache + a failed poll.
- **Partial-fetch `ok=True` semantics** (`marks.py:129`): a partial poll is "ok", so callers
  must NOT treat `ok=True` as "all marks present" — coverage is per-ticker (`value_positions`
  n_priced/n_total). The merge (WIP) fixes the wipe; the render must still show honest partial
  coverage.
- **Tile/detail NAMES must never depend on the mark cache.** This is the whole point of Item
  3.1: the human name comes from the persisted titles map or the ticker decode, never from a
  `Mark` that a failed poll may lack. If a name can vanish on a mark failure, the fix is wrong.
- **Engine vs pm_web:** two services, same tree. Only ever restart `prediction-markets-web`
  for a UI deploy; the engine (`trading-corp.service`) is armed and trades real money —
  untouched. (Unrelated but live: UFC method-types were enabled on both kalshi_jack and
  kalshi_karen this same day on the engine side; do not conflate.)

---------------------------------------------------------------------------------------------
## Runners produced this session (cc/, read-only unless noted)
- `pm_live_boxshas_ro.{ps1,sh}` — box pm_web CR-stripped sha8 (the box==prod-live check).
- `pm_live_suite.{ps1,sh}` — stages the worktree tree to the box venv, runs the full pm suite
  (the truth-baseline command above). Reuse for the differential.
- `pm_live_markfail_ro.{ps1,sh}` — pm_web mark-failure log evidence (§2).
- `pm_live_tail_ro.{ps1,sh}` — raw pytest tail puller.
All follow the sanctioned channel (a `.ps1` streams a `.sh` via `ssh "tr -d '\r\357\273\277' |
bash"`, ASCII-only, no ad-hoc inline ssh — the classifier blocks inline ssh, correctly).
