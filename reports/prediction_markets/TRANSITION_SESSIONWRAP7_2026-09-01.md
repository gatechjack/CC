# ★ SESSION WRAP 7 — 2026-09-01 (~19:20Z). SUPERSEDES SW6. FIRST-READ for the next agent.
> ★★ SUPERSEDED 2026-09-02 by SW8 (`TRANSITION_SESSIONWRAP8_2026-09-02.md`) — READ THAT FIRST. Since SW7: the
> loss-omission display + the prospects [Analyze] control were both DEPLOYED LIVE (schema 17), and ANOTHER AGENT
> is mid-build on per-account trading. SW7 is retained for the multi-account/whale-attribution detail.
> SW6 (2026-09-01 18:31Z) is superseded by this doc. Since SW6: WHALE ATTRIBUTION on /live was DEPLOYED LIVE.

---

## ★ JACK-MLB IS ARMED AND TRADING LIVE. Do NOT disarm as part of anything routine.
- **Config:** three whales (SDTrading `0x16bb…8492`, xifutloong3 `0x2dc1…`, `0x684baa57`), **5 contracts**/copy,
  **50 orders/day**, **$150 daily / $150 open**, three market types (moneyline / total / spread).
- **State (observed 19:20Z, persisted rows not a single verdict read):** GLOBAL + SUB both `armed=true, latched=false`,
  **ts unchanged since 2026-08-31** (`r8_arm`) → `effective_armed=True`, blocking=None. **orders_today 40/50**, max
  id=66; **15 open positions / 80 contracts; 0 opposing pairs held.**
- **★ STOP (verbatim) — authoritative kill, never depends on any UI:**
  `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`
- **DO NOT DISARM** (trades overnight, intended). No monitor running — discover state by READING.

## ★ WHAT IS LIVE (DEPLOYED, not just built — box-is-truth; do NOT read any of it as pending)
- **Engine bundle** (restart 16:14): opposing-guard MEMORY fix (flicker cannot recur) + shard-snapshot WRITER (5-min)
  + migration 016 (schema 15→16).
- **pm_web batch** (restart 18:10): M3-display + M4 scoping and write-action gates + `PM_ADMIN_IDENTITIES=jack` on the
  pm_web unit + Karen's `pm_account.owner_identity='karen'`.
- **★ Whale attribution on /live** (restart 19:11): whale on every held + ledger row; "Currently held" split per
  (ticker, whale); the per-whale LIVE-COPY record. Ledger `WHALE_ATTRIBUTION_2026-09-01.md`.
- PIDs: **engine 144229, pm_web 145927, schema 16.**

## ★ WHAT IS BUILT AND NOT DEPLOYED
- **★★ app.py DRIFT HAZARD — READ THIS BEFORE ANY pm_web DEPLOY.** HEAD's `web/app.py` carries M5's `is_admin`
  plumbing (commit 61b2e8f) that is DELIBERATELY NOT on the box. A wholesale HEAD app.py deploy would LEAK M5's admin
  surface onto prod **before its window, its Portal glance, and its env** — a silent premature go-live of an
  arm-adjacent control path. **Until engine M5 ships, GRAFT the intended hunk onto the box's app.py; NEVER
  wholesale-copy app.py.** (Today's whale deploy did exactly this: grafted the whale hunk onto the box's e7af7d8 app.py
  → sha 4c8ceb7a6050, verified M5 `is_admin` absent.) The box app.py is `4c8ceb7a6050` (M4 + whale, NO M5).
- **Engine M5** — the GLOBAL arm/disarm CONTROL at `trading.jacksumner.com/pm/arm` (engine-web, Option A: the arm
  WRITE must live on the engine/legacy-DB side; pm_web is isolation-guarded). Built + 12 tests. Needs the Portal :8000
  NSG glance + its own engine window.
- **The PM-side cross-console link** — built, held out; ships on the pm_web restart AFTER engine M5 (a UI pointer must
  not ship before what it points at).
- **`live_driver.py:639`** logging footgun — fixed on branch, rides the next engine window.

## ★ JACK'S OPEN ACTIONS (reproduced in full)
1. **Add Karen to Authelia (tonight).** All az-root (config + users file are `authelia:authelia` 750).
   - **Hash** (on the box as root): `/usr/local/bin/authelia crypto hash generate argon2 --variant argon2id
     --iterations 3 --memory 65536 --parallelism 4 --key-size 32 --salt-size 16 --password '<karen-password>'`
     → copy the `Digest:` (`$argon2id$v=19$m=65536,t=3,p=4$…`).
   - **`/etc/authelia/users_database.yml`** (mirror jack; non-admin group):
     ```yaml
       karen:
         disabled: false
         displayname: "Karen"
         password: "<Digest>"
         email: "karen@jacksumner.com"
         groups:
           - users
     ```
   - **★ `/etc/authelia/configuration.yml` — add under `access_control.rules`** (predictions ONLY, not trading; the
     load-bearing part — `default_policy: deny` + only `user:jack` rules today, so a users-file add alone leaves Karen
     authenticated-then-DENIED):
     ```yaml
         - domain: predictions.jacksumner.com
           policy: two_factor
           subject: 'user:karen'
     ```
   - **Restart required:** `systemctl restart authelia` (`file.watch: false` AND access_control is main-config). ★ it
     fronts BOTH consoles → a ~1–2s auth blip across every division, but the TRADING ENGINE is unaffected (auth is
     web-only). Karen enrolls TOTP on first login. Then the next agent verifies read-only that `Remote-User` carries
     `karen`.
