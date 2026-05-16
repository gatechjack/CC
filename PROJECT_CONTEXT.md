# Trading Corp — Project Context

> **Purpose**: this document is the single source of truth for a fresh Claude
> session (or fresh human collaborator) to understand what Trading Corp is,
> what's been decided, and what the conventions are. It is **not** a status
> log — for that see [BACKLOG.md](./BACKLOG.md). This document changes
> rarely; the backlog changes constantly.
>
> **Read order**: this file first, then BACKLOG.md, then dig into code as needed.

---

## 1. What Trading Corp is

A multi-agent automated trading system. The architecture is "invest in
everything": one platform, multiple **divisions** (each a brokerage ×
account portfolio manager), each running one or more **strategies** (the
trade-decision logic). Today's divisions are `robinhood_pmcc`,
`robinhood_ira` (**dedicated dashboard SHIPPED 2026-05-11 19:00→20:30 UTC**:
covered-call pairs in PMCC-style click-to-expand rows + Portfolio table
for pure stocks + Puts section for short puts; expert-analysis right rail
uses the same `_render_pair_analysis` PMCC renderer fed by the new
deterministic `_analyze_ira_covered_call` — rule-based decision tree
[R1-R5] + real broker-fetched next-week chain pricing for roll legs.
Approve/Defer buttons hidden since IRA has no automated execution wired),
`robinhood_joint`, `coinbase_spot` (running
`coinbase_btc_donchian` — 6h Donchian Channel Breakout, paper-mode,
shipped 2026-05-09 02:53 UTC; replaced the prior Otter+Cypher confluence
which failed walk-forward), `coinbase_futures` (`STANDBY` — kept as
failover), `bitunix_futures` (Phase 1 SHIPPED 2026-05-03; **equity 2× double-count
fix SHIPPED 2026-05-10 04:19 UTC; Phase 3.0 observer SHIPPED
2026-05-10 14:19 UTC; Phase 3.1 full ladder + paper auto-execute
SHIPPED 2026-05-10 15:00 UTC; Phase 3.2a live BitUnix 3m bar cache +
real ATR + paper_trade_record SHIPPED 2026-05-10 16:12 UTC; **Phase 3.2
multi-bar confluence score accumulator SHIPPED 2026-05-11 17:52→18:23 UTC
in three sub-phases (3.2.1 score engine + ledger + cooldown table, 3.2.2
price-action factors wired to live bar cache, 3.2.3 dashboard panel at
/division/bitunix_futures). First STANDARD SELL fired 2026-05-11 18:00 UTC
exactly as designed.** Score path replaces the Phase 3.1 single-bar
`_tier_for()` classifier when `bitunix_futures.scoring.enabled=true`
(Phase 3.1 code retained for fast rollback). Backtest verdict pre-deploy:
+0.29R/trade, 43% win rate, +6R total over 9 days. Division actively
classifies inbound Otter+Cypher triggers and emits paper trades when
multi-bar net score ≥ 8; auto_execute=true within 0.5% per-trade + 3%
daily risk caps; Phase 3.2b multi-leg scale-out queued, Phase 4 real
BitUnix order placement after that), `polymarket_arbitrage` (read-only Phase
1+2a SHIPPED 2026-05-09/10; $500 USDC live; strategy `enabled:true` in
paper-mode 2026-05-10 02:05 UTC; HITL-direct architecture; rich activity
rail + LLM analysis right-rail SHIPPED 2026-05-10 02:31 UTC; warm-and-fan
parallel LLM K=20 SHIPPED 2026-05-10 02:51 UTC; data-layer gaps A+B
[round-trips + 5-min equity snapshots] SHIPPED 2026-05-10 03:28 UTC;
**prompt cache fix + category priors SHIPPED 2026-05-10 16:56 UTC —
~2.5× cost reduction per call**; awaiting Phase 2.5 Backtester verdict
for live-mode greenlight),
`polymarket_copy_trading` (paper STANDBY
placeholder; deprioritized 2026-05-10 in favor of Kalshi work),
**`kalshi_arbitrage`** (read-only Kalshi broker SHIPPED 2026-05-10
22:29 UTC; structural arb strategies tail-price + temporal + bucket
SHIPPED 2026-05-10 23:28 → 23:43 UTC; per-candidate audit events
SHIPPED 2026-05-11 00:13 UTC; $499 USDC funded; both strategies
`enabled: true` in paper-mode collecting overnight),
**`kalshi_llm_arbitrage`** (Kalshi LLM-divergence strategy mirroring
polymarket — SHIPPED 2026-05-11 00:52 UTC; `enabled: true` since
01:08 UTC after Semaphore(8) added on the K=20 LLM fan to prevent
Anthropic 429s when both LLM strategies fan simultaneously; first
3 `would_have_placed` events emitted within minutes of enable),
**`kalshi_weather`** (Climate/Weather specialist; forecast-driven
deterministic math, no LLM — NWS + Open-Meteo cross-model ensemble σ
+ METAR nowcast blend (≤6h); fractional-Kelly sizing with per_market /
per_day / per_city cap ladder. Phase 1 SHIPPED 2026-05-14 20:54 UTC,
Tier-1 ensemble/Kelly upgrade 2026-05-15 02:56 UTC, fractional-trading
quote-read fix + paper_capital + dashboard wiring 2026-05-15 14:06→14:39 UTC,
day-cap raise to $600 2026-05-15 21:48 UTC. **Equity-snapshot writer + round-trip
resolver wiring SHIPPED 2026-05-16 02:10 UTC** — first kalshi_round_trips for
this division now landing; ~107 pending at 03:30 UTC awaiting overnight
market settlement),
**`kalshi_crypto`** (Crypto specialist; Coinbase spot + Gaussian
probability with annualized vol; bucket math for B-suffix tickers.
Phase 1 SHIPPED 2026-05-14 21:19 UTC; horizon pre-filter + quote-fix
2026-05-15 14:06→14:39 UTC. **Equity-snapshot writer + round-trip
resolver wiring SHIPPED 2026-05-16 02:10 UTC** — first 11 round-trips
landed within minutes of restart; firing past 10% gate when real
edges present, peak observed 42.9% on KXETH-26MAY1617-B2230),
**`kalshi_copy_trading`** (Phase K3 whale-shadow strategy + K3 watch-only
sibling SHIPPED 2026-05-15 06:09→06:54 UTC — see memory
`kalshi_watchlist_architecture`; currently 2 selected_whales +
2 watch_only_whales, weekly deep-scan timer grows watch-list ~1-3
visible whales/week against the ~3.3% Kalshi visibility ceiling),
**`kalshi_sports_scout`** (read-only observer SHIPPED 2026-05-14 21:42
UTC; no division — logs divergence to `kalshi_sports_observed` audit
for 7-day evaluation pass before deciding on a Sports trading division),
`fidelity_joint`, and `fidelity_401k` (Fidelity paper-fallback —
bot-blocked from Azure VM IP, P1 DEFERRED 2026-05-03 pending Plaid
investigation; `fidelity_individual` deactivated same day). Dashboard
groups them into Individual / Crypto / **Prediction Markets** /
Retirement (group renamed from "Polymarket" to "Prediction Markets"
2026-05-10 22:29 UTC when Kalshi K1 landed alongside Polymarket;
UI reorg originally shipped 2026-05-03 16:25 UTC;
`coinbase_spot` tile shows a CASH/BTC badge + state-aware Donchian
dial — shipped 2026-05-09 03:30 UTC).
A shared **research firm** is consulted by divisions for cross-division
knowledge work; see CLAUDE.md § Research consultation for the rule on
when. Every proposed order flows through a deterministic **risk gate**
(code, not LLM judgment) and then through a **HITL Board approval gate**
before reaching a broker. Default mode is PAPER on every startup. LIVE
mode requires explicit `--live` flag plus a confirmation prompt.

