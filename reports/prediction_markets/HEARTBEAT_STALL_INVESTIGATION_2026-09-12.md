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
directly and opportunistically catch a live freeze + confirm the grain. STATUS: DONE (Part C).

═══════════════════════════════════════════════════════════════════════════════════════════════
## PART C — MEASUREMENT RESULTS (read-only box runs, board-authorized; engine 351422 untouched)
═══════════════════════════════════════════════════════════════════════════════════════════════
Two read-only runners: `pm_hb_measure_ro` (snapshot + 150 s sampler) at 19:15 Z; `pm_hb_gap_ro`
(6 h journal retrospective) at 19:38 Z. Box code hashes MATCH the analysed files
(live_driver `6561b569`, heartbeat `57afdcc6`); schema 22; both accounts `multi_category_ok=1`;
arm global + 30 subs armed (intact). WIRED log: kalshi_jack 17 categories, kalshi_karen 15,
cycle order `mlb, atp, cfb, ...` (mlb 1st, cfb 3rd).

### C1. The stall is REAL and RECURRING — ~170-240 s, ~6x in 6 h (NOT a one-off, NOT every refresh)
6 h journal, per-account cycle period from a once-per-cycle wallet's `/positions` reads:
- kalshi_jack: mean 6.8 s, **max_gap 237.3 s**, exactly **6 gaps >=60 s, all 214-237 s**, then a
  cliff to ~44 s.
- kalshi_karen: mean 24.8 s, **max_gap 239.6 s**, **6 gaps of 216-239 s**, then ~47 s.
- Combined `/positions` INFO stream (108,849 lines) top gaps: **212.8 / 192.2 / 191.5 / 189.3 /
  187.7 / 172.9 s**, then a cliff to ~19 s. **189.3 s is literally in the data == the UI agent's
  event.** Both accounts blank TOGETHER (they booted aligned 01:33:35 Z) -> each event is a
  DUAL-account blackout.
- Frequency ~6/6 h (~1 per hour), INTERMITTENT (not the ~24 you'd see if every 900 s refresh
  stalled). The spacing tracks rate-limit windows, not a code cadence.

### C2. The 150 s live sampler MISSED the stall (expected) but pinned the baseline
Normal full-account cycle ~24-28 s (all 17/15 categories); max caught 44 s; mlb/cfb did NOT
refresh in the window (reach_age==eval_age throughout). A ~200 s event occupies ~5 % of the
timeline, so a 2.5 min sample missing it is expected -- which is why the RETROSPECTIVE journal
(C1) is the authoritative source, not a live catch.

### C3. Mechanism = KALSHI RATE-LIMIT BACKOFF on the shared engine IP (not the things it could be)
Ruled OUT, each on evidence:
- NOT a disconnect: `Server disconnect` = **0** in 6 h.
- NOT a refresh FAILURE: `index refresh failed` = **0** -> the fetch SUCCEEDS, just slowly.
- NOT the Polymarket 429s: `HTTP 429` = 385 in 6 h, but those are `/positions` (Polymarket) and
  429 there RAISES IMMEDIATELY (polymarket_data_api_client.py:690, no backoff) -> per-whale
  skip, adds no latency.
- NOT CPU index-building: `build_kalshi_{game,total,spread}_index` are O(n) single passes
  (mlb_poly_kalshi_match.py:303/454/466) -> sub-second over 20 k tickers.
- Intrinsic Kalshi fetch is ~5-7 s: measured LOCALLY (public /markets endpoint, clean IP, 3 runs
  5.7/5.4/7.4 s) for the full MLB refresh = **~20,000 markets / 24 pages** (KXMLBTOTAL settled
  10,526/11pp + KXMLBSPREAD settled 7,406/8pp dominate). ~200 s is ~30x that -> environmental.
=> The account tasks block on slow Kalshi HTTP during rate-limit-throttle windows on the shared
engine IP; the 24-page index refresh (fired every 900 s per category x2 accounts, aligned) is the
dominant Kalshi-call burst that triggers/sustains the throttle. Both accounts stall together
because the throttle is per-IP and their refresh phases are boot-aligned.

### C4. Correction banked (suspect-my-own-hypothesis, caught by reading the diff not the blame)
The 2026-09-11 ctx-pagination fix (`6a753b71`) did NOT inflate the settled fetch. Pre-fix was
`fetch_all=(status==MarketStatus.SETTLED)` -> **settled was ALWAYS fully paginated; only OPEN was
single-page**. The fix flipped OPEN to `fetch_all=True` (a no-op for settled). So the
~20 k-market/19-page settled fetch is BASELINE, not a regression. (An earlier lean that "the fix
made the settled stall ~20x worse", inferred from the blame line-number, was wrong -> refuted by
`git show 6a753b71`.) Separately: the fix DID enlarge the cfb OPEN fetch (2,508/2,541 open now
paginated), which adds to the shared-IP Kalshi load but is not the MLB stall.

