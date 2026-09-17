# Deploy manifest — leg-audit venue-verified code-alias (`ok:code_alias`)

**Branch:** `cs2-legaudit-lg-luminosity-2026-09-17` (code @ **9419c2d8**), off prod-live **d9468361**.
**Status:** BUILT · TESTED · BOX-SCRATCH VERIFIED · SKEPTIC-REVIEWED · ORDER-SAFETY CONFIRMED FROM THE BOX · COMMITTED · PUSHED.
**Structure: TWO independent authorizations. Deploy pm_web FIRST, verify green, THEN decide the engine bounce. HALT — graft/restart/FF are Jack's.**

---
## Ordering is a HARD coupling — confirmed safe from the box (not asserted)
The engine writes `ok:code_alias` only after ITS restart; pm_web's `classify_leg_audit` must already map it to CLEAN, or those rows fall to `UNEVALUATED` and surface as "could not check" — which the strip explicitly says is NOT a pass, i.e. WORSE than the `code_review` this replaces. So **pm_web first**.

Confirmed via `cc/pm_legaudit_classifier_parity_ro.*` (RO, live DB, box venv, 2026-09-17T19:15Z):
- **Every distinct verdict in the live DB classifies IDENTICALLY old-vs-new:** `None→legacy`(677), `ok→clean`(172), `na→clean`(155), `code_review:…LG!<Luminosity→soft`(2). `ALL existing verdicts old==new: True`. So the new classifier does NOTHING unexpected with existing verdicts while the engine is still on old code.
- `ok:code_alias` currently appears **0 times** (old engine never emits it). The ONLY behavioral delta is `old(ok:code_alias)=unevaluated → new=clean`.
- **No reverse dependency:** `leg_audit` is imported ONLY by `web/live_view.py` + `web/app.py` (pm_web); `live_driver.py imports leg_audit → NO`. The engine writer needs nothing from the classifier; the classifier only reads DB strings. The intermediate state (pm_web new, engine old) is stable indefinitely.

`live_driver.py` is an ENGINE file (writer); `leg_audit.py` is a PM_WEB file (classifier). **pm_web template needs NO change** — `ok:code_alias` is CLEAN, so it is never surfaced; no new label to render.

---
## AUTHORIZATION 1 — pm_web (graft `leg_audit.py` + restart `prediction-markets-web`)
Bounces pm_web ONLY; engine + 31 armed subs keep trading. Safe any time.
1. Graft (azureuser, drift-gated base d9468361; rolls back on mismatch):
   `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_legaudit_graft_pmweb.ps1"`
2. Restart pm_web only:
   `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\restart_pmweb.ps1"`
3. Phase-1 VERIFY (green before Phase 2): pm_web up; `/live` renders; the strip is UNCHANGED (the 2 existing `code_review:…LG!<Luminosity` still show as "code review"; no NEW "unevaluated" rows appeared). RO check: `cc/pm_legaudit_classifier_parity_ro.*` still shows all-identical + 0 `ok:code_alias` (none written yet — engine still old).

At this point you may sit indefinitely; nothing writes `ok:code_alias` until Authorization 2's engine restart.

---
## AUTHORIZATION 2 — engine (graft `live_driver.py` + restart `trading-corp`)
The expensive one: `restart_tc.ps1` **bounces ALL divisions** (MACE, PEAD, bitunix, coinbase, PM jack+karen…), ~3.5 min boot. **Time it clear of the 9:30 ET equity open** (PEAD co-tenant).
1. Graft (azureuser, drift-gated; **PRECONDITION: aborts unless leg_audit.py on box == target, i.e. Authorization 1 landed**):
   `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_legaudit_graft_engine.ps1"`
2. Restart engine:
   `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"`
3. Boot-verify (all divisions back, arm 31/0/0, 0 import errors); first-fill watch for an `ok:code_alias` row classifying clean. Then **FF push to prod-live**:
   `git push origin cs2-legaudit-lg-luminosity-2026-09-17:prod-live`

