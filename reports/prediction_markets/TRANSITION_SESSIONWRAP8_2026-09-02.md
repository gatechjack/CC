# ★ SESSION WRAP 8 — 2026-09-02 (~04:56Z). SUPERSEDES SW7. FIRST-READ for the next agent.
> SW7 (2026-09-01) is superseded by this doc. Since SW7: the LOSS-OMISSION DISPLAY and the PROSPECTS [Analyze]
> CONTROL were both DEPLOYED LIVE (pm_web). ★★ ANOTHER AGENT IS MID-BUILD ON THE BOX (per-account trading) — the
> box state you read WILL be moving; timestamp everything and do not treat its commits/backups as drift.

---

## ★ JACK-MLB IS ARMED AND TRADING LIVE. Do NOT disarm as part of anything routine.
- **Config:** three whales (SDTrading `0x16bb…8492`, xifutloong3 `0x2dc1…`, `0x684baa57`), **5 contracts**/copy,
  **50 orders/day**, **$150 daily / $150 open**, three market types (moneyline / total / spread).
- **State (observed 2026-09-02 04:54Z, persisted rows not a single verdict read):** GLOBAL + SUB both
  `armed=true, latched=false`, **ts UNCHANGED** (`2026-08-31T02:35:38Z` / `21:49:39Z`) → `effective_armed=True`,
  blocking=None. **orders_today 15/50, max id=81; 1 open position / 5 contracts; 0 opposing pairs held.**
