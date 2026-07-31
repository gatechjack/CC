# OptiTrade — Venue / corpus comparison (pick the data deliberately)

Wide empirical search of the box for crypto-bar stores. Provenance proven from
data + ETL build docs, not filenames. All reads `mode=ro`.

## The one table (store × venue × coins × TFs × rows × range × size)

| Store (path) | Venue (PROVEN) | Coins | Timeframes | Rows | Date range (UTC) | Size | Native 4h/1d? |
|---|---|---|---|---|---|---|---|
| `Desktop\backtest_corpus\binance_perp_corpus.db` | **Binance USD-M perp** | BTC, ETH, SOL, XRP | 1m, 3m, 15m, 1h, 4h, 1d (**6**) | **11,962,668** | **2022-07-01 → 2026-06-30** | **1331 MB** | **YES** |
| `cc\data\btc_scalping.db` | Bybit perp (USDT.P) | BTC | 1m,3m,15m,30m,1h (+3m_bitunix) | ~0.36M | 1h 2024-08→2026-06-19; 15m 2025-11→; 3m 2026-03→ | 72 MB | no |
| `cc\data\eth_scalping.db` | Bybit perp (USDT.P) | ETH | 1m,3m,15m,30m,1h | ~0.35M | 1h 2024-01→2026-06-26; 15m 2025-11→; 3m 2026-05→ | 63 MB | no |
| `cc\data\sol_scalping.db` | Bybit perp (USDT.P) | SOL | 1m,3m,15m,30m,1h | ~0.35M | (same windows as ETH) | 63 MB | no |
| `cc\data\xrp_scalping.db` | Bybit perp (USDT.P) | XRP | 1m,3m,15m,30m,1h | ~0.35M | (same windows as ETH) | 63 MB | no |
| `Documents\coinbase_forensics\data\coinbase.db` | Coinbase (accounts) | — | none (account_v2, 50 rows) | 50 | n/a | 197 MB | — |
| `Desktop\ksarb_local.db` | Kalshi arb (audit log) | — | none (audit_event) | 37,050 | n/a | 18 MB | — |

No `.parquet` / `.csv.gz` corpora exist on the box. Downloads holds only old
TradingView strategy CSVs + the raw Bybit CSVs that fed the scalping DBs.

## Provenance proof — Binance corpus (from data, not filename)

1. **Source column uniform:** `source='binance_perp'` for all 11,962,668 rows.
2. **Cross-venue divergence vs Bybit** (proves independent feed, not a relabeled
   copy): at 16,398 matching BTC 1h timestamps only **0.56%** of closes are
   identical (mean rel diff 0.0105%); ETH 21,788 matching, **1.41%** identical
   (mean rel diff 0.0118%). A copy would be ~100% identical.
3. **ETL build doc** (`CORPUS_1M_LOAD_REPORT.txt`): "Binance USD-M PERP monthly
   klines, 2022-07..2026-06 (48 months) × 4 coins"; per-file **SHA256 PASS 48/48**
   per coin; INSERT OR IGNORE dedup; gaps=0; OHLC sanity clean.

Honest limit: OHLC alone can't uniquely fingerprint "Binance" vs another major
venue, but the corpus is demonstrably NOT the Bybit feed and its documented +
checksummed origin is Binance monthly klines.

## Binance corpus — per (symbol, TF) coverage (all 100% contiguous, 0 gaps)

Every (coin, TF) spans **2022-07-01 00:00 → 2026-06-30**, identical windows across
all four coins. Rows per coin:

| TF | rows/coin | TF | rows/coin |
|----|-----------|----|-----------|
| 1m | 2,103,840 | 1h | 35,064 |
| 3m | 701,280 | 4h | 8,766 |
| 15m | 140,256 | 1d | 1,461 |

Freshness: last bar 2026-06-30 23:59 UTC → **~31 days stale** (one monthly refresh
behind; consistent with the monthly-refresh backlog).

## Recommendation

Use **`binance_perp_corpus.db`** as the primary study corpus:
- Matches the spec venue (Binance), **native 6 TFs incl. 4h & 1d → resample fork
  is moot**, ~4-year history (robust IS/OOS with a deep ~14-month OOS window),
  100% contiguous, checksum-verified.
- The **Bybit** scalping DBs remain available as an optional cross-venue
  robustness check (arguably apt since the target division trades Bitunix, a perp
  venue like Bybit), but they are shorter and lack native 4h/1d.

## Impact on the study plan (if Binance corpus adopted)

- 20 cells = 4 coins × the 5 requested TFs (3m/15m/1h/4h/1d); 1m is present but not
  requested. No resampling.
- 70/30 split on ~4 years → OOS ≈ 14 months. Daily OOS ≈ 438 bars/coin, healthier
  trade counts than the Bybit option, though 1d cells may still hit n<30 (flagged).
- OOS ends 2026-06-30 (~31 days stale) — a historical study, not live.
