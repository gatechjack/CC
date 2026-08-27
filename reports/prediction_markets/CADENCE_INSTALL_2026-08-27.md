# CADENCE INSTALL — PM poller/adjudicate/rollup cron + refresh moved 03:20→05:00 — 2026-08-27

**Authorization (Jack):** "CADENCE INSTALL — AUTHORIZED, WITH ONE TIMING CHANGE … the cron/timer changes only."
No deploy/code/restart/prod-live, no manual runs beyond the install's own verification. Installed **19:21 UTC** into
the **azureuser crontab** (the mechanism the existing refresh already used — no new scheduling system). **PM-only.**

## ★ Answered before installing — concurrency / ordering guard (from the code, crontab, unit files)
- **There is NO single-writer guard.** `flock` in crontab = **0**; PM code has **no** `flock` / `fcntl` / `filelock`
  / `BEGIN IMMEDIATE|EXCLUSIVE`. **Schedule spacing is the ONLY protection.**
- **What a collision does:** `db.connect` (`db.py:74-79`) opens **WAL, `busy_timeout=5000`, autocommit
  (`isolation_level=None`)**. Two writers → WAL's single write-lock serializes them; the 2nd **retries ≤5 s then
  raises `SQLITE_BUSY`** (`sqlite3.OperationalError`). Commit granularity is short everywhere: refresh commits
  **per-wallet** (`ingest.py:282/332`), poll **per-whale** (`paper.py:179`), adjudicate & rollup a **single
  sub-second commit** (`paper.py:413/500`). So overlaps are almost always absorbed silently inside the 5 s window.
- **WAL guarantees:** no corruption, no silent partial, atomic commits, readers never block writers. **WAL does NOT**
  let two writers commit at once — the 2nd waits or `SQLITE_BUSY`-fails. A BUSY failure = that run aborts cleanly and
  **logs** it; recoverable next cycle (missed poll in 30 min; missed adjudicate/rollup next day or manual re-run).
- **Since spacing is the only guard:** a real guard would be `flock -n /home/azureuser/pm_cron.lock` wrapping each PM
  cron command (~1 token/line; a job firing mid-run skips that cycle). Cost ≈ 15 min. **NOT built — Jack rules.**

## 05:00 UTC sanity (Jack's ruling: move 03:20 → 05:00)
05:00 UTC = 00:00 ET / 21:00 PT — a quiet window, no US market hours, no overlap with the engine's busy trading
windows. Most tracked US/EU sports have ended and settled by then; anything late (a West-coast game resolving ~09:00
UTC, or an FOMC day) simply books the next daily cycle within the **72 h grace**. **Not awkward — fine.** (The
~22-min refresh finishing by ~05:22 is exactly why 05:00 beats 03:20: it leaves real room before adjudicate.)

## The cadence (installed; times + margins)
| job | schedule (UTC) | log | notes |
|---|---|---|---|
| poller `paper-poll` | `*/30 * * * *` (:00,:30) | `~/pm_poll.log` | full-book capture (T1) |
| daily refresh `refresh --cap 50000` | **`0 5 * * *` (05:00, MOVED from 03:20)** | `~/pm_refresh.log` | ~22 min → done ~05:22 |
| adjudicate `paper-adjudicate` | **`40 5 * * *` (05:40)** | `~/pm_adjudicate.log` | ~18 min after refresh done; no poll at :40 |
| rollup `paper-rollup` | **`50 5 * * *` (05:50)** | `~/pm_rollup.log` | ~10 min after adjudicate; no poll at :50 |

**Ordering poll → adjudicate → rollup holds:** the 05:30 `*/30` poll precedes the 05:40 adjudicate precedes the 05:50
rollup; the 05:00 refresh completes (~05:22) well before adjudicate (subset-assertion overlap avoided, ~18 min
margin). Adjudicate and rollup are **collision-free** (no poll at :40/:50). The **only** overlap is the daily 05:00
poll vs the 05:00 refresh — unavoidable with a 22-min refresh and a `*/30` poll — and it is absorbed by WAL/busy_timeout
(both commit frequently). Rollup→next-poll margin 10 min.

## Install mechanism + scope
Backed up the pre-install crontab (`~/pm_crontab_bak_20260827T192128Z.txt`, 3 lines), stripped the old
`20 3 … pm_cli.py refresh` line (`grep -v 'pm_cli.py'` — preserves the 2 non-PM lines byte-exact), appended the 4 PM
lines under a dated comment, `crontab <file>`. Pre-created the 4 log files as azureuser (cron runs as azureuser →
clean ownership, no root-owned-log repeat of the P1 gotcha).
- **Verified PM-only:** the 2 non-PM crontab lines (`telegram_lifecycle_divergence_check`,
  `replay_audit_event_write_failed` — the latter writes the LEGACY `trading_corp.db`, not the PM DB) are **untouched**.
  **Zero** `mace|pead|bitunix|kalshi` entries in the crontab. The engine, MACE, PEAD, bitunix, poly_kalshi_mlb run in
  the **engine process / their own systemd timers** — none touched. (systemd timers like `pead-earnings-watcher`,
  `trading-corp-*`, `rh-relogin` are other divisions' — out of scope, not touched.)
- **Logging / failure visibility:** each job appends stdout+stderr to its own log (`>> log 2>&1`, matching the
  existing refresh). A failure writes its error/traceback there (not silent). **Caveat (honest):** there is **no
  active alert** on a failed run — same as the pre-existing refresh cron. A log-monitor/alert is a separate concern
  (recommended, not built — Jack didn't authorize new mechanisms here).

## Verify
- **Old `20 3` refresh: gone (0), not duplicated.** pm_cli cron lines = **4** (poll 1 / refresh 1, no dup /
  adjudicate 1 / rollup 1). Non-PM lines preserved = 2. Other-division leaked = 0.
- **Next fire times** (installed 19:21 UTC): poller **19:30 UTC today**; then the first full daily cycle
  **refresh 05:00 → adjudicate 05:40 → rollup 05:50 UTC on 2026-08-28** (the cycle Jack reviews tomorrow).
- **PM DB untouched by the install:** pm_paper_trade **121**, pcs **7**, schema **9**, /farm **130301**, engine
  **676** / pm_web **24808**.

## Docs updated (03:20 → 05:00)
Forward-looking / standing references updated in `PM_REBUILD_PLAN_2026-08-26.md`: the **cadence section** (now the
installed 05:00 schedule + concurrency note), **PRE-1 window rule** (a deploy window must now be clear of 05:00 AND
not straddle a `*/30` poll), and the **init_db unattended-trigger fact** (now FOUR PM cron jobs, not one 03:20
refresh). `PM_REQUIREMENTS.md` / tickets name no `03:20`. **Dated historical records** (execution records, PK-collision
triage, deploy-completes, transition docs, the original `pk_pm_cron_*` install runners) retain "03:20" **as accurate
history** — the cron *was* at 03:20 through 2026-08-27; they are not rewritten.

## Still unauthorized
**Advancing origin/prod-live** (Jack holds it until after market close, ~45 min; origin stays c77f618 — four deploys
behind — this pass). No further manual poller/adjudicate/rollup runs.

Runners: `cc\pm_cadence_investigate.sh` (read-only), `cc\pm_cadence_install.sh`. Rollback: `crontab ~/pm_crontab_bak_20260827T192128Z.txt`.
