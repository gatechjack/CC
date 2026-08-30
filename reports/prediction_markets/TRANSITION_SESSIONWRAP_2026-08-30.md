# PREDICTION MARKETS — SESSION-WRAP TRANSITION (2026-08-30)

> **★ THIS SUPERSEDES `TRANSITION_STAGE3_POSTDEPLOY_2026-08-29.md` AND `TRANSITION_R7C_FIRSTCUT_2026-08-29.md`.**
> Read this first. It carries the live state, the one thing left before money (R7.f), the queued small rungs,
> the standing lessons, and everything a fresh agent needs (box quirks, rules, settled rulings, the NO-LEG lens).
> Observed live state stamped **2026-08-30T14:56:16Z**. Order path: **nothing armed, no order ever placed.**

---

## 0. WHAT IS LIVE — four of six stages

**Stages 0–3 deployed (data → paper → farm funnel → money-layer schema), Stage 4 deployed AND RUN.** The Farm
League is **populated for the first time**: `pm_cli search` (run_id=1, Sports leaderboard) discovered 50 whales,
backfilled 47, and wrote **134 candidates across all 15 allowlist categories**; the funnel is now actively used
(135 candidates / 91 pinned as of the snapshot — Jack promoting/demoting through the UI). The **tile-vanish defect
is fixed and live** (a category exists by allowlist membership, not by having pinned whales).

**Deployed this session (the three deploys prod-live now records — see §7):**
- **Stage 4 SEARCH first cut** (2026-08-30 ~01:24Z): `search.py`, `search_run.py`, `db.py` (migration 013 →
  schema 13, `pm_search_run`), `stats.py` (the query_scoreboard completeness gate), the R4 Prospects screen
  (`pm_prospects_rows.html`, `pm_farm_category.html`, `pm_sort.js`, `pm.css`, `web/app.py`).
- **`pm_cli search` wiring** (2026-08-30 ~02:47Z): `scripts/pm_cli.py` — the entry-point that invokes the search
  pipeline; poll-confirmed clean.
- **Farm tile-rule fix** (2026-08-30 ~06:26Z): `farm.py`, `web/app.py`, `pm_dashboard.html`, `pm_farm_league.html`
  — category existence = the allowlist; empty-watchlist categories render; deactivated (non-allowlist) 404.

**NOTHING is armed. No order path is reachable. No order has ever been placed.** `pm_subdivision_order = 0`,
`agent_state` has 0 `pm_live` rows (arm DISARMED), the money chokepoint (`execution.py`) is behind the arm gate,
and the R7 live driver is wired but DISARMED. One sub-division exists: `kalshi_jack/mlb` (active), SDTrading
attached.

---

## 1. ★ R7.f IS THE ONLY THING LEFT BEFORE MONEY — and it waits on MARKET CONDITIONS, not on us

Everything on the R7 ladder is proven up to the irreversible step. **R7.f is the first (and only remaining)
money gate, and it is blocked on a market condition we do not control:** the read-only would-place check
(`cc\pm_r7f_index_match_ro.ps1`) returns **0 would-place** not because of a defect but because SDTrading's current
MLB positions all map to **dead in-progress-game Kalshi books** (one-sided, `$0` depth) or are out-of-window /
non-MLB. **The R7.f gate is a CLEAN would-place, which requires SDTrading to hold a position on a game that has
NOT started yet — a pre-first-pitch game with a LIVE two-sided Kalshi book.** Re-run
`cc\pm_r7f_index_match_ro.ps1` near first pitch when such a game exists; that is the trigger.

### The six-item go-live gate (§8, VERBATIM from R7_PLAN_2026-08-29.md §1) — irreversible step last

