================================================================================
PM UI -- DEPLOY 8: FARM LIVE-WHALE STATE + PROMOTE GUARD (Board-authorized 2026-09-11)
================================================================================
DEPLOYED LIVE + prod-live ADVANCED + VERIFIED. pm_web-ONLY, ONE pm_web restart. The
engine (trading-corp) was ARMED and trading throughout and was NEVER touched, restarted
or reloaded -- PID 313359 / NRestarts 0 before and after every step.

NEW MODEL (this deploy): origin/prod-live on GitHub is TRUTH; box == prod-live; NO
captures, NO grafts. The change was made a real commit ON prod-live and deployed from it.

DEPLOY TARGET (single commit, the deploy source):
  branch  pm-farm-livewhale-2026-09-11  off origin/prod-live tip 091b0e0
  commit  b1c552b15005f2952a3a465e0a4894e7904f1993
  tag     pm-farm-deploy8-2026-09-11 -> b1c552b1
  origin/prod-live advanced 091b0e0 -> b1c552b1 (fast-forward, verified)

FOUR pm_web files, CR-stripped sha16 before(prod-live tip)->after(deployed==prod-live now):
  web/app.py                                      23ec41a66d991846 -> de5f39ed21f301ef
  web/static/pm.css                               4a4b19dffd524c5b -> d71151bdd8419879
  web/templates/pm_shell.html                     6141979a864e4c04 -> d7fae4fc4fdc2081
  web/templates/partials/pm_watchlist_rows.html   ada2ffb3f9bd3890 -> 3c1101291c1a5799
  + tests/prediction_markets/test_farm_live_whale_state.py (6 new tests; not a served asset)
main.py / engine / order path / migrations: NOT touched. No packages/venv/unit-file changes.

pm_web PID 320362 -> 332976 (the ONE restart). Engine trading-corp 313359/0 UNCHANGED.
Backup (gate): /home/azureuser/pm_deploy8_backup_20260911T032739Z (4 files; each backup
CR-sha == the live file before the write; rollback = restore + pm_web-only restart).

