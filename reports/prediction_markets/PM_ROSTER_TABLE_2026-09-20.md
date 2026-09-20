# PM /live — SUB-DIVISION ROSTER TABLE + MONEY-STRIP RE-LAYOUT (BUILD + DEPLOY 12) — 2026-09-20

**STATUS: DEPLOYED LIVE (DEPLOY 12) 2026-09-20. prod-live `e9769aa9 -> b9e1c215` (FF), tag
`pm-roster-table-deploy12-2026-09-20`. pm_web-only; ONE pm_web restart (467489 -> 494253); engine `trading-corp`
491380 / NRestarts 0 NEVER touched. box == prod-live 47/47. Full deploy record: §11 below.** (Build status was: BUILT
+ TESTED + RENDERED + BOX-TIED + COMMITTED — preserved below.)
Branch `pm-roster-table-2026-09-20` off `origin/prod-live` @ **`e9769aa9`** (git truth = box), worktree
`C:\Users\AA Incorporado\cc-pm-roster-table-wt`. DEPLOY / PUSH-to-prod-live / RESTART are Jack's reserved actions.

The `/live/{account}/{category}` page is re-laid-out: the money strip moves directly beneath the header chips and
gains a **fifth SIZING cell** (the standalone sizing line disappears); the flat "Copies these whales" roster panel
becomes a **sortable TABLE** (same look + mechanics as the Farm-League watchlist) with an On-roster/All toggle,
server-side column sort, a footer-totals row, a phone stacked-card layout, and the existing drawer drill-through.

--------------------------------------------------------------------------------
## 1. TRUTH + BASELINE

- `origin/prod-live` tip at start = **`e9769aa9`** (the marc+trey 4th-account wire). Branched off it.
- **box == prod-live: 47/47 deploy-surface files byte-identical (CR-stripped)** — verified `cc/pm_roster_boxshas_ro.*`
  (streams an ASCII probe to the box, computes `sha256(content без \r)` for the whole `web/` tree + subdivision.py +
  db.py + sizing.py + scoring.py) vs `git show origin/prod-live:<f> | tr -d '\r' | sha256sum`. Zero mismatches. **No
  reconcile finding.** (The box also carries pre-existing `.bak_*`/`.orig` untracked files — a known cleanup backlog
  item, not drift of a tracked file.)
- **Baseline (the handoff/jacks-log command, measured myself on `.venv-webtest` at the prod-live tip):**
  `python -m pytest tests/prediction_markets -q -p no:pytest_ethereum -p no:cacheprovider --continue-on-collection-errors`
  → **24 FAILED** (test_live_r3 ×14, test_accounts_m2 ×3, test_stage2_nav ×2, test_stage2_phase3 ×2,
  test_ctx_pagination_fix ×1, **test_refresh_stagger_and_lookback ×1, test_rung3_observability ×1**). This is **2 more
  than the handoff's documented "22"** — the extra two are env-gap tests added *after* the handoff (they import engine
  code needing `pykalshi`, absent from the webtest venv), exactly the drift the handoff warned about. All 24 are
  cross-tree / env-gap, none touch this UI; **the differential is the gate, not the absolute count.**

--------------------------------------------------------------------------------
## 2. DISCOVERY (read-only, before coding)

