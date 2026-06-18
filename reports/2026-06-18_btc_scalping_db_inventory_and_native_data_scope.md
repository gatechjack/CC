# btc_scalping.db Inventory + Native-Data Feed Scope

**Date:** 2026-06-18
**Branch:** `btc-scalping-db-native-data-scope-2026-06-18` (worktree off the redeem-cap engine `74a23b4`)
**Mode:** READ-ONLY inventory + scoped plan. NO code, NO DB writes, NO deploy. Agent SSH read-only per CLAUDE.md (82fda13). Per §4.
**Purpose:** Inventory the static backtest corpus `data/btc_scalping.db`, assess what the now-accumulating live BitUnix-native data adds, and scope wiring that native data into the corpus so the existing redeem-cap backtest engine (`74a23b4`) can run on real BitUnix data instead of the Bybit proxy.

> ⚠️ **READINESS-ONLY, NOT AN EDGE VERDICT.** Nothing here draws any strategy-performance / profitability / edge conclusion. Live trading is only ~5 days old (N tiny). All counts below are descriptive: data shape, completeness, capture form, and ingest feasibility only.

---

## TL;DR

1. **Corpus (`btc_scalping.db`) is frozen** — Bybit BTCUSDT.P, 3m span `2026-03-30 → 2026-05-16`, 22,635 bars, unchanged since mid-May. Full Market Cypher + Lord Otter signal columns present and populated.
2. **Native BitUnix bars ARE being captured to the prod DB** (`bitunix_bar_history`), 3m OHLCV, **since 2026-05-15** (~34 days, 16,366 rows, 480/day, no gaps) — this is *more* than the "~5 days" framing (which refers to live *trades*, not bars). **OHLCV only — no signal columns.**
3. **The redeem-cap engine only needs OHLCV from the DB** (`ts,open,high,low,close,volume`); ATR is recomputed; alerts come from a separate prod-cache feed. So **native OHLCV fully satisfies the engine's bar contract** for the `--alert-source prod_cache --gate pa_validation --redeem-arms` path.
4. **The original "native high-vol 3m" gap is NOT filled.** Native data so far is **low-vol June**. Historical high-vol native bars *can* be backfilled via the kline API, but their **alerts cannot** (TradingView-only; only live webhook signals are captured, in `bitunix_signal_ledger`). So the high-vol arm remains blocked.
5. **Data-quality:** the P2 result-sign bug is **confirmed active** (2 of 4 resolved live trades are wins booked as `result='loss'`). **It does NOT pollute backtest inputs** — the engine re-derives outcomes from the bar walk; booked `result` is never read as ground truth. It matters only if booked live results are used to *validate* the backtest. `actual_pnl_dollars`/`actual_r_multiple` carry the correct sign; the categorical `result` label does not.

---

## Part 1 — `data/btc_scalping.db` current state (local, read-only)

Probed read-only via `sqlite3 mode=ro`. File: `C:\Users\AA Incorporado\cc\data\btc_scalping.db`, 28.3 MB, last modified **2026-05-18**.

### Tables & ranges

Timestamp column: `ts` (INTEGER, **Unix epoch seconds**), with a parallel `datetime_utc` (TEXT, ISO-8601) human-readable duplicate.

| Table | Rows | Span (UTC) |
|---|---|---|
| `bars_1m` | 24,442 | 2026-04-30 00:00 → 2026-05-16 23:59 |
| `bars_3m` | 22,635 | 2026-03-30 00:00 → 2026-05-16 03:42 |
| `bars_15m` | 15,571 | 2025-12-04 23:00 → 2026-05-16 03:30 |
| `bars_30m` | 18,653 | 2025-04-22 13:30 → 2026-05-16 03:30 |

Plus `source_files` (ingestion provenance log) and `sqlite_sequence`. No metadata/config table.

### Growth vs mid-May snapshot

`bars_3m`: prior snapshot ~22,600 rows / `2026-03-30 → 05-16`; now **22,635** / same span. **Effectively static** (+35 rows, 10 re-ingestion passes against the same window). The corpus is a **frozen Bybit snapshot, not a live feed** — consistent with the 2026-05-18 file mtime.

### Signal/indicator columns

`bars_3m` has **93 columns**. Market Cypher B and Lord Otter signatures are present and **populated across the full 3m span** (signals fire from the first day, no partial-span gap):

