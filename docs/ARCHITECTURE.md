# Trading Corp — Application Architecture

> Authoritative reference for the application's organizing principles,
> module layout, decision pipeline, domain model, state model, and key
> design decisions. Linked from [CLAUDE.md](../CLAUDE.md) and
> [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).
>
> Operational topology (Authelia, Caddy, Azure VM, NSG) lives below the
> application sections.

---

## Topology — agents, brokers, signals

```
                         ┌─────────────────┐
   You (Telegram /        │   CEO Agent     │
   Dashboard / CLI)  ─────│  (LangGraph     │
                          │   orchestrator) │
                          └────────┬────────┘
                                   │
          ┌────────┬────────┬──────┴──────┬────────┬────────┐
          ▼        ▼        ▼             ▼        ▼        ▼
 ┌──────────────┐ ┌──────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐
 │ Strategy     │ │ Risk │ │ Trend  │ │Portfolio│ │ Logger │ │ DataExec │
 │ agents       │ │ Agent│ │ Agent  │ │  Agent  │ │ Agent  │ │  Agent   │
 ├──────────────┤ │      │ │        │ │         │ │        │ │          │
 │ PMCCAgent    │ │deter-│ │ regime │ │aggregate│ │audit_  │ │ broker   │
 │ (RH options) │ │minist│ │ label  │ │ exposure│ │event   │ │ registry │
 │              │ │ caps │ │        │ │         │ │ writer │ │          │
 │ LordOtter    │ │ +    │ │        │ │         │ │        │ │          │
 │ (BTC scalp)  │ │ LLM  │ │        │ │         │ │        │ │          │
 │              │ │narra-│ │        │ │         │ │        │ │          │
 │ MarketCypher │ │ tion │ │        │ │         │ │        │ │          │
 │ (BTC swing)  │ │      │ │        │ │         │ │        │ │          │
 │              │ │      │ │        │ │         │ │        │ │          │
 │ FidelityOpts │ │      │ │        │ │         │ │        │ │          │
 │ (paper-only) │ │      │ │        │ │         │ │        │ │          │
 └──────────────┘ └──────┘ └────────┘ └─────────┘ └────────┘ └──────────┘
                                                                   │
                                                                   ▼
                                                        ┌──────────────────┐
                                                        │ Broker registry  │
                                                        ├──────────────────┤
                                                        │ • RobinhoodBroker│
                                                        │   ×3 (PMCC/IRA/  │
                                                        │       Joint)     │
                                                        │ • CoinbaseBroker │
                                                        │   spot           │
                                                        │ • CoinbaseBroker │
                                                        │   futures (stub) │
                                                        │ • FidelityBroker │
                                                        │   → paper-fall   │
                                                        │ • PaperBroker ×N │
                                                        │   (defaults)     │
                                                        └──────────────────┘
```

## TradingView signal path

```
TradingView alert (Once Per Bar Close)
              │
              ▼
POST /webhook/tradingview/{lord-otter|market-cypher}
              │
 ┌────────────┴────────────────────────────┐
 │ Caddy: bypass list, no Authelia needed  │
 └────────────┬────────────────────────────┘
              ▼
 ┌─────────────────────────────────────────┐
 │ web/webhooks.py per-strategy handler    │
 │  1. IP allowlist (or shared-secret only)│
 │  2. body cap + lenient JSON parse       │
 │  3. HMAC secret check (constant-time)   │
 │  4. Replay window (1200s otter / 25h    │
 │     cypher)                             │
 │  5. Symbol normalize (BTCUSD → BTC/USD) │
 │  6. AUDIT: webhook_received             │
 │  7. Snapshot broker → equity + held     │
 └────────────┬────────────────────────────┘
              ▼
 ┌─────────────────────────────────────────┐
 │ agent.on_alert(payload, equity, held)   │
 │  • record alert in state                │
 │  • _refresh_state_from_signal           │
 │    (bias / sommi / arming + persist)    │
 │  • news halt / cooldown / chop checks   │
 │  • _classify_tier → TierVerdict         │
 │  • _build_order → ProposedOrder         │
 └────────────┬────────────────────────────┘
              ▼
 ┌─────────────────────────────────────────┐
 │ risk_agent.evaluate(order, account, …)  │
 │  → APPROVE / REJECT / RESIZE            │
 └────────────┬────────────────────────────┘
              │
     ┌────────┴────────┐
     ▼                 ▼
 auto_execute      auto_execute
   == false          == true
     │                 │
     ▼                 ▼
Telegram push    broker.place_order
+ audit event    + Telegram fill
"would_have_     notify
 placed"
```