| # | question | finding (file:line) |
|---|---|---|
| **D1** | the watchlist table's template / CSS / sort / ROI(COST) reader to reuse | **Template:** `partials/pm_watchlist_rows.html` — `table.pm-table.pm-farm-table` in `div.pm-table-scroll`; page `pm_farm_category.html`. **CSS (pm.css):** `.pm-table` (65), **sticky `thead th`** (66), `.pm-num`/`.pm-primary`/`.pm-gain`/`.pm-loss` (71-88), `.pm-sortable`/`.pm-sort-asc`/`.pm-sort-desc` arrow glyphs (352-355), `.pm-farm-table` (204). Macros: `whale_label`, `score_badge` (`pm_macros.html`). **Sort:** watchlist/prospects sort is **client-only** `pm_sort.js` (`table.pm-sortable-table` + `th.pm-sortable`, reads `data-sort-value`, **first click = ASCENDING** — the comment lies, per the jacks-log trap); the server sets only the default load order (`app.py:545-547`). **There is no server-side `?sort/?dir` today** → I *built* it (see §Notes). **ROI(COST):** watchlist `r.roi = net_paper_pnl / cost_basis` over CLOSED (`db.py:566-568` `pm_paper_category_stats`; rendered `pm_watchlist_rows.html:37`), `None`→"—". Live analog mirrored = `realized_pnl / booked_cost_usd`. |
| **D2** | `whale_live_records` columns present vs to-add | **`subdivision.whale_live_records` (subdivision.py:433) ALREADY returns:** wallet, user_name, active, added_ts, removed_ts, `placed`(Copies), `booked_closes`(Booked), `settled_w`(W), `settled_l`(L), `unbooked_closes`, `realized_pnl`(Realized$), `realized_today`(Today$), `n_open`/`open_cost_usd`/`open_value`/`n_priced`/`n_total`(Open), `thin`. The loader attaches `score` (Farm verdict via `_score_cell`→`score_badge`). **ADDED:** **Cost$ of booked copies** — new `subdivision.booked_cost_by_whale` (additive); **ROI%**, **Win%**, **tenure days** — computed in new pure `live_view.build_roster_table`. |
| **D3** | the exact money-strip keys (footer tie-out) | **`live_view` summary** — non-MLB `_journal_summary` (544-565): `n_open_positions`, `unsettled_cost`, `unsettled_value`/`unsettled_priced`/`unsettled_total`, `realized_today`, `settled_today`, `has_game_feed`; MLB card path (807-812) same keys. Footer sums per-whale `n_open`/`open_cost_usd`/`open_value`/`realized_today` → tie to `n_open_positions`/`unsettled_cost`/`unsettled_value`/`realized_today`. **Caveat (subdivision.py:285-287):** per-whale sums equal the per-ticker strip *except* same-ticker multi-whale stacking / opposing-leg pairs — tested on a single-whale-per-ticker fixture and confirmed exact on the box. |

--------------------------------------------------------------------------------
## 3. WHAT CHANGED

**Re-layout (`pm_live_subdivision.html`, +15/-9).** Header chips (arm + liveness) stay. Directly beneath, the money
strip now carries **five cells**: Positions/Games held · Unsettled at cost · Unsettled current value · Realized today
· **Sizing** (moved in from the standalone line). The **roster table** sits directly below the strip; the game cards
/ positions table / trade drawer follow exactly as before.

**Sizing 5th cell (`pm_sizing_control.html`).** Repurposed from a standalone `.sizectl` row into a strip `.cell` —
current contracts/copy, "set by `<who>` · `<age>`", and the JS-off-safe change form (owner-or-admin). The strings the
sizing tests lock ("Sizing", "N contract(s) / copy", "set by …") are preserved; the routes/authz are untouched.

**Roster TABLE (`pm_whale_roster.html`, replaces the panel body).** One row per whale reusing the watchlist CSS
family (sticky header): **Whale · Farm verdict · Tenure · Copies · Booked · W · L · Win% · Realized $ · Cost $ · ROI% ·
Unbooked · Open · Today $ · Actions.**
- **Whale** = display name else right-truncated wallet (full wallet on hover); the cell is a filter button + the whole
  row filters the drawer (rule 5).
- **Farm verdict** = the compact `score_badge` already rendered (PROMOTE / PASS / INSUF DATA + its loss-omission
  figure) — one badge, no second line.
- **Tenure** = "attached `<date>` · N days" (on-roster, labelled *current span*) / "`<start>` – `<end>`" (formerly-live).
  The cell wraps a `.rt-spans` list so migration 023's prior spans drop in as extra `<span>` with no re-layout — no
  history is invented.
- **Copies/Booked/W/L/Win%/Realized/Cost/ROI/Unbooked/Open/Today** per D1/D2. **THIN** badge beside Booked under 50;
  **zero-copy → "—"**, never "0%"; **ROI zero-cost → "—"**; Unbooked carries the "no settlement P&L, by design" hover;
  Open = "n · $cost · $value (N of M priced)" / "no mark" / "—".
