# DB split map — before ingesting TradingView history for SOL/ETH/XRP (read-only)

Date 2026-06-26. No writes. Maps the two databases so the TV dump can be prepped correctly.

## TL;DR — the one thing that matters for ingestion
`btc_scalping.db` is **single-symbol per table**: each `bars_<tf>` table is BTC-only with **PK = `ts`
and NO symbol column**. The loader (`ingest_tv_export.py`) is hardwired to BTC (no `--symbol`), and
the SFP harness reads `bars_15m` directly. So SOL/ETH/XRP **cannot** go into the existing tables
(their bars would UPSERT-collide with BTC on matching `ts`). You must pick a per-symbol scheme.
**Recommended (zero code change): one DB per coin** via the loader's existing `--db` flag
(`data/sol_scalping.db`, …) — the harness just opens the per-coin DB. (Options below.)

---

## 1. THE BACKTEST DATABASE — `cc/data/btc_scalping.db`
Where SFP strategy validation runs against historical bars. Bybit BTCUSDT.P, TradingView-exported.

**Tables (9):**
| table | rows | role |
|---|---|---|
| `bars_1m` | 50,389 | BTC 1m OHLC + ~90 TV indicator cols |
| `bars_3m` | 38,899 | BTC 3m " |
| `bars_15m` | 22,086 | **BTC 15m — the SFP validation corpus** |
| `bars_30m` | 25,635 | BTC 30m " |
| `bars_1h` | 16,398 | BTC 1h " |
| `bars_3m_bitunix` | 16,387 | native Bitunix 3m (minimal: ts, OHLC, volume, venue) |
| `source_files` | 19 | TV-export ingest ledger (sha256, tf, ts range, rows) |
| `native_source_files` | 1 | native-CSV ingest ledger |
| `sqlite_sequence` | — | sqlite internal |

**Bar-table schema (TV tables, e.g. `bars_15m`):** `ts INTEGER PRIMARY KEY`, `datetime_utc TEXT`,
`open/high/low/close REAL`, `volume REAL`, **+ ~90 indicator columns** (emas, wavetrend, rsi, macd,
vwap, donchian, otter signals, cvd, …) all `REAL`. **No symbol column. PK = ts only → one symbol
per table.**
- `ts` = **Unix epoch SECONDS** (e.g. `1761955200` = 2025-11-01 00:00 UTC); 15m stride = 900s.
- `datetime_utc` = ISO-8601 UTC (`2025-11-01T00:00:00+00:00`), derived from `ts`.

**Per-(table) coverage** (BTC; one symbol per table):
| table | rows | UTC range |
|---|---|---|
| bars_1m | 50,389 | 2026-04-30 → 2026-06-19 |
| bars_3m | 38,899 | 2026-03-30 → 2026-06-19 |
| **bars_15m** | **22,086** | **2025-11-01 → 2026-06-19** |
| bars_30m | 25,635 | 2025-01-01 → 2026-06-19 |
| bars_1h | 16,398 | 2024-08-04 → 2026-06-19 |
| bars_3m_bitunix | 16,387 | 2026-05-15 → 2026-06-18 (native) |

**What the SFP 15m validation reads** (p6 oracle `confluence_exp6_p6_sfp_bos`):
- `TFTBL = {"1h":"bars_1h","30m":"bars_30m","15m":"bars_15m"}`; loads a read-only **copy** of
  `btc_scalping.db`.
- `load(tbl) = SELECT ts,open,high,low,close FROM {tbl} ORDER BY ts` — **only ts + OHLC**. The ~90
  indicator columns are NOT used by the SFP/BOS logic.
- **15m is stored NATIVELY** in `bars_15m` (a TV 15m export) — **not resampled** from 3m. (The
  harness also loads `bars_3m` for its 3m swing/fill context; Mode-A live setup is 15m.)
- → **BTC SFP depth to match: `bars_15m` 2025-11-01 → 2026-06-19 (~7.5 months, 22,086 bars).**

