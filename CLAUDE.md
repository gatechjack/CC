# CLAUDE.md

Loaded into every Claude Code session for this repo. Read it. Honor it.

## What this is

Trading Corp is a multi-agent Python system that places **real-money trades**
through Robinhood (PMCC options across Individual / IRA / Joint accounts),
Coinbase (BTC spot live, futures stub), and Fidelity (paper-fallback only —
currently bot-blocked from the Azure VM IP). It runs in production on Azure
VM `tc-prod-vm` at https://trading.jacksumner.com behind Caddy + Authelia.
Every strategy is currently `auto_execute: false` — proposals route to the
Board (Jack) via Telegram for approval before any live order placement.

## STOP AND READ — non-negotiable invariants

Before you change anything:

1. **Risk gate is a single chokepoint.** Every order, regardless of
   source, passes through `RiskAgent.evaluate()` in
   [agents/risk.py](trading_corp/agents/risk.py). No code path may
   bypass it.
2. **Audit log writes BEFORE every decision branch, not after.** Past
   silent-failure incidents (alerts disappearing) cost days. The
   dashboard renders snapshots; `audit_event` captures intent. If they
   disagree, audit wins.
3. **Paper is the default on every startup.** `--live` requires
   interactive confirmation AND populated broker creds
   (`assert_live_ready` in [utils/secrets.py](trading_corp/utils/secrets.py)).
4. **Risk caps are deterministic Python.** LLMs may *narrate* verdicts
   (`RiskAgent.narrate`); they may not produce them.
5. **The TradingView webhook → broker path is handling real capital.**
   Don't refactor without explicit, in-session approval.

Full architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (four
organizing principles + decision pipeline + domain model + state model
+ design decisions). Current-state context, risk profile, hard
constraints: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

---

## 1. Working agreements

### Risk + execution

- **Single risk chokepoint.** New signal sources route through
  `RiskAgent.evaluate()` like every other source. There is no second
  risk evaluator.
- **Deterministic-then-narrate.** Risk caps are Python in
  [agents/risk.py](trading_corp/agents/risk.py). LLM narration is
  optional and never overrides the verdict. Same for sizing math, halt
  conditions, tier classification.
- **Mode flag is process-wide; `auto_execute` is per-strategy.** A
  `--paper` process cannot place real orders regardless of config. A
  `--live` process places only for divisions where `auto_execute=true`
  AND risk approves AND (if HITL on) Board approves.
- **HITL approval is the default for any new division.**
  `auto_execute: true` is earned per-strategy after observed paper
  performance, not granted by default.
- **Harmonize the webhook auto-execute gate before any TV division
  flips to `auto_execute: true`.** Today the webhook path
  ([web/webhooks.py](trading_corp/web/webhooks.py)) gates on a single
  `agent.auto_execute` bool, while the LangGraph path
  ([graph/ceo_graph.py](trading_corp/graph/ceo_graph.py)) reads the
  full `auto_execute_caps` structure (require_approval_for, max-dollar
  caps, daily aggregates, VIX gate, LEAP-debit gate). Letting an Otter
  or Cypher division flip to auto without first wiring the rich gate
  into the webhook path would silently bypass the safety net. Don't
  flip the bool until the gates are equivalent.
- **`auto_execute_caps.require_approval_for` triggers are
  load-bearing.** Today: opening a new LEAP, closing any LEAP, any
  action on black-sheep symbols, any action when VIX > 30, rolling for
  debit > 5% of long LEAP value, neutral-strategy open/close. Removing
  or weakening any trigger requires a memo from the Board recording:
  (a) what incident or class of failure it was originally protecting
  against, (b) why that protection is no longer needed, (c) what
  observation would tell us the relaxation was wrong (so we know what
  to watch for after lifting it). No memo, no relaxation.
- **VIX-feed-unavailable is fail-safe to Board.** If `get_vix()`
  returns None, the order escalates regardless of caps. Don't replace
  this with a cached default.
- **Roll-debit-vs-LEAP-value gate uses a cache populated by
  `PMCCAgent.detect_existing_legs`.** Stale or missing → fail-safe
  (Board). Don't loosen this.

### State + audit

- **`audit_event` is the source of truth.** Written via `LoggerAgent`
  before every decision branch.
- **Required tags on webhook events:** every `webhook_received`,
  `alert_ignored`, `webhook_rejected`, `would_have_placed`,
  `agent_error` must include `strategy` and `division` keys. The
  dashboard's per-division activity rail
  (`_query_division_activity`) matches on these.
- **Any state that affects future trade decisions must be in SQLite.**
  Process memory is caching only. Use the `agent_state` table for new
  latches (generic `(agent, key) → JSON` with `updated_ts`). Each
  latch needs a staleness gate (Otter bias = 12h, Cypher bias/sommi =
  3d). Don't introduce new tables without a migration plan.
- **Stale latches are deleted on restore, not patched with defaults.**
  See `_restore_bias_state` pattern in
  [agents/strategies/lord_otter.py](trading_corp/agents/strategies/lord_otter.py).

### Code path isolation

- **Read-only divisions have no order-placement code at the adapter
  level.** Read-only is enforced by missing methods, not runtime
  flags. New read-only adapters subclass a `ReadOnlyBroker` ABC that
  exposes `connect` / `disconnect` / `snapshot` / `quote` only. There
  is no `place_order` to call. (Migration: `FidelityBroker` predates
  this rule and still subclasses the full `Broker` ABC — see § Known
  sharp edges.)