- **★ STOP (verbatim) — authoritative kill, never depends on any UI:**
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`
- **DO NOT DISARM** (trades overnight, intended). No monitor running — discover state by READING.

## ★★ ANOTHER AGENT IS MID-BUILD ON PER-ACCOUNT TRADING — the box may change under a reader
- **Its scope:** the NOT-SCOPED **per-account trading** phase (N accounts, one engine — backlog N1/N2/N3). **Its
  build surface = `main.py`, `live_driver.py`, `execution.py`, `settlement.py`, `shard_snapshot_task.py`.** It is
  authorized to build/test/commit/push but **NOT to deploy or restart**.
- **Observed tonight:** local `main-wip-2026-08-02` advanced **8fd95d1 → ed2e6c0** (NOT pushed; origin still
  `8fd95d1`). `ed2e6c0` = `ops(poly_kalshi_mlb): DISARM via [G-halt] DB gate + runners + deploy_log` — the
  **LEGACY poly_kalshi_mlb** division disarm (per-account prep: legacy retiring off the shared Karen account).
  **This is about LEGACY, NOT Jack-MLB** — Jack-MLB stays armed (confirmed above).
- **★ Do NOT touch those 5 engine files or `main-wip`, and do not duplicate the per-account work.** Unfamiliar
  commits/branches/backups on the box are the other agent's — say so, don't treat as drift.

---

## ★ WHAT THIS SESSION DEPLOYED LIVE (pm_web only; box-is-truth; DO NOT read as pending)
### 1. Loss-omission % beside win% (restart 03:44Z; ledger `LOSS_OMISSION_DISPLAY_DEPLOY_2026-09-01.md`)
- **Why it exists:** `/closed-positions` drops held-to-worthless losses wallet-dependently, so a whale can screen
  as a near-lock and be a coinflip. **SDTrading — a LIVE-copied whale — drops 94% of its losses; it screens
  ~93% win and is truly ~50% (honest 501W/528L, live-quoted).** The screening win% was fiction; the page now
  says so beside it.
- **How the figure is produced:** computed **on Analyze** (which already pays the `/activity` + gamma fetch),
  **cached per-whale** in `pm_loss_grounding_cache` (migration **017**, schema 16→17), **read by Prospects**.
- **Three display states (the invariant):** **MEASURED** (`−X% losses @ Y% cov`), **FLOOR** (truncated OR
  coverage<0.90 → a lower bound, marked `(floor)`; `analyze.loss_is_floor`), **UNKNOWN** (never analyzed →
  `omission unknown`, **NEVER 0%** — a 0 that means "unchecked" is the worst display).
### 2. Prospects [Analyze] control (restart 04:36Z; ledger `PROSPECTS_ANALYZE_CONTROL_DEPLOY_2026-09-02.md`)
- **A GAP, not a regression:** Analyze was never on the prospect rows (only Watchlist rows + whale detail); the
  loss-omission caveat exposed it. The un-analyzed caveat **is** now an ungated `[Analyze]` button
  (`omission_cell` macro, shared with the analyze-result OOB fragment).
- **★ Three independent reasons a spend runaway cannot happen** (the shape to prefer): (1) the button renders
  ONLY on un-analyzed rows; (2) the OOB fragment that replaces it carries **no `hx-post`** (cannot loop); (3) a
  re-click on a cached whale is a **free cache hit**. Plus `hx-disabled-elt`, no bulk/analyze-all, and the
  untouched $20/day cap. **Ungated** (Karen judges too, R3) while Promote/Refresh/Attach/Demote stay admin-only.

## ★ THE GROUNDING-NULL DEAD-END — observable LIVE on `0xd1acd…/mlb` (filed in the backlog)
If a whale's `/activity` has NO in-category held-to-resolution decisions (or grounding otherwise fails) while the
LLM verdict succeeds, the verdict caches but **no grounding-cache row is written** → the row keeps an **inert
`[Analyze]` button**, and a normal (non-force) re-click is a **free cache hit that does NOT re-ground** (only
`?force=1` re-grounds). Pressing it changes nothing. **NOT a correctness bug** (honest "omission unknown", no
fabricated figure) — a UX dead-end. Two fix options in `[[prediction-markets-backlog]]`; do NOT auto-force
(that re-spends the LLM).

## ★ TWO LENS ENTRIES THIS SESSION EARNED
- **Instance 12 of "a safety check that silently stops checking":** the `(floor)` marker keyed on **truncation**
  rather than **coverage**, so a low-coverage-but-untruncated omission would render as a full measurement — and
  the test **masked it** by setting both together. Caught in adversarial review, fixed pre-ship
  (`analyze.loss_is_floor` = truncated OR coverage<0.90). See `[[safety-check-silently-stops-checking]]`.
- **The counterpoint to PREFER:** the narrator loss tier fires on `a_only_losses > 0` — **a floor of ONE, not a
  tunable threshold**, so there is no number to set past the data and no reachable value that switches it off.

---

## ★ STANDING LENSES / BOX QUIRKS / RULINGS (do not re-derive or re-litigate)
- **★ THE app.py M5-DRIFT HAZARD STILL APPLIES to ANY manifest containing `.py`.** HEAD's `web/app.py` carries
  M5's `is_admin` plumbing (the `/pm/arm` control; **12** `is_admin` occurrences) that is DELIBERATELY NOT on the
  box (box = M4 + whale = **10** `is_admin`, **no** `/pm/arm`). A wholesale `app.py` copy LEAKS M5's admin surface
  onto prod. **GRAFT the intended hunk onto the box `app.py`; verify `is_admin` stays 10 and `/pm/arm`=0.** (Both
  this session's deploys had ZERO `.py` in the manifest → the hazard was checked and did NOT apply — templates+css
  only. Confirm this explicitly whenever a deploy is `.py`-free so the next agent sees it was checked.)
- **box-is-truth: reconcile prod-live FILE-BY-FILE.** CRLF-vs-LF: Windows `git show` emits CRLF, the box is LF →
  **compare LF-normalized** before deciding wholesale-copy vs graft. `pm.css` has box **whale-drift** → append the
  hunk onto the box file, never wholesale-copy. `main.py` on the box is LF.
- **Box pytest: `-p no:pytest_ethereum`** (broken web3 plugin false-STOPs collection). Local pm_web tests:
  `.venv-webtest` (over walletops) — it has **no `pytest-asyncio`**, so the full PM suite shows ~**117 baseline
  async/dep fails** locally (identical on unmodified base = zero-regression yardstick); the async orchestrator
  tests only pass in **box-scratch** (rsync box tree, exclude `/venv` + `/data`, overlay files, pin `PM_DB_PATH`).
- **Migrations** are applied by `pm_cli`/`init_db`, not the engine — and a **cron running the grafted `db.py` can
  apply a new migration before your explicit `init_db`** (benign, idempotent, IF NOT EXISTS). `init_db` tolerates
  a DB ahead of code (skips). Restarts are **az-root** (`az vm run-command`); pm_web = `prediction-markets-web`,
  engine = `trading-corp` (`restart_tc.ps1`).
- **command-paste-rule:** one `.ps1` runner in `cc\` streaming a pure-ASCII no-BOM `.sh`; per-step authorization
  ("board authorizes atomic execution") for any LIVE deploy/restart/DB-write; read-only runners run autonomously.
- **Analyze is now REAL LLM spend** (key wired) against the **$20/day** cap; a cache HIT spends nothing.
- Carried lenses (SW7): a-write-must-satisfy-every-view; a-log-call-can-silently-fail-to-emit; a UI pointer must
  not ship before what it points at; an assumed mechanism may have been deliberately never built; env-leads;
  grep-is-not-a-state-check; deploy-manifest-is-import-closure; retroactive-enforcement; asset-outlives-code;
  the unexecuted-path law; the false-alarm disarm read (an indeterminate `mode=ro` arm read returns DISARMED by
  design — check the persisted rows + ts before believing a disarm).

## ★ OBSERVED STATE (2026-09-02 04:54Z — the box is MOVING under the per-account agent)
- **PIDs:** engine **144229** (unchanged since 18:13:58Z — the other agent has NOT restarted it), pm_web
  **155543** (04:36:50Z = this session's analyze-control restart). **schema 17.** boot_reconcile clean (arm
  latched=False).
- **Shards (age 3m, fresh writer):** kalshi_jack **$506.01** (sh3 506.00 / sh0 0.008), kalshi_karen **$486.29**
  (sh3 461.28 / sh0 25.01). (Jack's sh3 grew from ~$446 as overnight settlements credit.)
- **4 PM crons** intact (paper-poll `*/30`, refresh `05:00`, adjudicate `05:40`, rollup `05:50` UTC).

## ★ HOUSEKEEPING — my artifacts only (KEEP all; do NOT restore onto a live armed division)
- **★★ DANGEROUS** `~/pm_lossomit_deploy_backup_20260902T023308Z/prediction_markets.db` (103M, **pre-mig-017**):
  restoring NOW reverts schema **17→16** AND all of today's PM journal (orders, settlements, grounding cache) on a
  LIVE ARMED division — catastrophic. Its `files/` = pre-loss-omission pm_web code (reverting it drops the omission
  display on the next restart; its `app.py` is the M4+whale version — reverts the omission hunks but KEEPS scoping).
- **Moderate** `~/pm_prospanalyze_backup_20260902T043321Z/files/` (100K, templates+css): restoring reverts the
  analyze-control on the next pm_web restart. **No DB, no schema.**
- **Runners** in `cc\` (`pm_reground_*`, `pm_sportsfeed_probe_ro`, `pm_lossomit_*`, `pm_prospanalyze_*`,
  `pm_wrap8_*`) + local staging `_boxgraft` / `_boxgraft2` + worktrees `cc-pm-pricebucket-reground-wt` /
  `cc-pm-lossomit-wt` / `cc-pm-prospanalyze-wt`: **KEEP** — the operational deploy record / branch working copies.
- **NOT mine (leave untouched):** the prior-session box backups (`pm_caps_*`, `pm_live_*`, `pm_m2_*`,
  `pm_multiacct_*`, `pm_r2c_*`, `pm_r5_*`, `pm_rd_*`, `pm_reattach_*`, `pm_rollup2_*`, `pm_mig014_*`,
  `pm_cli_search_*`, `pm_cp3a_*`). ★ The `pm_m2_*` / `pm_multiacct_*` are the **pre-M4** pm_web backups — restoring
  them while `PM_ADMIN_IDENTITIES` is set would drop account scoping; they are the multi-account session's, not
  this one's.

## ★ REMAINING QUEUE
- **Engine M5** (`/pm/arm` global arm control) + the PM-side cross-console link — built, NOT deployed; needs the
  Portal :8000 NSG glance + an engine window. ★ **Coordinate with the per-account-build agent** — both want engine
  windows and touch `main.py`.
- **`live_driver.py:639`** log fix — on branch, rides the next engine window. ★ `live_driver.py` is the other
  agent's surface — coordinate, do not graft over its work.
- **The grounding-null dead-end** NIT (backlog, two options).
- **The UI rewrite** — a SEPARATE chat agent has its OWN transition doc (do not act on it here).
- **`[[prediction-markets-backlog]]`** — the queued rungs (shard money-management, per-market cap, doubleheader
  ticket, whale-proportional CONTRAINDICATED, per-account-trading N1/N2/N3 = the other agent's phase, etc.).

## Branch / prod-live
- This session's branches (pushed, local==origin): **`pm-pricebucket-reground-2026-09-01` @ `6b9ab9b`** (the
  read-only investigations); **`pm-loss-omission-display-2026-09-01` @ `f238a5a`** (loss-omission deploy + ledger);
  **`pm-prospects-analyze-2026-09-02` @ `<this-commit>`** (analyze-control deploy + ledger; **SW8 appended here**).
- **prod-live `7220e32` / origin main-wip `8fd95d1` NOT advanced** (box-is-truth; file-by-file when advanced,
  never a branch/ledger advance). **★ LOCAL `main-wip` = `ed2e6c0`** = the OTHER agent's legacy-disarm commit
  (not pushed, not mine).
