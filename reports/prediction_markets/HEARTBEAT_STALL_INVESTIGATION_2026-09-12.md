# PM HEARTBEAT-STALL INVESTIGATION — 2026-09-12 (READ-ONLY)

Branch `pm-heartbeat-stall-invest-2026-09-12` off `origin/prod-live` @ `489a9ddb` (git truth; box byte-identical).
Build nothing, change nothing, deploy nothing. Jack rules the fix.

Local CR-stripped sha256(first16) of the files analysed — matches the box hashes in the transition doc, so this
analysis is of the RUNNING code:
- `trading_corp/prediction_markets/live_driver.py` = `6561b569316f6cdc`  (transition-doc box hash: `6561b569`)
- `trading_corp/prediction_markets/heartbeat.py`   = `57afdcc6c61055e2`

═══════════════════════════════════════════════════════════════════════════════════════════════
## PART A — CODE-ESTABLISHED FACTS (certain from source; no box needed)
═══════════════════════════════════════════════════════════════════════════════════════════════

### A1. Topology — ONE async task per ACCOUNT iterating ALL its categories SEQUENTIALLY
- `main.py:1626-1634` spawns ONE `scheduled_pm_live_loop` task per account, `categories=_acats` (the whole list).
- `driver_roster.plan_driver_tasks` (driver_roster.py:97) GROUPS every category onto that one task when
  `pm_account.multi_category_ok=1` (M4 opt-in, migration 019). The box runs 17 jack / 15 karen categories
  cycling (transition-doc §7), so BOTH accounts are opted-in -> one task, ~15-17 categories, sequential.
- Categories iterate SEQUENTIALLY inside the single task (`live_driver.py:1060 for c in cats`). No `gather`,
  no thread, no per-category task. This is DELIBERATE (M1/Option C): one task per account so the account-keyed
  open-exposure cap (gate 6) is enforced jointly without a within-cycle race.

### A2. The refresh is a SYNCHRONOUS await inside that one task
- `live_driver.py:1070-1072`: `if ctx is None or (time - last_idx[c]) > index_refresh_sec: ctx = await _bld(...)`.
  `_bld` = the category's market-context builder (mlb = `fetch_market_context`). It is `await`ed inline in the
  category loop, inside the cycle `try`. While it runs, the single account task is parked on that await.

### A3. Which grain stalls -> `task_alive` (the ACCOUNT grain) -> the WHOLE account goes quiet
Loop skeleton (live_driver.py):
```
while ...:                                # 1008 ACCOUNT LOOP
    upsert_task_alive(account)            # 1018  <- TOP of loop, OUTSIDE cycle try
    try:
        fetch_shard_balances(); fetch_open_exposure()   # 1031/1048 once per cycle
        for c in cats:                    # 1060 SEQUENTIAL
            upsert_reached(account, c)    # 1064  reached beat (first thing)
            if refresh due: ctx = await _bld(...)   # 1072  <- THE STALL
            ... settlement-scan / positions+activity / opposing-guard / evaluate+PLACE ...
            upsert_evaluated(account, c)  # 1186  evaluated beat
    except: log
    await sleep(poll_sec ~7s)            # 1204
```
- `task_alive` is written ONCE per account-cycle at the top (1018). A mid-cycle refresh await blocks the loop from
  returning to the top -> `task_alive` FREEZES for the full refresh. This is the ACCOUNT grain: per heartbeat.py's
  own design note, task_alive stalling means "the ACCOUNT LOOP is blocked and every category on that account goes
  quiet." `reached_ts` for the refreshing category was just written (fresh); `reached/evaluated` for later
  categories and the next `task_alive` all freeze.

### A4. Cadence — 900 s per category, ALIGNED at boot
- `index_refresh_sec=900.0` (signature default; `main.py:1632` reads `index_refresh_sec` cfg, default 900). Poll
  `poll_sec=7.0`.
- BOOT builds EVERY category's catalog and sets `last_idx_by_cat[c]=time()` for all (live_driver.py:959-966), so
  all per-category 900 s timers start ALIGNED -> they come due together -> MULTIPLE categories can refresh in the
  SAME cycle. Worst-case single-cycle account stall = the SUM of the converging refreshes, not one category.
  (Whether they stay clustered or stagger in steady state is an empirical timing question -> Part B.)