- **Market Cypher B (continuous, fully populated):** `wt_wave_1/2` (22,608/22,606), `vwap` (22,606), `rsimfi` (22,576), `mfi_bar_top/bottom_line` (22,635), `money_flow_signal` (22,631).
- **Market Cypher B (event-sparse fires):** `buy_circle` (557), `sell_circle` (564), `divergence_buy/sell_circle` (202/235), `wt_bearish/bullish_divergence` (657/253), `cvd_flip_bullish/bearish` (542/542), `gold_buy_gold_circle` (19).
- **Lord Otter (fully non-null, fires from day 1):** `otter_sell` (82), `otter_buy` (40), `ribbon_buy/sell_cross` (2,089/2,200), `super_sell/buy_high` (47/22), `super_sell/buy_std` (7/4), `top_signal` (49), `bottom_signal` (22).
- **Schema stubs, ENTIRELY NULL (never ingested):** `schaff_trend_cycle_1/2`, `sommi_bearish/bullish_flag`, `sommi_higher_vwap`, `sommi_bearish/bullish_diamond`.

### Venue / source

**Unambiguous: Bybit, BTCUSDT.P (perpetual).** Every row in `source_files.filename` is `BYBIT_BTCUSDT.P, <tf>_<hash>.csv` (3m ids 1–10, 15m 11–12, 30m 13–14). **There is no venue/source column on the bars tables themselves** — provenance lives only in `source_files`. If both Bybit and BitUnix bars were merged into `bars_3m`, they would be **indistinguishable** by row.

**Venue-fidelity consideration:** the corpus (Bybit, ends 05-16) and native capture (BitUnix, starts 05-15) overlap only ~1 day. Different venue → different price basis and (see Part 3) different volume units. They should **not** be spliced into one continuous 3m series without an explicit venue tag.

---

## Part 2 — Live native data: where it lands & its shape (prod, read-only SSH)

Probed prod `/home/azureuser/trading_corp/data/trading_corp.db` (~1.2 GB) via read-only `sqlite3 mode=ro` (82fda13). 22 tables.

### Native bar capture — `bitunix_bar_history` ✅ captured to DB

**Schema:** `ts_ms` (INTEGER PK, epoch **ms**), `timeframe` (TEXT), `open/high/low/close/volume` (REAL), `inserted_at` (TEXT). **Pure OHLCV — no signal/indicator columns.**

**Bars are persisted to the DB (not in-memory-only) and actively accumulating.** Total 18,029 rows across timeframes:

| TF | Rows | First | Latest |
|---|---|---|---|
| `3m` | 16,366 | 2026-05-15 | 2026-06-18 22:25 UTC |
| `1h` | 1,023 | 2026-05-07 | (current) |
| `4h` | 406 | 2026-04-12 | (current) |
| `1d` | 234 | 2025-10-27 | (current) |

3m completeness (last 5 full days): **exactly 480 bars/day, zero gaps** (Jun-14 → Jun-17 all 480; Jun-18 in-progress). So native 3m capture is **~34 days deep (since 2026-05-15)**, not 5 — the "~5 days" framing is about live *trades*, not bars.

### Trade data

- **`paper_trade_record`:** 187 total rows, all `division=bitunix_futures` / `symbol=BTC/USDT.P`; 16 in last 5 days; **4 with `execution_mode='live'`** (all Jun-18, after the Jun-17 exit-redesign re-arm). Columns: entry/stop/tp prices, `result`, `result_price`, `actual_pnl_dollars`, `actual_r_multiple`, `execution_mode`.
- **`audit_event`:** 1,220,322 rows total. Bitunix events last 5 days include **19 `live_order_placed`**, 6 `auto_book_server_side_close`, 37 `live_exit_order_placed` / 37 `live_exit_order_rejected`, 24 `entry_rejected_stale_bar`, 4 `bracket_placed`, 1 `orphan_broker_position_on_restart`.
- **`bitunix_signal_ledger`:** 1,109 signals received in last 5 days (top: `mc_a_red_diamond` 349, `mc_a_redx` 154, `mc_b_sell_circle` 94). **This is where native alerts/signals land** — the live equivalent of the corpus signal columns.

### Data-quality (descriptive only)

