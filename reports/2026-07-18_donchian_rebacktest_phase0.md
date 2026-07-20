# Coinbase BTC Donchian — Re-backtest, PHASE 0 (Recover Prior Work) + Data Readiness

**Date:** 2026-07-18 · **Scope:** read-only recovery; no backtests run yet (Phases 1–5 gated on go-ahead).

## Headline

The deployed `20/168/6` Donchian was **in-sample-flattered**. Its headline **+25.89% alpha vs HODL is a full-sample (in-sample) number**, and the only out-of-sample test was **one 6-month fold in a bear market** — a window where *any* exit-to-cash rule beats buy-and-hold almost by construction, and which **overlaps the same data as the headline**. That is a lead, not validated OOS edge. The 4-year Binance re-validation is warranted, and the data is present and clean.

---

## 1. What was actually validated (verbatim recovery)

**Deployed / tested parameter set** (`config/strategies.yaml:834–852`; `deploy_log.md` 2026-05-09):
- `entry_lookback: 20` · `exit_lookback: 6` · `trend_filter_lookback: 168` (SMA) · `granularity_seconds: 21600` (6h)

**How those params were chosen** — grid sweep in `scripts/walkforward_donchian.py:48–52`, select **top-N by *training* return**, re-run on test half:
```
entry_lookback: [12, 20, 24, 30, 40, 50, 55, 75, 100, 150]
exit_lookback:  [6, 10, 12, 15, 20, 25, 30, 50]         (constraint: exit < entry)
trend_filter:   [None, 168, 336, 720]                    (None / 1w / 2w / 30d SMA)
```
`20/168/6` is a grid point; per the deploy log it was among the "8/10 top-train configs [that] beat HODL OOS."

**Data source:** Coinbase Exchange **public REST** — `api.exchange.coinbase.com/products/BTC-USD/candles`, **BTC-USD spot, 6h** candles (`scripts/backtest_donchian.py:47`). Fills simulated at **next-bar open, one-bar latency** (`backtest_donchian.py:182–218`).

**Headline result vs HODL** (`config/strategies.yaml:822–827`; `deploy_log.md` 2026-05-09):

| Metric | Value | Window / basis |
|---|---|---|
| Strategy return | **+56.30%** | "24mo full corpus" |
| HODL benchmark | **+30.42%** | same 24mo |
| **Alpha vs HODL** | **+25.89%** | **full-sample (in-sample)** |
| Round-trips | 35 | 24mo |
| Win rate | 49% | 24mo |
| Max drawdown | 16.49% | 24mo |
| Time in BTC | ~25% | 24mo |
| Walk-forward | 8/10 top-train configs beat HODL OOS; median test α **+12.86%**, best **+27.21%** | 6mo, single split |

**Walk-forward vs full-sample — BOTH exist, and this is the crux:**
- The **headline +25.89% is FULL-SAMPLE** (the chosen config run over the entire 24mo).
- The only **out-of-sample** evidence was a **single split** on a **6-month corpus, "Nov 2025 → May 2026, BTC −26.85%"** (verbatim from `walkforward_donchian.py:8–12`).

**Fees/slippage in the original:** **none modeled.** `run_donchian_backtest` applies no fee or slippage at any state change. Consistent with "zero Coinbase fees," but it also omits slippage/spread — which you now want added.

**Provenance note:** the deployed Donchian came from a **dedicated research effort** (`scripts/{backtest,walkforward}_donchian.py`, Phase-1 commits `072a484`/`0eb7692`/`fe1cee8`/`f9277e9`, 2026-05-08), **not** the "Explore my BTC backtest data" `/goal` (whose scratch in `Goals/scratch/` produced the *ribbon/wavetrend V6* family in `reports/backtest_results.md` — Donchian is not among those survivors). If you remember it as a "/goal search," it was a manual grid-select via that walk-forward script, not the /goal scratch.

---

## 2. Was it in-sample-optimized? — YES

