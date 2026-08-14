# Kalshi divisions — performance + health assessment
### `kalshi_arbitrage` + `kalshi_llm_arbitrage`, since last review (2026-08-05)

- **Date:** 2026-08-14 (query time ~16:52 UTC)
- **Mode:** READ-ONLY. Nothing deployed, no code/config/roster/ref changes.
- **Both divisions are PAPER.** All P&L is paper/ceiling. `kalshi_round_trips.realized_pnl` is **GROSS** — fees are not stored. Net-of-fee here is computed per-order as `ceil(0.07·qty·P·(1−P))` (min $0.01), one fee per round-trip row (single entry fill, held to settlement, no exit-side fee). **Fill assumption:** paper fills at the stored `entry_price`; no slippage/queue modeled.
- **DB:** `/home/azureuser/trading_corp/data/trading_corp.db` (SQLite, 2.48 GB, mtime 2026-08-14 16:33), opened `mode=ro`.
- **Evidence scripts** (in this worktree, each hand-run by operator, raw output pasted back): `kq_svc.ps1` (service/journal), `kq_data.ps1` (round_trips analytics), `kq_data2.ps1` (placement cadence + pending). Every number below is traceable to one of these.

---

## TL;DR

- **Still backlog + coin-flip/too-thin. No forward edge in either division.**
- **kalshi_arbitrage:** realized is ~100% **backlog** (net **+$3,727.72**, pre-fix entries). **Forward = 2 distinct markets, 1W-1L, ≈ −$0.89 net** — negligible. `tail_price_arb` places *nothing*; `temporal_bucket_arb` just **revived placements on 08-12** (all still pending). Still opportunity-starved.
- **kalshi_llm_arbitrage:** forward **Option B (distinct-market, the deployable headline) = 16 markets, 8W-8L, net +$0.0036 ≈ $0.00** — a coin flip, and **identical to the 2026-08-05 review** (no new resolutions in 9 days). Per-emission Option A (+$111.26 / 76.5% WR) is the ~9.6× re-emission mirage. The ~$0 is a fragile cancellation: Economics +$4.24 vs Elections −$4.24, concentrated in 1-2 events.
- **Dashboard read-view is faithful** to the raw table (n/W/gross all match; the automated `gross=False` flag was a rounding-tolerance artifact — see §Cross-check).
- **Health: GREEN for both divisions.** Engine up, scanners polling live, resolver `errors:0`. Two minor items (4 delisted markets stuck `not_found`; `tail_price_arb` structurally idle). One **out-of-scope** red service (`tc-audit-reality`, a bitunix/SFP reconciler — not kalshi).
- **n is far below a verdict threshold.** Option-B n=16 (llm) and n=2 (arb). Directional only; not conclusive.

---

## STEP 0 — Health

### Service / engine (`kq_svc.ps1`)
```
=== SVC_TC ===
active
MainPID=707835
NRestarts=0
ActiveEnterTimestamp=Fri 2026-08-14 04:04:48 UTC
```
Engine active, 0 restarts, up since 04:04 UTC (the MACE OQ-2 deploy restart).

### Scanner poll recency — arbitrage scanner confirmed LIVE (`kq_data.ps1` AE_RECENCY + AE_TYPES)
```
kalshi_llm_arbitrage         c=327968  last_ts=2026-08-14T16:52:13+00:00
kalshi_temporal_bucket_arb   c=117953  last_ts=2026-08-14T16:51:59+00:00
kalshi_tail_price_arb        c=162454  last_ts=2026-08-14T16:51:50+00:00
```
All three actors emitted audit rows within ~1 minute of query time — **scanners are not starved of polling.** Activity breakdown (`kind`):
```
tail_price_arb : kalshi_market_evaluated=124,875 ; kalshi_tail_arb_scan=24,982 ; would_have_placed = (none)
temporal_bucket: kalshi_pair_evaluated=78,943 ; kalshi_temporal_bucket_scan=24,982 ; would_have_placed=539 (last 08-14T13:55)
llm            : kalshi_llm_probability_called=183,831 ; kalshi_llm_scan_cycle=118,239 ; would_have_placed=4,364 (last 08-14T05:10)
```
→ **`tail_price_arb` has issued ZERO `would_have_placed` in its entire history** — it evaluates 124k markets but nothing clears its bar. `temporal_bucket_arb` and `llm` do place (paper).

