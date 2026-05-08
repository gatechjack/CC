# AI-Powered Trading Corporation

A multi-agent Python system that runs trading divisions across Robinhood
(PMCC options + IRA stocks/CCs + Joint), Coinbase (BTC spot, futures
STANDBY), BitUnix (futures read-only Phase 1; Phase 4 live trading
ahead), and Fidelity (paper-fallback, bot-blocked from Azure — P1
DEFERRED pending Plaid investigation). Live in production on Azure
VM `tc-prod-vm` at
https://trading.jacksumner.com behind Caddy + Authelia. Every order
flows through a deterministic risk gate plus HITL Board approval.

> **DISCLAIMER:** Trading involves substantial risk of loss. This software
> is experimental. The Board (the operator) accepts all responsibility
> for any actions taken in LIVE mode. **The system defaults to PAPER mode
> on every startup.** All strategies today are `auto_execute: false`.

## Read order for new contributors

1. **[CLAUDE.md](CLAUDE.md)** — load-bearing invariants, working
   agreements, module map, sharp edges, things-to-ask-before-doing.
   Auto-loaded into every Claude Code session.
2. **[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)** — what Trading Corp is,
   tech-stack decisions, risk profile, production state, glossary.
3. **[BACKLOG.md](BACKLOG.md)** — prioritized work items + the current
   ⏸ PAUSED notices.
4. **[runbooks/deploy_log.md](runbooks/deploy_log.md)** — single source
   of truth for what's running on prod right now (no git on prod).

## Setup (local development)

Requires Python **3.12+**.

```bash
python -m venv .venv
source .venv/bin/activate           # macOS/Linux
.venv\Scripts\activate              # Windows

pip install -r requirements.txt

cp .env.example .env
# Fill .env — at minimum ANTHROPIC_API_KEY for LLM-narrated paths
```

### Environment variables

| Var | When required |
|---|---|
| `ANTHROPIC_API_KEY` | LLM narration paths (research firm, risk narration). Optional — system falls back to deterministic-only mode if absent. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Optional. Approval channel; CLI fallback otherwise. |
| `ROBINHOOD_USERNAME` / `_PASSWORD` / `_MFA_SECRET` | LIVE Robinhood broker connection (PMCC). |
| `COINBASE_API_KEY` / `_SECRET` | LIVE Coinbase broker connection. |
| `BITUNIX_FUTURES_API_KEY` / `_SECRET` | BitUnix Futures connection. Read-only today (Phase 1); Phase 4 unlocks live order placement. |
| `FIDELITY_USERNAME` / `_PASSWORD` | LIVE Fidelity (currently bot-blocked from Azure VM IP — P1 DEFERRED pending Plaid investigation). |
| `LORD_OTTER_WEBHOOK_SECRET` / `MARKET_CYPHER_WEBHOOK_SECRET` | TradingView webhook auth. |
| `TRADING_CORP_DB_URL` | Override SQLite path (default: `sqlite:///data/trading_corp.db`). |

## Running

```bash
# Default — PAPER mode
python -m trading_corp

# LIVE mode — prompts for "LIVE" confirmation, requires broker creds
python -m trading_corp --live --brokers robinhood coinbase

# Synthetic demo trade through the full risk + Board approval graph
python -m trading_corp --demo
```

### Talking to the CEO

- **Telegram:** approval requests with inline `Approve` / `Reject`
  buttons. Commands: `/brief`, `/status`, `/approve <id>`,
  `/reject <id>`, `/modify <id> <qty>`, free-form chat.
- **Dashboard:** https://trading.jacksumner.com (Authelia-gated).
  Live trade flow, research engagement log, position tables, equity
  curves.

## Architecture

