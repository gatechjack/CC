# BitUnix 3m history backfill — Step-1 assessment + plan + sample-proof (2026-06-26)

Goal: pull ~7.5 months of 3m klines (back to ~2025-11-01) for BTC/ETH/SOL/XRP into the LIVE
`trading_corp.db:bitunix_bar_history`, to validate 15m-SFP→3m-BOS at verdict-grade n. The live engine
(PID 3641539) is writing this same table continuously — the backfill must be **additive, idempotent,
and watched**. Read-only assessment + local sample-proof done here; **bulk pull HELD for operator go.**

## STEP 1 — READ-ONLY ASSESSMENT

### Schema (live prod, md5-read off the server) — OHLCV only, NO derived columns
```
CREATE TABLE "bitunix_bar_history" (
    symbol TEXT, ts_ms INTEGER, timeframe TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    inserted_at TEXT NOT NULL,
    PRIMARY KEY (symbol, ts_ms, timeframe)
)   -- index: (symbol, timeframe, ts_ms)
```
A historical 3m row needs exactly these 9 fields. **No indicators/derived columns** — the archiver
(prod md5 `53c2e64d`) writes raw OHLCV + a write-timestamp. Live write path, byte-for-byte:
- row tuple `(cache.symbol, b.ts_ms, cache.timeframe, open, high, low, close, volume, now)`
- `symbol` = `BTCUSDT`/`ETHUSDT`/`SOLUSDT`/`XRPUSDT`; `ts_ms` = bar-OPEN epoch-ms; `timeframe` = `3m`
- `volume` = the kline `baseVol` field (live cache line 134; NOT quoteVol)
- `inserted_at` = `datetime.now(utc).isoformat(timespec="seconds")` → `2026-06-26T23:04:20+00:00`
- statement: `INSERT OR IGNORE INTO bitunix_bar_history (symbol,ts_ms,timeframe,open,high,low,close,volume,inserted_at) VALUES (?,?,?,?,?,?,?,?,?)`
- live cache **drops the in-progress bar** (`ts+granularity > now`) — backfill mirrors this at the edge.

### Current 3m coverage (the gap) — backfill fills BELOW the live data, no overlap
| coin | rows | earliest | latest |
|---|---|---|---|
| BTCUSDT | 20,219 | 2026-05-15T05:30Z | 2026-06-26T23:00Z |
| ETHUSDT | 20,415 | 2026-05-15T05:30Z | 2026-06-26T23:00Z |
| SOLUSDT | 20,415 | 2026-05-15T05:30Z | 2026-06-26T23:00Z |
| XRPUSDT | 20,415 | 2026-05-15T05:30Z | 2026-06-26T23:00Z |

Live capture only goes back **~6 weeks** (2026-05-15). Gap to fill = **2025-11-01 → 2026-05-15 =
195.2 days = ~93,710 bars/coin**. The per-coin "seam" (live MIN ts) is the hard upper bound for the
backfill, so backfilled ts are strictly **disjoint** from live rows (no PK collision possible).

