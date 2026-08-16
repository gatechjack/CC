# Exploration provenance — PCT→Kalshi repurpose plan (2026-08-15)

Code-grounded findings behind the ratified plan (`PLAN_ratified.md`). Three parallel
Explore agents; file:line refs are into trading_corp/ of this worktree (base 2528aaa).
Read-only investigation — nothing was modified.

## Layer 1 — signal/detection + copy-list (Agent 1)
- **Detection is POLL-based, ~60s.** Loop `main.py:5043` `_scheduled_polymarket_copy_trader_loop`;
  `poll_interval_sec` default 60 (`config/strategies.yaml:104`), floored `max(15.0, poll)` at
  `main.py:5064`. Per cycle: `polymarket_copy_trader.py:220` loads `selected_whales`, `:243`
  `fetch_activity(wallet, limit=20)`. BUY entry `:315`, SELL exit `:334` (side is explicit).
  Cold-start records `last_seen_ts` without emitting. API lag 10–60s (comment `:375`).
  **Total latency 70–120s.** Activity limit 20/poll → truncation risk for busy whales.
- **No Polymarket on-chain/websocket infra — greenfield.** Only WS in repo = `data/bitunix_ws_feed.py`
  (bitunix futures, `wss://fapi.bitunix.com`). Polymarket = REST only; Polygon RPC used only for
  USDC balance (`brokers/polymarket.py:749` eth_call). Sub-second ⇒ new Polygon event-log watcher.
- **`selected_whales` is the single authoritative copy-trigger list** (`_load_selected_whales`
  `:761`→read `:220`). Pin: `web/routes.py:2995-2996` writes selected_whales + pinned_whales.
  Promotion: `refresh_polymarket_whales.py:567` writes selected_whales, merging pinned `:531-561`
  (`--algo-select` at `:527`). Per-whale state `whale_state:<wallet>` (`:791`/`:805`).

## Layer 3 — Kalshi execution + metadata + risk (Agent 2)
- **Placement:** `brokers/kalshi_live.py:315` `KalshiLiveBroker.place_order` → V2 event-order
  `/portfolio/events/orders` (`:366`), `build_v2_event_order` (`:357`), `usd_to_contracts` (`:359`).
  **Market orders:** `order_type="market"` (`kalshi_copy_trader.py:543`) + IOC time-in-force
  (`kalshi_live.py:61-64`).
- **Creds:** `utils/secrets.py:356` `load_secrets` (KeyVault; `KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PEM`
  + KAREN variants `:60-61`/`:186-187`; KV underscore→hyphen `:342`); PEM → tempfile
  `brokers/kalshi.py:116-144`.
- **Idempotency:** `client_order_id` UUID5 `kalshi_live.py:122-126` over (division, whale, ticker,
  outcome, signal_id). Snapshot reconciliation `kalshi_copy_trader.py:895-948`.
- **Coupling → DUPLICATE (ratified).** Agent recommended EXTRACT long-term, but **kalshi_copy_trader
  SKIPS sports tickers** (routes `KXMLB…`→`kalshi_sports_scout`) ⇒ MLB strategy has ZERO runtime
  overlap; duplicate keeps Kalshi Copy 100% untouched.
- **Kalshi metadata (Layer-2 Kalshi side):** `data/kalshi_market_map.py:146-203` — `MarketRecord`
  (ticker/event_ticker/title/subtitle/prices) + `EventRecord` (series_ticker/category/event_type).
  MLB events prefix `KXMLB-`.
- **Risk (kalshi):** `config/risk.yaml:56-67` (per-position 2%/$5, daily $50, tail bounds). Live copy
  path BYPASSES RiskAgent (`main.py:4757-4765`) — gates on auto_execute + roster only.

## Layer 2 — Poly-side matching + repurpose surface + guardrails (Agent 3)
- **Poly metadata:** `ActivityRow` `data/polymarket_data_api_client.py:133-200`
  (title/slug/event_slug/outcome/condition_id/outcome_index/price/size). MLB deterministically
  matchable (team codebook + date regex); LLM only for edge cases.
- **LLM infra:** `agents/llm.py:45` `build_chat_model`, `:77` `is_llm_available`; template
  `agents/strategies/kalshi_llm_arbitrage.py` (warm-and-fan, cooldown, cost). Default `sonnet-4-6`;
  **latency ~5–10s/call ⇒ cannot be inline** with a seconds budget → matching = pre-computed daily map.
- **Blast radius:** `config/divisions.yaml:218` (polymarket_copy_trading, broker: polymarket,
  standby:true); `config/strategies.yaml:~100` (polymarket_copy_trader block); `config/risk.yaml`
  (polymarket + kalshi sections).
- **Reusable (venue-agnostic):** `data/polymarket_whale_audit.py:686` `build_audit_report` (FULL),
  `scripts/polymarket_whale_recency.py` recency scorer (FULL), `scripts/refresh_polymarket_whales.py`
  discovery, agent_state persistence. **Dead:** `brokers/polymarket*.py` (never live; standby).
- **Guardrails:** `agents/risk.py:211` per-trade cap; `:144` daily-loss halt +
  `StrategyState.persist_halt` (`persistence/models.py:256`); `:170` max-drawdown; halt latch
  `:113`/`:120`. **Aggregate caps REMOVED 2026-06-16** (audit_event full-scan froze the engine) →
  use an in-memory per-day counter, NOT the aggregate query. CEO-graph auto_execute caps
  `graph/ceo_graph.py:113`.
