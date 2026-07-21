# kalshi_llm_arbitrage review — STOPPED AT STEP 2 (dashboard/DB divergence)

**Date:** 2026-07-21 · **Mode:** READ-ONLY · **Verdict:** the operator's reported numbers belong to a **different division** (`kalshi_arbitrage`). Per the task guardrail, no performance narrative was built on +$3,040.15. Steps 3–7 NOT executed.

## STEP 1 — What changed and when
The "logic change a couple weeks ago" = **2026-07-07 ~16:40 UTC** (14 days ago), deploy_log entry "Kalshi arb divisions". `main == origin/main == 0f79b22`.

| Commit | Change | Applies to |
|---|---|---|
| `b5eb93f` | `kalshi_llm_arbitrage.discovery.categories` → `[Economics, Elections]` (was Politics/Elections/Economics/Financials); hot-reload | kalshi_llm |
| (data op) | Deleted 1,563 non-Econ/Elections rows from `kalshi_round_trips` (results table only; audit/equity kept). 2508→945 | kalshi_llm |
| `d1f5ea6` | Resolver `ORDER BY COALESCE(expires_at, leg_date)` — **un-blinded `kalshi_arbitrage`** (0 round-trips for 2 months → started resolving; first tick resolved 100) | kalshi_arbitrage |
| `aa06498` / `0f79b22` | temporal/bucket 60-day horizon caps + ≥2-leg guard | kalshi_temporal_bucket |

Earlier kalshi_llm changes: strict-gate for Economics/Financials (divergence ≥30% + llm_prob extreme); Crypto + Climate/Weather stripped from discovery. Deploy_log's own 2026-07-07 verdict: **"NEITHER [llm nor arbitrage] has a demonstrated live edge; both stay paper."**

## STEP 2 — Reconciliation → DIVERGENCE → root cause

**The operator's reported "kalshi_llm_arbitrage" dashboard numbers are actually `kalshi_arbitrage`'s.** Exact match across every stat:

| Stat | Operator reported | `kalshi_arbitrage` (DB) | `kalshi_llm_arbitrage` (DB, all-time) |
|---|---|---|---|
| Resolved | 260 | **260** ✅ | 2,686 |
| Wins / Losses | 206 / 54 | **206 / 54** ✅ | 1,083 / 1,603 |
| Win rate | 79% | **79.2%** ✅ | **40.3%** |
| Realized P&L | +$3,040.15 | **+$3,040.15** ✅ | **−$472.67** |
| Equity | $532.84 | $532.84 (ambiguous) | $532.84 (both paper divs identical, n_positions=0) |

- The dashboard's own function `_query_pm_resolved_stats` (`web/data.py:4559-4575`) for `kalshi_llm_arbitrage` is `SELECT COUNT(*), SUM(won), SUM(realized_pnl) FROM kalshi_round_trips WHERE division='kalshi_llm_arbitrage'` (cutoff clause empty — no `DASHBOARD_RT_CUTOFFS` entry; no `metrics_epoch` set). Run against the live DB **right now it returns 2,686 / 40.3% / −$472.67** — not the operator's figures.
- +$3,040 / 79% WR has **never** existed for kalshi_llm in DB history (pre-deletion lifetime was −$518 / 2508 / 40.7% per deploy_log; peak cumulative ~+$141 in June).
- No table/filter reproduces the operator's numbers for kalshi_llm: `paper_trade_record` for this division = 0 rows; Econ+Elections subset = +$73.44; wins-only = +$1,130.33; unresolved `would_have_placed` = 4,147 (≠231).
- `kalshi_arbitrage`, by contrast, matches **all six** figures exactly.

**Most likely cause:** wrong dropdown selection — "Kalshi Arbitrage" vs "Kalshi LLM Arbitrage" are adjacent items; the identical $532.84 paper equity made the numbers look "confirmed." (Cannot fully rule out a dashboard slug cross-wiring from here, but every kalshi_arbitrage stat is internally self-consistent, pointing to a read of the arbitrage tile rather than a corrupted llm tile.) The "231 open" matches neither division's raw `would_have_placed` (arb=0, llm=4,147) and is unexplained — likely the dashboard's separate open-count query; not material to the finding.

### Honest topline for the division actually under review (context, not a narrative)
`kalshi_llm_arbitrage` is **net-negative and got worse after the 07-07 change**:
- All-time: 2,686 resolved / 40.3% WR / **−$472.67**.
- Monthly (resolved): May −$15.81 · June +$157.02 · **July −$613.87**.
- Since the 07-07 logic change (last 14d): 1,741 resolved / **37.2% WR / −$613.87**.
- Category (all-time): Economics +$108.40 is the only real positive; Sci&Tech −$281.82 (5 wins/304), Politics −$103.18, Financials −$49.17, Climate −$37.09, Elections −$34.97, Crypto −$25.02 drag it negative (carved-out categories' round-trips remain in the table).

## STOP — go/no-go
**NO-GO on Steps 3–7 as scoped.** The +$3,040.15 is a phantom for kalshi_llm_arbitrage; building sizing/edge/live-readiness analysis on it would be analyzing the wrong division. This is the exact "don't build conclusions on a metric the DB doesn't back" case.

**Fork for the operator (pick one; I did not assume):**
1. **Review `kalshi_llm_arbitrage` on its real numbers** — but the honest topline is net −$472.67 / 40.3% WR, worsening post-07-07; deploy_log already verdicted "no edge." A full 7-step review would mostly document why it's losing.
2. **Pivot the review to `kalshi_arbitrage`** — where the +$3,040.15 / 79% / 260 actually lives. ⚠ Caveat before celebrating it: that division had **0 round-trips for 2 months** and only started resolving on 2026-07-07 when the resolver leg_date fix drained its backlog — so +$3,040 may be a backlog-drain / newly-un-blinded artifact, not stable forward edge. Worth its own scrutiny (temporal vs bucket vs tail arb_type breakdown, time-series since 07-07, fee/slippage).
3. **Fix the dashboard ambiguity** — the two paper divisions showing identical $532.84 equity + adjacent dropdown names invites exactly this confusion; a distinguishing label/value would prevent recurrence.

## Guardrails honored
Read-only; no commits/deploys/config/roster changes; no flags flipped; no live recommendation. Stopped at the divergence fork rather than proceeding.
