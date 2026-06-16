# A / Phase 1 — pinpointing the event-loop freeze (diagnosis, pre-fix checkpoint)

§4 read-only diagnosis (82fda13). Branch `bitunix-deblock-eventloop-2026-06-16`
off `b3d1f08`. **No fix written — this is the Phase-1 checkpoint.**

## Headline: the medium-confidence suspect list is REFUTED. Do NOT fix it.

The investigation's suspects (BitUnix funding/bar-cache/snapshot, polymarket
data-api) are **already async + timeout-protected** — they cannot cause a 6-13 min
freeze, and they are **victims**, not the cause:

| suspect | code | verdict |
|---|---|---|
| BitUnix bar-cache refresh | `data/live_bar_cache.py:99` `async with httpx.AsyncClient(timeout=10.0)` | async+timeout — exonerated |
| BitUnix snapshot / funding / `_request` | `brokers/bitunix.py:429,822` `httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)` | async+timeout — exonerated |
| Polymarket data-api | `data/polymarket_data_api_client.py:387` `httpx.AsyncClient(timeout=…)`, catches `TimeoutException` | async+timeout — exonerated |
| Coinbase fetch_balance | `brokers/coinbase.py:135,215` `ccxt.async_support` + `await fetch_balance()` | async (ccxt timeout) — exonerated |

Fixing these (the obvious move) would have left the freeze intact.

## What the freeze actually IS: a SYNCHRONOUS block on the LOOP THREAD

8 freezes today (05:10–14:00), 384–832 s each, ~74 min apart. Proof the **loop
thread itself is blocked** (not merely idle / executor-starved):
- `paper_trade_replay` ticks every 60 s and logs at tick-start; during a 13-min
  freeze **~13 ticks never logged** until thaw — i.e. the loop couldn't run a
  ready timer callback for 13 min → the loop thread was blocked in sync code.
- If the loop thread were free (just executor-starved), the async 10 s timeouts
  would fire and `log.warning` on schedule. They don't — they all flush in a
  **cluster at thaw** (multi-service: bitunix + coinbase + kalshi + LiveBarCache).
  That cluster = victims flushing when the loop unblocks.

Freeze boundaries (BEFORE = last log pre-silence; AFTER = first on thaw):
```
06:09:58→06:23:12 (793s) BEFORE kalshi_llm_scan   AFTER BitUnix funding_rate fetch failed
07:24:02→07:30:27 (384s) BEFORE lord_otter wh     AFTER Coinbase fetch_balance failed
07:30:27→07:37:21 (414s) BEFORE lord_otter ignore AFTER paper_trade_replay tick
08:37:59→08:51:26 (807s) BEFORE polymarket-data-api activity   AFTER market_cypher wh
09:51:54→10:05:32 (818s) BEFORE polymarket_scan_cycle          AFTER paper_trade_replay
11:06:10→11:19:44 (813s) BEFORE kalshi_llm_scan   AFTER market_cypher wh
12:20:25→12:34:18 (832s) BEFORE polymarket_scan_cycle          AFTER paper_trade_replay
13:34:45→13:48:18 (813s) BEFORE polymarket-data-api activity   AFTER IC scanner
```

## Control group that narrows it

13:48→14:39 had **dozens of `pykalshi._async.client … ConnectTimeout`** (same
network flakiness) but **NO freeze** — because pykalshi is **async + fast
timeout + retry** (fails in ~0.5 s, loop stays responsive). So:
- The network outage is the TRIGGER, not the cause.
- A freeze requires a call that **hangs with NO fast timeout**. The async-timeout
  services fail fast (no freeze); the blocker has no such guard.

## Why the culprit is INVISIBLE in the journal

The only **synchronous** network client in the whole codebase is
**`py_clob_client`** (Polymarket CLOB SDK, `requests`-based, **no timeout** —
`brokers/polymarket.py:504 ClobClient(host=…)`). But it is **already offloaded**
via `asyncio.to_thread` (`polymarket.py:484`, and all of `polymarket_live.py`),
so by itself it runs OFF the loop thread. Its failures log at **`log.debug`**
(`polymarket.py:512`) → invisible at the journal's INFO level. So whatever the
blocker is, it does not announce itself; we only see its victims.

Every other sync SDK (robinhood, tastytrade, fidelity, web DB) is also
`to_thread`-wrapped. **`main.py` sets NO custom executor** → all `to_thread`
work shares the **default loop ThreadPoolExecutor (~36 threads)**, which is also
where async DNS (`getaddrinfo`) for httpx/ccxt runs.

## The remaining ambiguity (why I'm stopping here, not fixing)

The loop thread is blocked for minutes, yet every network call is async or
`to_thread`-offloaded. Two hypotheses survive, and they imply DIFFERENT fixes —
so I will not build blind:

- **H1 — thread-pool / GIL starvation:** during a blackhole, many no-timeout
  `to_thread` network calls (py_clob_client and/or other SDK polls) hang and
  spin; 36 threads contending for the GIL + the default executor saturating
  (which also blocks async DNS) starves the loop thread → total freeze. Fix =
  per-call timeouts on every `to_thread` network call + bound/isolate the
  executor.
- **H2 — sync DB op on the loop thread blocked on a lock:** the observer/agents
  run `db.connect()/execute()` **synchronously on the loop thread** (not
  `to_thread`); a write lock held across a hung network `await` elsewhere would
  block them for the hang's duration. Fix = never hold a txn across an await /
  offload DB or cap busy_timeout. (The chronic "db-lock journal-only" note is a
  related smell.)

Static read-only analysis **cannot disambiguate** these — both fit the evidence.

## To CONFIRM the exact culprit (operator-gated — agent is read-only on prod)

1. **`py-spy dump --pid 2797287` DURING a freeze** (definitive — shows the Python
   stack of every thread; names the blocking call). Needs `pip install py-spy` +
   ptrace (sudo). Catch a freeze via a silence-watcher (no new journal line >60 s).
   Cheaper read-only proxy without install: `sudo cat /proc/2797287/task/*/stack`
   during a freeze (kernel stacks → recvfrom/connect = network wait vs futex =
   GIL/lock wait).
2. **OR** restart with asyncio debug + slow-callback logging
   (`PYTHONASYNCIODEBUG=1` / `loop.slow_callback_duration`) → the loop will log
   "Executing <…> took N s" naming the blocking callback. Needs a restart
   (operator) — bundles awkwardly since it changes runtime.

## Proposed de-block direction (Phase 2 — pending target confirmation)

Defense-in-depth that holds regardless of which hypothesis wins, scoped to the
data/poll layer (NOT the order/B1-stop path; polymarket fixes in the shared
broker/data layer, not the division):
- Wrap every **sync-network `to_thread`** call in `asyncio.wait_for(…, timeout=T)`
  with fail-safe degrade (skip-cycle / last-known / log+continue) — starting with
  `py_clob_client` (no timeout today).
- Run the engine's blocking offload on a **dedicated bounded executor** separate
  from the default (so async DNS can't be starved), or semaphore-bound it.
- Audit sync DB ops on the loop thread for txns held across awaits (H2).
- Tighten the async httpx/ccxt poll timeouts (10 s → 3–5 s) + fail-safe degrade.

**CHECKPOINT: pick the confirmation method (py-spy during a freeze vs
asyncio-debug restart) so Phase 2 targets the proven culprit, not a guess.**
