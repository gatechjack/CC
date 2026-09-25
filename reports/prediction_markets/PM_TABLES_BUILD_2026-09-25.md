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

**STOP — no deploy, no restart. Awaiting "deploy phase 1".**
