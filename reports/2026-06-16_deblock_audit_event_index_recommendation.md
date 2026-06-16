# audit_event index recommendation — what still scans it after A (read-only analysis)

§4 read-only (82fda13). Branch `bitunix-deblock-eventloop-2026-06-16` (post-A removal,
90083ab). **Analysis + recommendation only — no index built, no deploy.**

## Headline

**A's removal already fixed the RISK path: post-A, `risk.py` has ZERO `audit_event`
queries** (grep confirms — only removal-comment lines remain). The KEPT stocks/futures +
global risk caps (`evaluate()` halts, per-strategy daily-loss, per-account drawdown
breaker/flatten = the bitunix DD-cap path) use in-memory `account`/`strategy_state`
snapshots — **they do NOT scan `audit_event`.** So **no index is needed for the risk
caps; A's removal suffices** (the priority question).

**HOWEVER** — A only touched `risk.py`. Three OTHER kept queries still do the *same*
unindexed full-`audit_event` scan on the event loop (a latent freeze family A didn't
address). One index fixes all of them.

## Every remaining `audit_event` reader, classified

`audit_event` = **1,186,342 rows**, single index `ix_audit_event_ts(ts)` only
(`persistence/db.py:39`; schema: `id PK, ts, actor, kind, payload_json`).

### KEPT, ON-LOOP, full SCAN (latent freeze risk) — EQP-confirmed on prod
| query | file:line | freq | EQP (current) |
|---|---|---|---|
| `polymarket_resolver` round-trip pairing | `agents/polymarket_resolver.py:65` | periodic resolver | **`SCAN a`** |
| `kalshi_resolver` round-trip pairing | `agents/kalshi_resolver.py:135` | periodic resolver | **`SCAN a`** |
| `_count_open_entries_by_condition_id` (the flagged dedup) | `agents/strategies/polymarket_arbitrage.py:69 / :291` | once/scan-cycle | **`SCAN audit_event`** |
| reconciler latest-state read | `agents/divisions/bitunix_position_reconciler.py:498` | every 60s | **`SCAN audit_event`** (ORDER BY id DESC LIMIT 1 — recency-mitigated, but unbounded worst-case) |

All four filter **`actor = ? AND kind = / IN (...)`** (then `json_extract` on the rows).
risk.py's removed scans were the same shape — these are the survivors.

### NOT a freeze risk (no index needed)
- **`web/data.py` (~30 queries) + `web/routes.py`** — dashboard/reporting; `web/data.py`'s
  contract is "all sync DB work pushed to `asyncio.to_thread`" → OFF the loop. `routes.py`
  uses `WHERE id = ?` (PK SEARCH).
- **`agents/logger.py`** — recent-events reads (`ORDER BY id DESC LIMIT ?`, fast PK walk),
  dashboard/CLI-triggered.
- **`agents/data_exec.py`, `bitunix_futures_observer.py`** — `WHERE id = ?` (PK SEARCH).
- **`ic_live_view.py` / `ic_telemetry.py` / `ic_daily_digest.py`** — IC reporting/digest,
  occasional (dashboard / cron), not per-cycle on the trading loop.
- **`scripts/*` (`ic_daily_digest`, `ic_paper_run_readiness`, `prune_stale_pct_entries`)**
  — one-off scripts/cron, not the running engine loop.

## Recommended index (build nothing — operator decides)

```sql
CREATE INDEX IF NOT EXISTS ix_audit_event_actor_kind ON audit_event(actor, kind);
```

**Why this exact shape:** all four on-loop SCANs filter `actor = ? AND kind = …` — an
equality match on the index's left prefix, so SQLite uses it. It narrows 1.19M rows to the
tiny `(actor,kind)` subset; the per-row `json_extract` IN/NOT-IN / `ORDER BY id` then runs
only on that subset.

**EQP-after (SCAN → SEARCH) — CONFIRMED** (local in-memory sqlite 3.50.4 with the real
`audit_event` schema + this index; prod untouched). Every on-loop reader flips to an
indexed search:
```
reconciler           : SEARCH audit_event USING INDEX ix_audit_event_actor_kind (actor=? AND kind=?)
_count_open_entries  : SEARCH audit_event USING INDEX ix_audit_event_actor_kind (actor=? AND kind=?)
polymarket_resolver  : SEARCH a          USING INDEX ix_audit_event_actor_kind (actor=? AND kind=?)
kalshi_resolver      : SEARCH a          USING INDEX ix_audit_event_actor_kind (actor=? AND kind=?)
```
(Residual TEMP B-TREE for ORDER BY/GROUP BY now runs on the tiny `(actor,kind)` subset, not
1.19M rows.) Prod EQP already proved the *current* state is `SCAN` for all four.

## Verdict

- **Risk caps: NO index needed** — A's removal eliminated the risk-path scan; the kept
  caps don't touch `audit_event`.
- **Resolvers + the flagged dedup: recommend adding `ix_audit_event_actor_kind`** to the
  **C + A bundle**. It's cheap insurance that closes the same freeze mechanism on the
  periodic resolvers (which A did not touch) and lets the operator KEEP the Board-approved
  `_count_open_entries` dedup **indexed** rather than removing it. Single index covers all
  four readers.
- **Migration:** add the one line to `persistence/db.py` (next to `ix_audit_event_ts`,
  line 39) as a startup migration. **Additive + idempotent** (`CREATE INDEX IF NOT EXISTS`,
  never touches/rewrites rows). One-time cost: a single index build over 1.19M rows at the
  post-deploy restart (~seconds; note the brief startup work in the deploy window).
- **Defer option:** if the operator instead REMOVES `_count_open_entries` and accepts the
  resolvers' recency/LIMIT mitigation, the index can be deferred — but the resolvers remain
  a latent (lower-frequency) freeze surface, so the cheap idempotent index is the safer call.

## Bundle note
A = `risk.py` removal (done, 90083ab); the index = `persistence/db.py` (disjoint). C =
observer/`main.py`/`strategies.yaml`. All disjoint → compose for one deploy window.