## Persistence tables

```
audit_event       ─── EVERY action: webhook, alert_ignored,
                      risk_rejected, would_have_placed, filled,
                      agent_error, halt, startup
                      (105+ rows, indexed by ts)
proposed_order    ─── Order lifecycle: proposed → risk_approved →
                      board_approved → filled (or rejected at any
                      stage). extra_json carries tier, position
                      context, leap_lifetime_key (P0 backlog)
position          ─── Cached position snapshots (account, symbol,
                      qty, avg_price)
account_state     ─── Equity + peak + halt state per account
strategy_state    ─── Per-strategy halt + realized P&L + daily reset
agent_state       ─── NEW (2026-04-30). Generic key/value JSON
                      store for agent state needing persistence.
                      Currently:
                       ('lord_otter',    'bias:BTC/USD')
                       ('market_cypher', 'bias:BTC/USD')
                       ('market_cypher', 'sommi:BTC/USD')
                      Staleness gates: 12h Otter, 3d Cypher
daily_brief       ─── Morning + EOD generated reports
```

## Auth flow

```
You open https://trading.jacksumner.com
              │
              ▼
┌──────────────────────────────────────┐
│ Caddy: forward_auth → Authelia       │
│  /api/authz/forward-auth             │
│  • Has session cookie? → 200 → proxy │
│  • No cookie? → 302 redirect ↓       │
└──────────────────────────────────────┘
              │
              ▼
https://auth.jacksumner.com
              │
              ▼
┌──────────────────────────────────────┐
│ Authelia login UI                     │
│  1. Username + password (argon2id)    │
│  2. TOTP 6-digit code (your phone)    │
│  3. Optional remember-me 30 days      │
└──────────────────────────────────────┘
              │
     valid auth
              ▼
Set-Cookie: authelia_session (.jacksumner.com)
              │
              ▼
302 back to original target URL
              │
              ▼
Caddy: forward_auth → 200 → reverse_proxy localhost:8000
              │
              ▼
trading-corp FastAPI dashboard
```

## Infrastructure (Azure)

```
Internet
                            │
             ┌──────────────┼──────────────┐
             │              │              │
             │              │              │
      TradingView      Your phone /    GitHub /
     (webhook IPs)     Browser (any    Let's Encrypt
                        location)
             │              │              │
             ▼              ▼              ▼
 ┌─────────────────────────────────────────────────────┐
 │  Azure NSG (rg-shared-prod / tc-prod-nsg)            │
 │  ─────────────────────────────────────────────────   │
 │   22  ← 98.231.16.63/32 only  (your home IP, SSH)   │
 │   80  ← Internet (Let's Encrypt challenge + redirect)│
 │   443 ← Internet (HTTPS / dashboard / webhooks)      │
 └─────────────────────────────────────────────────────┘
                            │
                            ▼
 ┌─────────────────────────────────────────────────────┐
 │  Azure VM  · tc-prod-vm · 20.51.145.253 (static)     │
 │  Standard_D2s_v3 · Ubuntu 22.04 · East US            │
 │  System-assigned Managed Identity                    │
 │                                                      │
 │  ┌─────────────────────────────────────────────┐    │
 │  │ caddy.service  (port 80/443)                │    │
 │  │  ──────────────────────────                 │    │
 │  │  trading.jacksumner.com                     │    │
 │  │    ├─ @public bypass (TV webhooks, PWA,     │    │
 │  │    │   /healthz, /static/*)                 │    │
 │  │    │   → reverse_proxy :8000                │    │
 │  │    └─ default → forward_auth :9091          │    │
 │  │        → reverse_proxy :8000                │    │
 │  │                                             │    │
 │  │  auth.jacksumner.com                        │    │
 │  │    └─ reverse_proxy :9091                   │    │
 │  └─────────────────────────────────────────────┘    │
 │              │                       │               │
 │              ▼                       ▼               │
 │  ┌────────────────────┐   ┌─────────────────────┐   │
 │  │ authelia.service   │   │ trading-corp.service│   │
 │  │ :9091 (localhost)  │   │ :8000 (localhost)   │   │
 │  │                    │   │ xvfb-run wrapper    │   │
 │  │ • argon2id auth    │   │ ┌─────────────────┐ │   │
 │  │ • TOTP 2FA         │   │ │  FastAPI app    │ │   │
 │  │ • SQLite session   │   │ │  +              │ │   │
 │  │ • file notifier    │   │ │  Agent runtime  │ │   │
 │  │   (no SMTP yet)    │   │ │  (asyncio)      │ │   │
 │  └────────────────────┘   │ └─────────────────┘ │   │
 │                            └─────────────────────┘   │
 │                                       │              │
 │           ┌───────────────────────────┼─────────┐    │
 │           ▼                           ▼         ▼    │
 │  ┌────────────────┐  ┌────────────────┐  ┌──────────┐│
 │  │ data/          │  │ /etc/authelia/ │  │ /etc/    ││
 │  │ trading_corp.db│  │ /var/lib/      │  │ caddy/   ││
 │  │ (SQLite WAL)   │  │   authelia/    │  │          ││
 │  └────────────────┘  └────────────────┘  └──────────┘│
 └─────────────────────────────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ Azure Key    │  │ Anthropic    │  │ External     │
  │ Vault        │  │ API          │  │ Brokers      │
  │ (Managed     │  │ (Claude      │  │ Robinhood    │
  │  Identity)   │  │  Sonnet 4.6) │  │ Coinbase     │
  └──────────────┘  └──────────────┘  └──────────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │ Telegram Bot │
                    │ API          │
                    └──────────────┘
```

