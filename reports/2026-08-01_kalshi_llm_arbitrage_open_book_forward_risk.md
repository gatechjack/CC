# kalshi_llm_arbitrage — open-book forward-risk read (0 resolved; NOT a P&L review)

**Date:** 2026-08-01 · **Mode:** READ-ONLY (no code/config/roster/DB/deploy; artifact uncommitted per guardrail).
**Division:** `kalshi_llm_arbitrage` · Strategy `kalshi_llm_arbitrage` (LLM-divergence detector). **PAPER** — `standby:true`, `broker:kalshi` on the **shared** `KALSHI-*` account (NOT Karen; Karen isolation was kalshi_arbitrage only), audit kind `would_have_placed` (never `placed_live`). Every number below is a **paper ceiling, not realized money.**
**Scope:** the dashboard is epoch-scoped to `2026-07-07T16:40Z` and shows **0 resolved / +$0.00 / 294 open** — CORRECT (nothing in the post-07-07 window has resolved yet). **0 resolved ⇒ no performance verdict is possible.** This review is the OPEN BOOK's composition + forward risk + settlement calendar.
**Open book defined** (matches `_query_pm_open_trades`): `audit_event` `would_have_placed`, `actor=kalshi_llm_arbitrage`, `division=kalshi_llm_arbitrage`, `side=buy`, `ts>=2026-07-07T16:40Z`, no matching round-trip.

---

## BOTTOM LINE (lead numbers)

1. **Concentration: 2 event-views = 72% of the book.** Michigan-Senate-primary voter-turnout = **112 (38.1%)**, 2-year-Treasury-yield-on-July-FOMC-day = **101 (34.4%)**. Top-3 = 80.6%, top-4 = 88.8%. The "294 positions" is **~2 dominant views expressed many times** — the same correlated-legs-on-one-event pattern as kalshi_arbitrage's farm bill / KXFDAAPPROVE-GED.
2. **High-divergence exposure: 141 positions (48% of the book, $141) sit at ≥35% divergence — essentially the ENTIRE Economics book** (140 of 141 Economics ≥35%; max 85.5%). This is the exact profile the pre-epoch review flagged as inverted-calibration (40%+ divergence → 10.7% WR). The 2Y-Treasury-FOMC stack (101 positions, div **45–56%**, NO) is squarely in it — **and it is the FIRST big settlement (08-05).**
3. **★ The "294" is inflated ~9.5×.** 294 positions across only **31 distinct markets** — daily re-emission of the same bets (2Y-Treasury = 2 markets ×~50; Michigan = 7 markets, one ×33). True distinct exposure = **31 market-bets across 14 events**, ~$31 dedup'd. Interpret "294 open / $294 stake" accordingly.

---

## STEP 1 — Open-book composition (294 positions / 31 markets / 14 events)

**Category (all 294 are NO bets):** Elections 153 (52%, $153) · Economics 141 (48%, $141). Clean [Economics, Elections] split — the 07-07 discovery narrowing is holding.

**Event concentration (dashboard positions; distinct markets in parens):**

| event | cat | positions | % book | markets | div range |
|---|---|---|---|---|---|
| Michigan Senate primary: voter turnout | Elections | **112** | **38.1%** | 7 | 11–25% |
| 2-year Treasury yield move on July FOMC day | Economics | **101** | **34.4%** | **2** | 45–56% |
| South Korea exports YoY July | Economics | 24 | 8.2% | 5 | 35–86% |
| Florida Republican Governor primary | Elections | 24 | 8.2% | 5 | 10–43% |
| S. African Reserve Bank rate decision | Economics | 7 | 2.4% | 1 | 39–84% |
| Italy GDP QoQ Q2 | Economics | 6 | 2.0% | 2 | 38–42% |
| *(8 more events, ≤5 each)* | — | 20 | 6.7% | 9 | 10–85% |

- **Top-2 events = 213 / 294 = 72.4%. Top-3 = 80.6%. Top-4 = 88.8%.** The book is two big correlated bets plus a tail.
- **Re-emission (the real story):** 294 positions / 31 markets = ~9.5 emissions/market. Worst: the 2Y-Treasury event is **2 markets re-emitted ~50× each**; Michigan turnout is 7 markets, the top one re-emitted **33× (07-13→08-01)**. Each re-emission is a distinct `order_id` — so the dashboard counts them as 294 separate paper positions. **Distinct market-level exposure is 31, not 294.**

## STEP 2 — Divergence distribution vs the prior inversion

| divergence bucket | positions | % book | note |
|---|---|---|---|
| <15% | 54 | 18.4% | Elections |
| 15–20% | 54 | 18.4% | Elections |
| 20–25% | 39 | 13.3% | Elections |
| 25–30% | 5 | 1.7% | Elections |
| 30–35% | 1 | 0.3% | — |
| **≥35%** | **141** | **48.0%** | **Economics (140/141)** |

- **Bimodal, and it's structural:** the Eco/Fin **strict gate** (`kalshi_llm_arbitrage.py:194` — Economics requires divergence ≥30% AND LLM-extreme prob ≤0.15/≥0.85) forces the **entire Economics book into ≥35% divergence** (up to 85.5%). Elections (no strict gate, min 10%) spans 10–35%. Crosstab: Economics 140 ≥35% / 1 <35; Elections 152 <35 / 1 ≥35.
- **Forward risk:** 48% of the book (by count and by $1-flat stake) is in the ≥35% bucket that historically had the worst calibration. The **2Y-Treasury-FOMC bet (101 positions / 2 markets, div 45–56%, NO)** is the concentration of this risk and settles first (08-05). Note the strict gate's *intent* was the opposite — to be MORE selective on Eco/Fin (extreme-LLM only) — so 08-05+ is the direct test of whether that gate picks winners or just re-selects the historically-losing high-divergence profile. **Untested forward; no verdict.**