## 2. THE SFP / LIVE DATABASE — `trading_corp.db` (prod: `/home/azureuser/trading_corp/data/`)
The live division's runtime DB. **Confirmed NOT where backtest bars go** — it has **zero `bars_*`
corpus tables**. Holds: `paper_trade_record` (trades), `agent_state` (incl. the SFP loop heartbeat),
`audit_event`, and on prod `sfp_watch_state` (the watch-emit) + `bitunix_bar_history` (the LIVE
capture — symbol-keyed `(symbol, ts_ms, timeframe)`, native Bitunix, **ms** timestamps). That live
capture is a separate store from the backtest corpus and is NOT the ingest target.
(Note: the local `cc/data/trading_corp.db` is a stale dev copy — `sfp_watch_state`/`bitunix_bar_history`
live on prod.)

## 3. THE INGEST PATH — `scripts/ingest_tv_export.py`
`python scripts/ingest_tv_export.py <csv> [<csv> …] [--db PATH] [--timeframe 15m] [--report]`
- **Timeframe**: auto-detected from filename token `BYBIT_BTCUSDT.P, 15_<hash>.csv` (`15`→15m, `3`→3m,
  `60`→1h, `240`→4h, `1D`→1d, `1`→1m). Override with `--timeframe`.
- **Target table**: `bars_<tf>` — **hardcoded, no symbol dimension; assumes BTC.**
- **Required CSV column**: **`time`** = Unix epoch **SECONDS** → stored as `ts` (PK). Plus
  `open/high/low/close` (+ `volume`/indicators optional). Header is sanitized to snake_case;
  duplicates get `_1/_2`; every non-ts col stored `REAL`. `datetime_utc` is derived.
- **Upsert by `ts`** (`ON CONFLICT(ts) DO UPDATE`); **idempotent** (file-level sha256 dedup +
  row-level upsert); **schema-extends on the fly** (`ALTER TABLE ADD COLUMN` for new indicators).
- Writes the `source_files` ledger row. Explicitly: "does NOT feed `trading_corp.db`."

## ⚠️ The decision you must make before ingesting (single-symbol constraint)
The loader + harness assume one symbol per DB/table. Three ways to add SOL/ETH/XRP:

- **(A) One DB per coin — RECOMMENDED, zero code change.** Run the loader with `--db
  data/sol_scalping.db` (etc.) against each coin's CSVs. No collision; the p6 harness just opens the
  per-coin DB (its `ORIG` path is the only thing to swap — a 1-line param). Mirrors how BTC is
  already structured, isolated per coin.
- **(B) Per-symbol tables in one DB** (`bars_15m_sol`, …) — needs loader + harness changes (table
  naming gains a symbol suffix).
- **(C) symbol column + composite PK `(symbol, ts)`** — needs loader + harness changes (same shape
  as the live `bitunix_bar_history` symbol-key migration). Cleanest long-term, most code.

## What the TV dump should look like (to match BTC, assuming Option A)
- **Source**: Bybit perp, same as BTC — `BYBIT_SOLUSDT.P` / `BYBIT_ETHUSDT.P` / `BYBIT_XRPUSDT.P`.
- **Timeframe**: **15m** minimum (the SFP validation TF). Add **3m** if you want to run the full p6
  harness (it loads `bars_3m` for 3m context); 1h/30m only if you'll run those modes.
- **Depth**: from **~2025-11-01** (matches BTC `bars_15m`) through current, or as deep as Bybit
  history allows for each coin.
- **Format**: TradingView CSV with a **`time` column in Unix seconds** (the default TV export) +
  open/high/low/close. Indicators are optional for SFP (harness reads OHLC only) — a bare OHLC
  export is sufficient and simplest.
- **Filename**: keep the TV pattern (`BYBIT_SOLUSDT.P, 15_<hash>.csv`) so timeframe auto-detects, or
  pass `--timeframe 15m`.

## Verified facts (read-only, this session)
btc_scalping.db bar tables have PK=ts, no symbol col; bars_15m = 22,086 rows 2025-11-01→2026-06-19,
ts in Unix seconds. p6 reads ts+OHLC from bars_15m natively. ingest_tv_export.py: `time`(sec)→ts PK,
upsert-by-ts, bars_<tf> per timeframe, BTC-only, supports `--db`. trading_corp.db has no bars_* tables.
