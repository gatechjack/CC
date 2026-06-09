# Robinhood Agentic Trading — Pattern-3 Manual Exploration (hands-on log)

**Date:** 2026-06-09 · **Author:** operator-driven Pattern-3 manual exploration (Claude Desktop) ·
**Status:** live exploration log (incremental — scribed during operator session) ·
**Base:** main `aa0b7dd` · **Branch:** `robinhood-agentic-pattern-3-exploration-2026-06-09` (unmerged) ·
**Companion to:** [`reports/2026-06-08_robinhood_agentic_evaluation.md`](2026-06-08_robinhood_agentic_evaluation.md)

> This is the **Pattern-3 minimal manual exploration** the 2026-06-08 evaluation report recommended as the
> zero-risk way to learn the surface (eval §3 Pattern 3, §5, §7). It is operator-driven via Claude Desktop;
> Claude Code cannot execute the connection or trades. Findings here resolve several of the eval report's
> open questions (§6).

---

## 1. Setup & connection (Phase 1)

- **Agent platform:** Claude Desktop (web app, per operator screenshot).
- **MCP connector:** `https://agent.robinhood.com/mcp/trading`, added as a **custom connector** named
  **"Robinhood Agentic."**
- **Connection state:** Connected. Default per-tool permission is **"Needs approval"** for **all 22 tools**
  (no tool pre-authorized; every call requires operator approval by default).
- **Account creation:** completed during the Robinhood **OAuth** flow (operator confirms the agentic
  sub-account was created as part of onboarding — no separate pre-step needed).

_Resolves eval §6 open-question framing: onboarding is desktop-browser, user-interactive OAuth (confirms
the "no non-interactive auth path observed" premise — see §7 trigger (a))._

---

## 2. Tool surface (Phase 2)

**Total exposed: 22 tools — full surface confirmed (operator screenshots 1 + 2).** Categorized (operator
taxonomy):

- **Account reads (3):** `get_accounts`, `get_portfolio`, `get_equity_positions`
- **Market data reads (3):** `get_equity_quotes`, `get_equity_tradability`, `search`
- **Order management (4):** `place_equity_order`, `review_equity_order`, `cancel_equity_order`, `get_equity_orders`
- **Watchlist management (9):** `add_to_watchlist`, `remove_from_watchlist`, `add_option_to_watchlist`, `remove_option_from_watchlist`, `get_options_watchlist`, `get_watchlists`, `get_watchlist_items`, `create_watchlist`, `update_watchlist`
- **Curated list management (3):** `get_popular_lists`, `follow_list`, `unfollow_list`

Count check: 3 + 3 + 4 + 9 + 3 = **22**. ✓

### Findings

- **Trading verb confirmed — preview and place are two separate calls.** `place_equity_order` exists, with
  a distinct `review_equity_order` preview tool. The docs' "preview before placement" is implemented as
  **two separate tool calls**; the agent + operator approval-gate sits **between** `review` and `place`
  (every tool defaults to "Needs approval" — §1). _(Behavioral confirmation in Phase 3, §4.)_
- **Options have a READ surface only.** No `place_option_order` / `review_option_order` — the three option
  tools are watchlist/read (`add_option_to_watchlist`, `remove_option_from_watchlist`,
  `get_options_watchlist`). **Confirms the equities-only beta scope.** _(Resolves the §2 open probe;
  sharpens watch-trigger (c) — see §7.)_
- **Watchlist surface is unusually heavy — 9 of 22 (~41%).** Positions the **watchlist as a primary
  agent-managed concept**, not an afterthought.
- **No bulk-position or batch-order tools.** Each tool operates on a single concept; multi-step workflows
  (rebalance, basket entry) must be **agent-composed** from single-item calls. Implication for any future
  Pattern-1 adapter: no atomic batch primitive to lean on.
- **No crypto / futures / prediction-market / event-contract tools.** Confirms the "coming soon"
  marketing — none of those asset classes are exposed yet.

**Secondary-source unconfirmed tools (eval §6 q1) — adjudicated:**

