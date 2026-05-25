# Polymarket watchlist 100.0% WR sweep — investigation

**Date:** 2026-05-25
**Scope:** Read-only investigation. No code changes proposed.
**Question:** Why do ~17 of the top watchlist rows show EXACTLY 100.0% windowed WR over N=100 resolved BUYs?

## TL;DR

- **The "denominator excludes losses" hypothesis is REFUTED.** Code is correct on paper: `_select_resolved_buys_window` keeps any BUY whose market `status="resolved"` regardless of winner, and `_is_win_for_buy` returns `False` (not `None`) for losing trades. Losses ARE counted in the denominator. `compute_polymarket_stats` then computes `wins=sum(is_win)` / `len(resolved)`. Verified by both static read and live empirical replication.
- **The metric IS broken — for a different reason: window-by-order-fill vs window-by-decision.** Each `ActivityRow` is treated as an independent sample, but in Polymarket sports data 29 BUYs at the same `condition_id` are one decision repeated. During winning streaks, a single market cluster fills a large fraction of the 100-slot window.
- **Compounded by staleness, but staleness self-heals.** Mosley1 was 100/0 at seed time (2026-05-24 13:25 UTC); is actually 95/5 today after Fucsovics-vs-Berrettini resolved at 2026-05-25 14:34 UTC. The Sunday weekly overwrite naturally absorbs this. Clustering does NOT self-heal — it's structural and will reproduce every cycle until the decision-unit fix lands.

## Operational status — PROMOTION PAUSED on the Polymarket watch list

**Do not promote any whale off this list until the clustering fix is shipped.** Specifically — none of the current windowed columns are safe selection inputs:

- **WR%** — actively broken by clustering (29 correlated wins look like 29 wins).
- **Realized PnL (windowed)** — same cluster contamination, just with real dollars attached. Runaround's $44k windowed PnL is genuinely earned money, but it's dominated by one Knicks-spread spree.
- **`<.70` share** — entry-style filter, not a quality signal. A whale at 100% sub-$0.70 can still have a cluster-saturated window.
- **AvgPx** — same caveat as `<.70`.
- **Leaderboard PnL/Vol** — Polymarket lifetime self-report; never was a quality signal.

The "constant sample size = comparable across whales" premise the 2026-05-23 windowing redesign was built on **is invalidated** for the current screen — the 100 was 100 correlated fills, not 100 independent decisions. The decision-unit fix has to restore a real per-decision sample unit before any column on this panel can be trusted as a promotion input again.

## Evidence

### Mosley1 (longshot longshots, $0.39 avg entry — should NOT be 100%)

- Wallet: `0x5bec79df9add70a3892041ab1a5516b60f53b215`
- Stored at seed time (2026-05-24 13:25:14 UTC): wins=100, losses=0, WR=1.000
- Computed against live APIs (2026-05-25): wins=95, losses=5, WR=0.950

The 5 new losses: all on conditionId `0xa2a8b2f7…c8d7` ("Roland Garros ATP: Marton Fucsovics vs Matteo Berrettini"). Mosley1 bet `outcomeIndex=0` (Fucsovics) at price ~$0.43 five times in 60 seconds at 2026-05-25 11:01-11:02 UTC. Market resolved 14:34 UTC: `outcomePrices=[0.0, 1.0]` → Berrettini won → `winner_idx=1` ≠ `outcome_index=0` → 5x `is_win=False`. The code maps these correctly. They simply landed AFTER the last seed run.

This is a **staleness phenomenon**, not an algorithm bug — but it's also evidence that 100/0 is the floor that decays once new trades resolve, not a stable signal.

### Runaround (mixed entries 0.64 avg, 42% sub-$0.70 — true cluster case)

- Wallet: `0xc0ff6a9ac424210cf218fda5c5753324c34a9953`
- Stored: wins=100, losses=0, WR=1.000
- Computed today: wins=100, losses=0, WR=1.000 — **identical**

But: this whale has 26 losing markets across 65 distinct resolved markets if you walk the full activity feed. Every single loss sits at position 160+ in the buy-row feed. The first 100 in-window resolved BUYs are all from one cluster: Knicks playoff spread markets (29 separate BUYs on Cavaliers vs Knicks alone — same condition_id repeated). They all won (Knicks beat Cavs).

First 15 in-window BUYs from the live verification:

```
13 × "Spread: Knicks (-11.5/-6.5/-5.5/-8.5)" — outcomeIndex=0 "Knicks", winner=Knicks → 13 wins
```

All 13 are on the same playoff series. The window-100 limit fires before we see any other event.

