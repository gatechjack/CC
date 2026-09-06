# ★ SESSION WRAP 11 — 2026-09-06 (~04:48Z). SUPERSEDES SW10. FIRST-READ for the next agent.
> SW10 (`TRANSITION_SESSIONWRAP10_2026-09-02.md`) is now superseded. Since SW10: multi-category went LIVE (UFC + ATP
> + WTA), the driver was silently deleted for ~28h and restored, the UI was rewritten (DEPLOY-5), a Farm Search button
> shipped, and **DRIVER LIVENESS (this session) is now COMPLETE + LIVE**. Nobody is monitoring; **no poll/watch of
> mine is running** — the liveness panel is how you check health now.

---

## ★★ EIGHT SUB-DIVISIONS ARMED AND TRADING — two accounts, four categories each. Do NOT disarm as part of anything routine.
- **`kalshi_jack` and `kalshi_karen`**, each trading **mlb, ufc, atp, wta** — one engine, ONE task PER ACCOUNT
  iterating that account's four categories (Option C). Caps per sub-division: 5 contracts/copy, 50 orders/day, $150
  daily / $150 open, $5.50 per-order, 2c slippage, 0.75 liquidity. mlb = moneyline/total/spread; ufc = moneyline +
  go_the_distance; atp/wta = moneyline only.
