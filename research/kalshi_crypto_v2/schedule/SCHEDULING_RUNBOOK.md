# KCV2 accrual scheduling — runbook

Two standing data accruals kept alive by **Windows Task Scheduler on this
research machine** (the D5 ruling, 2026-08-02, keeps them running as standing
processes; the 15m-direction chapter is closed, the ladder-distribution question
stays open pending thicker data).

| task | cadence | loader | table | why |
|---|---|---|---|---|
| `\TradingCorp\kcv2-ladder-snap` | daily 08:00 local | `loaders/kalshi_ladder_snap.py --every-hours 24` | `lab_kalshi_ladder_snap` | S5 Breeden-Litzenberger density source; accrue events until coverage ~doubles |
| `\TradingCorp\kcv2-fine-flow` | every 12h (07:00 / 19:00 local) | `loaders/coinalyze.py --fine-only` | `lab_coinalyze` (1/5/15min) | Rider A — bank fine flow before Coinalyze free-tier retention (1min ~26h) drops it |

Both loaders are **resumable / idempotent**: the ladder skips already-snapped
events; fine-flow is `INSERT OR REPLACE` with no `DELETE`. Re-running after any
miss or failure is always safe and never shrinks the archive.

## Where things live (all under this worktree — do not move without re-pointing the tasks)

- Loaders + lab DB: `C:\Users\AA Incorporado\cc-2026-08-02-wt\research\kalshi_crypto_v2\`
  - lab DB (gitignored, ~228 MB, WAL): `lab\kcv2_lab.db`
- Scheduling subsystem: `research\kalshi_crypto_v2\schedule\`
  - `run_accrual.ps1` — job wrapper (runs loader under the 25 GB cap, writes heartbeat)
  - `health.ps1` — one-glance health of both jobs + tasks
  - `register_tasks.ps1` — (re)register / remove the two tasks
  - `logs\<job>.heartbeat.log` — append-only heartbeat (the dead-timer signal; gitignored)
  - `logs\<job>.last.log` — full stdout of the most recent run (gitignored)

> ⚠️ The accrual home is a session worktree (`cc-2026-08-02-wt`, branch
> `claude-2026-08-02`). The worktree-cleanup pass MUST exclude it — pruning it
> deletes the lab DB and breaks both tasks. The lab DB path is anchored to
> `lab/labdb.py`, so the jobs write there regardless of working directory.

## Prerequisites (why a run can fail)

1. **Azure CLI login (the #1 failure mode).** The loaders fetch creds from Key
   Vault via `DefaultAzureCredential`. This machine has no managed identity, so
   the chain falls back to the **Azure CLI token cache** (`~/.azure`) of the
   logged-on user `MSI\AA Incorporado`. If that login expires you get
   `status=ERROR` heartbeats. Fix: `az login` (in this user's session), then
   re-run manually (below). This is why the tasks are **LogonType Interactive**
   (they run only when this user is logged on, inside their profile) — a
   "run whether logged on or not" task would not see the token cache.
2. **Concrete Python:** `C:\Users\AA Incorporado\AppData\Local\Python\pythoncore-3.14-64\python.exe`
   (NOT the WindowsApps store alias). Override via `run_accrual.ps1 -Python <path>`.
3. **procgov** (memory cap via `scripts\run_capped.ps1`) — installed via winget.
4. Network reach to `api.elections.kalshi.com` (KAREN key) and `api.coinalyze.net`.

Secrets are fetched in-memory and never printed; the wrapper only relays the
loaders' already-redacted stdout.

## Check health

```
powershell -NoProfile -ExecutionPolicy Bypass -File research\kalshi_crypto_v2\schedule\health.ps1
```

Reports per job: verdict (`OK` / `STALE` / `ERROR` / `NONE`), heartbeat age, the
last heartbeat line, and the task's `state / lastResult / lastRun / nextRun`.
- `STALE` = newest heartbeat older than 1.5× cadence (ladder > 36h, fineflow >
  18h) → the timer may be dead. `lastResult 0x0` = last run succeeded.
- A `rows_new=0` ladder run is **normal** when the day's bucket is already
  captured — it means "current", not "broken".

## Re-run a job manually (safe anytime)

```
# either the exact scheduled path:
Start-ScheduledTask -TaskName 'kcv2-fine-flow' -TaskPath '\TradingCorp\'
# or the wrapper directly (same code path):
powershell -NoProfile -ExecutionPolicy Bypass -File research\kalshi_crypto_v2\schedule\run_accrual.ps1 -Job fineflow
```

`-Job ladder` for the ladder. A manual fine-flow run is the recovery action if a
scheduled run was missed — do it within ~26h of the last success or the 1min
tail is gone (5min/15min tolerate longer).

## Disable / re-enable / remove

```
Disable-ScheduledTask -TaskName 'kcv2-fine-flow' -TaskPath '\TradingCorp\'   # pause
Enable-ScheduledTask  -TaskName 'kcv2-fine-flow' -TaskPath '\TradingCorp\'   # resume
powershell -NoProfile -ExecutionPolicy Bypass -File research\kalshi_crypto_v2\schedule\register_tasks.ps1 -Remove   # delete both
powershell -NoProfile -ExecutionPolicy Bypass -File research\kalshi_crypto_v2\schedule\register_tasks.ps1           # (re)register both, idempotent
```

## Verified first runs (2026-08-02)

- ladder: `rows_new=843` (events 70→71/series, snaps 34098→34941), then a
  scheduler-triggered re-run `rows_new=0` (idempotent), both `exit=0` / `0x0`.
- fineflow: `rows_new=92223` (fine tail 08-02 00:49Z → 18:43Z), scheduler re-run
  `rows_new=548`, both `exit=0` / `0x0`.

## Migration note

If these ever move to `tc-prod-vm`, that is a **separate operator-gated deploy**
(new cron/systemd-timer units, a lab DB on the prod host or a sync, and the
prod-side cred path — managed identity, not the local az cache). Not covered
here; do not fold it into a routine change.
