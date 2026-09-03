# BUNDLE DEPLOY — B2 + R1 + R2 (2026-09-03). HALT FOR JACK. Deploy is Jack's.

Branch `pm-multicategory-2026-09-02` @ `693666e` (pushed). Box-scratch PASSED read-only 2026-09-03 (`cc\pm_bundle_
scratch.{ps1,sh}`; engine PID **171106 UNTOUCHED** throughout; scratch overlay, the live tree was never modified).

## ★★ TOP: THE RESTART BOUNCES EVERYTHING -- BITUNIX INCLUDED. WARN BITUNIX FIRST.
This bundle needs an ENGINE restart (`restart_tc.ps1` -> `systemctl restart trading-corp`), so ALL divisions bounce:
**bitunix, MACE, PEAD, IC, tasty, the Kalshi strategies, AND the PM driver.** Time it accordingly; warn bitunix.

## WHAT THIS BUNDLE IS (and is NOT)
- **B2** = per-category matcher dispatch at the chokepoint. INERT: no ufc sub-division exists, so `evaluate` still uses
  the MLB adapter for the mlb category -> **byte-identical to today**. **R1** = opposed-guard logging (WARN new /
  DEBUG memory-suppressed / honest wording). **R2** = decision-keyed opposed memory (`pm_opposed_marker`, migration
  018) + the loud UN-FLATTENED-CONTESTED-POSITION ERROR instrumentation. Order-path-adjacent; tolerant of a
  pre-migration schema.
- It does NOT touch pm_web, main.py, app.py, boot_reconcile.py. It does NOT create a ufc sub-division or arm anything.

## BOX-SCRATCH VERDICT (read-only)
- **Reconcile CLEAN:** box pre-overlay hashes == expected -> the overlay is a verified clean superset:
  `execution.py bc806bc4`(==e5d6506 base), `live_driver.py 4b85f93f`(==A deploy), `db.py 46e612f1`(==loss-omission
  17), `boot_reconcile ecce7777`, `main.py bba046e8`; ufc matcher ABSENT.
- **PROOF A -- MLB BYTE-IDENTICAL: PASS.** Engine/schema tests GREEN on the box venv (`-p no:pytest_ethereum`) --
  `test_live_driver_r7c` (place_fn/mid-cycle-kill/whale-exit/M1/R1/R2), `kill_switch_r7d`, `boot_reconcile_r55`,
  `b2_dispatch`, `opposing_close_r5`, `venue_exposure_r7`, `ufc_match`, `search_r1`+`shard_snapshot_m3` (head 18),
  etc. -- EXCEPT the 2 PRE-EXISTING `shard_gate_r2` FakeClient fixtures (`test_driver_places_when_market_shard_funded`
  + `..sustained_underfunding..`); their FakeClient returns `unexpected get path '/portfolio/positions'` so R7's venue
  read fails -> documented in the A-proof a4 as failing IDENTICALLY on the un-overlaid e5d6506; NOT this change. (WEB
  tests were EXCLUDED: this branch is e5d6506-era on `web/` while the box has the newer loss-omission+UI web deployed,
  so branch web tests mismatch the box web -- a branch-behind-on-web divergence, not this bundle.)
- **CREATE-SQL COMPARE: IDENTICAL.** The back-ported `pm_loss_grounding_cache` CREATE SQL == the box's LIVE table
  (byte-for-byte, normalized). The back-port produces the table already live.
- **PROOF B -- DISARMED UFC DRY-RUN vs real live cards: SAFE.** 81 synth bets (Kalshi 19 fights/2 dates x Poly 27
  events). **0 WRONG-MARKET-TYPE. 0 REAL wrong-picks** -- the 4 "wrong-fight" flags are FALSE POSITIVES in the check,
  not matcher errors: in each the matched ticker is the CORRECT fighter (e.g. a Parnasse bet -> Parnasse's ticker),
  and the flag fired only because the OPPONENT'S name differs between venues (Kalshi "Daniel Hooker" vs Poly "Dan
  Hooker"; accent-strip "Charriere"/"charri re"). Miss profile = CAUTION not a bug: `out_of_window` 36 (fights beyond
  the 2 fetched Kalshi dates), `abbrev_collision_ambiguous` 14 (my SYNTH distance bets omitted the fighter hint the
  real path supplies -> the matcher correctly refuses to guess), `winner_outcome_unresolved` 4 (name-form: Dan vs
  Daniel), `no_kalshi_contract` 1. **The matcher NEVER mis-picks -- it is conservative.** ★ It surfaced a
  NAME-NORMALIZATION gap (short/full first names + accented chars) that would UNDER-match on real data -- a **go-live
  refinement BEFORE any ufc attach/arm**, and it does NOT affect this INERT mlb-only deploy (no ufc sub exists).

