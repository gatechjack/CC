# kalshi_arbitrage — Entry-Stoppage Diagnosis (post-Karen-isolation review)

**Date:** 2026-07-26 (prod clock 13:48 UTC)
**Division:** `kalshi_arbitrage` · Strategy `kalshi_temporal_bucket_arb`
**Scope:** Read-only investigation of why no new entries have been placed. No code/config/prod changes.
**DB:** `/home/azureuser/trading_corp/data/trading_corp.db` (read-only SQLite queries only)

---

## HEADLINE

**Root cause = Category B: discovery is running and healthy, but the supply of tradeable arb opportunities collapsed on 2026-07-07→07-08 and has been zero since.** The Karen account isolation (2026-07-21) is **exonerated** — entries had already stopped ~9–13 days earlier. Nothing in the pipeline is broken; there is simply nothing profitable to enter.

- Last emission of any kind: **2026-07-12 15:15 UTC** (14 days ago).
- Structural supply collapse: **2026-07-08** (examinable pairs/cycle fell 85 → 1.4, a −98% drop).
- Opportunities passing all gates (`n_temporal_opportunities` + `n_bucket_opportunities`): **0.00 per cycle every day since 2026-07-08.**

---

## STEP 1 — Entry activity since 2026-07-21

Emission = `would_have_placed` audit event (paper strategy; `auto_execute:false`, so this is the "would place an order" signal). Post-fix resolved round-trips (the memory trigger metric) = **0**.

**`would_have_placed` per day (actor = `kalshi_temporal_bucket_arb`):**

| Date | Emissions | Event(s) |
|---|---|---|
| 2026-07-01 | 3 | — |
| 2026-07-06 | 16 | KXFDAAPPROVE-GED (temporal, 15–16¢) |
| 2026-07-07 | 8 | KXFDAAPPROVE-GED (temporal, 15–16¢) |
| 2026-07-11 | 2 | KXDIAZOUT-MDC (temporal, 5¢) |
| 2026-07-12 | 2 | KXFOMCGUIDE-26 (bucket, 84¢) |
| **2026-07-13 → 2026-07-26** | **0** | — (14-day drought) |

- **Entries since the 2026-07-21 03:19 UTC Karen isolation: ZERO.**
- **Entries since 2026-07-13: ZERO** (>13 continuous days, no >24h window has any entry).
- `kalshi_round_trips` where `division='kalshi_arbitrage' AND entry_ts>='2026-07-07T16:40'`: **0** (the pending forward-edge trigger of n≥30 is therefore structurally unreachable at the current rate — see Notes).
- Pre-stoppage cadence: the "~4/day" historical rate was concentrated in a single rich series (KXFDAAPPROVE-GED). 07-06 alone = 16 emissions; that series drove essentially all volume.

**The entry rate is exactly ZERO, and has been since 2026-07-13.**

---

## STEP 2 — Discovery pipeline health

The scanner loop is **fully healthy and running on interval.**

| Metric | Value |
|---|---|
| Poll interval (`poll_interval_sec`) | 300 s (5 min), floored at 30 s |
| Scan cycles/day (`kalshi_temporal_bucket_scan`) | 271, 270, 268, 269, 269, 268, 270 (07-19…07-25); 157 by 13:44 on 07-26 — on pace |
| Last scan cycle | 2026-07-26 13:44:21 UTC (≈2 min before query) |
| `discovery_refreshed` events since 07-21 | 753 |
| `pair_evaluated` events since 07-21 | 1,756 |
| `bucket_evaluated` events since 07-21 | 83 |

**Latest discovery refresh (07-26 13:39 UTC):** 53 events / 342 markets returned from Kalshi. `events_by_type`: binary 9, multi_outcome 22, **temporal 5**, **bucket 0**, other 17.

Interpretation: the loop executes every 5 min, calls `broker.list_markets()`, gets real Kalshi markets with live prices, classifies them, builds temporal pairs / bucket sets, and evaluates them. **Candidates ARE being evaluated** — they are simply all being (correctly) skipped at the edge gate. This eliminates Category A (scanner broken) and Category D (write path broken — the audit write path is writing continuously).

---

## STEP 3 — Skip / rejection reason breakdown

**Zero risk-agent rejections** (`kalshi_tb_order_rejected_by_risk` absent since 07-21). The only skip is the **min-edge gate**, and it is failing for a genuine reason: the gross edge does not exist.

**Temporal path (min_edge = 4¢), `pair_evaluated` since 07-21 — n=1,756, ALL `would_emit=false`:**

