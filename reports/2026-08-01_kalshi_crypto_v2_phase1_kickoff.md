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

---

## 6. Phase-1 findings: bar depth + T3a signal census (2026-08-01)

**Data source resolved.** The local DB copy has NO bar table; `bitunix_bar_history` is archived on prod
only. Pulled read-only via `kc2_pull.ps1` (operator-run; `sqlite3 -readonly`, no prod writes) ->
`research/kalshi_crypto_v2/bitunix_bars_export.csv` (172,142 rows, 11.3 MB). Operator declined a blanket
SSH grant; if repeated prod reads become friction, the agreed path is a forced-command authorized_keys
key restricted to a whitelisted read-only script (enforced, not behavioral) — design when needed.

### Bar depth (retro corpus)
| asset | 15m rows | 15m window (UTC) | 3m rows | 1h | 4h | 1d |
|---|---|---|---|---|---|---|
| BTCUSDT | 3,759 | 2026-06-23 -> 2026-08-01 (39.2d) | 37,384 | 86.4d | 111.3d | 277.0d |
| ETHUSDT | 3,759 | 2026-06-23 -> 2026-08-01 (39.2d) | 37,580 | 55.0d | - | - |
| SOLUSDT | 3,759 | same | 37,579 | 55.0d | - | - |
| XRPUSDT | 3,759 | same | 37,580 | 55.0d | - | - |

15m coverage ~100% (3,759 bars == 39.16d x 96). Full multi-TF regime (h1+h4+d1) is **BTC-only**;
ETH/SOL/XRP have 1h only -> regime for those runs partial/SAFE_MODE. Funding-rate history is NOT in the
bar table, so retro regime runs with `funding_rate=None` (handled).

### T3a SFP signal census (lifted AS-IS, default constants, long-only)
26 raw SFP fires (ARMED) over the 39d window; BOS-confirmed entries by path:

| path | BTC | ETH | SOL | XRP | total |
|---|---|---|---|---|---|
| Mode-A (15m BOS) | 1 | 2 | 5 | 2 | 10 |
| Mode-B (3m BOS)  | 2 | 3 | 7 | 1 | 13 |

Signal span 2026-07-12 -> 2026-07-29. Full list: `research/kalshi_crypto_v2/signals_retro.csv`
(asset, sfp_mode, bos_tf, entry_ts_ms, entry_utc, swept levels, bos_ref_high). Mode-A and Mode-B are
alternative confirmations of the SAME fire pool (different entry timestamps), not additive opportunities.

**Load-bearing read:** the signal is RARE (~10-23 UP entries / 39d / 4 assets ~= 0.1% of Kalshi 15-min
windows). The retro-test is therefore a STRUCTURAL SCREEN (rank signals, expose gross mis-prediction),
NOT a statistically-powered EV verdict. Canonical EV comes from the T2 forward corpus per the metrics
discipline above. T4 alignment to Kalshi settled windows is pending Kalshi API access (creds).

---

## 7. cfbenchmarks_value channel verification — VERDICT: GO (2026-08-01)

**All four settlement indices stream live**, ~1/sec, with the trailing-60s average present:

| asset | index_id | tick rate | trailing-60s (`avg_60s_data`) |
|---|---|---|---|
| BTC | `BRTI` | ~1.00/s | yes |
| ETH | `ETHUSD_RTI` | ~1.00/s | yes |
| SOL | `SOLUSD_RTI` | ~1.00/s | yes |
| XRP | `XRPUSD_RTI` | ~1.00/s | yes |

The SOL/XRP missing-asset STOP-condition did NOT trigger. Probe: `research/kalshi_crypto_v2/probe_cfbenchmarks.py`.

**Creds (house pattern):** fetched at runtime from Azure Key Vault (`kv-tc-vtwbowt3wtkpy`) via
`azure-identity` `DefaultAzureCredential` (local `az login` context) + `SecretClient`, reusing the
`trading_corp/utils/secrets.py:245` mechanism scoped to `KALSHI-KAREN-*`. In-process only; env-var override
retained for prod/systemd; fail-loud, no file fallback. Gated on `KEY_VAULT_URI`.

**RESOLVED protocol (empirical — the docs were misleading):**
- endpoint = `wss://external-api-ws.kalshi.com/trade-api/ws/v2`. The docs' dedicated
  `/cfbenchmarks_value` base **404s** (AWS ELB). A plain-GET path probe (`probe_paths.py`) showed every
  candidate 404 EXCEPT `/trade-api/ws/v2` (401 `token_authentication_failure`) -> the feed is the
  `cfbenchmarks_value` CHANNEL on the standard trade-api ws path, also served on the external host.
- auth = ordinary RSA-PSS signed `KALSHI-ACCESS-KEY/SIGNATURE/TIMESTAMP` over `/trade-api/ws/v2` (the
  docs' "apiKey in user field" was a red herring; Basic-auth attempt also 404'd — it was a path, not auth, issue).
