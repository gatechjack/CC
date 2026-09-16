# GROUP 1 DEPLOY MANIFEST -- promotion-view display + category hygiene -- 2026-09-15

Branch `pm-promo-hygiene-g1-2026-09-15` @ `c1a5a22a` (off `origin/prod-live ecb4791e`; pushed to origin).
**pm_web-ONLY. NO migration. NO engine restart. ONE pm_web restart.** 31 sub-divisions stay armed/trading.

## What ships (4 runtime files; tests are repo-only, not deployed)
| file | base (prod-live, CR-sha16) | target (CR-sha16) |
|---|---|---|
| `trading_corp/prediction_markets/tradable_categories.py` (NEW) | ABSENT | `450ff54f39e6` |
| `trading_corp/prediction_markets/web/app.py` | `808123ac8023` | `55beb8a37fa4` |
| `trading_corp/prediction_markets/web/templates/pm_macros.html` | `631778b3a9a9` | `4232890ef6cd` |
| `trading_corp/prediction_markets/web/templates/partials/pm_prospects_rows.html` | `0d68cafdf249` | `c784369f7d42` |
Base shas == the LIVE box shas (box-scratch confirmed box == prod-live CR-stripped). `search.py` UNCHANGED
(`9c199967d6ab`) -- item 4 (retire tennis) is STOPPED, tennis stays a farm category.

## The changes
- **Item 1** -- `score_badge` (Watchlist + /live roster + the Analyze OOB) now surfaces loss-omission from the
  score dict: UNGROUNDED reads **"omit UNKNOWN" (never 0)**; grounded>0 shows "-X% loss" + coverage + floor;
  grounded==0 shows "omit 0%". Prospects already carried the persistent `omission_cell` (fed from
  `pm_loss_grounding_cache`), so no Prospects change was needed.
- **Item 6** -- a PERSISTENT Analyze button on the Prospects action cell (was only the un-analyzed score_cell
  control); same `/farm/analyze/{w}/{c}` route, JUDGE column + `#pm-score-{w}-{c}` OOB id unchanged.
- **Item 3** -- NEW dependency-free `tradable_categories.py` (23 matcher-backed cats, == box
  MATCHER_ADAPTERS==CATEGORY_CTX_BUILDERS) + `_tile_visible(cat, has_footprint)` filter on SUB-DIVISION tiles
  (/live `subs`; account-page `agg["subdivisions"]`). Hides INERT matcherless orphans (kalshi_jack/soccer +
  /tennis) -- ★ MONEY-AWARE (adversarial-review decision): a matcherless sub is hidden ONLY when footprint-free
  (0 whales/orders on /live; 0 open/closed on the account page), so money at risk is never hidden AND a hidden
  sub contributes 0 to the account aggregate (visible rows always sum to the total). Golf-scope guard: the FARM
  side is a DISTINCT gate (`search.CATEGORY_ALLOWLIST`) -> golf + tennis farm pages/prospects/watchlist survive.

## Verification (box-scratch, read-only, ISOLATED; live tree sha unchanged before/after)
- IMPORT-CLOSURE (pm_web-only PROVEN): importing `tradable_categories` AND `web.app` pulls NO
  broker/pykalshi/registries (leaked=NONE).
- `test_tradable_categories.py` 5/5 -- incl the **DRIFT-GUARD which RAN (not skipped)**: TRADABLE_CATEGORIES ==
  MATCHER_ADAPTERS == CATEGORY_CTX_BUILDERS; golf-scope guard passes.
- `test_prospects_score.py` 16/16 (item-1 badge omission UNKNOWN-not-0 + item-6 persistent button + item-3
  money-aware `_tile_visible`).
- DIFFERENTIAL vs a CLEAN origin/prod-live scratch: **0 NEW failures** -- identical 21 pre-existing TestClient
  env-gap failures on both; branch = baseline + 11 new PASSES.
- TWO adversarial skeptics: no BLOCKER/HIGH; the one MED (aggregate-vs-rows money visibility) is FIXED
  (money-aware filter); LOW polish applied (strip/docstring/sub-1% "<1% loss").

## DEPLOY SEQUENCE (each box step halts for board authorization)
- **STEP 1 -- GRAFT** `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_g1_graft.ps1"` -- scp+tar staging;
  drift-gate box==base on the 3 edited files + new-file-absent + writability; backup; apply LF-normalized;
  re-verify box==target; py_compile. Aborts + rolls back to base on ANY drift/mismatch/compile-fail. Inert until
  restart (the running pm_web keeps serving old code). azureuser scp+cp (no az-root).
- **STEP 2 -- RESTART pm_web** (RESERVED -- board/Jack runs; az-root is agent-blocked):
  `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart prediction-markets-web"`.
  Bounces pm_web only (~seconds); the ENGINE (trading-corp) is NOT touched -> 31 subs keep trading. This is when
  the new code loads.
- **STEP 3 -- POST-CHECK** `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_g1_postcheck_ro.ps1"` (read-only).
  ACCEPTANCE: 4 files == target + search.py unchanged; pages 200; /farm/mlb has the persistent Analyze button;
  **/farm/tennis + /farm/golf still 200 (farm survives)**; /live + account show NO soccer/tennis sub-tile while
  /live/mlb tile present; engine PID == pre-deploy + liveness all RUNNING + any_alarm False; 0 new pm_web tracebacks.
- **STEP 4 -- FOLD to prod-live** (RESERVED -- board/Jack): three-way prove box==prod-live(new)==branch on the 4
  files (CR-sha); FF `git push origin pm-promo-hygiene-g1-2026-09-15:prod-live` (linear descendant of ecb4791e);
  ledger commit. Deploy is complete only once prod-live carries it.

## STOP CONDITIONS
- STEP 1 aborts (exit != 0 / any "DRIFT"/"MISMATCH"/"ABORT") -> DO NOT restart; box is left == base (rolled back).
- POST-CHECK shows engine PID CHANGED, any page != 200, /farm/tennis or /farm/golf != 200 (farm wrongly hidden),
  a soccer/tennis sub-tile still present, any_alarm True, or new tracebacks -> ROLLBACK (restore the backup dir
  `~/pm_g1_graft_backup_<ts>` + restart pm_web) and STOP.
- ROLLBACK: `cp` the 3 files back from `~/pm_g1_graft_backup_<ts>/` + `rm tradable_categories.py` + restart pm_web
  -> back to base. (Pre-restart, restoring the files alone reverts, no restart needed.)

## Item 4 (retire tennis) -- STOPPED, NOT in this deploy
The ITF check found real ITF whale volume in the coarse `tennis` bucket (~$9.9M/85 wallets; dozens of current
`itf-{p1}-{p2}-{date}` matches + grand-slam matches mis-bucketed by tournament name). Tennis is NOT a clean
duplicate -> retiring it would delete the only ITF discovery surface. That is a new-category decision (Jack's).
See `TENNIS_ITF_STOP_2026-09-15.md`. Item 3's tile filter still hides the inert kalshi_jack/tennis SUB-DIVISION
orphan; tennis stays a FARM category.
