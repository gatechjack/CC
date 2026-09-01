# ★ SESSION WRAP 6 — 2026-09-01 (~18:31Z). SUPERSEDES SW5. FIRST-READ for the next agent.
> ⛔ SUPERSEDED 2026-09-01 by `TRANSITION_SESSIONWRAP7_2026-09-01.md` — READ THAT FIRST. Since SW6: WHALE ATTRIBUTION
> on /live DEPLOYED (pm_web restart 19:11, pm_web PID now 145927); the app.py DRIFT HAZARD (HEAD app.py carries M5
> is_admin not on the box → graft, never wholesale-copy) is documented in SW7. SW6's live numbers are the 18:31Z snapshot.
> SW5 (2026-08-31) is superseded by this doc. The multi-account phase is now DEPLOYED LIVE, not just built.

---

## ★ JACK-MLB IS ARMED AND TRADING LIVE. Do NOT disarm as part of anything routine.
- **Config:** three whales (SDTrading `0x16bb…8492`, xifutloong3 `0x2dc1…`, `0x684baa57`), **5 contracts**/copy,
  **50 orders/day**, **$150 daily / $150 open**, three market types (moneyline / total / spread).
- **State (observed 18:31Z, read-only):** `global_armed=True`, jack-mlb **`effective_armed=True`**, **not latched**
  (boot_reconcile clean). **orders_today = 40 / 50**, max order id = 66; most recent id=66 `KXMLBGAME-…SEABOS-SEA`
  yes 5ct @0.48 (~38m ago). **15 open positions / 80 contracts.** **0 opposing pairs held.**