---

# Trading Corp — application architecture

## 1. The four organizing principles

These are the design commitments everything else falls out of:

| Principle | What it means | Where you see it |
|---|---|---|
| **Layered, with strict downward dependencies** | Web/comms layer never imports brokers; brokers never import strategies; persistence depends on nothing internal. Reverse imports are bugs. | `web/` → `agents/` → `brokers/` → `persistence/` → `utils/` |
| **Divisions and strategies** | Division = one (brokerage × accounts) portfolio manager — `robinhood_pmcc`, `coinbase_spot`, `fidelity_options`. Strategy = how that division decides what to trade; one division can run multiple strategies (e.g. `coinbase_spot` runs both `lord_otter` and `market_cypher`). They share scaffolding but are independently configured, halted, and risk-gated. *(Vocabulary clarified 2026-05-02; see CLAUDE.md § Module map. Earlier text in this doc may use "broker × strategy = division" framing — that has been superseded.)* | `agents/divisions/{pmcc_robinhood, fidelity_options}.py` (division wiring) + `agents/strategies/{lord_otter, market_cypher}.py` (TV-driven strategies inside `coinbase_spot`) + `config/divisions.yaml` |
| **Paper-default, risk-gated, HITL on every live order until trust earned** | Three orthogonal switches: paper/live mode flag, `auto_execute` per-strategy, risk-cap evaluation. ANY of them blocking = no trade. | `main.py` mode arg, `strategies.yaml::auto_execute`, `agents/risk.py` |
| **Deterministic caps + LLM narration, not LLM judgment** | Risk caps are Python code (so reproducible & testable). LLMs only narrate why something was approved/rejected. Same for sizing math. | `agents/risk.py` evaluates, `agents/risk.py::_narrate` explains |

## 2. Module layout — the cake

