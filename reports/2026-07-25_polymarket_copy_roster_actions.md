# PCT Roster Actions — KEEP / REMOVE / PROMOTE (decision-support)

**Date:** 2026-07-25 · **Mode:** READ-ONLY. No `--algo-select`, no promotions, no roster writes. This is the candidate list; the operator executes changes separately.
**Windows:** current-roster metrics over the longest clean window (post-epoch, `entry_ts ≥ 2026-05-21T12:28:07`, through 2026-07-26). Watchlist metrics are the corrected-algo snapshot captured **2026-07-19** (single point-in-time).

**Two structural facts that frame everything below:**
- **The corrected 07-19 screen and the live roster are DISJOINT** — none of the 14 currently-copied whales pass the corrected screen (incl. our top performer `llllllII`). The screen's **$5,000 realized-PnL floor biases toward big-bankroll whales**, not high-edge-at-our-size whales; it misses the small-stakes esports whale we actually profit from.
- **Watchlist track records are un-copied and single-snapshot.** We have never copied these whales; their numbers are their *own* Polymarket realized activity as of 07-19, "observed" in our system for 6 days. True durability (positive across sub-windows) is **not** verifiable from stored data — see PROMOTE caveats.

---

## KEEP (3) — copy-verified sustained performers, ranked

Metrics: post-epoch. `dec_wr` = win rate on distinct (whale,market,outcome) decisions (net>0), the honest denominator.

| # | Whale | Cat | Net PnL | dec_wr | decisions / markets | last copy | AvgPx / sub70 | Monthly (May→Jun→Jul) |
|---|---|---|---|---|---|---|---|---|
| 1 | **llllllII…** (esports/LoL) | Sports | **+$304.30** | 0.584 | 485 / 463 | 0d | 0.57 / 0.72 | +133 → −23 → **+194** |
| 2 | **Hakei.** | Tech | **+$95.19** | **0.796** | 103 / 90 | 0d | 0.64 / 0.47 | — / — / +95 |
| 3 | **kitten147** | Crypto | +$11.19 | 0.607 | 56 / 34 | 3d | 0.76 / 0.19 | +0.06 → +3.6 → **+7.6** |

- **#1 llllllII** — the workhorse: deepest, widest breadth (463 distinct markets, low per-market clustering), active today, positive in 2 of 3 months with the biggest month most recent. **Exposure flag:** concentrated in esports/LoL BO3 match markets (correlated same-tournament outcomes) — this ONE whale is ~90% of the roster's positive PnL, so the book's "edge" is really this whale's edge. Sharp price profile (AvgPx 0.57). *Not in the corrected watchlist* (too small realized $ to clear the $5k floor) — a copy-verified winner the algo would drop.
- **#2 Hakei.** — highest honest WR (0.796) among keepers, good breadth (90 markets), active today. **Caveat:** July-only data (promoted 07-07) = ~3-week window; strong but not yet long. Keep, confirm it holds a second month.
- **#3 kitten147** — small but the **most consistent trend** (positive and rising every month) and a genuine diversifier (Crypto, favorite-lean AvgPx 0.76). Marginal keeper.

## WATCH (1) — good historical record, fading activity

| Whale | Cat | Net PnL | dec_wr | decisions / markets | last copy | note |
|---|---|---|---|---|---|---|
| **0x594d0c9a…** | Sports | +$42.87 | 0.603 | 68 / 66 | **20d ago** | best breadth (66 mkts, ~1 copy/mkt = cleanest signal); May +30 / Jun +13 / Jul ≈0 |

Historically the cleanest-diversified positive whale, but July activity ≈ 0 (last copy 07-05). **Keep only if it resumes generating signals; else move to REMOVE for dormancy.**

## REMOVE (7) — with the numbers justifying each cut