### The BitUnix kline API (public, no auth)
- `GET https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=&interval=3m&startTime=&endTime=&limit=200`
- **Server cap: 200 bars/call** (matches the live fetcher's verified cap). Response `data.code==0`,
  `data.data[]` rows with keys `time, open, high, low, close, baseVol, quoteVol`.
- **★ Reach-back PROVEN**: 200 bars returned at every probed anchor 2025-04-01 … 2026-06-26 for all 4
  coins — the endpoint reaches **well past** the 2025-11-01 target. No history-depth cap.
- **★ UA gotcha**: BitUnix now 403s urllib's default UA on both local and prod. Fix = send a browser
  `User-Agent` header (the live engine's httpx works only because of its own UA). Baked into the script.
- Rate: memory's "1 req/s/uid" is the signed-endpoint limit. This is the public market endpoint; we
  still pace conservatively (≥1.2 s/call, <1 req/s) per operator's "don't hammer / all night is fine."

### Derived columns? — None. (answered: just OHLCV + inserted_at.)

## STEP 3 — PLAN (per-coin call count, time, checkpoint, exact upsert)
| item | value |
|---|---|
| range/coin | 2025-11-01T00:00Z → live-seam (≈2026-05-15) = ~93,710 bars |
| windows/coin (=API calls) | ⌈93,710 / 200⌉ = **469** |
| total API calls (4 coins) | **~1,876** |
| pace | **1.2–1.5 s/call** (0.67–0.83 req/s) |
| **est. wall time** | **~45 min @1.2s → ~55 min @1.5s** (well under "all night") |
| rows expected | ~93k/coin, ~374k total (minus any venue gaps) |

- **Checkpoint**: per-coin cursor (ms) → JSON, persisted after **every** window via atomic `os.replace`.
  Resume = `max(checkpoint[coin], start)`. A stop/restart continues, never restarts. (Idempotent UPSERT
  means re-doing a window is harmless anyway.)
- **Exact upsert** (identical to the archiver): `INSERT OR IGNORE` on PK `(symbol, ts_ms, timeframe)`.
  Strictly additive — a pre-existing row (live OR prior backfill) is never overwritten; the live row
  always wins. Scoped to one (coin, '3m') at a time; never any other symbol/timeframe; never a schema change.
- **★ Live-capture safety** (engine runs throughout): pre-run snapshot of each coin's live high-water
  `MAX(ts_ms)` + row count + table `CREATE` sql; re-checked after every 25 windows + after each coin.
  ABORT LOUD (exit 3) if the live high-water regresses, a count shrinks, or the schema drifts. WAL +
  `busy_timeout=30000` + per-window commit so the engine's 60s archiver write is never starved.
- **OHLC validation**: reject only TRUE corruption (missing field / price≤0 / vol<0). **Tick-rounding
  "inversions" are KEPT** — see note below.

### ★ Tick-rounding note (why we do NOT drop "inverted" bars)
The venue rounds O/H/L/C to the tick independently, so ~3–4% of 3m bars have e.g. high 0.1 below open.
**The live archiver writes these raw** — prod already holds 569 (BTC) / 791 (ETH) / 686 (SOL) / 920
(XRP) such 3m rows, and **zero** genuinely-corrupt rows. Dropping them would gap the data the SFP/BOS
contiguity walks and break byte-parity with live. So the backfill writes them too (counted as
`tick_odd` for transparency, not dropped).

## SAMPLE-PROOF (local throwaway clone — NO prod write)
Pulled 400 real BTCUSDT 3m bars (2025-11-01 window) via the script into a prod-shape clone DB:
- **SCHEMA MATCH vs prod DDL**: PASS (clone `CREATE` byte-identical to live table)
- **BYTE-SHAPE PARITY**: PASS — every column type matches a real prod live row
  (`str/int/str/float×5/str`); `inserted_at` = ISO-sec `+00:00` like the archiver
- **CONTIGUITY**: PASS — 400 bars at 180000ms, 0 gaps
- **INTEGRITY**: PASS — 0 corrupt; `bad=0`, `tick_odd=14` (written, like live)
- **IDEMPOTENCY**: PASS — re-run (checkpoint reset, same windows) wrote **0** rows, coverage unchanged
- **RESUMABILITY**: PASS — a checkpointed re-run continued from the saved cursor
- Clone deleted after; no prod row touched.

## OPEN RUNBOOK ITEM (for the bulk run, on operator go)
The bulk run executes **on prod** (the sqlite file lives there; it pulls the public API + UPSERTs the
local DB in-place while the engine runs). It must run with **write permission to
`data/trading_corp.db`** — i.e. as whatever user/permission the engine uses to write it (the operator's
NOPASSWD `sqlite3` for trading-corp suggests it may need the privileged path; a `python3` process must
have the same write access). Stdlib-only (urllib + sqlite3 + json), no deps to install. Proposed:
`python3 scripts/backfill_bitunix_3m_history.py --db data/trading_corp.db --end auto --rate 1.2`
(resumable; safe to Ctrl-C and re-run). **Held for operator approval before the ~1,876-call pull.**