```
trading_corp/
│
├── main.py ─────────────────────────────── entrypoint, deps wiring,
│                                            mode selection, idle loop
│
├── web/  ─────────────── HTTP-facing layer
│   ├── app.py             FastAPI factory + WebDeps dataclass
│   ├── routes.py          dashboard routes
│   ├── webhooks.py        TV webhook endpoints (lord-otter, market-cypher)
│   ├── data.py            DB → template-friendly snapshot dataclasses
│   │                      (OptionLeg, PMCCPair, StockHolding, ...)
│   └── templates/         Jinja2 (htmx-driven, PWA-enabled)
│
├── comms/  ────────────── User-facing channels
│   ├── telegram_bot.py    bot poll loop + approval/modify/reject handlers
│   ├── cli.py             stdin-driven approval (when no Telegram)
│   └── approval_format.py rich Telegram-Markdown approval message body
│                          (option / crypto / stock / position-context)
│
├── graph/  ────────────── Orchestration (LangGraph)
│   ├── ceo_graph.py       build the graph, define nodes + edges
│   ├── state.py           TypedDict for global state
│   └── interrupts.py      HITL approval gate helpers
│
├── agents/  ───────────── Decision-makers
│   ├── ceo.py             routes user msgs, daily brief, EOD debate
│   ├── risk.py            ★ deterministic caps + LLM narration
│   ├── trend_regime.py    regime classifier (uptrend/down/chop)
│   ├── backtester.py      strategy gating (skeleton)
│   ├── portfolio.py       aggregate exposure / P&L / correlation
│   ├── data_exec.py       broker registry + place_order
│   ├── logger.py          audit event writer (single source of truth)
│   ├── divisions/         brokerage/account-level division wiring
│   │   ├── pmcc_robinhood.py     (RH options PMCC; mixes strategy logic — sharp edge)
│   │   └── fidelity_options.py   (paper-fallback; same conflation)
│   ├── strategies/        TV-driven strategies inside coinbase_spot division
│   │   ├── lord_otter.py         (BTC scalp via TV — 3m)
│   │   └── market_cypher.py      (BTC swing via TV — 4h/1D)
│   └── research/          shared research-firm consultant (see CLAUDE.md § Research consultation)
│
├── brokers/  ──────────── Execution adapters
│   ├── base.py            Broker / OptionBroker abstract interfaces
│   ├── paper.py           PaperBroker (used as default + fallback)
│   ├── robinhood.py       robin_stocks wrapper, multi-account, persistent session
│   ├── coinbase.py        ccxt-based (spot live, futures stub)
│   └── fidelity.py        Playwright headless (currently bot-blocked on VM)
│
├── persistence/  ──────── State & history
│   ├── db.py              SQLite engine, schema, agent_state helpers
│   ├── models.py          ProposedOrder, Position, FillEvent, etc.
│   └── checkpointer.py    LangGraph SqliteSaver
│
├── data/  ─────────────── External data
│   ├── feeds.py           WS aggregator skeleton (yfinance + ccxt)
│   ├── macro_calendar.py  YAML-backed news halt source
│   └── tradingview.py     unofficial WS supplement (lower priority)
│
└── utils/  ────────────── Foundation
    ├── secrets.py         env + Azure Key Vault loader (refuses live without keys)
    ├── time.py            iso() / now_utc() / etc.
    ├── divisions.py       loads divisions.yaml → Division dataclasses
    └── market_data.py     yfinance helpers (quote, ribbon, VIX)

config/  ──────────────── Hot-reloadable knobs (mtime-watched)
├── strategies.yaml        per-strategy: enabled, auto_execute, tiers, ...
├── risk.yaml              global risk caps + per-strategy overrides
├── agents.yaml            LLM model assignment per agent
├── divisions.yaml         broker × account routing
└── macro_calendar.yaml    hand-maintained FOMC/CPI/NFP halts

runbooks/  ────────────── Ops playbooks (introduced tonight)
└── auth_lockout_recovery.md
```

## 3. The decision pipeline — what happens when input arrives

