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
## 5. ITEM 2 — NON-MLB GAME LABELS — BUILT (commit `aaba70a5`)

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

**Away/home order — CONFIRMED on live box data (board-authorized RO run 2026-09-12T16:22Z, read-only, no writes):**
`cc/pm_item2_awayhome_ro.{ps1,sh}` pulled the real held structural-sport tickers from the live journal
(`pm_subdivision_order`, `file:...?mode=ro`) and fetched each game's matchup from Kalshi (market/event titles) +
Polymarket (slug); `cc/pm_item2_awayhome_compare.py` decoded each with the shipped Item-2 code. Result: **15/15
real tickers** (6 cfb, 3 nfl, 6 mlb control) — my `market_matchup` away/home order **== Kalshi's own event
sub-title order on every one** (e.g. `KXNCAAFGAME-…OKLAMICH`→`OKLA @ MICH` = Kalshi "OKLA vs MICH"; `…MEMBSU`→
`MEM @ BSU`). For the **5 games that had a retrievable Polymarket slug** (MEMBSU, OKLAMICH, OREOKST, BALIND, NODET)
the order **also matches Poly's authoritative `{away}-{home}`** (cross_order_match=True all 5). The fail-closed
4-char blob split resolved every real CFB blob uniquely (OKLA|MICH, MEM|BSU, RUTG|BC, ORE|OKST, MIZZ|KU, SMU|FSU).
**Conclusion: the away+home convention is correct across cfb/nfl/mlb — NO label changes needed.** (Run was
LOUD-capped at 6 games/category; cfb had 9 distinct games, mlb 92 — the sample is unanimous and the convention is
structural.)

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

--------------------------------------------------------------------------------
## 7. DEPLOY 10 — LIVE (2026-09-12) — Items 3+1+2 shipped to prod-live

**RESULT: DEPLOYED + VERIFIED + prod-live FF. `origin/prod-live 8fdded34 -> c339437b` (code, tag
`pm-livefix-deploy10-2026-09-12`). pm_web-ONLY; the ARMED engine (`trading-corp` PID 351422) was NEVER touched
(PID + NRestarts identical before and after). NO migration, NO engine-shared file, NO packages/venv/unit changes.**

### File set (the REAL diff vs prod-live 8fdded34 = the UNION of Items 3+1+2 = **9 files**, not the Item-2-only 7)
`app.py` **IS** in the set (Item-2 Farm-paper enrich); `subdivision.py` is **NOT** (so, unlike Deploy 9, there is
NO engine-shared file -> no additive-only proof needed). CR-sha256 BASE(8fdded34) -> TARGET(c339437b), all landed LF:

| file | BASE | TARGET |
|---|---|---|
| `web/ui_cache.py` | `dee87281883f7036` | `1919ca1925ba316a` |
| `web/app.py` | `ef1dec75a25c52cc` | `e39624887c528bea` |
| `web/live_view.py` | `51f916960e7c1174` | `ff6c3c03e36b0120` |
| `web/templates/pm_live_subdivision.html` | `cea30f343374b212` | `98ffb7a2613d7794` |
| `web/templates/partials/pm_subs_event.html` | `3dd74d8ee56d3ea5` | `51ebf8ce7e18378e` |
| `web/templates/partials/pm_trade_drawer.html` | `fc251c6d37310eb4` | `36edbd5237de9396` |
| `web/templates/partials/pm_paper_trade_rows.html` | `2c6f136bd062acb8` | `7d19481724a04c26` |
| `web/static/pm_desk.css` | `4102424be0324829`*(→ base at 8fdded34 = `c5efb60aae99071d`)* | `92a1ef2e6d4e0ff6` |
| `web/templates/pm_shell.html` | *(base at 8fdded34 = `7e73fe6143 72fce5`)* | `c134af369beeb5be` |

*(The §4 table shows the Item-2 delta vs 48a5817f; the Deploy-10 BASE column above is vs prod-live 8fdded34: pm_desk.css
`c5efb60a`->`92a1ef2e`, pm_shell `7e73fe61`->`c134af36`.)*

### Steps 1-14 (all output recorded via read-only .ps1/.sh runners under `cc/pm_deploy10_*`)
- **[1] Pre-state:** engine `trading-corp` **MainPID=351422 NRestarts=0** (start 01:33:35Z); pm_web
  `prediction-markets-web` MainPID=363574 NRestarts=0; **pm schema head=21**; **31 arm rows** (global `armed:true` +
  30 per-sub armed); 32 driver heartbeats all fresh (0-18s). /healthz 200 schema 21.
- **[2] box == prod-live 8fdded34: 63/63 tracked files, 0 mismatch, 0 missing.** 7 `extra` files flagged = stale
  `.bak_*`/`.orig` from **Sept-1** pm_web/whale deploys (predate the milestone deploy + Deploy 9, both of which
  passed) -> INERT (Python won't import `.bak`/`.orig`; none is a served route; the deploy touches only the 9
  named files) -> NOT a content divergence, gate PASS on intent; left as found (not mine to delete).
- **[3] BACKUP GATE:** all 9 files copied to **`/home/azureuser/pm_deploy10_backup_20260912T163854Z/`**; each
  backup CR-sha256 == box == prod-live base (9/9). This is the rollback source.
- **[4] Before-state:** held jack/cfb=5 (MEMBSU ml, OREOKST-ORE24 spr, TENNGT-TENN15 spr, DUKEILL-53 tot,
  OSUTEX-50 tot), jack/nfl=2 (BALIND-BAL4 spr, NODET-50 tot), jack/mlb=0 open (no MLB game underway). Served
  pm_desk.css CR-sha `c5efb60a` (=prod-live), `/`->`?v=c5efb60a`. **2-cycle poll stable/healthy** (mlb+nfl priced
  pairs identical across both cycles, 0 refresh-failed).