- **★ STOP (verbatim) — the authoritative kill, never depends on any UI:**
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`
- **DO NOT DISARM** (it trades overnight, intended). No monitor is running — the next agent discovers state by READING.

## ★ WHAT IS LIVE THAT WAS NOT THIS MORNING (DEPLOYED, not just built — box-is-truth; do NOT read M4/M5 as pending)
Two deploys landed today, both file-by-file box grafts (prod-live NOT advanced by ledger):
- **Engine bundle** (restart 16:14, engine bundle): **opposing-guard MEMORY fix** (opposed-memory — the flicker case
  cannot recur), the **shard-balance snapshot WRITER** (5-min, per-account, its own keys), **migration 016**
  (`pm_shard_balance_snapshot`, schema 15→16). Ledger: `DEPLOY_LOG_2026-09-01_engine_bundle.md`.
- **pm_web batch** (restart 18:10): **M3-display** (per-shard balance on the account page) + **M4 scoping and the
  write-action gates** + **`PM_ADMIN_IDENTITIES=jack`** on the pm_web unit + **Karen's `pm_account` row's
  `owner_identity='karen'`**. Ledger: `DEPLOY_LOG_2026-09-01_pmweb_batch.md`.
- PIDs at wrap: **engine 144229** (see the restart note below), **pm_web 143911**, **schema 16**.

## ★ WHAT IS BUILT AND NOT DEPLOYED
- **Engine M5** — the GLOBAL arm/disarm CONTROL at `trading.jacksumner.com/pm/arm` (engine-web, Option A: the arm
  WRITE must live on the side that owns the engine + legacy DB; pm_web is isolation-guarded and must never write arm
  state). Built + 12 tests green. **Needs the Portal NSG glance on :8000 and its own engine window.**
- **The PM-side cross-console link** (admin-only, on the overview) — built, but **held out** of the pm_web batch and
  ships on the pm_web restart AFTER engine M5 is live (a UI pointer must not ship before what it points at; a link to
  a not-yet-existing route teaches "broken").
- **`live_driver.py:639` fix** (the logging footgun) — fixed on the branch, rides the next engine window.

## ★ JACK'S OPEN ACTIONS
1. **Add Karen to Authelia (tonight).** All az-root, Jack's (the config + users file are `authelia:authelia` 750).
   Reproduced here in full (do not chase an earlier report):
   - **Hash** (on the box as root): `/usr/local/bin/authelia crypto hash generate argon2 --variant argon2id
     --iterations 3 --memory 65536 --parallelism 4 --key-size 32 --salt-size 16 --password '<karen-password>'`
     → copy the `Digest:` (`$argon2id$v=19$m=65536,t=3,p=4$…`).
   - **`/etc/authelia/users_database.yml`** (mirror jack; non-admin group):
     ```yaml
       karen:
         disabled: false
         displayname: "Karen"
         password: "<Digest from the hash step>"
         email: "karen@jacksumner.com"
         groups:
           - users
     ```
   - **★ `/etc/authelia/configuration.yml` — add under `access_control.rules`** (predictions ONLY, not trading —
     the load-bearing part; `default_policy: deny` + only `user:jack` rules today, so a users-file add alone leaves
     Karen authenticated-then-DENIED):
     ```yaml
         - domain: predictions.jacksumner.com
           policy: two_factor
           subject: 'user:karen'
     ```
   - **Restart required:** `systemctl restart authelia` (`file.watch: false` AND `access_control` is main-config,
     not hot-reloaded). ★ `authelia.service` fronts BOTH consoles, so the ~1–2s restart briefly fails in-flight auth
     across every division — but the TRADING ENGINE is unaffected (auth is web-only). Karen enrolls TOTP on first login.
   - Then the next agent verifies read-only that a live request carries `Remote-User: karen` (her env-leads step 1).
2. **The Portal glance at the :8000 inbound NSG rule** — confirm external :8000 is blocked (the effective probe showed
   it filtered; the literal rule read is a 30-second Portal confirm). Precondition for engine M5.
3. **The engine M5 window** — deploy `/pm/arm` (engine restart, PEAD-coordinated), then the PM-side link.

---

## Carried context

### ★ THE M4 CAVEAT — precisely scoped
- **JACK: verified END-TO-END on live** — his real Authelia login was OBSERVED on the wire to carry `Remote-User:
  jack` (vs `Remote-Name: Jack` / `Remote-Email: jack@…` — the code reads `Remote-User`), and the live console scopes
  him admin (both accounts; gates admit him).
- **KAREN: proven on the LIVE DEPLOYED app via a FORGED `Remote-User: karen` header** (stronger than a unit test — it
  is the deployed app on the box: she sees only her account, 403 on jack's, 403 on the gates). But her REAL login path
  (Authelia → `Remote-User: karen` → scoped view) is **UNVERIFIED until she exists in Authelia**. **"M4 verified" =
  jack's real path + karen's app-level scoping; it does NOT yet mean karen's real login.**

### ★ THE SHARD ANSWER IS NOW ON THE PAGE (a standing daily read, not a one-off arithmetic check)
- The per-shard split renders on each account page with an age band; the direction line reads **"Proceeds returning to
  shard 3 — shard-0 flat, the funding shard self-sustains."** At wrap (both fresh, ~3m): **Karen $25.01 / $433.28
  (total $458.29)**, **Jack $0.01 / $446.00 (total $446.01)**. Karen's shard-3 drifts as the legacy copy-trader trades
  her account (was $437.83 at deploy). This is the answer that the masked total-balance figure used to hide (it killed
  Karen's division for two days). Read it daily.

### The opposing-guard: memory-backed; the requirements-miss cost measured
- The three pre-existing opposing pairs' locked losses totalled **−30.85¢** — the measured cost of the
  requirements-miss (same-side stacking was correct; the OPPOSING-side guard was the real requirement). The guard is
  now **memory-backed** (opposed-memory), so the flicker/churn case cannot recur: at wrap, **0 opposing pairs held**,
  **2 `close_source='opposed'` rows** (the guard resolved pairs), and the live driver logs **`opposed_closes=0`** on the
  one pre-existing contested cid it re-detects each cycle (SKIPPING, not re-closing — the deliberate bound working).

### ★ BOTH HALVES OF THE SIGN CONVENTION ARE PROVEN on live data — strike any "−NO remains inference" text
- **+1.00 for YES** (proven at the first fill) and **−5.00 for NO** (proven — the journal holds NO-leg positions,
  e.g. `SEABOS-BOS2` away-side spread + `SDCIN-10` Under, journal signs them −5, boot_reconcile matched venue −5.00).
  The reconcile sign convention is closed on both legs; no inference remains.

### Per-account TRADING (the named N1/N2/N3 phase) — gated, and Karen-can't-trade is CORRECT not a gap
- Karen is structurally incapable of trading via PM today (no PM sub-division; she is display-only) — this is CORRECT.
- The per-account-trading phase (N1/N2/N3, `NOT_SCOPED_REVIEW_2026-09-01.md`) is gated on whether the legacy
  copy-trader RETIRES off Karen's account. Until that ruling, PM does not trade Karen; the display-only page states so.

### The backlog (unchanged priorities)
- **R7.h tx_hash re-entry key** — now SAFE to build (the opposed bound is DELIBERATE, no longer a coincidental
  side-effect of gate-4's coid). `/orderbook` depth precision; the doubleheader ambiguity; cron alerting; the flock
  guard; the Stage-5 price-bucket re-grounding (gated on Stage 5 loss-grounding); plain-language market descriptions +
  per-position realized P&L for the UI rewrite; the lock-in arbitrage question.

### ★ STANDING LENSES (do not re-derive; today's are marked NEW)
- **env-leads** — the environment (config/secret/env) must exist before the code that reads it; sibling of
  migration-leads. [proven live today: Remote-User observed + `PM_ADMIN_IDENTITIES` set before the enforcing restart]
- **a UI pointer must not ship before what it points at** (NEW) — a 404 link teaches "broken"; cross-surface deploy
  ordering is its own dependency (M3-display held for the writer; the M5 link held for engine M5).
- **a bound you did not design can be removed by accident** — a safety property that holds only as a side-effect gets
  removed when a future change "fixes" that mechanism (the opposed fee-loop bound → the deliberate opposed-memory).
- **an assumed mechanism may have been deliberately never built** (NEW) — verify a capability exists before building
  on it; an absence protected by a docstring-by-name AND a guard test is DESIGN, not a gap (M5's arm-write into
  pm_web — refused by name + `test_pm_web_imports_no_engine` — so M5 is engine-web, not pm_web).
- **a log call can silently fail to emit** (NEW) — a `--- Logging error ---` stub reaches the journal instead; verify
  load-bearing log lines actually APPEAR (live_driver:639: a lone dict `%`-arg → TypeError → the line is eaten).
- **box-is-truth: reconcile prod-live FILE-BY-FILE, never branch-first** — a branch carries stale other-division files;
  a wholesale advance reverts their live work. Both of today's deploys were file-by-file grafts (main.py grafted).
- Prior standing lenses still hold: **a safety check that silently stops checking (fails open)**; **grep is not a
  state check** (verify from the system that holds the state — proven twice today: the `$'\r'` false-CRLF, and
  observing Remote-User on the wire not from Caddy's config); **a deploy manifest is the import closure not the diff**;
  **a write must satisfy every view**; **retroactive enforcement** (leave pre-existing pairs to settle);
  **asset-outlives-code** (remove an asset only after its last user is gone).

### Box quirks + operating rules + settled rulings (do not re-litigate)
- **Box pytest quirk:** `web3.tools.pytest_ethereum` breaks collection → `-p no:pytest_ethereum`. **Local tests:** a
  layered `.venv-webtest` (over walletops packages: fastapi/jinja2/httpx/pytest/python-multipart/python-dotenv) runs
  the pm_web/engine TestClient suites (84/84 today). **Migrations** are applied by `pm_cli`/`init_db` (schema_version
  table; WAL + 5s busy_timeout + `IF NOT EXISTS`) — NOT the engine. **Restarts are az-root** (`az vm run-command`;
  sudo -n fails). **main.py on the box is LF** (git patches are CRLF via autocrlf — normalize both to LF before graft).
- **command-paste-rule:** every box command is ONE `.ps1` runner in `cc\` streaming a pure-ASCII no-BOM `.sh`; present
  the one-liner, HALT for "board authorizes atomic execution", then run. Read-only runners run autonomously.
- **Karen's Authelia access:** predictions ONLY (never trading); she CAN run Analyze (ungated, spend-capped), CANNOT
  promote/attach/demote/refresh (admin-gated, proven). Groups don't gate (access_control matches by username).
- **The CLI is the authoritative arm/disarm** (R7.d) — never depends on any web surface, in every design.

---

## ★ ENGINE RESTART NOTE (observed, flag for the next agent)
The engine restarted ~**18:17Z** (PID **139938 → 144229**) — NOT initiated by this session (the pm_web deploy was
pm_web-only; step-5a confirmed engine 139938 at 18:10Z). It came up **CLEAN**: arm not-latched, `effective_armed=True`,
schema 16, M3-writer producing fresh snapshots, opposed-memory active, R8 trading. Cause is external — a concurrent
division deploy or a crash+auto-restart. **Verify the cause** (systemd `NRestarts` / journal at 18:17). The PM deploys
survived (they are in the persisted code). The `live_driver:639` fix is NOT on the box yet, so that restart still runs
the logging footgun (benign) — the fix rides the NEXT engine window.

## Housekeeping — backups (KEEP all; do not delete; DANGEROUS ones flagged)
Today's box backups (all KEEP — the rollback safety for today's deploys):
- ★ **DANGEROUS** `data/prediction_markets.db.bak_mig016_20260901T160113Z` (pre-mig-016): restoring NOW reverts schema
  16 AND all of today's journal (orders 1–66, settlements, snapshots) on a LIVE armed division — catastrophic.
- **Moderate** `data/prediction_markets.db.bak_owner_20260901T180313Z` (pre-Karen-owner): restoring reverts Karen's
  owner_identity + any journal since 18:03.
- ★ **DANGEROUS** `web/{app.py,pm_accounts.html,pm_account.html}.bak_pmweb_… + subdivision.py + pm.css`
  (`.bak_pmweb_20260901T180057Z`, pre-M4/M3-display): restoring the pre-M4 code WHILE `PM_ADMIN_IDENTITIES=jack` is set
  removes the scoping/gates → pm_web serves everyone Authelia admits (all accounts, ungated actions).
- **Engine-bundle backups** `execution.py / live_driver.py / db.py / main.py .bak_bundle_20260901T16*` (pre
  opposed-memory / M3-writer / mig-016-in-db.py): INERT until an engine restart; restoring reverts the engine bundle on
  the next restart (opposed-memory + snapshot writer gone). KEEP.
- **Env drop-in** `/etc/systemd/system/prediction-markets-web.service.d/pm_admin_identities.conf`: NOT a backup —
  removing it (+ daemon-reload + restart) UNSETS `PM_ADMIN_IDENTITIES` (the M4 recovery path → serve-everyone).
- Older backups (Jul/Aug `trading_corp.db.bak_pead_*`, `main.py.bak_*`) are OTHER divisions' — leave untouched.
- **Local `cc\` scratch** (today's runners + `_build_*.py` + `pm_m3_mainpy.patch`): benign, untracked; KEEP as the
  operational deploy record + reusable read-only probes.

## Branch / prod-live
Branch **`pm-multiaccount-2026-09-01` @ `fc793a7`** (pushed, local==origin). Prod-live/main-wip **`8fd95d1`** NOT
advanced (box-is-truth; both deploys were file grafts). The four PM crons intact: `paper-poll */30`, `refresh 05:00`,
`paper-adjudicate 05:40`, `paper-rollup 05:50` (UTC).
