# TRANSITION — Stage 3 DEPLOYED + prod-live ADVANCED + first sub-division LIVE (2026-08-29)

**★★ SUPERSEDED by `TRANSITION_R7C_FIRSTCUT_2026-08-29.md` (2026-08-29). Read THAT first.** R5.5 shipped, R7 is planned,
R7.a proved the authenticated transport, and R7.c is a FIRST CUT (unproven / unwired / undeployed). This doc's
"R5.5 NOT built" is now stale. ★★

**★ SUPERSEDES `TRANSITION_STAGE3_DEPLOY_2026-08-28.md` (which was the pre-deploy handoff). Read THIS first.** The
built R1–R6 stack is now FULLY DEPLOYED across four rungs, `prod-live` is advanced, and the first account + first
sub-division are live. This doc is the handoff for the agent who picks up **R5.5 → R7 → R8** (none authorized).

---

## A. ★ WHAT IS LIVE NOW (observed 2026-08-29T04:04Z; do NOT assume anything is armed)
- **Stage 3 fully deployed, live PM DB `schema 11`.** `origin/prod-live` = **`c88beea`** (FF off `166b5ab`; `95e78c4`
  reachable). Branch `prediction-markets-stage3-2026-08-28` @ **`73366b4`** (local == origin, pushed).
- **Four money-layer tables present.** `pm_account` = **1** (`kalshi_jack`, active, `secret_ref='KALSHI'`,
  label 'Jack KALSHI'). `pm_subdivision` = **1** (`kalshi_jack, mlb`, active, DDL-default config
  `market_types='moneyline,total,spread'`, `sizing_mode='fixed'`, all caps NULL→code CONFIG_DEFAULTS).
  `pm_subdivision_attachment` = **1** (`kalshi_jack, mlb`, wallet `0x16bb…8492` = **SDTrading**, active,
  `source='promote_to_live'`). **`pm_subdivision_order` = 0 — NO order has ever been placed.**
- **The matcher (`data/mlb_poly_kalshi_match.py`) is deployed but the ENGINE still runs its OLD in-memory copy**
  (byte-identical for moneyline) until an engine restart — that restart is R7's.
- **`execution.py` + `arm.py` are deployed INERT — nothing imports/drives them until R7.** **arm is DISARMED**
  (`pm_live` rows in legacy `agent_state` = **0**; absent→DISARMED is the R5 fail-safe). **NO order path is
  reachable** from anything running now. `pm_cli.py` is live (its `live-*` subcommands work; the cadence poll runs it).
- pm_web `/live` shows the **Jack KALSHI · MLB** tile ("created · no live trades yet"); `/live/kalshi_jack/mlb` lists
  SDTrading. Engine PID **53046**, pm_web PID **59422**, both NRestarts 0. Four cron jobs healthy (poll `*/30`,
  refresh 05:00, adjudicate 05:40, rollup 05:50 UTC).

## B. ★ WHAT COMES NEXT, IN ORDER (none authorized — Jack rules each rung; build→verify→HALT, never chain)
1. **R5.5 — boot-reconcile (its OWN rung, BEFORE R7).** Two halves: (a) **seed the high-water marks from the journal
   without emitting — ALREADY DONE** (R4's `Journal`, leg-corrected). (b) **compare the journal against Kalshi's ACTUAL
   portfolio + detect a mismatch — NOT built:** needs the authenticated broker, a Kalshi-ticker↔journal
   position-matching rule, and tolerance semantics (partial fills / settled-vs-open / fees). It is itself a
   first-authenticated-portfolio read (§C item 4). Not small, not build-only-shaped. R5 shipped the
   `boot_reconcile_mismatch` LATCH as the seam; the comparison engine is this rung.
2. **R7 — the MONEY GATE: first live order** on `KALSHI`, smallest size (1 contract), engine armed for the ONE
   sub-division. **Needs an ENGINE RESTART** (to load the driver + the new matcher) — the engine
   (`trading-corp.service`) is SHARED with PMCC/MACE/PEAD/bitunix, so that restart is a **SEPARATE authorization** and
   must go via **az-root** (§E quirk 6). The §C go-live gate must pass first.
3. **R8 — parallel test:** Jack-MLB copies the SAME legacy whales side-by-side with the live legacy `poly_kalshi_mlb`
   (unedited), observation-only. SDTrading is already attached; xifutloong3 is the other. NOTE the SCOPE RULING: the
   sub-division is WIDER than legacy by design (moneyline+totals+spreads), so a divergence from legacy is EXPECTED, not
   a defect.

## C. ★ R7'S GO-LIVE GATE — VERBATIM from `STAGE3_PLAN_2026-08-28.md §8` (it enumerates SIX items; "seven" was a
miscount — item 1 bundles demo + first real order). **No live order has EVER been placed by this platform; the one
historical attempt 401'd on a STALE KEY on the LEGACY division (a DIFFERENT account), likely cleared by the 08-27
reboot but UNVERIFIED.**
1. **A successful order POST is demonstrated** — demo first, then one 1-contract real order on the `KALSHI` account.
2. **Dry-run parity on the box** — the built V2 body is byte-correct for real whale signals (incl. a NO-leg case:
   prove the `1−P` inversion holds *through the new engine*). (Moneyline only for first-live per the earlier ruling.)