- **Broker credentials never enter agent prompts or LLM context.**
  Only [utils/secrets.py](trading_corp/utils/secrets.py),
  [agents/data_exec.py](trading_corp/agents/data_exec.py), and broker
  adapters touch them. The redaction filter (`RedactingFilter`) is on
  the root logger.
- **The existing real-money pipelines must not be modified,
  refactored, or "improved" without explicit, in-session human
  approval.** New functionality is added in parallel. The two
  real-money paths today:
  - `TradingView → web/webhooks.py → agent.on_alert → risk.evaluate → place_or_notify`
  - `PMCC scan / Telegram cmd → graph/ceo_graph.py LangGraph → risk_node → approval_node → execute_node`

  Both touch the same `RiskAgent` and same audit log; the
  orchestration differs by design (see § 2 and § Known sharp edges).

### Process + safety

- **Single-instance lock.** `data/trading_corp.pid` is claimed
  atomically via `O_EXCL`. Stale-PID reaping is one-shot. Don't
  bypass `_acquire_lock()` — use a different DB path for tests.
- **`broker_fallback_to_paper` uses `starting_equity=0.0` because $0
  is a failure signal, not a default.** When a real broker connect
  fails, the system replaces it with
  `PaperBroker(starting_equity=0.0)` and writes a
  `broker_fallback_to_paper` audit event. The dashboard then renders
  $0 equity for that division, which is the visible signal that the
  division is down. A non-zero default would mask the failure as
  phantom equity. Don't change this.
- **Webhook flow audits inbound BEFORE agent dispatch.** See
  [web/webhooks.py:220](trading_corp/web/webhooks.py). If the agent
  throws, we still have a record.

### HITL surface direction (Board, 2026-05-03)

- **The web app at `https://trading.jacksumner.com` is the primary
  HITL surface.** Approve / Reject / Modify decisions belong in the
  dashboard. The dashboard is mobile-friendly (htmx + Tailwind
  responsive layout); on a phone it's the canonical UI.
- **Telegram is a notification-only channel.** When something needs
  Board attention (approval, fill notification, error, halt), Telegram
  emits a short ping with a deeplink to the relevant page on the
  dashboard. Telegram messages do not carry order detail, do not
  accept Approve/Reject replies, do not run inline keyboards. The
  dashboard is what the Board reads + acts in.
- **State today (post-B.4 flip 2026-05-05 01:34 UTC):** slim
  Telegram body is the live default — short ping with deeplink to
  `https://trading.jacksumner.com/approvals/{order_id}`. Set on
  prod via `Environment=TELEGRAM_NOTIFICATION_ONLY=true` in the
  systemd drop-in `/etc/systemd/system/trading-corp.service.d/override.conf`.
  Rich-format code (`comms/approval_format.py`) remains in the
  binary as dead-on-prod fallback; inline keyboard remains as
  belt-and-suspenders (resolves the same `PendingApprovalRegistry`,
  first-decision-wins). Don't enrich Telegram messages further;
  new HITL UX work goes into the web app. Phase E (PWA + web push)
  is the next deferred phase — when it lands, Telegram can be
  dropped entirely.
- **No new LangGraph TradeFlowState changes for HITL.** Pair-coalescing
  for paired roll orders happens at render time in the web app (group
  by `pmcc_pair_id`), not by extending `TradeFlowState`. The web-app
  POST endpoint resumes the existing `interrupt()` per order with the
  same `BoardDecision` shape `request_board_approval` returns today;
  graph internals unchanged. This deliberately avoids the §6 trigger
  for "Change the LangGraph checkpointer or `TradeFlowState` shape."
- **Web push notifications are deferred (Phase E in the BACKLOG entry).**
  When the dashboard adds PWA + push subscription flow, Telegram can
  be dropped or kept as belt-and-suspenders. Until then, Telegram is
  the bridge channel.

### Research consultation

The research firm
([agents/research/](trading_corp/agents/research/)) is a knowledge-work
consultant that any division can call. It is **not** a decision-maker.
This rule was codified 2026-05-02 after a vision realignment that found
the firm had been over-scaffolded relative to its actual cross-division
value; protect it from re-expansion.

- **A division calls research when ALL of:**
  - The question requires cross-source LLM synthesis — not deterministic
    rule application.
  - The latency budget tolerates 5–60s for an answer.
  - Multiple divisions could plausibly ask the same question, OR the
    answer is high-value enough to justify research overhead for one.
- **A division does NOT call research for:**
  - Per-alert tier / sizing / stop / direction decisions. Strategy code.
  - Mechanics the strategy itself can answer — RSI, ATR, breach %,
    position-size formula, halt conditions, cooldown windows. Strategy
    code, no LLM.
  - Anything in a sub-second loop.
- **Research's surface is the four structured products:**
  `CandidateRecommendation`, `Thesis`, `PositionContext`,
  `TradeConfirmation`. New product types require explicit Board approval
  before scaffolding — adding products has been the failure mode.