### Resolver booking correctly (`kq_svc.ps1` resolver ticks)
```
Aug 14 15:31:04 kalshi_resolver tick: {'scanned': 109, 'resolved': 0, 'pending': 98, 'void': 0,
                 'not_found': 4, 'errors': 0, 'paired': 0, 'pair_scanned': 607, 'pair_skipped_no_entry': 607}
```
Hourly ticks, `errors:0`. Resolver online (`interval=3600s`), equity snapshot writers online for both divisions. `not_found:4` = 4 delisted/renamed markets returning HTTP 404 (`KXCABLEAVE-26MAY-26AUG`, `KXIPOOLIPOP-26AUG01`) that can never resolve — stuck pending (cosmetic, resolver handles gracefully).

### Resolved-by-day + pending per division (`kq_data.ps1` RESOLVED_BYDAY, `kq_data2.ps1` PENDING_PROXY)
```
arb: 07-07=242, 07-14=12, 07-15=4, 07-18=2, 08-01=153            (total 413)
llm: 07-07=1006, 07-08=557, 07-11=22, 07-20=130, 07-21=138, 08-01=152, 08-04=5
```
**No resolutions since 08-04 (llm) / 08-01 (arb).** Last review was 08-05 → **zero new forward settlements in 9 days.** Pending (distinct forward tickers emitted but not yet booked, since 07-07):
```
kalshi_llm_arbitrage        emitted_distinct=54  booked_distinct=16  pending_distinct=38
kalshi_temporal_bucket_arb  emitted_distinct=15  booked_distinct=1   pending_distinct=14
kalshi_tail_price_arb       emitted_distinct=0   booked_distinct=0   pending_distinct=0
```
Resolver aggregate `pending:98` (all kalshi divisions). The forward *pipeline is filling* (38 llm + 14 temporal distinct in flight) even though *resolved* forward data is frozen.

### Journal errors since 08-05
5,574 lines match `error|exception|traceback` across the **whole** engine (all divisions). Kalshi-specific error lines are exclusively the repeated `404 not_found` for the 4 delisted markets above — **no kalshi tracebacks/crashes**. Example:
```
WARNING trading_corp.brokers.kalshi: KalshiBroker.get_market_resolution failed for KXCABLEAVE-26MAY-26AUG: 404: not found
```

### Out-of-scope anomaly (flagged, not investigated)
`tc-audit-reality.service` = **failed** (since 08-14 06:13 UTC). Its discrepancies are **bitunix/SFP v2** trades (R-multiple sim-vs-recorded mismatches, e.g. `recorded=-0.60 sim=-1.0`), unrelated to kalshi. Surfacing for visibility; outside this review's scope.

---

## kalshi_arbitrage

### (a) backlog vs (b) forward — the load-bearing cut (`kq_data.ps1` STEP1)
Query: split `kalshi_round_trips` on `entry_ts` at the 07-07 leg_date-fix boundary; gross from `realized_pnl`, fees computed per-order.
```
a_backlog (entry<2026-07-07)  n=404  W=322  gross=$3,744.12  fees=$16.40  net=+$3,727.72   WR=79.7%
b_forward (entry>=2026-07-07) n=9    W=1    gross=-$7.7952   fees=$0.58   net=-$8.3752     WR=11%
```
**Realized is ~100% backlog** (matches the prior-3-reviews finding of ~$3.5k backlog drained by the 07-07 fix). The 08-01 batch (153 rows) is more of the same backlog draining.

### (b) Forward detail (`kq_data.ps1` ARB forward rows)
The 9 forward rows are only **2 distinct markets**, and 8 of them are duplicate emissions of one loser:
```
KXFDAAPPROVE-GED-26AUG01  NO @0.03  x8 rows (4 entry-times x2)  resolved YES -> LOST  each -$1.00
KXDIAZOUT-MDC-26AUG01     NO @0.83  x1 row                      resolved NO  -> WON   +$0.2048
Option B (distinct by ticker): n=2, W=1, gross=-$0.7952  ->  net ~ -$0.89
```
So forward arb = **1 loss + 1 win across 2 events**; the −$8.38 per-emission figure is 8× duplication of the single FDA loss. Either way, **negligible and directionally flat-to-negative.**

