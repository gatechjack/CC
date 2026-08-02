# kalshi_crypto_v2 — Maker-Shadow: BUILD PROPOSAL (design only; approve before code)

**Date:** 2026-08-02 · **Status:** design doc, NO code written · **Standing:** read-only research; zero capital; no order/placement surface; old `kalshi_crypto` untouched; deploys operator-gated.

Observer specifics below were **verified against `trading_corp/agents/strategies/kalshi_crypto_v2_observer.py`** (methods `ws_loop`/`cycle_loop`; tables `kcv2_index_ticks(avg60_value,…)` / `kcv2_quotes(cadence,status,…)`; RSA-PSS `KALSHI-ACCESS-*` signing over `f"{ts}GET{path}"`; creds env→Key Vault in-memory; DB via `TRADING_CORP_DB_URL`).

## 1. Goal & the one question
The maker resolution study left ETH with a per-ATTEMPT positive that survives full backtest pessimism (ETH-A +$0.030, t=2.5; null controls all lose → it's the model's directional signal, not spread capture), while BTC collapses to ~0 and SOL/XRP go negative. **Every backtest number rides an OPTIMISTIC queue-free fill** (fills at the resting price on any ≥1-tick trade-through; no queue, no partial). The shadow is the live, zero-capital arbiter: **does the ETH maker edge survive REAL fills and queue position?** It places nothing.

## 2. Architecture
- **Second WS leg on the observer.** Add a `shadow_ws_loop` alongside the existing `ws_loop` (cfbenchmarks_value) via the same `asyncio.gather`, on a **separate WS connection** (isolation: a trade-stream fault must not drop the index feed the observer depends on). Same `self._sign("GET", "/trade-api/v2/ws/v2")` auth already proven in `ws_loop`.
- **Subscription:** Kalshi `trade` (v1) and `orderbook_delta` (v2) channels, `params.market_tickers = <active 15m tickers>`.
- **Active-market enumeration:** reuse the observer's existing signed REST call `rest_get("/markets", {"series_ticker": KX{ASSET}15M, "status":"open"})` (the same call `quote_rows` already makes) at each 15m boundary; subscribe on open, hold until a settled-trade prints or `window_close_ts+120s`. Typically 2–6 near-money 15m markets/asset → ~8–24 subscriptions total.
- **Reconnect:** mirror `ws_loop`'s exp-backoff (1s→30s); on reconnect re-read the active list and re-subscribe; flag the affected windows `coverage_gap=1` (do NOT backfill prints from REST — a gap is a non-observation, not "no trade").
- **Two tiers:** **v1 = trade-prints-only** (through-flag + fill timing; `queue_ahead` NULL) ships first — it captures the #1 unknown (real fill_rate vs backtest 0.90–0.95) with no orderbook complexity. **v2 = + orderbook_delta** maintains an in-memory book per market and records `queue_ahead` (resting size at/better than the rest level at entry time). v1→v2 is a config flag (`KCV2_SHADOW_TIER`), no schema change.

## 3. Fill model made live (identical to the backtest)
Per tracked 15m window: at the **first in-window trade** (entry minute, variant A), set `rest_level = ` that minute's last traded YES price (= backtest `price_close`); side = the S4 model's side. Then from live `trade` events: **through_flag=1** iff a later trade prints ≤ `rest_level−0.01` (YES) / ≥ `rest_level+0.01` (NO); `fill_ts` = first such print; fill price = `rest_level` (YES) / `1−rest_level` (NO). At close, `settle_value` = the `avg60_value` from `kcv2_index_ticks` nearest `window_close`; `hypo_pnl = (settle≥strike?1:0) − fill_price − kalshi_fee` on fills, **$0 on no-fills** (per-ATTEMPT convention). Track **variant A primary; add B** (second tradeable print) as a second row per window (`entry_variant` column).

## 4. Data model — `kcv2_maker_shadow` (matches kcv2_* conventions)
`id PK AUTOINCREMENT`; join on `market_ticker + window_open_ts_ms`. Columns: `market_ticker, asset, entry_variant('A'/'B'), window_open_ts_ms, window_close_ts_ms, cycle_id_at_entry, model_p, side, entry_ts_ms, rest_level, through_flag, fill_ts_ms, minutes_to_fill, queue_ahead (NULL v1), settle_value, outcome('win'/'lose'/'no_fill'/'no_entry'/'no_settle'), hypo_pnl_per_contract, coverage_gap, schema_tier('v1'/'v2'), created_ts_ms`. `UNIQUE(market_ticker, window_open_ts_ms, entry_variant)`; indexes on `(asset, window_open_ts_ms)`, `(created_ts_ms)`. Idempotent migration `scripts/migrate_kcv2_shadow_table.py` (IF NOT EXISTS; touches no existing table).