- **★ STOP (verbatim) — kills BOTH accounts + all categories, never depends on any UI:**
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`

## ★ HOW TO TELL IF PM IS HEALTHY — IN ONE LINE: the DRIVER LIVENESS PANEL (new today; look here FIRST).
The accounts page (`/`) and each account page now carry a **Driver liveness** panel: green **RUNNING/IDLE** = the
driver is cycling that sub; red **STALE/NEVER** = it is NOT cycling (alarm — PM may not be trading); amber **BOOTING**
= a restart is in progress (normal, not a fault); amber **PENDING** = a fresh attach awaiting the next restart. Arm
state answers "is it *supposed* to trade?"; **the liveness panel answers "is the driver actually running RIGHT NOW?"**
— the question that went unanswered for 28 hours on 2026-09-04. The read is `heartbeat.read_liveness(conn)`; the CLI
equivalent is a `read_liveness` over `data/prediction_markets.db`.

## ★ STATE, observed 2026-09-06 ~04:48Z (READ-ONLY; PERSISTED arm rows + ts, NOT a status call — the mode=ro fail-safe has read a false disarm 3+ times)
- **engine `trading-corp` PID 208950** (active, NRestarts=0, since 2026-09-06 03:13:32Z — the Stage-1 liveness restart);
  **pm_web `prediction-markets-web` PID 211803** (active, since 04:38:41Z — the Stage-2 liveness restart). **schema 20.**
- **ARM (9 persisted rows, all `armed=true latched=false`):** arm:global ts `2026-08-31T02:35:38` (r8_arm);
  jack:mlb `2026-08-31T21:49:39`; karen:mlb `2026-09-02T12:53:23` (karen_arm); jack:ufc + karen:ufc
  `2026-09-04T04:29:59`; jack:atp/wta + karen:atp/wta `2026-09-04T04:31:4x` (all `tennis_session`). Untouched this
  session.
- **LIVENESS PANEL: all 8 RUNNING, any_alarm=False** (jack+karen × atp/mlb/ufc/wta; ages 1–2s; placed=0 this instant —
  the driver copies whale OPENS, nothing new to mirror right now, which is normal).
- **Boot-reconcile 03:17:02Z: both accounts reconciled=True latched=False latched_categories=().**
- **Orders / open:** jack realized **-2.08** over 77 settled (34W/36L), OPEN 2 positions / 10ct / $3.75 at cost, 159
  journaled orders. karen realized **+0.50** over 26 settled (14W/12L), OPEN 1 position / 5ct / $2.85, 54 journaled.
- **Shards (★ age ~33.8h — VERY_STALE, see the finding below):** jack total $480.81 (sh0 $122.82, sh3 $357.99);
  karen total $460.36 (sh0 $101.41, sh3 $358.95). Both shard-3 (baseball/tennis funding, exchange_index 3) funded.
- **Four PM crons intact:** paper-poll `*/30`, refresh `05:00`, paper-adjudicate `05:40`, paper-rollup `05:50` UTC.
  Four trading-corp systemd timers (pct-pruner, watchlist-stats, pm-watchlist-deep, watchlist-deep). Box is NOT a git
  checkout (deployed tree; deploys are streamed grafts).

## ★★ THE TWO GRAFT HAZARDS — INSTRUCTIONS, not history (read before ANY box deploy)
- **`main.py` — GRAFT the intended hunk, NEVER wholesale-copy.** main.py is SHARED across every division (PM /
  bitunix / PEAD / coinbase / MACE / …). **On 2026-09-04 a WHOLESALE main.py deploy by another division (MACE) DELETED
  the PM driver block, and PM sat armed-but-idle for ~28 hours** (full incident below). It was restored by grafting
  PM's block back onto MACE's *current* file — never a revert. Compare CR-STRIPPED (`tr -d '\r' | sha256sum`), never
  raw `git show | sha256sum` (autocrlf smudges LF→CRLF and lies).
- **`app.py` — GRAFT, NEVER wholesale.** The box `web/app.py` is **M4-era** (`is_admin` present, **`/pm/arm`=0**); the
  engine-console arm control (M5) is BUILT but UNDEPLOYED. A wholesale copy from a branch carrying M5 would LEAK the
  admin arm surface before its window. This session's L3 graft added heartbeat/liveness only and re-verified `/pm/arm`
  stays 0 (counted, not assumed).

---

## ★ THE 2026-09-04 INCIDENT IN FULL — the reason driver liveness exists
MACE's agent shipped a **wholesale `main.py`** (its branch tip == the box main.py, LACKING PM's per-account driver
roster block). The clobber landed 09-04 18:57Z and **activated at the 19:55Z restart**; PM's driver task was simply
never spawned. For **~28 hours PM sat ARMED and IDLE behind nine green arm rows** — the arm state was correct the
whole time, and **nothing on any screen said the driver wasn't running**. Blast: 12 settlements went unbooked and
~28h of whale signals uncopied — but **no capital was at risk** (every open position had already resolved at Kalshi;
it was a bookkeeping gap, 0 contested). Restored 09-06 00:30Z by **grafting PM's driver block onto MACE's current
main.py** (+95/-0, MACE byte-survives — NOT a revert), then a Jack restart; the 12 unbooked settlements were booked
clean by the R-d boot-scan. **The lesson that became this feature:** an arm indicator would have shown green for all
28 hours; only a *liveness* signal catches it. Detail: `pm-driver-clobbered-by-mace-mainpy-2026-09-05.md`.

## ★ THE OPTION-C RULE — AND WHY IT IS STILL UNFINISHED (this must reach the other divisions)
The PM driver is ONE task per ACCOUNT iterating its categories, sharing ONE per-cycle Journal + venue read (Option C).
main.py is SHARED and must be **grafted, never wholesale** — that rule EXISTED before 09-04, but it **lived only in
PM's own docs, which is exactly why MACE's agent never saw it and clobbered the driver.** ★ **A rule only one
division knows is not a rule. It STILL needs to reach the other divisions** (MACE, bitunix, PEAD, coinbase) — nothing
about that has changed. Until it does, the next wholesale main.py deploy by any division can delete PM again; the
liveness panel would now make it *visible within minutes* (red NEVER), but visible-fast is not the same as prevented.

## ★ THE DRIVER-LIVENESS DESIGN — and why it is shaped this way (COMPLETE + LIVE this session)
Branch **`pm-driver-liveness-2026-09-06`** (worktree `cc-pm-liveness-wt`, base multicat `3f498d4`, pushed). Shipped in
two stages, board-authorized per step, box-is-truth grafts:
- **L1 migration 020** — two tables (`pm_driver_task_heartbeat` per account, `pm_driver_heartbeat` per
  account+category). **`heartbeat.py`** (pm_web-safe: imports only `time`+`dataclasses`, so pm_web's no-engine
  isolation holds). **★ 020 IS NOW TAKEN — see the migration hazard below.**
- **L2 engine writer** in `live_driver.py` — **THREE grains, because one would have LIED**: the cycle body is inside
  one `try/except` ("a bad cycle must never kill the loop"), so a per-sub heartbeat alone goes stale for siblings
  while the task is alive. So: `task_alive` (per account, at the TOP of the while-loop, OUTSIDE the try) +
  `reached` (first in the category loop, before the skips) + `evaluated` (after the arm-gated cycle, with a cheap
  summary). Every write via `safe_beat` (a liveness write can NEVER kill a trading cycle). **The property proven, not
  reasoned: `_max_cycles=0` → the loop body never runs → ZERO rows → the writer cannot report healthy on a dead loop.**
- **L3 pm_web panel** — read-only; grafted onto the DEPLOY-5 web. **★ BOTH-DIRECTIONS age**: a FUTURE ts (clock jump)
  reads STALE/dead, never "fresh forever." **★ BOOTING**: after a restart the driver runs catalog builds + settlement
  scans + reconcile (~3.5 min observed) BEFORE the first heartbeat, and the prior rows age — so a sub whose account
  cycled within `BOOT_GRACE_SEC=10min` reads amber BOOTING (non-alarm), escalating to red past the grace. Keyed
  PER-ACCOUNT (a healthy sibling can't mask a dead account); the 28h incident is far past grace → still RED. **A
  monitor that reds on every restart gets ignored, which is the same failure as no monitor** — hence BOOTING. **★ The
  fake always-green `RUNNING` badge on the DEPLOY-5 account page was REMOVED, not shipped beside the real panel**
  (Jack's ruling: a real-red panel next to a reassuring fake-green is worse than no monitor) → exactly ONE, real
  liveness indicator per page. Full detail + the manifest: `DRIVER_LIVENESS_2026-09-06.md`.

## ★ THE UNSCOPED `/live` GET ROUTE — GATED ON KAREN'S LOGIN (close before her first session, not after)
`/live/{account}/{category}` (GET) has **no authz scoping** — it never checks `visible_account_ids`. It is
**pre-existing and was an intentional DEPLOY-5/R3 decision** (`test_live_r3.py:66` "the /live pages themselves are not
scoped"), and it already openly shows orders / positions / copied-whale identities. **This is theoretical only while
Jack is the sole login. The moment Karen has a real login — which is on Jack's list — she can read Jack's positions.**
★ Treat this as GATED ON KAREN'S LOGIN, not backlog: close it (mirror `account_page` → `_load_account`: resolve
identity, return `_FORBIDDEN`/403 for a non-visible account, guarding the schema-9/no-`pm_account` cases) **before her
first session.** All current `/live` tests run as admin, so admins are unaffected.

## ★ THE ARM CONTROL — planned A0→A2, M5 BUILT but UNSHIPPED
The liveness INDICATOR shipped first (the missing safety signal); the arm CONTROL is the convenience half and is not
yet deployed. Plan (`.claude/plans/lovely-puzzling-wind.md`): **M5** = an admin-only GLOBAL `/pm/arm` control on the
ENGINE console (`trading_corp/web/pm_arm_view.py`, registered in `web/routes.py`, tested) — **BUILT, NOT on the box**
(box app.py is M4-era, `/pm/arm`=0). **A0** = glance at the engine console on `:8000` to confirm whether `/pm/arm` is
served + `PM_ADMIN_IDENTITIES` is set (if not, A1 ships M5-global first). **A1** = extend `pm_arm_view` to per-SUB
arm/disarm (needs its own ENGINE restart window). **A2** = pm_web read-only display of per-sub arm state. Rulings:
sub-division granularity WITH the global master retained; **the CLI stays the authoritative kill path**; a latched
auto-disarm still needs `--clear-latch` from the CLI (a UI click can never re-arm a self-killed sub). pm_web
STRUCTURALLY cannot write arm state (isolation-guarded) — do NOT revisit that.

## ★ THE MIGRATION-NUMBER HAZARD — 020 IS NOW TAKEN
`db.py` migrations are CONTIGUOUS (tested: `[versions] == range(1, HEAD+1)`) and `init_db` is a single-MAX counter
(`if version <= current: skip`). **Driver liveness took 020; the live box schema head is now 20.** ★ **pm-ui-rewrite
(and anyone else) MUST use 021+.** A same-number collision **silently skips** the loser's DDL — a monitor that lies
all-NEVER on a box that thinks it migrated. The deploy discipline that caught this: drift-check the live box head
immediately before applying, ABORT + renumber on any movement.

## ★ THE REMAINING QUEUE (not started / owned elsewhere)
- **★ SHARD SNAPSHOTS ARE ~34h STALE (live gap, found this session):** the M3 shard-snapshot writer block in main.py
  was ALSO dropped in the 09-04 clobber and was NOT restored by the driver-only re-graft — so `shard_snapshot` shows
  ~33.8h-old balances (very_stale). The DRIVER trades fine (it reads the venue directly), but the balance display is
  stale and shard-0-direction can't be read. **Restore the M3 snapshot block onto main.py (graft, like the driver
  block) — the same clobber, the other half.**
- **ITF tennis** — a third tennis category (series `KXITFMATCH` + `KXITFWMATCH`, combined men/women, matches-scoped);
  real but lowest-tier / 0-paper, DEFERRED (`TENNIS_DISCOVERY_2026-09-03.md`).
- **Whale-detach button** — UI self-service detach (today detach is CLI-only via `pm_cli live-detach`).
- **Grounding-null dead-end** — loss-grounding UNKNOWN cells; a dead-end to resolve.
- **Surname title-expansion** — recovering Poly surname-only tennis outcomes via title expansion; WRONG-PICK-unsafe
  (Cerundolo brothers), left as a safe miss unless expanded carefully.
- **The UI rewrite is on its OWN agent** (the pm-ui-rewrite / DEPLOY-5 branch — Farm redesign, sport-specific non-MLB
  cards). Its `tests/` on the box are STALE vs the deployed DEPLOY-5 UI — that reconcile is the UI branch's, not PM's.

## ★ STANDING LENSES / COUNTS / BOX QUIRKS / OPERATING RULES (carried from SW10, updated)
- **"a safety check that silently stops checking"** — still the load-bearing lens. **"suspect the measurement before
  the system"** got THREE more instances this session, all mine, all bad instruments not bad systems: the arm read
  missing `PYTHONPATH` (looked like a crash), a post-check run before the boot finished (looked all-NEVER), and the
  ghost-category inflating `account_newest` (looked like BOOTING when it was NEVER). Others stand: box-is-truth
  reconcile FILE-BY-FILE **compare CR-STRIPPED**; "demonstrate the bug before asserting the fix"; grep-is-not-a-
  state-check; a-write-must-satisfy-every-view; the false-alarm mode=ro disarm read (3+); "a field the code assumes
  but the live path does not supply."
- **★ NEW this session — the drift-abort is the discipline working:** the Stage-1 apply FAIL-CLOSED when the box
  `db.py` differed from my base by comment-only drift; I reconciled file-by-file (grafted my hunks onto box-current,
  0 box lines removed) rather than blind-overwrite. Every deploy runner carries a sha drift-check that ABORTS on any
  movement of the file it grafts onto.
- **Box quirks:** box is NOT a git repo → streamed grafts; base64 heredocs must wrap ≤76 chars. Box pytest needs
  `-p no:pytest_ethereum`. **PM DB connection is `isolation_level=None` (AUTOCOMMIT)** — this is why mid-cycle
  heartbeat commits are safe (no transaction to split, no rollback in `live_driver`). PM DB = `data/prediction_
  markets.db`; legacy/arm DB = `data/trading_corp.db`. Restarts are az-root via
  `C:\Users\AA Incorporado\Desktop\restart_tc.ps1`; pm_web = `prediction-markets-web` (port 8081); engine =
  `trading-corp`. A local python for offline tests: `C:\Users\AA Incorporado\p2venv` (fastapi + pykalshi absent →
  the async live-path + pykalshi tests only pass in box-scratch).
- **Operating rules:** command-paste-rule — one `.ps1` in `cc\` streaming a pure-ASCII no-BOM `.sh`
  (`Get-Content -Raw | ssh $h "tr -d '\r\357\273\277' | bash"`); validate the `.ps1` with `[scriptblock]::Create()` +
  0 chars >127. Per-step "board authorizes atomic execution" for any deploy/restart/DB-write/arm; a CHANGED runner
  needs FRESH authorization. Box-scratch + read-only runners are autonomous. **The engine restart bounces EVERY
  division — warn co-tenants (MACE, bitunix, PEAD, coinbase) first; a pm_web restart bounces nothing else.**
  **★ PowerShell mangles native stderr and SIGPIPEs ssh on `Select-Object -First N`** (it left two box-scratch STG
  dirs behind this session) — capture with `-Last N` or run via the Bash tool for clean stderr.

## ★ SETTLED RULINGS (do not re-litigate)
- Option C is settled (Option B / hot-path lock off the table). Caps = the ACCOUNT-LEVEL AGGREGATE ($150/day/account
  binds BEFORE any per-category cap); intra-cycle order is ALPHABETICAL (atp first, wta last — inherited from
  `ORDER BY category`, nobody chose it). Tennis structure = atp + wta (ITF deferred). market_types: mlb
  moneyline/total/spread, ufc moneyline+go_the_distance, atp/wta moneyline. The liveness BOOTING trade (a real death
  alarms ~10min late) is explicitly accepted vs the 28h failure it catches. The fake RUNNING badge is removed, not
  shipped. The pm_web-can't-write-arm invariant is load-bearing — do not revisit.

## ★ MY HOUSEKEEPING (this session's artifacts — done)
- **KEPT on the box (both DANGEROUS — flagged):** `~/pm_liveness_stage1b_backup_20260906T030140Z` (103M — the ENGINE
  rollback: db.py.orig + live_driver.py.orig + a pre-020 DB copy; **restoring reverts to PRE-heartbeat engine code —
  L2 gone, the monitor goes blind; it would NOT disarm or stop trading, but you lose the liveness signal**).
  `~/pm_liveness_stage2_backup_20260906T043038Z` (136K — the PM_WEB rollback: 5 web .orig; **restoring reverts pm_web
  to PRE-liveness — the panel disappears and the fake RUNNING badge returns; pm_web-only, no trading impact**).
- **REMOVED (mine, spent):** the aborted first-Stage-1 scratch + backup + its two tars; two leftover box-scratch STG
  trees (SIGPIPE'd before self-cleanup); locally the `_deploy5`/`_ov`/`_dep`/`_boxdrift` staging dirs + the overlay/
  deploy build tars. KEPT locally: the `cc/pm_liveness_*.{ps1,sh}` + `cc/pm_final_state.*` runners (read-only
  recon/postcheck/recheck/state are re-runnable diagnostics; apply runners are gated + record).
- **No monitor or poll of mine is running.** Nobody is watching PM — the liveness panel is the check.

## Branch / prod-live / next
- **`pm-driver-liveness-2026-09-06`** (worktree `cc-pm-liveness-wt`, base multicat `3f498d4`, pushed, local==origin).
  Carries L1+L2 (engine, deployed) + L3 heartbeat/partial/tests (deployed; the DEPLOY-5 app.py + templates were
  grafted onto the box — box is truth, not a branch commit). prod-live: engine 208950 runs L1+L2; pm_web 211803 runs
  L3 on the DEPLOY-5 web.
- **Next (Jack's / other agents'):** the Option-C main.py rule to the other divisions; restore the M3 shard-snapshot
  block; close the `/live` route before Karen's login; the arm control (A0 → A1 → A2, M5 to the box); ITF tennis; the
  UI-rewrite queue on its own agent. Full liveness detail: `DRIVER_LIVENESS_2026-09-06.md`.