- **P2 result-sign bug — CONFIRMED ACTIVE.** Of 4 resolved live trades (all SHORT), **2 are genuine wins booked as `result='loss'`:**

  | order (short id) | entry | exit (result_price) | actual_pnl | actual_R | `result` | verdict |
  |---|---|---|---|---|---|---|
  | `e1758fc9` | 64595.1 | 64478.8 | **+0.0349** | **+0.291** | loss | **WIN mislabeled** |
  | `679c15e2` | 63867.3 | 64047.0 | −0.0539 | −0.428 | loss | correct |
  | `a919d1f5` | 63844.5 | 63987.0 | −0.0428 | −0.400 | loss | correct |
  | `7d1a78dc` | 63521.0 | 63094.97 | **+0.2982** | **+0.863** | loss | **WIN mislabeled** |

  `actual_pnl_dollars` / `actual_r_multiple` carry the **correct** sign; the categorical `result` label is inverted for favorable-move shorts.
- **Booked vs placed discrepancy:** 19 `live_order_placed` events but only 4 booked live `paper_trade_record` rows — consistent with the known orphan / managed-exit registration bug (db-lock misclassifying filled entries).
- **Paper sentinel:** 7 Jun-17 paper rows carry `result_price=100.0` (sentinel, not a real BTC price) — `result_price` untrustworthy for those rows; PnL columns appear independently computed.

### Does ~5 days fill the gap?

**For the bar feed: yes, mechanically — native 3m OHLCV is flowing and queryable.** **For the original "native high-vol 3m" gap the redeem-cap test was waiting on: no.** June is low-vol (per prior corpus analysis); a low-vol native window does not provide the ≥1 high-vol ATR-regime rotation the §4 run needs. The native data unblocks the *pipe*, not the *regime*.

---

## Part 3 — Scope: feed native data into the backtest DB (propose, don't build)

### Engine bar-source contract (what native data must satisfy)

The redeem-cap path (`scripts/backtest_bitunix_confluence.py`, `--bar-source bybit_hybrid --alert-source prod_cache --gate pa_validation --redeem-arms`) reads from the DB **only**:

```sql
SELECT ts, open, high, low, close, volume FROM bars_3m   -- (and bars_15m for the 15m shim)
```

- **ATR-14 is recomputed on the fly** from H/L/C (`_atr14_at` / `LiveBarCache.get_atr()`) — no stored `atr` column required.
- **Signals are NOT read from the bars on this path.** Alerts arrive via `--prod-alerts-cache` JSON (`--alert-source prod_cache`) or are synthesized from DB signal columns only on the separate `--alert-source synth` path (`synth_ledger.py`, which reads 28 signal columns).
- Caveats (pre-existing): run from a worktree with **absolute** `--bybit-db`/cache paths (worktree `data/` is gitignored/empty); `--gate five_factor` is unavailable (`bitunix_confluence_gate` absent from git — guarded but broken); only `--gate pa_validation` works.

**Implication:** to run the redeem-cap backtest on real BitUnix data, native bars need **only the 6 OHLCV columns** — the signal-column problem (below) applies *only* to the `synth` alert path, which the redeem-cap arm does not use.

### The two feeds required (this is the actual ETL)

A native backtest run needs **two** native inputs, both of which exist on prod:

1. **Bars** → `bitunix_bar_history` (3m + 15m) → ingest into `btc_scalping.db` as venue-tagged native bars.
2. **Alerts** → `bitunix_signal_ledger` → export to the `--prod-alerts-cache` JSON shape the engine consumes.

Booked trade `result`s are **not** an input — the engine re-simulates outcomes from the bar walk. (So the P2 result-sign bug does **not** affect backtest inputs; it matters only if you later validate the backtest against booked live results.)

### Proposed ingest (mirror `scripts/ingest_tv_export.py`)

`ingest_tv_export.py` is the template: CSV → `bars_<tf>` with `ts INTEGER PRIMARY KEY, datetime_utc TEXT` + REAL columns, **`INSERT ... ON CONFLICT(ts) DO UPDATE`** (idempotent), SHA256 file-dedup in `source_files`, no venue tag.

