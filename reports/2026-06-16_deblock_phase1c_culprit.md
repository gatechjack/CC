# A / Phase 1c — naming the H3 CPU-bound frame (code trace, no sudo)

§4 read-only (82fda13). No sudo, no credential use, no fix. Path B (operator
declined password-in-chat; correct). Branch `bitunix-deblock-eventloop-2026-06-16`.
**Checkpoint — no Phase 2 until the operator confirms this target.**

## VERDICT (high confidence; inference from code + DB query plans, not a profiler)

**The H3 CPU-bound op freezing the loop is `RiskAgent` running UNINDEXED full-table
scans of the 1.19M-row `audit_event` table, synchronously on the event-loop thread,
inside `evaluate() → _evaluate_polymarket()`.**

Prime frames (`trading_corp/agents/risk.py`):
- **`_polymarket_open_positions` (risk.py:454–497)** — `SELECT a.payload_json FROM
  audit_event a LEFT JOIN polymarket_round_trips r ON r.order_id =
  json_extract(a.payload_json,'$.order_id') WHERE a.actor IN (…) AND a.kind=
  'would_have_placed' AND … AND json_extract(a.payload_json,'$.order_id') NOT IN (…)`.
- **`_sum_polymarket_today` (risk.py:431–452)** — `… WHERE actor IN (…) AND kind IN (…)
  AND substr(ts,1,10)=?`.
Both called together at **risk.py:358–364** in `_evaluate_polymarket()`, reached from
the synchronous `RiskAgent.evaluate()` — invoked on the loop in the async scanner
coroutines (`main.py:2669/2857/3011/3167`, NO `to_thread`).

Secondary (same anti-pattern, once per cycle): **`_count_open_entries_by_condition_id`
(polymarket_arbitrage.py:69–115, called :291)** — another `json_extract` scan of
`audit_event`.

## Proof (read-only, on the live DB)

| fact | evidence |
|---|---|
| `audit_event` is enormous | **`SELECT COUNT(*)` = 1,186,342 rows** (1.13 GB DB) |
| only index on the table | `ix_audit_event_ts ON audit_event(ts)` (db.py:39) — **nothing on actor/kind/json** |
| `_sum_polymarket_today` plan | **`EXPLAIN QUERY PLAN → SCAN audit_event`** (`substr(ts,1,10)` defeats the ts index) |
| `_polymarket_open_positions` plan | **`SCAN a`** (full 1.19M-row scan) + per-row `json_extract` for the join + LIST SUBQUERY |
| on the loop, not offloaded | `evaluate()` is sync, no `await`/`to_thread`; called directly in `async def _scheduled_*_arb_loop` |
| corroboration | the 16:04 freeze run-up logged `risk/risk_approved` + `hitl/pending_approval_added` (risk evals in flight) right before silence |

## Why this is H3 (matches the Phase-1b /proc capture exactly)

- **CPU-bound, not I/O:** the 1.13 GB DB is page-cached, so a full scan reads cached
  pages → the thread is `R` (on-CPU), never in `io_schedule`/disk-wait. ✔ main thread R/wchan=0.
- **~33% sys:** `pread64` on page-cached sqlite pages is counted as kernel/sys time even
  with zero disk I/O → matches the observed ~67% user (json parse + vdbe) / 33% sys split. ✔
- **Minutes-long & growing:** `_polymarket_open_positions` runs `json_extract` (a JSON
  parse) on each of 1.19M rows, per emitted order; `audit_event` is append-only (+rows
  every scan cycle), so scan cost rises monotonically with uptime → freeze duration grows
  and recurs. ✔ (the freezes are order-emission-driven — intermittent, ~74 min — not a fixed timer).
- **GIL-holding:** sqlite + json work on the main thread holds the GIL → the other ~11
  threads sit in `futex_wait`, uvicorn can't serve, the reconciler/exits stall. ✔

## Phase-2 fix class (for the confirmed target — NOT to be built yet)

This is an **"optimize"** H3 fix, NOT ProcessPool (the op is heavy only because it's an
unindexed 1.19M-row scan; make it bounded and it's trivially fast):
1. **Bound the scans with an index + range filter:** `CREATE INDEX ix_audit_event_actor_kind
   ON audit_event(actor, kind)` (or `(actor,kind,ts)`), and change `substr(ts,1,10)=?` →
   `ts >= ? AND ts < ?` so `_sum_polymarket_today` uses the ts index. Then `SCAN`→`SEARCH`
   and the per-row `json_extract` runs only on today's handful of `would_have_placed` rows,
   not 1.19M.
2. **Defense-in-depth:** wrap the three aggregate-cap queries in `asyncio.to_thread` so even
   a slow query can never freeze the loop.
Location = the **shared layer** (`agents/risk.py` + an index migration in `persistence/db.py`)
— NOT the polymarket division. (The queries are polymarket-scoped but live in the shared
`RiskAgent`.) Consider a one-time `audit_event` prune/archival separately (1.19M rows).

## Confidence & the optional definitive confirmation (Path A)
Inference from code + query plans + row count + on-loop verification — **high confidence**,
but not a captured frame. The armed watcher (`/tmp/pw3.sh`) still attempts py-spy each freeze
(sudo-denied). If you add the scoped NOPASSWD drop-in yourself, I'll switch the watcher to
`sudo py-spy` and the next freeze yields the exact Python frame to confirm `risk.py:_evaluate_polymarket`.
