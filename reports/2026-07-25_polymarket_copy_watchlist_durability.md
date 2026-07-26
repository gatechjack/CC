# PCT Watchlist Durability — Second Snapshot vs 07-19 (durability filter)

**Date:** 2026-07-25 (snapshot computed 2026-07-26 ~03:27 UTC) · **Mode:** READ-ONLY. No `--algo-select`, no roster/watchlist writes, no promotions. The stored 07-19 `watch_only_whales` baseline was NOT overwritten.
**Method:** targeted re-pull of the 7 ranked promote candidates through the *same* computation the 07-19 seed used — `_fetch_wallet_activity_windowed` → `_select_resolved_buys_window` → `_aggregate_window_to_decisions` → `compute_polymarket_stats` (half-life 36500 = windowed), same four floors (n≥10, recency≤60d, WR≥0.62, realized≥$5k). 861 condition_ids seen, **775 resolved (90% coverage)**. Leaderboard presence verified against live top-500 (global/Sports/Politics), endpoint healthy (500 rows/call, no errors).

**Note on scope:** the *full-breadth* dry-run (to surface brand-new gate-passers, item 5) was **Cloudflare-blocked** on the batch resolution endpoint (HTTP 403, escalating 30→240s backoffs across ~550 chunks for 27k+ condition_ids) and could not complete this session. The targeted 7-candidate re-pull (few chunks) cleared cleanly. New-whale discovery is deferred — see §4.

---

## §1 Side-by-side: 07-19 vs 07-26 (per candidate)

Realized PnL / size are the whale's OWN Polymarket windowed activity (un-copied). `LB` = present in current top-500 of its category/global.

| Whale | Cat | n 07-19→now | WR 07-19→now | Realized$ 07-19→now | span_d 07-19→now | AvgPx / sub70 now | LB now | Gate now |
|---|---|---|---|---|---|---|---|---|
| **viktorurolog16** | Pol | 72→81 | 0.778→**0.728** | 17.5k→**17.8k** | 208→**214** | 0.59 / 0.77 | off | **PASS** |
| **CVCM** | Spt | 100→100 | 0.67→**0.63** | 19.5k→**13.3k** | 80→**71** | 0.52 / 0.59 | off | **PASS** |
| **papuas** | Glb | 100→100 | 0.62→**0.63** | 74.5k→**71.4k** | 34→**16** | 0.51 / 0.96 | off | **PASS** |
| **ox1star84** | Spt | 70→68 | 0.871→**0.868** | 15.1k→**12.5k** | 280→**259** | 0.71 / 0.35 | off | **PASS** |
| **marchonnow** | Pol | 69→72 | 0.710→**0.708** | 27.2k→**28.2k** | 53→**53** | 0.70 / 0.38 | off | **PASS** |
| **DegenKingBetter** | Spt | 100→100 | 0.65→**0.62** | 114.5k→**67.5k** | 44→**44** | 0.54 / 0.99 | off | **PASS (@floor)** |
| **boomingtest** | Spt | 78→97 | 0.628→**0.608** | 81.4k→**120.0k** | 39→**16** | 0.50 / 0.94 | off | **FAIL (wr)** |

All 7 remain **active** (recency OK, all hit the BUY-target this snapshot) and **none went negative**.

## §2 Survivors vs regressors

**SURVIVORS — pass the quality gate on BOTH snapshots (6/7):**
`viktorurolog16`, `CVCM`, `papuas`, `ox1star84`, `marchonnow`, `DegenKingBetter`.

**REGRESSOR — fell below the gate (1/7):**
- **boomingtest** — WR 0.628 → **0.608 (below the 0.62 floor)**; window span collapsed 39d → **16d** while PnL rose ($81k→$120k). Signature of a **cooling hot streak**: the recent burst is high-dollar but the win rate is reverting toward the mean, and the 100-decision window now spans only ~2 weeks (churn). No longer a gate-passer.

## §3 Spiky vs durable (did the hot-window narrow, or hold?)

I could not recompute the exact window-PnL-vs-lifetime ratio (all 7 rotated off the leaderboard → no current lifetime entry; see §4). But the two-snapshot **stability** of the direct window stats is a cleaner durability read:

- **Durable / steady (held across both):**
  - **marchonnow** — the most stable: WR 0.71→0.71, PnL $27k→$28k, span 53→53, AvgPx 0.70. Flat and repeatable. *(favorite-lean: AvgPx 0.70, sub70 0.38.)*
  - **viktorurolog16** — WR 0.78→0.73, PnL ~$17.7k both, span **208→214d** and **sharp** (AvgPx 0.59, sub70 0.77). Long + sharp + steady = best durability profile.
  - **ox1star84** — WR 0.87→0.87, PnL $15k→$12.5k, span **280→259d** (longest). Steady but *favorite-lean* (AvgPx 0.71).