| §8 item | Needs ARM? | Needs engine RESTART? | Can run BEFORE the restart? | Notes |
|---|---|---|---|---|
| **2. Dry-run parity** (V2 body byte-correct incl. NO-leg 1−P, via the new engine) | No | No | **Yes** — a `pm_cli` dry-run in the service env against a live-fetched Kalshi index; largely proven at R4 box-scratch, R7 refreshes it against real authenticated quotes | read-only |
| **1. Successful POST demonstrated** (demo first, then real) | (real part = arm) | No for the auth proof | Yes for the auth/read proof | **Demo part BLOCKED — no demo creds. Re-scoped.** The real POST *is* item 3. |
| **5. Kill-switch proven** (arm, kill mid-session, next order blocked, halt survives restart, one auto-disarm fires) | Yes (then disarm) | Only the "survives restart" sub-check | Mostly yes (dry-run place_fn) | prove BEFORE the real order so the kill switch is trusted when you arm for real |
| **4a. Sign-convention check** (position_fp +YES/−NO on a REAL position, Q4) | No | No | **Yes — ideally BEFORE exclusivity shutdown** (read the co-tenant's existing NO positions) | authenticated READ, no placement |
| **3. One smallest-size real order** | **YES** | **Yes** (driver must exist + matcher loaded) OR the pm_cli-timer driver | **NO — this is the irreversible step** | 1 contract, moneyline, one sub-division armed |
| **4b. Reconcile the fill** (portfolio + balance delta + fee convention + boot-reconcile) | (post-order) | No | No (needs the fill) | authenticated reads + R5.5 `reconcile_account` |
| **6. Idempotency across restart** (same signal, kill+restart, no double order) | (post-order) | **Yes** (the restart is the test) | No (needs the placed order to prove no re-place) | durable-journal dedup, proven live |

**Ordered ladder** (each rung its own authorization): sign-check read (4a, pre-exclusivity) → exclusivity ops →
build the driver → dry-run parity + kill-switch proof (2,5) → deploy + engine restart (3-precondition) → **ARM +
PLACE ONE ORDER (3, IRREVERSIBLE)** → reconcile the fill (4b) → idempotency across restart (6) → disarm.

### ★ THE FIRST RECONCILE AFTER THE FIRST FILL IS LOAD-BEARING — hand-inspected

**`position_fp`'s sign is STILL UNPROVEN.** `boot_reconcile.py` assumes `position_fp > 0 == long YES`,
`< 0 == long NO`, but the existing reader (`brokers/kalshi.py:_fetch_positions`) only ever used `abs(position_fp)`,
so the sign has never been exercised. R7.a found the KALSHI account FLAT (no NO/YES holding to test against), so
`+YES/−NO` is **neither confirmed nor refuted**. It is the **6th instance of the NO-leg lens** and it is
**confirmed AT the first order's reconcile (R7.g), BY HAND.** If it INVERTS, that is a finding that changes
`boot_reconcile.py` before anything else proceeds. Do not automate past this; inspect the first fill's portfolio
row against the placed order by hand.

---

## 2. QUEUED SMALL RUNGS (each its own authorization; none started)

1. **Widen `_cmd_search`'s failure capture** — it clamps errors to `repr(e)[:80]`, which TRUNCATED the
   `condition_id` in the two PK-collision failures from the search run, so they cannot be classified from the log
   alone. Own tiny rung (pm_cli.py only).
2. **The two-wallet re-pull to classify the PK collisions** — `0x4956f69a…2ee2c7` and `0xe4a7b5c3…a2dc87` failed
   the ingest `_assert_no_pk_collision` guard on backfill (isolated, stored=0, nothing half-written). A targeted
   READ-ONLY re-pull of those two would classify transient-pagination-race vs structural-duplicate. Distinct from
   the 08-27 `0x767a` *refresh* collision (ruled historical). Do NOT fix — classify.
3. **The seven cap-truncated whales** — `0x2c33/0xe907/0x01c7/0xde9f/0x032e/0x6ac5/0xf68a` each hit the 8000-row
   default cap (stored=8000, `backfill_complete=0`, correctly unrankable). Completable with a higher `--cap`.
4. **The doubleheader ambiguity ticket** (`TICKET_doubleheader_matcher_ambiguity_2026-08-30.md`) — a Kalshi ticker
   carries HHMM, the Polymarket slug does not, so a doubleheader can map to the wrong game. Belongs **before R8**
   widens the live copy beyond a single game.

---

## 3. STANDING LESSONS THIS SESSION ADDED (memory-backed)

1. **NAVIGATION MUST BE DRIVEN BY EXISTENCE, NOT DATA PRESENCE.** The tile-vanish defect's class: a container
   (tile/tab/menu) that outlives its contents. Drive nav off a fixed existence authority (an allowlist constant,
   a created-and-persists record), never off a count/status/join hitting rows — else a state transition that
   empties the data strands it behind a vanished nav entry. Same shape as sub-division permanence. For every
   transition (demote/remove/close/deactivate) ask: *can this strand data behind a nav entry it just made vanish?*
   (memory: `nav-driven-by-existence-not-presence`.)
2. **A FEATURE IS NOT SHIPPED UNTIL SOMETHING CAN INVOKE IT.** `pm_cli search` was named in every prompt from R2
   and the module got built + tested, but the CLI entry-point fell between two rungs — it errored `invalid choice`
   on a fully-deployed first cut. Every rung states what INVOKES what it built, or says explicitly that nothing
   does yet. A box-scratch that tests a function proves the function, not that anything invokes it. (memory:
   `feature-not-shipped-until-invokable`.)
3. **THE GROUNDED SEARCH COST MODEL.** The first real search ran **92m36s vs a ~24–49 min estimate (~2× high
   end)**. The miss was the TAIL, not the median: 7 whales hit the 8000-row cap = 160 pages each = **59% of all
   calls from 7 whales**; the flat "~30 calls/whale" modelled neither the cap nor the right tail. Plus throttle:
   ~1900 calls / 5556s ≈ **~2.9 s/call** (429 backoff), not ~1/s. **Next estimate = separate the mass (median ~14
   calls) from the cap-hitting tail (160 calls each), price calls at ~3 s under throttle → ~1500–2000 calls,
   ~75–100 min for ~50 Sports whales.**

---

## 4. FRESH-AGENT ESSENTIALS

### Box quirks (the six, plus the load-bearing extras)
1. **pytest** on the box venv needs `-p no:pytest_ethereum` (a stray plugin errors collection otherwise).
2. **`az vm run-command`** serializes + truncates long output; keep az scripts short, read results via a separate
   ssh read.
3. **`tar`** created on the box lands mode 664 in places; force perms explicitly on deploy (`chmod 644` + assert).
4. **ssh/scp**: use `C:\Windows\System32\OpenSSH` (Sysnative for 32-bit hosts); the runners auto-resolve.
5. **`trading_corp/data/` is ROOT-owned / non-azureuser-writable** (owner 197609:197121) → the matcher
   (`data/mlb_poly_kalshi_match.py`) deploys via **az-root**; but **`trading_corp/prediction_markets/` IS
   azureuser-writable** → all PM package files deploy via plain ssh. `scripts/pm_cli.py` the FILE is
   azureuser-writable even though `scripts/` the DIR is not → in-place `cat >` overwrite works (no dir op).
6. **pm_web / engine restart needs az-root** (the systemd units are root:root; `User=azureuser` governs the
   PROCESS, not unit management). **FILE deploy = ssh; ACTIVATION restart = az-root** (`az vm run-command … systemctl
   restart prediction-markets-web`). The ENGINE (`trading-corp.service`) is only ever restarted for R7 money-layer
   changes — never for a pm_web/display change.

Extras: the package lives at **`/home/azureuser/trading_corp/trading_corp/prediction_markets/`** (double
`trading_corp`). `enabled:false` in `divisions.yaml` is SAFE even though the slug appears in the systemd
`--live-divisions` arg (`load_divisions` is enabled-only; wiring skips `!enabled`). All PM + config files are
azureuser-writable via ssh; ONLY the restart needs az-root.

### The rules
- **EXPLICIT MANIFEST, never `git diff prod-live..branch`.** The branch predates prod-live's PMCC base, so a raw
  diff lists unrelated files. Name the files you deploy.
- **Gate-A checks TRANSITIVE imports** in the SERVICE dir before any restart, not just the changed files (the R7.e
  lesson: a live_driver import of an unshipped `boot_reconcile.py` failed the first restart). Prove the import
  chain resolves on the box before activating.
- **The sanctioned channel** (`command-paste-rule`): box access via a Jack-executed `.ps1` runner; present ONE
  one-liner, wait for board authorization, then run THAT runner. Pure ASCII, LF, no BOM; ssh strips CR/BOM via
  `tr -d '\r\357\273\277' | bash`. Do NOT run ad-hoc box commands.
- **deploy ≠ prod-live advance** (Jack's ruling). The branch carries the ledger; prod-live advances deliberately,
  FF-only, at deployment milestones, with a message stating what it does NOT contain.

### Settled rulings — do NOT re-litigate
- Category existence = the 15-category allowlist (`search.CATEGORY_ALLOWLIST`, the single edit point; no table, no
  migration). Pair-grain `pm_watchlist.active` is orthogonal.
- Search backfill is ON-DEMAND (Ruling 1): a complete whale is read from the DB, never auto-re-pulled; the refresh
  button is the only ad-hoc re-pull. Candidate write is gated `backfill_complete=1`. Rank on cost-ROI, never win%.
- R7: exclusivity DONE; driver = an ENGINE TASK; placement = option-b; `fixed_stake_usd=0.01` → exactly 1¢;
  xifutloong3 re-attaches at R8; the liquidity floor = `liquidity_ratio * notional` (default 0.75, per-cycle,
  leg-correct: a NO leg's notional = count·(1−yes_price)).

### ★ The NO-LEG lens (load-bearing, applied six times)
Whenever you compare, price, or reconcile a Kalshi position, a bare magnitude will PASS a side-flip. A YES leg and
a NO leg on the same market are not interchangeable: the NO leg's price is 1−P, its notional is count·(1−P), and
its `position_fp` sign is negative. The lens: *does this computation treat a NO holding correctly, or would it
silently accept a YES↔NO inversion?* Boot-reconcile compares SIGNED net-per-ticker for exactly this reason. The
6th instance — `position_fp`'s sign itself — is UNPROVEN and hand-inspected at the first fill's reconcile (§1).

---

## 5. LEAVE-IT-RUNNING SNAPSHOT — observed 2026-08-30T14:56:16Z

- **Git:** branch `prediction-markets-stage3-r55-2026-08-29` @ `1000e72` (code work through the tile fix at
  `8402310`; this wrap's doc commit on top). **origin/prod-live advanced `c88beea` → `e5fbc60`** (FF-only; the §7
  ledger commit); `95e78c4` (MACE fork base) stays reachable.
- **Processes:** engine `trading-corp` PID **76416** (NRestarts 0); pm_web `prediction-markets-web` PID **89704**
  (NRestarts 0). pm_web on 127.0.0.1:**8081**.
- **HTTP:** `/healthz` 200 (`pm_db_schema_version:13`); `/` 200; `/farm` 200; `/farm/atp` 200; `/live` 200.
- **PM DB schema 13.** Counts: pm_whale **61**, pm_closed_position **121157**, pm_category_stats **612**,
  pm_watchlist **248** (active: candidate **135** / pinned **91**), pm_roster 248, pm_subdivision **1**
  (kalshi_jack/mlb, active), pm_subdivision_attachment 2, **pm_subdivision_order 0**, pm_paper_trade 227,
  pm_account 1, pm_paper_config 3, **pm_search_run 1**, pm_open_position 9752. Candidates by category (all 15
  populated): atp 14, cs2 10, epl 10, fed 1, golf 1, mlb 12, nba 10, nfl 6, nhl 6, soccer 20, tennis 8, ucl 10,
  ufc 10, wnba 7, wta 10.
- **Arm / order state:** `agent_state` pm_live rows **0** (DISARMED); `pm_subdivision_order` **0** (no order ever
  placed). All 15 categories currently have ≥1 pinned whale → no live zero-pinned tile to serve as the
  allowlist-vs-pinned smoking gun (proof rests on Gate-A + the pm_web PID change + the box-scratch fixture test).
- **Cron (4 PM entries):** `*/30 * * * *` → `pm_cli paper-poll` (`~/pm_poll.log`); `0 5 * * *` → `refresh --cap
  50000`; `40 5 * * *` → `paper-adjudicate`; `50 5 * * *` → `paper-rollup`. Next fire after the snapshot: the
  15:00Z poll, then daily at 05:00/05:40/05:50Z.

---

## 6. HOUSEKEEPING — this session's artifacts (LIST + RECOMMEND; Jack authorizes any deletion)

**On the box (`~`):** every deploy backup is a rollback that would revert code to the PRE-deploy state. Now that
all deploys are verified live + healthy for hours, each rollback would REGRESS the box, so all are KEEP-for-record
(small) unless Jack wants a purge.
- `pm_tilefix_deploy_backup_20260830T062306Z` — the 4 tile-fix files pre-deploy. Restoring = re-introduce the
  tile-vanish bug. **KEEP** (rollback insurance until Jack is fully satisfied); safe to remove on his word.
- `pm_cli_search_deploy_backup_20260830T024732Z` + `pm_cli_search_deploy_marker` — pm_cli.py pre-search-wiring +
  the poll-confirm marker. Restoring = un-ship `pm_cli search`. **KEEP** (or remove — the marker is spent).
- `s4deploy_backup_20260830T011111Z` — the 6 Stage-4 files pre-deploy + PM-DB backup. Restoring = un-ship Stage 4
  (but schema 13 + the search data would then mismatch older code). **KEEP.**
- `pm_search_run_20260830T032934Z.log` — the real search run's per-wallet log (the estimate-miss + PK-collision
  evidence). **KEEP** (it is the classification evidence for the queued re-pull rung).
- Prior sessions' (NOT this session, listed for completeness): `r7f_backup_…`, `r7e_backup_…` (R7 rollbacks — KEEP,
  R7 in progress); `mace_*` backups + `pead_paper_purge_backup` + the 2026-08-27 `pm_stage*/pm_t1/pm_rollup2`
  db-backups + `~/backups` — OLD, other divisions / superseded; **candidates for a Jack-authorized purge**, none
  mine.
- **Leftover scratch tars** (`~/mace_*.tar`, `~/pm_r7e_*.tar`, `~/pm_r7f_deploy.tar`, `~/pm_rung2/pm_stage2/pm_t1
  _deploy.tar`, `~/pm_cp3a_gate2.tar`) — OLD deploy tars that predate this session; my runners self-clean (none of
  mine remain). **REMOVE candidates** (Jack authorizes); restoring nothing — they are inert intermediates.
- `/tmp/pm_*` — assorted old temp files (cron out/err, prior before-snapshots, `pm_rows.txt`). Inert; `/tmp` clears
  on reboot. **REMOVE candidates**, no effect.

**Local (`C:\Users\AA Incorporado\cc\`):** the session's `.ps1`/`.sh` runner pairs (box-scratch, deploy steps,
search launch/monitor, snapshot, investigate). **KEEP** — they are the audit trail + are re-runnable (e.g.
`pm_r7f_index_match_ro.*` is the R7.f trigger check; `pm_session_wrap_snapshot.*` re-observes live state). The
worktree `C:\Users\AA Incorporado\cc-pm-stage3-r55-2026-08-29-wt` is the working checkout — KEEP.

**Recommendation:** KEEP all of this session's backups + the search log until Jack confirms he is done validating;
the OLD (pre-session) box tars + `/tmp` files are the only clear purge candidates, and they are not mine to delete.

---

## 7. PROD-LIVE ADVANCE RECORD (2026-08-30) — see the ledger commit message for the full statement

`origin/prod-live` advanced from `c88beea` to `e5fbc60` (child of c88beea, FF-only; `95e78c4` stays
reachable) recording the THREE deploys since the Stage-3 advance: **Stage 4 SEARCH, `pm_cli search` wiring, and
the tile fix — 13 code artifacts, fresh box re-hash 2026-08-30T14:56Z, 13/13 byte-matching this commit.** The
commit message states what prod-live does NOT contain: R7's order-path deploys (execution.py's liquidity floor,
arm.py, live_driver.py, boot_reconcile.py, the main.py driver block, divisions/strategies yaml) — deploy ≠
prod-live advance, R7 is mid-ladder — plus MACE and PEAD direct-to-box work. The shared `db.py` (e30d936a)
necessarily carries R7.f's migration 012 (the `liquidity_ratio` schema column) as a predecessor of Stage 4's
migration 013; the SCHEMA is recorded, the R7 order-path CODE that uses it is not. **Do NOT read this advance as
box == prod-live.**
