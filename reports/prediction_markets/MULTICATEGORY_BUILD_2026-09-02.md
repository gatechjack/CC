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

## RUNG A/M2 — auth-latch all account categories — IN PROGRESS
(SHA / proof to fill.)

## RUNG A/M3 — account-wide boot-reconcile latch — PENDING

## RUNG A/M1 — Option C one task per account + re-scopings (#14/#16 + functional #17/#18) — PENDING

## RUNG A-proof — byte-identical mlb-only behaviour — PENDING

## RUNG B — UFC matcher + category dispatch — PENDING

## RUNG C — caps mechanism — PENDING (HALT for Jack's ruling)

---

## DECISIONS I MADE (could have gone another way)
- Built off `e5d6506` (per-account tip == box main.py `9e8da82` CR-stripped), so every graft is a clean file-by-file
  diff off what the box runs.

## WHAT I FOUND THAT NOBODY ASKED ABOUT
- (A0) `consec_err` (#15) is NOT a real re-scoping — it is per-call, defended by construction. The plan listed
  three safety re-scopings; on building, the safety set is two (#14, #16). The other two per-category items
  (ctx/last_settle) are functional and fail-safe. This is the honest correction the build surfaced.

## DEPLOY QUEUE (authorize one rung at a time; nothing built here is deployed)
- (to fill as rungs complete: manifest LF-blob shas, graft approach, post-check, stop conditions.)