| # | Whale | Cat | Reason | The numbers |
|---|---|---|---|---|
| 1 | **superbeter007** | Sports | **Deteriorating**, sufficient n | May +$15 → Jun +$47 → **Jul −$72**; forward (07-08+) **−$69.43**, recent WR ~7.6%; 124 fills / 54 decisions. Confirmed. Autopause-shadow has flagged it 6,908×. |
| 2 | **Civic-Static** | Crypto | Negative over adequate n | −$7.33, dec_wr **0.424**, 33 decisions, July-only, negative from entry. |
| 3 | **LJa7io23MCv954j** | Politics | Negative, low WR | −$10.37, dec_wr **0.333**, 6 decisions (thin but clearly bad). |
| 4 | **TimmyTurner123** | Sports | **Dormant + one-shot** | Dormant **43d**; the +$246 is 100% from a single May cluster on **8 markets / 9 decisions** — a frozen historical spike, not live breadth. Non-repeatable. |
| 5 | **4gibg4i3o** | Sports | Dormant + noise | Dormant **28d**, 8 decisions, +$2.66 (noise). |
| 6 | **Magamyman** (0x4dfd…) | Politics | **Never active** | **0** copied trades ever. Dead weight. |
| 7 | **monkeybar** (0x9cc7…) | Sports | **Never active** | **0** copied trades ever. Dead weight. |

## PROBATION (3) — insufficient data to judge (all promoted 07-07, <20 fills)

`Moond` (19 fills / 7 decisions, +$3.12) · `ChadStarmer` (9 / 4, +$0.84) · `potatobrahh` (3 / 2, −$0.23). Cannot justify keep OR cut on signal. Recommend a short probation with a hard decision date, or cut to reduce noise. Not counted as edge either way.

*(3 KEEP + 1 WATCH + 7 REMOVE + 3 PROBATION = 14.)*

---

## PROMOTE — ranked watchlist candidates (from the 07-19 corrected screen)

**Pool:** 123 gate-passers (WR ≥ 0.62, realized ≥ $5k, recency ≤ 60d by construction). Of these: **46 non-provisional** (≥50 resolved positions), 77 provisional (<50); 63 span ≥30d, 53 span ≥45d; 26 are favorite-farmers (AvgPx > 0.85). I prioritize **depth (non-provisional) + long `window_days_span` + sharp price profile + strong realized ROI**, and DE-prioritize favorite-farmers and spiky windows, per the "long consistent windows over hot streaks" rule.

`win/life` = window realized PnL ÷ lifetime leaderboard PnL (≈1 = window representative/durable; ≫10 = recent hot streak). All are **un-copied, single-snapshot** — see caveats.

### Tier 1 — deep + sharp + large edge (best available)
| Rank | Whale | Cat | n | WR | Realized | ROI% | span_d | win/life | AvgPx / sub70 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **DegenKingBetter** | Sports | 100 | 0.65 | $114.5k | 24.7 | 44.3 | 24.7 | 0.54 / 0.99 |
| 2 | **boomingtest** | Sports | 78 | 0.63 | $81.4k | 31.2 | 38.8 | 11.2 | 0.51 / 0.94 |
| 3 | **papuas** | GLOBAL | 100 | 0.62 | $74.5k | 17.4 | 33.6 | 13.8 | 0.50 / 0.99 |

Deepest (n≈100), sharpest (AvgPx ~0.5, sub-70 ~0.95+ = genuine underdog edge, not favorite-farming), strong realized ROI, ~5-week spans. `boomingtest` has the tamest win/life (11.2 = least hot-streaky).

### Tier 2 — longest observation windows (durability-first)
| Rank | Whale | Cat | n | WR | Realized | ROI% | span_d | win/life | AvgPx / sub70 |
|---|---|---|---|---|---|---|---|---|---|
| 4 | **CVCM** | Sports | 100 | 0.67 | $19.5k | 18.7 | **80.4** | 16.8 | 0.55 / 0.54 |
| 5 | **viktorurolog16** | Politics | 72 | 0.78 | $17.5k | **55.5** | **208** | 55.7 | 0.59 / 0.76 |
| 6 | **ox1star84** | Sports | 70 | 0.87 | $15.1k | 14.3 | **280** | **10.2** | 0.71 / 0.36 |
| 7 | **marchonnow** | Politics | 69 | 0.71 | $27.2k | 19.9 | 52.9 | 95.0 | 0.70 / 0.38 |