- **Today only PMCC's `research_on_demand` candidate path is doing
  real cross-division knowledge work.** TradeConfirmation consults
  from Otter/Cypher are scaffolded but the underlying intraday-TA
  expert isn't built — treat those consults as ceremonial (fail-open
  no-ops most of the time) until either the TA capability lands or
  the consult surface is removed. Don't add features that depend on
  TradeConfirmation actually returning useful verdicts for crypto.
- **The decision rule, applied retroactively, would have flagged
  per-alert TradeConfirmation as wrong-fit and saved a phase of work.**
  Apply it forward when scoping new division/research interactions.
- **Webhook risk gate uses an equity fallback (`100_000.0`) when
  broker snapshot fails.** This is a known soft-fail — risk-cap math
  runs on a placeholder equity rather than rejecting the alert.
  Visible in audit via the snapshot-failure log. Don't tighten or
  loosen without an audit-trail review.
- **Schema changes to `proposed_order`, `audit_event`, `position`,
  `account_state`, `strategy_state`, `agent_state` require explicit
  approval AND a migration plan.** The `extra: dict` field on
  `ProposedOrder` (→ `extra_json`) is the escape hatch for
  strategy-specific data — use it before proposing schema changes.
- **Before any deploy-adjacent task, verify prod state.** There is no
  git on the prod VM, and `BACKLOG.md` describes intent (what we want),
  not state (what's shipped). Recurring failure mode pre-2026-05-02:
  starting work on a feature that already shipped — bundled into a
  prior bulk-track commit, scaffolded forward-compat in an earlier
  phase, or implemented before the `BACKLOG.md` entry was retired.
  Mitigation:
  1. **Read [runbooks/deploy_log.md](runbooks/deploy_log.md) first.**
     It's the single source of truth for what's running on prod right
     now. Look for `**Features shipped:**` lines that match your task.
  2. **md5-diff target files against prod** before writing any new
     code on a feature you can't 100% verify is unimplemented. Files
     that MATCH are likely already done — investigate before assuming
     new code is needed:
     ```bash
     for f in <files>; do
       l=$(md5sum "$f" | awk '{print $1}')
       p=$(ssh azureuser@trading.jacksumner.com "md5sum /home/azureuser/trading_corp/$f 2>/dev/null | awk '{print \$1}'")
       [ "$l" = "$p" ] && echo "MATCH $f" || echo "DIFFER $f"
     done
     ```
  3. **After every successful deploy, append an entry to
     [runbooks/deploy_log.md](runbooks/deploy_log.md)** per the
     template at the top of that file — including `**Features
     shipped:**` and `**Notable code changes:**` lines that future-you
     can grep for. This is the load-bearing step that prevents the
     next session from re-doing the work.

---

## 2. Architecture summary

The four organizing principles (verbatim — see
[docs/ARCHITECTURE.md § 1](docs/ARCHITECTURE.md)):

1. **Layered, with strict downward dependencies.**
   `web/ → agents/ → brokers/ → persistence/ → utils/`. Reverse
   imports are bugs.
2. **Divisions, not "the bot".** Each broker × strategy combo is its
   own division (`agents/divisions/*.py` + `config/divisions.yaml`).
   Independently configured, halted, risk-gated.
3. **Paper-default, risk-gated, HITL on every live order until trust
   earned.** Three orthogonal switches (mode flag, `auto_execute`,
   risk verdict) — any of them blocking = no trade.
4. **Deterministic caps + LLM narration, not LLM judgment.** Risk
   caps are Python (reproducible, testable). LLMs only narrate.

**Decision pipeline** (input → final state):

```
INPUT
  TV alert · user Telegram cmd · cron scheduler · web request
       ↓
INGEST   (web/webhooks.py · web/routes.py · comms/telegram_bot.py)
  auth · normalize · audit inbound
       ↓
STATE UPDATE   (agents/divisions/*.py)
  record_alert · _refresh_state_from_signal · persist latches
       ↓
DECISION GATES
  enabled? · halt? · cooldown? · chop? · classify_tier · modifiers
       ↓
ORDER CONSTRUCTION
  notional = equity × tier_size_pct · qty = notional/price · stop · max-loss shrink
       ↓
RISK GATE   (agents/risk.py)  ← single chokepoint
  approve / reject / resize
       ↓
auto_execute=false → HITL approval (web app at trading.jacksumner.com,
                     Telegram = notification ping with deeplink to
                     the approval page) + would_have_placed audit
auto_execute=true  → broker.place_order + Telegram fill notify
```

**Deliberate disclosure: the orchestration that wraps the gate has two
shapes today.** The pipeline is conceptually one path, but in code:

- TradingView webhooks call `risk_agent.evaluate()` *inline* in
  [web/webhooks.py](trading_corp/web/webhooks.py) and dispatch
  place-vs-notify directly.
- PMCC scans, demo orders, and Telegram-driven flows go through the
  LangGraph `build_trade_graph()` in
  [graph/ceo_graph.py](trading_corp/graph/ceo_graph.py), which adds
  HITL `interrupt()` checkpointing and the richer
  `auto_execute_caps` evaluation.

Both call the same `RiskAgent` and write the same audit kinds. New
TV-driven divisions mirror the webhook flow's shape, not the graph's.
See § Known sharp edges for the asymmetry's safety implication.

For full detail (module breakdown, domain model, state model, design
decisions), read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 3. Module map

| Module | Purpose |
|---|---|
| [trading_corp/main.py](trading_corp/main.py) | Entrypoint. Mode selection, deps wiring, PID lock, idle loop. |
| [trading_corp/web/](trading_corp/web/) | FastAPI app: dashboard routes, TV webhooks, snapshot data shaping, htmx templates. |
| [trading_corp/comms/](trading_corp/comms/) | Telegram bot, CLI fallback, rich approval-message builder. |
| [trading_corp/graph/](trading_corp/graph/) | LangGraph CEO trade flow + `interrupt()` for HITL. Uses `SqliteSaver` checkpointer. |
| [trading_corp/agents/](trading_corp/agents/) | Decision-makers: CEO, Risk, Trend, Backtester, Portfolio, DataExec, Logger. |
| [trading_corp/agents/divisions/](trading_corp/agents/divisions/) | Brokerage/account-level division wiring. Today: `pmcc_robinhood.py`, `fidelity_options.py`. These mix portfolio-management with strategy logic — see § Known sharp edges. |
| [trading_corp/agents/strategies/](trading_corp/agents/strategies/) | Strategy implementations. Today: `coinbase_btc_donchian_agent.py` + `donchian_btc.py` (6h Donchian breakout — poll-driven via `_scheduled_donchian_loop` in `main.py`; ACTIVE on `coinbase_spot` since 2026-05-09 02:53 UTC); `bitunix_confluence.py` (Phase 3.2 multi-bar score accumulator engine — pure-function, imported by `BitunixFuturesObserver` when `bitunix_futures.scoring.enabled=true`); `btc_accumulator.py` (scaffold dataclasses + helpers — originally for the abandoned coinbase_spot accumulator, kept because `bitunix_confluence` imports its dataclasses); polymarket + kalshi scanners. Disabled-but-preserved: `lord_otter.py` (3m scalp), `market_cypher.py` (4h/1D swing) — both `enabled: false` since the 2026-05-09 Donchian pivot, but their webhooks NOW feed the BitUnix score accumulator (their `enabled: false` only stops them from acting on `coinbase_spot`). Carved out of `agents/divisions/` 2026-05-02 to match the vision (division = portfolio manager; strategy = how that division operates). |
| [trading_corp/agents/research/](trading_corp/agents/research/) | Shared knowledge-work consultant. See § Research consultation for when to call it. |
| [trading_corp/brokers/](trading_corp/brokers/) | Adapters (see below). |
| [trading_corp/persistence/](trading_corp/persistence/) | SQLite engine, dataclass models, LangGraph checkpointer, `agent_state` kv store. |
| [trading_corp/data/](trading_corp/data/) | WS aggregator skeleton, macro calendar lookup, TradingView WS supplement, `live_bar_cache.py` (BitUnix 3m kline poller), `bitunix_price_context.py` (Phase 3.2.2 — VWAP / HH-LL_4h / volume / pct_change helpers consumed by the score path). |
| [trading_corp/utils/](trading_corp/utils/) | Secrets loader (env + Azure Key Vault), time helpers, divisions config loader, market-data helpers (yfinance, VIX). |
| [config/](config/) | Hot-reloadable knobs (mtime-watched on most agents): `risk.yaml`, `strategies.yaml`, `agents.yaml`, `divisions.yaml`, `macro_calendar.yaml`. |
| [runbooks/](runbooks/) | **Operational playbooks. No-edit by default — see § 6.** |
| [infra/](infra/) | Bicep IaC for Azure (`main.bicep`). **Edit-with-deploy-plan — see § 6.** |
| [scripts/](scripts/) | Utilities: webhook test harness, PWA icon gen, KV upload. |
| [docs/](docs/) | Architecture reference (`ARCHITECTURE.md`). |

### Current divisions and the strategies they run

A division is a (brokerage × account) portfolio manager. A strategy is
how a division decides what to trade. One division can run multiple
strategies. This vocabulary was clarified 2026-05-02 — earlier code +
docs sometimes called Otter and Cypher "divisions"; that was wrong.
They are strategies inside the `coinbase_spot` division.

| Division | Brokerage / accounts | Strategies running there | Status |
|---|---|---|---|
| `robinhood` (`pmcc_robinhood.py`) | Robinhood Individual (PMCC) + IRA (stocks/ETFs + weekly covered calls — see [BACKLOG.md "Robinhood IRA drilldown"](BACKLOG.md)) + Joint via `account_filter` | PMCC on Individual today; IRA + Joint surface in dashboard but no automated strategy yet | Live broker reads, paper-execute, HITL on every order |
| `coinbase_spot` | Coinbase spot | **Coinbase BTC Donchian** (6h Donchian Channel Breakout, [strategies/coinbase_btc_donchian_agent.py](trading_corp/agents/strategies/coinbase_btc_donchian_agent.py) + decision module [strategies/donchian_btc.py](trading_corp/agents/strategies/donchian_btc.py)) — pivot landed 2026-05-09 02:53 UTC. 100%-in/out CASH↔BTC, long-only, paper-mode (`auto_execute: false`). Lord Otter + Market Cypher set to `enabled: false` same deploy (files preserved per `trading_corp_bitunix_vision.md` for future BitUnix wiring). | Live: poll-driven 6h scheduler, broker reads, paper-execute, HITL on every order. |
| `coinbase_futures` | Coinbase futures | None today (kept as failover) | UI shows `STANDBY` badge (deploy 2026-05-03 16:25 UTC). Order path is still active in code today; behavioral disable is a follow-up. |
| `bitunix_futures` | BitUnix Futures (USDT + USDC margined) | **bitunix_futures division agent** ([divisions/bitunix_futures_observer.py](trading_corp/agents/divisions/bitunix_futures_observer.py)) running the **Phase 3.2 confluence score accumulator** ([strategies/bitunix_confluence.py](trading_corp/agents/strategies/bitunix_confluence.py)). Receives Otter + Cypher webhooks (fanned from `web/webhooks.py`); each signal appends to `bitunix_signal_ledger` with per-factor TTL; scorer sums weights of all live (TTL-filtered, deduped by signal_name) signals + price-action factors (VWAP / HH-LL_4h / volume / pct_change computed live from `data/bitunix_price_context.py` against the BitUnix 3m bar cache) + applies guard penalties → maps net_score to PREMIUM (≥12) / STANDARD (≥8) / SKIP. 30-min per-side cooldown gate. Order proposer with structural stop, 2R TP, 0.5%/trade effective-risk cap, 3% daily-loss kill-switch. **auto_execute=true** within those caps (risk gate IS the HITL gate, per Board). Live dashboard panel at `/division/bitunix_futures` (partial `partials/bitunix_score_panel.html`) surfaces live score + contributions + PA flags + cooldowns + recent fires. Phase 3.1 single-bar `_tier_for` retained in-code behind `scoring.enabled` flag for fast rollback. | Read-only Phase 1 SHIPPED 2026-05-03 17:54 UTC. Phase 3.0/3.1/3.2a SHIPPED 2026-05-10. **Phase 3.2 confluence score accumulator SHIPPED 2026-05-11 17:52→18:23 UTC.** First STANDARD SELL fired 2026-05-11 18:00:07 UTC. Live `place_order` raises `NotImplementedError` until Phase 4 (gated on stop-loss strategy + conviction → leverage map). Phase 3.2b multi-leg scale-out queued. See memories `trading_corp_bitunix_vision.md` + `trading_corp_bitunix_phase3_confluence_model.md`. |
| `polymarket_arbitrage` | Polymarket prediction markets (single dedicated EOA wallet on Polygon mainnet, signer == funder) | **polymarket_arbitrage** ([strategies/polymarket_arbitrage.py](trading_corp/agents/strategies/polymarket_arbitrage.py)) — scan-driven LLM-divergence detector. Pulls open Polymarket markets via gamma-api, deterministic-filters by volume/spread/ttr/implied-prob, **K=20 survivors per cycle (warm-and-fan parallel)** get a calibrated YES probability via direct Anthropic call (NOT through Research firm; shared analyst-persona system prompt at `_polymarket_prompts.py` is prompt-cached). Emits ProposedOrder when `\|LLM prob - implied prob\| × 100 ≥ 10%`. **HITL-direct** (no per-trade Board click; risk gate still load-bearing). Activity rail + LLM analysis right-rail render rich tiles per audit row. **Dashboard data layer** (`agents/polymarket_resolver.py`): hourly resolver writes `polymarket_round_trips` from would_have_placed + gamma-api resolution; 5-min equity snapshot writes `polymarket_equity_history`. | Read-only Phase 1+2a SHIPPED 2026-05-09/10. Wallet live ($500 USDC). Strategy **`enabled: true` in paper-mode** 2026-05-10 02:05 UTC. Awaits Phase 2.5 Backtester verdict (≥30 resolved trades) for live-mode flip. See `~/.claude/.../memory/trading_corp_polymarket.md`. |
| `polymarket_copy_trading` | Same Polymarket wallet (Phase 4+ will swap from `broker: paper` to `broker: polymarket`) | None today | UI shows `STANDBY` badge as a Phase 4+ placeholder for the future copy-trader strategy. Same investment-type group as arbitrage. |
| `fidelity` (`fidelity_options.py`) | Fidelity Joint + 401(k) (Individual deactivated 2026-05-03 — `enabled: false` in YAML) | Fidelity options | Bot-blocked from Azure VM IP — paper-fallback only. P1 backlog **DEFERRED 2026-05-03** pending Plaid investigation. |

### Brokers (`brokers/`)

| Adapter | Capability |
|---|---|
| `paper.py:PaperBroker` | In-memory account, deterministic fills. Default + universal fallback. |
| `paper.py:PaperExecutionBroker` | Wraps a real read-only broker: real snapshots, simulated fills. Used in PAPER mode for any live-cred division. |
| `robinhood.py:RobinhoodBroker` | `robin_stocks`, multi-account via `account_filter`, persistent session pickle. |
| `coinbase.py:CoinbaseBroker` | ccxt-based. Spot live, futures stub. Separate API keys per portfolio. |
| `bitunix.py:BitunixBroker` | BitUnix Futures, async httpx, SHA256-double-sign auth (no passphrase). Read-only Phase 1: `snapshot()` + `quote()` only; `place_order` / `cancel_order` raise `NotImplementedError` until Phase 4. Multi-margin-coin balance aggregation (USDT + USDC summed; BTC/ETH-margined deferred). |
| `polymarket.py:PolymarketBroker` | Polymarket prediction markets, async httpx. **First adapter to subclass `ReadOnlyBroker` ABC — `place_order` does not exist on the class** (static type-system enforcement of read-only, not runtime flag; CLAUDE.md "Code path isolation" rule). `snapshot()` reads USDC balance via direct Polygon RPC `eth_call(USDC.balanceOf)` + open positions via `data-api.polymarket.com`. `quote(symbol)` parses `slug:outcome`, fetches token_id from gamma-api, last-trade-price from CLOB. Stub mode if creds missing. httpx concurrency cap (semaphore=6) + 429 backoff with jitter. Phase 1+2a SHIPPED. Phase 3 (live order placement) will land as a separate `PolymarketLiveBroker(Broker)` class with signing. |
| `fidelity.py:FidelityBroker` | Playwright/Firefox browser automation. Currently bot-blocked from Azure VM IP. **Subclasses full `Broker` ABC — predates the read-only-by-ABC rule; see § Known sharp edges.** |

New read-only adapters: subclass `ReadOnlyBroker` (no `place_order`),
not the full `Broker`. `PolymarketBroker` is the first / canonical
example. The ABC was extracted as part of Polymarket Phase 1 (commit
`d7cbea2`, 2026-05-09 20:13 UTC).

---

## 4. Domain vocabulary

Defined in [persistence/models.py](trading_corp/persistence/models.py)
unless noted.

- **Division** — one (broker × account × strategy) tuple. Wired via
  [config/divisions.yaml](config/divisions.yaml). Has its own halt
  state, risk caps, broker handle.
- **ProposedOrder** — one decision the system wants to take. Carries
  `strategy`, `symbol`, `side`, `qty`, `extra: dict` (strategy-specific
  bag — tier, position context, `pmcc_pair_id`, source signal, etc.),
  status lifecycle (`proposed → risk_approved → board_approved →
  filled`).
- **RiskVerdict** — `approve | reject | resize`. Deterministic.
  Optional LLM `narration` field. Defined in
  [agents/risk.py](trading_corp/agents/risk.py).
- **TierVerdict** — strategy's per-signal conviction call (Otter
  Diamond through Solo Otter; Cypher GOLD through EMA_FLIP). Drives
  sizing. Defined in each division's module.
