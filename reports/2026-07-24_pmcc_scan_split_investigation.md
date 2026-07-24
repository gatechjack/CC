# PMCC scan-split — Build B item-0 investigation (report before restructuring)

Branch `pmcc-scan-split-2026-07-24` (off Build A `fd0f490`). READ-ONLY so far — nothing edited.
Purpose: the addendum's redesign splits the scan into a pre-open TRIAGE pass and a post-settle
ACTIONABLE pass. This maps what exists first.

## Scan entry points (all file:line)

| Pass | Trigger | Calls | Option-data source | Renders Approve cards? |
|---|---|---|---|---|
| **Pre-open scheduled** | `_scheduled_pmcc_scan_loop` (`main.py:2863`), window **8:30–9:25 ET** (`_scan_should_fire` `main.py:2794`), 5-min poll | `_on_scan` (`main.py:781`) → `pmcc_agent.scan` (`main.py:806`) | **Robinhood chain reads pre-market = LAST NIGHT'S settlement marks; bid/ask 0.0/stale.** `_find_best_weekly` (`pmcc_robinhood.py:3472`), `_short_roll_credit` + `net_limit_price` (`:3499/3578`) all off stale marks | **YES** (into `PendingComboRegistry`) |
| **Terminal 0-DTE** | same loop, `_terminal_should_fire` (`main.py:2837`), close−60min (15:00 ET) | `_on_terminal_scan` (`main.py:1268`) → `scan(zero_dte_only=True)` (`main.py:1281`) | Market open → **live** quotes | YES (0-DTE only) |
| **Manual `/scan` (Telegram)** | operator command, **NO window gate** (`telegram_bot.py:567`) | `_on_scan` unconditionally | live if run post-open | YES |
| **Web pair-analysis / execute** | `GET /pair-analysis/{sym}` (`routes.py:846`, 5-min cache), `POST /execute` (`routes.py:995`) | `analyze_symbol` / `propose_orders_for_pair` | live at request time | pair card |
| **Scout (page load)** | `GET /division/{slug}/scout` HTMX on load (`routes.py:1310`) | `scout_candidates` (`pmcc_robinhood.py:4006`) — NEW opens, not rolls | live | scout panel |
| **Reprice @ Approve** | `POST /approvals/pmcc-combos/{id}/decide` (`routes.py:1895`) → `reprice_combo` | `get_option_quote` per leg (`robinhood.py:1444`) | **live** | re-prices chosen strike only |

**"PMCCAgent detected N PMCC legs"** is logged at `pmcc_robinhood.py:1971` (end of `detect_existing_legs`,
`:1878`) — emitted by every scan/analyze/scout path, which is why it repeated 9:33–9:48 ET today
(user-driven re-scans + dashboard views), NOT a scheduled loop.

## The two findings that shape the redesign

- **A. No scheduled post-open actionable scan exists.** The only scheduled card source is the pre-open
  scan off stale marks. Today's good post-open cards (OPEN 4C, RKLB 75C) came from a *manual* re-scan on
  live quotes — the fix is to make that a *scheduled, liveness-gated* pass, and demote the pre-open scan
  to triage.
- **B. Cards are frozen at proposal time** (`combo_approval_view.py:76` reads `entry.net_limit_price`);
  reprice at Approve (`_pmcc_combo.py:117`) re-prices the chosen strike but does not re-select it. So a
  strike chosen off stale marks (e.g. RKLB $85C pre-open) can't be corrected at dispatch. This is why the
  RKLB card drifted ($85→$75) only when a *fresh* scan ran.

## Triage vs actionable split (feasible; split point ~`pmcc_robinhood.py:2051`)

- **Phase A — static / underlying-only (pre-open safe):** `snapshot`, `get_universe`,
  `detect_existing_legs` (position inventory), DTE from `short_leg_dte`, `_should_roll` (DTE + profit%),
  `_earnings_gate_state`, assignment/breach vs strike. No option-chain selection, no credit math.
- **Phase B — option-data-dependent (must be post-open):** `_find_best_weekly`/`_find_best_leap`
  (`:3472/3591`), `_short_roll_credit` + `net_limit_price` (`:3499/3578`), `_filter_liquid`
  (OI/vol/spread on option quotes), the early-release spot quote (`:2109`), and card build.

**Aside (out of scope, noted):** the scan's spot fetch `_fetch_prices` (`:2051`) uses **yfinance** for
LLM context — contrary to the brokerage-first policy. Not part of this redesign; flagged for a later fix.

## Proposed design (for your greenlight before I restructure)

1. **Pre-open scheduled pass → TRIAGE ONLY.** Run Phase A only: produce a morning watchlist
   (which shorts are near-DTE / breached / assignment-risk / earnings-blocked) as an audit + optional
   calm Telegram summary. **No** strike selection, credit math, liquidity gating on option quotes,
   Approve cards, or ABORTED alerts. Implemented via a `triage_only=True` path in `scan()` that returns
   after Phase A.
2. **New post-settle scheduled pass → ACTIONABLE.** Runs the full `scan()` (A+B) → cards off **live**
   marks. **Gated on quote LIVENESS** (preferred): a probe that options are open AND a reference chain
   returns two-sided quotes with sane spread/volume — so it self-waits past the 9:30–9:35 opening
   rotation. Configurable delay fallback (~9:38–9:45 ET). All windows/thresholds in config, not literals.
3. **Reconcile:** #3 settle-window was never built (dropped in Build A) → nothing to remove. Item-2
   consent guard stays as defense-in-depth. BULL-style persistent-low-volume names simply produce no
   post-settle candidate (no abort spam), because the pass runs on live quotes and item-4 already calms
   any residual abort.

**Deploy:** its own Stage-2 (separate from Build A) — it's an orchestration change and benefits from
independent review. Build A (money/observability fix) should deploy first.
