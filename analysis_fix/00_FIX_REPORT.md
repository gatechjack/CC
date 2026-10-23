# MACE DURABILITY FIX — manage-loop timeout + liveness watchdog (2026-10-23)

Branch `mace-durability-fix-2026-10-23` (worktree `cc-mace-durability-fix-2026-10-23-wt`) off box-truth **2ffa1d20**.
Root-cause fix for the 2026-10-13→10-23 nine-day silent manage-loop hang. **BUILD + REVIEW ONLY — Jack deploys.**

## Root cause recap
`manage_tick` → `_fresh_quotes` → `port.leg_quote` → robin_stocks chain pull via `asyncio.to_thread` with
**zero timeout**. On the 10-13 RH session meltdown that await never returned → the manage loop (which owns ALL
exits + live-mark writes) froze for 9 days while sibling loops kept running. No alarm fired.

## FIX #2 — manage-tick + leg-quote timeouts (the root fix)
- **execution.py `_fresh_quotes`**: each `leg_quote` now runs under `asyncio.wait_for(timeout=leg_quote_timeout_sec)`.
  On timeout the leg reads None and, since a timeout = session trouble (affects all legs; a no-bid wing still
  returns promptly), the remaining legs **short-circuit** so a dead session costs ~1 timeout/rung, not 4. The
  None-tolerant callers already degrade cleanly (mark None → stop/PT skip, time/exdiv still fire). Audit `mace_leg_quote_timeout`.
- **manager.py `_spot`**: same `wait_for` wrap (spot also hits the broker).
- **loops.py `mace_manage_loop`**: wraps `await mgr.manage_tick(now)` in `asyncio.wait_for(timeout=manage_tick_timeout_sec)`.
  On `TimeoutError`: cancel the tick, audit `mace_manage_timeout`, Telegram breaker, **next tick runs normally**.
- **config.py**: `management.manage_tick_timeout_sec` (default **600**), `management.leg_quote_timeout_sec` (default **30**),
  both **optional-with-defaults** (mirrors MarkGuardConfig/CloseabilityConfig) → **no mace.yaml change → config_hash
  931a8214be50 PRESERVED** (verified by a live load of the real yaml, below).

### Timeout values — data-driven basis
Measured the real sweep on the live box (`analysis_fix/measure_tick.ps1`, post-reconcile stable state): the manage
sweep runs **~21s/rung** (`leg_quote` pulls the full option chain per leg via `to_thread`; recent inter-write gaps
12-55s; a 5-rung cluster spanned 108s). So a normal 12-rung sweep ≈ **200-250s**, and the post-restart 19-rung sweep
was ~600s.
- **`leg_quote_timeout_sec = 30`**: a healthy heavy chain-pull is ~5-15s; 30s is 2-6× headroom so it NEVER trips on
  a slow-but-live pull, but converts an infinite hang into a 30s failure (the 10-13 await was ∞).
- **`manage_tick_timeout_sec = 600`**: a normal full sweep is ~200-250s and historically up to ~600s; 600s is a
  generous backstop that will not interrupt a legitimate slow/busy sweep, while capping any non-quote unbounded
  await. The 30s per-leg timeout already catches the quote-hang CLASS much sooner; the 600s outer is the
  belt-and-suspenders for other awaits in the tick (e.g. a hung order-status poll in a close ladder). A residual
  hang now self-heals in ≤10 min vs the 9-day actual. (At much larger rung counts raise the outer value; the 30s
  per-leg bound is the primary per-rung control.)

### CLOSING-idempotency proof (interrupted mid-exit is safe)
`wait_for` cancels `manage_tick` at its current await. The per-rung `try/except Exception` does NOT catch
`CancelledError` (BaseException in 3.8+), so it propagates cleanly → the in-flight rung is abandoned, next tick
re-loads from scratch. An interrupt is **equivalent to a process crash at that await**, which the 2026-10-09
exit-redesign already makes recoverable:
- a close persists its order id (`set_exit_order`) **before** the cancel await (execution.py:1116) → next tick's
  `close_rung` preamble (1002-1022) polls it: filled→book, unconfirmed→abort+retry, dead→clear+re-ladder;
- a committed (stop) close marks CLOSING before laddering (1040-1041) → next tick `_drive_closing` handles it;
- an orphaned order (interrupt during `_place`) is caught by the `_working_exit_order` combo-id `{rid}-x*` sweep;
- `reopen_from_closing` is SQL-guarded (only a CLOSING rung with NO booked exit; `rowcount==1`) → idempotent.
Proven by test `test_reopen_from_closing_is_idempotent_and_guarded` (reopens once, second call no-op; a booked
close is never reopened).