Proposed native ingest:
- **Source:** read `bitunix_bar_history` (prod, read-only) for `timeframe IN ('3m','15m')`, or backfill historical windows via the existing `scripts/fetch_bitunix_5m_history.py` (supports `--interval`; `ingest_bitunix_1m_to_db.py` already exists for 1m).
- **Target:** **do NOT append into the existing `bars_3m`** (Bybit). Either (a) a **separate `bars_3m_bitunix` table** (cleanest — preserves the Bybit corpus untouched, no cross-venue contamination), or (b) add a **`venue` / `source` TEXT column** to the shared tables and tag every row. **Recommend (a)** for the high-vol arm so venue fidelity is auditable; (b) is acceptable if the engine's `--bybit-db` query is pointed at a venue-filtered view.
- **Convert** `ts_ms → ts` (÷1000) and synthesize `datetime_utc`; map OHLCV directly; **flag volume units** (BitUnix `baseVol` ≈ USDT notional vs Bybit TV volume in contracts/BTC — VWAP/volume-confirmation will drift if mixed; for a native-only run it is internally consistent).
- **Idempotent upsert** on `ts` and file-dedup, same as the TV ingest.

### Native signal gap (affects only the `synth` alert path)

Native BitUnix klines are **pure OHLCV**. Market Cypher / Lord Otter / CVD-flip / divergence signals are **TradingView Pine Script** outputs — they arrive live via webhook (→ `bitunix_signal_ledger`) and are **not reproducible from native OHLCV**:
- *Partly* reconstructable (EMA-derived): `ribbon_*_cross`, `long_ema_signal`, `yellow_cross`.
- *Not* reconstructable: `cvd_flip_*` (needs bid/ask volume split BitUnix doesn't provide), all divergences, Cypher A/B panel circles/diamonds (proprietary).

So: **live native windows can run end-to-end** (bars from `bitunix_bar_history` + alerts from `bitunix_signal_ledger` → prod-cache JSON). **Historical high-vol native windows cannot** run the synth path (no signal data) and have no webhook alert history → the high-vol arm cannot be backfilled from native bars alone.

### What the redeem-cap backtest needs to finally run — and the honest window

- **To run on native data at all:** native bars ingest (above) + a `bitunix_signal_ledger → prod-cache JSON` exporter. Both are small ETL jobs over data that already exists; **no engine change** needed for the OHLCV side.
- **Is ~5 days enough?** For a *smoke/plumbing* run on native data: the ~34 days of native 3m bars + live signal ledger are **enough to wire and dry-run the native path now**. For a **§4-grade verdict**: **no** — the requirement is ≥1 high-vol 3m ATR-regime rotation, and the native window is low-vol June. **State plainly: this does not yet constitute a regime-diverse sample; do not draw an edge conclusion.**
- **Filling the high-vol gap:** only two real paths — (i) **wait** for a live high-vol regime to accumulate natively (bars + alerts together), or (ii) accept the original constraint that high-vol 3m *with signals* does not exist at any venue (Bybit corpus is high-vol only at 15m/30m). Backfilling native high-vol **bars** via the kline API does **not** help alone, because the matching **alerts** don't exist for historical periods.

### Data-quality prerequisites

1. **P2 result-sign bug:** must be fixed (or `result` derived from `actual_pnl_dollars`/`actual_r_multiple`) **before any comparison of backtest output to booked live results**. It does **not** block the backtest itself (inputs are bars+alerts, not booked results).
2. **Orphan / managed-exit booking gap** (19 placed vs 4 booked): live booked outcomes are incomplete — another reason booked live results are not yet a trustworthy validation target.
3. **Volume-unit normalization** between BitUnix `baseVol` and Bybit contract volume if a mixed corpus is ever used.
4. **Venue tagging** (separate table or `venue` column) so Bybit and native bars never silently merge.

---

## Forks / decisions for the operator (no action taken)

- **FORK 1 — high-vol gap:** native live data unblocks the native *pipe* but is low-vol; the high-vol 3m arm stays blocked. Wait for a live high-vol regime, or run the redeem-cap verdict on the (modest-vol) data available and label it as such? *(Recommend: build the native pipe now; run smoke only; defer the §4 verdict until a high-vol native window exists.)*
- **FORK 2 — ingest target:** separate `bars_3m_bitunix` table (recommended) vs venue-tagged shared `bars_3m`.
- **ANOMALY (surfaced, not acted on):** P2 result-sign bug active on prod (2/4 live wins booked as losses); booked-vs-placed gap (19 vs 4). Both are known issues — noted here as data-quality context for backtest validation, owned by the bitunix exit/booking track.

**No code written, no DB modified, no deploy, no prod write. Report only.**