- **Actions:** on-roster → profile · **Detach** (existing route, unchanged); formerly-live → profile only.
- **Toggle** "On roster (default) / All" — `?whales=all` appends formerly-live rows **beneath**, dimmed + tagged
  "formerly live", **server-rendered** (JS-off safe).
- **Sort** — every numeric column + Whale, default **Realized $ desc**, `?sort=<col>&dir=<asc|desc>` **server-side**
  (JS-off safe), the active header carrying the watchlist up/down arrow. None sorts last.
- **Footer totals** — Copies·Booked·W·L·Realized·Cost·Unbooked·Open over the shown rows (win%/ROI recomputed from the
  sums); ties to the money strip (§6).
- **Phone** — the table collapses to a stacked card per whale (name, verdict, tenure, Booked/W/L/Win%/Realized/ROI,
  Open, Actions); no sideways scroll. (Booked is kept on phone so the **THIN** honesty badge is never hidden — a
  deliberate, minimal deviation from the brief's card list to satisfy the non-negotiable THIN rule.)
- **Drawer drill-through** — binds on `DOMContentLoaded`, highlights the row, writes "filtered to `<whale>`" into the
  drawer heading, and reveals a "show all whales" clear control; JS-off shows every row.

**Readers.** `subdivision.booked_cost_by_whale` (ADDITIVE-ONLY) + `live_view.build_roster_table` (pure) — see §5.

--------------------------------------------------------------------------------
## 4. FILE DIFF vs prod-live (CR-stripped sha16 BEFORE → AFTER)

**★ app.py IS touched** (loader wiring + the route reading `?whales/?sort/?dir` — a read-only page, no new POST/order
path). **★ subdivision.py IS touched but ADDITIVE-ONLY** (one new function, `git diff` = **+23 / −0**, no existing
function changed) — the engine loads a behavior-identical file on its own next restart, exactly as Deploys 9/11 did.

| file | BEFORE (box == prod-live) | AFTER | note |
|---|---|---|---|
| `subdivision.py` | `83e3893079a40625` | `5b6f42a6ed12f4fc` | **engine-shared, ADDITIVE-ONLY** (+ `booked_cost_by_whale`) |
| `web/app.py` | `5303952cc138783c` | `0ef64011d2c223a2` | loader + route `?whales/?sort/?dir` (no order path) |
| `web/live_view.py` | `05691d237eac3884` | `233ed9a28fd325f4` | + `build_roster_table` (pure, pm-side) |
| `web/static/pm_desk.css` | `2a290250c04c41b5` | `b07fce488bb0634c` | roster table + sizing cell + phone media query |
| `web/templates/partials/pm_sizing_control.html` | `e96a94fc374931a2` | `3e1fe122f7145d85` | strip `.cell` (was standalone line) |
| `web/templates/partials/pm_whale_roster.html` | `849c6ca2dedf30fe` | `e496d37b412fc1ee` | the roster TABLE + drill-through JS |
| `web/templates/pm_live_subdivision.html` | `79e2c7f4bc6ea6be` | `86b22d9947e6ced9` | strip re-layout + roster below |
| `web/templates/pm_shell.html` | `2d48f5dcbf2e4504` | `ec3a5519f3c0991e` | `pm_desk.css?v` `2a290250`→`b07fce48` |

Git-only (NOT deployed to the box): `tests/prediction_markets/test_roster_table.py` (new, 17 tests),
`tests/prediction_markets/test_whale_roster.py` (render test updated for the new toggle default), this report.

Commits on `pm-roster-table-2026-09-20`: `71eb745d` (readers + tests), `6dc90e7f` (UI re-layout + wiring),
+ this report.

--------------------------------------------------------------------------------
## 5. READERS (the only logic added)

- **`subdivision.booked_cost_by_whale(conn, account, category) -> {wallet: cost}`** (ADDITIVE-ONLY). The ROI(COST)
  denominator, derived from the **settlement row itself**: `settlement.py` books `realized_pnl = proceeds -
  cost_basis_open`, and the close row stores `fill_count` = net-open settled and `fill_price` = the settled
  per-contract value, so `cost_basis = fill_count*fill_price - realized_pnl` per row — invertible, and *by
  construction self-consistent* with the realized the roster already shows (ROI = realized ÷ cost ties out
  arithmetically). **Settlements only** (`close_source='settlement'`, `realized_pnl NOT NULL`) — the same set that
  defines Booked / W-L; opposed + void never enter the denominator. Read-only, journal-only; mirrors
  `_realized_today_by_whale`.
- **`live_view.build_roster_table(whale_records, booked_cost, *, now_ts, sort, direction, show_all, thin_floor)`**
  (pure, pm-side). Enriches each record with `win_pct` (settled_w/booked, None if 0), `roi_cost`
  (realized/booked_cost, None if cost ≤ 0), `tenure_days`, `booked_cost_usd`; keeps on-roster (default) or appends
  formerly-live beneath (each group sorted independently); sorts server-side (default `realized` desc, None last,
  whale alphabetical); returns `rows` + `totals` (footer) + toggle/sort state. No DB, no network → unit-tested.

--------------------------------------------------------------------------------
## 6. VERIFICATION

- **Tests (`.venv-webtest`, same command both sides):** full-suite differential = **24 FAILED after == 24 baseline,
  byte-identical set — 0 new failures.** The new/affected files: **70 tests pass** (test_roster_table.py 17 +
  test_whale_roster.py 20 + test_sizing.py 33). New tests cover: booked-cost won/lost/void exact, opposed/void
  excluded; derived columns + edges (zero copies, all-unbooked, ROI zero-cost → None); toggle (formerly hidden
  default / beneath under All); sort (default realized desc, roi/None-last both dirs, whale alpha, invalid→realized);
  footer totals + ratios; **footer↔strip tie-out**; **THIN boundary 49/50**; route `?whales/?sort/?dir`.
- **Renders (`cc/pm_roster_table_render.py`, viewed):** `cc/renders_roster_table/` —
  `mlb_default_1600` / `mlb_default_1280` (5-cell strip incl. Sizing; roster default = on-roster, sorted Realized
  desc; PROMOTE/INSUF-DATA/PASS/not-analyzed verdicts; THIN conditional [SDTrading 55 booked = no THIN, others THIN]);
  `mlb_all_1600` (formerly-live dimmed + "formerly live" tag beneath, profile-only, footer 7-whale totals);
  `mlb_all_roi_1600` (sorted by ROI); `mlb_phone` (stacked card per whale, no sideways scroll); `mlb_all_filtered_1600`
  (whale row highlighted + drawer opened + filtered); `atp_all_1280` + `atp_phone` (the **shared template on a
  non-MLB page** — proves one template).
- **Money-strip tie-out (visual, atp render + unit test):** the ATP page strip (Positions held **2** · at-cost
  **$2.30** · current **$1.56** · **1 of 2 priced**) equals the footer (**2 · $2.30 · $1.56 · 1 of 2 priced**)
  exactly.
- **Box RO tie-out (`cc/pm_roster_boxtie_ro.*`, my reader vs the LIVE pm DB, `mode=ro`) — `cc/renders_roster_table/box_tie_out.txt`:**
  ran the roster reader + the inlined booked-cost query against the real box for **jack/mlb** (4 on-roster + 3
  formerly-live) and **karen/mlb** (4 on + 2 formerly). Per-whale figures are the current panel's numbers (unchanged
  `whale_live_records`) and the new ROI/cost columns compute sane values, e.g. jack/mlb `0x684baa57` 81 booked /
  47-34 / +$15.44 real / $259.56 cost / **+5.9% ROI**; `xifutloong3` +26.3%; the just-attached `0x41b4cd88` (0
  booked, 2 opens) → **win/ROI "--"** (edge case correct). **Footer → strip TIE = OK on all three mark-independent
  figures** (open_n 3==3, open_cost 13.40==13.40 jack / 10.72==10.72 karen, realized_today 0==0) → no stacking
  divergence currently. (The mark-*dependent* current-value tie is proven off-box — unit test + atp render — because
  the RO probe has no poller-cache marks.)