### Entry cadence — dormant or reviving? (`kq_data2.ps1` WHP_BYDAY)
`would_have_placed` (new-position signals) by day:
```
temporal_bucket_arb:  (nothing 08-01..08-11)  08-12=12  08-13=20  08-14=14
tail_price_arb:       (nothing, ever)
```
**`temporal_bucket_arb` revived placements on 08-12** after being silent Aug 1-11 — a genuine change since last review (all 46 new placements still pending). `tail_price_arb` remains dead as an opportunity source.

### Concentration (`kq_data.ps1` STEP3)
```
ARB_forward  total_net=-$8.3752  n_events=2
   KXFDAAPPROVE-GED  net=-$8.56  97.9% of |net|   (one event, 8 dup rows)
   KXDIAZOUT-MDC     net=+$0.18   2.1%
```
Effectively one event. No breadth.

---

## kalshi_llm_arbitrage

### (a) backlog vs (b) forward — the load-bearing cut (`kq_data.ps1` STEP1)
Boundary = dashboard epoch `2026-07-07T16:40:00+00:00` (EPO). Rows in `[07-07 00:00, EPO)` window = **0**, so the date and epoch boundaries agree.
```
a_backlog (entry<EPO)  n=2802  W=1127  gross=-$452.42  fees=$121.65  net=-$574.07   WR=40.2%
b_forward Option A (per-emission, entry>=EPO)  n=153  W=117  gross=$118.45  fees=$7.19  net=+$111.26  WR=76.5%
b_forward Option B (DISTINCT-market, headline) n=16   W=8    gross=$0.7636  fees=$0.76  net=+$0.0036  WR=50%
```
- **Option B is the deployable headline: 16 distinct markets, 8W-8L, net +$0.0036 ≈ $0.00 — a coin flip.**
- Option A's +$111 / 76.5% is the **re-emission mirage**: 153 emissions / 16 distinct = **9.56× re-emission** (the LLM re-fires the same market nightly). Not an edge.

### Cross-check: dashboard read-view vs raw table (`kq_data.ps1` STEP2 OptionB)
```
RAW OptB (python first-emission dedup):  n=16  W=8  gross=0.7636
DASHBOARD CTE (_query_kalshi_distinct_market_stats): n=16  W=8  gross=0.7636
CROSSCHECK: n=True  W=True  gross=False
```
**The read-view is faithful.** n and W match exactly; gross matches to 4 dp (both 0.7636). The `gross=False` is a **false alarm** — I compared my full-precision raw sum against the CTE's `ROUND(...,4)` output with a 1e-6 tolerance, so a ~1e-5 rounding gap trips it. Not a real discrepancy. (This is the opposite of the 08-05 "+$5.87 unreproducible" problem: here raw and dashboard agree.)

### Since last review — unchanged
The 08-05 review reported **16 distinct / 8W-8L / $0.00; Econ +$4.24 / Elec −$4.24**. This review reproduces those numbers **exactly** — no forward market has resolved since 08-04.

### Economics vs Elections (divergence-inversion test) (`kq_data.ps1` STEP2 by CATEGORY + divergence)
```
Economics:  n=12  W=8  net=+$4.24   (WR 67%)
Elections:  n=4   W=0  net=-$4.24   (WR 0%)   -> the two halves cancel to ~$0.00
```
Divergence buckets (Option-B canonical rows, split at median |divergence|):
```
low-div half   n=8  W=4  net=-$2.59  avg|div|=25.0
high-div half  n=8  W=4  net=+$2.60  avg|div|=66.6
```
**No divergence-inversion evident** — the high-divergence (LLM strongly disagrees with market) bets did *not* resolve against the LLM; they netted **positive**. But this is confounded with category: the high-div winners are the Economics rate/CPI markets (e.g. `KXCBDSA` div 69.5 +$4.26, `KX2YFOMC` div 46/55 +$2.51), while the losers are low-div Elections markets. It's the Econ/Elec split re-expressed, not a clean divergence signal. **n=8 per half — noise.**