- **SymbolState** — per-`(strategy, symbol)` runtime state: bias,
  sommi, arming, recent alerts ring buffer, halt state. Process
  memory + bias persisted to `agent_state`.
- **AccountSnapshot** — point-in-time broker state: equity, buying
  power, cash, positions. Defined in
  [brokers/base.py](trading_corp/brokers/base.py).
- **FillEvent** — what just executed at a venue.
- **AuditEvent** — `(actor, kind, payload)` row in `audit_event`.

---

## 5. Common tasks — canonical patterns

### Adding a new strategy or division

These are two different tasks now (see § Module map). Pick the right one.

**A new STRATEGY** runs inside an existing division (e.g. a second
crypto strategy alongside Otter and Cypher in `coinbase_spot`):
1. Create `agents/strategies/<name>.py` modeled on `lord_otter.py` or
   `market_cypher.py` for TV-driven, or copy the scan-driven shape
   from `agents/divisions/pmcc_robinhood.py`.
2. Add the agent class with `enabled` / `auto_execute` / `division`
   properties reading from `config/strategies.yaml` (mtime-cached).
3. Wire into [main.py](trading_corp/main.py) deps + `WebDeps`.
4. **`auto_execute: false`** in `strategies.yaml` until paper-track
   record is earned.
5. New persistent state → `agent_state` table with a staleness gate.
6. **Before scoping research consultation, read § Research
   consultation.** Most strategies should not be calling research
   per-alert.

