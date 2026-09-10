# Leg-independence fix — DEPLOY MANIFEST (2026-09-10). BUILT + TESTED + ADVERSARIAL-HARDENED. NOT DEPLOYED.

**Branch `pm-leg-independence-fix-2026-09-10` @ `4114908`** (base `9a1783d` = origin/prod-live). Reserved for
Jack: DEPLOY / PUSH / RESTART / any live-DB write (incl. the migration) / arm / disarm. Nothing here has reached
the running order path.

## What it fixes (the CLASS, not the two instances)
The audit found no matcher had a unit test and every pre-arming dry-run gate was tautological (expected side = the
matcher's own output). Three rungs:
- **Rung 1** — first independent per-matcher tests: hardcoded expected `(ticker, leg)` derived from the ticker's own
  `-CODE` + Poly outcome semantics, sharing no transform with the matcher. cs2/tennis/ufc + soccer Yes/No + nfl
  total/spread. The swap tests are RED on the box, GREEN on the fix.
- **Rung 2** — the cs2/tennis/ufc org/side bug (magic→FaZe class): back-port FED's independent code-vs-label check.
  A **bipartite swap detector** (`labels_code_swapped` in ufc_poly_kalshi_match.py) scores how well each side's
  ticker `-CODE` abbreviates each side's label and REFUSES the market when the swapped assignment scores strictly
  higher — fail-closed, accent-folded. Wired into cs2/tennis/ufc `_resolve_side`/`_resolve_winner_side`.
- **Rung 3** — observability. Migration 021 adds `signal_outcome`, `signal_slug`, `leg_audit` to
  pm_subdivision_order; `_record_order` persists the copy INTENT + an INDEPENDENT post-fill leg re-derivation
  (`_audit_leg_independent`) — the no-leg-lens net (previously `Decision.leg` was NEVER re-derived downstream).

## Adversarial review (2 skeptics) — both findings fixed, re-tested
- **BLOCKER (fixed):** the first asymmetric guard was defeated by short/common wrong codes (TL⊆"vitality"). Replaced
  with the symmetric bipartite check. New red-green test `test_cs2_short_code_swap_vitality_must_NOT_buy_TL`.
- **HIGH (fixed):** accent false-positive ("Borna Ćorić"/COR dropped). Now accent-folds. Test `test_tennis_accented_name_correct_kept`.
- **HIGH (deploy ordering — reflected below):** PM migrations do NOT run on engine restart; they need an explicit
  `init_db`. So the migration must be applied BEFORE the restart or the new INSERT hits "no such column".

## GRAFT SET — 5 files, box(base) CR-sha → mine CR-sha (ALL azureuser-writable; standard channel, no az-root)
| file | box (base) | mine |
|---|---|---|
| trading_corp/data/cs2_poly_kalshi_match.py | e0063823e2dba1e4 | **359fa79f95314e38** |
| trading_corp/data/tennis_poly_kalshi_match.py | c91da4355f6b4f11 | **1bfe16a275dac73f** |
| trading_corp/data/ufc_poly_kalshi_match.py | e5263328c30b5616 | **1a16b3aa58d2f5b1** |
| trading_corp/prediction_markets/db.py | 39b5ac8eb3d1da71 | **3b5ae50d68c16551** |
| trading_corp/prediction_markets/live_driver.py | 430a6f70d934d5f9 | **d0c25810feed3c94** |
Runner `cc/pm_legfix_graft.ps1` (mirrors pm_sprtot_graft): pre-verify each box==base + staged==mine + **writable**
(ABORT on any drift) → backup-all → LF-write-all → post-verify==mine → box-venv import smoke → engine PID UNCHANGED
(NO restart in the runner). NO app.py / main.py / pm_web.

## ★ DEPLOY ORDERING (mandatory — the migration LEADS the restart)
1. **Drift-check** the live PM DB schema head == **20** (confirmed 2026-09-10). If it moved (pm-ui-rewrite shipped a
   021 first), RENUMBER MIGRATION_021 → box-head+1 and rebuild (a MAX(version) counter SILENTLY SKIPS a collision).
2. **Apply migration 021** to the live PM DB via the sanctioned PM `init_db` (pm_cli / your migration channel) — a
   live-DB write, Jack's. Verify head → **21** and the 3 columns present. (Additive + nullable; the running old
   engine is unaffected.)
3. **Graft** the 5 files (`pm_legfix_graft.ps1`) — code on disk, engine still on old code.
4. **Restart** the engine. init_db is NOT the trigger on the box, so step 2 MUST already be done: if the new
   live_driver runs before the columns exist, `_record_order`'s INSERT throws "no such column" — FAIL-SAFE (the
   PENDING insert precedes `place_fn`, so no phantom Kalshi order) but the cycle loops and copying is bricked until
   the migration is applied.

## Behavioural impact (NOT inert, but zero-impact on legit flow)
The matcher guard changes behaviour ONLY on a MISLABELED market — verified **0 of cs2(1555)/atp/wta(502)/ufc(25)**
two-sided live events flagged, so normal copying is unchanged; on a real mislabel it fail-closes (safe miss). The
observability adds columns + a WARN log on an unambiguous leg inversion. moneyline (mlb/structural), soccer, fed,
go-the-distance, and structural total/spread paths are UNTOUCHED.

## POST-CHECK (after migration + graft + restart)
- schema head == 21; pm_subdivision_order has signal_outcome/signal_slug/leg_audit.
- engine restarted (new PID), boot-reconcile both accounts, liveness 30/30, 0 PM tb, MACE alive.
- the fix is live: a mislabeled-market fixture refused (import smoke re-runs the swap assertions on the box tree).
- the FIRST real fill after deploy carries signal_outcome + signal_slug + a leg_audit verdict; grep the journal for
  `leg_audit REVIEW` (should be none on correct fills); `cc/pm_fill_watch_ro` continues to read back strike/type/team.

## STOP CONDITIONS / rollback
- Any graft pre-verify mismatch → runner ABORTS, nothing written.
- Rollback = restore the 5 files from the runner's backup + restart. Migration 021 is additive (leave the columns;
  old code ignores them — no down-migration needed).
- If `leg_audit REVIEW` fires on a real fill post-deploy → read it back by hand; it is advisory (never blocks).

## RESIDUAL (documented, accepted)
- Guard is fail-OPEN at a TIE (a mislabel where the wrong code scores EQUAL to the correct is not refused). Backstops:
  Kalshi mislabels are rare; the Rung-3 leg_audit persists every fill for review. Closing ties fully needs a
  code→name map (out of scope).
- Observability is currently WRITE-ONLY: signal_outcome/leg_audit are persisted + inversions WARN-logged, but nothing
  READS them yet — the "compare intent vs the whale's settled side" reconcile is enabled but not built. Follow-up:
  extend `cc/pm_fill_watch_ro` (or a tile) to surface `leg_audit` REVIEW rows and the signal-vs-settlement delta.
- soccer/mlb "signal-time divergence" (the 188-unverifiable root) is now AUDITABLE (intent persisted) but not yet
  auto-diagnosed.

## prod-live fold (on deploy, Jack): fold these 5 files + migration 021 onto prod-live (base 9a1783d), three-way
prove (box==prod-live==branch CR-stripped for the 5 files), ledger, FF push — same session as the deploy.
