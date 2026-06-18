# Trading Corp — Divisions and Strategies

## What this is

This file is the authoritative list of trading divisions and the
strategies each one is currently running. Dated deploy markers that
used to live in CLAUDE.md have been moved to
[runbooks/deploy_log.md](../runbooks/deploy_log.md) — that file is
the source of truth for "what shipped when".

For the project-wide architecture (organizing principles, decision
pipeline, domain model, state model), see
[docs/ARCHITECTURE.md](ARCHITECTURE.md). For known sharp edges in
how divisions and brokers behave at runtime, see
[docs/sharp_edges.md](sharp_edges.md).

## Division × strategy matrix

A division is a (brokerage × account) portfolio manager. A strategy is
how a division decides what to trade. One division can run multiple
strategies. This vocabulary was clarified 2026-05-02 — earlier code +
docs sometimes called Otter and Cypher "divisions"; that was wrong.
They are strategies inside the `coinbase_spot` division.

| Division | Brokerage / accounts | Strategies running there | Status |
|---|---|---|---|
| `robinhood` (`pmcc_robinhood.py`) | Robinhood Individual (PMCC) + IRA (stocks/ETFs + weekly covered calls — see [BACKLOG.md "Robinhood IRA drilldown"](../BACKLOG.md)) + Joint via `account_filter` | PMCC on Individual today; IRA + Joint surface in dashboard but no automated strategy yet | Live broker reads, paper-execute, HITL on every order |
| `coinbase_spot` | Coinbase spot | **Coinbase BTC Donchian** (6h Donchian Channel Breakout, [strategies/coinbase_btc_donchian_agent.py](../trading_corp/agents/strategies/coinbase_btc_donchian_agent.py) + decision module [strategies/donchian_btc.py](../trading_corp/agents/strategies/donchian_btc.py)). 100%-in/out CASH↔BTC, long-only, paper-mode (`auto_execute: false`). Lord Otter + Market Cypher set to `enabled: false` same deploy (files preserved per `trading_corp_bitunix_vision.md` for future BitUnix wiring). | Live: poll-driven 6h scheduler, broker reads, paper-execute, HITL on every order. |
| `coinbase_futures` | Coinbase futures | None today (kept as failover) | UI shows `STANDBY` badge. Order path is still active in code today; behavioral disable is a follow-up. |
| `bitunix_futures` | BitUnix Futures (USDT + USDC margined) | **bitunix_futures division agent** ([divisions/bitunix_futures_observer.py](../trading_corp/agents/divisions/bitunix_futures_observer.py)) running the **Phase 3.2 confluence score accumulator** ([strategies/bitunix_confluence.py](../trading_corp/agents/strategies/bitunix_confluence.py)). Receives Otter + Cypher webhooks (fanned from `web/webhooks.py`); each signal appends to `bitunix_signal_ledger` with per-factor TTL; scorer sums weights of all live (TTL-filtered, deduped by signal_name) signals + price-action factors (VWAP / HH-LL_4h / volume / pct_change computed live from `data/bitunix_price_context.py` against the BitUnix 3m bar cache) + applies guard penalties → maps net_score to PREMIUM (≥12) / STANDARD (≥8) / SKIP. 30-min per-side cooldown gate. Order proposer with structural stop, 2R TP, 0.5%/trade effective-risk cap, 3% daily-loss kill-switch. **auto_execute=true** within those caps (risk gate IS the HITL gate, per Board). Live dashboard panel at `/division/bitunix_futures` (partial `partials/bitunix_score_panel.html`) surfaces live score + contributions + PA flags + cooldowns + recent fires. Phase 3.1 single-bar `_tier_for` retained in-code behind `scoring.enabled` flag for fast rollback. | Read-only Phase 1 shipped. Phase 3.0/3.1/3.2a shipped. **Phase 3.2 confluence score accumulator shipped.** Live `place_order` raises `NotImplementedError` until Phase 4 (gated on stop-loss strategy + conviction → leverage map). Phase 3.2b multi-leg scale-out queued. See memories `trading_corp_bitunix_vision.md` + `trading_corp_bitunix_phase3_confluence_model.md`. |
| `polymarket_arbitrage` | Polymarket prediction markets (single dedicated EOA wallet on Polygon mainnet, signer == funder) | **polymarket_arbitrage** ([strategies/polymarket_arbitrage.py](../trading_corp/agents/strategies/polymarket_arbitrage.py)) — scan-driven LLM-divergence detector. Pulls open Polymarket markets via gamma-api, deterministic-filters by volume/spread/ttr/implied-prob, **K=20 survivors per cycle (warm-and-fan parallel)** get a calibrated YES probability via direct Anthropic call (NOT through Research firm; shared analyst-persona system prompt at `_polymarket_prompts.py` is prompt-cached). Emits ProposedOrder when `\|LLM prob - implied prob\| × 100 ≥ 10%`. **HITL-direct** (no per-trade Board click; risk gate still load-bearing). Activity rail + LLM analysis right-rail render rich tiles per audit row. **Dashboard data layer** (`agents/polymarket_resolver.py`): hourly resolver writes `polymarket_round_trips` from would_have_placed + gamma-api resolution; 5-min equity snapshot writes `polymarket_equity_history`. | Read-only Phase 1+2a shipped. Wallet live ($500 USDC). Strategy **`enabled: true` in paper-mode**. Awaits Phase 2.5 Backtester verdict (≥30 resolved trades) for live-mode flip. See `~/.claude/.../memory/trading_corp_polymarket.md`. |
| `polymarket_copy_trading` | Same Polymarket wallet (`broker: paper` for sizing/PnL; reads activity feed via Polymarket Data API) | **polymarket_copy_trader** ([strategies/polymarket_copy_trader.py](../trading_corp/agents/strategies/polymarket_copy_trader.py)) — mirrors top-12 whale entries (Wilson-LCB × ROI × category-bonus selection) at $1/$2/$5 size tiers. 60s poll on `/v1/activity`; explicit `side` + `outcome_index` from the API (no inference). Entry gates: **resolution-readiness** (skip <24h-to-resolve markets) + **drift** (skip if market moved >30% against whale fill). | **PAUSED — paper-only (decided 2026-06-17).** Disarmed to the read-only `PolymarketBroker` (`is_live_armed=False`); stays paper, accumulating a paper track record. Go-live **PARKED** behind two options (EU-native Azure app / residential-IP proxy) + an **edge-validation gate** — after the 2026-06-17 cutover's first live `post_order` hit Polymarket's **pure IP-geoblock** and PCT was surgically disarmed (Bitunix preserved). Detail + go-live options + gates: [BACKLOG.md "Priority 2 — Polymarket Copy Trading"](../BACKLOG.md). Prior paper record: 60% WR / +$27.66 / 125 RTs. See `~/.claude/.../memory/trading_corp_polymarket.md`. |
| `kalshi_arbitrage` (structural + cross-venue Phase 0) | Kalshi (shared `KalshiBroker` across kalshi_* divisions; broker:paper for equity tracking) | Two structural arb strategies (no LLM in path): **kalshi_tail_price_arb** (YES+NO at price tails ≤5¢ or ≥95¢) + **kalshi_temporal_bucket_arb** (P(early)>P(late) violations + bucket-sum<$1 detection). 5-min poll each. Multi-leg ProposedOrders share `kalshi_pair_id` or `kalshi_arb_set_id`. **Plus Phase-0 observer-only sibling: kalshi_sports_arb_observer** ([strategies/kalshi_sports_arb_observer.py](../trading_corp/agents/strategies/kalshi_sports_arb_observer.py)) — read-only cross-venue scout for Kalshi MLB game-ML vs `the-odds-api` per-book (Pinnacle + DK/FD/BetMGM). Computes EV-at-fill for Hypothesis A (cross-venue arb) and Hypothesis B (sportsbook→Kalshi lead-lag) at qty=10. NEVER emits orders. 1h poll, NBA + MLB league-parameterized via `_PHASE0_LEAGUE_CLASSIFIERS` dispatch (NBA validated to-the-cent OKC-SAS row; MLB hand-cert LAA-vs-TEX 2026-05-24 15:40:54 UTC). Verdict design: B always INCONCLUSIVE at 1h cadence; `SHELVE_LATENCY_THESIS_CLOSED` A-verdict routes to kalshi-crypto-shelved pattern if A=0/negative-EV. **SHELVED 2026-06-14** — verdict realized; see [shelve report](../reports/2026-06-14_kalshi_sports_arb_observer_shelve.md) (the `[[project-kalshi-sports-arb-observer-phase0-live]]` memory was never written — repo is canonical). | Live paper. tail_price + temporal_bucket: `enabled: false` until structural opps appear (Phase K2.1+K2.2 shipped). **sports_arb_observer: SHELVED 2026-06-14** — Phase-0 verdict `SHELVE_LATENCY_THESIS_CLOSED` (no hourly cross-venue edge; 1,274 positives artifact-confirmed; mean EV-at-fill −$0.375/$10 over 8,360 MLB obs, 2026-05-24→06-04). `enabled: false` in repo; prod converge pending operator sed. See [shelve report](../reports/2026-06-14_kalshi_sports_arb_observer_shelve.md). |
| `kalshi_llm_arbitrage` | Kalshi (shared broker) | **kalshi_llm_arbitrage** ([strategies/kalshi_llm_arbitrage.py](../trading_corp/agents/strategies/kalshi_llm_arbitrage.py)) — structural clone of `polymarket_arbitrage` adapted to Kalshi. K=20/cycle warm-and-fan LLM divergence, 60s poll, 10% base divergence threshold. **Strict-category gate (Economics/Financials):** divergence ≥30% AND llm_prob ∈ [0,0.15]∪[0.85,1.0]. Sports/Climate/Crypto stripped from `discovery.categories` (owned by specialized agents). | Live paper. Phase K6.1 shipped. Climate/Weather + Crypto removed (now owned by specialized agents below). |
| `kalshi_copy_trading` | Kalshi (shared broker) | **kalshi_copy_trader** ([strategies/kalshi_copy_trader.py](../trading_corp/agents/strategies/kalshi_copy_trader.py)) — mirrors selected whales via Apify Starter scrape ($29/mo Bronze, ~$160/mo poll-throttled). 10-min poll cadence. Side detection via Kalshi public trade-tape size-match. Sports tickers skipped via `_is_sports_ticker` (owned by Sports Scout). **Watch-only sibling shipped:** `agent_state(watch_only_whales)` tracks observation-only handles (never emits ProposedOrders); daily stats + weekly deep-scan timers grow the list organically. See memory `kalshi_watchlist_architecture`. | Live paper. Phase K3 shipped. Exit-pricing fix + 253 RT backfill: corrected to **+$0.58 net / 149 wins / 104 losses**. Break-even paper / fee-negative live at $1-3 sizing. Watch-list at 2 visible whales (lengthy.starfish, Hispaniola). |
| `kalshi_weather` | Kalshi (shared broker) | **kalshi_weather_arb** ([strategies/kalshi_weather_arb.py](../trading_corp/agents/strategies/kalshi_weather_arb.py)) — forecast-driven, no LLM. NWS hourly forecast + **Open-Meteo cross-model ensemble σ** (GFS/ICON/ECMWF/MétéoFrance/GEM via [data/open_meteo_client.py](../trading_corp/data/open_meteo_client.py)) + **METAR nowcast blend ≤6h** ([data/metar_client.py](../trading_corp/data/metar_client.py)) + **fractional Kelly sizing** with per_market(5%) / per_day(25%) / per_city(15%) cap ladder + `min_usd=$1` floor. 5-min poll. `kalshi_weather_arb` yaml block is **prod-only** (drift; deployed via `scripts/patch_kalshi_weather_*.py`). | Live paper. Phase Weather shipped. **Tier-1 (ensemble σ + nowcast + Kelly) shipped**; city-code aliases shipped. Validation gate: ≥30 resolved RTs WR ≥65% before `auto_execute: true` flip. See `~/.claude/.../memory/trading_corp_kalshi.md`. |
| `kalshi_crypto` | Kalshi (shared broker) + Coinbase spot quotes | **kalshi_crypto_arb** ([strategies/kalshi_crypto_arb.py](../trading_corp/agents/strategies/kalshi_crypto_arb.py)) — live-spot-driven via Coinbase spot for BTC/ETH/SOL/DOGE/XRP; computes σ from hard-coded annualized vol × √T (v1 constants — see Tier-2 vol-v2 backlog). Same `_weather_math.evaluate_weather_market` math as kalshi_weather (unit-agnostic). B-suffix bucket markets dominant (~100% of evaluable); width derived per-event from neighboring B-ticker median gap. T-suffix tickers SKIP (direction ambiguous — P3 follow-up). HYPE/BNB recognized but skipped (no Coinbase US spot). 60s poll. | Live paper. Phase Crypto shipped. AM fix unblocked bucket markets (was 100% no_strike). Validation gate same as kalshi_weather. See `~/.claude/.../memory/trading_corp_kalshi.md` + `kalshi_market_structure.md`. |
| `fidelity` (`fidelity_options.py`) | Fidelity Joint + 401(k) (Individual deactivated — `enabled: false` in YAML) | Fidelity options | Bot-blocked from Azure VM IP — paper-fallback only. P1 backlog **DEFERRED** pending Plaid investigation. |

