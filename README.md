# AI-Powered Trading Corporation

A multi-agent Python system that runs three trading divisions (Robinhood PMCC, Fidelity options, crypto futures) under a CEO agent you converse with via Telegram, CLI, and Claude Projects.

> **DISCLAIMER:** Trading involves substantial risk of loss. This software is experimental. The Board (you) accept all responsibility for any actions taken in LIVE mode. **The system defaults to PAPER mode on every startup.**

## Phase status

- **Phase 1** — Architecture approved (see `.claude/plans/you-are-an-elite-compressed-micali.md`).
- **Phase 2** — *current.* Core framework + shared agents + paper-mode end-to-end.
- **Phase 3** — Robinhood PMCC division → Fidelity options → Crypto futures (each behind backtest gate).
- **Phase 4+** — Web dashboard, AWS deployment, sentiment agent.

## Setup

Requires Python **3.12+**.

```bash
# 1. (Recommended) Create a virtualenv
python -m venv .venv
source .venv/bin/activate           # macOS/Linux
.venv\Scripts\activate              # Windows

# 2. Install deps
pip install -r requirements.txt

# 3. Configure secrets — copy and fill .env
cp .env.example .env
# Edit .env with at least ANTHROPIC_API_KEY (other keys are Phase 3+).
```

### Environment variables

| Var | When required |
|---|---|
| `ANTHROPIC_API_KEY` | Always (Phase 2: optional — system falls back to deterministic mode if absent) |
| `TELEGRAM_BOT_TOKEN` | Optional (CEO falls back to CLI if absent) |
| `TELEGRAM_CHAT_ID` | Optional (paired with token) |
| `ROBINHOOD_USERNAME` / `_PASSWORD` / `_MFA_SECRET` | LIVE mode + Robinhood division (Phase 3) |
| `COINBASE_API_KEY` / `_SECRET` / `_PASSPHRASE` | LIVE mode + crypto division (Phase 3) |
| `FIDELITY_USERNAME` / `_PASSWORD` | LIVE mode + Fidelity division (Phase 3) |
| `TRADING_CORP_DB_URL` | Override SQLite path (default: `sqlite:///data/trading_corp.db`) |
| `ALLOW_SKELETON_BACKTEST=1` | Phase 2 only — bypass the Phase-2 backtester skeleton |
| `ENABLE_TRADINGVIEW=1` | Opt-in to unofficial TradingView WS (Phase 4) |

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

- **Telegram (recommended):** create a bot via [@BotFather](https://telegram.me/BotFather), put the token + your chat id in `.env`, then chat with the bot. Commands:
  - `/brief` — daily morning brief
  - `/status` — pending approvals + state
  - `/approve <order_id>` / `/reject <order_id>` / `/modify <order_id> <qty>`
  - Any other message — free-form chat with the CEO
  - Approval requests come with inline `Approve` / `Reject` buttons.
- **CLI:** when Telegram isn't configured, the same content is printed to stdout. Approvals are read from stdin.
- **Claude Projects:** every brief is also written to `data/briefs/`. Paste it into your Claude Project for deeper analysis.

## Architecture (one-liner)

```
You (Board) ──▶ CEO Agent ──▶ Division bots (PMCC / Fidelity / Crypto)
                  ▲                │
                  │                ▼
            Trend / Risk / Backtest / Portfolio / Data&Execution / Logger
```

LangGraph with `interrupt()` gates every live order behind Board approval; SQLite checkpointing means a crash mid-trade resumes from exactly the same state. Risk caps are enforced **deterministically in code**; the LLM only narrates rationales.

Full architecture lives in `.claude/plans/you-are-an-elite-compressed-micali.md`.

## Project layout

```
trading_corp/
├── main.py             # entrypoint
├── graph/              # LangGraph CEO graph + HITL interrupts
├── agents/             # CEO, Risk, Trend, Backtester, Portfolio, Data&Exec, Logger, divisions
├── brokers/            # Broker interface + paper / robinhood / coinbase / fidelity
├── data/               # WS aggregator, yfinance fallback, TradingView (optional)
├── comms/              # Telegram + CLI Board channels
├── persistence/        # SQLite engine + dataclass models + LangGraph checkpointer
└── utils/              # Secrets loader (with redaction), time helpers
config/
├── risk.yaml           # aggressive-but-capped risk profile
├── strategies.yaml     # division enablement + watchlists
└── agents.yaml         # LLM model assignment per agent
tests/                  # pytest suite
```

## Testing

```bash
pytest                                                          # full suite
pytest tests/test_risk_gates.py                                # risk caps
pytest tests/test_paper_trading_default.py                     # safety
pytest tests/test_graph_hitl.py                                # HITL graph
```

## Safety invariants (do not break)

1. **PAPER is the default on every startup.** Going LIVE requires `--live` AND interactive confirmation AND broker creds.
2. **Every live order requires Board approval** until per-strategy `auto_execute: true` is set in `config/strategies.yaml` (and even then, only under `auto_max_notional`).
3. **Risk caps are deterministic in code.** The LLM never overrides the Risk Agent.
4. **New strategies need backtest approval** before paper or live deploy.
5. **No credentials in logs.** Logger applies `RedactingFilter` automatically.

## Where to go next (Phase 3)

When you're ready to wire the Robinhood PMCC division live:
1. Set Robinhood env vars in `.env`.
2. Implement `trading_corp/brokers/robinhood.py` (skeleton already in place).
3. Implement `trading_corp/agents/divisions/pmcc_robinhood.py` — emits `ProposedOrder`s.
4. Run the Backtesting Agent's full impl (Phase 3) on the strategy.
5. Start in PAPER first; flip live only after multiple paper sessions look right to you.