```
 ┌─────────────────────────────────────────────────────────────┐
 │ INPUT                                                       │
 │  TV alert · user Telegram cmd · cron · web request          │
 └─────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ INGEST (web/webhooks.py · web/routes.py · comms/telegram_bot)│
 │  • Auth: secret/IP/cookie depending on entry                 │
 │  • Parse + normalize (symbol, ts, payload schema)            │
 │  • AUDIT inbound (kind=webhook_received / cmd_received)      │
 └─────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ STATE UPDATE (agents/strategies/*.py for TV-driven, or       │
 │   agents/divisions/*.py for division-level scan flows)      │
 │  • record_alert → ring buffer                               │
 │  • _refresh_state_from_signal:                              │
 │      bias ← signals that flip the latch                     │
 │      sommi ← HTF VWAP regime (Cypher only)                  │
 │      armed_long/short ← arming signals (timed window)       │
 │  • Persist any latched state (DB agent_state table)         │
 └─────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ DECISION GATES (in order)                                    │
 │  1. enabled? auto_execute?                                   │
 │  2. halt active (consecutive losses, daily PnL cap, news)?   │
 │  3. cooldown / chop guards?                                  │
 │  4. classify_tier → TierVerdict | None                       │
 │  5. apply Sommi modifier (Cypher only)                       │
 │  6. apply time-of-day / weekend modifiers                    │
 └─────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ ORDER CONSTRUCTION (build_order in each agent)               │
 │  • notional = equity × tier_size_pct                         │
 │  • qty = notional / price                                    │
 │  • technical stop from trigger bar                           │
 │  • SHRINK qty (never widen stop) to fit max_loss_pct_equity  │
 │  → ProposedOrder { strategy, symbol, side, qty, ..., extra } │
 └─────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ RISK GATE (agents/risk.py)                                   │
 │  Deterministic checks (Python, not LLM):                     │
 │    • per-trade risk pct cap                                  │
 │    • per-strategy daily loss cap                             │
 │    • account drawdown                                        │
 │    • correlation                                             │
 │    • volatility scalar                                       │
 │  → RiskVerdict { verdict: approve|reject|resize, reason }    │
 │  LLM narration adds the "why" string (optional, behind flag) │
 └─────────────────────┬───────────────────────────────────────┘
                       │
      ┌────────────────┴────────────────┐
      ▼                                 ▼
 risk REJECT                       risk APPROVE / RESIZE
 audit + return                    │
                                   ▼
                     ┌──────────────────────────┐
                     │ auto_execute config flag │
                     └──────┬─────────────┬─────┘
                            │             │
                     false  ▼             ▼  true
          ┌──────────────────┐   ┌─────────────────────┐
          │ HITL approval     │   │ broker.place_order  │
          │ • write would_    │   │ • fill event        │
          │   have_placed     │   │ • audit fill        │
          │ • Telegram push   │   │ • Telegram notify   │
          │ • LangGraph       │   │ • record_win/loss   │
          │   interrupt()     │   │   on the agent      │
          │ • wait for board  │   └─────────────────────┘
          └──────────────────┘
                │ board approves
                ▼
          broker.place_order (same as auto path)
```

The pipeline is the same for every input source. TV webhooks, scheduled
scans, and manual orders all land in this same shape. The risk gate is a
single chokepoint — there is no path around it.

## 4. Domain model (the nouns)

```
ProposedOrder ─── one decision the system wants to take
├─ strategy: str           ← which division emitted it
├─ symbol: str             ← BTC/USD, AAPL, RKLB, ...
├─ side: "buy" | "sell"
├─ qty: float
├─ order_type: "market" | "limit"
├─ limit_price: float | None
├─ rationale: str          ← human-readable, formatted
├─ status: "proposed" | "risk_rejected" | "board_approved"
│          | "filled" | ...
├─ risk_reason: str | None
├─ board_reason: str | None
├─ fill_price / fill_ts
└─ extra: dict             ← strategy-specific bag:
                              is_option, underlying, expiration,
                              strike, delta, dte, position_effect,
                              tier, source_signal, position_context,
                              pmcc_pair_id, ...

Position  ────── what we hold right now
├─ account, symbol, qty, avg_price, opened_ts, extra

FillEvent ────── what just executed
├─ price, venue, qty, ts, fees

AccountSnapshot  ─ the account's state at a moment
├─ account: str
├─ equity, buying_power, cash
├─ positions: list[Position]
└─ peak_equity (for drawdown calc)

RiskVerdict ──── deterministic gate output
├─ verdict: "approve" | "reject" | "resize"
├─ reason: str
└─ new_qty: float | None   ← when verdict == "resize"

TierVerdict ──── strategy's per-signal conviction call
├─ tier: str               ← gold/diamond/premium/standard/...
├─ direction: "long" | "short"
├─ size_pct_equity: float
├─ rationale: str          ← why this tier vs. another
├─ entry_price: float
└─ payload: dict           ← passes through to ProposedOrder.extra

SymbolState ──── per-(strategy, symbol) live state
├─ bias: "bull" | "bear" | "unknown"  ← persisted in agent_state
├─ sommi: ...                          ← Cypher only, also persisted
├─ armed_long/short: ArmedState | None ← timed window
├─ recent_alerts: list[AlertRecord]    ← ring buffer
├─ last_entry_at, last_close_at
├─ consecutive_losses
├─ daily_realized_pnl_pct
└─ halted_until / halt_reason
```

The relationships:

- An `AlertRecord` updates `SymbolState`, sometimes producing a
  `TierVerdict`, which becomes a `ProposedOrder`, which goes through
  the `RiskVerdict` gate and either creates a `FillEvent` or doesn't.
