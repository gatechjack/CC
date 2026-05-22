# quote_snapshot persistence — design

**Status:** design only, **NO deploy without operator review.** Long-pole infrastructure for Tier C (real-data PnL) replay of the kalshi_weather hourly re-evaluation work.

**Constraints (operator-mandated, repeated):**
- Additive. New table, new write path. No edits to existing strategy or broker code paths.
- Paper-safe. Failure to snapshot must not affect any trading decision.
- Write-only logging. No read path used by the strategy.
- Design only — operator approves before any deploy.

---

## 1. Why

Replay design doc (`planning/kalshi_weather_hourly_reeval_design.md`) found intraday Kalshi quotes (yes_bid/yes_ask/no_bid/no_ask between entry and settlement) are MISSING from prod persistence. The A2 pre-flight confirmed verbatim: the four prices are absent from `would_have_placed` payload — only `implied_prob_at_entry` is captured, single-snapshot at entry.

Without intraday quote history, Tier C (real-data exit-price simulation) cannot run. Tier A (decision-quality) and Tier B (model-based PnL with assumed-constant spread) can run today, but Tier B is `_DIRECTIONAL_ONLY_spread_assumed_constant` and cannot green-light any position-acting code. Only Tier C, run against ≥30 days of accumulated real intraday quotes, can produce a defensible EV-net-of-costs verdict.

Every day delayed pushes the Tier C clock out. This design is the long-pole.

---

## 2. Recommended approach (one-line summary)

**Sidecar Python script driven by a systemd timer, hourly, queries Kalshi for current quotes on the active weather market universe via a fresh `KalshiBroker` instance, writes to a new `quote_snapshot` table via a fresh SQLite connection with `journal_mode=WAL`.** No edits to `kalshi_weather_arb.py`, `brokers/kalshi.py`, or any other strategy/broker file. Independent of `trading-corp.service` — if the sidecar dies, the running service is unaffected.

---

## 3. Schema

New table `quote_snapshot`:

```sql
CREATE TABLE IF NOT EXISTS quote_snapshot (
  ts          TEXT    NOT NULL,        -- ISO UTC, e.g. '2026-05-22T19:00:00+00:00'
  ticker      TEXT    NOT NULL,        -- Kalshi market ticker, e.g. 'KXHIGHNY-26MAY22-B76.5'
  yes_bid     REAL,                    -- dollars (e.g. 0.51); NULL if book empty side
  yes_ask     REAL,
  no_bid      REAL,
  no_ask      REAL,
  source      TEXT    NOT NULL         -- 'kalshi_weather_arb_sidecar_v1' (versioned for future migrations)
);

CREATE INDEX IF NOT EXISTS ix_quote_snapshot_ticker_ts ON quote_snapshot (ticker, ts);
CREATE INDEX IF NOT EXISTS ix_quote_snapshot_ts ON quote_snapshot (ts);  -- for time-windowed replay queries
```

Notes:
- No PRIMARY KEY beyond the implicit rowid. Duplicates are acceptable; replay queries use `MAX(ts) WHERE ts <= H` for the latest snapshot.
- No foreign keys to `audit_event` / `proposed_order`. Loose coupling; analysis-only.
- NULL semantics: book may be one-sided (no resting bid or no resting ask). Replay code must handle missing sides.
- Storage estimate: ~30 active weather markets × 24 snapshots/day × ~80 bytes/row = ~56 KB/day → ~20 MB/year. Negligible.

---

## 4. Cadence

**Every hour, on the hour (UTC).** Triggered by systemd `OnCalendar=hourly` (or `*-*-* *:00:00`).

Rationale:
- Operator's stated target is "hourly re-evaluation of open positions." Replay needs at minimum hourly grain.
- Higher cadence (every 5 min) matches the scan cycle but is overkill for the hourly analysis and creates ~12× the storage.
- Lower cadence (every 4-6 h) loses signal resolution for mid-position decisions.
- Hourly matches METAR observation cadence (per A4) and Open-Meteo update cadence.

Drift tolerance: if the sidecar lags or skips an hour (Kalshi API hiccup, VM busy), best-effort — log and continue. Replay code tolerates gaps via `MAX(ts) WHERE ts <= H`.

---

## 5. Filter scope

**All weather markets in the strategy's discovery universe, not just markets with open positions.**