--------------------------------------------------------------------------------
## 7. MIGRATION

**NONE.** No schema change. ★ **Finding (verified against the box, not the brief):** the brief says "schema head is 22,
023 is reserved for the engine agent's attachment-history work." Against `origin/prod-live`/the box, **migration 023
already landed** as `pm_whale_score` (the 2026-09-13 Analyze-Upgrade deploy — the reader behind this table's Farm-verdict
column), so the live head is **23** and the **next number is 024**; the attach/detach event-log the brief refers to would
take 024, not 023. I created **no** migration this pass either way, and the roster-table cell is already structured so
that event-log's prior spans render as a list when it lands.

--------------------------------------------------------------------------------
## 8. DEPLOY SHAPE (for Jack — reserved)

**pm_web-only. Engine `trading-corp` NEVER restarted / NEVER behavior-changed.** No migration → no pre-restart migration
step.
- **Model:** branch off `origin/prod-live` = box = truth, edit directly (no capture/graft). 8 deploy files (§4).
- **subdivision.py is engine-shared but ADDITIVE-ONLY** (+23/−0, one new function). The running engine keeps its
  in-memory copy at deploy time and loads a behavior-identical file on its OWN next restart — do **not** restart the
  engine for this. app.py IS touched (loader + route); it carries no order path (read-only page).