## 5. Deploy plan
- **HARD PREREQUISITE (operator's sequencing):** the shadow lands only on a **verified-running T2 observer** — ≥2 consecutive clean `kcv2_heartbeat` cycles (`alarm=0`, `rows_index>0`, `rows_quotes>0`, `index_ws_connected=1`) and ≥1 `cadence='15m' status='open'` row in `kcv2_quotes`.
- **Separate process/unit** `trading-corp-kcv2-shadow.service` (`After=` the observer unit; `Restart=on-failure`), NOT folded into the observer — independent restart preserves the index feed. Shares only the SQLite DB.
- **Rollback:** stop the unit (observer unaffected); `kcv2_maker_shadow` has no FKs and no live-path reader — back up + `DROP TABLE`.
- **Smoke/verify:** 0-traceback boot; WS "subscribed to N markets" (not 403); rows landing within one closed window with `through_flag`+`settle_value`+`outcome`; zero-row alarm after 30 min of no rows; **grep gate** `place_order|submit_order|create_order|broker\.` → 0 matches before activation.

## 6. Soak & readout
**3 weeks min, checkpoint read at 2 weeks** (read-only, no verdict). Expected ~100–400 ETH attempts over 3 wks. Checkpoint metrics vs backtest: **live fill_rate** (vs 0.94–0.95 — a drop to 70–85% signals real queue drag); **queue-adjusted per-ATTEMPT EV on ETH** (vs +$0.030, |t|≥2 = surviving); **filled/unfilled win%** (backtest had unfilled ≈100% winners — if it holds live, the queue-free optimism was load-bearing); **BTC/SOL/XRP controls** should stay ≈0/negative — a strong positive there flags a live-shadow artifact.

## 7. Open questions for sign-off
- **OQ-1** WS per-connection/subscription limits for ~8–24 tickers + a second parallel connection under the KAREN key's plan (verify empirically week 1).
- **OQ-2** `orderbook_delta` message volume — can the asyncio loop keep up without lagging the trade channel? (the v1-first split exists to measure this before enabling v2; 2-wk checkpoint is the gate.)
- **OQ-3** clock alignment: confirm `fill_ts_ms < window_close_ts_ms` for all fills (trade match-time vs settlement time, both Kalshi server-side).
- **OQ-4 (key design decision) model inference in live:** the shadow needs `model_p` at T0−60s. **Recommend** adding an inference step to the observer's 30s `cycle()` that writes `model_p` to a small shared table the shadow reads (avoids the shadow holding the CatBoost model; avoids train/infer divergence) — vs serializing the model into the shadow process. Operator to choose before code.
- **OQ-5** enumerate confirm: does `/markets status=open` consistently return the right near-money 15m set through the window lifecycle (a 14-min-old market may still be "open" but in terminal convergence)?

## 8. Status & OQ resolutions (2026-08-02)
**APPROVED by operator.** Build PARKED until the operator completes the T2 deploy + heartbeat verification (the §5 hard prerequisite). OQ resolutions (operator: adopt my recommendation where it follows already-ruled principles — isolation, read-only, verified-observer prereq, no new pulls; escalate only new scope/risk):
- **OQ-4 (model inference) — CONFIRMED (operator):** the observer's 30s `cycle()` runs the S4 inference and writes `model_p` to a small shared table; the shadow reads it and never holds the CatBoost model.
- **OQ-1 (WS isolation/limits) — ADOPTED:** second parallel WS connection (isolation principle). Empirically confirm subscription/connection limits under the KAREN key in soak week 1 (a check, not new scope).
- **OQ-2 (orderbook_delta volume) — ADOPTED:** ship v1 trade-prints-only first; measure trade-channel + delta message rate; v2 (queue depth) gated at the 2-week checkpoint (no new scope beyond the approved two-tier design).
- **OQ-5 (active-market enumeration) — ADOPTED:** reuse the observer's existing `/markets status=open` signed REST call (read-only, no new pull); verify the returned near-money set through the window lifecycle in week 1.
- **OQ-3 (clock alignment) — week-1 verify:** assert `fill_ts_ms < window_close_ts_ms` on all fills.

None of the above create new scope or risk beyond the approved design, so nothing was escalated. **No code until the operator's T2 verification is done.**