System is live in production on Azure VM `tc-prod-vm` at
https://trading.jacksumner.com behind Caddy + Authelia, single-tenant
today. End goal: a personal infrastructure platform that runs trading
bots for the Board (Jack), eventually expanding to family member
accounts (wife, kids) and other non-trading apps. Long-term plan is
multi-tenant on Azure with proper isolation.

## 2. The Board (the user)

Address them as **Jack** in conversation when context calls for it. Some
relevant things to know:

- **Microsoft/Azure shop at work.** Building this on Azure has career value
  via AZ-104 / AZ-900 hands-on experience. Do not recommend AWS or Hetzner —
  the Azure decision is settled and it's deliberate.
- **Owns `jacksumner.com`** (registered at GoDaddy, DNS migrating to Azure DNS).
- **Risk tolerance**: aggressive-but-capped. Willing to size up to 5%
  per-trade on Coinbase. Per-account drawdown caps are firm.
- **Budget**: not on shoestring. Chooses scale, security, reputation over
  raw cost. ~$150/mo Azure budget initially; scales up as bots add value.
- **Domain knowledge**: solid on PMCC mechanics, scalping, options Greeks,
  general trading. Does not need basic concepts re-explained.
- **Pays for Lord Otter** — a TradingView Pine indicator from AlexOCrypto.
  This is the closed-source dependency the scalping strategy is built around.
- **Communication style preference** (see §10).