Rationale:
- Replay needs to answer "would NEW (open-new-position) signals at hour H have netted?" — this requires quotes for markets we considered but didn't fire on, not just markets we hold.
- ~30 markets per scan (per recent `kalshi_weather_scan` audit data: `candidates=24`, `pre_filter=76-123` after disabled-series filter).
- Cost: ~30 markets × 24 snapshots/day vs ~5 markets × 24 snapshots/day = 6× the data, still tiny in absolute terms.

Discovery mechanism: sidecar calls `KalshiBroker.list_markets(categories=['Climate and Weather'])` (re-using the existing broker method) once per snapshot cycle, gets the universe, snapshots each. The Climate-and-Weather category filter is the same one the live strategy uses.

Exclude `KXTEMPNYCH*` (the AccuWeather-sourced disabled prefix per Track-1) — read `_DISABLED_SERIES_PREFIXES` constant from the strategy module without mutating it. If that import is undesirable, hard-code the set in the sidecar config and document the drift risk.

---

## 6. Integration point

**New standalone script + systemd timer.** No edits to:
- `kalshi_weather_arb.py` — strategy file untouched
- `brokers/kalshi.py` — broker file untouched
- `trading-corp.service` systemd unit — main service untouched
- `main.py` wiring — process tree unchanged

New files (all in repo, all gitignored from prod via deploy script):
- `scripts/snapshot_kalshi_weather_quotes.py` — the sidecar entry point
- `infra/systemd/kalshi-weather-quotes-snapshot.service` — oneshot service definition
- `infra/systemd/kalshi-weather-quotes-snapshot.timer` — hourly trigger

Sidecar process tree:
```
sidecar process (own PID)
  ├── reads KV secrets (own KV client)
  ├── instantiates KalshiBroker(api_key_id, private_key_pem)  -- own instance, own PEM tempfile
  ├── connects + lists weather markets + iterates per-market quote fetches
  ├── opens sqlite3 connection to /home/azureuser/trading_corp/data/trading_corp.db
  ├── PRAGMA journal_mode=WAL (idempotent; main service already runs WAL)
  ├── INSERT INTO quote_snapshot ...
  ├── closes connection (releases WAL frame)
  └── exits cleanly (oneshot service)
```

Independence:
- Sidecar's `KalshiBroker` instance is separate from the main service's instance. Two clients hit Kalshi's API independently. Polite usage assumed (rate limit budget shared but small per-cycle: ~30 calls hourly).
- Sidecar's SQLite connection is separate from the main service's. WAL mode allows concurrent reads + one writer; since the main service writes to different tables (`audit_event`, `kalshi_round_trips`, etc.) the writes don't contend.
- Sidecar's PEM tempfile is in `/tmp`, deleted on exit. Main service's PEM is independent.

---

## 7. Failure mode

The sidecar is best-effort logging. All failure modes are non-blocking to trading.

