# kalshi_llm resolution measurement — FIX PLAN v2 → **DEPLOYED + VERIFIED LIVE 2026-08-01 (PID 536666)**

> **STATUS: DEPLOYED 2026-08-01 ~18:29 UTC.** Epoch-scope un-starve + Option-B read-view + 148-row backfill live. Verify clean (5a–d + live-position reconciliation); see `runbooks/deploy_log.md` 2026-08-01 entry. First market-level forward read: 15 markets, 8W/7L, +$1.76 net (A per-emission: 148, +$123.45). NOT a verdict (n=15).

**Date:** 2026-08-01 · **Decision (operator):** Option B (distinct-market) = **primary** forward-edge headline; Option A (per-emission) = **secondary** nightly-signal diagnostic. Must land before 08-05 settlements book. **PAPER.**

## STATE CHECK (verified against prod)
- **Nothing is deployed.** `kalshi_resolver.py` LF-md5 `d4a63eb1…` and `kalshi.py` `18626cf0…` on prod **== worktree (prod-live `dafe60b`)** — resolver is the original starved version, **no event-ticker fix, no dedup**. So Q3's "interoperate with the deployed event-ticker fix" is **moot** — there is no such fix; the un-starve + B-view is the only change. **Build base = worktree == prod, md5-confirmed.**
- **0 post-epoch round-trips** — backfill never ran; all 294 are still `would_have_placed`. Nothing booked to reconcile.

## Reframe accepted (changes the architecture)
Re-emissions are **distinct nightly paper decisions** (different price/size), not duplicates. Therefore **keep per-emission booking = Option A for free**; do **not** collapse at write (that would delete the A data). **Option B is a read-layer aggregation over the per-emission rows.** This is the epoch-scoping pattern, and it satisfies A+B with one low-risk read-layer change.

**Architecture = un-starve (resolver books per-emission → A) + Option-B distinct-market view (headline).**

---

## Q1 — DEDUP KEY (Option B) = full market `ticker`. Verified.

| event | positions | distinct markets (key=`ticker`) |
|---|---|---|
| Michigan turnout | 112 | **7** ✓ |
| 2Y-Treasury FOMC | 101 | **2** ✓ |
| South Korea exports | 24 | 5 |
| Florida Gov primary | 24 | 5 |
| **whole book** | **294** | **31** |

`event_ticker` over-collapses (Treasury→1, 6 multi-strike events wrongly merged) — the strikes (`-T10` "≥10bps" vs `-T8` "≥8bps") can resolve differently, so they must stay separate. **Key = `ticker` (strike included).**

## Q2 — CANONICAL = FIRST emission (earliest `ts`) per market. Reasoned, and it is load-bearing.

**This is now a real modeling choice, not "discard duplicates" — the price moved a lot across nights:**

| market | n | price range | first_px | last_px |
|---|---|---|---|---|
| Florida-52 | 8 | 0.061–0.70 | **0.58** | 0.063 |
| SARB-H25 | 7 | 0.10–0.65 | 0.19 | 0.10 |
| Michigan-1300000 | 33 | 0.11–0.52 | **0.52** | 0.12 |
| S.Korea-T60 | 5 | 0.23–0.58 | 0.26 | **0.58** |

**Choose FIRST, because:**
- It is **when the LLM first flagged the divergence = when a live division would have opened the position** and held to resolution. Option B *is* the deployable-performance measure ("one held position per market, entered on signal"), so FIRST is the entry that actually models it.
- It is **honest / hindsight-free**: it uses the price available *when the signal fired*, not a later better price. Note that in several high-spread markets FIRST is the *less* favorable entry (Florida/Michigan/SARB — first > last on a NO leg), which is correct: a live system can't wait for the best night. In others (S.Korea) FIRST is cheaper — also just the realistic entry.
- **LAST rejected:** it's not when you'd enter; it imports hindsight. **Peak-divergence-night rejected:** a live system fires on the *first* threshold crossing, not the (unknowable-in-real-time) peak. **Average/best rejected:** doesn't model a single held position.

**P&L effect:** B's P&L for a market = the **first-emission** row's `realized_pnl` (its `entry_price` + `qty`; $1 stake ⇒ qty≈1/price). Because spreads reach 0.64, FIRST vs LAST changes per-market P&L materially — so the rule must be applied consistently and stated. Implementation: for each `ticker`, the canonical = `MIN(entry_ts)` round-trip; B reads that row's outcome/P&L.

## Q3 — Reconcile the 294 already-open + interop

- **Booking:** un-starve → resolver books **every** unresolved emission per `order_id` (A). At 08-05 the Treasury 101 book as 101 A-rows; **the B-view aggregates them to 2 distinct-market outcomes** (canonical=first). So the *headline* reads 2, not 50; the raw table holds the 101 as the A diagnostic. Michigan → 7.
- **Heals the open book:** all 294 are unbooked now, so they collapse correctly under B as they book — no separate reconciliation of already-booked rows (there are none).
- **Interop:** there is **no deployed event-ticker fix** (md5-confirmed) — nothing to double-handle. Un-starve (write) and B (read) are orthogonal: the resolver writes per-emission; B only reads/aggregates. No double-booking, no write contention.

## Q4 — Option A (secondary) cost = FREE. Keep it.

Option A **is** the raw per-emission `kalshi_round_trips` table produced by normal booking — no extra table, no extra write path. "Nightly signal accuracy" ("was each night's call right") is queryable directly (group by night, or per-emission WR). **Keeping A adds zero cost;** B is the only *new* artifact (a read-layer view). Recommend: keep A as-is, label B the headline. No materialized A-table needed.

## Q5 — GROUND-TRUTH TEST (built-in truth)

1. **B distinct-count:** the Treasury set (101 emissions, 2 tickers) surfaces in B as **exactly 2** market outcomes; Michigan as **7**; whole book as **31**. Not 101/294 (that's A), not 1 (event over-collapse).
2. **Canonical:** B's row for each ticker uses the `MIN(entry_ts)` emission's `entry_price`/`realized_pnl` (assert against a known market, e.g. Florida-52 → first_px 0.58).
3. **Negative key test:** aggregating by `event_ticker` would give 14 — assert B uses `ticker` (14 is wrong), guarding against event-level regression.
4. **A intact:** per-emission count still 294 (A not destroyed by B).
5. **Outcome truth:** the stuck-3 markets surface in B as CPI-win / SARB-win / **BoK-loss** (2 market-wins / 1 market-loss; highest-div market lost).

## Sequencing / deploy (on approval)
One change: **un-starve** (resolver: raise `max_per_actor`/`max_per_tick` or deprioritize far-future `pending`) **+ Option-B distinct-market view** (read layer, e.g. `web/data.py`, like the epoch scope). Base = worktree (== prod, md5-confirmed) → stage → drift-gate + backup → **hold for your restart go** → restart → verify (Q5 live). Before 08-05.

*Plan only — nothing built. Verified against live prod + open book. No memory written.*