## STEP 3 — Stake, sizing, exposure

- **Total paper stake = $294.00, flat $1.00/position** (avg qty×price = $1.00; sizing is uniform, not conviction-weighted). Dedup'd to distinct markets ≈ **$31**.
- **Max single-event exposure (dashboard):** Michigan turnout $112 (7 markets) · 2Y-Treasury-FOMC $101 (**2 markets**). If either one LLM view is wrong, that whole correlated stack resolves against it — but max loss on a $1 NO leg is the $1 stake, so absolute downside is bounded (~$112 / ~$101 paper).
- **PAPER, shared account:** `standby:true`, no `agent_state` auto/halt override, `would_have_placed` (not `placed_live`), shared `KALSHI-*` account (equity ~$532.84, commingled with siblings). Not realized money.
- **★ Live-flip risk from re-emission:** in paper this is a harmless display inflation, but the strategy emits a NEW order_id for the same market every cycle with **no observed dedup**. Before any live flip, confirm the live path skips markets where a position already exists — otherwise it would pile ~50 orders onto the single 2Y-Treasury market. (Flagged as a pre-live check; not diagnosed here — out of read-only scope.)

## STEP 4 — Settlement calendar (when the forward data lands)

| expiry | positions | stake | events | profile |
|---|---|---|---|---|
| **2026-08-05** | **101** | $101 | 1 | **2Y-Treasury-FOMC — HIGH-div (45–56%) Economics NO. FIRST big test.** |
| 2026-08-06 | 6 | $6 | 1 | — |
| 2026-08-08 | 24 | $24 | 1 | S. Korea exports — high-div Economics |
| **2026-08-11** | **122** | $122 | 4 | **Michigan turnout (112) — LOW-div (11–25%) Elections + tail. BIGGEST day.** |
| 2026-08-18 | 25 | $25 | 2 | mixed |
| *(past-expiry, unresolved)* | 10 | $10 | 3 | 07-14 CPI, 07-16 BoK, 07-23 SARB |

- **The two big settlements test DIFFERENT profiles:** 08-05 tests the **high-divergence** hypothesis (2Y-Treasury, 45–56% div — where the prior inversion predicts a loss); 08-11 tests the **low-divergence** Michigan turnout stack (11–25% div — less exposed to the inversion). Read them separately, not as one blob.
- **★ Anomaly — 10 post-epoch positions are already past expiry (07-14/16/23) and still unresolved.** The resolver IS wired (2,799 pre-epoch kalshi_llm round-trips exist), so "0 resolved" is the young window — BUT these 10 (CPI/BoK/SARB) should have booked by now. Either they voided or the resolver hasn't matched them. **The very first forward settlements may be overdue; worth checking whether they resolved on Kalshi.** (Reported, not diagnosed.)

## STEP 5 — Entry rate / liveness

- **Steady daily flow, NOT a one-time batch.** Entries every single day 07-07→08-01, **3–21/day, avg ~11**, touching 2–7 events/day. The screenshot's "07-31/08-01 cluster" is an OPEN-tab sort-by-newest artifact — 07-31/08-01 were only 9/11 entries; the busier days were 07-28 (21), 07-21 (19), 07-14 (18).
- **Caveat:** most of that daily "flow" is **re-emission** of the same ~31 markets (the persistent divergence views), not 11 new distinct bets/day. So "alive and entering" = re-affirming existing divergence daily + occasional genuinely new markets. Materially different from kalshi_arbitrage (dormant ~0.09/day) — this division's discovery is active — but the *distinct* opportunity rate is far below the raw entry count.

## STEP 6 — Synthesis (data only, no edge/prospect claim)

- **What the open book says before settlements land:** the current-logic book is **repeating the high-divergence exposure**, not diverging from it. 48% sits at ≥35% divergence (the whole Economics side), anchored by the 2Y-Treasury-FOMC bet (101 positions, 45–56% div) — the exact profile the pre-epoch data lost on. Composition is **two dominant correlated views** (Michigan turnout, 2Y-Treasury) + a tail, all NO, flat $1, re-emitted ~9.5×.
- **Same pattern or different?** Both. The **Economics** half (high-div, gate-forced) looks like the old losing profile; the **Elections** half (Michigan turnout, 11–25% div) is a lower-divergence profile the inversion doesn't clearly condemn. So the division isn't monolithically repeating the mistake — but its single most-concentrated *Economics* bet is.
- **What 08-05→08-18 will actually reveal:** 
  - **08-05** — the high-divergence test. If the prior inversion holds, the 2Y-Treasury-FOMC NO bet (45–56% div) resolves AGAINST the LLM. This is the first real signal on whether the Eco/Fin strict gate fixed the calibration or just re-selected losers. (2 distinct markets, so it's really 1–2 data points re-emitted 50×, NOT 101 independent outcomes — weight it as ~2 events, not 101.)
  - **08-11** — the Michigan turnout stack (low-div Elections) — a separate, milder test.
  - Because of re-emission, **treat settlements at the EVENT/market level (14 events / 31 markets), not the position level (294)** — the effective forward sample is ~31 distinct outcomes, and the two big days are really ~1–2 + ~7 distinct markets.
- **No verdict:** 0 resolved in-window; the open book is a *risk read*, not evidence of edge or its absence. The first genuine forward data is 08-05 (or the 10 overdue positions, if they resolve).

---

*Guardrails: read-only; no code/config/roster/DB/deploy; paper stated explicitly; per-order fee model (n/a — 0 resolved, no fees yet); no memory characterizing edge/prospects; artifact left uncommitted; anomalies (re-emission inflation, 10 overdue settlements, live-flip dedup) flagged not diagnosed.*
