# Kalshi copy — roster re-selection feasibility (read-only, cached)

**Date:** 2026-07-26 · **Question:** does a *copyable* (readable under the side-inference ceiling) and *persistent* whale roster even EXIST on Kalshi, or is the venue's whale population skewed toward the scalar/multivariate/politics markets the bot can't read?
**Method:** cached audit only — no scraping spend. Per-whale **copyability = copies / (copies + no_side skips)** from `audit_event` (`would_have_placed` + `kalshi_copy_placed_live` vs `kalshi_copy_entry_skipped_no_side`; no_fill excluded per operator ruling). This is the **direct empirical readability measure** — a low no_side rate means the whale trades markets the size-match inference can read.
**Answer:** **A copyable cohort clearly EXISTS — the venue is NOT uniformly unreadable.** But it is whale-dependent (copyability spans 0.5%→99.4%), *persistence* is unproven (3-month window; the most-copyable whales faded by July), and **copyability ≠ profitability** (unassessed). The current 2-whale roster is sub-optimal on copyability; a clear re-selection candidate exists.

---

## Copyability + cadence ranking (all observed whales, ≥20 detections)

| whale | copies | no_side | **copyability** | months | active days | span | note |
|---|---|---|---|---|---|---|---|
| the.hoff.85 | 2,299 | 13 | **99.4%** | 2 | 17 | Jun–Jul | readable + **active into July** |
| tom14cat14 | 738 | 44 | 94.4% | 2 | 31 | May–Jun | readable, faded (last Jun) |
| smedtoshi | 3,351 | 946 | 78.0% | 2 | 36 | May–Jun | readable, faded (last Jun) |
| **MaggieTheEagle** ★sel | 39 | 13 | 75.0% | 3 | 16 | May–Jul | readable but low-activity |
| leftwithnothing | 218 | 95 | 69.6% | 1 | 12 | Jun | one-month |
| pritz786 | 193 | 113 | 63.1% | 2 | 14 | Jun–Jul | |
| **AI.EDGE** ★sel | 42 | 31 | 57.5% | 2 | 23 | Jun–Jul | **middling + degrading** (July ~31%) |
| szg.szg | 246 | 179 | 57.9% | 1 | 16 | Jun | |
| reach.draft | 74 | 76 | 49.3% | 1 | 4 | May | |
| warm.slope | 76 | 355 | 17.6% | 1 | 14 | Jun | mostly unreadable |
| teafordong | 65 | 433 | 13.1% | 1 | 9 | Jun | mostly unreadable |
| lengthy.starfish | 10 | 1,854 | **0.5%** | 3 | 29 | May–Jul | the anti-pattern: active but unreadable |

★sel = currently in the Selected roster.

---

## Findings

1. **Copyable whales exist — feasibility YES.** Four whales are ≥75% readable (the.hoff.85 99.4%, tom14cat14 94.4%, smedtoshi 78%, Maggie 75%). This **disproves** "the entire venue skews to unreadable markets." The size-inference ceiling is real but whale-specific, not venue-wide.

2. **The current roster is sub-optimal on copyability.** Of the two Selected whales, AI.EDGE is only 57.5% readable **and degrading** (recent July ≈31% as it drifted into scalar/multivariate markets), and Maggie is 75% but very low activity (39 copies). **the.hoff.85 (99.4%, active into July, 2,299 copies) is dramatically more readable than either** — the clearest re-selection candidate on the copyability axis.

3. **The unreadable tail is real too** — lengthy.starfish (0.5%), teafordong (13%), warm.slope (18%) are structurally uncopyable (active but unreadable). Confirms the ceiling exists; selection must screen it out.

4. **Persistence is UNPROVEN.** Observation window is only ~3 months (May–Jul). The two most-copyable high-volume whales (tom14cat14, smedtoshi) already **faded by end-June** (last activity Jun). Only the.hoff.85 combines high copyability with July activity. "Year-round" cannot be established from this window — copyable whales exist *now*, but whether a copyable roster *persists* is the open risk.

---

## Answering the operator's questions

- **What would a copyable roster look like?** Whales like **the.hoff.85** — dominated by readable markets (≤1% no_side) and recently active. Screenable directly by the no_side ratio (which S2 fix (a)+(b) would surface on the dashboard going forward).
- **Does a copyable roster EXIST?** **Yes, a copyable cohort exists in the already-observed population** (no scraping needed to establish this). It is NOT true that the venue is entirely unreadable.
- **Is Kalshi copy structurally tournament-only?** **No** — readable non-tournament whales exist (the.hoff.85's readable markets are mostly non-World-Cup; Maggie is the tournament-concentrated one). So the tournament-concentration is a property of the *current* roster, not a venue-wide constraint.
- **Does this justify investing in Kalshi copy?** It justifies **enough investment to manage re-selection** (i.e., S2 metrics so copyability/P&L per whale is visible), and points to a concrete first move (evaluate the.hoff.85-profile whales, deprioritize AI.EDGE). It does **NOT** by itself justify heavy investment, because of the two unresolved axes below.

## Two axes this pass does NOT resolve (both gate a real invest decision)
1. **Copyability ≠ profitability.** This measures whether we can *read/copy* a whale, NOT whether copying them *makes money*. The division is net-negative overall; a 99.4%-copyable whale can still be a net loser. Per-whale copy-P&L at n≥30 is required — which needs the S2 metrics + forward accumulation. **No edge/prospect claim is made here.**
2. **Persistence.** 3-month window can't prove year-round activity; the top-copyable whales already faded. A re-selected roster could go dormant like the current one.

## If the operator wants to go beyond the observed population (live-scraping cost — NOT run)
The above uses only whales the discovery pipeline already detected. To scan the **broader Kalshi leaderboard** for NEW copyable candidates (whales we've never tracked), each candidate needs an Apify profile/history scrape to classify readability — **that is live scraping spend**. Rough cost is one Apify actor run per candidate profile (the existing `watch_only` deep-scan pipeline). **STOP-and-cost before any such run** — but note it is not necessary to answer the feasibility question (already YES from cache); it is only needed to *expand* the candidate pool.

---

**Caveats:** copyability is a mechanical readability metric (side-inference success rate), not an edge signal. Ticker market-type classification was too coarse to characterize *what* the readable whales trade (an "other" catch-all dominated) — the no_side ratio is the reliable measure, not the category labels. Cadence is over a ~3-month window only. Cached-only; no scraping spend, no roster change, no edge/prospect memory written.