---
## STOP CONDITIONS (do not infer — act on these)
- **Phase-2 graft aborts "PM_WEB HALF NOT DEPLOYED":** Authorization 1 was not applied. HOLD. Complete Authorization 1 (graft + restart_pmweb + verify) first. Do not force the engine graft.
- **Phase-2 graft LANDED but pm_web is NOT confirmed on the new classifier:** HOLD — **do NOT run `restart_tc.ps1`.** Restore `live_driver.py` from `~/pm_legaudit_engine_backup_<TS>` (the engine has not restarted, so it never loaded the new file; the box returns to base = prod-live d9468361, no divergence). Then either fix/verify Authorization 1 or stand down.
- **Either graft aborts "BOX != BASE":** prod-live advanced past d9468361. Rebase the branch onto the new prod-live, recompute md5s, rebuild the `_legaudit_new_*.b64`, re-run. Do NOT graft onto a drifted base.
- **Any post-copy/py_compile failure:** the runner auto-rolls-back that file from its backup and aborts non-zero. Box is left consistent at base; do not restart.

## Files (drift-gated, LF md5)
| file | authorization / service | base (d9468361) | target (9419c2d8) |
|---|---|---|---|
| trading_corp/prediction_markets/leg_audit.py | 1 / pm_web (prediction-markets-web) | ec005466f1d18c364ca2597e185c855f | 61cfe84b094ab5b109452a18dc74f3bc |
| trading_corp/prediction_markets/live_driver.py | 2 / engine (trading-corp) | b9e67d1a7117191fa64b3e31290ec0f8 | 15069d88dd4d69aea3df9aaece74bfdb |

## What changed
`live_driver._LEG_AUDIT_CODE_ALIASES = {"lg":"luminosity"}` (venue-verified), consulted only when the name-family ordered-subsequence test fails; a hit → distinct verdict `ok:code_alias` (visible auto-clear, queryable), which `leg_audit.classify_leg_audit` maps to CLEAN. Seeded LG→luminosity only. Rejected the `canon(yes_sub_title)==canon(outcome)` shortcut (it re-derives the matcher's own bind → tautological gate; independence is the product). The value-specific anti-case (LG + outcome `NIP` stays `code_review`) is pinned by `test_code_alias_exact_verdict_and_anticase` — an alias can never blanket-pass a code onto the wrong side.

## Verification (box venv, live tree/engine/DB untouched, PIDs 458566/436431 unchanged throughout)
- Box-scratch differential: baseline **21 == overlaid 21** failures (0 new; the 21 are pre-existing pm_web render tests not runnable headless), set-diff EMPTY.
- `tests/prediction_markets/test_leg_audit_surface.py`: **11 passed** (incl. LG→Luminosity clean, anti-case LG+NIP→code_review, reader not-surfaced-but-queryable).
- Classifier parity over the live DB: all existing verdicts old==new; 0 `ok:code_alias` present; reverse-dep none.
- Skeptic review: no BLOCKER/HIGH/MED; the one LOW (empty-code path) fixed with the `c and` guard @ 9419c2d8.

## Rollback after deploy (standing rule)
Roll back via GIT (revert prod-live + re-graft base + restart), NOT a naive backup restore (which diverges box from git-truth). Backups `~/pm_legaudit_{pmweb,engine}_backup_<TS>` are audit trail / emergency source only (the pre-restart hold above is the one case a plain file restore is correct, because prod-live has not advanced).

## Runners (in cc)
Phase 1: `pm_legaudit_graft_pmweb.{ps1,sh}` + `restart_pmweb.ps1`. Phase 2: `pm_legaudit_graft_engine.{ps1,sh}` + `restart_tc.ps1`. RO: `pm_legaudit_classifier_parity_ro.*`, `pm_legaudit_alias_scratch.*`, `pm_cs2_legaudit_diag_ro.*`.
