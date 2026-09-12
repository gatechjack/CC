# PM /live — LIVE FIXES (Items 3 -> 1 -> 2) — 2026-09-12

**STATUS: Items 3 + 1 + 2 all BUILT + TESTED + RENDERED + COMMITTED (local). ALL THREE DONE.** NOT deployed /
NOT pushed. pm_web-only. Engine untouched. Branch `pm-live-fixes-2026-09-12` rebased onto `origin/prod-live`.
Item 2 was expanded per Jack's ruling (2026-09-12): the same label rule now covers the trade drawer's market
column AND the Farm whale paper-trade list, via ONE shared formatter — no second implementation, fail-closed
everywhere.

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
## 5. ITEM 2 — NON-MLB GAME LABELS — BUILT (commit `<item2>`)

Every non-MLB position/trade now reads as **matchup + signed shorthand**, never a raw ticker/slug, through **ONE
shared formatter** `live_view.format_market_label(matchup, kind, short, title) -> (primary, secondary)`. Three
surfaces, one rule (Jack's ruling 2026-09-12), each decoding its OWN source then feeding the same formatter:

- **/live positions table** (Kalshi tickers): `_positions_view` decorates each row with `market_matchup(tk)` +
  `structural_game_key(tk)` + `_short_label`; `_group_by_game` groups rows under one **"AWAY @ HOME · date ·
  start|unavailable"** header. Row `desc` = the tagged shorthand (`SPR -6.5 MIZZ` / `TOT +51.5` / `ML MIZZ`), `sub`
  = the Kalshi title (or the matchup when titleless). The bare **SIDE column is dropped** (the sign carries the
  held direction). Template `pm_live_subdivision.html`: `posrow` shows `desc`+`sub`; `postable(groups)` renders the
  per-game header; view uses `pv.active_groups`/`pv.complete_groups`.
- **Trade drawer** (`pm_trade_drawer.html`, Kalshi tickers): the *Game* column resolves matchup from the MLB feed
  OR the ticker decode (`market_matchup`) so a cfb/nfl row names its game; the *Type* column shows `t.label` = the
  same tagged shorthand from `format_market_label`.
- **Farm whale paper-trade list** (`pm_paper_trade_rows.html`, **Polymarket slugs**): `app._load_watchlist_whale`
  decorates each paper trade via `live_view.poly_market_label(category, slug, outcome, title)`, which parses the
  slug with the engine's canonical `parse_poly_bet` (no re-implementation) and feeds the SAME formatter. The market
  cell shows matchup + tagged shorthand; the **slug is always kept beneath as provenance**.

**The decode** reuses the structural matcher's DATA-side maps so pm_web's labels can never diverge from the
matcher's: `_SPORT_TEAM_MAP` (MLB/NFL/NBA/NHL/WNBA + `CFB_TEAMS`) with **longest-prefix wins** (KXWNBA≠KXNBA);
`_split_team_blob` splits "AWAYHOME" **fail-closed** (a split is returned ONLY when EXACTLY ONE partition has both
codes in the map — an ambiguous blob yields no matchup, never a guess); the `_short_label` spread regex widened
`[A-Z]{2,3}`→`[A-Z]{2,}` so 4-char CFB codes (MIZZ7) decode. `LEAGUES`/`parse_poly_bet` verified standalone-safe
(stdlib + data-side only; `mlb_poly_kalshi_match` imports only `re`/`dataclasses`/`sports_team_mapping`).

**FAIL-CLOSED everywhere** (validated): tennis/ufc/fed tickers and non-structural Poly categories → `matchup=None`
→ the honest single label (no game header); a Poly prop suffix (`-nrfi`, `-1h-*`) or non-sport slug →
`(None,None,None)` → the paper row keeps its title/slug. Decode proof (smoke + tests): CFB `MIZZKU`→`MIZZ @ KU`;
spread `MIZZ7` yes→`-6.5 MIZZ`/no→`+6.5 KU`; total `52`→`+51.5`; NFL `BAL4`→`-3.5 BAL`/`+3.5 IND`; MLB `CINCHC`→
`CIN @ CHC`; ATP/UFC→no matchup; Poly `cfb-mizz-ku-…-spread-home-6pt5` Kansas→`SPR -6.5 KU`, Missouri→`SPR +6.5 MIZZ`.

**Base-floor bug fixed (found by the Item-2 tennis cold-cache test):** `_base_label` appended `_kind`'s fallback
token, which for a non-ML/TOT/SPR series is the raw series (`kxatpmatch`) — so the Item-3 floor for a titleless
tennis/ufc row was **"ATP KXATPMATCH"**, LEAKING `KX` into the very label the floor exists to keep ticker-free.
Fixed: known market type → "CFB TOT"; otherwise the bare "<CATEGORY>" ("ATP"). (Latent before Item 2 because
tennis/ufc rows normally carry a persisted title.)

**Tests: 12 new** (`test_live_fixes_item2.py` — Kalshi decode both legs of ml/total/spread across cfb/nfl/mlb,
Poly decode incl. 4 fail-closed cases, `_group_by_game`, two `build_live_context` integrations [cfb grouped
shorthand + tennis fail-closed floor], and the drawer `_trade_rows` fallback). **3 Item-3 tests reconciled** to the
composed behavior (a CFB row's `desc` is now the shorthand, the persisted title survives as the `sub` secondary —
the Item-3 title-persistence invariant still asserted, just on the secondary line). **Renders viewed:**
`cc/renders/item2_{cfb,nfl,atp}_positions.png` (cfb/nfl grouped under a game header with signed shorthand + Side
column gone; atp unchanged — no header, honest single rows), `item2_cfb_drawer.png` (drawer Game=matchup,
Type=`SPR +6.5 KU`/`TOT +51.5`/`ML MIZZ`), `item2_farm_paper.png` (paper rows `MIZZ @ KU · ML MIZZ`/`TOT +52.5`/
`SPR +6.5 MIZZ`; the `-nrfi` prop fails closed to "No first-quarter score", slug kept beneath).

**Full suite after Items 3+1+2: 200 tests, 22 failures (the SAME baseline set), 0 NEW failures.**

**Away/home order note (per 2.4):** for the Poly path the slug encodes `{away}-{home}` (authoritative). For the
Kalshi path the blob order (away+home) is the MLB-verified convention (`SDCIN=SD@CIN`) applied uniformly; a wrong
order would be a display nit, never a money error (the matcher keys on a frozenset). Box RO validation against a
real held cfb/nfl ticker + its Poly source is the remaining confirmation before any deploy.

### ITEM 2 — FILE DIFF vs the branch tip `48a5817f` (CR-sha16 BEFORE -> AFTER)

`app.py` IS touched this item (the Farm-paper enrichment in `_load_watchlist_whale`); items 3+1 had left it
untouched. No migration, no shared-trio, no schema change. `pm_desk.css` gains `.psub`/`tr.pgame`/`.pgm` →
`pm_shell.html` `?v=` bumped `4102424b`→`92a1ef2e` (CR-stripped sha8).

| file | BEFORE | AFTER |
|---|---|---|
| `web/app.py` | `ef1dec75a25c52cc` | `e39624887c528bea` |
| `web/live_view.py` | `1f930df60b36e041` | `ff6c3c03e36b0120` |
| `web/templates/pm_live_subdivision.html` | `d1494f6f84e1ac9e` | `98ffb7a2613d7794` |
| `web/templates/partials/pm_trade_drawer.html` | `fc251c6d37310eb4` | `36edbd5237de9396` |
| `web/templates/partials/pm_paper_trade_rows.html` | `2c6f136bd062acb8` | `7d19481724a04c26` |
| `web/static/pm_desk.css` | `4102424be0324829` | `92a1ef2e6d4e0ff6` |
| `web/templates/pm_shell.html` | `8b5a340e44a13e59` | `c134af369beeb5be` |

Git-only: `tests/prediction_markets/test_live_fixes_item2.py` (new), `test_live_fixes_item3.py` (3 reconciled),
this report.

--------------------------------------------------------------------------------
## 6. RUNNERS / RENDERS
`cc/pm_fixes_item3_render.py`, `cc/pm_fixes_item1_render.py`, `cc/pm_fixes_item2_render.py`,
`cc/pm_item2_drawer_render.py`, `cc/pm_item2_smoke.py`; renders under `cc/renders/item3_*`, `item1_*`, `item2_*`.
