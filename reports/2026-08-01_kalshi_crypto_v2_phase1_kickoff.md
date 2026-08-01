# Kalshi Crypto Reopen — Phase 1 Research: Kickoff Reconnaissance + Open Forks

**Date:** 2026-08-01
**Branch:** `claude-2026-08-01b` (worktree off `prod-live` @ `dafe60b`)
**Status:** RECONNAISSANCE COMPLETE — blocked on operator decisions (see Open Forks).
**Scope:** RESEARCH only. No live orders, no auto_execute, paper/observation. Old `kalshi_crypto`
division (SHELVED 2026-05-22) must not be modified. New scaffold suggested: `kalshi_crypto_v2` /
`kalshi_structural`.

---

## 1. Required reading — completed

- `kalshi_crypto_knowledge_transfer.md` (from Downloads) — full read. Load-bearing: EV-at-fill is the
  ONLY decision metric; WR lies on near-resolution binaries; `implied_yes` = YES-ask always, NO fills at
  `no_ask`; store raw both-sided quotes + sum-to-1 guard; four historical data-integrity issues; §7
  closed avenues must not be re-attempted; path_logger flags are thesis-dependent (do not fix reflexively).
- `runbooks/strategy_harness_inventory.md` — EV-at-fill first-class; feed-health alarm + heartbeat are
  hard prerequisites for any collector; sports-arb observer is the raw-quote-storage reference.
- `trading_corp/agents/strategies/_sports_math.py` — `kalshi_fee(contracts, price)` = ceil(0.07*C*P*(1-P)*100)/100;
  `compute_ev_at_fill_b_directional(kalshi_leg, model_prob_outcome)`; `LegFill` dataclass. Reuse as-is.
- `CLAUDE.md` — read-only SSH standing / writes operator-gated; local Python via `scripts\run_capped.ps1`;
  `deploy_log.md` is prod source-of-truth; md5-verify prod independently of commits; no speculative divisions;
  auto_execute:false default; base64-chunked <=6500B for Azure payloads.

## 2. Reconnaissance findings (read-only; nothing modified)

### 2a. Signal source to lift AS-IS (T3)
- **SFP:** `trading_corp/agents/strategies/bitunix_sfp.py` — `SfpDetector` (REAL + CONSIDERABLE modes),
  `SfpModeBDetector` (15m fire -> 3m BOS), `TwoCandleSfpDetector`. Params (module constants): `PIVOT_LEN=50`,
  `BACK_TO_BREAK=4`, `WATCH_BARS=48`, `STOP_BUFFER_PCT=0.001`, `TP_R=2.0`. Feeds `SfpBar(ts_ms, o,h,l,c)`;
  `warm_start(bars)` replays history deterministically; emits `SfpEntrySignal`. Directly liftable for retro-test.
- **Regime:** `trading_corp/agents/strategies/bitunix_htf_regime.py` — `compute_regime(ctx, config)`,
  multi-TF (h1/h4/d1) EMA(20/50/200)+ADX(14,thr=20)+MACD+market-structure; `Regime` enum + `RegimeVerdict`.
  Config from YAML `bitunix_futures.htf_regime`.
- **Historical bars (retro-test corpus):** table `bitunix_bar_history` in `data/trading_corp.db`
  (`trading_corp/data/bitunix_bar_archiver.py`), cols `symbol,ts_ms,timeframe,o,h,l,c,volume,inserted_at`,
  PK `(symbol,ts_ms,timeframe)`. Assets/TF: BTC 3m/1h/4h/1d; ETH/SOL/XRP 15m/3m (+1h/1d). **Bitunix bars
  only — no Binance table.** Actual date-range depth per asset/TF must be probed empirically (see Fork E).

### 2b. Old division — blast radius to AVOID (do not touch)
`kalshi_crypto_arb.py`; `web/kalshi_crypto_vol_v2.py`; `web/data.py` (shared — add no new kalshi_crypto refs);
`web/templates/partials/pm_vol_v2_block.html`; `config/strategies.yaml` key `kalshi_crypto_arb` (~L1573,
`enabled:false`); `config/divisions.yaml` slug `kalshi_crypto` (~L264, `broker:paper`);
`data/kalshi_crypto_arb_cooldowns.yaml`. Shared providers `crypto_spot_provider.py` / `crypto_vol_provider.py`
are safe to reuse read-only.

### 2c. Kalshi reusable surface
- `brokers/kalshi.py` `KalshiBroker(ReadOnlyBroker)`: `quote`, `get_market_resolution`, `list_markets`
  (-> `discover_by_categories`), `get_market_trades`, `snapshot`. No candlestick/history wrapper — strategy
  calls `kalshi_broker._client.get_candlesticks(...)` (pykalshi) directly.
