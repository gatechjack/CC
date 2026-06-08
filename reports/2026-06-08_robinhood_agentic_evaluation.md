# Robinhood Agentic Trading — Evaluation & Integration Scoping

**Date:** 2026-06-08 · **Author:** operator-supervised planning session · **Status:** planning report,
no code · **Base:** origin/main `58744bb`

## 1. Executive summary

Robinhood launched **Agentic Trading** (beta) on 2026-05-27, exposing an MCP server at
`agent.robinhood.com/mcp/trading` that lets an AI agent place **real equity trades** inside a
**separate, isolated agentic sub-account**. It is **not** a fix for our existing `robin_stocks`
session/"pickle" problem and does **not** expose our main-account, IRA, or PMCC positions for
execution. The capability that would matter most for integration — a **non-interactive / service-account
auth path** — is **not documented**; onboarding is desktop-browser, user-interactive. Combined with
equities-only scope, a two-week-old beta with no SLA, and full user liability, the recommendation is to
**defer formal integration** and, if curious, run a **minimal operator-driven manual exploration** via
Claude Desktop. Building a `trading_corp` broker adapter is **not** warranted today.

## 2. What it is / what it isn't (operator-clarifying)

**It is:** a beta product (2026-05-27) that connects an MCP-compatible agent (Claude, ChatGPT, Cursor,
Grok, etc.) to a dedicated Robinhood **agentic sub-account** to read portfolio data and place/cancel
**equity** orders, with per-trade-approval or autonomous modes, order preview, push notifications, a
user-set spending cap, and one-click disconnect.
[Robinhood Newsroom](https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/) ·
[Agentic Trading page](https://robinhood.com/us/en/agentic-trading/) ·
[Support: Overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) ·
[Support: Trading with your agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/) ·
[TechCrunch 2026-05-27](https://techcrunch.com/2026/05/27/robinhood-now-lets-your-ai-agents-trade-stocks/)

**It is NOT:**
- **Not a fix for the `robin_stocks` pickle problem.** That path serves the *main* account (IC, IRA,
  PMCC) and is untouched by Agentic Trading. Orthogonal capability.
- **Not options today.** Equities only at launch; options "rolling out," crypto/futures/predictions
  "coming soon."
- **Not access to main-account / IRA / PMCC positions for execution.** The agent has **read** access
  across accounts but can **execute only** in the isolated agentic account.

## 3. Architectural compatibility & pattern analysis

Trading Corp's binding invariant: **every order passes through `RiskAgent.evaluate()` — single
chokepoint, no bypass** (`CLAUDE.md:30-33`; verified across ~22 production call sites). Any integration
is judged first on whether it preserves this.

| Pattern | What it is | Risk gate | HITL | Engineering scope | Risk to existing systems | Verdict |
|---|---|---|---|---|---|---|
| **1. Agentic MCP as a normal `Broker` adapter** | New `brokers/robinhood_agentic.py : Broker`; orders flow strategy → `RiskAgent.evaluate()` → `data_exec.place()` → MCP `place_order` | **Preserved** (adapter sits below the gate; no `risk.py` change) | Preserved (existing `auto_execute`/Board flow) | **LARGE & GATED** — `trading_corp` must become an MCP client (new dependency, MCP `ClientSession`, credential storage in Key Vault) **and** there is **no documented non-interactive auth**; desktop-browser OAuth onboarding implies a human-in-the-loop token mint with unknown refresh | Low (added in parallel; existing pipelines untouched) | **Blocked on auth feasibility.** Correct shape *if* a programmatic auth path exists; today it doesn't. |
| **2. Hybrid — `trading_corp` proposes, operator runs Claude Desktop → MCP** | Orders generated as today; operator manually executes via Claude Desktop | **Broken** unless the human pastes only risk-approved orders — execution happens *outside* `trading_corp`, bypassing the gate | Full (human in loop) | Small | Low | **Reject** — defeats automation and routes execution around the single chokepoint. |
| **3. Parallel isolated system (no integration)** | Don't integrate; use Claude Desktop + the agentic account by hand for separate experiments | N/A (outside `trading_corp`) | Full | Small | **None** | **This is the minimal-exploration path** — learn the surface with zero risk to existing real-money pipelines (`CLAUDE.md` STOP-AND-READ #5). |

**Where an "agentic" division would sit, if ever built:** a new equities division
(`agents/divisions/robinhood_agentic.py` modeled on `pmcc_robinhood.py`) with an isolated equity
budget, a new `Broker` adapter, an entry in `config/divisions.yaml`, and `auto_execute: false` until a
paper track record is earned — per the standard "adding a new division" pattern. The governance rule
"build only after an existing pattern is validated in production; don't design speculatively" applies
directly and argues against doing this now.

## 4. Risk assessment

- **Maturity:** two-week-old beta; **no published rate limits, SLA, or deprecation policy**; no
  reported outages yet. Anthropic↔Robinhood is a protocol relationship (MCP), **not** a documented
  supported partnership.
  [TechCrunch](https://techcrunch.com/2026/05/27/robinhood-now-lets-your-ai-agents-trade-stocks/)
- **Blast radius:** funds are isolated to the agentic sub-account. A compromise of the MCP server or of
  our (hypothetical) MCP client credentials is bounded by that account's balance and the user spending
  cap — so **keep the account minimally funded** if ever used.
- **Compliance/liability:** FINRA broker-dealer, but the agent is **user-directed** and **the user is
  liable**; Robinhood "does not control, supervise, monitor, recommend, or audit" the agent; regulatory
  treatment of agentic execution is unresolved.
  [Newsroom](https://robinhood.com/us/en/newsroom/robinhood-is-now-open-to-agents/) ·
  [Agentic Trading page](https://robinhood.com/us/en/agentic-trading/)
- **Invariant tension:** Pattern 1 preserves the risk gate and HITL default; Pattern 2 breaks the
  single-chokepoint invariant. The "no `auto_execute: true` by default" rule applies unchanged to any
  agentic strategy.

## 5. Recommendation

**Defer formal integration.** If the operator wants to learn the surface, do a **Pattern-3 minimal
manual exploration** (open an agentic account, fund small, place one trade via Claude Desktop) — an
operator-gated action, out of scope for an automated session. **Do not build a broker adapter** until:
documented programmatic/service-account auth exists; GA; options/crypto support lands; rate limits/SLA
are published; and beta stability is observed.

**Smallest exploratory step that commits to nothing:** the Pattern-3 manual trial above.

**Proposed BACKLOG entry (for the operator to file):**
> `[BACKLOG] Robinhood Agentic Trading — revisit integration. DEFERRED 2026-06-08. Revisit when: (a)
> documented non-interactive/service-account auth, (b) GA out of beta, (c) options or crypto support,
> (d) published rate limits/SLA, (e) operator capacity. Pattern 1 (Broker adapter under the existing
> risk gate) is the only integration shape that preserves the single-chokepoint invariant; it is
> blocked today on auth feasibility and on trading_corp not being an MCP client. Ref:
> reports/2026-06-08_robinhood_agentic_evaluation.md.`

## 6. Open questions for the operator (need account/auth to resolve)

1. Exact MCP tool names and the order types exposed (only `analyze_concentration`, `read_analyst_notes`,
   `place_order` appear in public sources, secondary only — unconfirmed).
2. **Is there ANY non-interactive / service-account auth path?** (Gates Pattern 1 entirely.)
3. Is the MCP schema discoverable pre-auth (MCP `initialize`/`tools/list` unauthenticated)?
4. Minimum balance / funding mechanics for the agentic sub-account.

## 7. Operator decision required

**Pursue / Defer / Explore-minimally.** This report recommends **Defer**, with an optional
**Explore-minimally** (Pattern 3, operator-driven). If **Pursue** is chosen despite the above, the next
step is to resolve open question #2 (auth) before any engineering — Pattern 1 cannot start without it.