3. **One smallest-size real order on `KALSHI`, manually observed** — places, fills or benign-nofills, FillEvent matches.
4. **Reconcile that fill against Kalshi's portfolio/balance delta** — esp. the per-contract-vs-total fee convention
   (`kalshi_live.py:188`). Exercises boot-reconcile once by hand.
5. **Kill-switch proven** — arm, kill mid-session, next order blocked, halt survives restart; one auto-disarm fires.
6. **Idempotency across restart** — same signal, kill+restart, no double order.

## D. HOW TO DEPLOY ON THIS BOX (the mechanics, learned the hard way)
- **`prediction_markets/` + `scripts/pm_cli.py` FILE are azureuser-owned → ssh `cp` (in-place overwrite works even
  though `scripts/` DIR is non-writable).** `data/*` files are `root:root` → **az-root** (`az vm run-command
  RunShellScript` on RG-SHARED-PROD/tc-prod-vm). Any RESTART is az-root (quirk 6).
- **Standing deploy shape:** timing guard clear of 05:00–05:50 UTC + a poll gap → baseline → Gate-A (box == prod-live
  per file) → backup → place → forced `chmod 644` + re-hash gate (sha256 + perms + owner) → activate → post-checks.
  For the web layer the activation is a **pm_web restart via az-root**, split into 3 runners (ssh stage → az-root
  restart w/ self-rollback → ssh post-check) so the fail-closed boundary sits BEFORE activation.

## E. THE SIX STANDING BOX QUIRKS (carry them)
1. **Broken `pytest_ethereum`** — run pytest with `-p no:pytest_ethereum`.
2. **`az run-command` serializes + truncates** — a transient "run in progress" Conflict is ANOTHER agent; retry, don't
   interpret; keep root-script output terse (stdout truncates).
3. **tar deploys land 664** — force `chmod 644` after extract + assert perms in the re-hash gate.
4. **32-bit PowerShell needs Sysnative OpenSSH** — runners resolve `ssh`/`scp` via System32/Sysnative.
5. **`trading_corp/data/` is NOT azureuser-writable — root-owned files need the `az` root path.** The dir is owned by
   `197609:197121` (Windows-numeric, a Windows-side `scp` artefact) `755`; files inside (e.g.
   `mlb_poly_kalshi_match.py`) are `root:root 644`. Deploy them via az-root RunShellScript, keep them `root:root 644`;
   NEVER chown-a-shared-legacy-file-to-azureuser for PM's convenience.
6. **A pm_web (or engine) RESTART needs az-root — there is NO azureuser path.** Both are SYSTEM services
   (`/etc/systemd/system/*.service`, unit files `root:root`). **`User=azureuser` governs what the PROCESS RUNS AS, NOT
   who can MANAGE THE UNIT.** `sudo` is forbidden + has no NOPASSWD. Read the unit file
   (`systemctl show -p FragmentPath`), don't assume ssh can restart.