```
┌─ TradingView webhooks ──┐
│  /webhook/tradingview/* │  ─┐
└─────────────────────────┘   │
                              ▼
                        ┌──────────────────────────────────┐
                        │  Strategy code (per-strategy)    │
                        │   agents/strategies/*.py         │
                        │   (lord_otter, market_cypher)    │
                        │                                  │
                        │   agents/divisions/*.py          │
                        │   (pmcc_robinhood,               │
                        │    fidelity_options)             │
                        └─────────────────┬────────────────┘
                                          │ ProposedOrder
                                          ▼
            ┌─────────────────────────────────────────────────┐
            │  Research firm consult (advisory, fail-open)    │
            │  agents/research/  — see CLAUDE.md § Research   │
            └─────────────────────────┬───────────────────────┘
                                      │
                                      ▼
            ┌─────────────────────────────────────────────────┐
            │  Risk gate (deterministic, single chokepoint)   │
            │  agents/risk.py — hot-reloads config/risk.yaml  │
            └─────────────────────────┬───────────────────────┘
                                      │
                  auto_execute=false  │  auto_execute=true
                                      ▼
            ┌──────────────────┐  ┌─────────────────────────────┐
            │ Telegram push    │  │ broker.place_order(...)     │
            │ + would_have_    │  │ + audit + Telegram fill     │
            │   placed audit   │  │   notify                    │
            └──────────────────┘  └─────────────────────────────┘
```

LangGraph with `interrupt()` gates every live order behind Board
approval (PMCC scan + Telegram-driven flows). The TV webhook flow uses
inline risk gating with FastAPI `BackgroundTasks` for return-fast
behavior. SQLite checkpointing means a crash mid-trade resumes from
exactly the same state. Risk caps are enforced **deterministically in
code**; the LLM only narrates rationales.

Full architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project layout

```
trading_corp/
├── main.py                 # entrypoint
├── graph/                  # LangGraph CEO graph + HITL interrupts
├── agents/
│   ├── divisions/          # brokerage/account-level division wiring
│   │   ├── pmcc_robinhood.py
│   │   └── fidelity_options.py
│   ├── strategies/         # TV-driven strategies inside coinbase_spot division
│   │   ├── lord_otter.py
│   │   └── market_cypher.py
│   ├── research/           # shared research-firm consultant
│   ├── risk.py             # deterministic risk caps
│   ├── data_exec.py        # broker dispatch + dry-run
│   └── ...                 # CEO, Trend, Backtester, Portfolio, Logger
├── brokers/                # Broker interface + paper / robinhood / coinbase / bitunix / fidelity
├── data/                   # WS aggregator, yfinance fallback, macro calendar
├── comms/                  # Telegram bot + CLI Board channels
├── persistence/            # SQLite engine + dataclass models + LangGraph checkpointer
├── utils/                  # Secrets loader (KV + redaction), time helpers
└── web/                    # FastAPI app: dashboard, webhooks, htmx partials

config/
├── risk.yaml               # global caps + per-strategy overrides
├── strategies.yaml         # per-strategy enabled/auto_execute/tiers
├── divisions.yaml          # broker × account routing
├── agents.yaml             # LLM model assignment per agent
├── research.yaml           # research firm cost caps + consult timeouts
└── macro_calendar.yaml     # hand-maintained news halt calendar

runbooks/                   # operational playbooks (no-edit by default)
infra/                      # Bicep IaC (edit-with-deploy-plan only)
docs/ARCHITECTURE.md        # full architecture reference
```

## Testing

```bash
pytest                                                  # full suite
pytest tests/test_risk_gates.py                        # risk caps
pytest tests/test_paper_trading_default.py             # paper safety
pytest tests/test_graph_hitl.py                        # HITL graph
pytest tests/test_lord_otter_bias_persistence.py       # Otter state machine
pytest tests/test_webhooks_return_fast.py              # webhook timing contract
```

## Safety invariants (do not break)

1. **PAPER is the default on every startup.** Going LIVE requires
   `--live` AND interactive confirmation AND non-empty broker creds.
2. **Every live order requires Board approval** until per-strategy
   `auto_execute: true` is set in `config/strategies.yaml`. Currently
   `false` everywhere.
3. **Risk caps are deterministic in code.** The LLM never overrides
   the Risk Agent.
4. **No credentials in logs.** Logger applies `RedactingFilter` on the
   root logger.
5. **Audit log writes BEFORE every decision branch, not after.**
   `audit_event` is the source of truth; the dashboard renders
   snapshots.
6. **Single risk chokepoint:** `RiskAgent.evaluate()`. No code path
   may bypass it.

See [CLAUDE.md § STOP AND READ](CLAUDE.md) for the full invariant list.

## Current status

System is live in production on Azure. Paper-mode on every strategy.
Active focus through 2026-05-05 is observing PMCC's research-as-
consultant pattern in production to decide whether to extend the
research firm to crypto strategies; until that decision lands,
Lord Otter and Market Cypher feature work is paused (existing code
continues running paper-mode). See `BACKLOG.md` ⏸ PAUSED notice.