- **Deploy:** plain scp+tar diff of the 8 files onto prod-live, drift-gated (box == BEFORE for all 8, CR-sha16),
  **backup-is-a-gate** (verify the backup dir + every file sha before any write; fail-closed if missing), post-write
  CR-sha16 == AFTER + `py_compile` + import/standalone gate, roll back all on any mismatch.
- **ONE `az vm run-command … 'systemctl restart prediction-markets-web'`** (a ~2s UI blip). Confirm the restart by
  **MainPID change + ActiveEnterTimestamp** (never the az exit code / empty stdout). **Verify `trading-corp` PID +
  NRestarts UNCHANGED before and after every step.**
- **Cache-bust:** `pm_desk.css?v=b07fce48` (the file's CR-stripped sha8) — verify the served file sha8 + the shell
  `?v=` match after deploy.
- **prod-live advance (SAME session, after post-check green):** fast-forward `origin/prod-live` to the deployed commit
  + tag `pm-roster-table-deploy-2026-09-20`; re-verify box == prod-live. FF-only; non-FF → STOP. main untouched.

**Report the FF push command with the deploy** (do NOT defer): after the box post-check is green and the tip is the
deployed commit, `git push origin pm-roster-table-2026-09-20:prod-live` (FF-only) + `git push origin <tag>`.

--------------------------------------------------------------------------------
## 9. NOTES / DECISIONS

- **Server-side sort (built, not inherited).** The watchlist/prospects sort is client-only (`pm_sort.js`,
  ascending-first — the jacks-log trap). Rule 4 requires **JS-off-safe URL sort**, so the roster's headers are
  server-side `?sort=&dir=` links (strictly better for JS-off); I did **not** wire `pm_sort.js` onto this table (it
  isn't even loaded on `/live`) to avoid a double-handler / ascending-first conflict. The active header still shows
  the watchlist up/down arrow.
- **Drawer drill-through bind timing (bug caught by the render).** The roster's inline `<script>` runs at parse time,
  *before* the drawer (`details.drawer`) exists later in the DOM — so the old `querySelector('details.drawer')`
  returned null and silently no-op'd (indistinguishable from JS-off). Fixed by deferring the bind to
  `DOMContentLoaded`.
- **Phone keeps Booked.** The brief's phone card omits Booked; I keep it so the **THIN** badge (a non-negotiable
  honesty rule) is never hidden — a minimal, documented deviation.
- **MLB strip vs footer.** For a category with a game feed (MLB) the strip's open figures come via the card path
  (feed-dependent); the render seeds no feed, so the MLB strip shows $0 while the footer shows the raw-journal opens —
  an artifact of the feed-less render, not a defect. The tie-out is proven on the journal path (atp render + unit
  test) and on the box (mark-independent figures, both accounts).
- **Numbers never depend on the mark cache except Open current value** (rule 8): win%/ROI/realized/today/cost are all
  journal-derived; a failed poll leaves the table intact with the Open value cell showing "no mark".

--------------------------------------------------------------------------------
## 10. RUNNERS (cc/, read-only)
`pm_roster_boxshas_ro.{ps1,py}` (box == prod-live 47/47), `pm_roster_boxtie_ro.{ps1,py}` (RO reader tie-out on the
live box DB → `renders_roster_table/box_tie_out.txt`), `pm_roster_table_render.py` (the 8 render PNGs +
`box_tie_out.txt` in `cc/renders_roster_table/`). Deploy 12 runners: `pm_deploy12_precheck_ro`, `pm_deploy12_graft`,
`pm_deploy12_restart_az` (az-root, Jack-run), `pm_deploy12_postrestart_ro`, `pm_deploy12_postcheck_ro`,
`pm_deploy12_journal_ro`, `pm_deploy12_fetch_served` + `pm_deploy12_phone_render.py`.

--------------------------------------------------------------------------------
## 11. DEPLOY 12 — LIVE ON PROD 2026-09-20 (board-authorized)

**SHIPPED `b9e1c215` (branch pm-roster-table-2026-09-20) off prod-live `e9769aa9`. prod-live FF `e9769aa9 ->
b9e1c215`, tag `pm-roster-table-deploy12-2026-09-20`. pm_web-only (8 files, incl. the engine-shared additive
subdivision.py); ONE pm_web restart; engine `trading-corp` NEVER touched. box == prod-live 47/47 before AND after.**

**Step 1-5 PRE-DEPLOY (RO, all green — `pm_deploy12_precheck_ro`, `pm_roster_boxshas_ro`):**
- Engine `trading-corp` MainPID **491380** NRestarts 0 active (boot 2026-09-20 15:54:57Z); pm_web **467489** NRestarts 0;
  **schema_head = 24** (NOT the brief's 23 — migration 024 = `pm_subdivision_attachment_event` attachment-span-history
  ALREADY landed by the engine agent; I ship no db.py, so head is recorded, not touched); heartbeats fresh (task
  11-16s, cat 1-3s); pm_live arm rows 69.
- prod-live tip still `e9769aa9`; box == prod-live **47/47** (CR-stripped); FF-able. No reconcile finding.
- **★ COST-BASIS GATE (Jack's ruling, ROI settled-only): PASS to the cent** for every whale on jack/mlb + karen/mlb —
  the close-derived `booked_cost` (fill_count*fill_price - realized_pnl) equals the entry-side sum (Σ entry
  fill_count*fill_price + fee over copies with a booked settlement). No partial-exit divergence (whale-exits fully
  close; they never leave a partial-then-settle position).
- BEFORE figures — jack/mlb: 4 on-roster + 3 formerly-live, footer copies 163 / unbooked 13 / realized 24.27 / open_n
  2 / open_cost 8.00; strip positions_held 2 / unsettled_cost 8.00 / realized_today 4.51. karen/mlb: 4 on + 2 former,
  footer copies 119 / open_n 2 / open_cost 6.40; strip 2 / 6.40 / today 3.61. Served pm_desk.css sha `2a290250`.

**Step 4+6 BACKUP + GRAFT (`pm_deploy12_graft`, scp+tar):** GATE 1 staged CR-sha == TARGET (8/8); GATE 2 box CR-sha
== BEFORE (8/8, no drift); **GATE 3 BACKUP IS A GATE** = `/home/azureuser/pm_deploy12_backup_20260920T203451Z`
(8 files backed up + each sha verified == box before any write); APPLY (CR-stripped); VERIFY box CR-sha == TARGET
(8/8); py_compile OK; import/standalone gate **routes=24, /pm/arm=0, detach=2, forbidden modules=[]**. Before -> After
CR-sha16:

| file | BEFORE | AFTER |
|---|---|---|
| subdivision.py (engine-shared, ADDITIVE +23/-0) | 83e3893079a40625 | 5b6f42a6ed12f4fc |
| web/app.py | 5303952cc138783c | 0ef64011d2c223a2 |
| web/live_view.py | 05691d237eac3884 | 233ed9a28fd325f4 |
| web/static/pm_desk.css | 2a290250c04c41b5 | b07fce488bb0634c |
| web/templates/partials/pm_sizing_control.html | e96a94fc374931a2 | 3e1fe122f7145d85 |
| web/templates/partials/pm_whale_roster.html | 849c6ca2dedf30fe | e496d37b412fc1ee |
| web/templates/pm_live_subdivision.html | 79e2c7f4bc6ea6be | 86b22d9947e6ced9 |
| web/templates/pm_shell.html | 2d48f5dcbf2e4504 | ec3a5519f3c0991e |

**Step 7 RESTART (`pm_deploy12_restart_az`, az-root, board-authorized):** `az vm run-command ... 'systemctl restart
prediction-markets-web'`. az returned Provisioning-succeeded with EMPTY stdout (ambiguous — NOT the confirmation).
Confirmed by PID + timestamp (`pm_deploy12_postrestart_ro`): pm_web **467489 -> 494253**, ActiveEnter **2026-09-20
20:37:44Z**, active. **Engine `trading-corp` 491380 / NRestarts 0 UNCHANGED.**

**Step 8-15 POST-DEPLOY (RO, `pm_deploy12_postcheck_ro` = 72 OK / 0 FAIL):**
- 5-cell strip (4 money + **Sizing** cell; standalone `sizectl` line gone) then the roster table, on jack/mlb,
  karen/mlb, and jack/cfb (shared template). Every column present; default sorted Realized $ desc; THIN badges;
  zero-copy `—`; Detach on on-roster; profile on all; `data-whale`/`data-wallet`/`rt-clear` present; toggle On
  roster/All; **0 raw tickers in the roster region; 0 double-escaped entities.**
- `?whales=all` appends dimmed formerly-live ("formerly live" tag, `rt-formerly`); `?sort=roi&dir=desc` reorders.
- **Step 10 TIE-OUT (after):** per-whale figures == BEFORE (no fills in-window); footer open_n/open_cost tie to the
  strip (jack 2/8.00, karen 2/6.40); **COST-BASIS GATE re-confirmed PASS**; Cost $ == the step-3 entry-cost sums.
- Gating: karen own 200 / karen->jack 403 / no-identity 403; sizing n=0 -> 400, n=51 -> 400 (GET-only, no POST).
- All pages 200 (`/`, `/live`, both account pages) + **all 26 `/farm/{category}` 200** + all static assets 200
  (0 404s — no rollback condition). **Served pm_desk.css sha8 == `b07fce48`; shell `?v=b07fce48`.**
- **Step 15:** pm_web `journalctl -p err` since the restart = **No entries (0)**; engine `journalctl -p err` = No
  entries; engine **491380 / NRestarts 0 active — UNCHANGED** across every step.
- **Step 11 drawer filter:** `data-wallet` + `rt-clear` present in the served HTML; the click-to-filter + "filtered to
  <whale>" + clear behaviour is render-proven (`renders_roster_table/mlb_all_filtered_1600.png`, identical template).
- **Step 12 phone (served page, `pm_deploy12_phone_render.py`):** the **roster table collapses to stacked cards with
  NO overflow** (`.roster-table` right-edge <= viewport). ★ HONEST FINDING: the PAGE as a whole shows a pre-existing
  895px horizontal overflow from the SHELL HEADER nav + poll/arm chips + the live MLB game-card scoreboard (all
  unchanged by this deploy — I only bumped the shell `?v=`). NOT a Deploy-12 regression and NOT a rollback condition;
  the roster deliverable meets "no sideways scroll". Filed as a pre-existing shell/game-card phone-overflow item.

**Step 16 WRAP:** FF `origin/prod-live e9769aa9 -> b9e1c215`; tag `pm-roster-table-deploy12-2026-09-20`; box ==
prod-live **47/47** re-verified at b9e1c215. main untouched.

**NOT EXERCISED ON PROD:** no Detach POST and no sizing POST were run during the deploy (GET confirms only) — the
first real Detach / sizing change from the new table is Jack's, as before.