- subscribe = `{"id":N,"cmd":"subscribe","params":{"channels":["cfbenchmarks_value"],"index_ids":[...]}}`
  -> `{"type":"subscribed","msg":{"channel":"cfbenchmarks_value","sid":1}}`. `indexlist` is rejected
  (code 5 Unknown command); unused since the 4 index_ids are known.
- message = `{type:"cfbenchmarks_value", sid, seq, msg:{index_id, received_at(ms), data:"<raw CF frame>",
  avg_60s_data:{value, window_size, window_start_ts_ms, window_end_ts_exclusive},
  last_60s_windowed_average_15min? (only at :00/:15/:30/:45)}}`. `window_size` warms 0->60 over a minute.

**Implication for T2:** the forward logger connects to this endpoint + subscribes the 4 index_ids, logging
`received_at` + parsed `data.value` + `avg_60s_data` (the settlement TWAP). pykalshi's `AsyncFeed` still
can't be reused directly (hardcoded channel set + no `index_ids` param), but the hand-rolled `websockets`
client is proven. Kalshi market quotes (both-sided) + candlesticks for T1/T4 remain to be probed next.

**Guardrail note:** during protocol iteration the Karen **api_key_id** (a UUID identifier, not the private
key) appeared once in a tool-output error before output-redaction was added; the private key PEM never
appeared, and the key id is unusable without the private key. Redaction now scrubs all creds from stdout/stderr.

---

## 8. T1 — market census + history-depth probe + hand-verify (2026-08-01)

Live crypto series discovered via `/series?category=Crypto` (272 total; tickers NOT hardcoded).
Census via signed REST (`_kalshi_auth.py`, in-memory creds); scripts `t1_explore.py` / `t1_census.py`.

| series | cadence | asset | open | settled | earliest settled | candle granularity |
|---|---|---|---|---|---|---|
| `KXBTC15M` | 15-min up/down | BTC | 1 | **6,503** | 2026-05-25 | 1m (16/mkt) |
| `KXETH15M` | 15-min up/down | ETH | 1 | **6,503** | 2026-05-25 | 1m |
| `KXSOL15M` | 15-min up/down | SOL | 1 | **6,503** | 2026-05-25 | 1m |
| `KXXRP15M` | 15-min up/down | XRP | 1 | **6,503** | 2026-05-25 | 1m |
| `KXBTC` | hourly ladder | BTC | 318 | >=10,000 (capped) | older than sample | 1m |
| `KXETH` | hourly ladder | ETH | 390 | >=10,000 (capped) | older than sample | 1m |
| `KXSOLE` | hourly ladder | SOL | 425 | >=10,000 (capped) | older than sample | 1m |
| `KXXRP` | hourly ladder | XRP | 165 | >=10,000 (capped) | older than sample | 1m |

- **`KXSOL` (SOL "range") is inactive (0/0); SOL's active hourly ladder is `KXSOLE`.** Secondary hourly
  above/below directional series are all active: `KXBTCD` 318, `KXETHD` 390, `KXSOLD` 425, `KXXRPD` 165.
- **15-min up/down = the retro-test's Kalshi window:** 6,503 settled per asset back to **2026-05-25**
  (~69d), exact (not capped). Bitunix 15m bars start 2026-06-23, so the T4 overlap is **2026-06-23 ->
  2026-08-01 (~39d, Bitunix-limited)** — and all 23 SFP signals (2026-07-12 -> 07-29) fall inside both
  windows, so every signal is alignable.
- **Candlesticks:** 1-minute granularity confirmed (`/series/{s}/markets/{tkr}/candlesticks?period_interval=1`,
  epoch `start_ts`/`end_ts`); **5,000-candle/request cap** -> chunk longer ranges. Candle price fields are
  `yes_bid`/`yes_ask` OHLC in dollars.
- **Settlement mechanic (confirmed):** an up/down market settles YES iff the 60-second BRTI average at
  close >= `floor_strike` (the reference set at open). `rules_primary` states the CF Benchmarks 60s window.
- **Field notes for T2:** prices are `*_dollars` (0-1 OK); on SETTLED markets the book is degenerate
  (`yes_ask`+`no_ask` can = 2.0) so the sum-to-1 guard applies to LIVE quotes only; `floor_strike` +
  `expiration_value` give the resolution; `event_ticker` encodes date/time (`KXBTC15M-26AUG011500`).

### Hand-verified settled market (end-to-end, to the cent)
`KXBTC15M-26AUG011500-00` (event `KXBTC15M-26AUG011500`; page `kalshi.com/markets/kxbtc15m`):
- strike_type `greater_or_equal`, `floor_strike` 62344.15, close 2026-08-01 19:00Z, `result` "yes".
- **BRTI check:** settled `expiration_value` 62522.81 >= strike 62344.15 -> "yes" == result "yes" **[MATCH]**.
- Last 1m candle (of 16): `yes_bid` high/close 0.9990/0.0000, `yes_ask` 1.0000, `last_price` 0.9990 -->
  consistent to the cent (near-certain-yes book collapsing at settlement). Prices in dollars 0-1 confirmed.
