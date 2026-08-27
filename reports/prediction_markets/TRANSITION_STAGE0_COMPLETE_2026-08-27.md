# Prediction Markets — TRANSITION: Stage 0 COMPLETE → next code agent (2026-08-27)

> **First line, per the anti-drift rule — restate the THREE LISTS and their THREE BASES from memory before touching code:**
> **Prospect** = completed trades (`pm_closed_position`→`pm_category_stats`, code `status='candidate'`) ·
> **Watchlist** = our paper trades (`pm_paper_trade`→`pm_paper_category_stats`[Stage 1], code `status='pinned'`) ·
> **Live** = live trades (P3 tables, not built). One (wallet,category) pair can be on all three at once showing a
> different number on each. Keeping the three bases separate is the invariant whose violation caused the rebuild.

**This supersedes `TRANSITION_STAGE0_TO_NEXT_2026-08-27.md`** (written during the mixed state; its "/farm renders
unchanged is CORRECT" is now FALSE — the code is deployed and the 22 rows are written).

---

## (i) STAGE 0 IS COMPLETE AND DEPLOYED LIVE — the single most important fact
The mixed state is OVER. All three rungs of the Stage-0 ladder are done, and `/farm` now shows **15 categories /
92 pairs** (the deployed, gated, 22-removed state). If you see 18/114 or a byte-identical-to-228,569 `/farm`,
something is wrong — the live board is 15/92 at 182,835 bytes.

| Rung | What | When (UTC) | Record |
|---|---|---|---|
| 1 | migration 008 (`active`/`removal_reason`/`removal_ts` + `ix_pm_watchlist_active`) applied to live | 2026-08-27 02:15Z | plan §Rung-1 |
| 2 | deploy 5 gated PM files + pm_web restart (40483→132990) | 2026-08-27 12:33–12:49Z | `RUNG2_DEPLOY_COMPLETE_2026-08-27.md` |
| 3 | 22-row `active=0` write (cbb3/fifwc8/unknown11) | 2026-08-27 13:10:36Z | `RUNG3_STAGE0_CLOSEOUT_2026-08-27.md` |

Branch `prediction-markets-stage0-2026-08-26` @ **`df1300b`** (pushed). `origin/prod-live` advanced
`95e78c4 → c77f618` (fast-forward; `95e78c4` remains an ancestor — MACE forks from it).

## (ii) END-OF-STAGE-0 SNAPSHOT — all values OBSERVED 2026-08-27 (~13:32Z), not recalled
- **Refs (ls-remote):** `origin/prod-live` **`c77f618`** (`95e78c4` an ancestor ✓) · `origin/main` **`2c8aa23`**
  (untouched) · branch **`df1300b`** local == origin.
- **Box == branch:** all 5 PM artifacts (`db.py`/`paper.py`/`farm.py`/`stats.py`/`__init__.py`) sha256 **==
  `c77f618`** (== the deployed NEW values).
- **Live PM DB:** schema **8**; pm_watchlist **92 active / 22 inactive** (114 total); **pm_paper_trade 102**.
- **pm_web:** `/healthz` 200; `/farm` 200, **182,835 bytes** (15 cat / 92 pair). PID **132990**, NRestarts 0.
- **engine** `trading-corp.service` PID **89366** (unchanged all of Stage 0).

## (iii) ROLLBACK INSTRUMENT + its limits
- **Current:** `~/pm_stage0_rung3_dbbackup_20260827T130737Z.db` — schema 8, taken just before the rung-3 write
  (114 rows all active=1), sha256 `9066a392…b9fa78`, integrity_check ok. Restoring it **reverts rung 3 only**
  (back to 18 cat / 114 pair, all active). It does **not** undo the code deploy (rung 2) — that rolls back via
  the per-file code backup `~/pm_stage0_rung2_bak_20260827T123827Z/` + a pm_web restart.
- **Limits:** restoring the DB backup requires **stopping pm_web first** (it holds live handles) and **loses
  every PM write since 13:07Z**. Restore is Jack's call, not an agent's initiative.
- The rung-1 **schema-7** backup was **DELETED** 2026-08-27 (obsolete footgun — schema 7 under schema-8 code).
  See `~/PM_DB_BACKUPS_README.md` on the box: it names the current instrument and flags that restoring any
  older-schema backup (5 obsolete CP3a/CP3b-2 schema-4/6 copies remain, recommended for deletion) is a footgun.

## (iv) WHAT'S NEXT — Stage 1, NOT prepared
**Stage 1 = close the paper lane** (the reframe's foundation). It is **NOT authorized and NOT prepared** — no
branch, no code. Its load-bearing change (`PM_REBUILD_PLAN §B` + Stage 1): **re-base the adjudicator
(`paper.py::adjudicate`) on gamma `/markets` resolution**, not `pm_closed_position` row-presence, or the paper
lane inherits the `/closed-positions` loss-omission. Expect a **blank Watchlist for weeks** — that is correct
(0 of 102 open paper trades had resolved at adjudicator-readiness). Build the paper rollup as **migration 009
`pm_paper_category_stats`**.

**Migration renumber (Stage 0 consumed 008):** Stage 1 = **009** (`pm_paper_category_stats`); Stage 4 = **010**
(`pm_search_run`); Stage 5 = **011** (`loss_completeness`, optional).

## (v) REQUIREMENTS — read `PM_REQUIREMENTS.md` first (reachable from `prediction_markets/__init__.py` docstring)
- **R1** — the Stage-1 paper rollup MUST gate `active=1` (a deactivated pair shows NOWHERE, including the paper
  scoreboard). Durably recorded in R1, not just a migration comment.
- **R2** — Stage 4 search needs a **CATEGORY-LEVEL** exclusion at candidate SELECTION; a per-row flag can't stop
  a *newly discovered* whale in an excluded category (the seed writer defaults `active=1`). Mechanism unchosen;
  do not implement (Stage 4 unauthorized).
- **R5** — INGEST STAYS ALL-CATEGORIES; category exclusion lives at the QUERY layer (the `active` gate), the
  TILE set, and candidate SELECTION — **never** at ingest.
- Also R3 (resolution from gamma, never `/closed-positions`) and R4 (every displayed number needs a BASIS test).

## (vi) OPERATING RULES (this workstream ran under these)
Report a SHA for every commit; **verify on the box, never narrate** ("a report without a commit SHA is not
evidence"); **no live step without Jack's explicit per-step authorization**; never edit legacy; `poly_kalshi_mlb`
+ MACE + the trading engine are OUT OF SCOPE; box access via validated `.ps1`/`.sh` runners (fail-closed for any
live write); **do NOT move `origin/prod-live` except by fast-forward with Jack's authorization** (MACE forks from
its tip). Stop-and-report at forks; don't rationalize a failed check (a byte-verify caught a 664/644 perm drift
in rung 2 — fix, don't explain away).

## (vii) KNOWN BOX QUIRKS
- venv **`pytest_ethereum` plugin is broken** — disable for any PM box-scratch (`-p no:pytest_ethereum`).
- **`az vm run-command` serializes + truncates stdout** — mutate via the root `az` channel (RG `RG-SHARED-PROD`,
  VM `tc-prod-vm`), then VERIFY via `ssh azureuser` streaming (full output). Code deploy + pm_web restart go via
  root `az`; read-only DB pulls + azureuser-owned writes go via `ssh azureuser` (`mode=ro`, stdlib).
- pm_web = `prediction-markets-web.service` on **`127.0.0.1:8081`** (loopback, behind Caddy+Authelia; direct
  curl to :8081 bypasses auth for `/healthz`/`/farm`). PM DB = `~/trading_corp/data/prediction_markets.db`.
- **azureuser cannot `sudo`** (no NOPASSWD) → `systemctl restart` needs root `az`. A **manual `systemctl
  restart` does NOT bump `NRestarts`** — clean-restart signal is PID-changed + NRestarts-still-0 + running.
- The only scheduled PM-DB writer is the **`20 3 * * *` cron** (`pm_cli refresh --cap 50000`); the **poller is
  MANUAL-only** (verified 2026-08-27). Any deploy/write window must be provably clear of the 03:20 cron —
  `/farm` renders `pm_category_stats` which a refresh rewrites, so a refresh mid-window breaks any byte compare.
- `pm_refresh.log` contains a non-JSON `ADHOC_REFRESH_START …` marker (from the 2026-08-27 ad-hoc refresh) —
  any log-summary tooling must **count JSON blocks (`json.raw_decode`), never lines (`wc -l`)**.

## (viii) OPEN / UNRESOLVED (honestly labelled)
1. **PK-collision on whale `0x767a…d8ac5` (MadeiraIsland) — CONFIRMED TRANSIENT, ticket DEFERRED behind Stage 0.**
   The 2026-08-27 03:20 cron failed on a `pm_closed_position` PK collision; a read-only reproduction (04:35Z) and
   an authorized ad-hoc refresh (12:00–12:23Z, 14/14 OK) proved it a **transient settlement/pagination race**, not
   corrupt data (`PK_COLLISION_TRIAGE_2026-08-27.md`, `AD_HOC_REFRESH_2026-08-27.md`). It is **orthogonal to
   Stage 0** and does not block anything shipped. A fix (if any) is an **ingest-robustness ticket** (dedupe-on-PK-
   before-guard / retry-later) — its own authorization, sits **behind Stage 0 entirely**. If a future cron
   re-collides on this whale, it is this anomaly, not a regression.
2. **Engine PID history `37596 → 89366` never explained.** PID 89366 up since 2026-08-26 18:26:16Z (`NRestarts=0`),
   so a restart happened ~18:26 Aug 26; the *reason* was never established. Read-only PID-history probe authored
   (`cc\pm_pid_history_probe.ps1`) but never run. Engine is OUT OF SCOPE for PM — flagged so it doesn't drop.
3. **Box backups — CLEANED 2026-08-27 (authorized).** The 5 obsolete PM DB backups (CP3a 1×schema-4 + 3×schema-6,
   CP3b-2 1×schema-6) and the rung-2 code backup dir were **DELETED** (survivor re-verified first; box==c77f618
   re-verified before the code-dir delete). **~119.1 MB reclaimed.** The ONLY PM DB backup that remains is the
   current rollback instrument `pm_stage0_rung3_dbbackup_20260827T130737Z.db` (schema 8) — see
   `~/PM_DB_BACKUPS_README.md`. Prior-session CP3a/CP3b-2 **non-DB** artifacts (tar, extract dir, `.py.bak`, poll
   logs, `pm_cp3b2_gate2_bak_…/`, `…_box.sh`) were **left untouched** (not Stage 0's; other divisions may use them).