- `analyze_concentration` — **does NOT exist** as a tool. Plausibly a *capability* composable via
  `get_equity_positions` + agent analysis, not a first-class verb.
- `read_analyst_notes` — **does NOT exist** as a tool. May be surfaced indirectly via `search`.
- `place_order` — **actual name is `place_equity_order`** (more specific, asset-class-qualified naming).

**Net:** the eval report's unconfirmed-tool caveat is **confirmed** — secondary sources were **inaccurate
on tool naming**, but the **functional categories** they implied (read positions, trade equities) **do
exist**. _(Fully resolves eval §6 q1.)_

### Open probes carried forward (from eval §6)

- q1 — exact tool names: **resolved** (full 22-tool surface confirmed; trade verb `place_equity_order`).
  Supported *order types* still pending the behavioral preview in §4.
- q3 — is the MCP schema discoverable pre-auth (`tools/list` unauthenticated)? _Not yet tested._

---

## 3. Read probes (Phase 2 cont.)

Four read probes run across the connected accounts. **Headline: read scope ≫ write scope** — the MCP
token reads *every* account on the login; only the Agentic sub-account can be traded. (This asymmetry is
material enough to be filed as a standalone eval finding — see the memory entry
`project_robinhood_agentic_evaluated_deferred.md`, "Read-vs-write scope asymmetry.")

- **`get_accounts` — cross-account read confirmed.** **7 accounts** visible: main, IRA, managed, joint,
  Mortgage cash, Coinbase cash, Agentic. **Only Agentic has `agentic_allowed=true`.** Read scope ≠ write
  scope — the token sees all 7, can trade only 1.
- **`get_portfolio` — isolated sandbox is live.** Agentic funded at **$75 cash, all uninvested.** Confirms
  the funded agentic account exists and is currently empty (no positions yet).
- **`get_equity_quotes` — single-symbol read works, sub-second perceived latency.** Returns **both** the
  regular-session close **and** the after-hours print; **the caller must select most-recent-by-timestamp.**
  _Adapter implication:_ any downstream consumer must replicate the timestamp-comparison logic or risk
  reporting a **stale regular-session price during extended hours.** (Also evidence the quote is **live**,
  carrying the current extended-hours print — not a cached regular-session value.)
- **`get_equity_orders` (across all 7 accounts) — fully successful.** Each account returned its history.
  **Three accounts (main, IRA, managed) hit context-size limits and required spillover.** **Pagination cap
  is 200 orders/account.** Confirms read **truly spans every account.**

- **Option chain (Probe 5) — no enumeration path exists in the 22-tool surface.** The options tools
  (`add_option_to_watchlist`, `get_options_watchlist`) require **contract UUIDs sourced from
  `get_option_instruments` — which is NOT in this MCP.** So the option watchlist tools are **functionally
  unusable through the MCP alone**: you can't add what you can't discover. PMCC's existing `robin_stocks`
  broker adapter remains the established option-chain path; Agentic MCP provides no equivalent.
  _Implication:_ the options surface here is **broker-data-exposed but not discoverable**, which sharpens
  watch-trigger (c) further — it must require **option-chain discovery** in addition to trading execution;
  without `get_option_instruments`, even a hypothetical `place_option_order` would be of limited use (§7).
- **`search` (Probe 6) — name → instrument lookup only, NOT a fundamentals screener.** Robinhood's
  marketing ("screen for stocks growing 20% annually") is **misleading**: the actual implementation is
  **LLM-orchestrated web research** using `search` only for ticker resolution — the intelligence is in the
  LLM, not the MCP. No analyst-notes access via MCP (the secondary-source `read_analyst_notes` surfaces
  neither as a tool nor as a discoverable capability through `search`). _Implication:_ the "AI-powered
  screening" framing is **not an MCP capability** — it's a wrapper around generic LLM research.
