# Backtest-infra inventory — before building the PA-redeem-cap backtest

**Date:** 2026-06-14 · **Session:** operator-supervised, **read-only inventory** (no code run, no
backtest run, no §4 change). Agent-driven read-only SSH not needed this session; local DBs inspected
**read-only** (`sqlite3 ... mode=ro`, SELECT/PRAGMA only — probe `introspect_dbs.py`, committed).
**Branch:** `bitunix-backtest-infra-inventory-2026-06-14` (bitunix, unmerged).
**Feeds:** the PA-redeem-cap §4 backtest scoped in
`reports/2026-06-14_bitunix_entry_timing_analysis.md` / BACKLOG P1 (`current vs cap@1bar vs
no-redeem`, fire-bar-priced, net-of-cost, over ≥1 ATR-regime rotation).

> **BOTTOM LINE: EXTEND, don't reuse-as-is or build-new.** The existing bitunix backtest has the
> corpus + gate machinery but models **none** of the three things the redeem-cap test needs (the
> redeem loop, the v2 3-leg trade_plan, late/fire-bar entry). The redeem loop must be **built**; the
> v2 trade-plan + entry-timing walk can be **grafted from this session's `etharness.py`/`fgharness.py`**.
> **Regime caveat (the runnability gate): a strong high-vol regime exists in the corpus only at
> 15m/30m (Feb-2026). At 3m — the test's timeframe — the corpus is a modest ~1.9× gradient
> (Mar→May 2026) that ends ~3 weeks before the live low-vol window.** The test is runnable on that
> modest 3m spread but is NOT a textbook rotation; a stronger test needs additional high-vol 3m ingest.

---

## 1. The two backtest databases

### A. `data/btc_scalping.db` (29.7 MB) — the dedicated backtest corpus
Indicator-**enriched** TradingView/Bybit kline export (90+ columns per bar: `red_diamond`,
`blood_diamond`, `cvd_flip_*`, `sommi_*`, `wt_*_divergence`, `otter_buy/sell`, `atr`, `vwap`, …) — i.e.
the bars **carry the signal columns**, which is what `backtest_bitunix_confluence.py --bar-source
synth/bybit_hybrid` consumes. Tables + spans + a volatility proxy (median bar `(H−L)/C`):

| table | span | rows | vol (median bar-range %) |
|---|---|---:|---|
| `bars_3m` | **2026-03-30 → 05-16** | 22,635 | Mar **0.148** · Apr 0.097 · May 0.077 |
| `bars_15m` | 2025-12-04 → 2026-05-16 | 15,571 | **Feb-2026 0.402 (p90 0.942)** · May 0.194 |
| `bars_30m` | **2025-04-22 → 2026-05-16** | 18,653 | **Feb-2026 0.559 (p90 1.364)** · Nov-25 0.469 · Sep-25 0.235 |
| `bars_1m` | 2026-04-30 → 05-16 | 24,442 | ~0.036–0.042 (low) |
| `source_files` | — | 14 | provenance (filename/sha256/tf/ts_min/max) |

**Regime read:** the corpus clearly contains a **high-vol regime (Feb-2026, ~2–2.4× the low-vol
baseline) — but only at 15m/30m.** The 3m table only spans **Mar–May 2026**; its highest-vol month
(March, 0.148% median) is ~1.9× the May low (0.077%) — approaches the board memo's ~2× high/low
definition but is a continuous *decline*, not two distinct regimes, and ends 2026-05-16.

### B. `data/trading_corp.db` (local copy, 652 MB) — STALE; the live data is on prod
Local copy is an **old snapshot**: `audit_event` 2026-04-26→05-25, `paper_trade_record` **empty**,
**no `bitunix_bar_history` table**. The live 3m bars (`bitunix_bar_history`, ~2026-06-08→06-14,
low-vol) + the 42 fires / 88 declines used by the fee + entry-timing analyses live on the **prod**
`/home/azureuser/trading_corp/data/trading_corp.db` (read via SSH in those sessions). So the
**low-vol live 3m window and the corpus 3m don't overlap** (gap 05-16 → 06-08) and come from
different sources (Bitunix-native vs Bybit/TV export).

*(`data/no_such.db` = a missing-DB test fixture; `data_localtest/smoke.db`, `tmp/*.db` = sandboxes —
not backtest corpora.)*

---

## 2. Prior backtest routines

