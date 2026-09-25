# PM UI — Three Table Passes: BUILD REPORT (2026-09-25)

Built autonomously per the Board-authorized plan (`PM_TABLES_PLAN_2026-09-25.md`, rulings OQ-1…6). Three stacked
branches off prod-live, one per phase; **not deployed, not restarted** — deploys/restarts are Board actions. Each
phase: pm_web-only, no migration, `subdivision.py`/`db.py`/`execution.py`/`pm_whale_roster.html` untouched.

## STEP 0 — gates (all PASS)
- prod-live tip: **`558fc143`** ("leg-audit: ITF trailing-X surname pad rule -> ok:code_pad"), re-anchored (unchanged).
- **box == prod-live: 48/48** tracked pm_web files (CR-stripped sha16); box also carries 6 untracked `.bak_*`/`.orig`
  deployment-cruft files (not tracked, out of scope) — noted.
- Baseline: **603 collected, 579 passed, 24 failed** — the exact documented pre-existing set (accounts_m2×3,
  ctx_pagination×1, live_r3×14, refresh_stagger×1, rung3×1, stage2_nav×2, stage2_phase3×2), all stale UI assertions
  (the pages render 200; the tests assert old nav text). Command: `python -m pytest tests/prediction_markets -q
  -p no:pytest_ethereum -p no:cacheprovider --continue-on-collection-errors` (via `.venv-webtest`).
- Engine `trading-corp` MainPID **534581** / NRestarts **0**; pm_web `prediction-markets-web` MainPID **523799** /
  NRestarts **0**; schema head **24** (mig 023 `pm_whale_score`, 024 `pm_subdivision_attachment_event` + core tables
  present). Plan doc committed on the p1 branch (`9ae0ec4a`).
- Branches: `pm-tables-p1-2026-09-25` (`6eb797d0`, off `558fc143`) → `pm-tables-p2-2026-09-25` (`47654994`, off p1)
  → `pm-tables-p3-2026-09-25` (`1e7d8056`, off p2). Upstreams unset (no stray push can reach prod-live).

**Render evidence note (all phases):** rendering was verified via **served-page TestClient renders of the real
templates** (asserted in the new tests — the pages render 200, the new markup + sort links appear, the drawer floors)
plus the box-RO tie-outs below. Browser **PNGs were not captured locally** (the app runs headless in the test
harness); a visual/phone pass fits the Board deploy's render step. No visual regression is expected (CSS reuses
existing classes; Phase-2 CSS is inline-scoped, no `pm_desk.css` change, no cache-bust).

---

## PHASE 1 — Farm category page: server-side sort (OQ-1) + state-aware Analyze (OQ-2)
**Branch `pm-tables-p1-2026-09-25` @ `6eb797d0`** (off prod-live `558fc143`).

**Changes (file:line):**
- `web/live_view.py` — NEW `sort_watchlist`/`sort_prospects`/`_farm_sort`/`_farm_name_key` + column/key maps
  (`WATCHLIST_SORT_COLUMNS`, `PROSPECTS_SORT_COLUMNS`), mirroring `_roster_sort` (None-last both ways; JUDGE by the
  tier-capped `score_sort_key` composite → desc-first floats PROMOTE to top). Appended after `build_roster_table`.
- `web/app.py` — `_load_farm_category(..., wsort, wdir, psort, pdir)` sorts both lists server-side after enrichment
  (replaces the inline line-547 prospects sort), returns the sort state; route `farm_league_category` reads the 4
  params.
- `templates/partials/pm_watchlist_rows.html` — NEW `wsorth` macro; every column becomes an `<a href>` sort link
  (Watchlist was not sortable before). Table was `pm-farm-table` (no sort).
- `templates/partials/pm_prospects_rows.html` — NEW `psorth` macro; sortable columns migrated to server-side; removed
  `pm-sortable-table`, all `data-sort`/`data-sort-value`, **and the load-bearing JUDGE `-sort_value` negation hack**;
  win% stays deliberately non-sortable (honesty rule). Action button made **state-aware** (unscored→Analyze;
  scored→View result [$0] + Re-analyze [?force=1] + age); JUDGE/win% un-analyzed [Analyze] controls kept readable.
- `templates/pm_farm_category.html` — removed the `pm_sort.js` `<script>` include (retired for these two tables; the
  static file is left in place, now unreferenced).