--------------------------------------------------------------------------------
STEP 0 -- the change as a real commit on truth
--------------------------------------------------------------------------------
Branched pm-farm-livewhale-2026-09-11 off origin/prod-live tip 091b0e0. Cherry-picked
59be5c9 (the earlier build) --no-commit; dropped the two non-deployable modify/delete
conflicts (reports/.../PM_TILES_REDESIGN_BUILD_2026-09-10.md and tests/.../test_web_r6.py
-- neither is on prod-live); the three pm_web source files auto-merged CLEAN onto the
intended shas (de5f39ed / d71151bd / 3c110129) -- no .pm-xlink re-add, no leg-audit undo;
the new test file came across as an add. Then corrected pm_shell.html's cache-bust to the
deploy-faithful pm.css?v=d71151bd (the cherry-pick left the build branch's stale 81cb16cc)
-> pm_shell d7fae4fc. Verified all four CR-shas == target and the cache-bust logic (pm.css
?v=d71151bd == disk d71151bd; pm_desk 246a3fa9; htmx 491955cd -- all MATCH). Committed
b1c552b1.
  Full 81-file suite from truth (box venv; scratch trees removed): prod-live tip 47 failing
  -> deploy-target 40 failing; REGRESSIONS (pass on tip, fail with change) = EMPTY. The
  47->40 delta is SEVEN tests flipping fail->pass: the 6 new feature tests + test_asset_
  cache_bust (the corrected ?v fixes a PRE-EXISTING prod-live cache-bust failure). This is
  one better than the brief's estimate of 41 -- the brief's arithmetic (47-6) did not model
  that the deploy-faithful ?v also fixes cache-bust. Delta fully attributed; no unexpected
  change; not a stop condition.

--------------------------------------------------------------------------------
PRE-DEPLOY CHECKS (read-only)
--------------------------------------------------------------------------------
[5] engine trading-corp PID=313359 NRestarts=0 active/running ; prediction-markets-web
    PID=320362 NRestarts=0 active/running.
    schema head = 21 (schema_version table), NOT the brief's expected 20 -- because
    migration 021 (leg-independence fix) is ALREADY live from a prior deploy. This deploy
    is pm_web-only and touches NO schema, so 21-vs-20 is immaterial (flagged, not silently
    passed). PM db has no arm tables; the engine arm state lives in the engine legacy DB
    (not touched by pm_web or this deploy; the engine stayed armed+running).
[6] BOX == prod-live tip for the pm_web package: 60/60 files CR-stripped-identical, zero
    mismatches, zero one-side-only (the 6 .bak_*/.orig are untracked deploy backups). GATE
    PASSED.
[7] BACKUP IS A GATE: backed up the four live files to the dated dir above and verified
    each backup CR-sha == the live file before any write. Verified the dir exists.
[8] attachment rows (badge-expected = active=1 AND pinned): mlb 6, cs2 2, nfl 6; 44 active
    attachments total. Served pm.css = 4a4b19df (before). /farm/mlb BEFORE: 0 live-badges,
    18 attach-forms, served shell pm.css?v=204d9051 (the stale-on-truth value), 0 raw
    tickers.

--------------------------------------------------------------------------------
DEPLOY + RESTART
--------------------------------------------------------------------------------
[9] Copied the four files from the deploy-target commit (staged to /tmp), re-verified each
    CR-sha == target, wrote them CR-stripped (LF, matching prod-live raw) into the service
    path, and verified each in place == target. py_compile app.py OK. app imported with no
    engine/broker/execution/driver modules (the import surfaced trading_corp.prediction_
    markets.arm -- the PRE-EXISTING read-only arm-status-badge dependency already in
    prod-live's app.py; the change's diff was +37/-2, all farm logic, zero new imports; the
    standalone-invariant test is green). /pm/arm route decorators in the placed app.py = 0.
    Engine PID 313359 unchanged across the write.
[10] Restarted prediction-markets-web ONLY via az vm run-command (RunShellScript,
    -g rg-shared-prod -n tc-prod-vm): systemctl restart prediction-markets-web. pm_web
    320362 -> 332976 active/running. Engine PID 313359 / NRestarts 0 -- unchanged, confirmed
    in the same run-command before and after.

--------------------------------------------------------------------------------
POST-DEPLOY VERIFICATION (read-only; served via curl :8081 with Remote-User: jack)
--------------------------------------------------------------------------------
[11] All 23 /farm/{category} = 200. Live-whale badges tie EXACTLY to the attachment rows:
     mlb 6, cs2 2, nfl 6 (== step-8 badge-expected). Categories whose whales are not both
     live-attached AND pinned correctly show 0 badges (nba/nhl/fed/golf/tennis/sea/fl1/uel).
     Every category: 0 raw KX tickers, 0 double-escaped entities, Analyze + Demote +
     Prospects present. /farm index: 200 with the Search control present. (Promote/Demote
     POST routes were NOT exercised on prod -- the 409 paths are covered by tests only.)
[13] Served pm.css sha = d71151bd (== target). Served shell pm.css?v = d71151bd -- the stale
     204d9051 is GONE. Every static asset (pm.css, pm_desk.css, pm_live_subs.js, pm_sort.js,
     htmx.min.js, a logo) = 200. No 404 (no rollback condition).
[14] / , /account/kalshi_jack , /account/kalshi_karen , /live , /live?account=kalshi_karen ,
     /live/kalshi_jack/mlb -- all 200 and styled (link the new stylesheets).
[15] engine trading-corp PID=313359 NRestarts=0 -- unchanged across all steps. journalctl
     -p err for prediction-markets-web + trading-corp since the restart: "No entries."
     Order counts (jack 286 / karen 173) are the engine's normal trading -- pm_web has no
     order path (imports no execution/broker, /pm/arm=0), so it places NOTHING.

Result: no rollback condition met. Deploy verified functionally on prod.

--------------------------------------------------------------------------------
WRAP -- truth stays true
--------------------------------------------------------------------------------
[16] Pushed pm-farm-livewhale-2026-09-11. Fast-forwarded origin/prod-live 091b0e0 ->
     b1c552b1 (FF-only; the deploy target's parent IS 091b0e0, so it was a clean FF -- a
     non-FF would have been rejected and stopped). Pushed the tag pm-farm-deploy8-2026-09-11
     -> b1c552b1. Re-ran box == prod-live: 60/60 after the advance -- box == prod-live ==
     b1c552b1 == the tag. The four deployed files match on both sides.

TRUTH BASELINE FOR THE NEXT AGENT: the full 81-file tests/prediction_markets/ suite against
CURRENT prod-live (b1c552b1) = 40 failing. All 40 are pre-existing cross-tree / env-gap
cases (test_pm_arm_view_m5 imports the engine-web trading_corp.web.pm_arm_view absent from
the standalone pm_web package; test_search_r1 asserts an old schema head of 15 vs the live
21; ingest/stats/ranking/cli/fixtures/integrity data-layer tests) -- NONE touch the farm UI.
(prod-live TIP before this deploy was 47; this deploy's code makes the 6 feature tests pass
and its corrected ?v makes test_asset_cache_bust pass, hence 40.)

★ pm-tiles-redesign-2026-09-10 is RETIRED. The box is no longer ahead of truth; there is no
capture-and-graft. ALL future PM UI work branches off origin/prod-live, is committed there,
deployed from that commit, and prod-live is fast-forwarded to the deployed commit.

--------------------------------------------------------------------------------
RUNNERS / ARTIFACTS
--------------------------------------------------------------------------------
  cc/pm_farm_deploy8_precheck.{ps1,sh}   steps 5/6/8 read-only
  cc/pm_farm_deploy8_apply.{ps1,sh}      steps 7/9 backup-gate + write (no restart)
  cc/pm_deploy7_restart_az.ps1           step 10 restart pm_web only via az (reused)
  cc/pm_farm_deploy8_postcheck.{ps1,sh}  steps 11-15 served-page verification
  cc/pm_farm_recon.{ps1,sh}              box==prod-live 60/60 comparison (steps 6 + 16)
  cc/pm_farm_truthtest.{ps1,sh}          step 0.4 full-suite from truth
  backup on box: /home/azureuser/pm_deploy8_backup_20260911T032739Z
================================================================================