## ★ SCOPE UPDATE 2026-09-03 15:30: BUNDLE NOW = B2 + R1 + R2 + **M4 + C** (all box-scratch PASSED together @ tip)
Built in the pre-deploy window on THIS branch (droppable -- each is its own commit). **M4** = relax the guard behind a
FAIL-CLOSED per-account opt-in (`pm_account.multi_category_ok`, migration 019, DDL DEFAULT 0). **C** = the
account-level aggregate cap (gate 5b/8b, $150/day + 50 orders across categories, config-in-code, race-free on the
shared Journal). Both are INERT today (no account is opted in, no 2nd category exists) -> byte-identical to today.
**FALLBACK (kept open):** if either weren't clean they'd be reverted and the B2+R1+R2 bundle ships alone; they ARE
clean, so all five ride ONE restart. Re-proven box-scratch (whole bundle @ tip `e96fdf8`): reconcile clean incl.
`driver_roster.py 802c9a82`(==e5d6506); Proof A engine-only = ONLY the 2 pre-existing shard_gate FakeClient failures
(test_m4_optin / test_account_cap_c / test_per_account_driver_n2 GREEN); CREATE-SQL IDENTICAL (scratch head 19, box
17); UFC dry-run unchanged; engine PID untouched.

## MANIFEST (box-is-truth; hashes CR-stripped/LF; tip `e96fdf8`)
| File | Box now | Target (HEAD) | ACTION |
|---|---|---|---|
| `trading_corp/prediction_markets/db.py` | `46e612f1` (loss-om 17) | `5342ad98` (+018,+019) | **★ GRAFT** -- add `MIGRATION_018` + `MIGRATION_019` blocks + both tuples ONLY (box has 017; NEVER wholesale my db.py) |
| `trading_corp/prediction_markets/execution.py` | `bc806bc4` (==e5d6506 base) | `b25984d0` | WHOLESALE (box == base, verified; target = base + B2/R2/**C**) |
| `trading_corp/prediction_markets/live_driver.py` | `4b85f93f` (==A deploy) | `6c20891e` | WHOLESALE (box == A, verified; target = A + B2/R1/R2) |
| `trading_corp/prediction_markets/driver_roster.py` | `802c9a82` (==e5d6506 base) | `0277fa5c` | WHOLESALE (box == base, verified; target = base + **M4**) |
| `trading_corp/data/ufc_poly_kalshi_match.py` | ABSENT | `2fa2166b` | ADD (new, pure/stdlib) |
| `main.py` / `boot_reconcile.py` / `app.py` / pm_web | unchanged | -- | NOT shipped (main.py already groups spawn by account -> M4 needs no main.py edit) |

Import closure: execution.py adds `from ..data import ufc_poly_kalshi_match as U` (the NEW file -> ship it); live_driver
adds the same import; both already on the box otherwise. NO other new imports.

## MIGRATION ORDERING (migration LEADS the code)
1. **Gate-1:** BACKUP `data/prediction_markets.db` (+ wal/shm) and `PRAGMA integrity_check` -> must be `ok` FIRST.
   Also back up the 2 code files + db.py to `~/pm_bundle_backup_$TS`.
2. **Pre-check:** the 4 box hashes == the manifest's "Box now" (abort writing nothing on any drift).
3. **Migration FIRST:** graft `db.py` (add BOTH `MIGRATION_018` and `MIGRATION_019` blocks + their tuples), then RUN
   `init_db` explicitly (`venv/bin/python -c "from trading_corp.prediction_markets import db; db.init_db()"`) -> applies
   018 THEN 019 in order. VERIFY: schema head **19**; `pm_opposed_marker` present (CREATE SQL == manifest);
   `pm_account.multi_category_ok` column present with **DEFAULT 0** (`PRAGMA table_info(pm_account)`); and ALL existing
   `pm_account` rows read `multi_category_ok=0` (the guard stays CLOSED -- no account is opted in).
4. **Then the code:** wholesale `execution.py` + `live_driver.py` + `driver_roster.py`, ADD `ufc_poly_kalshi_match.py`
   (.tmp -> sha-verify -> mv).
5. **Gate-A:** `py_compile` all 3 + `python -c "import trading_corp.prediction_markets.live_driver, .execution,
   trading_corp.data.ufc_poly_kalshi_match"` on the box venv. RESTORE on any failure; do NOT restart.
6. **Restart** (Jack, warned bitunix): `restart_tc.ps1`.

Tolerance note (the 014 lesson): the engine READ/WRITE of `pm_opposed_marker` both guard on `_table_exists` -> if the
code somehow ran before the migration it degrades to opposed-close-only, no crash. pm_web NEVER reads the table
(grep-confirmed). Migration still leads.

## POST-CHECK (read-only)
- schema head **19**; `pm_opposed_marker` present (EMPTY); `pm_account.multi_category_ok` present.
- **★ THE GUARD IS STILL CLOSED (M4 default OFF):** every `pm_account.multi_category_ok = 0` -> the roster log must
  read **exactly 2 SINGLE-category tasks {kalshi_jack:[mlb], kalshi_karen:[mlb]}** -- NEITHER account has a 2nd
  category, NEITHER is opted in. If the roster shows any account with >1 category, or >2 tasks, M4 opened the guard
  when it must not -> STOP + rollback. (C is inert too: no 2nd category exists, so gate 5b/8b bind at the same $150/50
  as gate 5/8 -> byte-identical.)
- Roster log: **2 account task(s) {kalshi_jack:[mlb], kalshi_karen:[mlb]}** -- Option C unchanged, B2 invisible.
- Arm rows PERSISTED + ts BYTE-UNCHANGED, both armed latched=False (global 08-31T02:35:38 / jack 08-31T21:49:39 /
  karen 09-02T12:53:23). NOT a status call.
- Boot-reconcile CLEAN both (`reconciled=True latched=False latched_categories=()`).
- 0 `skip:exposure_unknown` storm. 0 `UN-FLATTENED CONTESTED POSITION` ERROR (none is expected -- no held-but-
  uncloseable contest right now). Guard now DEBUGs memory re-suppression (no more per-cycle WARN spam).
- Order counts move ONLY on the engine's own trading; the deploy places nothing.

## STOP CONDITIONS
- Pre-check drift (a box hash != manifest) -> ABORT, write nothing, investigate.
- `integrity_check` != ok, or init_db doesn't reach 18, or `pm_opposed_marker` absent/CREATE-SQL mismatch -> restore
  the DB backup + db.py, do NOT proceed.
- Gate-A import/compile failure -> restore all, do NOT restart.
- Post-restart: >2 tasks or a task with >1 category, or an arm ts CHANGED, or a boot-reconcile LATCH, or a
  `skip:exposure_unknown` storm, or an unexpected `UN-FLATTENED CONTESTED POSITION` ERROR -> restore
  `~/pm_bundle_backup_$TS` + restart to revert.
- Global STOP throughout: `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.

## ROLLBACK
Restore the code + db.py from `~/pm_bundle_backup_$TS` + restart. Migration 018 is additive (an EMPTY table nothing
reads once the code is reverted); the DB backup is the belt-and-suspenders if a schema revert is wanted.

## FOLLOW-UP (NOT this deploy): UFC name-normalization before any ufc attach/arm
The dry-run proved the matcher is safe but conservative; Poly<->Kalshi name-form differences (Dan/Daniel; accent
stripping in `_norm`) would under-match. Add a fighter-name normalizer (first-name aliases + accent-fold instead of
accent-to-space) BEFORE attaching a ufc whale. This is a matcher refinement, orthogonal to this inert deploy.
