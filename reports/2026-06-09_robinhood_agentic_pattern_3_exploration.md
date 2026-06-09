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

_Pending. Candidate: 1 share of a cheap liquid name (SOFI ~$15 / F ~$12 / T ~$22)._

- Preview shown first? _pending._
- Explicit approval required, or auto-fire? _pending (default is "Needs approval" — see §1)._
- Latency from "yes, place" → fill confirmation: _pending._
- Fill confirmation contents: _pending._
- Phone push notification received? _pending._
- Order types actually offered in the preview: _pending._

---

## 5. Disconnect check (Phase 4)

_Pending. Disconnect agent via Robinhood mobile app; retry a trade via Claude Desktop; expect clean
"not connected" failure._

---

## 6. Reconnect + close (Phase 5, optional)

_Pending / may skip. Reconnect, sell the share, observe close flow — or leave the position open._

---

## 7. Synthesis (for future-you)

_Filled at end. Maps to the Output checklist._

- **MCP tools that actually exist** (vs. the three secondary-source names): see §2 — full 22-tool surface,
  `<verb>_<object>` convention; the three cited names do not exist (trade verb is `place_equity_order`,
  preview verb `review_equity_order`; no batch/bulk and no crypto/futures/prediction tools).
- **Order types (supported, per Probe 7 schema):** `market`, `limit`, `stop_market`, `stop_limit` — **no
  trailing stop**; TIF `gfd`/`gtc` (no IOC/FOK/OPG); extended-hours via `market_hours`. _Live confirmation
  of an actually-placed order pending §4._
- **Preview / approval flow observed:** _pending §4 (surface confirms two-step `review_equity_order` →
  `place_equity_order`; default "Needs approval" on all 22 tools)._
- **Notification quality:** _pending §4._
- **Surprises (positive / negative):** options are read-only **and not even discoverable** (no
  `get_option_instruments` — Probe 5); `search` is ticker-lookup only, so Robinhood's "AI screening"
  marketing is an **LLM wrapper, not an MCP capability** (Probe 6); **extended-hours trading exists**
  (broader than assumed) and **`ref_id` idempotency** signals deliberate programmatic design (Probe 7,
  positives); offset by **no trailing-stop / IOC / FOK** (Probe 7) and watchlist management at ~41% of the
  surface; **no batch/bulk, no crypto/futures/prediction** tools. _Live trade-flow surprises pending §4._
- **Do the Pattern-1 watch-trigger thresholds still feel right (auth + GA + options)?** _Reassess at end._
  Early read:
  - **(a) auth** — exploration so far reconfirms desktop-browser interactive OAuth only; no non-interactive
    path surfaced. Trigger holds; still the load-bearing blocker.
  - **(b) GA** — n/a from this session (still beta).
  - **(c) options** — full surface confirms options are **watchlist/read-only AND not even discoverable**
    (no `get_option_instruments` to enumerate contracts — Probe 5). **Recommend refining trigger (c) to:
    "options or crypto *trading-execution* support, including instrument-chain discovery."**
    Trading-execution alone is incomplete — without chain discovery even a future `place_option_order` is
    of limited use. The current state (option *watchlist* tools with **no discovery**) is itself the
    warning sign that trading-execution could ship in similarly incomplete shape. Same logic extends to
    crypto/futures/prediction — none have execution verbs today.
  - **(g) — NEW candidate trigger (soft; OPERATOR DECIDES):** a **trailing-stop order type, or an
    equivalent ratcheting primitive** supporting trading_corp's existing stop-management patterns (Bitunix
    advanced-SL). **Not critical** for initial Pattern 1 (ratcheting is achievable via cancel + replace —
    Probe 7), but it removes a friction layer in adapter implementation. _Not yet adopted into BACKLOG —
    pending operator yes/no at close-out._