| edge_cents bucket | count |
|---|---|
| < 0 (negative — no arb) | 1,488 |
| 0–1¢ | 264 |
| 1–2¢ | 4 |
| ≥ 2¢ | 0 |

- avg edge = **−6.79¢**; min = −91¢; **max = +1.0¢**.
- Best temporal pair in the entire window: `KXCLAYTONCONF-26JUN11` — early 0.96 vs late 0.95 = **1.0¢** gross, still `would_emit=false`.
- The 4¢ floor exists to clear ~2–4¢ of 2-leg taker fees. A 1¢ gross edge is a **net loss** after fees. **This is not a too-tight filter (Category C); the profitable edge is genuinely absent (Category B).**

**Bucket path (min_edge = 5¢), `bucket_evaluated` since 07-21 — n=83:**

| would_emit | count | avg edge | note |
|---|---|---|---|
| true | 77 | +28.0¢ | **all one event: `KXNBERRECESSQ`** ("next US recession", 6 legs, sum_yes_asks 0.784, edge 21.6¢) |
| false | 6 | −78.0¢ | one-sided / negative |

**The 77 `would_emit=true` rows are a cosmetic false-positive, NOT a missed trade** (see Anomaly below). Every one is the NBER recession event, whose legs resolve far beyond the 60-day horizon; the real detector rejects it. Zero genuine bucket opportunities have appeared since KXFOMCGUIDE-26 on 07-12.

### Anomaly investigated & resolved: bucket audit `would_emit` ≠ actual emission

`kalshi_temporal_bucket_arb.py`:
- **Detector** `_detect_bucket_violations` (lines 234–284) applies guards: ≥2 legs, every leg `yes_ask>0`, every leg has parseable `expected_expiration_time`, and **`latest_exp` within the 60-day horizon** (comment at lines 266–273 literally names *"NBER recession quarters whose expiration is far-future"* as the case it drops). NBER → returns `None` → not added to `bucket_opps` → not emitted. **Correct.**
- **Audit** `examined_buckets` (lines 541–554) recomputes `would_emit = (1 − Σyes_ask) ≥ threshold` with **none of those guards**. So it logs `would_emit=true` for NBER even though the detector rejected it.

Runtime proof both paths are working: NBER (far-horizon) → 0 emissions; KXFOMCGUIDE-26 (within-horizon) → emitted on 07-12. The bucket emission path is **not** broken; the `would_emit` audit field is just decoupled from the guarded detector. (Minor observability defect worth noting to the owner; out of scope to fix here.)

---

## STEP 4 — Karen account state

The Karen account is **healthy, authenticated, and unaffected.**

| Check | Result |
|---|---|
| Karen equity / cash (kalshi_equity_history, division='kalshi_arbitrage') | **$507.97 / $505.84 cash** — stable, snapshotting every 5 min (latest 13:48 UTC) |
| Matches 07-21 deploy record? | Yes — memory recorded $505.84 / $507.96 at isolation; unchanged |
| Kalshi API errors / auth failures / rate limits / stub events since 07-21 | **ZERO** (no error/fail/auth/limit/reject/stub audit kinds for any kalshi actor) |
| Broker returning live data? | Yes — 342 markets/refresh with real bid/ask prices → broker is authenticated, **not a stub** |

**Kalshi-side restrictions (KYC / market access / subaccount) as a cause: ruled out for this stoppage.** The account reads markets and snapshots equity without error. Live *order-placement* capability on Karen is untested (strategy is paper / `auto_execute:false` and never reached emission), but that is **irrelevant to the current stoppage**, which is upstream at candidate detection — no order was ever attempted for a restriction to block. This eliminates Category E as the cause.

---

## STEP 5 — Market conditions since 07-21

The stoppage is a **supply-side market phenomenon**, quantified from the scanner's own accounting.

**Daily scan supply trend (avg per cycle):**

| Date | cycles | temporal events | **pairs examined** | temporal opps | bucket opps | emitted |
|---|---|---|---|---|---|---|
| 2026-07-06 | 271 | 3.4 | **85.2** | 0.11 | 0.00 | 0.11 |
| 2026-07-07 | 269 | 3.4 | **58.3** | 0.09 | 0.01 | 0.10 |
| 2026-07-08 | 268 | 3.4 | **1.4** | 0.00 | 0.00 | 0.00 |
| 2026-07-09 … 07-26 | ~270 | ~3.5 | **~1.3** | 0.00 | 0.00 | 0.00 |

