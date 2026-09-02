# /plan prompt — Multi-category-per-account support (e.g. kalshi_jack/mlb + kalshi_jack/ufc)

Paste the block below after `/plan` to kick off the study. It is a PLAN task (produce a plan, build nothing).

---

/plan NEW SESSION — Prediction Markets platform. You are the code agent. This first task is a PLAN, not a build.
Produce a plan and halt.

=== THE GOAL, AND WHY IT IS A CORRECTION, NOT A FEATURE ===
Make ONE Kalshi account trade MULTIPLE categories safely — concretely, add a second sub-division to an account that
already trades (e.g. `kalshi_jack/ufc` alongside the live `kalshi_jack/mlb`). ★ This was the requirement from the
FIRST day of this division: one account, many categories, NOT one-account-per-category. The engine today supports N
DISTINCT accounts with ONE category each (kalshi_jack/mlb + kalshi_karen/mlb, both live) but REFUSES a second
category on one account. That refusal is a wall WE built — three account-scoped safeties were written per-category or
per-account in ways that only work at one-category-per-account. Treat this as CORRECTING the design back to the
original requirement, and assess honestly whether the wall was a deliberate decision or an accident of incremental
building. Do not frame it as a new feature bolted on.

=== ORIENT — read these in order, they are canonical ===
  1. reports/prediction_markets/TRANSITION_SESSIONWRAP9_2026-09-02.md — CURRENT STATE (two accounts armed+trading,
     one engine) + the filed "2nd-category-on-one-account preconditions." Opens with the STOP command and the graft
     hazards. Read it first.
  2. reports/prediction_markets/OVERNIGHT_2026-09-02.md — the per-account build (N1/N2/R7) that made two accounts
     possible, and the filed precondition in full.
  3. [[prediction-markets-backlog]] — the SHARD MONEY-MANAGEMENT items (funding a shard-0 category).
  4. The code the guard protects, read for yourself (do not trust remembered line numbers):
     - `trading_corp/prediction_markets/execution.py` — the `Journal` (open_usd keyed on account_id;
       `commit_would_place`; `in_cycle_open_usd`) and gate 6 (the R7 venue-exposure rebase).
     - `trading_corp/prediction_markets/live_driver.py` — the per-cycle `Journal(conn, [account_id], now_ts)`, the
       per-cycle venue-exposure read, and the `arm.latch_auth_failure(sub.account_id, [sub.category])` call.
     - `trading_corp/prediction_markets/boot_reconcile.py` — the whole-keypair-book venue read + the DEFERRED note
       (~lines 50-53) that a 2nd sub-division on one account needs a whole-account latch.
     - `trading_corp/prediction_markets/arm.py` — `latch_auth_failure` (it already ACCEPTS a category LIST),
       `latch_boot_reconcile_mismatch`, `auto_disarm`.
     - `trading_corp/prediction_markets/driver_roster.py` — `plan_driver_tasks`, which REFUSES a 2nd sub-division on
       an account (`reason='second_subdivision_on_account'`). This is the guard to relax LAST, after the safeties.
Verify branch tips + prod-live yourself; box-is-truth, prod-live lags.

★★ TWO ACCOUNTS ARE ARMED AND TRADING LIVE — kalshi_jack/mlb and kalshi_karen/mlb, one engine, real money. Do NOT
disarm. STOP, verbatim:
  PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global

