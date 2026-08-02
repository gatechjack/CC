# kalshi_crypto_v2 — S3 Data Backfill Coverage Report

_Generated 2026-08-02 15:36 UTC. Period: 2026-05-25 00:00 -> present. Assets: BTC/ETH/SOL/XRP. Lab DB only (prod untouched)._

**Gap rule:** if any continuous-cadence source's gaps exceed 1% of windows in the period, STOP before S4 and the operator decides (proceed / patch / re-pull).

## 1-minute spot bars (continuous grid)

| source | asset | rows | span | missing | gap% | verdict |
|---|---|---|---|---|---|---|
| binance | BTC | 99378 | 2026-05-25 00:00..2026-08-02 00:17 | 0 | 0.000% | OK |
| binance | ETH | 99378 | 2026-05-25 00:00..2026-08-02 00:17 | 0 | 0.000% | OK |
| binance | SOL | 99378 | 2026-05-25 00:00..2026-08-02 00:17 | 0 | 0.000% | OK |
| binance | XRP | 99378 | 2026-05-25 00:00..2026-08-02 00:17 | 0 | 0.000% | OK |
| coinbase | BTC | 99378 | 2026-05-25 00:00..2026-08-02 00:36 | 19 | 0.019% | OK |
| coinbase | ETH | 99375 | 2026-05-25 00:00..2026-08-02 00:36 | 22 | 0.022% | OK |
| coinbase | SOL | 99372 | 2026-05-25 00:00..2026-08-02 00:36 | 25 | 0.025% | OK |
| coinbase | XRP | 99352 | 2026-05-25 00:00..2026-08-02 00:36 | 45 | 0.045% | OK |

## Coinalyze flow/positioning (per interval — retention-limited)

Coinalyze retains fine-grained history only for a recent tail; **only 1-hour reaches the full period.** CVD is derived at analysis time (sell = vol - buy_vol).

| interval | asset | price pts | span | gap% | verdict |
|---|---|---|---|---|---|
| 1min | BTC | 1517 | 2026-07-31 23:33..2026-08-02 00:49 | 98.5% | **EXCEEDS 1%** |
| 1min | ETH | 1516 | 2026-07-31 23:34..2026-08-02 00:49 | 98.5% | **EXCEEDS 1%** |
| 1min | SOL | 1516 | 2026-07-31 23:34..2026-08-02 00:49 | 98.5% | **EXCEEDS 1%** |
| 1min | XRP | 1516 | 2026-07-31 23:34..2026-08-02 00:49 | 98.5% | **EXCEEDS 1%** |
| 5min | BTC | 2004 | 2026-07-26 01:50..2026-08-02 00:45 | 89.9% | **EXCEEDS 1%** |
| 5min | ETH | 2004 | 2026-07-26 01:50..2026-08-02 00:45 | 89.9% | **EXCEEDS 1%** |
| 5min | SOL | 2004 | 2026-07-26 01:50..2026-08-02 00:45 | 89.9% | **EXCEEDS 1%** |
| 5min | XRP | 2004 | 2026-07-26 01:50..2026-08-02 00:45 | 89.9% | **EXCEEDS 1%** |
| 15min | BTC | 2002 | 2026-07-12 04:30..2026-08-02 00:45 | 69.8% | **EXCEEDS 1%** |
| 15min | ETH | 2002 | 2026-07-12 04:30..2026-08-02 00:45 | 69.8% | **EXCEEDS 1%** |
| 15min | SOL | 2002 | 2026-07-12 04:30..2026-08-02 00:45 | 69.8% | **EXCEEDS 1%** |
| 15min | XRP | 2002 | 2026-07-12 04:30..2026-08-02 00:45 | 69.8% | **EXCEEDS 1%** |
| 1hour | BTC | 1657 | 2026-05-25 00:00..2026-08-02 00:00 | 0.0% | OK |
| 1hour | ETH | 1657 | 2026-05-25 00:00..2026-08-02 00:00 | 0.0% | OK |
| 1hour | SOL | 1657 | 2026-05-25 00:00..2026-08-02 00:00 | 0.0% | OK |
| 1hour | XRP | 1657 | 2026-05-25 00:00..2026-08-02 00:00 | 0.0% | OK |

## Kalshi markets + 1m candles

| series | kind | markets | pulled | candles | window cov | status |
|---|---|---|---|---|---|---|
| KXBTC15M | 15m | 6526 | 6526 | 104273 | 97.6% | DONE |
| KXETH15M | 15m | 6526 | 6526 | 104266 | 97.6% | DONE |
| KXSOL15M | 15m | 6526 | 6526 | 104191 | 97.6% | DONE |
| KXXRP15M | 15m | 6526 | 6526 | 104223 | 97.6% | DONE |

**Ladder snapshots (KXBTC/KXETH/KXSOLE/KXXRP, daily, window-open):** full 1m ladder (674k+ mkts, >24h) intentionally OFF; instead all strikes at window open for a daily event sample (S5 Breeden-Litzenberger source).

| asset | events | strike-snaps |
|---|---|---|
| BTC | 70 | 13160 |
| ETH | 70 | 8752 |
| SOL | 70 | 7015 |
| XRP | 70 | 5171 |

## Hand-verify (one row per source vs origin)

| source | probe | result |
|---|---|---|
| Binance | BTC 2026-07-01 12:00 | stored == origin (o/h/l/c/v) exact |
| Coinbase | BTC 2026-07-01 12:00 | stored == origin exact; vs Binance +15bps (sane spread) |
| Coinalyze | BTC 1h 2026-07-01 12:00 | price_c/buy_vol/vol match origin |
| Kalshi | one 15m candle | stored == origin; settle=RTI hand-verified to the cent (S1 report) — 15m pull DONE (26104/26104 markets pulled) |

## GAP-RULE GATE VERDICT

- **Binance 1m:** all 4 assets 0 gaps (0.000%). PASS.
- **Coinbase 1m:** all 4 assets 0.019-0.045% (thin no-trade minutes). PASS.
- **Coinalyze 1hour:** full period, 0 gaps. PASS at 1h only.
- **Coinalyze 1min/5min/15min:** 98.5% / 89.9% / 69.8% gaps — **EXCEED 1% by design (API retention limit, not a flaky pull).** Fine-grained LEAD flow features are recent-tail only.
- **Kalshi 15m candles:** DONE (26104/26104 markets pulled).
- **Kalshi ladders:** DONE (daily window-open sample, 280 events).

**=> S3 backfill COMPLETE.** Both S3-time operator decisions were resolved: (1) Coinalyze flow granularity — 1-hour full-period is the v1 flow source, with fine-grained intervals retained recent-tail only (Riders A/B); (2) Kalshi ladder scope — daily window-open snapshot sample (S5 Breeden-Litzenberger source), full 1m ladder intentionally off. S4 has proceeded. Bar/regime/cross-asset features have FULL history (Binance/Coinbase) throughout.
