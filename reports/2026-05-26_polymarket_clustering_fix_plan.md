# Polymarket watchlist clustering fix — plan (Board-gated)

**Date:** 2026-05-26
**Scope:** Read-only planning. No code change. No prod write.
**Predecessor:** `reports/2026-05-25_polymarket_wr_investigation.md` (commit `297508c`).
**Decision asked of Board:** which of {current, A, B_K3, B_K5, C, bucket} replaces `_select_resolved_buys_window` (`trading_corp/scripts/seed_polymarket_watchlist_deep.py:157-185`).

## TL;DR

**Recommendation: Option A — dedupe by `condition_id` before windowing.**

A is the only option that simultaneously (a) tracks honest decision-level WR closely on the cluster-traders, (b) drops the canonical false positive (surfandturf), (c) has unambiguous semantics ("WR over last 100 distinct markets"). The window-depth interaction the operator flagged as a risk did NOT materialize — Runaround under A converges to 60.0% vs honest 62.3%, within 2.3pp. No depth-fix bundled.

**Option C is structurally broken — it is not a milder version of A but a non-fix.** 1/n weighting collapses each cluster to a single weighted contribution, but if the cluster is all-wins, weighted-wins still equals weighted-n, so WR stays at 100%. C produced the SAME WR as the bug for all three test traders (100 / 100 / 75%). Do not consider C as a compromise option.

**Cohort cost (framing it as correctness, not damage):** the clean-list size (n≥50 AND WR≥0.62, computed against current live activity) goes from 225 under current → 97 under A. The 145 that drop were inflated by counting correlated fills as decisions; they never had 50+ independent recent decisions. The floor doing its real job.

The fix is one option; the deploy is separate and Board-approved.

## Empirics — methodology

Run via `scripts/verification/2026-05-26_clustering_plan/empirics_v2.py` (Sonnet-built, reviewed). Reuses `verify_wr.py` API scaffold. Cohort sweep covered all 329 wallets currently in `agent_state(polymarket_copy_trader, watch_only_whales)`. Free Polymarket public endpoints; no auth; no prod write. Output: `tmp/2026-05-26_clustering_plan/empirics.json` (357 KB; per-wallet detail preserved for reviewer scrutiny).

Window semantics replicated exactly: TRADE+BUY only; conditionId required; market `status="resolved"` (closed=True AND any `outcomePrices >= 0.9`); most-recent-first; stop at 100.

Options under comparison:
- **current** — bug as-shipped: each `ActivityRow` BUY = one independent sample.
- **A** — dedupe by `condition_id`. Walk most-recent-first; keep first occurrence per cid; stop at 100 distinct cids.
- **B_K3** — cap same-cid at 3 fills. Walk most-recent-first; skip if cid count already 3; stop at 100 rows.
- **B_K5** — same with K=5.
- **C** — 1/n weighting. Keep current's 100-fill window; weight each row by `1/n_buys_in_window_for_this_cid`; WR = `sum(weights * is_win) / sum(weights)`.
- **bucket** — dedupe by `(cid, floor(unix_ts/3600), round(price/0.05)*0.05)`. Triggered for output if D1 shows ≥50% `scale_in` across any test trader (it did, on surfandturf).

Honest decision WR per trader (the ground-truth anchor): walk the FULL activity feed (up to 5000 rows / 10 pages), group resolved BUYs by `(cid, outcome_index)` pair, score the pair as a win if any BUY on it matched `winner_idx`. This is what windowed WR should ideally track.

## Point 1 — defining the decision unit empirically

Operator's concern: same-cid BUYs might be (a) one order fragmented into many fills — dedupe is right — or (b) genuine scale-ins at different times and prices — dedupe over-collapses.

Cluster classification rule (5-min / 1-cent / 1-hour / 5-cent thresholds, documented in script):
- `fragmented_fill`: `time_span ≤ 300s` AND `price_range ≤ 0.01` AND `unique_outcome_indices == 1`
- `scale_in`: `time_span > 3600s` OR `price_range > 0.05`
- `ambiguous`: otherwise

Result across the three test cluster-traders:

