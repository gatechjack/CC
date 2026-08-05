# kalshi_llm dashboard read-view vs raw table — reconciliation audit
**Date:** 2026-08-05 (probe 3 @ ~23:0xZ) · **Mode:** read-only (`PRAGMA query_only=ON`) · **Branch:** `claude-2026-08-02-kalshi-review` off prod-live `ef613e5`
**Trigger:** the operator's stated LLM forward verdict (17 distinct / 11W-6L / +$5.87) did not match the raw-table measurement (16 / 8W-8L / net $0.00). This audit reconciles the dashboard read-view (`_query_kalshi_distinct_market_stats`) against the raw `kalshi_round_trips` table on the live box.
**Evidence:** `2026-08-05_kalshi_review_probe3.py` / `..._probe3_output.txt` (committed alongside).

---

## TL;DR / Verdict

- **The dashboard read-view is FAITHFUL — it is NOT miscounting.** Run against the live DB it returns **16 distinct markets, 8W/8L, +$0.76 gross**, byte-identical logic to `b10a010`, with a **zero per-market diff** vs the raw table.
- **TRUE forward distinct-market number = 16 markets · 8W/8L (50%) · +$0.76 gross · $0.00 net-of-fee.** Confirmed three independent ways (raw ground truth, b10a010 read-view logic, LIVE deployed read-view) — all identical.
- **The operator's 17 / 11W-6L / +$5.87 is NOT reproducible** from the raw table or the deployed read-view under any tested scenario (live cutoff, b10a010 cutoff, no cutoff, all-divisions, event-collapse). It is **not a dashboard miscount** — origin appears external/manual (see §5).
- **My earlier hypothesis (§6 of the main review: "the figure most probably came from the drifted read-view") is REFUTED.** The read-view computes correctly.
- The read-view function is nonetheless **code-drifted** (present live, absent from prod-live git). Reconciling it is the **same single `b10a010` merge** as the resolver-epoch drift — a **deploy-gate hazard, not a data-integrity bug** (§4).

---

## 1. Where does the discrepancy enter? (STEP 1) — it doesn't

The live read-view SQL (verified byte-identical to `b10a010`):
```sql
WITH ranked AS (
  SELECT ticker, won, market_result, realized_pnl,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY entry_ts ASC, id ASC) AS rn
  FROM kalshi_round_trips
  WHERE division IN (?)                                    -- caller passes ['kalshi_llm_arbitrage'] (L5871)
    AND NOT (division='kalshi_llm_arbitrage'
             AND entry_ts < '2026-07-07T16:40:00+00:00')   -- _kalshi_cutoff_clause (== my raw cut)
    -- _kalshi_copy_mode_clause('all', …) => no-op
)
SELECT COUNT(*) n_resolved, COALESCE(SUM(won),0) n_wins,
       COALESCE(SUM(CASE WHEN COALESCE(market_result,'')='void' THEN 1 ELSE 0 END),0) n_voids,
       COALESCE(SUM(realized_pnl),0.0) total_pnl
FROM ranked WHERE rn = 1;
```

Scenarios run against the **live DB**:

| Scenario | n | W/L | gross P&L |
|---|---|---|---|
| **A — llm, LIVE cutoff (exactly what the dashboard runs)** | **16** | **8W / 8L** | **+$0.76** |
| B — llm, b10a010 cutoff (16:40) | 16 | 8W / 8L | +$0.76 |
| RAW ground truth (my probe: division=llm, entry_ts≥16:40, canonical=MIN entry_ts) | 16 | 8W / 8L | +$0.76 gross / **$0.00 net** |
| C — llm, NO cutoff (all-time) | 895 | 438 / 457 | −$111.50 |
| D — ALL kalshi divisions, live cutoffs | 3,909 | 2,239 / 1,670 | +$183.29 |
| (event-collapse by `event_ticker`, for reference) | 9 | — | — |

