> ⛔ SUPERSEDED 2026-09-06 by **`TRANSITION_SESSIONWRAP11_2026-09-06.md`** — read SW11 FIRST. Since this doc:
> multi-category (ufc/atp/wta) went live, the driver was clobbered for ~28h and restored, the UI was rewritten
> (DEPLOY-5), and DRIVER LIVENESS shipped. The state below (schema 17, mlb-only, engine PID 171106) is STALE — SW11
> has the current state (schema 20, 8 armed subs, the liveness panel). Only the graft hazards + standing lenses/rules
> below remain valid, and SW11 carries them forward.

# ★ SESSION WRAP 10 — 2026-09-02 (~23:25Z). SUPERSEDES SW9. FIRST-READ for the next agent.
> SW9 (`TRANSITION_SESSIONWRAP9_2026-09-02.md`) is the prior first-read. Since SW9: the **multi-category-per-account
> plan was accepted, WORKSTREAM A (the safety restructure) was BUILT, proven, and DEPLOYED LIVE**, and the UFC
> matcher core was built (inert). Nobody is monitoring; no poll/watch is running.

---

## ★★ TWO ACCOUNTS ARMED AND TRADING — one engine, one task PER ACCOUNT. Do NOT disarm as part of anything routine.
- **`kalshi_jack/mlb` AND `kalshi_karen/mlb`**, one shared engine, each: **5 contracts/copy, 50 orders/day, $150 daily
  / $150 open, $5.50 per-order, 2c slippage, 0.75 liquidity**, market types moneyline/total/spread.
