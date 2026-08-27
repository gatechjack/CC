# Stage-0 RUNG 2 — DEPLOY COMPLETE (2026-08-27)

**Authorized rung 2 ONLY** (deploy the 5 gated files + restart pm_web). **Rung 3 (the 22-row `active=0` write)
remains UNAUTHORIZED and NOT done.** No row writes, no poller, no adjudicator, no PK-collision fix. Behaviour-
neutral (all 114 rows `active=1`) — proven by a byte-identical `/farm`. Executed 2026-08-27 12:33–12:46Z.

## Refs
- **Code source:** branch `prediction-markets-stage0-2026-08-26` @ **`3cedb47`** (pushed).
- **prod-live ledger (LOCAL only):** `95e78c4` → **`c77f618`** (`deploy(pm-cp3b-stage0): record Stage-0 rung-2
  active-gate artifacts on prod-live (== box)`). **NOT PUSHED** — `origin/prod-live` stays **`95e78c4`**;
  `95e78c4` is an ancestor (fast-forward), MACE fork base undisturbed. Advancing `origin/prod-live` is a
  SEPARATE authorization.
- **Deploy set — 5 PM-only files** (Ruling 2), branch==box sha256:
  `db.py 76eb52b2` · `paper.py 14020d62` · `farm.py 428c4a7c` · `stats.py 2867de83` · `__init__.py 958a809a`.
  `persistence/db.py` name-guarded **ABSENT** (MACE shared). `scripts/pm_cli.py` unchanged.

## Pre-flight (read-only) — PASSED
- **Window clear:** UTC 12:33Z; next `03:20` cron **14.77h** away. **The only scheduled PM-DB writer is the
  `20 3 * * *` `pm_cli refresh`.** The **poller is manual-only — NOT scheduled** (no `paper-poll`/adjudicator
  in cron or systemd timers). The other crons write `trading_corp.db` (legacy) / a log, not the PM DB. `/farm`
  unchanged since the 12:23Z refresh → nothing wrote the PM DB in the window.
- **Box == OLD** (all 5 files sha == `95e78c4`/base) → box ran the pre-Stage-0 code; box ≠ branch.
- **B0 = `/farm` 228,569 bytes** (fresh, post-refresh-settle); healthz 200; schema 8; watchlist 114 / pinned
  114 / 18 cat / active1 114 / active0 0; paper 102; engine 89366 / pmweb 40483 NRestarts 0.
- origin/prod-live confirmed `95e78c4`.

## Deploy (fail-closed, azureuser channel — no root, no restart, no DB write)
Transfer via `git archive` tar (LF-exact blob content). **Custody** OK (tar sha `39056ba1` box==local) →
**manifest-assert** OK (only the 5 `prediction_markets/` files; no `persistence`) → **per-file CODE backup**
`~/pm_stage0_rung2_bak_20260827T123827Z` (shas == OLD; the rollback instrument) → **extract** → **re-hash
gate: all 5 box files == NEW(branch)** — box==branch became a FACT here.

## Restart (root `az vm run-command` — the one privileged, behaviour-changing act)
`systemctl restart prediction-markets-web.service`: pmweb **40483 → 132990**, `NRestarts=0`,
`ExecMainStatus=0`, active/running. Engine `trading-corp.service` **89366 unchanged**.

## Post-checks (read-only) — ALL PASSED
- `/healthz` 200; **`/farm` 200 and 228,569 bytes == B0** → **behaviour-neutral proof holds**.
- **All 7 gated queries execute on schema 8** (no `no such column: active`): farm_categories 18 · farm_rows
  114 · farm_summary candidates 0 · query_scoreboard 113 · assert_pinned_subset `{n_pinned:14, n_refreshed:14,
  unrefreshed:[]}` (no raise) · poll_pinned SQL 114 · seeded-review SQL 114.
- **18 categories / 114 pairs unchanged** — correct: rung 3 not done, so all `active=1`, nothing removed
  (the 15/92 state is post-rung-3).
- schema **8**; poller sees **114**; pm_paper_trade **102**; watchlist 114 / active1 114 / active0 0.
- engine **89366** unchanged; pmweb **132990** new, NRestarts 0, active/running; all 5 NEW modules import.
- **★ NRestarts correction:** a manual `systemctl restart` does **not** increment `NRestarts` (that counter
  tracks `Restart=`-triggered auto-restarts). The correct clean-restart signal is **PID-changed + NRestarts
  still 0 + active/running** (observed). `NRestarts > 0` would have meant a crash-loop. (PM_REBUILD_PLAN POST-8
  wording corrected accordingly.)

## Permissions correction (content-only deploy)
The `tar` extract left the 5 files **664** (group-write) vs the prior **644**. Not world-writable (security
bar held), but a drift from convention — **`chmod 644` restored 644 == OLD** on all 5 (azureuser:azureuser),
making the deploy content-only. Verified mode parity NEW==OLD==644.

## Rollback material (KEPT until stale)
`~/pm_stage0_rung2_bak_20260827T123827Z/` — the 5 pre-deploy (OLD) files. **Rung-2 rollback = restore these +
`systemctl restart prediction-markets-web`** (code-only; no DB revert — rung 2 wrote nothing to the DB). The
rung-1 DB backup is the WRONG tool (reverts schema + loses writes).

## Cleanup
Removed the ad-hoc-refresh **sentinel** and the **staging tar** (`~/pm_rung2_stage.tar`). Local
`cc\pm_rung2_stage.tar` removed. Backup dir KEPT. **`pm_refresh.log` `ADHOC_REFRESH_START` marker LEFT in
place** — see the note below.

### pm_refresh.log marker — LEFT, with a note (my call)
The ad-hoc refresh added a non-JSON `ADHOC_REFRESH_START …` line to `pm_refresh.log`. **Nothing in production
reads that log by count** (the cron only appends). The only parser is the triage tooling
(`pm_pkcollision_triage_ro.sh`), which counts **JSON summary blocks via `json.raw_decode`** and **skips
non-JSON lines** — so the marker causes **no off-by-one** (it is not counted as a run). Removing it would mean
editing a live log to delete a harmless line that also documents the authorized run. **Decision: leave it;
count JSON blocks, never lines.** (Recorded in PM_REBUILD_PLAN so a future parser author does not `wc -l`.)

## Scope held
Rung 3 NOT done (funnel intact: 18 cat / 114 pair / active0 0). No PM-DB write during rung 2. No poller/
adjudicator. No PK-collision fix. `origin/prod-live` `95e78c4` untouched; engine + MACE untouched.