| artifact | what it is | redeem? | v2 trade_plan? | entry-timing? |
|---|---|:--:|:--:|:--:|
| `trading_corp/agents/backtester.py` | the §4 **deploy gate** — a Pass/Fail **registry** (`validate_strategy`); only `coinbase_btc_donchian` registered; **bitunix not registered** (human-process gate). NOT a simulator. | — | — | — |
| `scripts/backtest_bitunix_confluence.py` | the bitunix **simulator**: score (`evaluate_confluence_futures`) + **PA / five-factor gate**, once per alert; `--bar-source coinbase\|bybit_hybrid`; `--start/--end`, `--config-path`. | **NO** (PA-reject → `continue`/drop; grep `redeem`=0) | **NO** — single-TP **fixed-ATR** model (`open_trade`:283, `atr=entry×ATR_FALLBACK_PCT`); no fee gate | **NO** — enters at `ctx.current_price` (signal bar only) |
| `scripts/walkforward_donchian.py`, `backtest_btc_accumulator.py`, `backtest_donchian.py`, `backtest_kalshi_*`, `backtest_polymarket_arbitrage.py`, `backtest_rounding_flip.py` | other-strategy backtests (Donchian is the validated one) | — | — | — |
| **`etharness.py` / `fgharness.py`** (this session; branches `bitunix-entry-timing-…` / `bitunix-fee-gate-…`) | replay harnesses using the **real `build_trade_plan`** (v2 3-leg + fee gate) + bar walk (SL-first tie, BE/TP1 ratchet); **`etharness` models early-vs-late (signal vs fire-bar) entry + net-of-cost** | reads recorded `bars_waited` | **YES** | **YES** | 

**Key gap:** the corpus backtest (`backtest_bitunix_confluence.py`) has the from-scratch
signal→gate→bar machinery but a *simplified* trade model and **no redeem/entry-timing**; the `et/fg`
harnesses have the *real* trade economics + entry-timing but are **recorded-fire-driven** (a single
prod window), not corpus-driven — they can't re-derive the redeem timeline over historical bars.

---

## 3. Fit to the PA-redeem-cap test, and recommendation

The test = `current redeem` vs `cap@1bar` vs `no-redeem`, v2 economics, **late entry priced at the
fire bar**, net-of-cost, over a regime rotation. Mapped to existing infra:

| need | status | source |
|---|---|---|
| corpus + signal generation over a rotation | **REUSE (partial)** | `backtest_bitunix_confluence.py` loader + `btc_scalping.db` (synth alerts) / prod alerts | 
| score + PA gate re-derived per bar | **REUSE** | `evaluate_confluence_futures` + `evaluate_pa_validation` (already wired) |
| **PA-redeem loop with a configurable cap** | **BUILD (missing entirely)** | new: cache PA-rejects, re-eval per subsequent bar up to cap N, fire at the pass-bar |
| v2 3-leg `build_trade_plan` + fee gate economics | **GRAFT** | `etharness.py`/`fgharness.py` (file-load `trade_plan.py`) — replaces the single-TP `open_trade` |
| late/fire-bar entry pricing + net-of-cost walk | **GRAFT** | `etharness.py` early-vs-late walk + taker/maker fee deduction |

**Recommendation: EXTEND `backtest_bitunix_confluence.py`** — graft the v2 `build_trade_plan` trade
model + the `etharness` late-entry walk (both already written this session) onto its corpus/gate
loader, and **build the one genuinely new piece: the PA-redeem loop with a `--redeem-cap` knob**
(0=no-redeem, 1=cap@1bar, ∞=current). Do **not** build a new engine (the corpus/gate loader is
reusable) and do **not** try to reuse the `et/fg` harnesses as-is (they can't re-derive the redeem
timeline from raw bars).

**Runnability / data caveat (surface before committing to the run):** the §4 requirement is "≥1
ATR-regime rotation." At **3m** (the test's timeframe) the corpus offers only a **modest ~1.9×
gradient (Mar→May 2026)** and ends 3 weeks before the live low-vol window; the **strong high-vol
regime (Feb-2026) is 15m/30m only**. So the test is **runnable now** on the Mar-vs-May/June 3m
spread, but for a defensible high-vol-3m arm it would be worth **ingesting a high-vol 3m period**
(re-export Feb-2026 3m to `btc_scalping.db`, or pull a known high-vol window from the Bitunix/Bybit
API) — a small data task, not new backtest tooling. Flag this in the §4 scoping; it does not block
starting the build, only the strength of the regime-robustness claim.

---

## Disclosure (82fda13)
No agent SSH this session (local DBs only). Local backtest DBs inspected **read-only**
(`sqlite3 mode=ro`, SELECT/PRAGMA only via `introspect_dbs.py`); no writes, no backtest executed, no
code/§4 change. Inventory only.
