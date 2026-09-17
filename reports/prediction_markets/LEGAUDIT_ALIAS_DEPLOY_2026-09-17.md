# Deploy manifest — leg-audit venue-verified code-alias (`ok:code_alias`)

**Branch:** `cs2-legaudit-lg-luminosity-2026-09-17` @ **9419c2d8** (pushed to origin), off prod-live **d9468361**.
**Status:** BUILT · TESTED · BOX-SCRATCH VERIFIED · SKEPTIC-REVIEWED · COMMITTED · PUSHED. **HALT AT DEPLOY** (graft + both restarts + FF push are Jack's).

## ★ THIS IS AN ENGINE + PM_WEB DEPLOY — graft + TWO restarts; the engine restart BOUNCES EVERY DIVISION
- **`live_driver.py` is an ENGINE file** → engine (`trading-corp`) restart via `restart_tc.ps1` → **bounces ALL divisions** (MACE, PEAD, bitunix, coinbase, PM jack+karen, ...), ~3.5 min boot. Pick a window clear of the 9:30 ET equity open (PEAD) per the co-tenant rule.
- **`leg_audit.py` is a PM_WEB file** (imported only by `web/app.py` + `web/live_view.py`) → **pm_web ALSO needs a restart** via `restart_pmweb.ps1` (restarts `prediction-markets-web` only; does NOT touch the engine — the 31 armed subs keep trading).
- **pm_web template needs NO change:** `ok:code_alias` classifies CLEAN, so it is NOT surfaced in the `/live` review strip — there is no new label to render.

## ★ COUPLING / ORDER (hard requirement)
The engine starts writing `ok:code_alias` only after ITS restart. pm_web's `classify_leg_audit` must already know `ok:code_alias`=CLEAN, else those rows fall to `UNEVALUATED` and surface in the strip as "unevaluated (could not check)" — a regression. Therefore:

1. **Graft both files** (azureuser, drift-gated): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_legaudit_alias_graft.ps1"`
2. **Restart pm_web** (picks up the new classifier): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\restart_pmweb.ps1"`
3. **Restart engine** (picks up the writer; bounces all divisions): `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"`
4. **Boot-verify** (all divisions back, arm 31/0/0, 0 import errors), then **FF push to prod-live**: `git push origin cs2-legaudit-lg-luminosity-2026-09-17:prod-live`

(pm_web-before-engine is the safe order; grafting both first is fine because the OLD engine still writes only `code_review` until step 3.)

## Files (drift-gated, LF md5)
| file | service | base (d9468361) | target (9419c2d8) |
|---|---|---|---|
| trading_corp/prediction_markets/live_driver.py | engine | b9e67d1a7117191fa64b3e31290ec0f8 | 15069d88dd4d69aea3df9aaece74bfdb |
| trading_corp/prediction_markets/leg_audit.py | pm_web | ec005466f1d18c364ca2597e185c855f | 61cfe84b094ab5b109452a18dc74f3bc |

The graft runner drift-gates box==base for BOTH before touching anything, backs up to `~/pm_legaudit_graft_backup_<TS>`, rm-then-cp (handles root:root files), re-verifies box==target, py_compiles, and rolls back ALL on any mismatch (no partial graft). If prod-live has ADVANCED past d9468361 by deploy time, the gate ABORTS with "BOX != BASE" (rebase the branch onto the new prod-live, recompute md5s, rebuild `_legaudit_graft.b64`, re-run).

## What changed
`live_driver._LEG_AUDIT_CODE_ALIASES = {"lg":"luminosity"}` (venue-verified), consulted only when the name-family ordered-subsequence test fails; a hit → distinct verdict `ok:code_alias` (visible auto-clear, queryable), which `leg_audit.classify_leg_audit` now maps to CLEAN. Seeded LG→luminosity only. Rejected the `canon(yes_sub_title)==canon(outcome)` shortcut (it re-derives the matcher's own bind → tautological gate; independence is the product).

## Verification (already done, box venv, live tree/engine/DB untouched, PIDs unchanged 458566/436431)
- Box-scratch differential: baseline **21 == overlaid 21** failures (0 new; the 21 are pre-existing pm_web render/route tests not runnable headless), set-diff of new failures EMPTY.
- `tests/prediction_markets/test_leg_audit_surface.py`: **11 passed** (incl. the LG→Luminosity clean, the anti-case LG+NIP→code_review, and reader not-surfaced-but-queryable).
- Real-data smoke on box venv: LG/Luminosity→`ok:code_alias`→clean; ANTI LG/NIP→`code_review`→soft; NIP/NIP→ok; FAZE/magic→soft (regression intact).
- Skeptic review: no BLOCKER/HIGH/MED; one LOW (empty-code path) addressed with the `c and` guard @ 9419c2d8.

## Rollback (standing rule)
Roll back via GIT (revert prod-live + re-graft base + restart), NOT a naive backup restore (which diverges box from git-truth). The `~/pm_legaudit_graft_backup_<TS>` dir is audit trail / emergency source only.

## Runners (in cc)
`pm_legaudit_alias_graft.{ps1,sh}` (graft, Jack), `pm_legaudit_alias_scratch.{ps1,sh}` (RO box-scratch), `pm_cs2_legaudit_diag_ro.{ps1,sh}` (RO venue diag). Restarts: `restart_pmweb.ps1`, `restart_tc.ps1` (Jack, az-root).