Longest spans among deep whales. `ox1star84` (280d span, win/life 10.2) has the best window-vs-lifetime corroboration = least likely a hot streak. `CVCM` = long (80d) + deep (100) + sharp (0.55) — the cleanest durability profile. `viktorurolog16` = very long (208d) + sharp + 55% ROI.

### Spiky/short-window — high realized $ but DEPRIORITIZE (fail the "no hot streak" rule)
- `Gloobera` ($73k, span **11d**, win/life **324**), `oss` ($60k, win/life **61,893** = leaderboard ≈$0, unverifiable), `bignewsagencypro` (ROI 99% — implausible), and all **provisional** rank-1/2 names: `Winnnnnnning` (27 pos / 15d / win-life 4.2), `Vvv`, `BirdMan.` (35 pos / **1.9d**), `MajidJavadi` (15 pos), `RhinoBank` (13 pos / **1.2d**). Big dollars, thin/short evidence → not durable enough to lead with.

### Favorite-farmers — EXCLUDE (high WR, illusory edge, tail risk)
`0x4E8Bd0…` (WR 0.96, AvgPx 0.95/sub70 0.02), `nedsta` (0.98/0.95/0.0), `Smallpeepee` (0.98/0.97/0.0), `Paeniscus.` (0.98/0.96/0.0), `air3` (0.90/0.87/0.10), `MarkLuis` (0.94/0.93/0.0), `jaytee158`, `kutsumiakia`, `f2hf84hg52h5`, `0x120b3E…`, `0x8DB5…`, `tony1919` (borderline, 0.78). These print 90–98% WR by betting near-certainties — the classic favorite-farming impostor; small edge, blow-up risk on the rare loss.

### PROMOTE caveats (load-bearing — the "durable vs hot-streak" answer)
1. **No copy verification.** Every number is the whale's own Polymarket realized activity, not our copied result. Promotion acts on a third-party track record we have never reproduced.
2. **Single snapshot (07-19).** I cannot confirm "positive across multiple sub-windows" from stored data — only aggregate window stats exist. The clean durability test is a **second watch-only refresh** (a later time-point): a whale that still passes weeks later is durable; one that drops out was a hot streak. That is read-only and advisory — recommended before any promotion.
3. **Systematic hot-window bias.** Almost every candidate's window PnL is **10–300×+ its lifetime leaderboard PnL** — the 07-19 pull caught them mid-run. Even Tier-1 `DegenKingBetter` shows the window at ~25× lifetime. `ox1star84` (win/life 10.2) is the least affected.
4. **Big-bankroll ≠ copyable-at-our-size.** These whales bet $150k–$600k per window; at our fractional sizing on the thin markets they move, their realized ROI will not survive our latency/slippage. High realized *dollars* is not the same as high copyable *edge*.

**Bottom line on PROMOTE:** if forced to rank today, lead with **`ox1star84`** (durability-corroborated), **`CVCM`** (long + deep + sharp), **`DegenKingBetter`** (deepest + sharpest + biggest edge), then `boomingtest`, `papuas`, `viktorurolog16`, `marchonnow` — but the honest recommendation is to **re-run the watch-only refresh for a second time-point first**, because none of these has a copy-verified or multi-snapshot track record, and the 07-19 window systematically favors recent hot runs.

---

### Provenance
Prod DB read-only via `sqlite3 -readonly`; scripts `poly_roster_deep.sh`, `poly_watch_probe.sh`, `poly_promote.sh`. No prod writes. Hold stands; roster unchanged.