- All of these are written to `audit_event` for replay/debug.

## 5. State model (where state lives, by lifetime)

```
┌───────────────────────────────────────────────────────────────┐
│ Process memory (lost on restart unless persisted)              │
│ ─────────────────────────────────────────────────────────────  │
│  • LordOtterAgent._states / MarketCypherAgent._states          │
│  • PMCCAgent caches                                            │
│  • LangGraph checkpoint state (paused interrupts) ← survives   │
│    via SqliteSaver                                             │
└───────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────┐
│ SQLite (durable, single-file, WAL mode)                        │
│ ─────────────────────────────────────────────────────────────  │
│  audit_event       every action ever                            │
│  proposed_order    every order intent + lifecycle               │
│  position          snapshot cache (rebuildable)                 │
│  account_state     equity + peak (per account)                  │
│  strategy_state    halt + daily PnL (per strategy)              │
│  agent_state       NEW — generic kv JSON store for latch state  │
│                    e.g. ('lord_otter', 'bias:BTC/USD'),         │
│                         ('market_cypher', 'sommi:BTC/USD')      │
│  daily_brief       morning + EOD reports                        │
└───────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────┐
│ Filesystem (config + session caches)                           │
│ ─────────────────────────────────────────────────────────────  │
│  config/*.yaml                  hot-reloadable strategy knobs   │
│  ~/.tokens/robinhood.pickle     RH session (auto-managed)      │
│  data/fidelity_session/         playwright cookies              │
│  /etc/authelia/                 user db + secrets               │
│  /var/lib/authelia/             session state                   │
└───────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────┐
│ External (out of process)                                      │
│ ─────────────────────────────────────────────────────────────  │
│  Azure Key Vault                all secrets (RBAC + MI auth)   │
│  Robinhood / Coinbase           positions, fills (we cache)    │
│  TradingView                    alerts (we receive only)       │
│  Telegram                       messages (we send + receive)   │
└───────────────────────────────────────────────────────────────┘
```

**Invariant:** any state that affects future trade decisions and isn't
trivially derivable from inputs MUST be in SQLite. Process memory is for
caching, not authoritative state. (This invariant is what the
bias-persistence work was about — the bias was in process memory only,
restarts wiped it, strategy went mute.)

## 6. Key design decisions worth knowing

1. **`extra: dict` on every ProposedOrder.** Lets us add arbitrary
   strategy-specific context (tier, position context, PMCC pair ID,
   source signal) without schema migrations. The cost: things in `extra`
   aren't queryable via SQL columns. We accept this because most queries
   we care about read full payloads anyway.
2. **Mode flag is process-wide, `auto_execute` is per-strategy.** A
   `--paper` process can never place real orders no matter what config
   says. A `--live` process places only for divisions where
   `auto_execute=true` AND risk approves AND (if HITL on) board approves.
3. **Audit log is the source of truth, not the dashboard.** The
   dashboard renders snapshots; the audit log captures intent. If they
   ever disagree, audit wins. This is why the audit log gets written
   before every decision branch, not after.
4. **Hot-reloadable config via mtime polling** (every ~5s on the next
   read). Lets you tweak `strategies.yaml::tier_sizes` or
   `risk.yaml::per_trade_risk_pct` without bouncing the service. The
   cost: config validation isn't enforced — typos can silently degrade
   a strategy. Mitigated by audit-log visibility (you'd see the strategy
   stop firing).
5. **One LangGraph for everything user-facing.** The CEO graph
   orchestrates: routing user messages, daily brief, EOD debate, approval
   interrupts. Strategy agents are NODES in that graph (well, called by
   graph nodes). A single graph means a single checkpoint stream —
   restart-resilient.
6. **Strategies are agent classes, not graph nodes.** This was an early
   decision worth re-evaluating eventually. Pro: simpler test harness
   (instantiate agent directly). Con: can't visualize strategy internals
   in graph traces. The cost has been low so far.

That's the application logic top-to-bottom. The vocabulary, the flow,
the layers, the invariants.

---

*Source: drafted in conversation with the Board on 2026-04-30 and
saved to this file as the canonical architecture reference.
[CLAUDE.md](../CLAUDE.md) links here for the full body; do not duplicate
content in CLAUDE.md.*