- **A == B == RAW.** Per-market diff: `in read-view but not raw: none` / `in raw but not read-view: none` (`|A|=16, |raw|=16, shared=16`).
- Live function source == `b10a010` (printed & compared); live `DASHBOARD_RT_CUTOFFS[kalshi_llm_arbitrage]` == `2026-07-07T16:40:00+00:00` == b10a010; single call site (L5871) passes one division. **No branch of the deployed pipeline produces 17/11W-6L/+$5.87.**

## 2. Which is correct? (STEP 2) — both; they agree

- **Raw `kalshi_round_trips` = ground truth** (each row carries its own `won`/`realized_pnl` from resolution). The read-view **aggregates it correctly**:
  - Dedup key = full **`ticker`** (correct — NOT `event_ticker`, which would over-collapse distinct strikes like FOMC-T8/T10).
  - Canonical = earliest emission via `ROW_NUMBER() … rn=1` (correct; models "enter once on first signal, hold to resolution").
  - No JOIN fan-out, no double-count, no dropped market.
- **W/L = 8/8 is invariant to the collapse method** — canonical-first AND sum-all-emissions both yield 8W/8L (probe 2). Only the P&L *attribution* differs (canonical gross +$0.76 vs sum-all-emissions gross +$111.26); the read-view reports the canonical view. Neither yields 11W/6L.
- **The raw query is not buggy either** — the two agree because both make the same (correct) modeling choice, and the W/L is robust across choices.

## 3. The TRUE forward distinct-market number (STEP 3)

> **16 distinct markets · 8W / 8L (50.0%) · +$0.76 gross · $0.00 net-of-fee.**
> (The dashboard field shows the **gross** +$0.76 — it does not subtract fees; the $0.00 is the per-order fee-adjusted figure. Neither equals +$5.87.)

This is the deployable-performance headline and it stands from the original review: a fee-eaten coin flip, statistically indistinguishable from zero, n far below any threshold.

## 4. Tie to the b10a010 drift (STEP 4) — same fix, but it's a code hazard, not a number bug

- **Numeric:** there is **nothing to fix** in the read-view's behavior — the deployed function computes the correct number.
- **Code:** the read-view function lives on the box but **not in prod-live git** — the same unmerged `b10a010` that also carries the resolver epoch clause. **Reconciling the read-view = the SAME single action as reconciling prod-live** (merge/cherry-pick `b10a010`); one fix covers both the resolver AND the read-view. They are **not separate fixes.**
- **Framing correction:** the hazard is a **deploy-gate integrity problem** (git does not reflect deployed reality → a future resolver/`data.py` deploy off prod-live would drift-gate against stale code), **not** a dashboard-correctness problem. **The dashboard is trustworthy right now.** This *lowers* the data-trust concern from the original review while leaving the deploy hazard fully intact.

## 5. So where did 17 / 11W-6L / +$5.87 come from? (open, non-blocking)

Not reproducible from the deployed system. The only distinct-market function has a **single call site** and provably returns 16/8W-8L. Candidates for the operator's figure: a manual tally / transcription error, a different ad-hoc computation, or a stale pre-resolution note. **If it was read off a specific dashboard screen, point me at that screen** — a UI showing 17/11W-6L would have to be a *different* code path (worth hunting). Absent that, **16 / 8W-8L / $0.00 net is the true number** and should supersede the +$5.87 figure.

---

## Appendix — provenance
- Probe 3 (`..._probe3.py`) opened the live DB read-only, read the live `trading_corp/web/data.py` (273,920 bytes) as text to dump `DASHBOARD_RT_CUTOFFS` + the `_query_kalshi_distinct_market_stats` source + call sites, then replicated the read-view SQL across the scenarios above and diffed per-market vs the raw canonical set. Output: `..._probe3_output.txt`.
- Confirms and closes the ⚠️ item in §6 of `2026-08-05_kalshi_arbitrage_and_llm_postfix_review.md`.