═══════════════════════════════════════════════════════════════════════════════════════════════
## PART D — BLAST RADIUS, THRESHOLD VERDICT, TWO COSTED FIXES, RECOMMENDATION
═══════════════════════════════════════════════════════════════════════════════════════════════

### D1. Blast radius -> this is a TRADING GAP (answer to brief #4)
During each ~200 s event BOTH accounts do NO entries, NO whale-exits (`/positions`+`/activity`),
NO settlement-close, NO opposing-pair guard -- every trading path sits after the refresh `await`
in the blocked task (A7). Duty cycle: 6 x ~200 s / 21,600 s ~= **~5.5 % of the time both accounts
are fully blocked**, in ~200 s contiguous chunks, ~24x/day. For a "copy within seconds" system
that is a real latency-SLA gap (a whale entry/exit landing in the window is copied up to ~200 s
late) -- not a correctness break (the exit paths are eventually-consistent), but a measurable hole.
**Plainly: a TRADING GAP whose heartbeat stall is only the symptom, NOT a monitoring artefact.**

### D2. Threshold verdict -> the alarm did NOT fire; the blind window is the real monitor issue
Max stall 237 s < BOOT_GRACE 600 s -> subs read BOOTING, and `_LV_ALARM_STATES=("STALE","NEVER")`
excludes BOOTING -> **no page-top alarm fired in 6 h.** The "STALE threshold (300 s) must sit above
189 s or the strip fires on schedule" framing is a MIS-READ: the governing gate is the 600 s boot
grace, and it already covers the observed stalls. BUT the boot grace's cost is a real, RECURRING
BLIND WINDOW: for ~200 s, ~6x/6 h (~5 % of the time), a genuinely dead driver is INDISTINGUISHABLE
from a throttled-but-alive one (both BOOTING). Headroom is only ~2.5x (237 vs 600): more categories,
larger sizes, a worse throttle window, or a convergent multi-category refresh could push a stall
past 600 s -> then it false-alarms AND still hides the blind window.

### D3. The two fixes, costed
(a) **Raise the STALE / boot threshold above the stall.** Cost ~trivial (one constant). BUT the
    threshold is NOT currently breached (no alarm to suppress) -> it fixes a non-problem today,
    WIDENS the already-recurring blind window, and does nothing for D1. Per #4 trading IS blocked,
    so (a) would silence the symptom of a real gap. **NOT a fix.**
(b) **Make the refresh not block the ACCOUNT LOOP.** Engine work + one restart. Two levers:
    - **b1 (cheapest, highest-leverage, targeted): shrink `_SETTLED_LOOKBACK_SEC` (160 days).**
      It pulls ~20 k MLB markets / ~19 settled pages per refresh -- the dominant Kalshi-call burst
      and rate-limit surface -- and has NO rationale comment (unlike its neighbours). A few days
      would cut it ~20-30x (-> ~1-2 pages) so a refresh drops from ~200 s-under-throttle to seconds
      AND lowers the shared-IP throttle for everything. ★ CONTINGENT on the engine owner confirming
      what CONSUMES settled ctx entries: entries only match OPEN markets, so settled is there for
      describe/booking of already-held positions at most -- if nothing needs 160 days, shrink it.
      Shared constant -> benefits all categories.
    - **b2 (structural, purpose-agnostic): background the catalog fetch** (separate task/executor
      updates `ctx_by_cat`; the trading loop reads the latest cached ctx without awaiting). Fixes
      the block regardless of fetch size / mechanism. More invasive; adds a concurrency seam -- but
      NOT on the placement path, so the account-cap race M1 guards against is untouched. Pair with
      STAGGERING the two accounts' refresh phase so they never blank together (halves per-event
      blast radius).
    - ★ **TRAP: decoupling ONLY the heartbeat** (a background task_alive writer) pins the monitor
      GREEN during the 200 s trading blackout -- the worst outcome. (b) must unblock the LOOP, not
      just the beat.

### D4. Recommendation (Jack rules the fix)
1. **Reject (a) as a fix.** At most, a stall approaching 600 s is a signal to ship b1/b2 -- not to
   raise the grace.
2. **Ship b1 first: shrink the 160-day settled lookback**, once the engine owner confirms the
   settled-ctx consumer. Cheapest, one constant, attacks the fetch volume that drives the throttle,
   benefits every category. Re-measure the 6 h gap distribution after (expect the >=214 s tier to
   vanish).
3. **If b1 cannot fully eliminate it (or settled is needed), add b2** (background the fetch) as the
   structural guarantee, plus refresh-phase staggering across accounts.

Residual uncertainties (honest): the exact blocking Kalshi call is inferred (the dominant 24-page
refresh), not proven per-call -- but the fix is robust to which Kalshi call is the straw (both b1
and b2 help regardless). 6 h is one sample (US afternoon, real load); the ~1/hour spacing is
throttle-driven and may vary. No engine CPU was sampled during a stall (CPU ruled out by code, not
measurement). The sampler did not catch a stall live (the journal did, retrospectively -- stronger).
