# Native BitUnix ETL → btc_scalping.db + redeem-cap PLUMBING smoke-run

**Date:** 2026-06-18
**Branch:** `bitunix-native-etl-2026-06-18` (worktree off the scope branch `97a95ff`)
**Scope:** Build the 2 ETL jobs that feed native BitUnix data into the backtest corpus, then a smoke-run of the redeem-cap engine (`74a23b4`) on that native data. Per the approved scope `reports/2026-06-18_btc_scalping_db_inventory_and_native_data_scope.md`.
**§4 status:** BUILD + TEST + SMOKE-RUN only. Read of prod = read-only SSH (`mode=ro`, 82fda13). ETL writes only the LOCAL backtest corpus. NO prod write, NO deploy, NO engine modification.

> 🚦 **PLUMBING-ONLY — NOT A §4 REDEEM-CAP VERDICT.** The native data here is **low-vol June 2026**, the wrong regime for the redeem-cap question. The smoke-run proves the native data flows through the engine end-to-end; it is **not** an edge/profitability conclusion. The real verdict waits for a **live high-vol native window** (see §5). Do not cite the arm numbers as the redeem-cap decision.

---

## 1. What was built

| Artifact | Path | Role |
|---|---|---|
| Bar ingester | `scripts/ingest_bitunix_bars.py` | native OHLCV CSV → venue-tagged table; ON CONFLICT(ts) upsert + sha256 file-dedup (mirrors `ingest_tv_export.py`) |
| Alert exporter | `scripts/export_bitunix_alerts.py` | signal-ledger CSV → engine prod-cache alert JSON (tz-aware `ts`, factor-vocab `signal`) |
| Bar extract SQL | `scripts/native_etl/extract_bitunix_bars_3m.sql` | read-only prod dump (`ts_ms/1000`) |
| Ledger extract SQL | `scripts/native_etl/extract_bitunix_signal_ledger.sql` | read-only prod dump |
| Tests | `tests/test_native_etl.py` | 6 tests, all pass (§6) |

Local-only (gitignored `data/`, regenerable — not committed): the prod CSV extracts, the native-only smoke DB, the alert JSON, and the `[]` 5m placeholder.

---

## 2. What was ingested (native data)

Read-only prod extract (`bitunix_bar_history` / `bitunix_signal_ledger`, `mode=ro`):

| Feed | Rows | Range (UTC) |
|---|---|---|
| Native 3m bars → `bars_3m_bitunix` | **16,387** | 2026-05-15 05:30 → 2026-06-18 23:24 |
| Native alerts (signal ledger) | **7,434** (6,888 in smoke window) | 2026-05-11 17:54 → 2026-06-18 23:12 |

