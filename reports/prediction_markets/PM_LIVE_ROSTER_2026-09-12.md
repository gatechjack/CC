# PM /live — PER-WHALE LIVE RECORD + DETACH (BUILD + STAGED DEPLOY) — 2026-09-12

**STATUS: BUILT + TESTED + RENDERED + BOX-TIED (read-only) + COMMITTED. NOT DEPLOYED / NOT PUSHED / NOT RESTARTED.**
Branch `pm-live-roster-2026-09-12` off `origin/prod-live` @ `dfbb140a` (git truth), worktree
`C:\Users\AA Incorporado\cc-pm-live-roster-wt`. DEPLOY / PUSH / RESTART are Jack's reserved actions.

The "Copies these whales" line on `/live/{account}/{category}` becomes the whale ROSTER: each whale's real-money
live-copy record for THIS sub-division, grouped ON-ROSTER vs FORMERLY-LIVE, with a Detach control (owner-or-admin)
and a click-to-filter drill-through into the trade drawer.

--------------------------------------------------------------------------------
## 1. TRUTH + BASELINE

- `origin/prod-live` tip recorded at start = **`dfbb140a`** (the milestone-start-times deploy). Branched off it.
- **box == prod-live: 40/40 pm_web files byte-identical (CR-stripped)** — verified `cc/pm_live_boxshas_ro` vs
  `git show origin/prod-live:<f>` for every `.py/.html/.css/.js` under `web/`. Zero mismatches. NO reconcile finding.
- **Baseline (§1 command, box venv via `cc/pm_roster_suite` -> `pm_live_suite.sh`):**
  `cd /tmp/scr_pllive && PYTHONPATH=/tmp/scr_pllive venv/bin/python -m pytest tests/prediction_markets -q -p no:pytest_ethereum -p no:cacheprovider --continue-on-collection-errors`
  The documented baseline was **22 FAILED**; on the current prod-live (dfbb140a) the box FAILED SET is the SAME
  pre-existing UI tests (`test_live_r3` ×14, `test_accounts_m2` ×3, `test_stage2_nav` ×2, `test_stage2_phase3` ×2).
  `test_ctx_pagination_fix` is env-dependent (needs pykalshi: FAILS on the local `.venv-webtest`, PASSES on the box
  engine venv) — that is the 22-vs-21 difference, NOT this workstream. NONE are ours to fix.

--------------------------------------------------------------------------------
## 2. DISCOVERY (read-only, done before coding)