- `data/kalshi_market_map.py`: `MarketRecord` (raw yes/no bid/ask in dollars), `discover_by_categories`
  (rate-limit `inter_call_delay_sec=0.15`), `EventType` enum incl. BUCKET/TEMPORAL.
- **Auth:** RSA-PSS via `pykalshi.AsyncKalshiClient`; creds from env `KALSHI_API_KEY_ID` +
  `KALSHI_PRIVATE_KEY_PEM` (isolated second account: `KALSHI_KAREN_*`). Sourced from Key Vault / systemd env
  on prod. **Not present in the local session env.**
- **pykalshi 1.0.6 WS/history surface:** generic feed `afeed.py` (`.on(channel)`, `.subscribe(channel)`,
  base `wss://api.elections.kalshi.com/trade-api/ws/v2`); `get_candlesticks(ticker, ...)` in `history.py`.
  **No `cfbenchmarks` reference in pykalshi or the repo.**

### 2d. Observer reference pattern (T2/T3 storage)
`kalshi_sports_arb_observer.py` writes one `audit_event` row per market per cycle via
`logger_agent.log_event(name, kind, payload)` — raw both-sided quotes + `kalshi_quote_invalid` flag +
EV-at-fill in the JSON payload; never emits orders. This is the exact shape to copy for the new observer.

## 3. Corrected infrastructure picture vs. the mission brief

| Mission premise | Verified reality |
|---|---|
| `cfbenchmarks-value` WS channel, auth with our creds, "verify first" | pykalshi has a *generic* channel-subscribe feed but **no cfbenchmarks support**; channel unverified; cited doc URL may be a different WS base. Needs an **authenticated** probe. |
| Historical candlesticks endpoint (depth unknown) | `pykalshi.get_candlesticks()` exists; depth + auth-requirement must be probed empirically. |
| T2 "start immediately" | Blocked: needs (a) working cfbenchmarks channel (needs creds) and (b) a 24/7 deploy target (operator-gated). |
| Bitunix/Binance historical OHLCV for retro-test | Only **Bitunix** bars archived; no Binance. Depth per asset/TF unprobed. |

## 4. Open forks (operator decisions)

- **Fork A — Kalshi API access for research.** No creds in local env. Options: (A) operator sets read-only
  creds (prefer isolated `KALSHI_KAREN_*`) in the session env for local `run_capped` probes; (B) I author
  read-only `.ps1` probe runners, operator runs + pastes output; (C) deploy a read-only probe/observer to
  prod (operator-gated) and read via read-only SSH. Blocks all Kalshi-facing tasks (T1, T2, T4 Kalshi side).
- **Fork B — Verify cfbenchmarks-value before building on it.** First authenticated task: confirm the
  channel exists, streams ~1/s with trailing-60s avg, and covers BTC/ETH/SOL/XRP. Gated on Fork A.
- **Fork C — T2 logger deploy target.** New in-repo `infra/systemd/` observation service on prod VM (paper),
  vs. a new observation loop inside the main engine, vs. a separate paper VM (none found in repo/memory).
  Any deploy is operator-gated.
- **Fork D — Scaffold shape.** CLAUDE.md: "don't design a new division speculatively." Recommend an
  **observer strategy** (sports-arb-observer pattern) under the existing kalshi umbrella, not a new
  brokerage division. Confirm shape + name (`kalshi_crypto_v2` vs `kalshi_structural`).
- **Fork E — Retro-test data depth.** Probe `bitunix_bar_history` actual date-range + row counts per
  asset/TF (local, read-only) to gauge T4 feasibility before building the retro harness.

## 5. Proposed sequencing

1. Operator resolves Fork A (access) + Fork D (shape/name).
2. Unblocked-now local work (no creds, no deploy): lift SFP + regime AS-IS into an observation module;
   probe `bitunix_bar_history` depth (Fork E); build the retro-test harness against our bars.
3. On access: verify cfbenchmarks-value (Fork B) + T1 census/candlestick-depth probe with one hand-verified
   settled market.
4. On deploy target (Fork C): build + operator-deploy the forward logger with heartbeat + sum-to-1 guard.
5. Retro-test report; basis report after >=1 week forward data; one-page Phase-2 verdict draft.

**Metrics discipline (non-negotiable):** EV-at-fill is the only decision metric; pseudo-EV(candle) is
trade-price-based and ranks only; flat-window bucket reported separately; validation gate = positive mean
EV-at-fill on winners AND losers on the forward corpus, fees in, realistic size.