| Problem | Detail |
|---|---|
| Headline is in-sample | +25.89% alpha = full-sample run of a config that was itself grid-selected. Not an OOS number. |
| OOS was one bear fold | The single 6mo walk-forward window fell **−26.85%**. A 100%-in/out trend-follower that exits to cash **beats HODL near-tautologically** in a down market — "beats HODL" ≈ "spent time in cash," which is not evidence of *timing* alpha. |
| OOS not independent of headline | The 6mo window (Nov 2025 → May 2026) is the **tail of the ~24mo window** (inferred ~2024-05 → 2026-05; exact 24mo start not recorded in committed artifacts). The "OOS" test is a **subset** of the "in-sample" data. |
| No parameter stability | One split = one fold. No evidence the winning params are stable across time — the thing that actually distinguishes "found" from "fitted." |
| Regime-conditional metric | For a 100%-in/out follower, "alpha vs HODL" is dominated by regime (bear → beats by sitting out; bull → often *lags* via cash-drag + whipsaw; chop → whipsaws). Prior evidence sampled one net-bull (24mo, in-sample) + one bear (6mo, OOS) — never a clean multi-regime OOS. |

**Verdict:** the deployed edge is **unproven out-of-sample**. This is exactly the gap the 4-year re-validation closes.

---

## 3. Data readiness — the Binance 4Y corpus

**Location:** `C:\Users\AA Incorporado\Desktop\binance_corpus\{1m,3m,15m,1h,4h,1d}\BTCUSDT-<tf>-YYYY-MM.csv` (monthly kline CSVs + `_fetch*.py` loaders + `INTEGRITY_REPORT.txt`).

| Property | Value |
|---|---|
| Source | **Binance USD-M FUTURES (perpetual)** BTCUSDT — `data.binance.vision/.../futures/um/...` |
| Window | **2022-07 → 2026-06 (48 months = 4 years)**, perpetual only, no roll-stitch |
| Native timeframes | 1m, 3m, 15m, 1h, 4h, 1d (**no native 6h**) |
| Integrity | checksum **PASS 48/48** every interval; **0 missing months, 0 dup open-times, 0 missing bars** (largest gap 1 bar) |
| 1h rows | 35,064 (2022-07-01 00:00 → 2026-06-30 23:00 UTC) |

**6h derivation (for Phase 1):** no native 6h → resample **1h → 6h** (bucket at 00/06/12/18 UTC: open=first, high=max, low=min, close=last, vol=sum). Exact and gap-free because 6h = 6×1h and 1h has zero missing bars. **Matches the prod scheduler's 00/06/12/18 UTC boundaries.**

**⚠ Two instrument/venue mismatches to carry into Phase 1:**
1. **Venue:** Binance vs Coinbase.
2. **Instrument:** Binance USD-M **perpetual futures (USDT-quoted, funding-bearing)** vs the deployed Coinbase **spot BTC-USD**.

For 6h Donchian these track closely, but Donchian **triggers on extremes** (20-bar high / 6-bar low), and **perp liquidation wicks can print more extreme than Coinbase spot** — so the venue can disagree exactly where it matters. Phase 1's cross-venue extreme-agreement check is the right guard; I'd also pull Coinbase BTC-USD 6h for the overlap to quantify it.

---

## 4. Decisions I need before running Phases 1–5

1. **Price proxy.** Run PRIMARY on the clean Binance-perp 4Y corpus, and in Phase 1 pull Coinbase BTC-USD 6h for the overlap to measure signal disagreement (re-run headline on Coinbase spot if disagreement is material)? *(My recommendation.)* Or do you want Coinbase spot / Binance *spot* as primary?
2. **Slippage/spread model** (fees = 0). Proposed: **3 bps/side** applied at each state change, with a sensitivity band **{0, 2, 5, 10} bps/side**. Confirm or set your number.
3. **Selection objective** for the grid + walk-forward. Proposed: rank by **Calmar** (or alpha-vs-HODL with a drawdown penalty) rather than raw return, to avoid selecting return spikes. Confirm.
4. **Phase 5 spec is truncated** — your message cut off at *"Report each with the full"*. Please paste the rest before I run variants.
5. **Go-ahead** to execute Phases 1–4 locally in Python (reusing `trading_corp.agents.strategies.donchian_btc.evaluate_donchian` for exactness — research only, no prod touch).

---

*Phase 0 only. No backtests were run and nothing was modified. Sources: `config/strategies.yaml`, `runbooks/deploy_log.md`, `scripts/{backtest,walkforward}_donchian.py`, `Desktop/binance_corpus/INTEGRITY_REPORT.txt`.*