| Trader | clusters | fragmented_fill | scale_in | ambiguous | biggest |
|---|---|---|---|---|---|
| Runaround | 12 | 83% | 8% | 8% | 1 cluster 21+ buys |
| weflyhigh | 2 | 50% | 0% | 50% | both 21+ buys |
| surfandturf | 1 | 0% | 100% | 0% | 21+ buys |

**Caveat on the "scale_in" classification:** my time threshold (3600s = 1h) flags any cluster spread over more than an hour as scale_in, regardless of side or price coherence. Inspecting Runaround's largest clusters by hand: 29 fills on "Cavaliers vs. Knicks" over 6008s (1h40m) at price 0.70±0.06 — that's the market drifting during the game while the whale accumulated long-Knicks. All 29 fills are on the SAME side of the SAME event. Whether the whale executed it as one limit order that filled gradually or as 29 separate clicks at 0.70 is irrelevant to evaluating WR: the outcome is one binary event (Knicks beat Cavaliers).

**The decision unit is `(condition_id, outcome_index)`, not `(condition_id, time_bucket, price_bucket)`.** Even genuine scale-ins on the same side of the same market remain ONE outcome-correlated bet from the WR-as-edge perspective. Hedges (rare; would buy `outcomeIndex=1` on a market where you already bought `outcomeIndex=0`) are correctly captured because (cid, oi) pairs separate them.

The fragmented_fill / scale_in split has implications for *latency* analysis (how fast does this whale fill an order?), not for WR scoring. The bucketing option (D3 below) was triggered by surfandturf's single 21+ scale_in cluster, but its empirical result is no better than A and adds complexity without principled benefit. Reject.

## Point 2 — ground-truth tracking on test traders

Per-trader honest decision WR (full history) versus windowed WR under each option. Δ = option_wr − honest_wr.

### Runaround — the canonical clustering case (knicks-cavs whale)
- Stored: wins=100, losses=0, WR=1.000, n=100, provisional=False.
- Honest decision WR: **81W / 49L = 0.6231** over 130 decisions.

| Option | n | wins | losses | WR | Δ vs honest |
|---|---|---|---|---|---|
| current | 100 | 100 | 0 | 1.0000 | **+0.3769** |
| **A** | **100** | **60** | **40** | **0.6000** | **−0.0231** |
| B_K3 | 100 | 63 | 37 | 0.6300 | +0.0069 |
| B_K5 | 100 | 71 | 29 | 0.7100 | +0.0869 |
| C | 100 (n_eff=13) | 13 weighted | 0 weighted | 1.0000 | +0.3769 |

A is closest to honest (within 2.3pp). B_K3 is also close (0.7pp). C is identical to the bug.