### A5. Cost driver — 160-DAY SETTLED lookback, 3 series, full pagination
- `_SETTLED_LOOKBACK_SEC = 160*86400` (160 days). Each builder fetches OPEN + 160-days-SETTLED for every series,
  `fetch_all=True` paginated (`fetch_market_context` :199-209 for mlb's KXMLBGAME/TOTAL/SPREAD). The "899-game"
  KXMLBGAME index = ~90 open (measured, comment :196) + ~160 days of settled. cfb's total(2008)/spread(2541) OPEN
  alone exceed 1000 (comment :196), now paginated post-2026-09-11 -> cfb's fetch is LARGER than mlb's -> cfb likely
  stalls LONGER than mlb.

### A6. The MONITOR does NOT false-alarm at 189 s — the governing threshold is 600 s, not 300 s
- `heartbeat.py`: `FRESH_MAX_SEC=90`, `STALE_MAX_SEC=300`, `BOOT_GRACE_SEC=600`.
- During a stall, ALL of an account's beats freeze together, so `account_newest` ages uniformly and `boot_recent`
  (`now-account_newest < 600`) stays TRUE. `read_liveness` branch order checks `not task_fresh and boot_recent`
  BEFORE the STALE branch -> every sub reads **BOOTING** (informational), for ANY stall < 600 s.
- `live_view.py:892 _LV_ALARM_STATES=("STALE","NEVER")` and `app.py` calls `read_liveness`/`any_alarm` with the
  DEFAULT boot_grace (no override). BOOTING is NOT an alarm state -> the page-top alarm strip does NOT fire.
- So the naive "189 s > 300 s STALE -> alarm on schedule" is a MIS-READ of which threshold governs. The alarm
  fires only if a single account-wide (convergent) stall EXCEEDS 600 s. The liveness alarm is pm_web-DISPLAY-only
  (no telegram/push); a false strip is "cries wolf when a human looks at /live", which is exactly the trust erosion
  the boot grace was built to avoid.

### A7. Blast radius — a TRADING GAP, of which the heartbeat stall is only the symptom
Every trading path lives AFTER the refresh await, inside the same blocked task:
- entry copies: `run_live_arm_gated_cycle` (1181); whale-EXIT: `/positions` (1102) + `/activity` (1116) +
  `detect_exit_signals` (1118); periodic settlement-close (1085-1095); opposing-pair guard (1129-1178).
- During the stall none of these run for the refreshing category or any subsequent category, and the account's
  NEXT cycle is delayed by the full stall. So for the stall window the ACCOUNT does no entries, no exits, no
  settlement-close, no opposed-flatten. The monitor reading BOOTING (correctly not alarming) does NOT change that
  the account is not trading. => **primarily a TRADING GAP, not a monitoring artefact.**

### A8. Consequence for the two fixes (pre-measurement)
- (a) Raise the threshold (BOOT_GRACE / a convergent-stall grace): cheap, but WIDENS an already-existing blind
  window (< 600 s a genuinely dead driver == a refreshing one, both BOOTING) and, per A7, silences the symptom of
  a real trading gap. If A7 holds, (a) is NOT a fix.
- (b) Make the refresh not block the ACCOUNT LOOP (background/pre-fetch the catalog, and/or shrink the 160-day
  fetch): fixes BOTH the heartbeat and the trading gap. ★ Decoupling ONLY the heartbeat (a background task_alive
  writer) is the TRAP variant — it pins the monitor green while the account is still blocked from trading.

═══════════════════════════════════════════════════════════════════════════════════════════════
## PART B — WHAT REQUIRES MEASUREMENT (suspect-the-measurement-first)
═══════════════════════════════════════════════════════════════════════════════════════════════
1. Is 189 s STEADY-STATE or a TRANSIENT? ~15 paginated GETs at 189 s = ~12 s/GET, implausible for a healthy
   endpoint -> likely rate-limit/retry-stretched. heartbeat.py's own docstring cites a transient venue "Server
   disconnected" on an atp index-build stretching a boot to ~3.5 min. MEASURE the distribution, not one number.
2. Does the account-wide CONVERGENT stall reach/exceed 600 s (the real alarm gate)? Only timing tells.
3. Per-category refresh durations (is cfb > mlb? -> 189 s is the floor, not the ceiling).

Measurement design (read-only): (i) heartbeat snapshot + multi_category_ok + CR-hash of the running code;
(ii) systemd journal retrospective (boot time, index-refresh-failed / Server-disconnected / 429 lines, cycle-log
inter-arrival gaps per account); (iii) a bounded ~150 s task_alive sampler (mode=ro) to measure the cycle period
directly and opportunistically catch a live freeze + confirm the grain. STATUS: pending Jack's authorization.