2. **Portal glance at the :8000 inbound NSG rule** — confirm external :8000 blocked (effective probe showed filtered;
   the literal rule read is the confirm). Precondition for engine M5.
3. **The engine M5 window** — deploy `/pm/arm` (engine restart), then the PM-side link.

---

## Carried context

### ★ Whale attribution — what it proves
The per-whale LIVE-COPY record decomposes the account P&L by whale, and it **sums to the account total on the LIVE
page**: SDTrading −17.33 (5/13, 18 settled, 1 opposed), 0x684baa57 +0.33 (2/2, 4 settled, 1 opposed), xifutloong3
−3.23 (0/1, 1 settled) → **−20.24 = the account realized, 7W/16L**. ★ Attribution is by the **wallet on each row**, NOT
a close→entry join — chosen because the NULL-cid first-Cubs settlement (id=8, predates the cid-stamping fix) would
VANISH from a join and nobody would notice (a missing row does not error). This is the section that answers "is
copying THIS whale working for me" — distinct from paper ("would it have") and prospect ("did it historically").

### ★ M4 caveat — precisely scoped
JACK verified END-TO-END on live (real Authelia login, `Remote-User: jack` observed on the wire → admin, both
accounts). KAREN proven on the LIVE deployed app via a FORGED `Remote-User: karen` header (she sees only her account,
403 on jack's, 403 on the gates) — stronger than a unit test but NOT her real login, which is UNVERIFIED until she
exists in Authelia. "M4 verified" ≠ Karen's real login.

### ★ The shard answer — a standing daily read (not a one-off)
Per-shard split renders on each account page with an age band + direction line "Proceeds returning to shard 3 —
shard-0 flat, self-sustains." At wrap (both fresh ~3m): **Karen $25.01 / $433.28 ($458.29), Jack $0.01 / $446.00
($446.01).** Karen's shard-3 drifts as the legacy copy-trader trades her account. Read it daily; the masked total used
to hide it (it killed Karen's division for two days).

### The opposing guard + the sign convention
The three pre-existing opposing pairs' locked losses totalled **−30.85¢** (the measured cost of the requirements
miss). The guard is **memory-backed** (opposed-memory) so the flicker/churn case cannot recur (0 pairs held now; the
driver logs `opposed_closes=0` = skipping, no re-close). **Both sign-convention halves PROVEN on live: +1.00 YES /
−5.00 NO** — strike any "-NO inference" text.

### Per-account TRADING (N1/N2/N3) — gated; Karen-can't-trade is CORRECT
Karen is structurally display-only today (no PM sub-division). The per-account-trading phase (`NOT_SCOPED_REVIEW`) is
gated on the legacy copy-trader RETIRING off Karen's account. Not a gap.

### Backlog (incl. today's two)
- **★ Opposed-close realized P&L is UNBOOKED (engine-side, needs a window)** — the engine doesn't compute realized on
  a guard flatten, so an opposed-closed copy's outcome is in neither realized nor open; the page shows
  `opposed_closed=N` so it's visible, but the P&L is lost from the per-whale record. Fix = book realized on opposed
  flattens engine-side (2 rows today).
- **0x684baa57 has no display name** → wallet short-form fallback (correct); name it in `pm_whale.user_name` if wanted.
- R7.h tx_hash re-entry key (now SAFE — opposed bound is deliberate); /orderbook depth precision; doubleheader
  ambiguity; cron alerting; the flock guard; Stage-5 price-bucket re-grounding; plain-language descriptions +
  per-position P&L for the UI rewrite; the lock-in arbitrage question.

### ★ THE FALSE-ALARM DISARM READ (so it is not rediscovered as a fault)
A single `arm.read_status` may return `effective_armed=False` on an INDETERMINATE `mode=ro` read — this is the arm
module's correct FAIL-SAFE inversion (an unreadable arm state must never read as armed), NOT a real disarm. Today's
post-check hit one during post-restart contention; the re-read showed both rows `armed=true` with UNCHANGED
timestamps. **Before believing a disarm, check the PERSISTED rows + their ts** (a disarm stamps a new ts); a verdict
read alone can be a transient fail-safe. [[grep-is-not-a-state-check]]

### ★ STANDING LENSES (do not re-derive; today's marked NEW)
- **env-leads** (config before code; proven live: Remote-User observed + env set before the enforcing restart).
- **a UI pointer must not ship before what it points at** (NEW; M3-display held for the writer, M5 link held for M5).
- **a bound you did not design can be removed by accident** (the opposed fee-loop bound → deliberate opposed-memory).
- **an assumed mechanism may have been deliberately never built** (NEW; M5 arm-write into pm_web refused by name + a
  guard test → M5 is engine-web).
- **a log call can silently fail to emit** (NEW; live_driver:639 lone-dict %-arg → TypeError → eaten line).
- **a write must satisfy every view** — incl. the TEMPORAL form (NEW today): historical rows written before a schema
  fix do not satisfy joins written after it (the NULL-cid id=8 would vanish from a close→entry join → wallet-on-row).
- **box-is-truth: reconcile prod-live FILE-BY-FILE, never branch-first** (NEW instance: HEAD app.py's M5 drift caught
  → grafted, not wholesale-copied). **grep is not a state check** (the `$'\r'` false-CRLF; Remote-User on the wire).
  Also: **fails-open safety check**; **deploy manifest is the import closure**; **retroactive enforcement**;
  **asset-outlives-code**.

### Box quirks + operating rules + settled rulings (do not re-litigate)
- Box pytest: `-p no:pytest_ethereum`. Local tests: `.venv-webtest` (over walletops) for TestClient suites. Migrations
  applied by `pm_cli`/`init_db` (schema_version, WAL + 5s busy_timeout + `IF NOT EXISTS`), not the engine. Restarts are
  az-root (`az vm run-command`; sudo -n fails). main.py on the box is LF (git patches are CRLF via autocrlf — normalize
  both before graft).
- command-paste-rule: one `.ps1` runner in `cc\` streaming a pure-ASCII no-BOM `.sh`; present the one-liner, HALT for
  "board authorizes atomic execution", then run; read-only runners run autonomously. NO ad-hoc SSH/az.
- Karen's Authelia access: predictions ONLY; CAN run Analyze (ungated), CANNOT promote/attach/demote/refresh
  (admin-gated). The CLI is the authoritative arm/disarm (R7.d).

---

## ★ OPEN QUESTION for the next agent — the 18:13:58Z engine restart (NOT PM)
The shared engine restarted at **18:13:58 UTC** (PID **139938 → 144229**), unannounced by this session (PM's work was
pm_web-only; step-5a confirmed 139938 at 18:10). **NRestarts=0** → it was a MANUAL restart (systemd would increment
NRestarts on a crash-auto-restart), NOT a crash — so another division deliberately restarted the shared engine. It
came up CLEAN (arm not latched, M3-writer producing fresh snapshots, opposed-memory active, R8 trading). **Confirm the
CAUSE with the other divisions** — an unexplained manual restart of the shared engine on a live trading box should not
be forgotten just because it was harmless. The `live_driver:639` fix is NOT on the box, so that boot still runs the
logging footgun (benign).

## Housekeeping — backups (KEEP all; do not delete; DANGEROUS flagged precisely)
- ★ **DANGEROUS** `data/prediction_markets.db.bak_mig016_20260901T160113Z` (pre-mig-016): restoring NOW reverts schema
  16 AND all of today's journal (orders 1–66, settlements, snapshots) on a LIVE armed division — catastrophic.
- **Moderate** `data/prediction_markets.db.bak_owner_20260901T180313Z` (pre-Karen-owner).
- ★ **DANGEROUS** pre-M4 pm_web code `web/{app.py?,pm_accounts.html,pm_account.html}.bak_pmweb_20260901T180057Z +
  subdivision.py + pm.css` (NB the pre-M4 app.py backup is `web/app.py.bak_pmweb_…`): restoring the PRE-M4 code WHILE
  `PM_ADMIN_IDENTITIES=jack` is set removes scoping/gates → pm_web serves everyone Authelia admits.
- ★ **CARE** `web/app.py.bak_whale_20260901T191034Z` (the PRE-WHALE app.py = the box's M4 e7af7d8 version): restoring
  reverts whale attribution (scoping stays — it is the M4 version). Restore only to roll back the whale change, and do
  NOT confuse it with the pre-M4 `.bak_pmweb` app.py (which drops scoping). `subdivision.py` + `pm_live_subdivision.html`
  `.bak_whale_…` likewise revert only the whale change.
- **Engine-bundle backups** `execution.py / live_driver.py / db.py / main.py .bak_bundle_20260901T16*`: INERT until an
  engine restart; restoring reverts the engine bundle on the next restart. KEEP.
- **Env drop-in** `…/prediction-markets-web.service.d/pm_admin_identities.conf`: NOT a backup — removing it (+
  daemon-reload + restart) unsets `PM_ADMIN_IDENTITIES` (the M4 serve-everyone recovery).
- Older Jul/Aug backups are OTHER divisions' — untouched. Local `cc\` scratch (runners + `_build_*.py` + patch):
  benign, untracked; KEEP as the operational deploy record.

## Branch / prod-live
Branch **`pm-multiaccount-2026-09-01` @ `fd9b49c`** (pushed, local==origin). Prod-live/main-wip **`8fd95d1` NOT
advanced** (box-is-truth; every deploy a file graft). Four PM crons intact: `paper-poll */30`, `refresh 05:00`,
`paper-adjudicate 05:40`, `paper-rollup 05:50` (UTC).