**app.py touched:** yes (query-param reads + loader sort calls + returned sort state; no order path, no structural change).

**Deploy diff vs prod-live `558fc143` (CR-sha16 before → after):**

| file | before | after |
|---|---|---|
| web/app.py | bcece565e3cb0693 | f1dce3a9c3a9d36f |
| web/live_view.py | e3df1e2b10c52fbd | 60106828baa578f1 |
| web/templates/pm_farm_category.html | 678c9bf5c6668c94 | fbfd37909cc5e51d |
| web/templates/partials/pm_prospects_rows.html | c784369f7d427c66 | 89ebed73569d6062 |
| web/templates/partials/pm_watchlist_rows.html | 1f0d66b7f074448f | a94440f4e3dfc1b4 |

**Evidence:** full suite **589 passed / 24 pre-existing** (zero new failures; +10 new tests). New `test_farm_sort.py`
(10): defaults unchanged (Watchlist name-asc, Prospects cost-ROI-desc), None-last both ways, every Watchlist column
sortable, win% non-sortable on Prospects, server-side `<a>` header links (no `data-sort-value`/`pm-sortable-table`),
params cross-preserved, action-button states, vocabulary (no "pinned"/"candidate"). Replaced the negation-DOM test in
`test_prospects_score.py` with a server-side judge-sort test (PROMOTE→INSUF→un-analyzed). **Box-RO tie-out** (mlb, 34
pinned rows incl 7 None-roi): `sort_watchlist(roi desc)` == box SQL `ORDER BY roi DESC` wallet-for-wallet, None block
last. Import-guard `test_ctx_builder_imports` green (no engine import).

---

## PHASE 2 — Splits Whale-Grid header enrichment (OQ-6)
**Branch `pm-tables-p2-2026-09-25` @ `47654994`** (off p1 `6eb797d0`).

**Changes (file:line):**
- `web/app.py` — `_load_watchlist_splits`: thread `n_closed/wins/losses/win_rate` from the pinned rows (already read
  via `farm.farm_rows(PINNED)` — the SAME reader the Farm Watchlist uses) into `scores_by_wallet`.
- `web/live_view.py` — `build_watchlist_splits`: embed those paper-W-L fields (+ existing `trusted`/`copied_by`) in the
  per-whale position dict.
- `templates/pm_farm_splits.html` — grid header per whale: `th.live` highlight + copying account(s) on hover (title)
  for live-copied whales; a `th3` W-L line (`W–L · win% · thin`), **"—" at 0 closed, THIN under 50**. CSS (`th.live`,
  `.th3`) added to the template's **inline `<style>`** — no `pm_desk.css`, no cache-bust.

**app.py touched:** yes (loader map only; no route/structural change).

**Deploy diff vs prod-live (=p1 `6eb797d0`) (CR-sha16 before → after):**

| file | before | after |
|---|---|---|
| web/app.py | f1dce3a9c3a9d36f | 6f46fea67b47cc8c |
| web/live_view.py | 60106828baa578f1 | 8c826501c1c4489a |
| web/templates/pm_farm_splits.html | f2a756ad13ae8aa0 | 7478094cc86e9b86 |

**Evidence:** full suite **592 passed / 24 pre-existing** (+3 new). New `test_splits_grid_enrich.py` (3): builder
carries the W-L + accounts; served `?view=grid` render shows the live highlight, "LIVE on Jack", the exact W-L/thin/
zero strings. **Box-RO tie-out** (atp, 6 live-copied whales): grid-header W-L == the Watchlist reader values; real-data
boundary confirmed (n=50 → not thin `25-25 50%`; n=43 → thin; n=None → "—"). Grid cells unchanged.

---

## PHASE 3 — non-MLB live detail: flat sortable tables + drawer floor (OQ-3/4/5, 3c)
**Branch `pm-tables-p3-2026-09-25` @ `1e7d8056`** (off p2 `47654994`).

