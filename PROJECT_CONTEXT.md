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

A multi-agent automated trading system. The core architecture is one
**LangGraph CEO agent** that routes between several **division agents**, each
of which manages a separate brokerage account running its own strategy.
Every proposed order flows through a deterministic **risk gate** (code, not
LLM judgment) and then through a **HITL Board approval gate** before reaching
a broker. Default mode is PAPER on every startup. LIVE mode requires
explicit `--live` flag plus a confirmation prompt.

End goal: a personal infrastructure platform that runs trading bots for the
Board (Jack), eventually expanding to family member accounts (wife, kids)
and other non-trading apps. Long-term plan is multi-tenant on Azure with
proper isolation.

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
| Push | Telegram | Reliable; lock-screen UX is good enough on iOS |
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
| Per-trade risk | 1.5% of equity | `lord_otter`: 5% · `manual_coinbase_spot`: 5% · `crypto_scalper`: 0.5% |
| Per-strategy daily loss | 3% (halts strategy for the day) | `lord_otter`: 2% |
| Per-account max DD | 15% (auto-flatten + global halt) | none |
| Correlation cap (30d returns) | 0.7 between concurrent positions | none |
| Counter-trend size | 0.5× — **stocks only** (not options or crypto) | n/a |
| Vol scalar | `min(1, target/realized)` — **stocks only** | n/a |
| PMCC sizing | 1 contract per $25k equity per underlying | n/a |
| PMCC roll | 21 DTE or 50% profit | n/a |

## 6. Lord Otter strategy specifics

The flagship scalping strategy. Built on TradingView's webhook alerts
firing into our system. Some non-obvious decisions captured here so we
don't relitigate them.

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

## 8. Active deployment phase (`as of 2026-04-30`)

Mid-Azure-deployment-prep, paused pending blockers below:

- **Step A (Azure account)** — done
- **Step B (MFA + non-root Entra ID user `jack@<tenant>.onmicrosoft.com`)** — done
- **Step C ($150/mo budget alert)** — done
- **Step D (DNS delegation jacksumner.com → Azure DNS)** — pending
- **Step E (Azure CLI on laptop)** — pending
- **Deploy session (~3-4 hr): Bicep IaC, VM B2ms East US, VNet/NSG, Key Vault, Front Door + WAF, Azure Backup, Defender Plan 1, Caddy on VM, migrate 14 TV alerts to `https://trading.jacksumner.com`** — pending

## 9. Active blockers / known pain

1. **Cloudflared quick tunnel keeps dying overnight.** Free quick tunnels
   are not durable. Every restart rotates the URL, requiring all 14 TV
   alerts to be reconfigured. This blocks accumulating organic signal
   data — we can't grade the strategy until alerts reach the system reliably.
   The Azure deployment is the fix. Until then, accept that overnight
   signals are lost.
2. **No organic Lord Otter signal data yet.** Every event in the audit log
   to date is from manual test-script runs. Real strategy validation can't
   start until Cloudflared dies-and-rotates issue is solved (i.e., post-Azure).

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

These are real items, just not active:

- Phase 1.6 of Lord Otter: real ATR/swing-pivot stops, profit-target
  tracking, win/loss feedback into halt counters
- Telegram approval message enrichment Phase 2: position context (LEAP
  details, days held, prior-roll history, unrealized P&L)
- Paired-roll combination: today PMCC rolls fire as 2 separate approvals;
  should be 1 with both legs + net debit/credit
- Coinbase Futures wiring (Phase C, requires `coinbase-advanced-py`)
- Multi-tenant family expansion: separate Azure environments per family
  member
- Real macro calendar fetcher (FRED FOMC + BLS scraping into the existing
  `config/macro_calendar.yaml` format)
- JSON `/api/v1/*` endpoints (only if PWA isn't enough — currently not
  scoped)
- Authentication beyond shared-secret (Sign in with Apple or magic-link
  email, before public exposure)

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
trading_corp/agents/divisions/      ← per-strategy agents
   pmcc_robinhood.py                  PMCC strategy
   fidelity_options.py                Fidelity options
   lord_otter.py                      TradingView-driven scalper
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

- **Board** — the user (Jack). Approves or rejects orders via Telegram or dashboard.
- **Division** — one account at a broker (e.g., `robinhood_pmcc`, `coinbase_spot`).
- **Strategy** — logic that operates on a division and emits ProposedOrders.
- **HITL** — Human-in-the-loop. The Board-approval gate.
- **Tier** (Lord Otter context) — conviction level for a signal cluster, drives sizing.
- **Arming** — Pre-trigger state set by Pink Box / Spoon, lasts N bars.
- **Black Sheep** — PMCC underlyings (TSLA, MSTR) that follow special rules
  (perpetual roll, never accept assignment).
- **Dry-run** — `--live --dry-run` mode that runs the full LIVE pipeline
  but skips `broker.place_order()`.

---

*Last meaningful update: 2026-04-30 — initial draft after the Phase 1.5 + Telegram enrichment + Azure prep session.*