- **Temporal event count barely moved (~3.4), but pairs examined fell 85 → 1.4 on 07-08 (−98%).** That signature = one rich *multi-date* series dropped out. The bulk of the 50–85 pairs (and every fee-clearing 15–16¢ edge) came from **`KXFDAAPPROVE-GED`** (an FDA-approval temporal event with many "before <date>" buckets → C(n,2) pairs).
- **`KXFDAAPPROVE` and `KXFOMCGUIDE` are absent from discovery since 07-13** (resolved / delisted). Only `KXDIAZOUT` persists (70 appearances, last 07-26 11:57) but its edges are now ≤1¢.
- Categories targeted (Politics, Elections, Economics, Financials) are **still active** — 53 events / 342 markets discovered every cycle. The markets exist; the *mispricings that clear fees* do not. Remaining temporal pairs sit at 0–1¢ gross (below the fee floor), and the only recurring bucket "candidate" is horizon-ineligible (NBER).

This is opportunity-supply exhaustion, not a market-access or structural-listing change.

---

## STEP 6 — Code changes since 07-07

- **Last kalshi strategy/discovery/entry code commit: 2026-07-07.** Three commits that day: `d1f5ea6` (resolver leg_date fallback, 11:33 EDT), `aa06498` (60-day horizon cap on temporal pairs, 12:06 EDT), `0f79b22` (bucket guards: ≥2 legs + expected_expiration 60d cap, 12:36 EDT). No kalshi *code* commit since.
- **The 07-21 change was auth-layer only** — `secret_ref: kalshi_karen` (`divisions.yaml:216`) plus `secrets.py`/`main.py` broker selection. It does not touch strategy or discovery logic.
- **Deployed prod file verified:** `/home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py`, mtime **2026-07-07 16:37 UTC**, md5 `5bd03e6e…`, horizon guard present (`max_horizon_days=60`, lines 420/423). Runtime behavior (NBER correctly horizon-rejected; within-horizon KXFOMCGUIDE emitted) confirms this code is live.
- **Could the 07-07 guards have gated entries?** No. The horizon cap and bucket guards were already in force during the 07-06/07 KXFDAAPPROVE emissions (which passed fine). The collapse on 07-08 is a −98% drop in *pairs examined* (input supply), not a change in gate pass-rate. **No code side-effect; the Karen isolation had no unintended effect on discovery or entry.**

---

## STEP 7 — Synthesis / root-cause categorization

**CATEGORY B — Discovery runs but finds no (tradeable) candidates. Opportunity supply dried up.**

Evidence chain (numbers, ordered):
1. Scanner healthy: ~270 cycles/day, last 13:44 UTC (STEP 2).
2. Discovery healthy: 53 events / 342 markets per refresh; Karen broker authenticated; 0 errors (STEP 2/4).
3. Opportunities passing all gates = **0.00/cycle every day since 07-08** (STEP 5) — the detector's own count, not the cosmetic `would_emit`.
4. The collapse is a **−98% drop in examinable pairs on 07-08** driven by the exit of the `KXFDAAPPROVE-GED` FDA series (STEP 5); it resolved/delisted, and no comparable rich series replaced it.
5. Remaining candidates fail the edge gate for a real reason: best temporal edge = **1.0¢ gross « 4¢ fee floor**; best bucket candidate is horizon-ineligible (STEP 3).
6. Last emission 07-12; **Karen isolation (07-21) post-dates the stoppage by 9–13 days → not causal** (STEP 1/4/6).

**Explicitly ruled out:** A (loop is running), C (gates are fee-coverage floors, not arbitrarily tight — the gross edge is ≤1¢, so entries would be net-negative), D (audit/write path writes continuously; emission path proven by 07-12 fill), E (account healthy, authenticated, error-free; nothing attempted a placement to be blocked).

**One observability defect noted (not a cause, not fixed here):** the bucket `would_emit` audit field omits the detector's horizon/leg guards, so it shows 77 phantom "would-emit" NBER rows. Cosmetic only.

---

## NOTES / FLAGS FOR OPERATOR (no action taken)

- **The pending forward-edge trigger is structurally unreachable at the current rate.** The memory `kalshi-arbitrage-followup-and-commingling-2026-07-21` sets a trigger of *n≥30 post-fix entered-and-resolved OR 2026-09-15*. Post-fix entries = 0 and the entry rate has been exactly 0 for 14 days, so n≥30 will not arrive; only the 2026-09-15 calendar fallback remains, and it would fire on n=0 → "insufficient sample, defer." This matches the operator's stated concern. (Reported, not acted on — no memory written per session guardrail.)
- No recommendation is made here on thresholds, going live, or edge/prospects — out of scope by instruction.