=== THE KNOWN GAPS (name them; an honest per-safety split, never a sweep) ===
Each same-account category task is a separate asyncio `scheduled_pm_live_loop` sharing ONE broker per account. The
task loop body is already per-(account,category) scoped. The gaps are the account-level safeties:
  M1 — THE open_usd CAP RACE. `open_usd` is keyed on `account_id` (not per-category), and each task builds its OWN
       per-cycle `Journal` + reads the account's venue exposure independently. Two same-account tasks placing in
       overlapping cycles each accumulate in-cycle against the shared cap WITHOUT seeing the other's in-flight orders
       (they interleave at the POST await) → a within-cycle over-place window against the account's $150 open cap.
       ★ Establish precisely whether R7's venue rebase (account-wide base) mitigates this or whether the per-task
       in-cycle accumulation leaves the race real. Then design the coordination: a per-account async lock around
       evaluate+place, a shared cap accountant per account, a transactional cap read, or per-order venue re-read —
       each with its cost (latency, complexity, correctness).
  M2 — AUTH-FAILURE LATCH SCOPE. `latch_auth_failure(account_id, [category])` is called with ONLY the caller's
       category, but both categories share ONE broker per account — a 401 latching one category leaves the sibling
       POSTing on the same dead auth. Fix: latch ALL of the account's active categories on an auth failure (query
       them; `arm.latch_auth_failure` already takes a list) or trip a whole-account stop.
  M3 — WHOLE-ACCOUNT BOOT-RECONCILE LATCH. boot_reconcile reads the whole keypair book; a KALSHI_ONLY mismatch
       should latch the WHOLE account (loop its sub-divisions) or trip global, not just the reconciling category
       (the deferred note). ★ With two categories on one keypair, a position from category B reads as a mismatch for
       category A's reconcile — reason through the false-latch / missed-latch cases.
  M4 — THE GUARD. `driver_roster.plan_driver_tasks` refuses the 2nd sub-division. Relax it ONLY after M1-M3, and
       decide the gate (a per-account opt-in flag / a `pm_account` column / a config allowlist) so it fails closed.
  M5 — SHARD FUNDING (required for ufc specifically, and any shard-0 category). ufc is a SHARD-0 category; the money
       is all on shard 3, so ufc would `skip:shard_underfunded` every cycle even with M1-M4 done. The shard
       money-management item (a `target_balance_allocation` split or a deposit to shard 0) is a PREREQUISITE. Decide:
       is it operational (Jack moves funds) or a build (a shard-aware rebalancer), and how it interacts with the
       existing shard-3-only funding of mlb.
  ★ AND SEARCH FOR SAFETIES THE ABOVE MISSED. Re-check per (account,category) vs per-account for: daily_usd /
     orders_today counters, the opposed-side guard, gate-4 dedup/COID, the settlement scanner, the shard-snapshot
     writer, the per-market exposure gap. Say which are already safe for two-categories-one-account and which are
     not — verified / assumed-but-unverified / not-safe.

=== WHAT THE PLAN MUST ESTABLISH FIRST, read-only ===
  - The broker-sharing model: is one KalshiLiveBroker (httpx.AsyncClient) concurrency-safe for two asyncio tasks
    POSTing through it in the same cycle? Any shared cursor / rate-limit / auth state that two category tasks corrupt?
  - M1 empirically: trace evaluate→commit_would_place→place across two same-account tasks and pin the exact race
    window under R7's venue rebase. Is it a real over-commit or does the account-wide venue base bound it?
  - jack's live shard split (shard-0 balance) and the shard-money-management options for funding ufc.
  - Whether adding kalshi_jack/ufc needs a NEW whale roster + which ufc whales are candidates (Farm League), and the
    per-market stacking (N whales × ufc, no per-market cap).

=== THE PLAN ITSELF ===
Cover, for a SINGLE account trading N categories:
  - The open_usd coordination design (M1) — the recommended mechanism + why, and what it costs on the hot path.
  - M2 whole-account auth latch, M3 whole-account boot-reconcile latch — exact mechanisms.
  - M4 the guard relaxation + its fail-closed gate.
  - M5 shard funding — the path to fund shard-0, and whether it is operational or a build.
  - Rungs in order, each with what it needs (engine restart / migration / live DB write) and what is PROVABLE before
    deploying (box-scratch with two same-account category tasks; a forced-concurrent-placement test for M1; a forged
    401 for M2; a co-category venue position for M3).
  - ★ WHAT JACK MUST RULE: jack/ufc caps + sizing + which ufc whales; the shard-0 funding decision; the open_usd
    coordination choice if it trades latency for safety; whether to build the shard rebalancer now.

=== HOW TO WORK ===
Autonomous for: reading, testing, box-scratch, review, commits, pushes, read-only runners. HALT for: any deploy,
restart, live DB write, anything touching the order path or arm state, any prod-live advance, and any ruling that is
Jack's. THE BOX IS TRUTH — reconcile prod-live file-by-file, and compare CR-STRIPPED (raw `git show | sha256sum`
lies under autocrlf; the box is LF). The standing lenses apply — especially "a safety check that silently stops
checking" (now at 13 instances) and "when a gate never passes, suspect its input before its logic." The engine is
ARMED and trading two accounts throughout; nothing may interrupt it.

Produce the plan. Build nothing.

---

## Notes for Jack (not part of the prompt)
- This corrects the original one-account-many-categories requirement; the wall is the three account-scoped safeties
  (M1 open_usd race, M2 auth-latch scope, M3 boot-reconcile scope) plus the shard-0 funding (M5) that ufc needs.
- M5 (funding) is independent of M1–M4 (the safety fixes) and can be scoped in parallel — but ufc won't trade until
  BOTH are done.
- The guard I built (`plan_driver_tasks`) is what stops this from silently going wrong today; it is relaxed LAST.