- **`place_equity_order` schema (Probe 7) — full order surface, schema-level (pre-live).**
  - Order types: **`market`, `limit`, `stop_market`, `stop_limit`. No trailing stop.**
  - Time-in-force: **`gfd` (day), `gtc`.** No IOC/FOK/OPG.
  - Extended hours: **yes**, via `market_hours` = `regular_hours` (default) / `extended_hours` /
    `all_day_hours`. **Fractional and dollar-based orders are rejected outside regular hours.**
  - Sizing: exactly one of **`quantity`** (share count; fractional allowed only for `market` +
    `regular_hours`) **XOR `dollar_amount`** (USD notional, `market` type only).
  - Idempotency: optional **`ref_id` UUID** for retry safety. **Hard gate:** must be an
    **`agentic_allowed=true`** account.
  - _Pattern-1 implications:_ **no trailing stop** ⇒ trading_corp's advanced-SL ratcheting (Bitunix
    pattern) can't translate directly; an adapter would ratchet via **cancel + replace** per stop update.
    **No IOC/FOK** ⇒ liquidity-sensitive/HFT patterns unavailable (fine for retail). **Extended-hours
    support exists** (broader than initially assumed). **`ref_id` idempotency** signals the API was
    designed for **programmatic** use, not just interactive.
- **`review_equity_order` shape (Probe 8) — pure pre-trade simulation, no side effects.** Returns the
  current quote + **pre-trade alerts** (buying power, **PDT** pattern-day-trader, instrument halt) — a good
  regulatory safety surface. Tool guidance **recommends a marketable-limit-at-ask over a plain market
  order** (price protection).

### Still pending
- **Live test trade (§4)** — actual `place_equity_order` execution; preview → approval → fill behavior,
  latency, push notification. (Order schema known from Probe 7; live behavior not yet observed.)
- Disconnect (§5) and reconnect/close (§6).

_(Resolved: option-chain discovery — Probe 5; `search` capability — Probe 6. Positions-empty covered
indirectly by `get_portfolio` (all uninvested). No server rate limits hit — only client context-size
spillover on the 3 high-history accounts.)_

---

## 4. Single test trade (Phase 3)

**SKIPPED.** The Probe 7 schema inspection (§3) sufficiently validated the **shape** of the write path; an
actual trade adds only incremental signal and **does not change the Defer verdict** — the **auth blocker**
(a), not trade-execution mechanics, governs usability for trading_corp. The two-step `review_equity_order`
→ `place_equity_order` flow and the "Needs approval" default are understood structurally (§3 Probes 7–8).
Deferred indefinitely; structural findings supersede the need.

---

## 5. Disconnect check (Phase 4)

**SKIPPED.** The one-click-disconnect safety control is documented; the operator can validate it
interactively in future without scribe involvement if ever relevant. Not load-bearing for the verdict.

---

## 6. Reconnect + close (Phase 5, optional)

**N/A** — no test trade was placed (§4 skipped), so there is nothing to reconnect for or close. The
agentic account remains funded at $75 cash, uninvested.

---

## 7. Synthesis (for future-you)

**Verdict: DEFER formal integration — unchanged from the 2026-06-08 evaluation.** Today's hands-on
Pattern-3 exploration **validates that verdict empirically** (the auth blocker is real and unmoved) and
**sharpens the watch-triggers**. Pattern 1 (broker adapter under the single risk-gate) remains infeasible
today. Maps to the Output checklist:

- **MCP tools that actually exist** (vs. the three secondary-source names): see §2 — full 22-tool surface,
  `<verb>_<object>` convention; the three cited names do not exist (trade verb is `place_equity_order`,
  preview verb `review_equity_order`; no batch/bulk and no crypto/futures/prediction tools).
- **Order types (supported, per Probe 7 schema):** `market`, `limit`, `stop_market`, `stop_limit` — **no
  trailing stop**; TIF `gfd`/`gtc` (no IOC/FOK/OPG); extended-hours via `market_hours`. _Live confirmation
  of an actually-placed order pending §4._
