# SPEC (do not build) -- ETH 1h emission-continuation shadow-logger

Log-only forward shadow of the ONE config from `XCOIN.md` (emission-clock
continuation, Normal, MACD off, SL=2.5*ATR(14), RR=3.5, SL-first, ETH 1h). Purpose:
accumulate paired (signal, realized-outcome) rows going forward in time, so the
ETH-only in-corpus result can be re-tested out-of-sample-in-time. It places **no
orders and gates nothing** -- a logged challenger, exactly like `llm_shadow`.

## What it writes

One new table in the recorder's db (`~/market_context/market_context.db`,
`MCTX_DB_PATH`). Append-only; outcome columns are NULL until the nightly backfill
resolves them. Follows the `llm_shadow` spirit (log-at-fire, pair-outcome-later,
verdict gated at `SIG_N`).

```sql
CREATE TABLE IF NOT EXISTS optitrade_shadow (
  id            INTEGER PRIMARY KEY,
  coin          TEXT NOT NULL,   -- 'ETHUSDT'
  timeframe     TEXT NOT NULL,   -- '1h'
  signal_set    TEXT NOT NULL,   -- 'emission_continuation_normal_macd_off'
  venue         TEXT NOT NULL,   -- the recorder's live feed venue (RECORD it; study was binance/bybit)
  signal_ts     TEXT NOT NULL,   -- ISO-UTC of the CLOSED 1h bar that fired
  side          TEXT NOT NULL,   -- 'long' | 'short'
  entry_ref     REAL NOT NULL,   -- close at signal bar
  atr14         REAL NOT NULL,
  sl_mult       REAL NOT NULL,   -- 2.5
  rr            REAL NOT NULL,   -- 3.5
  sl            REAL NOT NULL,   -- entry -/+ sl_mult*atr14
  tp1 REAL, tp2 REAL, tp3 REAL, tp4 REAL,   -- entry +/- rr*(i/4)*sl_mult*atr14
  logged_ts     TEXT NOT NULL,
  -- outcome-backfill (NULL until resolved) --
  outcome       TEXT,            -- 'sl' | 'tp1'..'tp4' | 'all_tp' | 'open'
  rungs_hit     INTEGER,
  realized_r    REAL,            -- SL-first accounting, identical to optitrade_bt.simulate
  exit_ts       TEXT,
  backfilled_ts TEXT,
  UNIQUE(coin,timeframe,signal_set,signal_ts,side)
);
```

Signal fields (ts/side/entry/bracket) mirror the study's `optitrade_ai_signals` +
`optitrade_bt`; `realized_r` uses the same SL-first, 4-rung, MTM-if-open accounting
so forward numbers are directly comparable to the corpus tables.

## Where it runs

**Inside the Market-Context Recorder process** -- the sole writer of
`market_context.db` (the same process that emits `context_snapshot`/`llm_shadow`
rows). Two hooks, both in that process (never in the trading engine):

1. **On each CLOSED ETH 1h bar** (the recorder already ticks market data): compute
   the emission-continuation signal on hlc3-EMA Normal ribbon + ATR(14); if it
   fires, `INSERT OR IGNORE` one row with side/entry/bracket. Emission-clock spacing
   (`>30` bars since last EMISSION) is derived from the table itself
   (`SELECT max(signal_ts) ... WHERE side=? `) so the logger is restart-safe and
   stateless. Needs ~300 bars of 1h warmup (EMA-120 + freshness); backfill history
   once at first run.
2. **Nightly backfill**: for rows with `outcome IS NULL` whose bracket has had
   enough subsequent 1h bars to resolve, replay SL-first and write
   `outcome/rungs_hit/realized_r/exit_ts/backfilled_ts` -- exactly the `trade_outcome`
   pairing step of the `llm_shadow` pattern, keyed on `signal_ts` instead of order_id.

Consumers (an optional `/optitrade-shadow` dashboard tile) open the db `mode=ro`,
like `sfp_llm_analysis_view` -- never a 2nd writer.

## How it stays zero-impact

- **No order path.** It imports no broker/execution module and calls nothing that
  can place, size, or gate a trade. The engine never reads this table for any
  decision.
- **Single-writer preserved.** It runs in the recorder (already the only writer of
  `market_context.db`) and adds one table -- no new writer process, no lock
  contention, and a **different db file from `trading_corp.db`** (which is never
  touched, read or write).
- **Fail-closed.** All logger/backfill logic is wrapped so any exception is caught
  and logged and **never propagates** to the recorder's main loop (same discipline
  as "never raises to the route"). A logging failure cannot affect recording.
- **Idempotent & bounded.** `UNIQUE(...)` + `INSERT OR IGNORE` makes bar reprocessing
  a no-op; per-bar work is 10 EMAs + ATR on a rolling window; backfill scans only
  unresolved rows.
- **Honesty gate (mirror `llm_shadow`).** Any forward verdict is gated behind
  `n >= SIG_N` (30) resolved rows; below that the display shows
  "accumulating n/N -- not yet significant" and the banner states it gates no trade.

## Open items to confirm at build (not assumed here)

- Exact recorder module + its per-closed-bar hook (I read the read-only *reader*
  `sfp_llm_analysis_view.py`; I did not pin the recorder *writer* file -- confirm it
  before wiring the two hooks).
- The recorder's live ETH 1h feed venue (Bitunix? aggregated?) -- record it in
  `venue`; forward results are on that feed, whereas the corpus study was
  Binance/Bybit, so the two are comparable in method but not identical in venue.