## 3. Tech stack (locked decisions)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Existing codebase; LangGraph + ccxt are Python-native |
| Web | FastAPI + HTMX + Jinja2 | Server-rendered, lightweight; PWA-installable |
| Mobile UX | **PWA** (not native iOS) | Single user, family expansion possible. Native iOS = wasted effort |
| Orchestration | LangGraph + SqliteSaver checkpointer | HITL `interrupt()` is core to the trade flow |
| LLM | Anthropic Claude (Sonnet 4.6 default; Opus 4.7 for Backtesting + EOD Debate) | Quality + tool use |
| Brokers | ccxt (Coinbase), robin_stocks (Robinhood), Playwright/Firefox (Fidelity) | Best-in-class for each |
| Database | SQLite local → Postgres on Azure (planned) | Schema portable across both |
| Push | Telegram (notification-only; deeplink to web app) | Bridge channel until web push lands; HITL UX lives in the web app at trading.jacksumner.com |
| Hosting | **Azure** (East US, single VM B2ms initially) | Career synergy + multi-tenant security |
| Domain/DNS | jacksumner.com → Azure DNS | Stable URL for TV webhooks; trading.jacksumner.com is the target |
| Reverse proxy | Caddy (Let's Encrypt auto) | Simpler than nginx; single binary |
| Secrets | `.env` locally → Azure Key Vault on cloud | Managed Identity → KV at runtime, no creds on disk |

## 4. Architecture invariants

These are baked-in. Don't propose changes without raising a flag.

1. **Risk gate is deterministic code, not LLM judgment.** The LLM may narrate
   *why* a decision was made (for the audit trail), but the cap math is
   plain Python, hot-reloadable from `config/risk.yaml`.
2. **Every order flows the same path**:
   `ProposedOrder → Risk Agent → Board approval (HITL) → DataExecAgent.place() → Broker`
   The HITL step is bypassed only when a strategy has `auto_execute: true` — currently
   off everywhere by default.
3. **Dry-run short-circuits at `DataExecAgent.place()`.** It builds a synthetic
   `FillEvent` (priced via `broker.quote()` for market orders, the limit price
   otherwise), tags `venue` with `:dry-run`, and never calls
   `broker.place_order()`. This is for validating the LIVE pipeline without
   placing real orders.
4. **Auto-execute is off by default** on every strategy. Flipping it per-strategy
   is a deliberate Board action.
5. **Webhook auth model**: shared secret in JSON body (constant-time compared)
   is the primary defense; IP allowlist is defense-in-depth. The lenient JSON
   parser tolerates prefix/suffix on bodies but logs a warning.
6. **Audit log captures everything.** Every alert (received, ignored, placed,
   rejected, errored) writes a row to `audit_event`. Future "silent failure"
   debugging depends on this — never add a code path that quietly drops events.
7. **Divisions are accounts; strategies are logic.** A strategy targets a
   division. One division can be the target of multiple strategies (e.g., a
   Coinbase Spot division can host both Lord Otter and manual orders).

## 5. Risk profile (`config/risk.yaml`)

| Cap | Global | Per-strategy overrides |
|---|---|---|
| Per-trade risk | 1.5% of equity | `coinbase_btc_donchian`: 100% (full sleeve sizing — strategy is 100%-in/out by design) · `lord_otter`: 5% · `manual_coinbase_spot`: 5% · `crypto_scalper`: 0.5% |
| Per-strategy daily loss | 3% (halts strategy for the day) | `coinbase_btc_donchian`: 100% (effectively disabled — see note below) · `lord_otter`: 2% |
| Per-account max DD | 15% (auto-flatten + global halt) | `coinbase_btc_donchian`: opt-out via `max_drawdown_disabled: true` (24mo backtest max DD 16.49% — the cap would have force-flattened mid-run; see [agents/risk.py](trading_corp/agents/risk.py) section 4) |
| Correlation cap (30d returns) | 0.7 between concurrent positions | none |
| Counter-trend size | 0.5× — **stocks only** (not options or crypto) | n/a |
| Vol scalar | `min(1, target/realized)` — **stocks only** | n/a |
| PMCC sizing | 1 contract per $25k equity per underlying | n/a |
| PMCC roll | 21 DTE or 50% profit | n/a |

## 6. Lord Otter strategy specifics  *(disabled 2026-05-09 — reference only)*

A TradingView-webhook-driven 3-min scalp strategy on `coinbase_spot`,
historically running alongside Market Cypher (4h/1D swing on the same
division). Both flipped to `enabled: false` on 2026-05-09 with the
Donchian pivot deploy. Files preserved for future BitUnix Futures
wiring per memory `trading_corp_bitunix_vision.md`. Some non-obvious
decisions captured here so we don't relitigate them if/when the
strategy is revived for futures.

> **⏸ Disabled 2026-05-09 (superseded the 2026-05-02 pause).** Walk-forward
> testing (commit `cd26a75`) showed the Otter+Cypher confluence approach
> on `coinbase_spot` had no demonstrable out-of-sample edge. Board pivoted
> the division to a single Coinbase BTC Donchian Channel Breakout strategy.
> Otter+Cypher webhook endpoints still accept POSTs (agents short-circuit
> on `enabled: false` before order construction); no Telegram pushes fire.
> Strategy logic + tier sizing below remains accurate as a reference for
> the eventual BitUnix Futures revival.

> **Path note:** as of 2026-05-02 these strategies live at
> `trading_corp/agents/strategies/{lord_otter,market_cypher}.py`, not
> the old `agents/divisions/` path. The rename reflects the corrected
> vocabulary (division = portfolio manager; strategy = how a division
> operates).

### Alert configuration that actually works

- **Operator: `Greater Than 0`** (with "Once Per Bar Close" trigger). Discovered
  empirically via the TV Data Window — Lord Otter plots all signals as 0
  inactive / 1.0 active (1.5 for CVD flips). `Crossing Up 0` is unreliable;
  `Crossing Down 0` never fires (signals don't go negative).
- **All 14 alerts use the same operator** (no asymmetry between bull/bear).
- **Symbol scope**: BTC/USD on Coinbase, 3m chart. Multi-symbol expansion
  planned but not yet wired.
- **Webhook body must be pure JSON** ideally, but server has a lenient parser
  that strips alert-name prefixes/suffixes if present. Fix the alert body
  for cleanliness; don't depend on the parser long-term.

### Visual-only signals (not alertable)

These render on the chart but Lord Otter doesn't expose them as alert
condition sources. Strategy ignores them rather than trying to alert.

- **Pink Box** (candle coloring) — replaced by Spoon Bull/Bear (Bull/Bear
  Divergence) as the arming source.
- **90m Bias Bar** (top strip) — replaced by Ribbon Buy/Sell Cross as proxy.
- **Ribbon exhaustion** (white edges) — Diamond tier no longer requires it.

### Conviction tier sizes (`config/strategies.yaml`)

| Tier | Size | Trigger |
|---|---|---|
| Diamond | 5.0% | Bias + arming + Otter + CVD flip + (Money Bag OR Large Water) |
| Premium | 3.0% | Bias + arming + Otter + CVD flip |
| Water Large | 3.0% | Bias + Large Water (multi-TF aligned, bypass arming) |
| Water Small | 2.0% | Bias + Small Water + (recent Otter or Money Bag) |
| Standard | 1.5% | Bias + Otter + CVD flip |
| Money Bag | 1.5% | Bias + Money Bag |
| Solo Otter | 0.75% | Bias + Otter alone |

Bear signals in long-only mode close held positions:
- Diamond bear → close 100%
- Premium / Water Large → close 75%
- Standard / Water Small / Money Bag → close 50%
- Solo Otter → close 25%

### Stop loss

- **Method**: trigger-bar swing. `stop = bar_low × (1 - 0.001)` for longs,
  `bar_high × (1 + 0.001)` for shorts (0.1% buffer beyond the swing).
- **Hard cap**: 0.5% of equity max dollar loss per trade. If technical stop
  is wider, qty is shrunk — never the stop widened.
- Phase 1.6 will add real ATR(14) and multi-bar swing detection from
  broker `fetch_ohlcv`. Currently the trigger-bar stop is the floor.

### Direction-aware cooldowns

- `last_entry_at` and `last_close_at` are tracked separately on `SymbolState`.
- An entry doesn't block a subsequent close (the bear signal AFTER an entry
  is the legitimate exit, not chop).
- A close doesn't block a subsequent entry on the opposite side.
- 180-second cooldown within the same path (entry-to-entry, close-to-close).

## 7. Broker phases

| Broker | Status |
|---|---|
| Robinhood | Live for PMCC. Stock + options orders work. |
| Fidelity | Browser automation (Playwright/Firefox). Phase A login session caching wired. Phase B/C session refresh logic is sensitive to UI changes. **Bot-blocked from Azure VM IP since 2026-05-01** (Akamai pre-JS layer rejects datacenter IPs); paper-fallback only. P1 backlog DEFERRED 2026-05-03 pending Plaid investigation. |
| Coinbase Spot | Phase A (read-only ccxt) DONE. Phase B (orders via ccxt `create_order`) DONE — uses `quote_size` for market buys (account-config quirk discovered empirically). |
| Coinbase Futures | Phase C — stub only. Will use `coinbase-advanced-py` SDK because ccxt's coinbase driver doesn't fully cover US FCM futures. UI shows STANDBY badge since 2026-05-03 16:25 UTC. |
| BitUnix Futures | Read-only Phase 1 SHIPPED 2026-05-03 17:54 UTC (`brokers/bitunix.py`): `snapshot()` + `quote()` against live BitUnix Futures API (`https://fapi.bitunix.com`), SHA256-double-sign auth (no passphrase), multi-margin-coin balance aggregation across USDT + USDC. Azure VM IP works against BitUnix (unlike Fidelity). Phase 2 paper-orders shipped via `PaperExecutionBroker` wrapping in same deploy. `place_order` / `cancel_order` raise `NotImplementedError` until Phase 4 (gated on stop-loss strategy + conviction → leverage map). **Phase 3.2 confluence score accumulator SHIPPED 2026-05-11 17:52→18:23 UTC** — multi-bar signal scoring with 34 factors + price-action + guards replaces single-bar tier classifier; division agent code in `trading_corp/agents/divisions/bitunix_futures_observer.py`, scorer in `trading_corp/agents/strategies/bitunix_confluence.py`, PA helpers in `trading_corp/data/bitunix_price_context.py`. Equity 2× bug FIXED 2026-05-10 04:19 UTC. See memories `trading_corp_bitunix_vision.md` + `trading_corp_bitunix_phase3_confluence_model.md`. |
| Polymarket | Read-only Phase 1+2a SHIPPED 2026-05-09/10 (`brokers/polymarket.py`, subclasses **`ReadOnlyBroker`** — first adapter to use the new ABC; `place_order` doesn't exist on the class, enforced by missing methods). `snapshot()` reads USDC balance via direct Polygon RPC `eth_call` + open positions via `data-api.polymarket.com`. `quote(symbol)` uses gamma-api slug→token_id then `clob.polymarket.com` last-trade-price. Path A wallet pattern: signer == funder (`signature_type=EOA`); single EOA holds USDC + signs orders (Phase 3+). Wallet live at `0x2FC7…ADA11` with $500 native USDC (verified on-chain). httpx concurrency cap (semaphore=6) + 429 backoff baked in. No EU egress proxy needed — 2026-05-09 smoke confirmed Polymarket's read APIs serve tc-prod-vm's US-east IP without geo-block (caveat: Phase 3 trade-placement may still hit write-path geo-checks; task #31 tracks the re-test). See memory `trading_corp_polymarket.md`. |
| Kalshi | Read-only Phase K1 SHIPPED 2026-05-10 22:29 UTC (`brokers/kalshi.py`, subclasses **`ReadOnlyBroker`** — second adapter on the ABC after Polymarket). Built on `pykalshi>=1.0.6` (MIT, async + sync, RSA-PSS auth handled cleanly, REST + WebSocket coverage). `snapshot()` reads `portfolio.get_balance()` (cents → dollars) + positions; `quote(symbol)` returns mid from `market.get_orderbook()`. RSA private key PEM materialized to a restricted-perms `/tmp/kalshi_*.pem` tempfile at connect, deleted on `disconnect()` (pykalshi takes a filesystem path, not bytes). KV-managed credentials (`KALSHI-API-KEY-ID` + `KALSHI-PRIVATE-KEY-PEM`). $499 USDC funded as of session end. **Two divisions on the same broker:** `kalshi_arbitrage` (structural tail+temporal+bucket strategies) and `kalshi_llm_arbitrage` (LLM-divergence strategy mirroring polymarket pattern). Phase K5+ adds `KalshiLiveBroker(Broker)` for live order placement; gated on observed paper PnL. See memory `trading_corp_kalshi.md`. **Lesson logged:** pykalshi's `get_all_series(limit=N)` silently fetches ALL pages despite the limit param; cap consumption at OUR layer + use `inter_call_delay_sec=0.15` between calls to stay under Kalshi rate limit. |

## 8. Production state (`as of 2026-05-10`)

System is live on Azure: VM `tc-prod-vm` (Standard_D2s_v3, eastus,
resource group `rg-shared-prod`), reachable at
https://trading.jacksumner.com (Caddy + Authelia, Let's Encrypt auto).
Webhook URLs (auth-bypassed for TradingView):
- `https://trading.jacksumner.com/webhook/tradingview/lord-otter`
- `https://trading.jacksumner.com/webhook/tradingview/market-cypher`

(Both endpoints still accept POSTs; the agents short-circuit on
`enabled: false` before order construction since the 2026-05-09
Donchian pivot. Files preserved for eventual BitUnix Futures
revival per memory `trading_corp_bitunix_vision.md`.)

App runs as `trading-corp.service` (systemd, wraps `xvfb-run` for
Fidelity's Playwright dependency). Restart takes 30-90s to reach "web
up" (Fidelity browser login is the long pole). SQLite DB at
`/home/azureuser/trading_corp/data/trading_corp.db`. Secrets from Azure
Key Vault `kv-tc-vtwbowt3wtkpy` via managed identity — no `.env` on prod.

Auto-execute is `false` on every strategy. Every approved order routes
through HITL via the **web app at `https://trading.jacksumner.com`**
(primary HITL surface as of the 2026-05-03 Board direction; mobile-
friendly htmx + Tailwind). Telegram is **notification-only** since the
2026-05-05 01:34 UTC slim-flag flip — short ping with deeplink to
`/approvals/{order_id}`, no order detail in the body. Web push (Phase E)
is the deferred next step. Five-broker status: Robinhood live (PMCC
reads + paper-execute on Individual; IRA + Joint surface in dashboard
without automated strategy yet), Coinbase Spot live (reads + Coinbase
BTC Donchian Channel Breakout strategy in paper-mode since 2026-05-09
02:53 UTC), BitUnix Futures live (read-only Phase 1, paper-orders via
`PaperExecutionBroker` wrap), Polymarket live (read-only ReadOnlyBroker
adapter, $500 USDC funded 2026-05-10 00:39 UTC; arbitrage strategy
disabled awaiting gamma-api tuning + Phase 2.5 backtest verdict),
Fidelity bot-blocked from Azure VM IP (paper-fallback only — Akamai
pre-JS layer rejects datacenter IPs; residential proxy is the unblock
path, deferred — Plaid investigation ongoing).

**`runbooks/deploy_log.md` is the single source of truth for what's
running on prod right now.** Prod has no git; the deploy log is how we
know what shipped when. Always check it before assuming a feature isn't
implemented.

## 9. Active blockers / known pain

1. **Fidelity browser automation is bot-blocked from Azure VM IP**
   (Akamai pre-JS layer flags datacenter IPs at network layer). Falls
   back to paper. Residential proxy is the documented fix; deferred.
2. **Otter/Cypher disabled on `coinbase_spot` 2026-05-09** — superseded
   the original 2026-05-05 pause/review. Walk-forward (commit `cd26a75`)
   showed no out-of-sample edge; division pivoted to Donchian. Otter +
   Cypher files preserved for eventual BitUnix Futures revival, agents
   `enabled: false`.
3. **Research firm's intraday TA capability isn't built.** The
   technical expert (`agents/research/experts/technical.py`) is
   yfinance-daily-bar with 5 indicators (RSI, MA cross, ATR, returns).
   Now mostly inert on prod since Otter+Cypher are disabled — their
   `TradeConfirmation` consults are no longer firing. The capability
   gap remains relevant if/when Otter+Cypher revive on BitUnix Futures.
4. **`auto_execute_caps` asymmetry between webhook and LangGraph
   paths** (see CLAUDE.md § 1). The TV webhook flow gates on a single
   `agent.auto_execute` bool; the LangGraph path uses the rich
   `auto_execute_caps` structure (VIX, LEAP-debit, black-sheep, daily
   aggregates). Harmonize before flipping any TV strategy to
   `auto_execute=true`.

## 10. Communication style (lessons learned, please respect)

These are direct preferences from the user, hard-won across many sessions.
Future sessions: please honor them.

- **Evidence first, speculation never.** When a symptom is reported, ask for
  logs, audit-DB rows, screenshots, or HTTP responses BEFORE proposing a
  cause. Saying "this is what's happening" without verification has burned
  this project's time repeatedly.
- **Closed-source systems require empirical tests.** When inspecting a paid
  Pine indicator, third-party API, or any system whose internals we can't
  read, propose tests (TV Data Window, alert duplicates with different
  operators, packet capture) BEFORE forming a hypothesis. Do not invent
  explanations for unknown internal behavior.
- **Admit mistakes plainly.** Don't grovel or over-apologize. Just correct,
  state the new evidence, and move forward. Don't continue defending a wrong
  hypothesis.
- **Commands and screenshots over prose.** When walking through a multi-step
  procedure (Azure portal, TradingView alert config, etc.), give numbered
  steps, exact button names, expected screen output. The user will paste
  back what they see; don't ask them to interpret.
- **Don't bury the lede.** When proposing changes, lead with the one-line
  recommendation. Justify after.
- **No flattery openings.** Don't start replies with "great question" /
  "excellent point" / etc. Start with the substantive answer.

## 11. Hard constraints

These are non-negotiable. If a user request seems to violate one, raise it
explicitly rather than silently sliding past.

- **Never recommend AWS, Hetzner, or other clouds.** Azure is the chosen
  path. The career-skills argument settled this; reopening it wastes time.
- **Never recommend native iOS over PWA.** Same reason — settled decision.
- **HITL approval is mandatory** until `auto_execute` is explicitly flipped
  for a specific strategy. Do not propose code paths that bypass this.
- **Risk caps are deterministic Python in `risk.py`.** LLM outputs do not
  override caps. Ever.
- **Never write secrets to git.** `.env` is gitignored. On Azure, use Key
  Vault + Managed Identity; the app fetches secrets at runtime, never
  stores them on disk.
- **Audit log every event.** Any new code path that emits or rejects an
  event MUST write a row to `audit_event`. Silent paths are forbidden by
  policy after the multi-day "where did the alerts go?" debugging incident.
- **Default to PAPER on startup.** Going LIVE requires `--live` flag plus
  the typed-LIVE confirmation prompt plus non-empty broker creds.

## 12. What's deferred (won't surprise you when raised)

These are real items, just not active. See BACKLOG.md for prioritized
detail; this list captures the long-shape items.

- **Otter/Cypher feature expansion**: paused 2026-05-02; superseded
  2026-05-09 when Coinbase BTC Donchian shipped to prod (see BACKLOG.md
  "✅ DONE — Coinbase BTC Donchian Phase 2"). Walk-forward (commit
  `cd26a75`) showed the Otter+Cypher confluence approach had no
  demonstrable out-of-sample edge. Both strategies flipped to
  `enabled: false` on `coinbase_spot`; files preserved for future
  BitUnix Futures wiring (memory `trading_corp_bitunix_vision.md`).
- **Research firm intraday TA**: harmonic patterns (3 drives, ABCD,
  etc.), Fibonacci (golden pocket / golden ratio), price-action
  structure (HH/HL/LH/LL), order blocks, divergences, vision-capable
  expert for pink-box image analysis. Decided post-2026-05-05.
- **PMCC strategy isolation**: `pmcc_robinhood.py` and
  `fidelity_options.py` still conflate division-level and strategy-level
  concerns. Future cleanup: extract strategy logic to
  `agents/strategies/` once a second strategy lands on either broker.
  See CLAUDE.md § Known sharp edges.
- **Phase 1.6 of Lord Otter**: real ATR/swing-pivot stops, profit-target
  tracking, win/loss feedback into halt counters. Subsumed by the
  Otter disable on 2026-05-09; revival path is BitUnix Futures, not
  Coinbase. Re-evaluate when BitUnix Phase 4 lands.
- **HITL approval flow → web app** (Board direction 2026-05-03):
  Approve / Reject / Modify moves to `trading.jacksumner.com`
  (mobile-friendly already). Telegram becomes notification-only with
  deeplink to the dashboard's approval page. Subsumes the prior
  "Telegram approval enrichment Phase 2" + "Paired-roll combination"
  items — pair-coalescing happens at render-time in the web UI, no
  LangGraph state-shape change. See BACKLOG.md
  "P0 — HITL approval flow lives in the web app".
- **Coinbase Futures wiring** (Phase C, requires `coinbase-advanced-py`).
- **Multi-tenant family expansion**: separate Azure environments per
  family member.
- **Real macro calendar fetcher** (FRED FOMC + BLS scraping into the
  existing `config/macro_calendar.yaml` format).
- **JSON `/api/v1/*` endpoints** (only if PWA isn't enough — currently
  not scoped).
- **Authentication beyond shared-secret** (Sign in with Apple or
  magic-link email, before any public-internet exposure beyond TV
  webhook IPs).

## 13. File-tree pointers

The most-read files when picking up context:

```
BACKLOG.md                          ← active work items by priority
config/strategies.yaml              ← strategy definitions, tier sizes
config/risk.yaml                    ← risk caps + per-strategy overrides
config/divisions.yaml               ← accounts ↔ broker mappings
config/macro_calendar.yaml          ← hand-maintained news halt calendar

trading_corp/main.py                ← entry point, CLI flags, agent wiring
trading_corp/graph/ceo_graph.py     ← LangGraph trade flow + HITL
trading_corp/agents/risk.py         ← deterministic risk caps
trading_corp/agents/data_exec.py    ← broker dispatch + dry-run
trading_corp/agents/divisions/      ← brokerage/account-level division wiring
   pmcc_robinhood.py                  PMCC division (mixes strategy logic — sharp edge)
   fidelity_options.py                Fidelity division (same conflation)
trading_corp/agents/strategies/     ← strategies inside coinbase_spot + polymarket_arbitrage divisions
   donchian_btc.py                    ACTIVE: Donchian Channel Breakout decision module (pure-function)
   coinbase_btc_donchian_agent.py     ACTIVE: 6h-poll agent wrapper (state persistence + ProposedOrder build)
   polymarket_arbitrage.py            ENABLED in paper-mode (Phase 2a, 2026-05-10): scan-driven LLM-divergence
                                      detector; direct Anthropic call per K=20 markets/cycle (warm-and-fan
                                      parallel); emits ProposedOrder when |LLM prob - implied prob| × 100 ≥ 10%.
                                      HITL-direct (no Board click); risk gate still load-bearing. Awaits Phase 2.5
                                      Backtester verdict before live-mode flip.
   _polymarket_prompts.py             Shared analyst-persona system prompt (~1554 tokens; clears 1024 cache threshold).
                                      Imported by polymarket_arbitrage today; copy_trading will share.
trading_corp/agents/polymarket_resolver.py  ← Two periodic loops feeding the dashboard data layer
                                      (SHIPPED 2026-05-10 03:28 UTC):
                                      - resolve_pending_round_trips: hourly. Walks would_have_placed audit rows,
                                        looks up gamma-api resolution, INSERTs polymarket_round_trips row per
                                        resolved market. INSERT OR IGNORE keyed on order_id (idempotent).
                                      - write_equity_snapshot: every 5 min. Calls broker.snapshot(), appends
                                        polymarket_equity_history row. Source for the equity curve.
   lord_otter.py                      DISABLED 2026-05-09: 3-min scalp (preserved for BitUnix Futures revival)
   market_cypher.py                   DISABLED 2026-05-09: 4h/1D swing (preserved for BitUnix Futures revival)
   bitunix_confluence.py              ACTIVE: Phase 3.2 score accumulator engine. Pure-function. Imported by
                                      BitunixFuturesObserver when `bitunix_futures.scoring.enabled=true`.
                                      Reuses FactorConfig/GuardConfig/AlertEvent/PriceContext dataclasses
                                      from btc_accumulator.py.
   btc_accumulator.py                 SCAFFOLD: dataclass + scoring helpers originally built for the
                                      coinbase_spot accumulator (now abandoned in favor of Donchian). Kept
                                      because bitunix_confluence imports its dataclasses. Pure-function,
                                      no side effects on import.
trading_corp/data/bitunix_price_context.py  ← Phase 3.2.2 helpers: session_vwap, higher_highs_lower_lows_4h,
                                      volume_above_20bar_avg, pct_change_in_window, _resample_to_4h,
                                      compute_price_context(bar_cache, ...). Consumed by the observer
                                      score path at evaluation time.
trading_corp/agents/divisions/bitunix_futures_observer.py  ← Phase 3.0/3.1/3.2 BitUnix division agent.
                                      Maintains bias + CVD state; appends every webhook to
                                      bitunix_signal_ledger; routes to either Phase 3.1 single-bar
                                      `_tier_for` OR Phase 3.2 `_score_and_maybe_propose` based on
                                      scoring_config.enabled flag.
trading_corp/agents/paper_trade_replay.py  ← Phase C replay loop. Walks paper_trade_record rows where
                                      result IS NULL, fetches OHLCV from the right venue per symbol
                                      (`_default_router_fetcher` dispatches `.P` suffix → BitUnix
                                      native kline, else → Coinbase ccxt — venue-aware as of
                                      2026-05-11 22:30 UTC). Classifier returns 'still_open' (no DB
                                      write) when neither TP/SL hit AND wall-clock elapsed <
                                      max_hold_seconds — prevents premature `expired` marking
                                      (caught 2026-05-11 23:00 UTC).
trading_corp/web/templates/partials/ira_dashboard.html  ← Robinhood IRA division UI: Covered Calls /
                                      Portfolio / Puts sections. Replaces the generic Holdings table
                                      + empty PMCC pairs section that the IRA page used to render.
trading_corp/web/templates/partials/ira_pair.html  ← PMCC-style click-to-expand row for covered calls.
                                      Left panel = shares; right panel = short call. Reuses
                                      `static/js/pair_list.js` (single-open accordion + loading-flash
                                      feedback) via shared `#pair-list` container id.
trading_corp/agents/research/       ← shared research-firm consultant (see CLAUDE.md § Research consultation)
trading_corp/brokers/                ← broker implementations
   base.py                            abstract ReadOnlyBroker + Broker(ReadOnlyBroker) interfaces
   paper.py                           PaperBroker + PaperExecutionBroker
   robinhood.py
   fidelity.py
   coinbase.py
   bitunix.py                         BitUnix Futures (read-only Phase 1; place_order raises until Phase 4)
   polymarket.py                      Polymarket prediction-markets (ReadOnlyBroker subclass — first to use
                                      the new ABC; place_order doesn't exist on the class. Phase 1+2a SHIPPED.)
trading_corp/web/                    ← FastAPI app
   app.py                             app factory + WebDeps dataclass
   routes.py                          dashboard routes
   webhooks.py                        TradingView webhook receiver
trading_corp/comms/                  ← user channels
   telegram_bot.py
   approval_format.py                 rich approval message builder
trading_corp/data/                   ← data sources
   feeds.py                           WS aggregator scaffold
   tradingview.py                     supplemental indicators (inert)
   macro_calendar.py                  news halt calendar lookup

scripts/test_lord_otter_webhook.py   ← synthetic alert harness
scripts/generate_pwa_icons.py        ← PWA icon generator from SVG
```

## 14. Glossary (short, project-specific)

- **Board** — the user (Jack). Approves or rejects orders via the
  web app at `trading.jacksumner.com` (primary HITL surface;
  mobile-friendly). Telegram is the notification channel that pings
  with a deeplink to the dashboard.
- **Division** — a brokerage × accounts portfolio manager. One per investing
  surface (`robinhood_pmcc`, `coinbase_spot`, `fidelity_options`). Future:
  Polymarket, crypto futures, etc.
- **Strategy** — the trade-decision logic running inside a division. One
  division can host multiple strategies (e.g. `coinbase_spot` runs both
  `lord_otter` and `market_cypher`). Lives in `agents/strategies/` (TV-driven)
  or inside the division module itself (PMCC, Fidelity — sharp edge).
- **Research firm** — shared LLM-driven consultant any division can call for
  cross-division knowledge work (`CandidateRecommendation`, `Thesis`,
  `PositionContext`, `TradeConfirmation`). NOT a decision-maker. See
  CLAUDE.md § Research consultation for when to call it.
- **HITL** — Human-in-the-loop. The Board-approval gate.
- **Tier** (Lord Otter / Market Cypher context) — conviction level for a
  signal cluster, drives sizing.
- **Arming** — Pre-trigger state set by Pink Box / Spoon, lasts N bars.
- **Black Sheep** — PMCC underlyings (TSLA, MSTR) that follow special rules
  (perpetual roll, never accept assignment).
- **Dry-run** — `--live --dry-run` mode that runs the full LIVE pipeline
  but skips `broker.place_order()`.

---

*Last meaningful update: 2026-05-15 — specialized Kalshi agents wave +
fractional-trading quote-read unblock. §1 division list extended with
4 new entries (kalshi_weather, kalshi_crypto, kalshi_copy_trading,
kalshi_sports_scout). All four were live but blocked from producing
fires by: (a) Kalshi flipping weather+crypto markets to
`fractional_trading_enabled: true` which dropped integer-cent quote
fields, (b) the new Tier-1 Kelly sizer multiplying against `$0` paper
broker equity, (c) the dashboard actor-whitelist missing the new
specialized agents. All three fixed today; first weather ProposedOrders
flowing through the full pipeline + visible on dashboard. Memory
`kalshi_market_structure.md` updated with the fractional-trading caveat;
memory `trading_corp_kalshi.md` carries the full per-deploy detail. Earlier same day:

Last meaningful update: 2026-05-11 — Kalshi sprint (K1 → K6.1) wrap.
§1 division list extended (kalshi_arbitrage, kalshi_llm_arbitrage; group
renamed Polymarket → Prediction Markets). §7 broker phases table extended
with the Kalshi entry. Six-broker count now includes Kalshi. Memory
`trading_corp_kalshi.md` carries the full Kalshi phasing; new memory
`anthropic_concurrent_connections.md` captures the Semaphore lesson from
the K6.1 first-scan 429 incident. Earlier same day:

Last meaningful update: 2026-05-10 — Polymarket Phase 1+2a wrap.
§1 division list extended (polymarket_arbitrage real, polymarket_copy_trading
paper-fallback STANDBY, both grouped into new Polymarket investment
type). §7 broker phases table extended with the Polymarket entry +
ReadOnlyBroker ABC note; BitUnix entry annotated with the P2 transfer
double-count bug. §8 production-state date refreshed; four-broker →
five-broker. §13 file tree extended with brokers/polymarket.py +
agents/strategies/polymarket_arbitrage.py + _polymarket_prompts.py.
Earlier same day (2026-05-09) — Polymarket Phase 1+2a shipped (broker
+ scanner + risk caps + scheduler), Coinbase BTC Donchian division-
detail UI cleanup + balance-change tracking (state-as-source-of-truth).
Prior major update 2026-05-02 — vocabulary realignment (divisions vs
strategies), research firm consultation rule codified.*
