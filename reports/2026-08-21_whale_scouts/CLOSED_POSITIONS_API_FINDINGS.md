# Polymarket `/closed-positions` API — data-foundation characterization (2026-08-22)

READ-ONLY. Question: can Polymarket's positions API be the foundation for a per-whale, all-categories,
COMPLETE-record copy DB — i.e. does it solve the 5,000-row `/activity` truncation that broke the NBA/Fed scouts?
**Answer: YES.** This is the key artifact of the session.

## 1. Endpoints (all `data-api.polymarket.com`, public, no auth, HTTP 200; no rate-limiting at ~60 calls/wallet)
| Endpoint | Returns | Params |
|---|---|---|
| **`/closed-positions`** | resolved positions, **1 row/market** | `user`, `limit`(≤50 hard-cap), `offset` |
| `/positions` | current OPEN positions (mark-to-market) | `user` |
| `/activity` | raw trade+redeem feed (5,000-row truncation source) | `user`, `limit`, `offset` |
| `/trades` | per-market tape | `market=<cid>` |
| gamma `/markets?condition_ids=&closed=true` | resolution/winner + tags | — |
Client wraps these: `PolymarketDataAPIClient.fetch_closed_positions` (polymarket_data_api_client.py:452), `.fetch_positions`:439, `.fetch_market_resolutions`:477.

## 2. `/closed-positions` full field schema (real sample)
```json
{ "proxyWallet":"0x989b…", "asset":"<ERC1155 token = side held>", "conditionId":"0x655e…",
  "avgPrice":0.9866, "totalBought":1581137.59, "realizedPnl":21171.43, "curPrice":1,
  "title":"Will Zelenskyy wear a suit before July?", "slug":"…", "eventSlug":"…", "icon":"…",
  "outcome":"No", "outcomeIndex":1, "oppositeOutcome":"Yes", "oppositeAsset":"…",
  "endDate":"2025-06-30", "timestamp":1752021039 }
```
- **Present:** market id (conditionId/slug/eventSlug/title) · side held (outcome/outcomeIndex/asset) · avg entry price · USDC-in (totalBought) · **✅ realized P&L DIRECT (`realizedPnl`)** · settlement price (curPrice≥0.9=won) · **resolution timestamp** + market endDate.
- **Absent (gaps):** ❌ entry timestamp (resolution only) · ❌ per-entry granularity (scale-ins collapsed into avgPrice/totalBought) · ❌ explicit shares (=totalBought/avgPrice) · ❌ fee field · ❌ category tag (derive from eventSlug prefix or gamma join).

## 3. Completeness / cap — the make-or-break: SOLVED
| wallet | closed-positions | resolution dates | category spread |
|---|---|---|---|
| cigarettes (Fed n=0 under activity) | **≥3,050** (still full pages at offset 3000) | 2024-11-06 → **2026-08-21** | nba 533, fifwc 512, atp 326, cbb 225, wta 153… |
| S-Works (NBA n=0 under activity) | **≥3,050** (still full pages) | 2024-08-17 → **2026-08-21** | nba **820**, nhl 314, mlb 268, cs2 193, ufc 70… |
| scanner | 35 (complete) | 2024-09-18 → 2026-07-29 | fed 18… |
| Kh4mz4t | 299 (complete) | 2025-09-06 → 2026-08-19 | ufc 265… |
Wallets that returned **n=0 under the activity method** return **complete category histories** here, each row with **direct realizedPnl**. The codebase-documented "~1,500 cap" is **stale** — empirically ≥3,050 and the ceiling was never hit. **Freshness:** same-day (dates reach 2026-08-21).

## 4-6. Granularity / coverage / freshness
- **Granularity:** per-market-position (1 row/resolved market; scale-ins collapsed → avgPrice+totalBought+realizedPnl). Per-entry detail lives in `/activity`.
- **Category coverage:** UNIFORM — all categories in one per-wallet call (nba/mlb/nhl/nfl/ufc/fed/tennis/soccer/esports/politics). Category via eventSlug prefix ~85-90% clean; ambiguous political slugs need gamma-tag join.
- **Open positions:** `/positions` returns size/avgPrice/initialValue/currentValue/pnl for live mark. Both closed (records) + open (live) covered.

## 7. Fit-for-vision + A-vs-B
- **Solves truncation? YES, strongly** — complete per-market realized P&L, all categories, ≥3,050 depth, direct P&L (no reconstruction), fresh.
- **A vs B: B (`/closed-positions`) is the record-keeping backbone; DROP A (`/trades` reconstruction).** A needs market-discovery first + buy/sell/redeem stitching + no direct P&L — strictly worse.
- **Architecture: `/closed-positions` for the historical record DB + `/activity` for live copy-signal detection.** (Entry-timing is NOT a requirement — the old ~15-min lag was a Kalshi-native-path/Apify artifact, not a Poly concern.)

### DB schema sketch (fed from `/closed-positions`)
- `whale(wallet PK, user_name, first_seen, last_refresh)`
- `whale_closed_position(wallet, condition_id, slug, event_slug, category⟵derived, outcome, outcome_index, avg_price, total_bought, realized_pnl, cur_price, won⟵cur≥0.9, end_date, resolved_ts)` — **PK (wallet, condition_id)**; core table.
- `whale_category_stats(wallet, category, n_resolved, wins, net_realized_pnl, roi, avg_bet, last_ts)` — materialized rollup = the per-whale-per-category scoreboard the scouts couldn't build.
- `whale_open_position(wallet, condition_id, size, avg_price, current_value, pnl)` — from `/positions`.
- `whale_activity(wallet, tx_hash, condition_id, side, outcome, size, price, ts)` — from `/activity`, ONLY for live-signal.

**Next workstream (not started, Jack's call):** scope a `/closed-positions` → DB ingestion job (per-whale backfill + `whale_category_stats` rollup) as the maturing all-categories platform foundation.
