# COMBINED G2 + G3 DEPLOY MANIFEST -- attachment-span (mig 024) + scorer/skill -- 2026-09-16

Branch `pm-g2g3-combined-2026-09-16` (clean merge of `pm-attach-span-g2` into `pm-scorer-skill-g3`, both onto
prod-live `ffa646b3`). **pm_web + pm_cli-owned; MIGRATION 024 (additive); ONE pm_web restart; NO engine restart.**
31 sub-divisions stay armed/trading. Ships G2 + G3 together (one bounce) per Jack's ruling.

## Why combined (and the rollback check Jack asked for)
G2 and G3 touch mostly DIFFERENT files; the only shared file is `app.py`, in NON-OVERLAPPING hunks (G2 = actor
threading ~L798/876/904/1168; G3 = the farm_analyze data dict ~L438; G1 = tile filter, already on prod-live). The
merge was CLEAN (no conflicts). Combined box-scratch: all groups' tests pass together, 0-new differential (21 same
pre-existing / 425 passed), SCHEMA_HEAD=24, web.app import-closure clean (no broker/pykalshi).
★ ROLLBACK IS NOT MESSIER than two separate ones: migration 024 is ADDITIVE CREATE-only, so a code rollback
leaves an empty unused table (harmless -- the old farm_actions simply never writes it); the SKILL_VERSION bump is
cache-KEYED, so a rollback to "4" re-keys the cache cleanly (recomputes old verdicts). One restore-6-files +
one restart reverts everything; no data migration to reverse. => the single bounce is the right call.

## What ships (6 runtime files; tests + manifests repo-only). base(prod-live ffa646b3) -> target(combined) CR-sha16
| file | base | target |
|---|---|---|
| `trading_corp/prediction_markets/db.py` (MIGRATION_024) | 8c025addc216 | 49f4f6d8d53d |
| `trading_corp/prediction_markets/farm_actions.py` (attach/detach events, reader, atomic) | 37b9d7b1a0cb | 23f91d44e592 |
| `trading_corp/scripts/pm_cli.py` (actor='cli') | b5cb0b91fa84 | 5290b444c01d |
| `trading_corp/prediction_markets/web/app.py` (G2 actor threading + G3 ranking_metrics route) | 55beb8a37fa4 | 5303952cc138 |
| `trading_corp/prediction_markets/analyze.py` (item 5 ranking_metrics + item 7 2-sentence + skill 5 + cap 220) | a53c63b75ebd | 1048bd1e37ef |
| `trading_corp/prediction_markets/web/templates/partials/pm_analyze_result.html` (ranking-metrics render) | 03613222ebd1 | 359e7472db92 |
base shas == the LIVE box (post-G1). NO pm_macros.html / pm_prospects_rows.html / tradable_categories.py here --
those are G1, already on prod-live.

## The changes (see the two group manifests for detail)
- **G2 (item 2):** append-only `pm_subdivision_attachment_event` (mig 024) written ATOMICALLY by
  farm_actions.promote_to_live/detach_from_live (+ actor threaded from pm_web/pm_cli) + read_attachment_events.
  Engine-neutral (driver reads `active=1`, never the event table). Manifest: GROUP2_DEPLOY_MANIFEST_2026-09-15.md.
- **G3 (items 5+7):** item 5 = the stats.py ranking metrics on the Analyze panel -- CLEAN set (net_roi cost-basis,
  edge_factor, n_resolved, n_excluded, min_resolved) shown + fed to Sonnet; INVERTING set (composite score,
  wilson_lcb, roi_notional) shown FLAGGED "a whale hiding losses scores HIGHER", WITHHELD from the verdict + tier.
  item 7 = INSUFFICIENT_DATA narrates caveat + one shape-read sentence (tier-conditional; skill 4->5; cap 160->220).

## Verification (box-scratch, isolated, live tree sha unchanged)
- COMBINED tree: 5 files py_compile OK; web.app import-closure clean; SCHEMA_HEAD=24; the 4 group test files
  (attach-span 8, scorer-skill 7, tradable 5, rung3 11 = 31) pass together; pm-suite 0-NEW (same 21 pre-existing,
  425 passed).
- Two adversarial skeptics PER group: G2 detach-atomicity (fixed + injection test); G3 recency-samples truncation
  (fixed -- recency metrics dropped, n_resolved conveys sample size). No BLOCKER/HIGH open.
- ★ Item 5 proven by ASSERTING THE PROMPT STRING (inverting metrics ABSENT from _build_user_content), not by code.

## DEPLOY SEQUENCE (each box step halts for board authorization)
- **STEP 1 -- GRAFT** `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_g2g3_graft.ps1"` -- scp+tar; drift-gate
  box==base on all 6 + writability; backup; apply LF-normalized; re-verify target; py_compile the 5 py. Rolls back
  to base on ANY mismatch. azureuser scp+cp (no az-root). Inert until restart.
- **STEP 2 -- MIGRATION 024** self-applies via `db.init_db` (the pm_cli cron, or the pm_web restart's init_db);
  additive CREATE-only; the engine (old db.py) is backward-compatible with a head-24 DB.
- **STEP 3 -- RESTART pm_web** (RESERVED -- Jack; az-root): `systemctl restart prediction-markets-web`. Engine untouched.
- **STEP 4 -- POST-CHECK** `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_g2g3_postcheck_ro.ps1"` (read-only):
  6 files == target; schema head==24 + event table present; pages 200; G1 still live (soccer/tennis sub-tiles hidden,
  tennis/golf farm survive); engine PID == pre-deploy + liveness clean; 0 new tracebacks.
  ★ ITEM 5/7 ACCEPTANCE (a live Analyze, ~$0.006 vs the $20/day cap): POST /farm/analyze/<an INSUFFICIENT_DATA whale>/mlb
  -> the panel shows the ranking-metrics block (inverting flagged "hider scores HIGHER") + a TWO-sentence
  INSUFFICIENT_DATA verdict; confirm tokens_out < 220 (the 2nd sentence is not clipped) -- the only real proof of
  item 7, since box-scratch cannot call the LLM.
- **STEP 5 -- FOLD** to prod-live (RESERVED -- Jack): FF `git push origin pm-g2g3-combined-2026-09-16:prod-live`
  (linear descendant of ffa646b3) + ledger. Complete once prod-live carries it.

## STOP CONDITIONS / ROLLBACK
- Graft drift/mismatch/compile-fail -> auto-rollback to base (box left consistent); DO NOT restart.
- Post-check: 6-file sha off, head != 24, engine PID changed, a matcherless sub-tile visible, /farm/tennis|golf
  != 200, liveness alarm, or new tracebacks -> restore backup `~/pm_g2g3_graft_backup_<ts>` + restart pm_web.
- The additive mig-024 table is left in place on a code rollback (harmless, unused by the reverted farm_actions).