Sample losses that sit OUTSIDE the 100-window (so don't count):
- "Spread: Spurs (-14.5)" — bet Spurs, Timberwolves won
- "Wild vs. Avalanche" — bet Wild, Avalanche won
- "Magic vs. Pistons" — bet Magic, Pistons won
- "Raptors vs. Cavaliers" — bet Raptors, Cavaliers won
- "Will Real Madrid CF win?" — bet No, Madrid won

True all-resolved WR for Runaround is ≈60% (39W/26L). Windowed WR is 100% because the most recent 100 trades happened to be the winning Knicks-spread spree.

## Why the whole dashboard shows the same pattern

The seed selection floors (`min_windowed_wr=0.62`, `min_windowed_pnl=$5,000`) bias toward whales whose most recent trades cluster on a single resolved event in their favor. Sports playoff whales are precisely the cohort that:

1. Spam-bets a single playoff series (high trade count per decision)
2. Has $5k+ realized PnL from one winning cluster
3. Shows 100% WR mechanically because all in-window trades are correlated copies of one bet

The dashboard's 17 of 18 visible 100% WR rows are NOT 17 independent counting bugs. They are 17 whales that recently rode a sports-streak cluster.

This also explains why AvgPx doesn't separate them: Mosley1 ($0.39 longshot) and Alder ($0.95 favorite-farmer) both show 100% via the same window-saturation mechanism, applied to different bet styles.

## Bug location

File: `trading_corp/scripts/seed_polymarket_watchlist_deep.py`
Function: `_select_resolved_buys_window` (lines 157-185)

The bug is semantic, not control-flow. Each `ActivityRow` is treated as an independent decision sample. In Polymarket sports data, 29 BUYs at the same `condition_id` are one decision repeated. The "last 100 resolved BUYs" denominator over-counts decision concentration as decision diversity.

## Candidate fix directions (NOT being picked this session)

Three plausible decision-unit fixes exist. **They are not equivalent** — each interacts differently with the `n ≥ min_resolved_buys=10` floor and the `n < provisional_threshold=50` provisional flag, and the cohort that survives each is different:

1. **Dedupe by `condition_id` before windowing** — keep one BUY per market (most recent), window is "last 100 distinct markets." Maximum honesty. Will collapse cluster-traders' `n` and tip many current 100% rows to provisional or under the n≥10 floor; that's truth surfacing, not damage.
2. **Cap same-`condition_id` slots** at some K of 100 — preserves a higher `n` for cluster-traders but partially launders the bias. Choice of K is non-obvious.
3. **`1 / n_buys_in_same_market` weighting** — keeps all rows in the denominator at fractional weight, preserves `n` for the floor/provisional checks but `wins/n` math becomes weighted.

Operator's current lean: dedupe-by-decision. Not locked. A dedicated fix-planning session needs to walk the cohort impact of each option against the current 329-row watchlist before any code change.

**Fix surface is Board-gated** per CLAUDE.md § 4 — `_select_resolved_buys_window` is live prod scoring shipped 2026-05-23 (see memory `project_pm_watchlist_windowed_live.md`). Do not edit in passing.

Staleness (Mosley1's case) is a separate, lesser axis. The weekly-overwrite cadence (first fire Sun 2026-05-31 ~13:00 UTC) absorbs new resolutions naturally; no design change needed there. Do not bundle a staleness "fix" with the clustering fix.

## Interim promotion basis — answer to the user's question

> "until this is resolved, I should be promoting on realized PnL + sub-$0.70 share, NOT WR%. Confirm that's the sound interim basis."

**No — the sound stance is to PAUSE promotion across all columns.** See "Operational status" above. PnL + sub-$0.70 is directionally better than WR (PnL at least represents real dollars; sub-$0.70 correctly filters favorite-farmers) but both inherit the same cluster contamination from the underlying window. Promoting on PnL today still over-picks sports-cluster whales.

## What did NOT happen

To be explicit, since the user's hypothesis pointed elsewhere:

- No losses are being silently dropped from `resolved`.
- `_is_win_for_buy` does not return `None` for legitimate losing BUYs on resolved markets.
- `_decode_resolution`'s `≥0.9` winner detection works correctly for both YES-winning and NO-winning binary markets, and the binary index correctly maps to `activity.outcome_index`.
- `fetch_market_resolutions` is returning resolved markets for losing BUYs (verified by hitting gamma directly).
- The gamma `conditionId` keys match `/activity`'s `conditionId` keys — no field misalignment.

## Artifacts

Verification scripts and per-trade samples preserved under `scripts/verification/2026-05-25_polymarket_wr/`:

- `verify_wr.py` — main empirical replication: stored agent_state vs live-API compute, for Mosley1 + Runaround
- `deep_dive.py` — full activity-feed walk to compute true all-resolved WR (Runaround's 39W/26L beneath the window)
- `mosley1_window_trace.py` — per-trade in-window trace, used to confirm Fucsovics losses arrived after seed
- `count_losses.py` — minimal loss counter
- `results.json` — per-trade samples (15 each for Mosley1 + Runaround), JSON

These are the evidence base for the eventual fix. Re-runnable against live Polymarket APIs (no auth, free public endpoints).