## FIX #3 — liveness watchdog (silent-death detection)
- **loops.py `mace_watchdog_loop`** — a NEW **sibling, broker-free** scheduler (it cannot live inside the loop it
  watches; makes NO broker calls — only a DB heartbeat read + the clock — so it can't hang the way manage did).
  During market hours (09:35-15:55 ET weekday) it alerts ONCE PER EPISODE (existing Pulse audit + Telegram breaker):
  - **STALL** (`mace_manage_stall`): no completed manage tick within `stall_alert_sec` (the silent hang; also fires
    if there's no heartbeat at all in-window), with a market-open **warmup grace** (no judgment until a full stall
    window after 09:35, since the overnight gap + first-tick latency aren't a stall);
  - **SESSION DEAD** (`mace_marks_dead`): ticks ARE completing but 0 of N open rungs priced within
    `marks_dead_alert_sec` — this catches the 10-13 ROOT (dead RH session → all-None marks) that the timeout alone
    turns from a hang into a loud-but-degraded state.
  Off-hours silence is expected → no alert + latch reset.
- **manager.py**: `manage_tick` writes a heartbeat (`agent_state robinhood_mace/manage_tick_heartbeat`:
  `{last_tick_ts, last_marked_ok_ts, open_count, marked_ok}`) **only after a COMPLETED sweep**, so a hung/cancelled
  tick leaves it stale → the watchdog fires. `_tick_marked_ok` counts rungs priced this tick (session-dead signal).
- **execution.py RungStore**: `set_manage_heartbeat` / `get_manage_heartbeat` (agent_state upsert, same SQL the
  codebase uses).
- **config.py**: `management.watchdog` = `WatchdogConfig(enabled=True, poll_interval_sec=60, stall_alert_sec=900,
  marks_dead_alert_sec=900)` — optional-with-defaults. **N = 900s (15 min) ≈ 3× the 300s tick interval and > a
  normal ~250-600s sweep**, so it only fires on a true stall, never on a slow-but-live sweep.

### Watchdog placement (design choice)
A dedicated broker-free task, **not** folded into reconcile/daily-slots — those make broker calls and share the
same hang risk, so a watchdog inside them could die with them. It is wired as a 5th MACE task in main.py (the only
shared-file touch — see below).

## SHARED-FILE FLAG
- **`trading_corp/main.py`** (shared across divisions): 3 minimal, additive MACE-scoped lines — init
  `mace_watchdog_task=None`, `create_task(mace_loops.mace_watchdog_loop(...))` alongside the other 4 MACE tasks,
  and add it to the existing MACE shutdown-cancel tuple. No other division touched. All other changes are MACE-only
  files (config.py, execution.py, manager.py, loops.py — all under `trading_corp/mace/`).

## Tests + results (box-scratch, `analysis_fix/run_tests.ps1`)
Overlay the 6 changed files onto a `/tmp` copy of the live package (scp+tar, binary-exact; prod tree/engine/DB
untouched), py_compile, live config-load, pytest with `-p no:pytest_ethereum`.
- **py_compile: OK** (config, execution, manager, loops, main, test).
- **config load: config_hash 931a8214be50 (UNCHANGED)**; new fields default mtt=600 / lqt=30 / watchdog defaults.
- **pytest: 16 passed** — 9 new (`test_mace_durability.py`: manage-tick timeout self-heals; leg-quote timeout +
  short-circuit; watchdog stall / off-hours-silent / no-heartbeat / warmup-grace / dead-session; heartbeat
  round-trip; reopen idempotency) + 7 regression (`test_pt_alert_dedupe.py`) — **0 new failures.**
  (The repo-wide ~24 stale UI-assert failures are non-MACE / out of scope; the MACE-targeted suite is green.)

## DEPLOY (RESERVED — Jack; box-only graft + restart; config_hash UNCHANGED)
Code-only graft of 5 files (config/execution/manager/loops under mace/ + main.py), then restart. Recommended path:
scp+tar binary-exact staging with a box==base drift-gate + per-file CR-sha verify + py_compile, then
`restart_tc.ps1`. (A ready runbook is in `analysis_fix/DEPLOY_RUNBOOK.md`.) No mace.yaml change → config_hash stays
931a8214be50. Optional later tuning: add a `management.watchdog` / timeout block to mace.yaml — that MOVES
config_hash (flag) and also needs a restart.