### Concentration (`kq_data.ps1` STEP3)
```
LLM_OptB  total_net=+$0.0036  sum|net|=$14.55  n_events=9
   KXCBDSA-26JUL23        net=+$4.20  28.9%
   KX2YFOMC-26JUL29       net=+$2.51  17.3%
   KXSCRSENSRUN-26        net=-$2.12  14.6%
   KXSKEXPYOY-26JULY31    net=-$1.96  13.5%
   KXCBDECISIONKOREA      net=-$1.07   7.4%
```
Top-3 events = **60.8%** of |net|; a single event (KXCBDSA) = 28.9%. The ~$0 net is a **fragile cancellation of a few larger swings**, not a broad wash. The entire Economics "+$4.24" is essentially one market (KXCBDSA +$4.20) plus KX2YFOMC, offset by two losers.

---

## STEP 4 — Honest synthesis (data only)

**Is there ANY forward signal yet?** No.
- **kalshi_llm:** Option B is a coin flip — **8W-8L, net $0.00 at n=16**, unchanged since 08-05. What structure exists (Econ + / Elec −) cancels and is concentrated in 1-2 events. The only positive-looking number (Option A +$111) is a 9.6× re-emission artifact and must be ignored. **Backlog was net −$574.**
- **kalshi_arbitrage:** Forward is 2 distinct markets (1W-1L, ≈ −$0.89) — indistinguishable from zero. `tail_price_arb` places nothing; `temporal_bucket_arb` just resumed placing (08-12) but nothing has resolved. **All realized P&L (+$3,727 net) is backlog.**

**Still backlog + too-thin/dormant** for both. Not reviving in *resolved* terms, though the temporal-arb placement restart (08-12) and 38 llm + 14 temporal distinct pending markets mean the forward corpus should grow over the coming weeks.

**What n does each need?**
- Per-market variance is ~$1 (flat ~$1/leg sizing), so at n=16 the standard error on net is ~$0.25/market ≈ ±$4 on the total — the observed $0.00 is statistically indistinguishable from any small edge. **Need n≥30 distinct resolved for a directional read, ≥100 for a confident call.** With 38 llm distinct pending, llm could reach n≈30-50 within a few weeks → natural re-review trigger.

**Health:** GREEN for both divisions — engine up (0 restarts), scanners polling live (<1 min lag), resolver `errors:0`. Minor: 4 delisted markets stuck `not_found` (cosmetic); `tail_price_arb` structurally idle (starvation, not a bug — worth a design question but not a fault). Separate red service `tc-audit-reality` (bitunix, out of scope) flagged for the operator.

**Framing:** This is an **initial read on low-n forward data — directional, NOT conclusive.** The honest answer remains *still dormant (arb) / still coin-flip (llm) / too thin to call*.

**Re-review triggers:** llm Option-B n≥30 distinct resolved (est. 2-4 weeks as the 38 pending settle), OR an arb ≥5-distinct-resolved-forward day, OR calendar 2026-09-01.

---

## Appendix — traceability
- `kq_svc.ps1` → service state, resolver ticks, journal error grep.
- `kq_data.ps1` → SCHEMA, RESOLVED_BYDAY, STEP1 backlog/forward, STEP2 Option A/B + cross-check + canonical rows + category + divergence, ARB forward rows, ENTRY_RATE_BYDAY, STEP3 concentration, AE recency/types.
- `kq_data2.ps1` → WHP payload samples, would_have_placed by-day, distinct-ticker emission, pending proxy.
- Fee model: `math.ceil(0.07*qty*entry_price*(1-entry_price)*100)/100`, per order row.
- Option B / dashboard reproduction: `WITH ranked AS (... ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY entry_ts ASC, id ASC) ...) WHERE rn=1`, llm cutoff `entry_ts>='2026-07-07T16:40:00+00:00'`.