- **★ STOP (verbatim) — kills BOTH accounts, never depends on any UI:**
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`
- **STATE, observed 23:25:35Z (PERSISTED rows + ts, NOT a status call — the mode=ro fail-safe has read a false disarm
  3×):** engine **PID 171106** (active, restarted 23:10:02Z; NRestarts=0 = a MANUAL restart, that counter ignores
  `systemctl restart`), pm_web **PID 170400** (active), **schema 17**. arm:global armed=True latched=False ts
  `2026-08-31T02:35:38`; arm:kalshi_jack:mlb armed=True latched=False ts `2026-08-31T21:49:39`; arm:kalshi_karen:mlb
  armed=True latched=False ts `2026-09-02T12:53:23`. Boot-reconcile 23:14:42Z: **both reconciled=True latched=False
  latched_categories=()**. Orders (dry_run=0): jack total 104 / max_id 114 / 103 filled; karen total 11 / max_id 115
  / 10 filled. Open: jack 3 legs (NYMTB-TB y5, TORCLE-CLE y5, MILCHC total-9 y5); karen 3 legs (NYMTB-TB y10,
  DETMIN-MIN y5, MILCHC-MIL y5). Shards (age 0min): jack $496.32 (sh0 **$0.0081**, sh3 $496.31), karen $471.39 (sh0
  $25.01, sh3 $446.38) — both shard-3 funded. Four PM crons intact (paper-poll `*/30`, refresh `05:00`, adjudicate
  `05:40`, rollup `05:50` UTC).
- **★ OPTION C HAS NOT YET PLACED IN PRODUCTION — say it plainly.** `PLACED_SINCE_RESTART=0` for BOTH accounts (DB +
  journal). The new engine boot-reconciled clean and is CYCLING (the opposed-pair guard fires per-account), but it
  has placed ZERO orders since 23:10:02Z. The 6 open legs all predate the restart. It is late in the MLB slate and
  current signals hit the opposed guard / gates. **Option C is proven by tests + a clean boot + byte-identical
  mlb-only behaviour — NOT yet by a live fill.** WATCH for the first Option-C placement next slate; it is the one
  thing not observed live. (Not a concern: the code is byte-identical to what was placing pre-restart.)

## ★★ OPTION C IS LIVE (and INVISIBLE — that invisibility IS the deploy's claim)
The PM driver is now **ONE task per ACCOUNT, iterating that account's categories, sharing ONE per-cycle Journal +
ONE venue-exposure read** (`live_driver.scheduled_pm_live_loop(..., categories=[...])`; `main.py` groups the roster
spawn by account). Roster log: `PM LIVE DRIVER WIRED -- 2 account task(s): {'kalshi_jack': ['mlb'], 'kalshi_karen':
['mlb']}`. **It is INVISIBLE today because each account has a SINGLE category** — one task with `categories=['mlb']`
is byte-identical to the old one-task-per-(account,category) wiring. **What would make it visible: a SECOND category
on one account** (jack/ufc) — then that account's ONE task iterates [mlb, ufc], the shared Journal enforces the $150
open cap JOINTLY across both, and gate 6/`evaluate`/the POST path are still untouched (no lock, nothing on the hot
path). That second category is gated behind M4's per-account opt-in (unbuilt) and the matcher (B2).

## ★★ GRAFT HAZARDS — INSTRUCTIONS, not history (read before ANY box deploy)
- **`main.py` — GRAFT the intended hunk, NEVER wholesale-copy.** The box `main.py` (`bba046e8` CR-stripped, as of
  this deploy) carries the per-account driver roster (N2) **AND now the Option C account-grouping**. A wholesale
  `main.py` write from a branch lacking either would DELETE that wiring. Compare CR-stripped (`git cat-file blob` /
  `tr -d '\r' | sha256sum`), never raw `git show | sha256sum` (autocrlf smudges LF→CRLF and lies).
- **`app.py` — GRAFT, NEVER wholesale.** The box `web/app.py` is M4-era (`is_admin`=10, `/pm/arm`=0); HEAD carries
  undeployed M5 `/pm/arm` plumbing. A wholesale copy LEAKS M5's admin surface before its window. (This session
  touched ZERO pm_web files.)

---

## ★ WHAT WENT LIVE IN A (deployed 23:03Z graft + 23:10:02Z restart; board-authorized per-step; box-is-truth graft)
Branch `pm-multicategory-2026-09-02` (worktree **`cc-pm-multicat-wt`**), base `e5d6506` (== the box for the engine
files). Box files now: `live_driver.py 4b85f93f` + `boot_reconcile.py ecce7777` wholesale (box==e5d6506, no drift) +
`main.py` GRAFTED `bba046e8`. NO new module (the ufc matcher is B, NOT shipped), NO migration.
- **M1 (Option C):** one task/account iterating categories; shared per-cycle Journal enforces the account open-cap
  (gate 6, account-keyed) JOINTLY — a later category's `evaluate` sees an earlier category's in-cycle
  `commit_would_place` through the ONE Journal. No lock, nothing added between decide-and-POST. Sequential categories
  never POST concurrently, so pykalshi's un-vendored concurrent-POST safety is never relied on.
- **M2 (account-wide auth latch):** a 401/403 now latches EVERY active category on the account (`live_driver.
  account_active_categories`, fail-SAFE to `[caller]`), via `arm.latch_auth_failure` which already looped a list.
- **M3 (whole-account boot-reconcile latch):** the comparison was already account-wide; the LATCH now hits ALL active
  categories (`reconcile_account(latch_categories=)`, passed from `run_boot_reconcile`). NO false-latch — a
  co-category position is in BOTH the account-wide journal and the book, so it reconciles as MATCH.
- **Re-scopings (see A0 below):** #14 per-category shard-underfunding ALARM, #16 `prior_snapshots` keyed
  `(category, wallet)`, #17 per-category `ctx`/catalog via an injectable `CATEGORY_CTX_BUILDERS` seam, #18
  per-category settlement throttle.

## ★ THE A0 ENUMERATION RESULT — honestly as found (this must survive; it keeps the list from growing forever)
The plan named THREE safety re-scopings; the third adversarial pass found the SAFETY set is **TWO**: #14 (alarm) and
#16 (snapshots). **#15 (`consec_err`) was DEFENDED BY CONSTRUCTION** — it is a per-CALL local inside
`run_live_arm_gated_cycle`, invoked once per category, so it never shares across categories. TWO further per-category
items — **#17 (`ctx`/catalog) and #18 (settlement throttle) — are FUNCTIONAL, not safety**: if wrongly shared they
degrade to `skip:no_quote` / a delayed booking, NOT a silent safety bypass. That safety-vs-functional distinction is
why the re-scoping problem did not grow past three and did not trip the stop condition. **Rung B6 (a fresh
adversarial pass over the restructured loop) is still owed before M4 opens the guard.**

## ★ THE RACE-DEMO TEST — repeat this pattern
`test_m1_shared_journal_caps_account_open_across_categories` proves the two-Journal (old two-task) model **actually
OVER-PLACES** (both categories authorize against the same base → account exceeds the $150 cap) BEFORE proving the
shared Journal caps the second category. Demonstrating the bug EXISTS is what makes M1 real rather than asserted.
Worth repeating for any "this races" claim.

## ★ B2 IS THE NEXT WORKSTREAM — and its FIRST STEP IS FIXED
**Establish what the live `MarketContext` actually carries BEFORE writing anything against it.** The UFC matcher
matches the Poly outcome (fighter full name) against the Kalshi market **`title`**, and the live `live_driver.
_market_quote_dict` does **NOT** carry `title` — the **FOURTH** instance of "a field the code assumes but the live
path does not supply," after `yes_bid` (dropped by our own dict), `exchange_index` (dropped by the SDK), and
`liquidity_dollars` (a deprecated always-'0.0000' stub). B2 **touches the CHOKEPOINT** (`execution.evaluate` gains a
per-category matcher dispatch) — the one place a subtle mistake places real orders wrongly — so it earns box-scratch
+ the same byte-identical care A got. B2 = a UFC `MarketContext` shape (fight + distance indices) + `evaluate`
category-dispatch registry + a ufc ctx builder that carries `title` and registers into `CATEGORY_CTX_BUILDERS`.

## ★ THE UFC MATCHER (B core) — built, INERT
`trading_corp/data/ufc_poly_kalshi_match.py` (43 tests green, force-added like the sibling `data/*.py`). Two binary
types ONLY: moneyline `KXUFCFIGHT-{YYMONDD}{K1}{K2}-{FTR}` + go-the-distance `KXUFCDISTANCE-{...}-DIST`. Real tickers
/slugs probed (Kalshi public API + Polymarket, 2026-09-02). kcode = `upper(last_name[:3])` (first-name fallback).
Poly slug codes are OPAQUE → the match is driven by the Poly OUTCOME full-name vs the Kalshi `title`. Unresolvable
cases are MISS TESTS, not papered over (3-char abbrev collision, no-distance-ticker, ambiguous-date, opaque-slug).
Nothing imports it yet.

## ★ SETTLED RULINGS (do not re-litigate)
- **Caps: the ACCOUNT-LEVEL AGGREGATE CAP, NOT the 75/75 divide.** The account aggregate STAYS $150/day + 50 orders —
  adding a category must NOT silently grow total exposure. That is **Workstream C, UNBUILT** (a gate-5b/8b on a
  per-account daily/count aggregate, race-free under Option C's shared Journal).
- **jack/ufc's FIRST order gets PLACE-ONE-AND-INSPECT** — `max_orders_per_day=1` + small caps for the first fill (the
  treatment Karen skipped). Reason: Option C's shared-account safeties are provable OFFLINE, but the UFC venue write
  is a NEW market family (KXUFCFIGHT/KXUFCDISTANCE) with no offline proof.
- **UFC numbers + whale set DEFERRED** pending the matcher proving out and Jack reading the loss-omission figures off
  the shipped Prospects UI before attaching anyone (5 ufc whales pinned: STC14 best-clean; AVOID kutsumiakia=CHALK +
  4751346=only-44%-single-fight).
- **The GUARD stays** (`driver_roster.plan_driver_tasks` refuses a 2nd category) until M1+M2+M3 are all closed AND the
  §4 re-scopings + B6 land; M4 relaxes it behind a per-account opt-in, OFF by default. Two-of-three is not a reason.
- Option C SETTLED (Option B / hot-path lock OFF THE TABLE); M5 CLOSED (gate-6b per-market-shard correct for shard0;
  funding shard0 = operator's job); the plan's other rulings stand (`MULTICATEGORY_PLAN_2026-09-02.md`).

## ★ THE FOUR KNOWN TEST FAILURES (classified — not real findings)
On the box's real venv, the engine suite is green EXCEPT: (1) `test_search_r1::test_schema_head_is_15` +
(2) `test_shard_snapshot_m3::…head_is_16` — HARDCODED stale schema constants (live head is 17). (3)+(4)
`test_shard_gate_r2::{driver_places_when_market_shard_funded, sustained_underfunding_alarm…}` — their `FakeClient`
RAISES on `/portfolio/positions` (never mocked for R7's venue read, which shipped at base e5d6506). **Proven to fail
IDENTICALLY on the box's un-overlaid e5d6506 `live_driver` (same line numbers) → pre-existing stale fixtures, not the
restructure.** R7's real venue behaviour is covered green by `test_venue_exposure_r7`. (A cleanup rung could bump the
two schema constants + teach `shard_gate_r2`'s fake to serve `/portfolio/positions` — filed, low priority.)

## ★ STANDING LENSES / COUNTS / BOX QUIRKS / OPERATING RULES
- **"a safety check that silently stops checking"** — now **16** instances (13 prior + #14/#16 built this session;
  #15 was investigated and DEFENDED-by-construction, so it is NOT a 15th). #17/#18 are FUNCTIONAL (fail-safe), not
  this lens. Others stand: "when a gate never passes, suspect its input before its logic"; "demonstrate the bug
  before asserting the fix" (the race-demo); box-is-truth reconcile FILE-BY-FILE **compare CR-STRIPPED** (raw
  `git show | sha256sum` under autocrlf LIES); grep-is-not-a-state-check; a-write-must-satisfy-every-view;
  the false-alarm mode=ro disarm read (3+ instances); "a field the code assumes but the live path does not supply"
  (now 4 instances — the B2 gate).
- **Box quirks:** box is NOT a git repo → deploys are base64-embed/stream grafts. **★ base64 heredocs must wrap at
  ≤76 chars — a single ~77KB base64 line exceeds a pipe line-limit and gives `base64: invalid input` (bit this
  session; the proven `pm_r2_graft` topped out at 76082).** Box pytest needs `-p no:pytest_ethereum` (a broken web3
  plugin crashes collection). Local tests: `C:\Users\AA Incorporado\cc\.venv-webtest` (pytest-asyncio + pyyaml
  present; `pykalshi` NOT installable there → the async live-path tests only run in box-scratch). Restarts are
  az-root via `C:\Users\AA Incorporado\Desktop\restart_tc.ps1`; pm_web = `prediction-markets-web`.
- **Operating rules:** command-paste-rule — one `.ps1` in `cc\` streaming a pure-ASCII no-BOM `.sh`; per-step "board
  authorizes atomic execution" for any deploy/restart/DB-write/arm; a CHANGED runner needs FRESH authorization (the
  heredoc-fix re-auth this session). Box-scratch + read-only runners are autonomous; after the board phrase YOU run
  the exact reviewed runner. main.py restart bounces EVERY division (bitunix included) — warn bitunix first.

## Branch / prod-live / next steps
- **`pm-multicategory-2026-09-02`** (worktree `cc-pm-multicat-wt`, base `e5d6506`, pushed, local==origin). Carries A
  (deployed) + B core (inert) + the plan + this handoff. prod-live `7220e32` / main-wip NOT advanced.
- **Next:** B2 (chokepoint dispatch; first step = the live-`MarketContext` probe) → C (account-level aggregate cap)
  → B6 (3rd adversarial pass) → M4 (per-account opt-in guard) → Jack's enablement (fund shard0 / opt-in / create
  jack/ufc + attach whales / restart / arm with place-one-and-inspect). PLAN = `MULTICATEGORY_PLAN_2026-09-02.md`;
  full build ledger = `MULTICATEGORY_BUILD_2026-09-02.md`.
- **Rollback of A** (only if needed): restore `~/pm_a_deploy_backup_20260902T230350Z` (pre-Option-C
  live_driver/boot_reconcile/main.py) + restart. DANGEROUS on a live armed division — reverts the whole restructure.