## F. ★ THE TWO DEPLOY RULES THAT COST US TONIGHT (do NOT rediscover them)
- **EXPLICIT MANIFEST, NEVER the raw diff.** The deploy set is ALWAYS an enumerated file list, never "whatever
  `git diff prod-live..branch` shows." The branch was cut BEFORE the PMCC `166b5ab` perf deploy, so the diff lists
  **PMCC's LIVE files** (`web/data.py`, `web/routes.py`, `division.html`, `_pmcc_pricing.html`, config, tests) as
  "changes" — deploying any would REVERT another division's live work. Gate-A + manifest-assert against the explicit
  list; name-guard everything else OUT (e.g. `pm_cli.py`/`arm.py`/`execution.py` were kept out of the wrong rungs).
- **★ WHEN SOMETHING BECOMES SCHEDULED, RE-READ EVERY PLAN THAT TOUCHES IT.** `pm_cli.py`'s rung placement was wrong —
  not because the file changed, but because its RISK CLASS did: the cadence was installed AFTER the phased plan was
  drafted, so a broken `pm_cli.py` went from "harmless until someone types a command" to "runs unattended every 30
  min." **A file's blast radius can change without the file changing.** Re-derive a scheduled file's rung by its
  IMPORT GRAPH, not the file list. (`pm_cli.py` imports `arm` → it moved to rung 4 with `arm.py`+`execution.py`.)