- **Sharp, passing, mild cooling:**
  - **CVCM** — WR 0.67→0.63, PnL $19.5k→$13.3k; still the **sharpest** (AvgPx 0.52, sub70 0.59) and long (71d). Cooling but intact.
  - **papuas** — WR ~0.62 both, PnL ~$72k both, sharp (0.51/0.96); but span **34→16d** = its 100 decisions now pack into 2 weeks (high churn, shorter effective window).
- **Spiky — confirmed cooling (the 07-19 number was inflated):**
  - **DegenKingBetter** — realized **$114.5k → $67.5k (halved)** and WR down to **exactly 0.62 (at the floor)**. Still deep + very sharp (sub70 0.99) and $68k is large, but the drop confirms the 07-19 headline rode a hot window that is mean-reverting. **Watch, don't lead with it.**
  - **boomingtest** — regressed out (§2).

**Answer to "did they go quiet/negative":** No — none went quiet or negative; all still trade actively. The spiky ones' realized PnL is *reverting down* toward sustainable levels (DegenKing halved, boomingtest's WR fell), while the steady ones held. That is exactly the durable-vs-streak separation the second snapshot was meant to produce.

## §4 Two hard caveats (load-bearing)

1. **All 7 rotated off the top-500 leaderboard** (verified: absent from live GLOBAL/Sports/Politics top-500; endpoint healthy). The seed algorithm discovers candidates **only** from the top-500 leaderboard, so **none of these 7 would be auto-re-surfaced by a fresh run** — they'd survive only via manual pinning. This is the *discovery layer* churning (the Polymarket leaderboard is a rolling-window ranking), somewhat independent of edge — their direct window-stats (§1) are the edge signal, and 6/7 still pass. But it means: the corrected algorithm, re-run today, would propose a **different** set than these 7.
2. **Still un-copied, still single-venue-self-reported.** Every number is the whale's own Polymarket activity; we have never copied them, so there is still no paper-verified track record. The second snapshot removes the *one-time* risk (6/7 held), not the *no-copy-verification* risk.

## §5 High-confidence PROMOTE set (passes BOTH snapshots, ranked by durability + sharpness)

1. **viktorurolog16** (Politics) — steady across both, longest-with-sharp (214d, AvgPx 0.59, sub70 0.77), WR 0.73, PnL steady ~$17.8k. **Top durability+sharpness.**
2. **CVCM** (Sports) — sharpest (AvgPx 0.52, sub70 0.59), deep (n100), long (71d), passes both with only mild cooling.
3. **papuas** (Global/Sports) — sharp + deep + stable PnL (~$72k, WR 0.63); caveat: short/churning window (16d).
4. **ox1star84** (Sports) — most stable + longest span (259d), WR 0.87; caveat: favorite-lean (AvgPx 0.71).
5. **marchonnow** (Politics) — rock-steady; caveat: favorite-lean (AvgPx 0.70, sub70 0.38).
6. **DegenKingBetter** (Sports) — deepest/sharpest/biggest $ but **cooling hard** (PnL halved, WR at floor) → watch, likely mean-reverting.

**Dropped by the durability filter:** `boomingtest` (regressed below WR floor).

## §6 New gate-passers (item 5) — NOT completed

The full-breadth second crawl needed to surface *brand-new* whales was Cloudflare-blocked on the batch-resolution endpoint and killed after it stalled (chunk 553/~550, 240s backoffs). **No new-whale list this session.** To get it cleanly, re-run `seed_polymarket_watchlist_deep --dry-run` off-peak or with slower resolution pacing / smaller `--candidates`. This does not affect §1–§5 (the 7 named candidates were pulled cleanly).

## §7 Probation whales (current copied resolved-decisions)

Still **too thin to judge** — essentially static since the last assessment (these are OUR copied decisions in `polymarket_round_trips`):

| Whale | copied decisions | fills | net$ | last copy |
|---|---|---|---|---|
| Moond | 7 | 19 | +3.12 | 2026-07-20 |
| ChadStarmer | 4 | 9 | +0.84 | 2026-07-24 |
| potatobrahh | 2 | 3 | −0.23 | 2026-07-25 |

None is near a judgeable sample (~30–50 decisions). No change in verdict: hold on probation with a decision date, or cut for noise.

---

### Provenance
Read-only. Targeted harness `/tmp/poly_target.py` (7 wallets, real seed functions, `dry_run` semantics — no writes); leaderboard liveness `/tmp/poly_lb.py`; probation counts via `sqlite3 -readonly`. Full dry-run harness `/tmp/poly_watch_dryrun.py` launched then **killed** (Cloudflare) — wrote nothing (both `set_agent_state` calls are `dry_run`-guarded). Baseline `watch_only_whales` (07-19) untouched. Hold stands; roster unchanged.