## Brokers powering them

| Adapter | Capability |
|---|---|
| `paper.py:PaperBroker` | In-memory account, deterministic fills. Default + universal fallback. |
| `paper.py:PaperExecutionBroker` | Wraps a real read-only broker: real snapshots, simulated fills. Used in PAPER mode for any live-cred division. |
| `robinhood.py:RobinhoodBroker` | `robin_stocks`, multi-account via `account_filter`, persistent session pickle. |
| `coinbase.py:CoinbaseBroker` | ccxt-based. Spot live, futures stub. Separate API keys per portfolio. |
| `bitunix.py:BitunixBroker` | BitUnix Futures, async httpx, SHA256-double-sign auth (no passphrase). Read-only Phase 1: `snapshot()` + `quote()` only; `place_order` / `cancel_order` raise `NotImplementedError` until Phase 4. Multi-margin-coin balance aggregation (USDT + USDC summed; BTC/ETH-margined deferred). |
| `polymarket.py:PolymarketBroker` | Polymarket prediction markets, async httpx. **First adapter to subclass `ReadOnlyBroker` ABC — `place_order` does not exist on the class** (static type-system enforcement of read-only, not runtime flag; CLAUDE.md "Code path isolation" rule). `snapshot()` reads USDC balance via direct Polygon RPC `eth_call(USDC.balanceOf)` + open positions via `data-api.polymarket.com`. `quote(symbol)` parses `slug:outcome`, fetches token_id from gamma-api, last-trade-price from CLOB. Stub mode if creds missing. httpx concurrency cap (semaphore=6) + 429 backoff with jitter. Phase 1+2a shipped. Phase 3 (live order placement) will land as a separate `PolymarketLiveBroker(Broker)` class with signing. |
| `kalshi.py:KalshiBroker` | Kalshi prediction markets via `pykalshi.AsyncKalshiClient` (MIT, RSA-PSS auth). Read-only `snapshot` + `quote` + discovery (`list_markets`, `get_market`, `get_market_trades`, `get_market_resolution`). Shared across all `kalshi_*` divisions (one connected instance — lazy-resolved in each strategy's loop). Phase K1 shipped. Live order placement gated on individual division validation gates. |
| `fidelity.py:FidelityBroker` | Playwright/Firefox browser automation. Currently bot-blocked from Azure VM IP. **Subclasses full `Broker` ABC — predates the read-only-by-ABC rule; see [docs/sharp_edges.md](sharp_edges.md).** |

New read-only adapters: subclass `ReadOnlyBroker` (no `place_order`),
not the full `Broker`. `PolymarketBroker` is the first / canonical
example. The ABC was extracted as part of Polymarket Phase 1 (commit
`d7cbea2`).

## Status legend

- **Live:** broker connected with real credentials. Reads are real;
  writes may be paper-execute via `PaperExecutionBroker` depending on
  the division's `auto_execute` setting and process mode flag.
- **Paper:** `PaperBroker` instance, no real broker connection. Used
  as default + universal fallback.
- **STANDBY:** division wired in code + UI but holds no active
  strategy. UI badge only; see
  [docs/sharp_edges.md](sharp_edges.md) for the runtime-disable
  caveat.
- **Read-only:** broker adapter that subclasses `ReadOnlyBroker`
  (`PolymarketBroker`, `KalshiBroker`). No `place_order` method
  exists on the class.

## How a new division gets added

See [CLAUDE.md § 5 "Adding a new strategy or division"](../CLAUDE.md)
for the canonical recipe. In short: division code lives in
`agents/divisions/<name>.py`; strategy logic lives in
`agents/strategies/<name>.py`; wire into
[config/divisions.yaml](../config/divisions.yaml). Don't design a
new division speculatively — build only after an existing pattern is
validated in production.