**A new DIVISION** is a new brokerage/account surface (e.g. Polymarket,
crypto futures, a different equity broker):
1. Create `agents/divisions/<name>.py` modeled on `pmcc_robinhood.py`.
   Division code does portfolio-manager work — universe sizing,
   per-account allocation, capacity gates — and routes to one or more
   strategy modules under `agents/strategies/` for the actual
   trade-decision logic.
2. Add the division to [config/divisions.yaml](config/divisions.yaml)
   (broker + account_filter + slug).
3. New broker adapter if the venue is new — see "Adding a new broker
   adapter" below.
4. **Don't design a new division speculatively.** Build only after an
   existing division's pattern is validated in production. Premature
   division design was the failure mode pre-2026-05-02.

### Adding a new broker adapter
1. **Read-write?** Subclass `Broker` in
   [brokers/base.py](trading_corp/brokers/base.py). Implement
   `connect`, `disconnect`, `snapshot`, `place_order`, `cancel_order`,
   `quote`. Set `paper: bool = False` when in live mode.
2. **Read-only?** Subclass `ReadOnlyBroker` (no `place_order`,
   no `cancel_order`). The ABC will refuse instantiation if you try
   to use it where `place_order` is called — that's the enforcement.
3. Wire into `_build_broker_for_division` in
   [main.py](trading_corp/main.py) so `--live --brokers <family>`
   selects the live class and PAPER mode wraps in
   `PaperExecutionBroker`.