## G. ★ THE CORRECTED BOX-AHEAD PICTURE (never use a file-count confinement gate)
`origin/prod-live` LAGS the box. It is a lagging FF pointer that only captures the division that last advanced it
(PMCC → `166b5ab`, then PM → `c88beea`). The box ALSO carries OTHER divisions' unpushed direct-to-box deploys:
~**9 MACE-namespace** files (`mace/*.py`, `web/mace_view.py`, `web/templates/mace_live.html`) + ~**10 non-MACE package**
files (PEAD `pead_strategy`/`pead_signal`/`pead_backtest_driver`; `brokers/{robinhood,kalshi_live,base,tastytrade}.py`;
`main.py`; `persistence/db.py`; `agents/divisions/_observer_test.py`) + operationally-edited config
(`config/nasdaq_composite.txt`, `config/mace.yaml`). **Every one traces to a documented division deploy — none is a
rogue deploy.** The transition-doc's original "box ahead for exactly 8 MACE files" was an UNDER-COUNT and its
file-count confinement STOP was retired (Jack). **Gate-A PER FILE against prod-live** (`prediction_markets/**` and the
shared files DID match prod-live byte-for-byte, which is what made PM's deploys safe); never assert box == prod-live.
When PM advances prod-live, the commit MESSAGE must SAY prod-live does not fully describe the box (the `c88beea` message
does).

## H. RULINGS NOT TO RE-LITIGATE (Jack DECIDED)
- **DISARM blocks EXITS too** (off is off; the exit-exempt budget gates 5/6/8 are a DIFFERENT question — a budget cap
  must never strand an exit; the kill switch is not a budget; when disarmed you flatten by hand on Kalshi).
- **Sub-divisions are PERMANENT** — never delete/GC an empty one; the row + lifetime stats survive; dashboard
  visibility is keyed on ATTACHMENTS (≥1 active), reconciled with tile-on-create (auto-create always attaches → shows
  immediately; drops off the dashboard when the LAST attachment detaches, but the row + URL persist).
- **Auto-create-on-promote** (ONE atomic `BEGIN IMMEDIATE`; the credentialed ACCOUNT must pre-exist — never
  auto-created; category-join is structural).
- **Demote REFUSES when live-attached** (`attached_live_detach_first`) — enforces **live ⊆ pinned**; detach first (CLI).
- **Sizing = FIXED** for first-live (Kelly column carried, not built); **kill-switch default = fail-safe DISARMED on
  restart**; **tile on CREATE**; **whale-exit = Option D** (`/activity` SELL trigger + `/positions` size-reduction
  confirm; bias-down); **target account = `KALSHI` (original)**; **scope = moneyline + totals + SPREADS** (totals/spread
  settlement is a HARD R7 gate).
- **`account_id` is a PERMANENT PK** (`pm_account` PK → `pm_subdivision` PK → the `/live/{account_id}/…` URL,
  case-sensitive, NO delete). `kalshi_jack` is now permanent.

## I. ★ THE NO-LEG LENS — a DOMAIN PROPERTY, not a one-off bug (carry it into ALL R7+ money math)
The Kalshi book is YES-centric; a **NO** leg's committed cash is `count * (1 − yes_price)`, never `count * yes_price`.
This has bitten **FOUR times**, TWICE in risk math that passed every body-inversion test while exposure was
mis-accounted:
1. `kalshi_live.py` NO-leg price (`1−P`) — the original "$163.84 bug" (pre-existing, hardened).
2. **R4:** the per-order/daily/exposure CAPS used the YES `limit_price` for a NO copy → over-reject + mis-account.
3. **R5:** the `Journal` SEED query summed `count*price` for all legs → NO-leg exposure UNDER-seeded on restart →
   gates 5/6 bypassable. (Fixed: `CASE WHEN outcome_leg='yes' THEN count*price ELSE count*(1−price) END`.)
4. The matcher carries `.leg` on `MatchResult` so the executor NEVER re-derives it.
**Rule for R7+: anywhere a price / count / notional / fill is used, ask WHICH LEG it belongs to.** The adversarial
review is where #2 and #3 were caught — keep running it on money math.

## J. HONEST OPEN ITEMS (do not paper over)
- **MLB game-total contract UNCONFIRMED** — whether `KXMLBTOTAL` runs on `BASEBALLENTITYSTAT` or its own doc is
  suggestive, not conclusive. A HARD R7 gate for totals.
- **Polymarket's settlement side has NEVER been read** — every divergence claim assumes Polymarket settles on the
  official score. Probably right, unconfirmed. Recorded as an assumption.
- **No alerting on the four unattended cron jobs** — a silent poller/refresh/adjudicate/rollup failure goes unnoticed.
  (The R4 pm_cli-import gate we ran is a one-time proof, not ongoing monitoring.)
- **The flock guard is costed but UNBUILT** — overlapping cron runs rely on the designed poll/refresh overlap guard; a
  hard `flock` lock was scoped, not built.
- **`secret_ref` is DECORATIVE until R7** — nothing reads `pm_account.secret_ref` today (execution.py inert;
  `promote_to_live` checks only `account_id`+`active=1`). It becomes load-bearing when R7's broker resolves creds
  (legacy convention `main.py:2998–3007`: `secret_ref='kalshi_karen'`→Karen keys, else→shared `KALSHI_API_KEY_ID/PEM`).
  `kalshi_jack`'s `secret_ref='KALSHI'` is set correctly now to avoid an R7 surprise.

## K. Ledgers + runners (the paper trail)
Per-rung ledgers: `STAGE3_R1_DEPLOY_2026-08-28.md`, `STAGE3_R{2,3,4}_DEPLOY_2026-08-29.md`;
`PMACCOUNT_CREATE_2026-08-29.md` (the account + promote context). Plan: `STAGE3_PLAN_2026-08-28.md`; durable
requirements: `PM_REQUIREMENTS.md`. Runners live in `C:\Users\AA Incorporado\cc\pm_*` (read-only `*_ro.*`; deploy
`pm_r{1,2,3,4}_deploy.*`; az-root `pm_r{2,3}_az_*`; prod-live `pm_prodlive_verify_ro.*`; account `pm_account_create.*`).
Rollback backups on the box: `~/pm_stage3_r1_bak_*`, `~/pm_stage3_r2_azbak_*` + `~/pm_stage3_r2_bak_*`,
`~/pm_stage3_r3_bak`, `~/pm_stage3_r4_bak`, `~/pm_account_create_bak_*` — see the housekeeping note in the session
report for which are live rollback material vs obsolete.

*Written 2026-08-29 by the deploy agent (through several compactions). Stage 3 shipped in 4 rungs + a prod-live advance
+ the first account + sub-division. NOTHING is armed; NO order path is reachable. R5.5 / R7 / R8 are UNAUTHORIZED. HALT.*
