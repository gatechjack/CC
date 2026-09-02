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

## RUNG A-proof — byte-identical mlb-only — ★ PROVEN ON THE REAL VENV (box-scratch, 2026-09-02 ~22:0xZ)
Read-only box-scratch (rsync the live tree to `~/pm_multicat_scratch_*`, overlay this branch's files, run the PM
suite on the box venv with `-p no:pytest_ethereum`; **live engine PID 163519 NRestarts=0 active — UNTOUCHED**;
scratch dirs cleaned up after). Runners: `cc\pm_scratch_a{,2,3,4}.{sh,ps1}`.
- **The pykalshi-path tests that never run locally PASS on the box** with Option C: `test_live_driver_r7c` (incl. the
  scheduled_loop tests + my new M1/M2), `test_kill_switch_r7d`, `test_boot_reconcile_r55` (incl. M3),
  `test_ufc_match`, `test_venue_exposure_r7`, `test_optiond_r1`, `test_idempotency_r7h`, `test_disarm_r7i`,
  `test_arm_r5`, `test_per_account_driver_n2`, etc. — all green. (The box's own `tests/` dir is stale/partial, so a3
  replaced `tests/prediction_markets/` with THIS branch's via `git archive`.)
- **Only 4 failures, ALL pre-existing / not-my-change (classified, evidence not assertion):**
  - `test_search_r1::test_schema_head_is_15` + `test_shard_snapshot_m3::…head_is_16` — HARDCODED stale schema
    constants; live `SCHEMA_HEAD=17, n_migrations=17` (moved 15→16 multi-acct →17 loss-omission). Just stale.
  - `test_shard_gate_r2::test_driver_places_when_market_shard_funded` + `::…sustained_underfunding_alarm…` — their
    `FakeClient` RAISES on `/portfolio/positions` (never mocked for R7's venue read, which shipped at my BASE
    e5d6506) → gate 6 fails-closed `exposure_unknown` before gate 6b. **a4 PROVED these fail IDENTICALLY on the box's
    un-overlaid e5d6506 `live_driver` (same old line numbers 511/565/382)** → pre-existing stale fixture, NOT my
    regression. Production venue-read behavior is correctly covered by `test_venue_exposure_r7` (green).
- **Verdict: on the real venv Option C is byte-identical for the mlb-only path.** Not a real finding → proceed to
  prepare the deploy.

## RUNG B(core) — ufc_poly_kalshi_match.py — DONE @ `07c65a2`, 43 tests green (built by a Sonnet agent, reviewed)
- Pure/stdlib matcher, 2 binary types: moneyline `KXUFCFIGHT-{YYMONDD}{K1}{K2}-{FTR}` + go-the-distance
  `KXUFCDISTANCE-{...}-DIST`. Real tickers/slugs probed from the Kalshi public API + Polymarket (2026-09-02).
- The JOIN (honest crux the agent surfaced): the **Polymarket slug codes are OPAQUE** (`dan6`, `salpar`) and do NOT
  map to the Kalshi kcodes, so the match is driven by the Poly **outcome (fighter FULL NAME)** vs the Kalshi market
  **`title`** (`"{Full Name} wins"`). kcode = `upper(last_name[:3])` (first-name fallback when last<3). Exact match
  only; carry (ticker, leg) on MatchResult; MISS on ambiguity.
- KNOWN unresolvable cases as MISS tests (Jack's "show what it can't resolve"): 3-char abbrev COLLISION (two same
  first-3-last-name fighters on one card -- kcode ambiguous; SYNTHETIC test, agent could not find a real same-card
  collision), no-distance-ticker-for-bout, ambiguous-date-without-a-fighter-hint, opaque-Poly-slug.
- ★★ INTEGRATION GAP found in REVIEW (this is rung B2, NOT built -- a careful live-code change, deliberately not
  rushed at context depth):
  1. **`title` is not on the live path.** `build_kalshi_fight_index` reads `mkt.get("title")`, but
     `live_driver._market_quote_dict` carries only quotes+exchange_index, NO title. B2 must add `title` to the UFC
     ctx builder's market dicts (additive; MLB ignores it).
  2. **UFC needs its own MarketContext shape.** `execution.MarketContext` is MLB-shaped (moneyline/total/spread
     indices). UFC needs a fight index + distance index. B2 introduces a per-category context + a uniform matcher
     adapter.
  3. **evaluate must category-dispatch.** Today `evaluate` hardwires `M.parse_poly_mlb_bet` / `M.match_bet(...)`.
     B2 adds a registry `{"mlb": mlb_adapter, "ufc": ufc_adapter}` where each adapter exposes `parse(slug,outcome)`
     + `match(parsed, ctx, allowed_market_types) -> MatchResult` with the uniform fields evaluate reads
     (`.status/.kalshi_ticker/.leg/.market_type/.reason`). evaluate picks by `sub.category`; gates/sizing stay
     category-agnostic. The ufc ctx builder registers into `CATEGORY_CTX_BUILDERS` (the M1 seam) and builds the
     fight+distance indices carrying `title`.
  B2 is INERT (no ufc sub-division exists) but it DOES touch `execution.py` (the chokepoint) + `live_driver.py`, so
  it needs the same care + box-scratch as A. Recommended: build B2, then run ONE box-scratch validating A+B on the
  real venv (the byte-identical mlb-only gate + a disarmed ufc dry-run against live UFC market data).

## RUNG B2 — dispatch integration (title + UFC context + evaluate registry) — NOT BUILT (scoped above)

## RUNG B(old placeholder) — superseded by B(core)+B2 above
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
### ★ A-DEPLOY (engine graft + ONE restart) — PREPARED + STAGED + PATCH-VERIFIED, NOT RUN — HALT for the board
- **★ THE RESTART BOUNCES EVERYTHING.** A needs an ENGINE restart (`restart_tc.ps1` -> `systemctl restart
  trading-corp`), so ALL divisions bounce: **bitunix, MACE, PEAD, IC, tasty, the Kalshi strategies, AND the PM
  driver.** Time it accordingly. Box-scratch already proved A on the real venv; the restart is the only live step.
- **MANIFEST = THE IMPORT CLOSURE, not the diff** (3 files; every import already on the box; NO new module -- the
  ufc matcher is B, NOT A; NO migration). Hashes CR-STRIPPED both sides (`tr -d '\r'|sha256sum`), never raw `git show`:
  - `trading_corp/prediction_markets/live_driver.py` — WHOLESALE (box `a99139832970fd61` ==e5d6506 no-drift ->
    target `4b85f93f0bb20fd8`). Imports arm/boot_reconcile/db/execution/paper/settlement/shard_balance/
    venue_exposure + mlb matcher + kalshi_live — ALL on the box; my change added NO new import.
  - `trading_corp/prediction_markets/boot_reconcile.py` — WHOLESALE (box `dc9bbc9f89c29a5e` ==e5d6506 ->
    `ecce77770f951f74`). Imports arm + stdlib.
  - `trading_corp/main.py` — ★ GRAFT, NEVER wholesale (the box carries the per-account roster). box
    `9e8da82de3b8bfcf` (== e5d6506:main.py CR-stripped, verified) -> `bba046e8f1ce9801`. 20-line hunk; `patch -p1`
    VERIFIED locally to apply to base==box and yield exactly the target (LF patch + LF box + `tr -d '\r'` -> no CRLF
    failure like the overnight one).
  - `arm.py` UNCHANGED (M2 reused the existing looping `latch_auth_failure`).
- **STAGED RUNNER (authored + validated; NOT run): `cc\pm_a_deploy.{sh,ps1}`.** Pre-checks the 3 box pre-state hashes
  (ABORTS writing nothing on any drift); backs up all 3 to `~/pm_a_deploy_backup_$TS`; wholesale-writes the 2 package
  files (.tmp -> sha-verify -> mv); patch-grafts main.py + content-verifies (`_pm_by_account` + `categories=_acats`
  present, old `category=_t["category"]` spawn GONE) + sha-verifies target; Gate-A = py_compile all 3 + `import
  live_driver, boot_reconcile` on the real venv; RESTORES from backup on ANY failure. **Does NOT restart.** One-liner:
  `powershell -ep bypass -f .\pm_a_deploy.ps1`.
- **THEN (separate board steps):** `restart_tc.ps1`, then the post-check (`cc\pm_arm_persisted_ro.ps1` + a roster-log read).
- **POST-CHECK HEADLINE = NOTHING CHANGED** (Option C is invisible until a 2nd category exists):
  1. Roster log `2 account task(s): {'kalshi_jack': ['mlb'], 'kalshi_karen': ['mlb']}` — ONE task PER ACCOUNT, each
     the SINGLE category [mlb] (byte-identical to the old per-(account,category) wiring).
  2. Both accounts STILL ARMED, **persisted ts UNCHANGED** — read PERSISTED rows (`pm_arm_persisted_ro.ps1`), NOT a
     status call (the mode=ro fail-safe read a false disarm 3x): global `2026-08-31T02:35:38` / jack
     `2026-08-31T21:49:39` / karen `2026-09-02T12:53:23` must be byte-identical.
  3. Boot-reconcile CLEAN for BOTH (`reconciled=True latched=False latched_categories=()`).
  4. Order counts move ONLY for legit engine fills (the graft placed nothing). 0 skip:exposure_unknown storm.
- **STOP:** pre-check drift -> aborts writing nothing (investigate). Post-restart: >1 task/account, or a task with
  >1 category, or an arm ts CHANGED, or a boot-reconcile LATCH, or a skip:exposure_unknown storm -> restore
  `~/pm_a_deploy_backup_$TS` + restart to revert. Global STOP throughout:
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`.
- **B-bundle (ufc matcher + dispatch) — HALT.** Adds `trading_corp/data/ufc_poly_kalshi_match.py` (NEW) + evaluate
  dispatch + the ufc ctx-builder registration. INERT (no ufc sub-division yet). (fill shas when built.)
- **C (caps mechanism) — HALT for Jack's ruling** (account-level cap vs 75/75 divide).
- **Enablement (Jack's, not tonight): fund shard 0 -> M4 opt-in + create (kalshi_jack,ufc) + attach whales -> restart
  -> arm ufc with PLACE-ONE-AND-INSPECT.**
