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
`robinhood_ira`, `robinhood_joint`, `coinbase_spot` (running
`coinbase_btc_donchian` — 6h Donchian Channel Breakout, paper-mode,
shipped 2026-05-09 02:53 UTC; replaced the prior Otter+Cypher confluence
which failed walk-forward), `coinbase_futures` (`STANDBY` — kept as
failover), `bitunix_futures` (read-only Phase 1 SHIPPED 2026-05-03;
Phase 4 live ahead), `fidelity_joint`, and `fidelity_401k` (Fidelity
paper-fallback — bot-blocked from Azure VM IP, P1 DEFERRED 2026-05-03
pending Plaid investigation; `fidelity_individual` deactivated same day).
Dashboard groups them into Individual / Crypto / Retirement (UI reorg
shipped 2026-05-03 16:25 UTC; the `coinbase_spot` tile shows a
CASH/BTC badge + state-aware Donchian dial — shipped 2026-05-09 03:30 UTC).
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
| Fidelity | Browser automation (Playwright/Firefox). Phase A login session caching wired. Phase B/C session refresh logic is sensitive to UI changes. |
| Coinbase Spot | Phase A (read-only ccxt) DONE. Phase B (orders via ccxt `create_order`) DONE — uses `quote_size` for market buys (account-config quirk discovered empirically). |
| Coinbase Futures | Phase C — stub only. Will use `coinbase-advanced-py` SDK because ccxt's coinbase driver doesn't fully cover US FCM futures. |

## 8. Production state (`as of 2026-05-02`)

System is live on Azure: VM `tc-prod-vm` (Standard_D2s_v3, eastus,
resource group `rg-shared-prod`), reachable at
https://trading.jacksumner.com (Caddy + Authelia, Let's Encrypt auto).
Webhook URLs (auth-bypassed for TradingView):
- `https://trading.jacksumner.com/webhook/tradingview/lord-otter`
- `https://trading.jacksumner.com/webhook/tradingview/market-cypher`

App runs as `trading-corp.service` (systemd, wraps `xvfb-run` for
Fidelity's Playwright dependency). Restart takes 30-90s to reach "web
up" (Fidelity browser login is the long pole). SQLite DB at
`/home/azureuser/trading_corp/data/trading_corp.db`. Secrets from Azure
Key Vault `kv-tc-vtwbowt3wtkpy` via managed identity — no `.env` on prod.

Auto-execute is `false` on every strategy. Every order is a paper-mode
`would_have_placed` row + Telegram push to the Board. Three-broker
status: Robinhood live (PMCC reads + paper-execute), Coinbase Spot live
(reads), Fidelity bot-blocked from Azure VM IP (paper-fallback only —
Akamai pre-JS layer rejects datacenter IPs; residential proxy is the
unblock path, deferred).

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
trading_corp/agents/strategies/     ← strategies inside coinbase_spot division
   donchian_btc.py                    ACTIVE: Donchian Channel Breakout decision module (pure-function)
   coinbase_btc_donchian_agent.py     ACTIVE: 6h-poll agent wrapper (state persistence + ProposedOrder build)
   lord_otter.py                      DISABLED 2026-05-09: 3-min scalp (preserved for BitUnix Futures revival)
   market_cypher.py                   DISABLED 2026-05-09: 4h/1D swing (preserved for BitUnix Futures revival)
trading_corp/agents/research/       ← shared research-firm consultant (see CLAUDE.md § Research consultation)
trading_corp/brokers/                ← broker implementations
   base.py                            abstract Broker interface
   paper.py                           PaperBroker + PaperExecutionBroker
   robinhood.py
   fidelity.py
   coinbase.py
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

*Last meaningful update: 2026-05-09 — Coinbase BTC Donchian Phase 2
shipped to prod, Otter+Cypher disabled (files preserved for BitUnix
revival), home-tile CASH/BTC badge + state-aware Donchian dial added.
Prior major update 2026-05-02 — vocabulary realignment (divisions vs
strategies), research firm consultation rule codified.*