| question | finding (file:line) |
|---|---|
| **R2 — does the attachment table keep span history?** | PARTIAL. `pm_subdivision_attachment` (db.py:702) carries `added_ts` + `removed_ts` + `active`, PK `(account,category,wallet)` = ONE row per whale. `added_ts` = attached-since; `removed_ts` = last detach. **NO multi-span history:** re-attach (`promote_to_live`, farm_actions.py:152-156) does `ON CONFLICT DO UPDATE SET active=1, removed_ts=NULL` and OMITS added_ts, so the ORIGINAL added_ts is preserved and a prior detach/re-attach boundary is erased. -> a re-attached whale shows ONE span (attached since the original date); explicit PRIOR spans are UNKNOWABLE from the schema. **The RECORD itself is complete regardless** (R1: it is the journal, which spans every attached period). |
| **R2 — migration needed?** | **NO.** Current-span dates (added_ts/removed_ts) give the two groups R2 asks for; the per-whale figures come from the journal (complete across spans). Explicit prior-span DATE boundaries would need migration **022** (an attach/detach EVENT log) — **NOT built** (the figures don't need it; history before such a migration is unknowable). Flagged for a Jack ruling if he wants prior-span dates. Migration head confirmed = **21** (db.py `SCHEMA_HEAD`), so the next number WOULD be 022. |
| **R6 — detach mechanism** | The CLI function is `farm_actions.detach_from_live` (farm_actions.py:165) = `UPDATE pm_subdivision_attachment SET active=0, removed_ts=? WHERE ... active=1` — reversible, idempotent. The driver's roster query is **attachment-gated and read per cycle**: `live_driver.py:1096-1098` `SELECT wallet FROM pm_subdivision_attachment WHERE ... active=1` INSIDE the ~7s evaluate loop -> a detach drops the whale next cycle, **no restart**. My route calls the SAME function (never re-implemented). |
| **authz — owner-or-admin check exists?** | NO. authz.py had `visible_account_ids` (SEE) + `is_admin`, but no owner-or-admin WRITE gate. ADDED `can_act_on_account` (+ request wrapper) — the first use of `owner_identity` to gate a WRITE. |

--------------------------------------------------------------------------------
## 3. WHAT WAS BUILT

- **Reader `subdivision.whale_live_records(conn, account, category, *, marks, now_ts, today_start_ts, thin_floor)`**
  (NEW, pure): reuses `live_copies_by_whale` (the existing per-whale journal aggregate) and enriches it with the
  attachment dates + active status, per-whale CURRENT VALUE at held-leg BID with honest N-of-M coverage (`marks`
  passed IN, duck-typed `.yes_bid/.no_bid` so subdivision.py imports no web module), `realized_today` (ET calendar,
  settlements only), `unbooked_closes = n_closed - n_settled` (opposed + whale-exit, never hidden). Groups
  ON-ROSTER (active=1) vs FORMERLY-LIVE (inactive attachment OR journal copies with no active attachment). Honest
  empty pre-money-layer.
- **authz `can_act_on_account` / `can_act_on_account_request`** — owner-OR-admin, fail-closed.
- **Routes (app.py):** `GET /live/{a}/{c}/detach/{wallet}` (server-rendered CONFIRM page, JS-off safe) +
  `POST /live/{a}/{c}/detach/{wallet}` (owner-or-admin server gate `_owner_or_admin_gate`, calls
  `farm_actions.detach_from_live`, 303 PRG). Loader `_load_live_subdivision` now passes `whale_records` + `marks`
  + `can_detach`. Farm demote-409 text updated to point at each sub-division's roster page (was "the CLI").
- **Templates:** NEW `partials/pm_whale_roster.html` (the two groups, dollars-first figures, Detach control, +
  inline R4 drill-through JS) + NEW `partials/pm_detach_confirm.html`; `pm_live_subdivision.html` changed by ONE
  include line (replacing the old panel); `partials/pm_trade_drawer.html` gains `data-wallet` on each row (R4).
- **CSS:** `pm_desk.css` `.roster` + `.roster-confirm` blocks (scoped); `pm_shell.html` `pm_desk.css?v=` bumped
  `246a3fa9 -> c5efb60a`.
- **R4 drill-through:** clicking a whale filters the drawer to its `data-wallet` rows (toggle to clear); JS-only,
  JS-off shows all. The JS is inline in the roster partial (respects the ONE-include-line rule for the page).
- **R5:** no paper-vs-live — the roster is live-copy only; paper stays on Farm.

**Collision-avoidance with `pm-live-fixes-2026-09-12`** (which edits the positions table + event block on the same
page): my `pm_live_subdivision.html` change is ONE include line; my reader is in **subdivision.py** (NOT live_view.py,
which pm-live-fixes edits); the drawer attr is in `pm_trade_drawer.html` (not in their set); app.py + authz.py are
not in their set. Shared touch points are `pm_desk.css` (additive) + `pm_shell.html` (`?v=`) — the second-to-deploy
branch rebases those two trivially.

--------------------------------------------------------------------------------
## 4. FILE DIFF vs prod-live (CR-stripped sha16 BEFORE -> AFTER)

**★ app.py IS touched** (the detach routes + loader wiring + demote-409 text). This is expected for R6 (a new POST
route); `pm-live-fixes` does NOT touch app.py, so no collision.

| file | BEFORE (box == prod-live) | AFTER | note |
|---|---|---|---|
| `subdivision.py` | `f11d755e1045068f` | `83e3893079a40625` | **engine-shared, ADDITIVE-ONLY** (3 new fns; see §6) |
| `web/authz.py` | `b566c96fdf0d7221` | `3799a80c56698f0c` | + can_act_on_account |
| `web/app.py` | `b8b512ea6f9bcea9` | `ef1dec75a25c52cc` | detach routes + loader + 409 text |
| `web/templates/pm_live_subdivision.html` | `fff281bf65acea7c` | `cea30f343374b212` | ONE include line |
| `web/templates/partials/pm_whale_roster.html` | ABSENT (new) | `8f7d3e9ea5ff79a7` | roster + inline R4 JS |
| `web/templates/partials/pm_detach_confirm.html` | ABSENT (new) | `f50b3d8536266fe0` | confirm page |
| `web/templates/partials/pm_trade_drawer.html` | `48d579db5c2deb65` | `fc251c6d37310eb4` | + data-wallet |
| `web/static/pm_desk.css` | `246a3fa9dd20376c` | `c5efb60aae99071d` | .roster + .roster-confirm |
| `web/templates/pm_shell.html` | `d7fae4fc4fdc2081` | `7e73fe614372fce5` | pm_desk.css ?v= bump |

Git-only (NOT deployed to the box): `tests/prediction_markets/test_whale_roster.py`, this report.

--------------------------------------------------------------------------------
## 5. MIGRATION

**NONE.** Schema head stays **21**. No new table/column. If Jack later wants explicit prior-span date boundaries for
re-attached whales, migration 022 (an attach/detach event log) is the smallest that could — but the record (figures)
is already complete via the journal, so it is deferred, not required.

--------------------------------------------------------------------------------
## 6. DEPLOY SHAPE

**pm_web files + ONE engine-shared file (subdivision.py, additive-only) + ONE `prediction-markets-web` restart.
Engine `trading-corp` NEVER restarted, NEVER behavior-changed.** No migration -> no pre-restart migration step.

- **subdivision.py is engine-shared** (imported by driver_roster.py + settlement.py). My change is **purely additive**
  (three NEW functions: `whale_live_records`, `_bid_for_leg`, `_realized_today_by_whale`; no existing function
  modified). The running engine keeps its in-memory copy at deploy time (not re-imported); on its NEXT restart it
  loads a behavior-IDENTICAL file. **Precedent:** the tiles Deploy-7 shipped a subdivision.py change (`f11d755e`,
  folded in the 2026-09-12 git reconcile) the same pm_web-only way. Verify by grepping the diff: only additions after
  `live_copies_by_whale`.
- **Deploy:** plain diff of the 9 files onto prod-live via scp+tar (drift-gate box == BEFORE for all 9, backup is a
  gate, post-sha + py_compile + import-closure, rollback-all on any failure) — the milestone deploy's proven
  `pm_milestone_graft` pattern, re-pointed. ONE `az vm run-command ... 'systemctl restart prediction-markets-web'`.
- **Post-check:** all 9 shas == AFTER; pm_web recycled; **engine PID + NRestarts UNCHANGED**; `/live/{a}/{c}` 200 with
  the roster (both groups); the confirm page 200; a Karen-on-Jack detach POST -> 403 (route test, not exercised on
  prod); `/pm/arm` 404; no journal errors.
- **prod-live advance (same session, after post-check green):** FF `origin/prod-live` to the deployed commit + tag;
  re-verify box == prod-live. main untouched.
- **R7 — Detach POST is NEVER exercised on prod during the deploy** (tests only). The first real Detach is Jack's.

--------------------------------------------------------------------------------
## 7. VERIFICATION

- **Reader/authz/route tests:** NEW `tests/prediction_markets/test_whale_roster.py` — **20 tests, all pass**
  (grouping; re-attached-once with original added_ts; full dollars-first record; N-of-M current value; no-marks ->
  None; formerly-live removed_ts; attached-never-copied zeros; honest-empty; authz admin/owner/other-403/no-id-403/
  null-owner; roster page renders both groups + Detach; confirm states consequences; detach POST sets active=0 +
  idempotent; owner detaches own; owner 403 on other's; no-identity 403; Detach hidden for formerly-live).
- **Full-suite differential (local `.venv-webtest`, same venv both sides):** baseline **155/22** -> change **175/22**;
  **NEW failures EMPTY** (0 regressions; the 20 new tests pass).
- **Box suite (with changes, §1 command):** 21 pre-existing UI failures (= the clean baseline minus the
  env-dependent `test_ctx_pagination_fix`); the 14 `test_live_r3` match the clean baseline exactly; **all 20 new
  tests pass on the box venv.** No new failure.
- **Box RO tie-out (`cc/pm_roster_boxtie_ro`, ran MY reader on the real box DB, mode=ro):** across the 5 busiest
  sub-divisions **ALL TIE** — per-whale summed placed/booked/unbooked/realized == independent journal totals; e.g.
  kalshi_jack/mlb (3 on-roster + **1 formerly-live**): placed 115==115, booked 101==101, unbooked 11==11, realized
  3.3691==3.3691. **Whale names match the drawer's pm_whale attribution** (xifutloong3, MadeiraIsland; wallet-only
  whales -> None on both). Real prod data exercises the formerly-live group + the wallet-only truncation path.
- **Renders (viewed):** `cc/renders/roster_desktop.png` (SDTrading full record + a wallet-only whale on-roster +
  xifutloong3 formerly-live, Detach on the on-roster rows only), `roster_confirm.png` (the R6 confirm text, verbatim),
  `roster_phone.png` (374px, stacks cleanly). Harness `cc/pm_roster_render.py`.

--------------------------------------------------------------------------------
## 8. RUNNERS (cc/, read-only unless noted)
`pm_live_boxshas_ro.*` (box==prod-live), `pm_roster_suite.*` (box §1 suite), `pm_roster_boxtie_ro.*` (RO reader
tie-out on the box DB), `pm_roster_render.py` (renders). Deploy (below): `pm_roster_precheck_ro.*`,
`pm_roster_graft.*` (verify|graft), `pm_milestone_restart_az.ps1` (reused, pm_web-only restart),
`pm_roster_postcheck_ro.*`, `pm_roster_prodlive_ff.ps1`.

--------------------------------------------------------------------------------
## 9. DEPLOY 9 — LIVE ON PROD 2026-09-12 (board-authorized, full atomic authority)

**SHIPPED `afbcfbea` (branch pm-live-roster-2026-09-12) off prod-live `dfbb140a`. prod-live FF `dfbb140a ->
afbcfbea`, tag `pm-roster-deploy9-2026-09-12`. pm_web-only + one engine-shared additive file; ONE pm_web restart;
engine `trading-corp` NEVER touched. Box == prod-live 42/42 web files + subdivision.py, before AND after.**

**Pre-deploy (RO, all green):** prod-live tip still `dfbb140a`; branch FF-able; **subdivision.py diff = 112
insertions / 0 deletions** (the 3 new functions only -> engine-shared but ADDITIVE-ONLY, behavior-neutral for the
engine; recorded here for whoever restarts the engine next); box == prod-live 40/40 (pre); schema head **21**;
engine `trading-corp` MainPID **351422** NRestarts 0 / pm_web **360799** NRestarts 0; 9-file BEFORE gate all OK.
Attachment rows recorded: jack/mlb = 3 active + 1 inactive (SDTrading `0x16bb9951`, added 1787975891 removed
1788468918); karen/mlb = 3 active. `/live/kalshi_jack/mlb` before = 201280 bytes, old panel ("Copies these whales",
no "roster").

**Graft (scp+tar, `pm_roster_graft.ps1 -Mode graft`):** drift-gate box==BEFORE (no drift); **backup =
`/home/azureuser/pm_roster_deploy9_backup_20260912T143619Z`** (OUTSIDE the service path; 7 existing files backed up
and sha-verified == box before any write); 9 files written, each post CR-sha16 == AFTER; py_compile OK;
import/route/standalone gate OK (detach route present, `/pm/arm`=0, engine-modules=0). Before -> After CR-sha16:

| file | BEFORE | AFTER |
|---|---|---|
| subdivision.py | f11d755e1045068f | 83e3893079a40625 |
| web/authz.py | b566c96fdf0d7221 | 3799a80c56698f0c |
| web/app.py | b8b512ea6f9bcea9 | ef1dec75a25c52cc |
| web/templates/pm_live_subdivision.html | fff281bf65acea7c | cea30f343374b212 |
| web/templates/partials/pm_whale_roster.html | ABSENT | 8f7d3e9ea5ff79a7 |
| web/templates/partials/pm_detach_confirm.html | ABSENT | f50b3d8536266fe0 |
| web/templates/partials/pm_trade_drawer.html | 48d579db5c2deb65 | fc251c6d37310eb4 |
| web/static/pm_desk.css | 246a3fa9dd20376c | c5efb60aae99071d |
| web/templates/pm_shell.html | d7fae4fc4fdc2081 | 7e73fe614372fce5 |

**Restart:** `az vm run-command ... 'systemctl restart prediction-markets-web'` -> pm_web **360799 -> 363574**
(NRestarts 0, active). **Engine `trading-corp` 351422 NRestarts 0 UNCHANGED** immediately after and through every
subsequent step.

**Post-deploy (RO, all green):** 9 files sha16 == AFTER; served pm_desk.css sha8 **c5efb60a** + shell ?v=**c5efb60a**
(target). Roster renders jack/mlb (on-roster + formerly-live) and karen/mlb (on-roster) — "attached since", span
dates, per-whale placed/booked/W-L/unbooked/realized + open with N-of-M priced, THIN, Detach on on-roster rows only,
**0 double-escaped entities, 0 raw tickers** on the roster panel. **DEPLOYED reader ties on live data:** jack/mlb
(3 on + 1 former) placed 115==115 booked 101==101 realized 3.3691==3.3691; karen/mlb (3 on) placed 70==70 booked
65==65 realized -16.9120==-16.9120. **Authz (owner-or-admin, NO POST on prod):** karen confirm OWN -> 200; karen
confirm JACK -> 403; no identity -> 403; jack page as no-identity -> 403 (scoped). Farm `/farm/mlb` 200 with
live-whale badges; deployed app.py carries the new demote-409 text pointing at the roster page. All pages 200
(`/`, `/live`, both account tabs, both account pages, every real `/farm/{category}`); all static assets 200
(`GET /farm/search` -> 404 is a POST search-action path caught by the href grep, a GET to a non-category ->
pm_category_404 by design, PRE-EXISTING — farm untouched by this deploy, NOT a regression). `journalctl` 0 errors
since the restart.

**Shared-module proof (for whoever restarts the engine next):** subdivision.py changed BEFORE `f11d755e1045068f`
-> AFTER `83e3893079a40625`, a PURE ADDITION of `whale_live_records` + `_bid_for_leg` + `_realized_today_by_whale`
(git diff = 112 insertions, 0 deletions; no existing function's signature or body changed). The engine imports
subdivision.py (via driver_roster.py + settlement.py); its next restart loads a behavior-identical file. Precedent:
tiles Deploy-7 shipped a subdivision.py change (`f11d755e`) the same pm_web-only way.

**Truth:** box == prod-live 42/42 web files + subdivision.py, after the FF. main untouched; 95e78c4 reachable.

**★ FIRST REAL DETACH NOT YET EXERCISED** (R7 held: no POST on prod). Jack performs the first Detach; the expected
observation is that whale's next signal showing "placed 0" on the driver heartbeat within one ~7s cycle (copying
stops, no restart; its open positions ride to settlement).
