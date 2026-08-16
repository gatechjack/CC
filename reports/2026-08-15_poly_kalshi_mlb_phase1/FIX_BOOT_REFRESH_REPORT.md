# Boot index-refresh fast-retry — fix, deploy, live proof (2026-08-16)

## Problem
On every engine restart the first `KXMLBGAME` index refresh hung on a cold
connection for ~2 min, then dropped ("Server disconnected without sending a
response"). The loop then waited a full `index_refresh_sec` (900 s) steady cycle
before trying again — a **~15-min window** where the match index was empty and no
whale bet could match/fire. Observed twice on 2026-08-16 (15:06, 15:48 UTC boots).

## Evidence gathered before designing
Cold-connection timing probe (`fix_00_timing_probe.py`, run locally):

```
connect: 0.71s (cold)
OPEN fetch:      0.09s  tickers=82   -> 41 games, 3 dates  [MATCHABLE at t+0.8s]
SETTLED fetch:   0.30s  tickers=1746
FULL index games=913                 [complete at t+1.1s]
```

The fetches are **not** slow — full index builds in 1.1 s. So the prod failure is
a **cold-connection TCP/DNS stall**, not a heavy fetch. A bare retry-after-failure
can't hit ~30 s because each attempt hangs ~2 min first; the fix must **bound each
attempt** so a hang fails fast, then retry.

## Fix (main.py only; steady-state cadence unchanged)
- `_pk_guarded_refresh(do_fetch, *, timeout, log)` — one attempt, `asyncio.wait_for`
  bounded, never raises, returns bool.
- `_pk_boot_refresh_retry(refresh_fn, *, tries=3, timeout=12, backoff=10, ...)` —
  BOOT-ONLY fast retry, stops on first success, falls through to the steady cycle
  if all fail.
- Steady-state call is byte-identical: `await _pk_refresh_index()` → `timeout=None`
  → unbounded, unchanged.
- Shared files (`kalshi_copy_trader.py` / `sports_team_mapping.py` /
  `kalshi_live.py`) byte-unchanged (git diff empty vs prod-live).

7 proof tests (`test_poly_kalshi_boot_refresh.py`): recovery-on-retry,
timeout-bounds-a-hang, all-fail fallthrough, timeout-forwarding, steady-state
unbounded path. Full poly_kalshi suite **62/62 green**.

## Deploy (Board-authorized atomic, 2026-08-16 16:36 UTC)
- Drift-gate: box main.py `fcf99e32` == prod-live 01593b4 (no drift).
- Installed `fcf99e32 -> 044cc21` (md5-verified both ends), backup
  `main.py.bak_pkbootfix_20260816_163608`, restart PID 748917 -> 753629.
- prod-live `01593b4 -> 5fba5ee` (main.py only; prod-live tip == box).
- Re-armed clean: `auto_execute=True`, `dry_run=False`, `halted=False`, stake $5,
  $100 loss-halt. **0 orders** during deploy.

## LIVE proof (journal, this restart)
```
16:36:29  loop online (poll=7.0s, dry_run=False)
16:38:42  index refresh failed:                       <- cold failure reproduced (empty msg = TimeoutError)
16:38:42  boot index refresh try 1/3 failed; retrying in 10s
16:38:52  KXMLBGAME index refreshed (913 games)       <- RETRY recovered
```
Index up at 16:38:52 vs the old behaviour (next steady cycle ≈ 16:53:42) — **~15 min
faster**. The 15-min blind spot is eliminated.

## Honest caveat (load-bearing)
The per-attempt **12 s `wait_for` timeout did NOT fire fast**. The first attempt
still hung ~2 min 13 s (16:36:29 → 16:38:42), and the exception was an
empty-message `TimeoutError` that only surfaced once the call returned. That means
the hung call **freezes the asyncio event loop** (almost certainly a synchronous
DNS/connect at boot), so `wait_for`'s timer can't run until the loop unfreezes.
Consequence: the **RETRY** delivers the fix (recovers ~15 min sooner), not the
timeout. Residual first-attempt blind window ≈ **2 min**, not the ~30 s target.

To actually fail-fast at ~12 s would require a **client-level socket/read timeout**
on the shared Kalshi client (OS-enforced, fires even with a frozen loop) — more
invasive (shared surface, pykalshi 2.0.0 API), left as a follow-up for explicit
approval. Minor regression note: if the cold hang recurred across retries, each
hung attempt would re-freeze the loop ~2 min (up to 3×); empirically it cleared
after attempt 1 (attempt 2 instant), so this deploy saw one freeze — same as before.

## Rollback
`powershell -ep bypass -f .\pk_rollback.ps1` (restores newest
`main.py.bak_pkbootfix_*` + restart).