| Failure | Behavior |
|---|---|
| KV fetch fails | Log warning, exit cleanly. Timer fires again in 1h. |
| KalshiBroker connect fails | Log + exit. Timer fires again. |
| Kalshi `list_markets` fails | Log + exit. (Note: if Kalshi API is fully down, the main service has the same problem and that's the visible signal — sidecar silence is not load-bearing.) |
| Per-market `get_market` fails (one ticker) | Log, skip that ticker, continue to next. INSERT what we have. |
| SQLite INSERT fails | Log + continue. Worst case: missing hour for some/all tickers in this snapshot cycle. |
| Sidecar exceeds wall-clock budget (e.g. 5 min for one cycle) | systemd timer's `OnCalendar=hourly` ensures next cycle still fires; lagging cycles do not pile up because `Type=oneshot` cleans up. |
| Sidecar crashes mid-write | WAL mode means the next sqlite reader sees a consistent state; no partial-row corruption. Main service unaffected. |

Critical invariant: **the sidecar has no path that affects trading decisions, places orders, or modifies any table other than `quote_snapshot`.**

Audit-trail: each sidecar invocation writes ONE row to `audit_event` with `actor='kalshi_weather_quotes_sidecar'`, `kind='snapshot_cycle_complete'`, payload `{tickers_seen: N, tickers_written: M, duration_ms: T, errors: [...]}`. Lets us observe sidecar health from the dashboard without instrumenting it deeply.

Or — even simpler to keep the "no edits to existing tables" rule strict: the sidecar logs to its own log file under `/var/log/trading-corp/quote-snapshot.log` (managed by systemd journald) and we read journalctl for health. Whichever the operator prefers.

---

## 8. Concurrency + rate limits

- Kalshi API rate limit: per `pykalshi` docs, soft limit ~10 req/s. Sidecar makes ~30 sequential requests per cycle (one `list_markets` + ~30 `get_market`); with 100ms sleep between, ~3-4 sec total per cycle. Comfortably under any reasonable limit.
- Main service's scan cycle (every 5 min) also queries Kalshi. Sidecar's hourly cycle aligns roughly with one out of every 12 scan cycles. No coordination needed.
- SQLite contention: WAL allows reads during write; sidecar writes ~30 rows per cycle in ≤ 50 ms. Main service's writes to other tables are uncontested.

---

## 9. Rollback story

Three reversible deploy steps; rollback is inverse:

| Step | Forward | Backward |
|---|---|---|
| 1. Schema | `CREATE TABLE quote_snapshot ...` (idempotent, via migration script) | Leave table in place — read-only data is harmless. Optional: `DROP TABLE quote_snapshot` if disk pressure later. |
| 2. Sidecar script + units | Copy `scripts/snapshot_kalshi_weather_quotes.py` to prod, install systemd unit + timer, `systemctl enable --now <timer>` | `systemctl disable --now <timer>; systemctl stop <service>` — stops snapshots immediately. Remove files at leisure. |
| 3. (Optional) Audit-event integration | If we add `snapshot_cycle_complete` audit kind | No-op rollback — the audit_event row is just data |

No code changes to revert in the existing strategy or broker layers.

---

## 10. Deploy plan (for operator review; NOT execution)

When operator approves:

1. **Migration on prod first** (idempotent CREATE TABLE IF NOT EXISTS) via az run-command — no service restart, no behavior change.
2. **Copy sidecar script + units to prod** via scp (or az run-command base64 push).
3. **`systemctl daemon-reload; systemctl enable --now kalshi-weather-quotes-snapshot.timer`** — first snapshot fires on next hour boundary.
4. **Verify after 2-3 cycles:** query `SELECT COUNT(*), MIN(ts), MAX(ts) FROM quote_snapshot;` — expect ≥2 cycles × ~30 rows = ~60+ rows, ts strictly increasing.
5. **Monitor for 24h** before relying on data for any replay decision.

After ≥30 days of clean accumulation, Tier C replay becomes runnable.

---

## 11. What this design does NOT include (out of scope)

- **No changes to existing strategy or broker code.** Re-iterating because it's the core constraint.
- **No retention/pruning policy yet.** Will land when storage approaches problematic levels (~years out at current cadence).
- **No backfill of historical quotes.** Kalshi doesn't archive bid/ask history; no backfill is possible. Tier C clock starts at deploy.
- **No quotes for non-weather Kalshi markets.** Other Kalshi strategies (LLM-divergence, copy-trading, structural arb) are unaffected by this design. If similar accumulation is wanted for them, design a sibling sidecar.
- **No real-time quote streaming via Kalshi WebSocket.** The hourly polling cadence matches replay needs at minimum cost; streaming is over-engineered for this use case.
- **No instrumentation of failure rate.** Adding metrics/alerting on sidecar health is a follow-up if observed failures justify it.

---

## 12. Decisions surfaced for operator (not pre-resolved)

These shape deploy, not design. Plan stops before deploy regardless.

1. **Audit-event integration vs log-only health observability.** Section 7 alternative. Default proposal: log-only via journald (cleaner separation from existing tables).
2. **Sidecar deploy mechanism.** scp-then-systemctl, or az-vm-run-command base64-push? Both work. Default: az-run-command for consistency with this session's other prod-touches.
3. **First-cycle clock start.** Deploy at next hour boundary (clean), or immediately (faster start to Tier C accumulation)? Default: clean hour boundary.
4. **KV path for sidecar credentials.** Inherit from main service env (same `EnvironmentFile=` if any) vs new dedicated `EnvironmentFile=` line? Default: new dedicated, isolated from main service.

---

## Cross-references

- `planning/kalshi_weather_hourly_reeval_design.md` — replay design; this sidecar is the long-pole infrastructure for Tier C in that design.
- `BACKLOG.md` Item 2 (P2 kalshi_weather intraday work, 2026-05-22) — the originating backlog item.
- `trading_corp/brokers/kalshi.py` — `KalshiBroker.list_markets`, `KalshiBroker.quote` — sidecar re-uses; does not modify.
- `[[feedback-session-committed-phantom-pointer]]` — design committed durably (this doc) to avoid a phantom pointer.
