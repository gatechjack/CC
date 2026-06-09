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

_Pending operator observations._

- Positions (expect empty initially): _pending._
- Supported order types (market/limit/stop/stop-limit/…): _pending (Phase 3 preview will reveal)._
- `get_equity_quotes` for SPY (basic read): _pending._
- Option chain for SPY (likely fails — equities-only beta): _pending._
- Cross-account recent transactions (tests read-across-accounts): _pending._
- **Response latency:** _pending._
- **Live web quote vs. cached data:** _pending._
- **Errors / rate limits hit:** _pending._

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
- **Order types that worked:** _pending §4._
- **Preview / approval flow observed:** _pending §4 (surface confirms two-step `review_equity_order` →
  `place_equity_order`; default "Needs approval" on all 22 tools)._
- **Notification quality:** _pending §4._
- **Surprises (positive / negative):** options expose a 3-tool *read-only* surface yet no trade verb (§2);
  watchlist management is ~41% of the surface (9/22); **no batch/bulk tools** (workflows must be
  agent-composed); **no crypto/futures/prediction tools** (confirms "coming soon"); _trade-flow surprises
  pending §4._
- **Do the Pattern-1 watch-trigger thresholds still feel right (auth + GA + options)?** _Reassess at end._
  Early read:
  - **(a) auth** — exploration so far reconfirms desktop-browser interactive OAuth only; no non-interactive
    path surfaced. Trigger holds; still the load-bearing blocker.
  - **(b) GA** — n/a from this session (still beta).
  - **(c) options** — full surface confirms options are **watchlist/read-only** (no option-trading verb).
    **Recommend sharpening trigger (c)** to fire specifically on an **option *trading* verb**
    (`place_option_order`) landing — the existing option *watchlist* tools are irrelevant to Pattern-1,
    which is about execution. As written ("options or crypto support lands"), (c) risks a false trigger on
    a watchlist-only update; tighten to "option/crypto **trading-execution** support." Same logic extends
    to crypto/futures/prediction — none have execution verbs today.