- **Preview / approval flow:** known **structurally** (not live-tested — §4 skipped): a two-step
  `review_equity_order` (pure pre-trade sim with PDT/BP/halt alerts — Probe 8) → `place_equity_order`,
  every tool defaulting to "Needs approval" (§1). Sufficient to characterize the write path's shape.
- **Notification quality:** **not tested** (§4 skipped); Robinhood's real-time-alerts claim is unverified
  here — operator can confirm interactively in future if ever relevant.
- **Surprises (positive / negative):** options are read-only **and not even discoverable** (no
  `get_option_instruments` — Probe 5); `search` is ticker-lookup only, so Robinhood's "AI screening"
  marketing is an **LLM wrapper, not an MCP capability** (Probe 6); **extended-hours trading exists**
  (broader than assumed) and **`ref_id` idempotency** signals deliberate programmatic design (Probe 7,
  positives); offset by **no trailing-stop / IOC / FOK** (Probe 7) and watchlist management at ~41% of the
  surface; **no batch/bulk, no crypto/futures/prediction** tools. _Live trade-flow surprises pending §4._
- **Pattern-1 watch-triggers — final reassessment.** Priorities unchanged from 2026-06-08; today's
  findings sharpen the wording:
  - **(a) auth** — **unchanged, load-bearing.** Direct evidence: the OAuth flow is interactive,
    browser-based, desktop-only for setup; **no service-account or programmatic-renewal path documented or
    discovered.** This — not trade-execution mechanics — determines usability for trading_corp.
  - **(b) GA** — n/a from this session (still beta).
  - **(c) options** — full surface confirms options are **watchlist/read-only AND not even discoverable**
    (no `get_option_instruments` to enumerate contracts — Probe 5). **Recommend refining trigger (c) to:
    "options or crypto *trading-execution* support, including instrument-chain discovery."**
    Trading-execution alone is incomplete — without chain discovery even a future `place_option_order` is
    of limited use. The current state (option *watchlist* tools with **no discovery**) is itself the
    warning sign that trading-execution could ship in similarly incomplete shape. Same logic extends to
    crypto/futures/prediction — none have execution verbs today.
  - **(g) — NEW trigger (soft; ADOPTED by operator at close-out):** a **trailing-stop order type, or an
    equivalent ratcheting primitive** supporting trading_corp's existing stop-management patterns (Bitunix
    advanced-SL). **Not strictly required** for initial Pattern 1 (ratcheting is achievable via cancel +
    replace — Probe 7), but it eliminates a friction layer in adapter implementation. **Added to BACKLOG
    P3 at close-out.**
- **Additional material finding (NOT in the 2026-06-08 evaluation): read-vs-write scope asymmetry.** The
  MCP token grants **full cross-account READ visibility** (7 accounts incl. IRA / advisory-managed) despite
  write-isolation to the agentic sub-account (§3). Any future Pattern-1 implementation needs **explicit
  Board acknowledgment of the data-exposure scope** (CLAUDE.md §4) before any read-adapter wiring. Captured
  in auto-memory: `2026-06-08-robinhood-agentic-evaluated-deferred.md`.

---

## 8. Cross-references

- **Evaluation report (2026-06-08):** `reports/2026-06-08_robinhood_agentic_evaluation.md` (branch
  `robinhood-agentic-evaluation-2026-06-08`, unmerged). This exploration is the Pattern-3 trial that report
  recommended; the Defer verdict is unchanged.
- **BACKLOG:** `BACKLOG.md` P3 — "Robinhood Agentic Trading: revisit integration (DEFERRED 2026-06-08)."
  Updated at close-out with a reference to this report, the sharpened Trigger (c), the new Trigger (g), and
  a pointer to the read-vs-write finding.
- **Auto-memory:** `2026-06-08-robinhood-agentic-evaluated-deferred.md` — read-vs-write scope asymmetry +
  Pattern-3 completion (verdict unchanged).
- **This report:** branch `robinhood-agentic-pattern-3-exploration-2026-06-09`, pushed to origin unmerged
  (mirrors the 2026-06-08 evaluation-report pattern).