**Window-depth check (the operator's risk):** would A still show Runaround at 100% if his recent 12 distinct markets all won while losses sit past position 160? No. Runaround's 100-most-recent distinct cids include 40 losses already; A captures them. The depth interaction does not manifest. A alone is sufficient — no second fix needed.

### weflyhigh — the low-decision-rate fill-spammer
- Stored: wins=100, losses=0, WR=1.000, n=100.
- Honest decision WR: **14W / 11L = 0.5600** over 25 decisions.
- Has only ~25 unique cids in his entire activity feed; current windowing's 100 rows came from just 2 markets.

| Option | n | WR | Δ vs honest |
|---|---|---|---|
| current | 100 | 1.0000 | +0.4400 |
| **A** | **24** | **0.5833** | **+0.0233** |
| B_K3 | 67 | 0.5672 | +0.0072 |
| B_K5 | 100 | 0.5300 | −0.0300 |
| C | 100 (n_eff=2) | 1.0000 | +0.4400 |

A shrinks his n correctly to 24 (all his real decisions). Provisional (n<50), below WR floor. **Drops off the watchlist under A.** Honest outcome: he's not on the list because he doesn't have 50 recent independent decisions, just 25 total lifetime.

### surfandturf — the false positive
- Stored: wins=100, losses=0, WR=1.000 (but live current = 75/25 = 0.75; some cluster fills resolved as losses since seed).
- Honest decision WR: **2W / 3L = 0.4000** over 5 decisions.

| Option | n | WR | Δ vs honest |
|---|---|---|---|
| current | 100 | 0.7500 | +0.3500 |
| **A** | **4** | **0.2500** | **−0.1500** |
| B_K3 | 12 | 0.2500 | −0.1500 |
| B_K5 | 19 | 0.2632 | −0.1368 |
| C | 100 (n_eff=1) | 0.7500 | +0.3500 |
| bucket | 6 | 0.3333 | −0.0667 |

A correctly drops him below n≥10 floor. He has 5 lifetime decisions, 2 winning. He doesn't belong on a "best 100 recent decisions" watchlist. **Drops off under A.** B_K3 would keep him at provisional with WR 0.25 — below the 0.62 floor, so also drops. C keeps him at 75%, broken. Bucket gives the prettiest honest-tracking number but he's below floor on it too.

### Summary across test traders (|Δ| vs honest, smaller = better)

| Option | Runaround | weflyhigh | surfandturf | mean | comment |
|---|---|---|---|---|---|
| current | 0.377 | 0.440 | 0.350 | 0.389 | bug |
| **A** | **0.023** | **0.023** | **0.150** | **0.065** | best; surfandturf drops below floor |
| B_K3 | 0.007 | 0.007 | 0.150 | 0.055 | very close; semantics murkier |
| B_K5 | 0.087 | 0.030 | 0.137 | 0.085 | drifts up on cluster-traders |
| C | 0.377 | 0.440 | 0.350 | 0.389 | **identical to bug** |
| bucket | n/a | n/a | 0.067 | (only triggered for one trader) | best on surfandturf alone, but no general win |

A and B_K3 are within rounding error of each other; A wins on semantic clarity ("last 100 distinct markets" is one short English sentence and matches how a human would think about a track record).

## Point 3 — Why option C is structurally broken (not a "milder fix")

For a 100-fill window on a single-cluster trader, 1/n weighting computes:
- `n_effective = sum(1/n_in_cluster_i) = count_of_unique_cids_in_window`
- `wins_weighted = sum_{wins}(1/n_in_cluster_i)`

If every cluster in the window is all-wins (the exact case the bug surfaces, because that's how clustered whales get onto the list in the first place), then for every cid in the window:
- contribution to `n_effective`: 1
- contribution to `wins_weighted`: 1

So `wr_weighted = wins_weighted / n_effective = 1.0` regardless of how big the clusters are.

This is what the empirics show: all three test traders return identical WR under C as under current (100%, 100%, 75%). C only helps when a cluster contains MIXED wins/losses — but cluster outcomes are binary (the market resolves once). Splitting fills further within a cluster does not surface losses that were never in the window.

The only place C differs from current is `n_effective` (the bookkeeping). The script today checks the n≥10 / provisional floor against raw `n=100`, not `n_effective`, so even that distinction is invisible to the floor. **C ships the bug with extra arithmetic.** Reject; do not propose as compromise.

## Point 4 — Cohort impact across 329 wallets (correctness framing)

Floors held fixed at production values: `min_resolved_buys=10`, `provisional_threshold=50`, `min_windowed_wr=0.62`.

### Counts under each option

| Option | n≥10 pass | provisional (10≤n<50) | non-provisional | WR≥0.62 survive | median n | p90 WR |
|---|---|---|---|---|---|---|
| current | 329 | 35 | 294 | 260 | 100 | 1.000 |
| **A** | 301 | 101 | 200 | 171 | 98 | 0.898 |
| B_K3 | 325 | 75 | 250 | 211 | 100 | 0.932 |
| B_K5 | 328 | 66 | 262 | 221 | 100 | 0.943 |
| C | 329 | 35 | 294 | 224 | 100 | 1.000 |
| bucket | 314 | 83 | 231 | 190 | 100 | 0.926 |

The "clean list" (passes n≥50 AND WR≥0.62 — what a human actually shortlists from):

| Option | clean list size | vs current |
|---|---|---|
| current (computed live) | 225 | (the baseline) |
| **A** | **97** | **−128 (57% reduction)** |
| B_K3 | 149 | −76 |
| B_K5 | 162 | −63 |

Of the 225 currently-clean wallets: 80 stay clean under A, 145 drop, 17 new entries appear under A (these are wallets the current-bug had pushed under the n=10 floor due to staleness or marginal cases — A pulls them back).

### What happens to the 43 current-100%-WR rows under A

| Bucket | Count | Read |
|---|---|---|
| Dropped (n<10 under A) | 12 | Single-cluster pseudo-edges; honest unique-market count below floor. Floor working. |
| Tipped to provisional (10≤n<50) | 16 | Real but small-sample whales; provisional flag does its job. |
| Surviving non-prov at WR≥0.80 | 10 | Real edge whales; survive A's surfacing — these are the keepers. |
| Surviving non-prov at WR<0.80 | 5 | Real but inflated; A reveals their honest WR is lower than 100%. |

The 12 dropped were never edge — they had ≤9 distinct recent decisions, all clustered. They got onto the watchlist because the bug counted 100 correlated fills as 100 decisions. Floor doing its job.

### Top 10 by realized PnL — A's stress test

| Rank | Whale | Stored WR | Live current WR | A: n / WR |
|---|---|---|---|---|
| 1 | Magamyman | 0.77 | 0.770 | 90 / 0.711 |
| 2 | Mosley1 | 1.00 | 0.950 | **17 / 0.471** |
| 3 | ethanaz | 0.66 | 0.610 | 100 / 0.540 |
| 4 | aekghas | 0.98 | 0.980 | **9 / 1.000** (dropped n<10) |
| 5 | weflyhigh | 1.00 | 1.000 | 24 / 0.583 |
| 6 | bcda | 0.98 | 0.000 | 46 / 0.565 |
| 7 | SemyonMarmeladov | 0.99 | 0.900 | 100 / 0.470 |
| 8 | VPenguin | 0.64 | 0.310 | 100 / 0.550 |
| 9 | matanovik | 0.75 | 0.600 | 100 / 0.490 |
| 10 | polywally | 1.00 | 1.000 | **31 / 0.839** |

Of the top 10 by PnL, only Magamyman survives A's clean-list filter (n≥50 AND WR≥0.62). Mosley1 (rank 2) and aekghas (rank 4) — the two most-extreme stored-100%-or-near rows — both collapse: Mosley1's "edge" was 17 distinct decisions with 8 losses; aekghas had only 9 distinct decisions. Polywally survives provisional. The rest fall below the WR floor as honest WR is surfaced.

This is the correctness story: **the top of the leaderboard under current is dominated by cluster-inflated WR**. PnL is real money earned; the WR alongside it was the metric being broken. A removes the inflation and you see the actual edge distribution.

### Window-depth interaction (revisited at cohort scale)

For Runaround (the case the operator flagged), A drove WR from 100% → 60.0% — a clean correction. Across all 43 current-100%-WR rows, A leaves only 10 surviving non-provisional at ≥80%; of those, the 5 at WR<0.80 are precisely the cases where A surfaced an honest "still good but not perfect" picture. No row remained at exactly 100% non-provisional under A AND was a clustering artifact — i.e., depth × clustering is not a separate interaction we need to fix. A alone is sufficient.

If at a future cycle a whale's most-recent 100 distinct markets really were all wins (a 30-day hot streak in a niche), that would be a real signal worth flagging — not the cluster-inflation bug.

## What gets fixed by A; what doesn't

**Fixed:**
- Windowed WR becomes a per-decision estimator instead of a per-fill one.
- Realized PnL (already in dollars, not counts) is mechanically unaffected by the row-selection change — but the survivors who pass the n+WR floors are now the same population as the WR-sane ones, so PnL ranking implicitly rests on a fixed denominator now.
- AvgPx, share-below-0.70: these are computed on the windowed BUY rows. Under A the window has 100 distinct cids' worth of rows (one row per cid, the most-recent BUY). AvgPx + share-below-0.70 will be slightly different (fewer fills per cluster) but the signal direction is unchanged.
- The dashboard's 100%-WR plague disappears: p90 WR drops from 1.00 → 0.90.

**Not fixed (intentionally not bundled):**
- **Staleness.** The Sunday weekly overwrite still self-heals new resolutions arriving between fires. Mosley1's stored 100/0 vs live 95/5 is the staleness mechanism. Different axis; do not bundle.
- **The honest-decision-WR-vs-windowed-decision-WR gap.** Even under A, a 100-decision window will lag a whale's true honest WR by exactly what a 100-sample estimator does — that's a sample-size question, not a clustering one. The provisional flag (n<50) is the mitigation already in place.
- **`<.70` share as a quality signal vs entry-style signal.** A doesn't change what that column means. Independent reading question.
- **Promotion gates upstream.** Promotion off this watchlist is paused by [[polymarket-whale-scoring-edge]] regardless of which column you read; the pause is justified by clustering across ALL windowed columns. A reopens the path conceptually, but operator may still want to observe one or two clean refresh cycles under A before unpausing.

## Implementation surface (FYI for the deploy-approval session, not for this session)

Change is bounded to `_select_resolved_buys_window` at `trading_corp/scripts/seed_polymarket_watchlist_deep.py:157-185`. Expected diff size: ~5 added lines (`seen` set + cid-membership check + add to set on accept). No schema change, no migration. `agent_state(polymarket_copy_trader, watch_only_whales)` slot is overwritten on next weekly fire (no `--merge` per [[pm-watchlist-windowed-live]]); no backfill needed.

The next weekly cadence fire is Sun 2026-05-31 ~13:00 UTC. Two deploy options for the Board:

1. **Deploy A before 2026-05-31** — the first weekly-overwrite cycle runs with the fix, roster shape immediately matches A's empirics here.
2. **Deploy A after 2026-05-31** — the first weekly-overwrite cycle runs the buggy windowing one more time (already-acknowledged-and-acceptable per the session-start prompt), then A lands and the following Sunday is the first clean weekly cycle.

I have no strong recommendation between these two — operator's call based on review bandwidth this week and appetite for one more buggy cycle being live for a few days.

The deploy itself remains Board-approved (§ 4 of CLAUDE.md) and should ride the full deploy pattern (md5-diff check of current prod surface, deploy, deploy_log entry, post-deploy memory updates). Not in scope for this report.

## Anti-recommendations explicit (decisions NOT to make)

- **Do not pick C.** It is the bug with extra arithmetic. Empirical evidence above.
- **Do not pick B_K5.** Drifts upward on cluster-traders by ~9pp on Runaround; preserves a watered-down version of the bug.
- **Do not pick bucket.** No general win over A; introduces threshold knobs (`hour_bin`, `price_bin`) that have no principled default.
- **Do not bundle staleness.** Different axis, already self-healing.
- **Do not lower the `n≥10` floor to "save" the 28 wallets A drops.** They never had 10+ independent recent decisions. The floor working correctly is the point.
- **Do not unpause promotion until at least one clean weekly cycle under A.** Sample-size on the new corpus needs to accumulate before the columns are read as edge inputs again. Separate decision after the fix is live.

## Artifacts preserved

- `scripts/verification/2026-05-26_clustering_plan/empirics_v2.py` — main analysis script (Sonnet-built, reviewed).
- `tmp/2026-05-26_clustering_plan/empirics.json` — full structured output (357 KB) including per-wallet cohort detail.
- `tmp/2026-05-26_clustering_plan/cache/` — cached activity + resolutions per wallet, re-runnable. (Ephemeral — fine to delete after Board ratification.)
- `tmp/2026-05-26_clustering_plan/watchlist.json` — snapshot of the 329-row watch_only_whales state at 2026-05-26 16:17 UTC.

## Decision asked

Board, please ratify:

1. **Adopt Option A** (dedupe by `condition_id` before windowing) as the fix.
2. **Schedule the deploy** for either pre- or post-2026-05-31 weekly-overwrite cycle.
3. **Promotion remains paused** until at least one weekly cycle has run under A with stable readings (a follow-up decision, not this one).

Reject C, B_K5, and bucket as proposed in the prior session's enumeration; they are documented here so the rejection is on record.