4. Failed connect must fall back via the `broker_fallback_to_paper`
   path with `starting_equity=0.0`.

### Adding a new signal source (TV-driven)
**This is the firm-research-agent path.** Mirror
[web/webhooks.py](trading_corp/web/webhooks.py) lines 462–794
(`market_cypher_webhook`):
1. New endpoint `POST /webhook/<source>/<strategy>`.
2. IP allowlist (or env override flag) → body cap → lenient JSON
   parse → constant-time HMAC secret check → replay window (size to
   the bar duration of the signal source) → symbol normalize.
3. **`webhook_received` audit BEFORE agent dispatch.** Tag with
   `strategy` and `division`.
4. Snapshot broker for equity-aware sizing + held-qty lookup. Reuse
   the same snapshot for the risk gate.
5. Agent's `on_alert(payload, account_equity, held_qty)` returns
   `(ProposedOrder | None, decision_str)`.
6. Risk gate inline:
   `deps.risk_agent.evaluate(order, account, strat_state, regime, None)`.
7. `auto_execute=false` → `would_have_placed` audit + Telegram
   notification ping (deeplink to web-app approval page; see § HITL
   surface direction).
   `auto_execute=true` → `data_exec.place(order, division=...)` +
   fill notify. Before flipping any new TV division to
   `auto_execute=true`, harmonize the webhook gate with the
   LangGraph path's `auto_execute_caps` (see § 1).