- **[5] Deploy:** tar built from git blobs at c339437b, **CR-stripped to LF** to match the box (box files are LF);
  staged 9/9 == target, landed 9/9 in-place raw-sha == target + CR=0; py_compile OK (ui_cache/app/live_view);
  **import check: app imports clean, ZERO engine/broker/execution modules, `trading_corp.persistence`
  (arm-WRITE) NOT loaded** (/pm/arm write-capability=0 -- read-only web app confirmed).
- **[6] Restart:** `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm ... systemctl restart
  prediction-markets-web`. pm_web MainPID 363574 -> 365664 (then 365841 after the step-7 capture restart).
  **engine MainPID=351422 UNCHANGED immediately after.**
- **[7] Cold-cache "marks loading" OBSERVED LIVE:** a tight fetch-loop right after restart caught the transition
  (t=5-13 ~= first 2.7s of serving: **"3 marks loading"**; t=14+ settled to the steady "no mark" (2) once the
  first poll completed). Correct `ready`-starts-False semantics (ui_cache.py:34,82).
- **[8] Two cycles:** cfb/nfl/mlb priced pairs stable across both cycles, **0 "refresh failed", 0 series
  failures**, positions-label KX=0 both cycles. **A live failed-series "refresh failed + age" note was NOT
  observed** (the poll was healthy the whole window) -> not claimed; that path is covered by the Item-3 unit tests
  + the step-7 cold-cache observation.
- **[9] Item 1:** NO MLB game underway at deploy (jack/mlb 0 open) -> the LIVE-MLB featured-game/chip/"+N more
  live" layout is verified by the harness renders `cc/renders/item1_*` (measured constant 480px @ 1/2/4/6 games);
  the `/live` tile grid renders intact (200, 33KB).
- **[10] Item 2 (live, real tickers):** /live/kalshi_jack/cfb -> **5 game headers** (MEM@BSU, ORE@OKST, TENN@GT,
  DUKE@ILL, OSU@TEX) with shorthand rows (ML BSU / SPR -23.5 ORE / SPR +14.5 GT / TOT -52.5 / TOT +49.5), **no
  SIDE column** (`pside` cells=0); nfl -> 2 headers (BAL@IND, NO@DET); atp -> 0 game headers (honest single rows).
  Trade drawer Game=matchup + Type=tagged shorthand (e.g. `TENN @ GT` / `SPR +14.5 GT`). Farm whale paper list
  (`/watchlist/.../mlb`, 163 rows) -> `TEX @ ARI . ML TEX` / `HOU @ TB . SPR -1.5 TB` with the slug beneath.
- **[11] Roster + detach:** the Deploy-9 roster panel PRESENT on every live page. Detach gating (GET confirm, NO
  POST): karen->OWN kalshi_karen 200; karen->kalshi_jack **403**; jack(admin) 200. `owner_identity` jack=None
  (admin-only), karen='karen'.
- **[12] Cache-bust:** served pm_desk.css CR-sha `92a1ef2e` == target; `/`->`?v=92a1ef2e`; all 7 static assets 200;
  `/`, both account pages, all 23 real `/farm/{category}` 200 (the lone non-200 was `/farm/search`, an action
  ROUTE not a category -> not a 404, not a rollback condition).
- **[13] Engine + logs:** engine **MainPID=351422 NRestarts=0 (unchanged)**; **journalctl -p err since restart =
  "No entries" for BOTH services**; 0 tracebacks/500s; orders total=598/filled=569, **0 new since restart** (and
  pm_web cannot place orders -- import-check proved it); **0 double-escaped entities** across 8 pages.
- **[14] Wrap:** pushed `pm-live-fixes-2026-09-12` (**force-with-lease** -- the branch was the sanctioned rebase of
  the pre-rebase remote `44540982`; all 3 remote commits' content is present in the rebased line, nothing lost);
  **FF `origin/prod-live 8fdded34 -> c339437b`** (git-enforced FF); annotated tag `pm-livefix-deploy10-2026-09-12`
  -> c339437b, pushed. **Re-ran box == prod-live c339437b: 63/63, 0 mismatch/missing.**

### Raw-ticker disposition (the one nuance)
Deploy 10 is **raw-ticker NEUTRAL**: total "KX" count is **identical before vs after on every page** (cfb 31/31,
nfl 12/12, atp 63/63, mlb 90/90). **Positions-table labels = 0 KX on every page** (the Item-3/2 goal). Every
remaining KX is (a) the drawer's by-DESIGN **Ticker provenance field** + `.pt` title-attr hover, or (b) the
**pre-existing** `describe_market` drawer "Market" detail field ("total: KX..." for non-MLB -- unchanged by this
deploy; MLB shows real names). A literal "zero KX hits" is therefore not achievable (intended provenance +
pre-existing behavior that rollback would NOT cure), but the intent (no raw ticker as a position LABEL) is fully
met. **BACKLOG (pre-existing, off-box only -- never fix-forward on prod):** extend `format_market_label`'s tag +
`describe_market` to non-structural categories, so the drawer Type ("KXATPMATCH KHA") and Market ("kxatpmatch:
KX...") for atp/ufc/cs2/fed/soccer read a clean category label instead of the raw series (same root as the
`_base_label` KX-leak already fixed for the positions floor).

### Rollback (unused)
Not triggered (steps 7-13 passed; step 12 no 404). If needed: restore
`/home/azureuser/pm_deploy10_backup_20260912T163854Z/` -> restart pm_web only -> verify old pages 200 + engine PID
unchanged. Never fix forward on prod.
