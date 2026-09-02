# MULTICATEGORY BUILD — kalshi_jack/ufc (2026-09-02). Handoff, kept current per rung.

**Read this instead of scrollback.** Plan of record: `MULTICATEGORY_PLAN_2026-09-02.md @ e5d6506`. Branch
`pm-multicategory-2026-09-02` (worktree `C:\Users\AA Incorporado\cc-pm-multicat-wt`), base `e5d6506` (per-account
tip). Autonomous for build/test/box-scratch/review/commit/push/read-only runners; HALT for deploy, restart, live DB
write, arm, prod-live advance, or a ruling that is Jack's.

## ★★ LIVE STATE — NOTHING THIS BUILD TOUCHES THE BOX
- Two accounts ARMED + TRADING throughout: **kalshi_jack/mlb + kalshi_karen/mlb**, one engine, nobody monitoring.
  STOP (kills both): `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.
- I build ONLY in this local worktree + local venv (+ authorized box-scratch/read-only runners). **Zero** box
  deploys/restarts/DB-writes/arms. Last known engine PID **163519** (SW9). The only box touch this session is the
  board-approved read-only `pm_shards_ro.ps1` (16:47Z: jack shard0=$0.0081, karen shard0=$25.01). Box code = the
  per-account tip; my branch does not reach it until a deploy (HALT-for-Jack).

## ★ ORDER OF WORK (Jack's ruling)
A. Safety restructure FIRST (Option C + M1/M2/M3 + re-scopings) — touches live code, ships INDEPENDENTLY + INERTLY
   (mlb-only must be byte-identical to today). B. UFC matcher (greenfield, no live path until a ufc sub exists).
   C. Caps mechanism (HALT — Jack's ruling un-struck). D. Attach + arm (Jack's, not tonight).

## ★ CAPS MECHANISM RULING IS OPEN
Jack left the `<<<JACK — RULE HERE>>>` block un-struck. Recommendation of record: the ACCOUNT-LEVEL AGGREGATE CAP
(race-free under Option C's shared Journal; holds $150/day + 50 orders exactly; headroom flows to the busy
category) over the 75/75 divide (holds the ceiling but strands headroom on a quiet night). Aggregate stays
$150/50 either way. **C is built only after his strike; A and B do not need it.**

---

## RUNG A0 — THE THIRD ADVERSARIAL PASS (the stop gate) — DONE, does NOT trip the stop condition

**Method:** enumerated every variable in `live_driver.scheduled_pm_live_loop` (lines 481-682) that is either
initialized ONCE before the `while` (task-level, persists across cycles) or shared across the per-category work in
a cycle, and classified each: SAFETY (a check that would silently stop checking) vs FUNCTIONAL (needed to iterate
categories, but fails SAFE) vs CORRECT-TO-SHARE (an account-level resource).

| State (live_driver.py) | Scope today | Under one task, N categories | Class |
|---|---|---|---|
| `broker`/`client` (489) | per account | shared — one keypair/account | **CORRECT to share** |
| `shard_bal` (539,552) | per cycle | account-wide (all shards), read once | **CORRECT to share** |
| `venue_exp` (542,563) | per cycle | account-wide exposure, read once — M1's whole point | **CORRECT to share** |
| `journal` (585) | per cycle | account-keyed; shared enforces the account open-cap across categories | **CORRECT to share (M1 fix)** |
| `consec_err` (in run_live_arm_gated_cycle:367) | per-CALL | run_..._cycle is called once PER CATEGORY → own local each call | **#15 defended BY CONSTRUCTION (not shared)** |
| `consec_underfunded` (528) | task-level, cross-cycle | shared → an mlb placement resets ufc's starvation alarm | **#14 SAFETY → per-category dict** |
| `prior_snapshots` (533) | task-level, keyed by wallet | a wallet in both cats merges → wrong exits | **#16 SAFETY → key (category, wallet)** |
| `ctx` + `last_idx` (490,491) | task-level, MLB catalog | mlb vs ufc need DIFFERENT series catalogs | **#17 FUNCTIONAL (wrong catalog → skip:no_quote, fail-SAFE) → per-category** |
| `last_settle` (496,513) | task-level throttle | shared throttle → one cat's scan resets the other's cadence | **#18 FUNCTIONAL (delayed booking, fail-SAFE) → per-category** |
| `cycles` (527) | loop counter | harmless | share fine |

**RESULT — the stop condition does NOT trip.** The SAFETY re-scoping set (a safety check silently stopping) is
**TWO**: #14 (`consec_underfunded`) and #16 (`prior_snapshots`). **#15 (`consec_err`) is defended by construction**
— it is a local inside `run_live_arm_gated_cycle`, which is invoked once per category per cycle, so it never shares
across categories as long as the loop calls it per-category (which Option C does). So the safety set is smaller
than the three named, not larger.

**★ SAID PLAINLY (Jack asked):** the pass DID find two MORE per-category items — but they are **FUNCTIONAL, not
safety**: `ctx`/`last_idx` (the market catalog, which MUST be per-category so a ufc signal is matched against the
ufc series, not mlb) and `last_settle` (the settlement-scan throttle). Both FAIL SAFE if wrongly shared (a
ufc-vs-mlb catalog mismatch yields `skip:no_quote`/no-match → NO order; a shared throttle just delays a settled
position's booking). They are the mechanics of iterating categories — part of building the loop — NOT a hidden
fourth set of silent-safety degradations. So the *shape of the build is unchanged*: the safety re-scoping problem
is two items; the loop-mechanics re-scoping is two more, both fail-safe. I proceed; flagged for visibility.

**Convergence rule (Jack's "treat the list as OPEN"):** B6 re-runs this pass AFTER the restructure is written, as a
fresh adversarial read, and it must find NOTHING new (safety or functional) before M4. If B6 finds a further
SAFETY item, that would make the safety set > 3 → report and STOP.

---

## RUNG A/M2 — auth-latch all account categories — DONE @ `e2db6fd`, proven
- `live_driver.account_active_categories(conn, account_id, fallback_category=)` — every active category on the
  account from pm_subdivision, fail-SAFE to [fallback] (never [] / never fail-open). Auth-failure call site now
  passes the whole list to `arm.latch_auth_failure` (which already loops). No-op for one category/account.
- PROVED (local `.venv-webtest`, no pykalshi needed): `test_auth_failure_latches_ALL_account_categories` (a 403 on
  mlb's cycle latches BOTH mlb+ufc, both `manual_exit_required`), `test_account_active_categories_failsafe_and_union`
  (missing table -> [fallback]; active-only + fallback union). Existing single-account auth test still green.

## RUNG A/M3 — account-wide boot-reconcile latch — DONE @ `3e02c5c`, proven
- The comparison was ALREADY account-wide; only the latch was per-category (the deferred R-f note, now built).
  `reconcile_account(..., latch_categories=None)` loops the list on mismatch/read-fail (None -> [category],
  backward-compatible); `run_boot_reconcile` passes `account_active_categories(...)`. `ReconcileResult.
  latched_categories` makes the scope visible.
- PROVED: 4 unit tests (whole-account mismatch + read-failure latch ALL; clean two-category = NO false-latch, since
  a co-category position is in BOTH journal+book -> MATCH; default single-category preserved) + a run_boot_reconcile
  integration test. boot_reconcile suite 26/26; r7c boot/auth/helper 8/8.

## RUNG A/M1 — Option C one task per account + re-scopings — DONE @ `5d104a3`, proven locally
- `scheduled_pm_live_loop` is now per-ACCOUNT (`categories=[...]`; legacy `category=` still accepted). Per cycle:
  ONE account-level shard + venue-exposure read + ONE account-keyed `Journal`, SHARED across categories -> gate 6
  (open_usd, account-keyed) enforces the account cap JOINTLY (category B's evaluate sees category A's in-cycle
  commit) with NO lock, NOTHING on the order hot path; sequential categories never POST concurrently.
- Re-scopings: per-category ctx/last_idx (#17) via an INJECTABLE `CATEGORY_CTX_BUILDERS` seam (mlb registered; ufc
  = B); per-category `last_settle` (#18); per-category `consec_underfunded` ALARM (#14); `prior_snapshots` keyed
  `(category, wallet)` (#16). #15 `consec_err` is per-call -> defended by construction (confirmed in code).
- Boot: per-category settlement scan + ONE account-wide boot-reconcile force-latching ALL categories.
- `main.py` groups the guard-approved spawn BY ACCOUNT and passes `categories`. Guard (`plan_driver_tasks`) UNCHANGED
  -> today at most one category/account -> ONE task with `categories=[cat]` == byte-identical to the old wiring.
- PROVED (local `.venv-webtest`, no pykalshi -- via the injectable ctx builder + `_prior_snapshots` seam):
  `test_m1_shared_journal_caps_account_open_across_categories` (ufc capped by mlb's in-cycle open on a SHARED
  Journal; + the two-Journal RACE demo -> both place -> account over the cap); `test_m1_loop_mlb_only_..._both_param_
  forms` (byte-identical mlb-only, disarmed, 0 orders); `test_m1_prior_snapshots_keyed_by_category_wallet` (#16);
  `test_m1_underfunded_alarm_is_per_category` (#14, mlb AND ufc each alarm). Full PM suite: **16 env-gap failures
  unchanged** (pykalshi live-path + stale schema_head_is_15); every new test passes.
- ★ REMAINING PROOF (deploy gate, NOT local): byte-identical mlb-only on the REAL venv = the pykalshi-path
  scheduled_loop / kill_switch / shard_gate tests still green under box-scratch after the restructure. They cannot
  run locally (no pykalshi). Run box-scratch on the A+B bundle before the deploy queue.

## RUNG A-proof — byte-identical mlb-only — LOCAL done (disarmed one-task test); BOX-SCRATCH gate pending (pykalshi path)

## RUNG B — UFC matcher + category dispatch — IN PROGRESS (greenfield)
Discovery landed: UFC = 2 binary types -- moneyline `KXUFCFIGHT-{YYMONDD}{FTR1}{FTR2}-{FTR}` (one market per fighter)
+ go-the-distance `KXUFCDISTANCE-{YYMONDD}{FTR1}{FTR2}` (no line). Polymarket: winner slug + `-go-the-distance`.
Build `ufc_poly_kalshi_match.py` mirroring the MLB matcher surface + fighter-name canonicalization (the doubleheader
analog = the 3-char ticker-abbreviation collision, e.g. two fighters sharing the first 3 last-name letters on one
card -> a MISS, never a wrong pick). Then category-dispatch `evaluate` (registry by `sub.category`) + register the
ufc ctx builder in `CATEGORY_CTX_BUILDERS`. INERT until a ufc sub-division exists. ★ needs a disarmed live probe of
real KXUFCFIGHT/KXUFCDISTANCE tickers + real Poly ufc slugs to build canonicalization against real names.

## RUNG A-proof — byte-identical mlb-only behaviour — PENDING

## RUNG B — UFC matcher + category dispatch — PENDING

## RUNG C — caps mechanism — PENDING (HALT for Jack's ruling)

---

## DECISIONS I MADE (could have gone another way)
- Built off `e5d6506` (per-account tip == box main.py `9e8da82` CR-stripped), so every graft is a clean file-by-file
  diff off what the box runs.
- **M1 is a WIRING change, not an evaluate/Journal change.** I kept gate 6 / the Journal / the POST path untouched
  and achieved the joint account-cap purely by SHARING one per-cycle Journal across categories in the restructured
  loop. Alternative was a new account-scoped gate or a lock; both were rejected (the shared Journal already
  account-keys open_usd, so it is free + off the hot path).
- **Introduced an injectable `CATEGORY_CTX_BUILDERS` seam + a `_prior_snapshots` test seam.** This let me prove the
  WHOLE loop restructure locally WITHOUT pykalshi (a fake builder returns a canned MarketContext), instead of relying
  only on box-scratch. It is also the exact seam Workstream B uses to register the ufc catalog builder.
- **M2/M3 both route through one helper `account_active_categories` (fail-safe to [fallback]).** One place to reason
  about "every category on the account", used by both the auth-latch site and run_boot_reconcile.
- **Kept the guard (`plan_driver_tasks`) unchanged in M1** so Option C ships INERTLY (one category/account today ->
  byte-identical). The guard relaxation is M4's per-account opt-in, deliberately last.

## WHAT I FOUND THAT NOBODY ASKED ABOUT
- (A0) `consec_err` (#15) is NOT a real re-scoping — it is a per-CALL local inside run_live_arm_gated_cycle (invoked
  once per category), so it never shares across categories. The plan listed THREE safety re-scopings; on building,
  the SAFETY set is TWO (#14 alarm, #16 snapshots). The other two per-category items (#17 ctx/last_idx, #18
  last_settle) are FUNCTIONAL and fail-SAFE (a wrong catalog -> skip:no_quote; a shared throttle -> delayed booking).
  So the safety re-scoping problem is NOT larger than three -> Jack's stop condition does not trip. (Honest correction
  the build surfaced; documented in A0.)
- The pykalshi-path scheduled_loop tests fail LOCALLY as ASSERTIONS (placed 0), not import errors, because the loop
  swallows the pykalshi ImportError at the ctx-build and skips the category (fail-safe). Same failure mode before/
  after my change; they are byte-identical-provable only on the box (pykalshi present). Flagged as the deploy gate.

## DEPLOY QUEUE (authorize one rung at a time; nothing built here is deployed; box-is-truth, GRAFT main.py never wholesale)
- **A-bundle (engine; ONE restart) — HALT.** Files changed vs the box (`e5d6506` base == box):
  `trading_corp/prediction_markets/live_driver.py` (M1 loop + M2 helper/call-site + M3 run_boot_reconcile),
  `trading_corp/prediction_markets/boot_reconcile.py` (M3 latch_categories), `trading_corp/prediction_markets/
  arm.py` UNCHANGED (latch_auth_failure already looped), `trading_corp/main.py` (GRAFT the account-grouping hunk;
  NEVER wholesale). Import closure: live_driver imports arm/boot_reconcile/execution/settlement/shard_balance/
  venue_exposure/paper + the mlb matcher (all already on the box). NO new module in A (ufc matcher is B). NO
  migration. Gate-A = py_compile + import-closure + the box-scratch PM suite (`-p no:pytest_ethereum`), which MUST
  show the pykalshi-path scheduled_loop/kill_switch/shard_gate tests GREEN (byte-identical mlb-only on the real
  venv) + all the new M1/M2/M3 tests green. Post-check after restart: roster log `N account task(s): {jack:[mlb],
  karen:[mlb]}` (ONE task per account), both boot-reconciled clean, jack+karen still armed+trading unchanged.
  Stop: any behavior change to the live mlb path, or a pykalshi-path test red on box-scratch -> do not restart.
- **B-bundle (ufc matcher + dispatch) — HALT.** Adds `trading_corp/data/ufc_poly_kalshi_match.py` (NEW) + evaluate
  dispatch + the ufc ctx-builder registration. INERT (no ufc sub-division yet). (fill shas when built.)
- **C (caps mechanism) — HALT for Jack's ruling** (account-level cap vs 75/75 divide).
- **Enablement (Jack's, not tonight): fund shard 0 -> M4 opt-in + create (kalshi_jack,ufc) + attach whales -> restart
  -> arm ufc with PLACE-ONE-AND-INSPECT.**