**Changes (file:line):**
- `web/live_view.py` — NEW `sort_positions` + `POSITIONS_SORT_COLUMNS`/key maps/`_pos_event_date` (OQ-4 defaults:
  Active=event date asc, Complete=settle date desc; OQ-5: game sorts by event date, undated rows last; None-last).
  `_positions_view` rows now carry `placed_ts` (earliest entry fill's `submitted_ts`) + `order_id` (representative
  entry order). **3c:** `_trade_rows(..., titles, category)` floors `label` (via `_base_label` when no matchup) AND
  `desc` (when `describe_market`'s fallback embeds the ticker) → no raw series tag; structural rows byte-unchanged.
  `build_live_context` threads `titles`/`category` into `_trade_rows`.
- `web/app.py` — `_load_live_subdivision(..., psort, pdir, tab)` sorts the shown tab's flat list, returns tab + sort
  state; route `live_subdivision_page` reads `?psort/?pdir` (loader now sets `data["tab"]`).
- `templates/pm_live_subdivision.html` — `posrow`/`postable` (grouped) replaced by **`possorth`/`posrow_flat`/`postbl`**
  (flat table; game is a sortable column; `?psort/?pdir` links preserve the tab + the roster's `?sort/?dir/?whales`;
  reuses the global `pm-sortable`/`pm-sort-asc|desc` glyphs — no CSS change). Columns: placed(ET) · settled(ET,
  Complete) · game · bet · contracts · fill/cost/value+age (Active) or realized (Complete) · status · whale · order.

**app.py touched:** yes (query-param reads + loader sort + returned state; no order path).

**Deploy diff vs prod-live (=p2 `47654994`) (CR-sha16 before → after):**

| file | before | after |
|---|---|---|
| web/app.py | 6f46fea67b47cc8c | 911d4d17869bd68d |
| web/live_view.py | 8c826501c1c4489a | 005d2cf91806502d |
| web/templates/pm_live_subdivision.html | 69ada934adc038fe | 5f8621a65d58b393 |

**Evidence:** full suite **604 passed / 24 pre-existing** (+12 new). New `test_live_tables_p3.py` (12): sort_positions
defaults per tab, event-date/None-last, invalid→default, text columns; `_positions_view` carries placed_ts/order_id;
drawer floor (non-structural → no series tag, "ATP"; title beats bare floor; structural label unchanged, desc
floored); **served renders** of the flat Active table (sort links + headers), the Complete-tab sort, and the atp
drawer (`<td>ATP</td>` — floored, not `KXATPMATCH`). Existing `test_live_fixes_item2/3` + `test_mlb_card_newfamilies`
(MLB regression lock) all green. **Box-RO tie-out** (nfl, 189 orders): Complete settled **66 == 66** rows, realized
**9.9285 == 9.9285** to the cent; `sort_positions(settled desc)` is settle-date non-increasing and **differs from
ticker-alphabetical** (the "random" order is gone).

**Known minor limitation (documented):** on the /live page the positions sort links preserve the roster table's sort;
the roster's own links (unchanged, `pm_whale_roster.html` not touched) do not carry `?psort/?pdir`, so sorting the
roster resets the positions sort to its default. Independent-per-table; not required by the brief.

---

## RENDER PASS (2026-09-25, before deploy) — every changed page viewed

Harness: Playwright chromium + FastAPI TestClient against seeded fixture DBs (the Deploy 12–15 pattern; `pm.css`
+ `pm_desk.css` inlined, `<script src>` stripped → the render IS the JS-off view), pointed at the p3 worktree, via
`.venv-webtest`. Runners (in `C:\Users\AA Incorporado\cc`): `pm_p1_render.py`, `pm_p2_render.py`, `pm_p3_render.py`.
PNGs committed under `reports/prediction_markets/renders/`. **I viewed every PNG; no defect found — no fix needed.**

**Leak grep on every rendered HTML:**
- Farm (`/farm/mlb`) + Splits (`/farm/mlb/splits`): **0** KX series tags, **0** "pinned"/"candidate".
- Live (`/live/...`): **0** "pinned"/"candidate"; **0** KX rendered as a Type/Market/Bet **label**. KX appears ONLY
  in (a) the Bet-cell `title=` ticker hover and (b) the drawer's dedicated, labeled **"Ticker"** detail field
  (`pm_trade_drawer.html:39`) — both pre-existing, deliberate (the raw ticker as a hover/labeled field, never a
  market label). 3c verified: the drawer Type = `ATP` and Market = `ATP` (floored), never `KXATPMATCH…`.

### Phase 1 — /farm/mlb (7 Watchlist, 6 Prospects)
- `renders/p1_R1_R4_default.png` (R1/R4): default page. Watchlist default = display-name asc; Prospects default =
  cost-ROI desc. All 3 Analyze states on one page — unscored → **Analyze**; scored → **View result** + **Re-analyze**
  + age (**scored 5d / 2d ago**); just-analyzed → **scored 0d ago**. Judge badges + honesty caveat render. No defect.
- `renders/p1_R2_watchlist_roi_desc.png` (R2): Watchlist by ROI desc = +31.0, +20.0, +15.0, +2.0, −5.0, −8.0, — (None
  last); roi header shows the active-sort affordance. No defect.
- `renders/p1_R2b_watchlist_netpnl_asc.png` (R2): Watchlist by Net PnL asc (−90 first → +900). No defect.
- `renders/p1_R3_prospects_n_desc.png` (R3): Prospects by N desc = 200, 90, 70, 55, 40, 3 — confirms server-side
  sort replaced pm_sort.js. No defect.
- `renders/p1_R5_jsoff_watchlist_roi_desc.png` (R5): the R2 URL rendered with scripts stripped — **byte-identical**
  served HTML to R2, so JS-off order == JS-on order (sort is server-side). No defect.
- `renders/p1_R6_default_phone.png` (R6): phone (390px). Both tables render; default orders correct; judge + the
  un-analyzed Analyze controls visible; wide tables scroll horizontally (pre-existing `pm-table-scroll`, not new). No defect.

### Phase 2 — /farm/mlb/splits?view=grid (2 live + 3 non-live)
- `renders/p2_R7_grid_1280.png` (R7): the 2 live whales (Kingfish, domer-1848) show the green `th.live` highlight;
  hover titles carry the accounts ("LIVE on Jack", "LIVE on Jack, Karen"); per-whale W-L = 40–20·67%, 30–25·55%,
  **6–4·60%·thin**, 38–42·48%, **—** (zero closed); grid cells unchanged; the money strip names the 2 copied whales. No defect.
- `renders/p2_R8_grid_phone.png` (R8): phone. Summary + controls stack; grid scrolls horizontally (as designed);
  the live highlight is still visible; heatmap not shown (grid view). No defect.

### Phase 3 — /live/kalshi_jack/{nfl,atp,mlb}
- `renders/p3_R9_nfl_active_default.png` (R9): flat Active table, **all columns** — Placed ET · Game (matchup+date) ·
  Bet (shorthand + matchup secondary) · Contracts · Fill · Cost · Value (with mark-age chips incl. amber "3m ago·stale")
  · Status · Whale · Order; one row reads **"no mark"** (KC@LV). Default = **event date asc** (SEP14, SEP14, SEP21, OCT05). No defect.
- `renders/p3_R10_nfl_active_cost_desc.png` (R10): Active by Cost desc ($4.00, $3.00, $2.50, $2.00). No defect.
- `renders/p3_R11_nfl_complete_default.png` (R11): flat Complete table with a **Settled ET** column + **Realized**
  (net of fees); all four statuses present — **WON** (+$8.40), **LOST** (−$5.20), **OPPOSED** ("not booked"),
  **EXITED** (—). Default = **settle date newest-first** (09-24, 09-23, then the un-settled opposed/exited last). No defect.
- `renders/p3_R12_nfl_complete_realized_asc.png` (R12): Complete by Realized asc (−5.20, +8.40, then None last). No defect.
- `renders/p3_R13_atp_drawer.png` (R13): ATP page, trade drawer **expanded**. Positions Bet cell = **"ATP"** (floored,
  not KXATPMATCH); drawer **Type = "ATP"**, **Market = "ATP"** (both floored). The raw ticker shows only in the
  drawer's labeled "Ticker" field. No series tag in Type or Market. No defect.
- `renders/p3_R14_mlb_cards_regression.png` (R14): MLB page renders the **game card** (diamond, PREVIEW SD @ CIN, bet
  slots incl. TOT +8.5 $3.60 FROM Alpha) — unchanged; MLB stays cards (out of scope). Regression check PASS.
- `renders/p3_R15_nfl_complete_phone.png` (R15): phone Complete tab. Money strip + roster stack (Deploy-12 phone
  treatment); the Complete table wraps readably; all four statuses + settle-desc order visible. No defect.

---

## DEPLOY SHAPE (per phase — Board action; NOT run here)
Deploys go **1 → 2 → 3**, each fast-forwarding prod-live before the next ships (a phase never depends on a later one).
Per phase, pm_web-only: (1) re-verify box==prod-live + baseline (differential empty) + engine/pm_web PID; (2) the
phase branch is already a real commit; (3) render vs a RO box-DB snapshot; (4) staged scp+tar plain diff, drift-gated
(box BEFORE == the table's "before" CR-sha16 above), **backup-is-a-gate**, post-write CR-sha == "after" + `py_compile`
+ import gate; **rollback rm's any file this phase CREATED** (none — all edits are to existing files); (5) **one**
`systemctl restart prediction-markets-web` under the standing pm_web restart authority (verify MainPID + ActiveEnter,
NOT az exit); **engine `trading-corp` PID + NRestarts unchanged before/after every step**; (6) fast-forward
`origin/prod-live` to the phase tip (FF-only; non-FF → STOP) + tag; re-verify box==prod-live.

**Fast-forward commands (Board, after each phase's deploy is proven):**
- Phase 1: `git push origin pm-tables-p1-2026-09-25:prod-live` + tag `pm-tables-p1-deploy-2026-09-25`
- Phase 2: `git push origin pm-tables-p2-2026-09-25:prod-live` + tag `pm-tables-p2-deploy-2026-09-25`
- Phase 3: `git push origin pm-tables-p3-2026-09-25:prod-live` + tag `pm-tables-p3-deploy-2026-09-25`

RO runners used (in `C:\Users\AA Incorporado\cc`): `pm_roster_boxshas_ro`, `pm_tables_step0_status_ro`,
`pm_p1_tieout_ro`, `pm_p2_tieout_ro`, `pm_p3_tieout_ro`.

---

## DEPLOY 17 (2026-09-25) — PHASE 1 LIVE ON PROD-LIVE

Board-authorized. pm_web-only, engine untouched. Deploy target **`6eb797d0`** (branch `pm-tables-p1-2026-09-25`,
off prod-live `558fc143`). Shipped exactly the 5 Phase-1 files (no new file, no CSS, no cache-bust, no
subdivision.py/db.py/migration/engine). `pm_sort.js` left in place (still served, unreferenced by the two tables).

**Pre-deploy (1-4):** prod-live still `558fc143`; **box == prod-live 48/48** (CR-stripped sha16). Engine `trading-corp`
MainPID **534581** / NRestarts **0**; pm_web `prediction-markets-web` MainPID **523799** / NRestarts 0; schema head 24.
No `arm` table / armed column (pm_web read-only → arm state structurally immune); heartbeat tables engine-owned (immune
to a pm_web restart). `/farm/mlb` before: 200, pinned=0, candidate=0, KX=0, pm_desk.css sha `0b50095b62c5e7b1`;
watchlist(34) + prospects(48) default order recorded.

**Backup gate (3):** `/home/azureuser/pm_d17_backup_20260925T201753Z` — all 5 files, each backup sha == box
(bcece565 / e3df1e2b / c784369f / 1f0d66b7 / 678c9bf5). No file created → no rm-on-rollback.

**Graft (5):** staged git-archive of `6eb797d0`; drift-gate box==prod-live BEFORE, applied, **VERIFY AFTER == target** —
app.py `bcece565→f1dce3a9`, live_view.py `e3df1e2b→60106828`, pm_prospects_rows.html `c784369f→89ebed73`,
pm_watchlist_rows.html `1f0d66b7→a94440f4`, pm_farm_category.html `678c9bf5→fbfd3790`. py_compile OK. **Import gate**
(service venv, fresh process): IMPORT_OK routes=25 post_routes=8, analyze+farm routes present, **forbidden_modules=[]**.
Diff: no new POST route, no forbidden imports.

**Restart (6):** `az … systemctl restart prediction-markets-web` (pm_web ONLY). pm_web MainPID **523799 → 569065**,
ActiveEnter 2026-09-25 20:21:57 UTC, active/running. Engine `trading-corp` **534581 / NRestarts 0 UNCHANGED**.

**Post-deploy (7-13):** `/farm/mlb`, nfl, nba, splits, /live, /live detail (both tabs), /, all 4 account pages → **200
+ styled**. Watchlist + Prospects **default order UNCHANGED** vs before (34==34, 48==48). URL sort: `?wsort=roi&wdir=desc`
+ `?wsort=netpnl&wdir=asc` reorder (and differ), nfl watchlist sorts too; `?psort=n&pdir=desc` reorders; sort links
present. (Column key is `netpnl`; the header link emits `?wsort=netpnl` — the brief's "net_pnl" is the display name.)
Analyze: unscored **Analyze**; scored **View result** + **Re-analyze** + **scored Nd ago**. Vocab pinned=0, candidate=0,
KX=0. Static assets all **200** (incl `pm_sort.js`); pm_desk.css sha `0b50095b62c5e7b1` unchanged. **journalctl -p err
since restart: pm_web 0, engine 0.** Engine 534581/0 unchanged throughout; order_count 2683 (engine trades on — pm_web
cannot write orders).

**Wrap (14-15):** FF prod-live `558fc143 → 6eb797d0` (FF valid); tag `pm-tables-deploy17-p1-2026-09-25`; **box ==
prod-live 48/48** re-verified at 6eb797d0. Rebase p2/p3 = **no-op** (already stacked on 6eb797d0); p2 full suite
**592 passed / 24 pre-existing**. No rollback condition at any step. Runners: `pm_d17_predeploy_ro`, `pm_d17_graft`,
`pm_d17_importgate_ro`, `pm_d17_restart`, `pm_d17_postrestart_ro`, `pm_d17_postdeploy_ro`.

**STOP — Phase 1 LIVE. Awaiting "deploy phase 2".**

---

## DEPLOY 18 (2026-09-25) — PHASE 2 LIVE ON PROD-LIVE

Board-authorized. pm_web-only, engine untouched. Deploy target **`a5db434b`** (branch `pm-tables-p2-2026-09-25`,
off prod-live `507053f6`). Shipped exactly the 3 Phase-2 files (app.py, live_view.py, pm_farm_splits.html); no new
file, no CSS, no cache-bust, no subdivision.py/db.py/migration/engine.

**Pre-deploy (1-4):** prod-live still `507053f6`; **box == prod-live 48/48**. Engine `trading-corp` MainPID **534581**
/ NRestarts **0**; pm_web `prediction-markets-web` MainPID **569065** / NRestarts 0; schema head 24. Before-state for
mlb (the live-attached category): **4 live-copied whales** (0x2dc13c, 0x684baa, 0x5674f6, 0x41b4cd — all 4 accounts)
via pm_subdivision_attachment active=1; each pinned whale's farm_rows W-L recorded; splits `?view=grid` 200, th.live=0
(pre-Phase-2), pinned=0, candidate=0, KX=0, grid tbody sha `3c0f2c83fa0f4e64`, css `0b50095b62c5e7b1`.

**Backup gate (3):** `/home/azureuser/pm_d18_backup_20260925T205926Z` — all 3 files, each backup sha == box
(f1dce3a9 / 60106828 / f2a756ad). No file created.

**Graft (5):** staged git-archive of `a5db434b`; drift-gate box==prod-live BEFORE, applied, **VERIFY AFTER == target**
— app.py `f1dce3a9→6f46fea6`, live_view.py `60106828→8c826501`, pm_farm_splits.html `f2a756ad→7478094c`. py_compile OK.
**Import gate** (service venv): IMPORT_OK routes=25 post_routes=8, **forbidden_modules=[]**. Diff: no new POST route.

**Restart (6):** `az … systemctl restart prediction-markets-web` (pm_web ONLY). pm_web MainPID **569065 → 569992**,
ActiveEnter 2026-09-25 21:00:09 UTC, active/running. Engine `trading-corp` **534581 / NRestarts 0 UNCHANGED**.

**Post-deploy (7-13):** splits `?view=grid` 200 — the **1 live whale that is currently a grid column (0x684baa)** is
highlighted (`th.live`) with **"LIVE on Jack, Karen, Marc, Trey"** on hover; the other 3 live wallets have **0 open mlb
positions** (verified) so they are not grid columns and correctly not highlighted; no non-live header highlighted →
th.live count (1) == live-wallets-that-are-columns (1). Header **W-L == farm_rows** (0x684baa 65–47·58%; SDTrading
172–143·55%; THIN only under 50 closed). **Grid cells unchanged by the deploy** — the local template diff shows only the
`<th>` header cell + 3 CSS rules changed (the split_row/tbody body is byte-identical); the tbody sha moved
`3c0f2c83→96efd60b` purely from the **paper poller updating open positions** (max last_observed_ts 4.4 min old; two
consecutive reads now stable at `96efd60b`). `?mode=trusted` / `?view=splits` / `?view=heatmap` render; nfl grid 200;
atp shows **"NO STRUCTURAL DECODE"**. Vocab pinned=0, candidate=0, KX=0. **Phase 1 intact**: `/farm/mlb?wsort=roi&wdir=desc`
reorders; default order unchanged; Analyze states (43 Analyze / 39 View result / 39 Re-analyze). `/live` both tabs,
/live detail, /, account pages all **200**. Static assets all **200**; pm_desk.css sha `0b50095b62c5e7b1` unchanged.
**journalctl -p err since restart: pm_web 0, engine 0.** Engine 534581/0 unchanged; order_count 2683 → **2686** (3
attributable engine fills — pm_web cannot write orders).

**Wrap (14-15):** FF prod-live `507053f6 → a5db434b` (FF valid); tag `pm-tables-deploy18-p2-2026-09-25`; **box ==
prod-live 48/48** re-verified at a5db434b. Rebase p3 onto the new tip = **no-op** (p3 already contains a5db434b — D17's
parallel rebases produced the identical p2 commit); p3 full suite **604 passed / 24 pre-existing**. No rollback
condition. Runners: `pm_d18_predeploy_ro`, `pm_d18_graft`, `pm_d17_importgate_ro`, `pm_d17_restart`,
`pm_d17_postrestart_ro`, `pm_d18_postdeploy_ro`.

**STOP — Phase 2 LIVE. Awaiting "deploy phase 3".**

---

## DEPLOY 19 (2026-09-25) -- PHASE 3 LIVE ON PROD-LIVE

Board-authorized. pm_web-only, engine untouched. Deploy target **`60bbe750`** (branch `pm-tables-p3-2026-09-25`,
off prod-live `8e450210`). Shipped exactly the 3 Phase-3 files (app.py, live_view.py, pm_live_subdivision.html); no
new file, no CSS, no cache-bust, no subdivision.py/db.py/migration/engine. This is the largest phase (every non-MLB
`/live` detail page), so the post-check is proportionally longer.

**Pre-deploy (1-4):** prod-live `8e450210`; **box == prod-live 48/48**. Engine `trading-corp` MainPID **534581** /
NRestarts **0**; pm_web `prediction-markets-web` MainPID **569992** / NRestarts 0; schema head 24. Journal baselines
captured RO from prod-live DB: **NFL jack** active 4 (order ids 2417/2421/2422/2660), complete 79 (66 settled),
`SUM(realized_pnl over settlement) = 9.9285`; **tennis jack/atp** active 0, complete 30 (21 settled),
`realized = -6.7264`; atp non-structural tickers e.g. `KXATPMATCH-26SEP03ZVEHAL-HAL`. css sha `0b50095b62c5e7b1`.

**Backup gate (3):** `/home/azureuser/pm_d19_backup_20260925T225031Z` -- all 3 files, each backup sha == box before any
write. No file created (all 3 pre-exist), so no rollback-rm path needed.

**Graft (5):** staged git-archive of `60bbe750`; drift-gate box==prod-live BEFORE, applied, **VERIFY AFTER == target**
-- app.py `911d4d17`, live_view.py `005d2cf9`, pm_live_subdivision.html `5f8621a6` (CR-stripped sha16). py_compile OK;
rollback-on-any-fail armed. Diff: query-param reads only (no new POST route, no engine import).

**Restart (6):** `az ... systemctl restart prediction-markets-web` (pm_web ONLY, run under standing pm_web restart
authority; verified by MainPID + ActiveEnter, not the az exit code). pm_web MainPID **569992 -> 571194**, ActiveEnter
2026-09-25 22:51:19 UTC, active/running. Engine `trading-corp` **534581 / NRestarts 0 / boot 2026-09-22 21:11:47 UTC
UNCHANGED**.

**Post-deploy (7-16) -- all served-HTML, waited one poll cycle before judging marks:**

- **NFL Complete:** row count **79 == baseline**; the 79 order ids are **identical** to the baseline set; **realized
  column ties to the journal per row to the cent** -- the rendered column sums to **9.87**, which equals the journal's
  **sum-of-per-row-rounded-to-2dp** (9.87); the unrounded journal total is 9.9285, so the 6-cent visual delta is
  purely `_money` 2dp per-row display rounding (PROVEN by `pm_d19_reconcile_ro`: `sum(round(v,2)) = 9.87`,
  `sum(v) = 9.9285`). Rows ordered **settled newest-first** (`settled_desc_ok`). Statuses: WON / LOST / **OPPOSED ->
  "not booked" (x12)** / **EXITED -> "--" (x1)**. Placed (ET) + Settled (ET) columns present, format `MM-DD HH:MM ET`.
- **NFL Active:** row count **4 == baseline**; order ids **2417/2421/2422/2660 == baseline**; ordered **event date
  ascending** (all 2026-09-27); cost column sum **$21.50**; "no mark" placeholder present (marks per cadence).
- **URL sort:** `?psort=cost&pdir=desc` reorders Active + the header link carries `psort=cost`; `?psort=realized&pdir=asc`
  reorders Complete + keeps the Complete tab. **Positions sort is namespaced `?psort/?pdir`** (distinct from the roster's
  `?sort/?dir`); the brief's `?sort=/&dir=` example is the roster convention -- the positions header links emit `?psort/?pdir`.
- **Tennis jack/atp:** Complete **30 == baseline**, realized **-6.72** (journal -6.7264, 2dp rounding); Active **0 ==
  baseline**; settled newest-first.
- **DRAWER FLOOR (atp):** Type cell KX-series hits **0**, Market field KX hits **0**, floored **`<td>ATP</td>` x33**;
  the raw `KXATPMATCH` series tag appears ONLY in the **33 labelled Ticker provenance fields**. No leak into any
  human-facing Type/Market label.
- **MLB regression:** `/live/kalshi_jack/mlb` keeps **game cards** (`mlb_has_cards`), **no flat positions table**
  (`mlb_has_postbl=false`) -- non-MLB-only ruling (OQ-3) honored; roster intact.
- **Full live sweep:** **90 active sub-divisions x 2 tabs = 180 fetches, ALL 200** (`live_non200=[]`).
- **Phase 1 intact:** `/farm/mlb?wsort=roi&wdir=desc` reorders the watchlist. **Phase 2 intact:** splits `?view=grid`
  `th.live` count **1** (the single live wallet that is a grid column).
- **Vocab:** pinned=0, candidate=0 across live pages. **Static** all 200; **pm_desk.css sha `0b50095b62c5e7b1`
  UNCHANGED** (no CSS shipped); double-escape (`&amp;amp;`) = 0.
- **journalctl -p err since restart: pm_web 0, engine 0**; **0 template/jinja errors** across the 180-page sweep.
- **Engine 534581 / NRestarts 0 / boot 2026-09-22 21:11:47 UTC UNCHANGED** throughout.

**Wrap (17-18):** FF prod-live `8e450210 -> 60bbe750` (FF valid); tag `pm-tables-deploy19-p3-2026-09-25`; **box ==
prod-live 0 mismatches** re-verified at 60bbe750 (the 3 P3 files match their AFTER shas; engine-shared
subdivision.py/db.py/scoring.py/sizing.py and the Phase-1/2 files unchanged). **Three-phase table workstream CLOSED
(Deploys 17-18-19 all live).** No rollback condition at any step.

**Backlog (filed at wrap):**
- Tennis/UFC/fed have no structural player-code -> full-name map, so the drawer/floor shows the category floor
  ("ATP"/"TENNIS") rather than a human matchup. Add a data-only player-code map (like `sports_structural_match`) to
  give these categories a real bet label.
- The roster sort (`?sort/?dir`) and the positions sort (`?psort/?pdir`) share the `/live/{acct}/{cat}` URL but do not
  compose: re-sorting the roster does not carry the positions sort, and a positions header link does not carry the
  roster sort -- each resets the other to its default. Minor; both work independently.

Runners: `pm_d19_predeploy_ro`, `pm_d19_graft`, `pm_d19_post_a_ro`, `pm_d19_post_b_ro`, `pm_d19_reconcile_ro`.

**STOP -- Phase 3 LIVE. Three-phase table workstream complete.**
