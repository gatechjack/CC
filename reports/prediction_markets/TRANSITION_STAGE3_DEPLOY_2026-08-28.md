# TRANSITION — Stage 3 (Money Layer) BUILT, UNSHIPPED → DEPLOY handoff (2026-08-28)

**SUPERSEDES `TRANSITION_STAGE2_COMPLETE_2026-08-28.md`.** That doc described Stage 2 (live). This one is the
handoff for the **fresh agent who will DEPLOY the built-but-unshipped Stage 3 stack under Jack's per-rung
authorization.** Read this FIRST, then `STAGE3_PLAN_2026-08-28.md` (the full plan + §12–§18 execution records).

**★ YOU ARE THE DEPLOY AGENT. Your predecessor BUILT six rungs (R1–R6); NONE are deployed. Live is schema 9,
running Stage 2. Do not re-build. Do not re-litigate rulings (§K). Deploy one rung, Gate-A against the RUNNING
prod tree, verify, HALT — Jack authorizes each rung.**

---

## A. STATE — OBSERVED 2026-08-28T22:26Z (read-only box read, not recalled)

| Fact | Observed value |
|---|---|
| Branch | `prediction-markets-stage3-2026-08-28` |
| Branch tip (local == origin) | **`920b2a1`** |
| `origin/prod-live` | **`166b5ab`** (PMCC render-then-stream perf deploy off `7220e32`; FF; **the DEPLOY BASE — but re-fetch, see §H**) |
| `origin/main` | `2c8aa23` (untouched by today's work) |
| `95e78c4` (the CP3b ledger base) ancestor of prod-live? | **YES** |
| Engine (`trading-corp.service`) MainPID | **`53046`**, active since **21:30:05Z** (= MACE's restart, §C) — SHARED |
| pm_web (`prediction-markets-web.service`) MainPID | **`42343`**, `NRestarts=0`, up since **04:03:58Z** — **UNDISTURBED** by the engine bounce (decoupled; it never went down) |
| pm_web `/healthz` | `{"status":"ok","service":"pm_web","pm_db_schema_version":9}` (port 8081, loopback) |
| Live PM DB schema | **9** (`PRAGMA quick_check = ok`) |
| Money-layer tables live | **0 / absent** (nothing from R1–R6 deployed) |
| `pm_live` arm rows in live legacy `agent_state` | **0** (nothing armed) |
| Counts | pm_whale 14 · pm_closed_position 29852 · pm_category_stats 114 · pm_watchlist 114 (active 92, pinned 92, candidate 0) · pm_paper_trade 140 (open 113, pending 6, closed 21, stale 0, void 0) · pm_paper_category_stats 9 |
| **Did the 21:30Z engine bounce disturb PM?** | **NO.** pm_web unchanged (42343, 0 restarts, healthz ok); PM DB intact (schema 9, quick_check ok); **cadence undisturbed — the 30-min poller fired at 22:00:02Z, AFTER the bounce.** |

**The four PM cadence cron jobs (azureuser crontab), confirmed running:**
- `*/30 * * * *` — `pm_cli paper-poll` → `~/pm_poll.log` (last fire 22:00:02Z; next ~every 30 min)
- `0 5 * * *` — `pm_cli refresh --cap 50000` → `~/pm_refresh.log` (daily 05:00Z; last 05:00:01Z)
- `40 5 * * *` — `pm_cli paper-adjudicate` → `~/pm_adjudicate.log` (daily 05:40Z)
- `50 5 * * *` — `pm_cli paper-rollup` → `~/pm_rollup.log` (daily 05:50Z; last 05:50:02Z)
- Plus systemd timer `trading-corp-pm-watchlist-deep.timer` (weekly deep watchlist refresh; next Sun 2026-08-30 13:11Z) — engine-side.

---

## B. ★ THE DEPLOY LADDER (built-but-unshipped; deploy IN THIS ORDER, one rung per Jack authorization)

The box `~/trading_corp` has **NO `.git`** — deploy = **copy the branch-tip file content onto the RUNNING prod
tree + activate**, then Gate-A. Files combine multiple build rungs (e.g. `web/app.py` carries R3+R6; `db.py`
carries 010+011), so you ship the **branch-tip (`920b2a1`) version** of each file. Sequence:

### Rung 1 — SCHEMA (migrations 010 + 011) · file `trading_corp/prediction_markets/db.py` · **PM-ONLY**
- **Deploys:** migration 010 (pm_account / pm_subdivision / pm_subdivision_order) + migration 011
  (pm_subdivision_attachment) + the `db.SCHEMA_HEAD` constant (R2). **010 AND 011 land TOGETHER — see §D.**
- **Activation:** `init_db()` on the next boot of any PM entrypoint (pm_cli / the poller cron / pm_web) → **9 → 11**,
  each migration in its own transaction. **Behaviour-neutral** (all tables created EMPTY, read by no live code).
- **Backup instrument:** online DB backup FIRST (`sqlite3 … ".backup"` or the copy-verify runner) — keep it.
- **Post-checks:** `MAX(version)=11`; `PRAGMA quick_check=ok`; migration-008/009 rows intact; existing counts
  unchanged; the four cadence jobs still run.
- **STOP:** any table REBUILD (the mig-002/006 lesson) or a non-atomic `schema_version` bump.

### Rung 2 — MATCHER · file `trading_corp/data/mlb_poly_kalshi_match.py` · **★ SHARED**
- **★ SHARED with the LEGACY `poly_kalshi` copy-trader** (it imports `match_poly_to_kalshi`, kept **BYTE-IDENTICAL**
  by R2; the legacy copy-trader test is 35/35 no-regress). Gate-A the legacy consumer, not just PM.
- **Activation:** pure functions — **INERT** until the engine calls them (R7). No restart.
- **Post-checks:** legacy copy-trader 35/35; the moneyline path byte-identical. **STOP:** any moneyline regression.

### Rung 3 — WEB (R3 read-only `/live` + R6 farm actions) · **PM-ONLY** · pm_web restart · **DEPLOYED LIVE 2026-08-29 (`STAGE3_R3_DEPLOY_2026-08-29.md`)**
> **★ CORRECTIONS (2026-08-29 deploy):** (1) **`pm_cli.py` is NOT in rung 3 — it MOVED to rung 4.** It
> module-level-imports `arm` (a rung-4 module) and the LIVE cron runs it every 30 min, so shipping it before `arm.py`
> ImportErrors the poller (+ refresh/adjudicate/rollup). See the SCHEDULING LESSON in §G. (2) **Activation = pm_web
> restart via az-root** (QUIRK #6), NOT ssh — pm_web is a root-owned system unit. The FILE deploy is ssh
> (azureuser-owned); the RESTART is az-root. Ran as a 3-runner split (ssh stage → az-root restart w/ self-rollback →
> ssh post-check). (3) The deploy set is the **EXPLICIT 10-file manifest**, NEVER `git diff prod-live..branch` (§G rule).
- **Files (the 10, all PM-ONLY):** `prediction_markets/web/app.py`, `subdivision.py` (NEW), `farm_actions.py` (NEW),
  templates `pm_dashboard.html`, `pm_shell.html`, `pm_live_{list,subdivision,404}.html`,
  `partials/pm_prospects_rows.html`, `partials/pm_watchlist_rows.html`. (**`pm_cli.py` → rung 4.**)
- **Depends on Rung 1** for the money-layer tables (but is **defensive** — honest-empty if absent, so it can even
  precede Rung 1). **Activation: a pm_web restart ONLY** (loads app.py + templates + subdivision + farm_actions).
- **Backup:** code backup of the touched files + record pm_web PID (currently 42343).
- **Post-checks:** legacy pages (`/`, `/farm`, `/farm/{cat}`, `/watchlist/...`) byte-identical or intended-diff;
  `/live` 200 (tiles once a sub-division exists + is attached); the THREE buttons render as POST **forms** (prospects
  "Promote to watchlist"; watchlist "Demote" + "Promote › {account}"); POST → **303**; a GET on a mutating path → **405**;
  `/live` pages stay **read-only** (no `<form>`); `healthz` schema 11; three-bases held.
- **STOP:** a POST reaches an order / places anything; a demote DELETES `pm_paper_trade`; a GET mutates; `/live` grows a form.

### Rung 4 — EXECUTION modules (R4 chokepoint + R5 arm) · `prediction_markets/execution.py`, `arm.py`, **`scripts/pm_cli.py`** · **PM-ONLY, ENGINE-side, INERT**
- **★ `scripts/pm_cli.py` DEPLOYS IN THIS RUNG (moved from rung 3, §G scheduling lesson):** the branch `pm_cli.py`
  module-level-imports `arm`, so it can only ship once `arm.py` is present — deploy `execution.py` + `arm.py` +
  `pm_cli.py` together. `pm_cli.py` is at `scripts/` (azureuser-owned → ssh-`cp`-deployable) and the live cron runs it,
  so verify the next `*/30` poll after deploying it. Its new `live-arm`/`live-attach` subcommands are inert until R7.
- **Deploys** the DRY-RUN chokepoint (R4) + the arm/kill control plane (R5). **Nothing imports `execution` yet**
  (no engine driver — that is R7). So these files deploy **INERT: zero runtime effect, NO engine restart needed.**
- `arm.py` is reachable via `pm_cli live-arm/disarm/status` (settable post-deploy) but the arm state does NOTHING
  until R7's driver runs the arm-gated cycle. `arm.py`'s WRITE path lazily calls the engine's `set_agent_state`
  (writes the legacy `agent_state` — the money-layer bridge; engine/CLI side only).
- **Post-checks:** the files import cleanly (`python -c "import trading_corp.prediction_markets.execution, ...arm"`);
  `pm_live` arm rows stay **0** unless you deliberately arm (do not); nothing placed. **STOP:** anything is armed/placed.

---

## C. ★ WHICH RUNGS NEED AN ENGINE RESTART (a SEPARATE authorization from a pm_web restart)

- **Rungs 1–3 do NOT need an engine restart.** Rung 1 activates on the next `init_db()`; Rung 3 activates on a
  **pm_web restart** (pm_web is `prediction-markets-web.service`, DELIBERATELY decoupled — `not After=trading-corp.service`).
- **Rung 4 (execution.py + arm.py) is ENGINE-side but INERT on deploy** — no driver imports it, so **no engine
  restart is required to ship it**.
- **★ The engine restart is load-bearing at R7 (NOT authorized here):** R7 wires the engine driver that runs the
  arm-gated cycle, and THAT needs an engine restart. **The engine (`trading-corp.service`) is SHARED with PMCC,
  MACE, PEAD, and bitunix — MACE restarted it once tonight already (21:30Z). An engine restart is a SEPARATE
  authorization that Jack rules on; a deploy agent must NOT restart the engine to "activate" R4/R5.** A pm_web
  restart is PM's own and low-blast; an engine restart bounces every division.

---

## D. Migrations 010 AND 011 land in ONE rung — the reasoning

`init_db()` applies each pending migration `version > current` in order, **each inside its own transaction**, then
records the version; re-running is a no-op. Both 010 and 011 are **additive PURE DDL** (`CREATE TABLE/INDEX IF NOT
EXISTS`; no data writes, no table rebuild, no config). So a single boot of any PM entrypoint takes live **9 → 10 →
11** atomically-per-migration with no intermediate risk. **There is no reason to split them into two rungs**, and
splitting would leave a pointless transient schema-10 window. Ship `db.py` once; `init_db` does the rest.

---

## E. ★ THE MACE BOX-AHEAD SITUATION (do NOT "reconcile"; verify it is confined)

> **★ CORRECTION (2026-08-28, rung-1 DEPLOY agent — SUPERSEDES the "exactly 8 MACE files" framing below; Jack
> acknowledged the under-count and retired the confinement STOP criterion).** A full-tree git-tracked-blob compare
> of the box against `origin/prod-live` (`166b5ab`) shows the box is ahead for **far more than 8 files** — `166b5ab`
> is a **PMCC-only** fast-forward off `7220e32` and therefore **lags every division's direct-to-box deploys**, not
> just MACE's. Measured box-vs-prod-live drift (git-tracked files): **9 MACE-namespace** (`mace/broker_port.py`,
> `mace/config.py`, `mace/domain.py`, `mace/execution.py`, `mace/manager.py`, `mace/rh_broker.py`, `mace/strategy.py`,
> `web/mace_view.py`, **+ `web/templates/mace_live.html`** — the last one escaped a naive `web/mace_*` glob, the
> KXMLBRUNLINE-class miss) **AND ~10 non-MACE package files**, every one tracing to a documented deploy: PEAD
> (`agents/strategies/pead_strategy.py` / `pead_signal.py` / `pead_backtest_driver.py` — the 2026-08-26 ISSC→IA +
> Part-3 rename-defense), shared `brokers/robinhood.py` (box `e90af223`), `brokers/kalshi_live.py`, `brokers/base.py`,
> `brokers/tastytrade.py`, `main.py`, `persistence/db.py`, `agents/divisions/_observer_test.py` — plus
> operationally-edited config/data (`config/nasdaq_composite.txt` = the ISSC→IA universe edit, `config/mace.yaml`,
> `data/research_starter_universes/large_mid_cap.json`). **None is unexplained; none is PM's to reconcile.**
> **What IS clean and confined:** the ENTIRE `trading_corp/prediction_markets/` package AND the rung-2/3 shared files
> (`data/mlb_poly_kalshi_match.py`, `utils/secrets.py`) match `166b5ab` **byte-for-byte** — so PM's Gate-A is a
> **per-file** check against prod-live (done at rung-1 deploy: box `db.py` `106e2b03` == prod-live before overwrite),
> and the broad box-ahead is other divisions' unpushed work. **The `MISSING=946` in that compare is the box being a
> deployed SUBSET** (tests/reports/deploy-archives/scripts/runbooks never ship to the box), not a gap. Runner:
> `cc\pm_r1_drift_ro.*` (read-only full-tree blob compare).

MACE deployed (wing-pricing + exit-disposition) and restarted the shared engine at ~21:30Z (PID 49441 → **53046**,
stable). **MACE PUSHED NOTHING: `origin/prod-live` is still `166b5ab`, `origin/main` untouched.** MACE's changes are
**BOX-AHEAD AND LOCAL-ONLY**, folding into a deferred prod-live advance AFTER predictions ships.

- **★ Therefore the BOX is AHEAD of `origin/prod-live` for exactly 8 MACE-ONLY files** — `trading_corp/mace/*.py`
  and `trading_corp/web/mace_*` (mace_view etc.). A Gate-A that asserts **box == prod-live WILL SHOW DRIFT on those
  8 files.** **THAT DRIFT IS EXPECTED AND IS NOT PM's TO FIX.** Do NOT reconcile it, do NOT overwrite those files,
  do NOT treat it as a STOP — **but DO verify the drift is confined to exactly those MACE files and nothing PM/shared.**
- **MACE did NOT touch `brokers/robinhood.py`** (box md5 `e90af223ef645153971208523ef9a16a` — matches the recorded
  `e90af223`, PEAD hook intact) **or `persistence/db.py`** (box md5 `d6395badc3b9b21e13175950d6162949`, baseline —
  Gate-A this at deploy). Shared broker + shared DB are byte-unchanged.
- **★ LEDGER-ACCURACY CAVEAT (subtle, load-bearing):** if PM advances `prod-live` after deploying, that ledger
  commit records PM's artifacts **on top of `166b5ab`** — it will **NOT** contain the box-only files that lead it:
  per the §E CORRECTION that is **~9 MACE-namespace + ~10 non-MACE package files (PEAD / brokers / main.py /
  persistence/db.py)**, NOT merely "8 MACE files". So
  immediately after a PM prod-live advance, **`prod-live` will NOT fully describe the box** (MACE folds theirs in
  afterward). That is by design — but **the PM ledger commit message MUST SAY SO** ("prod-live advanced to <sha>;
  box additionally carries 8 MACE-only files [mace/*.py, web/mace_*] not yet in prod-live, MACE to fold in") rather
  than implying box == prod-live.

---

## F. Every SHA + what each rung contains

| Rung | Code | Docs/tests | Contents |
|---|---|---|---|
| Plan/R0 | — | `53605ec` | Plan ACCEPTED; 6 rulings; R0 whale list (2 whales SDTrading + xifutloong3, both pinned+active mlb) |
| **R1 schema** | **`1ee0d63`** | `cf141be` (head-pins), `0bd33f8` (plan) | migration 010 money-layer tables (PURE DDL) |
| **R2 matcher** | **`2e07b3f`** | `1bd0762` (plan) | 3-dim MLB matcher (moneyline+total+spread) + `db.SCHEMA_HEAD`; legacy 35/35 no-regress |
| **R3 /live** | **`24ae03a`** | `10bfe1b` (testfix), `0982016` (plan) | read-only `/live` list + routes; tile-on-create; defensive |
| **R4 chokepoint** | **`06e6ffa`** | `77c8d97` (plan) | central execution chokepoint DRY-RUN; structurally cannot place; NO-leg notional fix |
| **R5 arm/kill** | **`c8f3606`** | `ff3640e` (plan) | arm/kill-switch (default DISARMED); latching auto-disarm; leg-aware seed fix; latch guard |
| **R6 farm actions** | **`df51f2d`** + amendment **`1dde22f`** | `72f9a8c` (plan), `920b2a1` (testfix) | 3 farm actions + migration 011; auto-create/permanent/visible; demote-refuses-when-live |

**Branch tip = `920b2a1`.** Every rung was box-scratch GREEN (R6: 11/11 unit + 8/8 route + full PM suite + integration
on a live-prod COPY). Two adversarial reviews per money-touching rung; both R4 and R5's confirmed CRITICALs were the
NO-leg lens (§J); R6's were RACE conditions (auto-create/demote), fixed with atomic transactions.

---

## G. The six STANDING BOX QUIRKS (carry them)
1. **Broken `pytest_ethereum`** — always run pytest with **`-p no:pytest_ethereum`**.
2. **`az run-command` serializes + truncates** — a transient "run in progress" is ANOTHER agent; **retry, do not
   interpret**; stdout may truncate.
3. **tar deploys land 664** — force **644** after extract (`find … -type f -exec chmod 644`).
4. **32-bit PowerShell needs Sysnative OpenSSH** — the runners resolve `ssh`/`scp` via `System32`/`Sysnative`.
5. **`trading_corp/data/` is NOT azureuser-writable — root-owned files need the `az` root path** (found 2026-08-29,
   rung 2). The `data/` dir is owned by `197609:197121` (Windows numeric UID/GID — an artefact of a Windows-side `scp`
   that preserved numeric IDs, NOT PM's to fix) at `755`, and shared legacy files inside it (e.g.
   `mlb_poly_kalshi_match.py`) are `root:root 644`. azureuser (uid 1000) cannot overwrite them via ssh `cp` (permission
   denied); `sudo` is forbidden and has no NOPASSWD anyway. Deploy such files via **`az vm run-command RunShellScript`
   on RG-SHARED-PROD/tc-prod-vm** (runs as root; keep the file `root:root 644` — do NOT chown-to-azureuser a shared
   legacy file for PM's convenience). The rest of the tree (`prediction_markets/`, `brokers/`, `agents/`, `web/`,
   `persistence/`, `utils/`) IS azureuser-owned and ssh-`cp`-deployable (rungs 1/3/4).
6. **A pm_web (or engine) RESTART needs az-root — there is NO azureuser path** (found 2026-08-29, rung 3). Both are
   SYSTEM services (`/etc/systemd/system/*.service`, unit files `root:root`). **`User=azureuser` governs what the
   PROCESS RUNS AS, NOT who can MANAGE THE UNIT** — restarting a system unit needs root; `sudo` is forbidden + has no
   NOPASSWD. So a PM file deploy is ssh (files azureuser-owned) but its ACTIVATION restart is `az vm run-command
   RunShellScript` (root) — same channel as the engine. Read the unit file (`systemctl show -p FragmentPath`), don't
   assume ssh can restart.

**★ STANDING DEPLOY RULES (carry them):**
- **EXPLICIT MANIFEST, never the raw diff.** The deploy set is always an enumerated file list, NEVER "whatever
  `git diff prod-live..branch` shows." The branch was cut before PMCC's `166b5ab`, so the diff lists PMCC's LIVE files
  (`web/data.py`, `web/routes.py`, `division.html`, `_pmcc_pricing.html`, config, tests) as "changes" — deploying any
  would REVERT another division's live work. Gate-A + manifest-assert against the explicit list; name-guard the rest out.
- **★ WHEN SOMETHING BECOMES SCHEDULED, EVERY PLAN THAT TOUCHES IT NEEDS RE-READING.** `pm_cli.py`'s rung placement
  was wrong not because the file changed but because its RISK CLASS did: before the cadence was installed a broken
  `pm_cli.py` was harmless (nothing ran it until someone typed a command); after, it runs unattended every 30 min. A
  file's blast radius can change without the file changing — when a thing becomes cron/timer-driven, re-read every plan
  that touches it and re-derive its rung by the IMPORT GRAPH, not the file list.

---

## H. ★ THE DEPLOY BASE IS WHATEVER `origin/prod-live` IS AT THAT MOMENT
`prod-live` moved **twice today** via PMCC (`7220e32` → `9e9890a` → `166b5ab`). **Never a remembered SHA. FETCH
FIRST, re-confirm the tip, base on what it ACTUALLY is, and Gate-A against the RUNNING prod tree (the box files),
not against a git blob you assumed.** FF-only; keep `95e78c4` reachable. Confirm the tip again IMMEDIATELY before
any prod-live advance.

## I. Three other agents active (concurrency discipline)
- **PMCC, MACE, PEAD/bitunix share the engine** (`trading-corp.service`). **An engine PID change is probably NOT
  PM's** (this session saw 676 → 49441 → 53046, all other divisions). **pm_web (`prediction-markets-web.service`,
  PID 42343) is PM's ALONE — an unexplained change there IS a finding.**
- **`az` Conflicts = serialization** (another agent's run in progress) → **retry, not partial-apply.**
- **SHARED files** (`data/mlb_poly_kalshi_match.py`, `utils/secrets.py`, `brokers/robinhood.py`, `persistence/db.py`)
  — re-verify **box == base** immediately before any deploy; **STOP on unexplained mismatch** (but see §E: the 8
  MACE files are EXPECTED drift, not a STOP).

---

## J. ★ THE NO-LEG LENS — a DOMAIN PROPERTY, not a one-off bug
The Kalshi book is YES-centric; a **NO** leg's committed cash is `count * (1 − yes_price)`, never `count * yes_price`.
This has bitten **four times**, twice in **risk math that passed every body-inversion test** while exposure was
mis-accounted:
1. `kalshi_live.py` NO-leg price (`1 − P`) — the original "$163.84 bug" (pre-existing, hardened).
2. **R4:** the per-order/daily/exposure **caps** used the YES `limit_price` for a NO copy → over-reject + mis-account.
3. **R5:** the `Journal` **seed** query summed `count*price` for all legs → NO-leg exposure under-seeded on restart →
   gates 5/6 bypassable. (Fixed: `CASE WHEN outcome_leg='yes' … ELSE count*(1−price)`.)
4. The matcher carries `.leg` on `MatchResult` so the chokepoint never RE-DERIVES it.
**Rule for R7+: anywhere a price/count/notional/fill is used, ask WHICH LEG it belongs to.** The adversarial review
is where #2 and #3 were caught — keep running it on money math.

## K. Rulings NOT to re-litigate (Jack DECIDED)
- **Disarm blocks EXITS too** (off is off; the exit-exempt budget gates are a different question; human flattens).
- **Sub-divisions are PERMANENT** (never delete/GC an empty one; the row + lifetime stats survive; dashboard
  visibility is keyed on ATTACHMENTS, reconciled with tile-on-create — auto-create-always-attaches shows immediately).
- **Auto-create-on-promote** (atomic; account credentialed must pre-exist; category-join structural).
- **Demote REFUSES when live-attached** (enforces live ⊆ pinned; detach first).
- **Sizing = FIXED**; **kill-switch default = fail-safe DISARMED on restart**; **tile on CREATE**; **whale-exit =
  Option D**; **target account = `KALSHI` (original)**; **scope = moneyline + totals + SPREADS** (settlement of
  totals/spread is a HARD R7 gate).

## L. What comes AFTER (neither authorized; the DEPLOY comes first, per-rung)
1. **R5.5 boot-reconcile** — its OWN rung, slotted BEFORE R7 (scoped in the plan §17d): the journal-seed half is
   done (R4 `Journal`, leg-corrected); the Kalshi-portfolio COMPARISON half needs the authenticated broker +
   position-matching + tolerances (partial fills / settled-vs-open / fees) — not small, not build-only-shaped.
2. **R7 — the MONEY GATE:** first live order on `KALSHI`, smallest size, engine armed for one sub-division. Needs
   an engine driver + an engine restart (§C) + the §8 go-live gate (a proven successful POST first — no live order
   has EVER succeeded on this platform).
3. **R8 — parallel test:** Jack-MLB copies the same 2 legacy whales side-by-side with legacy, observation-only.

## M. Honest OPEN ITEMS (do not paper over)
- **MLB game-total contract UNCONFIRMED:** `BASEBALLENTITYSTAT` may or may not govern `KXMLBTOTAL` (its Underlying
  mentions game totals — suggestive, not conclusive). A HARD R7 gate for totals.
- **Polymarket's settlement side has NEVER been read** — every divergence claim (§15d) assumes Polymarket settles on
  the official score. Probably right, unconfirmed. Recorded as an assumption.
- **No live order has EVER been placed** by this platform. The one exercise 401'd (legacy stale key, likely cleared
  by the 08-27 reboot, UNVERIFIED, and never on the target account). R7 requires a proven successful POST first.
- **No alerting on the four unattended cron jobs** — a silent poller/adjudicate/rollup failure would go unnoticed.
- **The flock guard is costed but UNBUILT** — overlapping cron runs (the 05:00 refresh vs a 30-min poll) rely on the
  designed poll/refresh overlap guard; a hard flock lock was scoped, not built.

---

*Written 2026-08-28 by the build agent (through one compaction) for the fresh DEPLOY agent. Six rungs built @
`920b2a1`, NONE deployed; live schema 9. DO NOT DEPLOY without Jack's per-rung authorization. Gate-A against the
RUNNING prod tree. The engine restart (R7) is a separate authorization. HALT.*