### Adding a new risk check
1. Add to `RiskAgent.evaluate()` in
   [agents/risk.py](trading_corp/agents/risk.py). New cap ⇒ new
   `params.get(...)` read from `config/risk.yaml`.
2. Determine semantics: `approve` / `reject` / `resize`. Resize must
   set `new_qty`. Reject must set a human-readable `reason`.
3. **Options whole-contract guard:** if the check resizes, floor to
   whole contracts when `is_option` is true.
4. Tests in `tests/test_risk_gates.py`.

### Adding a new audit event kind
1. Pick a stable kind name (e.g. `regime_changed`, `pair_rolled`).
   Reuse existing kinds where possible — the audit query layer
   filters on them.
2. Always include `strategy` + `division` in payload when the event
   is division-scoped.
3. Write the event from the producing agent via
   `LoggerAgent.log_event(actor=..., kind=..., payload={...})`.

### Adding a column to an existing table
1. Don't, if `extra: dict` (→ `extra_json`) can carry the data.
2. If you must: schema change → explicit Board approval → migration
   script in `scripts/` → backfill existing rows or document the
   "pre-migration aggregates" caveat.

### Hot-reloading a config change
- `risk.yaml` — `RiskAgent` mtime-checks on every `evaluate()` call.
- `strategies.yaml` — division agents (Otter/Cypher) mtime-check on
  property reads. **`graph/ceo_graph.py:_check_auto_execute` re-reads
  on every order with no mtime cache** (sharp edge).
- `divisions.yaml`, `agents.yaml` — loaded at startup. Restart
  required.
- **There is no validation.** Typos silently degrade the strategy.
  Watch the audit log for "would have fired but didn't."

---

## 6. Things to ask before doing

Stop and ask the human first if you're about to:

- Touch the existing TradingView → broker path (Otter or Cypher
  webhooks, PMCC scan flow, `agents/risk.py`, `agents/data_exec.py`,
  broker adapters in any way that affects placement).
- Add a new path that places orders.
- Change the risk gate logic, including adding/removing/reordering
  caps.
- Change `audit_event` write ordering (must remain "before each
  branch").
- Add new secrets handling, change the redaction filter, or alter
  the Key Vault fetch path.
- Change the LangGraph checkpointer or `TradeFlowState` shape.
- Edit anything in [runbooks/](runbooks/) — operational playbooks,
  no-edit without explicit Board approval. They're a recovery
  contract, not a refactor target; a stale or "improved" runbook is
  worse than a missing one when you're locked out.
- Edit [infra/main.bicep](infra/main.bicep) — IaC for the Azure
  deployment. **Edit-with-deploy-plan only:** any change must be
  paired with a step-by-step deploy plan (what `az`/`bicep` commands
  will run, in what order, what to roll back if it fails). Don't
  edit speculatively.
- Modify VM-side configuration (Caddy, Authelia, NSG rules, systemd
  units, anything in `/etc/` on the VM). **No-edit from this repo.**
  These live on the production VM and are managed via SSH per the
  runbooks. A change here would silently disagree with the deployed
  state.
- Change the `broker_fallback_to_paper` semantics (especially the
  `starting_equity=0.0`).
- Touch `_acquire_lock()` / PID-file logic.
- Add a new `auto_execute_caps.require_approval_for` trigger or
  remove an existing one.
- Bypass HITL "for testing" or "for the demo." Build a paper-mode
  fixture instead.
- Default any new strategy to `auto_execute: true`.
- **Deploy a new strategy or change a strategy's parameters
  (sizing, tier thresholds, halt conditions) without a Backtester
  approval.** This rule is documented as a hard constraint
  ([PROJECT_CONTEXT.md § 11](PROJECT_CONTEXT.md)) but isn't
  code-enforced today (see § Known sharp edges) — treat it as a
  human-process gate until enforcement lands.

---

## 7. Known sharp edges

These are intentionally true. Don't "fix" them without explicit
approval.

- **Webhook risk gate ≠ LangGraph risk gate orchestration.** TV
  webhooks call `risk_agent.evaluate()` inline. PMCC scans + Telegram
  flows go through `build_trade_graph()`. Same gate, two
  orchestrations. The webhook path's `auto_execute` is a single bool;
  the graph path's `auto_execute_caps` is much richer (VIX,
  LEAP-debit, black-sheep, daily aggregates). Safety implication:
  flipping a TV division to `auto_execute=true` today would skip the
  richer caps. Harmonize before flipping (see § 1).