- **`bars_3m_bitunix`** now exists in the canonical local corpus `data/btc_scalping.db`, OHLCV-only, every row `venue='bitunix'`. (Native klines carry no signal columns — expected; the engine reads OHLCV from the DB and alerts from the JSON.)
- **Alert JSON**: 24 distinct signals, all in the `bitunix_futures` factor vocab (top: `mc_a_red_diamond` 2278, `mc_a_redx` 996, `mc_b_buy_circle` 694, `mc_a_bluetriangle` 563, `cvd_bear_flip` 466…). `ts` normalized to tz-aware `+00:00` (the engine's `datetime.fromisoformat` requires tz-aware or it TypeErrors).

---

## 3. Corpus safety — the Bybit corpus is FROZEN (verified)

The native ingest writes ONLY the new `bars_3m_bitunix` table. The frozen Bybit `bars_3m` is **byte-identical before and after** (fingerprint over the canonical corpus):

| | count | min_ts | max_ts | sum(close) | sum(volume) |
|---|---|---|---|---|---|
| `bars_3m` (Bybit) BEFORE | 22,635 | 1774828800 | 1778902920 | 1704868712.5 | 3083004.394 |
| `bars_3m` (Bybit) AFTER | 22,635 | 1774828800 | 1778902920 | 1704868712.5 | 3083004.394 |

`bars_15m` (Bybit) also unchanged (15,571). A pre-ingest backup exists: `data/btc_scalping.db.bak-pre-native-etl-2026-06-18`. Two enforced rails keep native and Bybit data separate:
1. **No venue mixing** — native goes into a *separate* table, never `bars_3m`.
2. **Safety rail in code** — `ingest_bitunix_bars.py` HARD-REFUSES writing any reserved Bybit table name (`bars_3m`/`bars_15m`/…) into a file named `btc_scalping.db` (the canonical corpus). The smoke DB (different filename) may use `bars_3m` so the *unmodified* engine can read it.

---

## 4. Smoke-run result — PLUMBING-ONLY (NOT a verdict)

Engine: `scripts/backtest_bitunix_confluence.py --bar-source bybit_hybrid --alert-source prod_cache --gate pa_validation --redeem-arms`, **unmodified**, against the native-only smoke DB (native `bars_3m` 16,387 + empty `bars_15m`) + the native alert JSON. Window 2026-05-15 → 2026-06-19. Exit 0.

The pipe works end-to-end: engine loaded 16,387 native 3m bars, 0 15m (empty table — no crash), 6,888 windowed native alerts, and ran all three arms producing fires/walks:

| arm | first-pass | redeem | dropped | plan-skip | walked | net-taker/fire | net-maker/fire | max_bw |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| no_redeem | 254 | 0 | 3205 | 176 | 78 | -0.4005 | -0.3006 | 0 |
| cap_1bar | 234 | 89 | 3104 | 233 | 90 | -0.4572 | -0.3572 | 1 |
| current (cap 30) | 207 | 267 | 2917 | 376 | 98 | -0.3725 | -0.2720 | 30 |

(Raw engine output: `reports/2026-06-18_native_smokerun_engine_output.md`.)

**Interpretation — NONE.** These numbers are low-vol-June plumbing output. The negative net-taker direction here is **noise on the wrong regime**, exactly the caveat from the 2026-06-14 engine validation; it does **not** overturn (or confirm) anything about redeem-cap. The only conclusion drawn here is: **the native data flows through the engine and produces a valid comparison** — plumbing proven.

---

## 5. High-vol readiness — how much more is needed

This native window does **not** satisfy the §4 redeem-cap requirement (≥1 high-vol 3m ATR-regime rotation). The accumulated data is low-vol June; the corpus's only high-vol regime (Feb-2026, ~2–2.4× low) exists at 15m/30m, never at 3m.

**The gap is regime-gated, not calendar-gated.** A defensible §4 run needs a *contiguous native window where both bars AND alerts coexist* across a sustained ATR expansion of roughly ≥2× the current low-vol baseline, with enough fires in that high-vol cohort to be meaningful (order ~100–200+ fires, vs the all-low-vol fires above). Two ways to get there:
1. **Wait for it to accumulate live.** The pipe now captures native bars (`bitunix_bar_history`) + native alerts (`bitunix_signal_ledger`) continuously, so the next live high-vol episode is captured automatically — re-run this ETL + smoke when one appears. Timing is market-dependent (cannot be predicted).
2. **Historical backfill does NOT solve it:** native high-vol *bars* could be fetched via the kline API, but the matching native *alerts* don't exist for past periods (TradingView signals are only captured live via webhook → ledger; not reproducible from OHLCV). So the high-vol arm cannot be manufactured from history.

**Recommendation:** treat the pipe as built and validated; defer the §4 verdict; re-run when a high-vol native window has accumulated.

---

## 6. Tests + data-quality note

- `tests/test_native_etl.py` — **6/6 pass**: venue-tagged table creation; ON CONFLICT(ts) upsert (changed bar updated, new bar inserted); file-level idempotence; corpus safety rail (reserved table refused); native-ingest-leaves-other-tables-untouched invariant; alert-JSON shape (tz-aware `ts`, `Z`→`+00:00`, verbatim signal names, sorted). All stdlib + temp DBs — the real corpus is never touched by the tests.
- **P2 result-sign bug does NOT affect this pipe.** The backtest re-simulates outcomes from the bar walk; it never reads prod booked `result`/PnL. The P2 bug (and the orphan booking gap) matter only if booked live results are later used to *validate* the backtest — out of scope here.

---

## 7. Reproduce / refresh (read-only prod → local)

```
# 1. read-only prod extract (mode=ro), -> local CSVs
Get-Content scripts/native_etl/extract_bitunix_bars_3m.sql -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|sqlite3 -csv -header 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'" | Set-Content data/native_extracts/bitunix_bars_3m.csv -Encoding ascii
Get-Content scripts/native_etl/extract_bitunix_signal_ledger.sql -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|sqlite3 -csv -header 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'" | Set-Content data/native_extracts/bitunix_signal_ledger.csv -Encoding ascii
# 2. durable native table (Bybit corpus untouched)
python scripts/ingest_bitunix_bars.py data/native_extracts/bitunix_bars_3m.csv --db <abs>/data/btc_scalping.db --table bars_3m_bitunix
# 3. native-only smoke DB + alerts + placeholder, then run the engine (see reports/2026-06-18_native_smokerun_engine_output.md)
```

---

## 8. Hard stops honored / NOT done
- bars_3m (Bybit corpus) **untouched** (verified); native in a separate venue-tagged table; **no venue mixing**.
- **No prod DB write** — prod read strictly `mode=ro`; ETL wrote only the local backtest corpus + local throwaway smoke DB.
- **No engine modification** — the unmodified engine reads native data via a filename-aliased smoke DB.
- **No §4 verdict** rendered on the low-vol data — smoke/plumbing only; verdict deferred.
- No git stash; no signed/live API; no deploy; no polymarket.