- **`FidelityBroker` subclasses the full `Broker` ABC, not
  `ReadOnlyBroker`.** Predates the "read-only enforced by missing
  methods" rule. Migration TODO: extract a `ReadOnlyBroker` ABC and
  rebase `FidelityBroker` onto it once the Fidelity options ticket
  flow is either shipped (Phase 3 backlog) or formally deferred. New
  read-only adapters use `ReadOnlyBroker`; don't model them on
  `FidelityBroker`.
- **Strategies are agent classes, not graph nodes** (deliberate — see
  [docs/ARCHITECTURE.md § 6 design decision 6](docs/ARCHITECTURE.md)).
  Pro: simple test harness. Con: can't visualize strategy internals
  in graph traces.
- **`pmcc_robinhood.py` and `fidelity_options.py` conflate
  division-level and strategy-level concerns.** Otter and Cypher were
  carved out into `agents/strategies/` on 2026-05-02; PMCC and Fidelity
  remain mixed. Future work should follow the Otter/Cypher precedent
  when it becomes load-bearing — extract strategy logic from PMCC into
  `agents/strategies/pmcc.py` once a second Robinhood strategy is
  needed. Don't refactor speculatively.
- **`extra_json` is unqueryable by SQL columns.** The trade-off:
  schema-stable, strategy-specific bag, but `LIKE`-based queries
  (e.g. `_query_prior_rolls` filtering on `pmcc_pair_id`) are
  brittle. Accepted because most reads are full payloads.
- **Config hot-reload has no validation.** Typos silently degrade.
- **`graph/ceo_graph.py:_check_auto_execute` re-reads
  `strategies.yaml` every call without mtime caching.** All other
  agents mtime-cache. Inconsistent but not harmful.
- **Webhook risk gate falls back to `equity = 100_000.0` if broker
  snapshot fails.** Means risk caps run on a placeholder rather than
  rejecting. The snapshot-failure log is the trail.
- **`FidelityBroker` is bot-blocked from Azure VM IP** (Akamai
  layer, pre-JS). Falls back to paper. Residential-proxy plan
  **DEFERRED 2026-05-03**; user investigating Plaid integration
  as a legitimate alternative. See P1 BACKLOG entry "Fidelity
  broker: read-only + analysis on Azure VM".
- **BitUnix accepts the Azure VM IP fine** — no anti-bot at the
  network layer. Useful contrast with Fidelity. Phase 1 broker
  uses SHA256-double-sign auth (no HMAC, no passphrase). The
  `transfer` field in `/api/v1/futures/account` is **additive**
  to total equity, NOT a duplicate of `available` (verified
  2026-05-03 against the BitUnix UI: $1250 available + $1250
  transfer = $2500 total). Crypto-margined balances (BTC/ETH
  margin) need quote conversion to USD; stablecoins (USDT/USDC)
  are summed 1:1.
- **Investment-type UI grouping is divisions-aware, not
  broker-aware.** `classify_investment_type(d)` in
  `trading_corp/utils/divisions.py` maps each division to
  Individual / Crypto / Retirement using a small rule
  (intent=retirement → retirement; broker in {coinbase, bitunix}
  → crypto; else individual). New broker families decide their
  group via `_CRYPTO_BROKERS` set membership. New retirement-style
  intents reuse the existing `intent: retirement` YAML field.
- **STANDBY badge is UI-only** (Coinbase Futures + BitUnix Futures
  today). Setting `standby: true` in `divisions.yaml` does NOT
  disable order routing or broker registration. The signal that
  "this division doesn't trade live today" is enforced separately:
  for BitUnix via `BitunixBroker.place_order` raising; for Coinbase
  Futures it's not enforced today (still order-capable in code).
- **Backtester approval gate is documented but not code-enforced.**
  [PROJECT_CONTEXT.md § 11](PROJECT_CONTEXT.md) and § 6 above say
  "new strategies need backtest approval"; today the path doesn't
  enforce it. Treat the rule as human-process until enforcement
  lands.
- **PMCC `_query_prior_rolls` aggregates rolls by symbol, not by
  LEAP lifetime** (P0 backlog item). Multi-LEAP-on-one-symbol
  scenarios silently miscount.

---

## 8. How to use this file

- This file is loaded into every Claude Code session for this repo.
- If a rule here conflicts with a user's in-session instruction,
  raise the conflict explicitly and ask before proceeding.
- This file is updated by humans, with AI assistance, never by AI
  alone.
- When proposing additions, propose them in a separate message — do
  not edit CLAUDE.md as part of unrelated work.
- The user's communication preferences (no flattery openings,
  evidence first, commands+screenshots over prose, don't-bury-the-
  lede) live in [PROJECT_CONTEXT.md § 10](PROJECT_CONTEXT.md). Honor
  them.
