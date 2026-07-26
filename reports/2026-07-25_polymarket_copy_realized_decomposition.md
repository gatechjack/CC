# PCT PROMOTE Survivors — REDEEM-Grounded Realized Decomposition (option-c)

**Date:** 2026-07-25 · **Mode:** READ-ONLY, no roster/watchlist writes. · **Trigger:** operator caught that both durability snapshots scored on `compute_polymarket_stats` (HELD-to-resolution) — impostor #4 — so a whale that never closes its losers looks steady across any number of snapshots. This pass re-scores every remaining survivor on `build_audit_report` (REDEEM-grounded realized PnL), over each whale's **full exhausted activity window** (not the last-100 held window).

**Methodology validated:** re-running the decomposition on **marchonnow** reproduced the operator's numbers to the cent — realized **−$3,010**, held **+$48,223**, inflation **1.06**, clean-holds **+$10,280**, partial-sells **−$13,291**, 54% >85¢, clustering 3.66×. The harness is faithful; the five survivor results below are trusted.

---

## The decomposition (full-window, REDEEM-grounded)

| Whale | Realized$ | Held$ | Infl.ratio | Clean-hold$ | Partial-exit$ | ROI | >85¢ | Clustering | WR | n_dec |
|---|---|---|---|---|---|---|---|---|---|---|
| **DegenKingBetter** | **+97,574** | 116,675 | 0.16 | **+57,625** | +39,950 | +15.0% | 0.00 | 4.85 | 0.59 | 128 |
| **ox1star84** | **+17,207** | 17,456 | **0.01** | **+12,553** | +4,654 | +14.8% | 0.18 | 6.01 | 0.87 | 77 |
| **CVCM** | **+13,562** | 11,064 | **−0.23** | +3,830 | +9,732 | +9.5% | 0.08 | 1.25 | 0.50 | 325 |
| **viktorurolog16** | +27,996 | 43,858 | 0.36 | **−10,287** | +38,283 | +16.2% | 0.14 | 5.73 | 0.69 | 232 |
| **papuas** | **−61,264** | −49,430 | 0.24 | **−671,484** | +610,221 | **−2.6%** | 0.00 | 2.63 | 0.50 | 691 |
| *marchonnow (control)* | *−3,010* | *48,223* | *1.06* | *+10,280* | *−13,291* | *−0.7%* | *0.54* | *3.66* | *0.74* | *170* |

Answering the operator's 5 items per candidate:

### papuas — **CUT** (the big catch)
1. **Realized is NEGATIVE: −$61,264** (ROI −2.6%), while the held mark reads −$49k and the *windowed* held screen reported it as a top-3 **+$71k** promote. The held-basis screen was off by ~$130k of sign-and-magnitude.
2/3. **Clean holds −$671,484** — catastrophic. It holds enormous losing positions to $0; only +$610k of active partial-sell trading claws it back to −$61k net. On $2.4M of buys.
4. Not favorite-farming (0% >85¢, sharp 0.51 avg) — but sharpness is irrelevant when realized is deeply negative.
5. Clustering 2.63 (moderate).
→ **Fails the realized test outright. This is exactly the whale the held-mark ranking hid** — sharp-looking, high-volume, "steady" across both snapshots, and losing money.

### viktorurolog16 — **DOWNGRADE / conditional** (edge is in exits, not holds)
1. Realized +$28k positive (good), inflation 0.36 (moderate).
2/3. **Clean holds are NEGATIVE −$10,287**; the entire +$28k realized comes from partial-sell/round-trip trading (+$38,283). It is a good *trader*, a losing *holder*.
4. Sharp, not favorite-farming (14% >85¢).
5. Clustering 5.73 (high).
→ Passes the letter of "realized+ and exits create value," but **for COPY it is dangerous: if we copy entries and hold to resolution, we inherit the −$10k clean-hold profile, not the +$38k exit profile.** Copyable ONLY if the copy system replicates its exit timing faithfully — which it does not reliably. Rank below the genuine holders.

### DegenKingBetter — **PROMOTE #1** (best realized)
1. Realized **+$97,574** (largest), inflation only 0.16.
2/3. **Clean holds +$57,625 (59% of realized)** — a genuine holder; exits ALSO positive (+$39,950). Both legs make money.
4. **0% favorite-farming**, 98% sub-70¢ — the sharpest profile in the set.
5. Clustering 4.85 (high) but inflation stays low → clustering is NOT masking paper PnL here.
→ Ironically ranked #6 / "cooling" on the held-basis durability test; on realized cash it is the **strongest**. (Recency caveat from snapshot-2 still stands: recent momentum is softening — watch, but the realized quality is real.)

### ox1star84 — **PROMOTE #2** (cleanest holder)
1. Realized +$17,207, **inflation 0.01** (held ≈ realized — almost no paper).
2/3. **Clean holds +$12,553 (73% of realized)** — the most hold-driven edge; exits +$4,654 (also positive).
4. Favorite-LEAN (avg 0.71, 18% >85¢) — the one yellow flag — but unlike marchonnow it is paired with **positive realized and positive clean holds**, so the favorite-lean is not masking a loss.
5. Clustering 6.01 (highest) — but inflation ~0, so benign here.
→ Genuine hold-to-resolution edge, near-zero inflation. Strong copy candidate (edge is in entries).

### CVCM — **PROMOTE #3** (highest integrity)
1. Realized +$13,562 with **inflation −0.23 — realized EXCEEDS the held mark** (zero paper; the safest possible signature).
2/3. Clean holds +$3,830 AND partial exits +$9,732 — **both positive**.
4. Sharp (0.50 avg, 76% sub-70, 8% >85¢).
5. **Clustering 1.25 — the lowest** (≈1 fill/decision, no cluster-masking at all).
→ Lowest headline $ but the cleanest record: positive on both legs, negative inflation, no clustering, sharp entries. Low WR (0.50) is longshot style, not a defect (ROI +9.5%).

---

## Re-ranked PROMOTE list — REALIZED, clean-hold basis

| New rank | Whale | Why it survives | Watch-flag |
|---|---|---|---|
| **1** | **DegenKingBetter** | +$97.6k realized, +$57.6k clean holds, 0% favorite, sharpest, exits + | recent momentum cooling (snapshot-2) |
| **2** | **ox1star84** | +$17.2k realized, +$12.6k clean holds (73%), inflation ~0, WR 0.87 | favorite-LEAN (avg 0.71) — monitor |
| **3** | **CVCM** | +$13.6k realized, **negative inflation** (realized>held), clustering 1.25, sharp | low WR 0.50 (longshot style, not a defect) |
| **4 (conditional)** | **viktorurolog16** | realized + and exits + | **clean holds −$10.3k — copyable only if exits are replicated; holding it loses** |
| **CUT** | **papuas** | — | **realized −$61.3k; clean holds −$671k. Held-screen illusion.** |
| **CUT** | **marchonnow** | — | realized −$3.0k, exits −$13.3k, 54% favorite, inflation 1.06 (operator-confirmed) |

## The meta-finding

The held-basis durability ranking was **substantially inverted** by the realized decomposition:
- **papuas**: durability #3 → **CUT** (realized −$61k). The screen's sharp, high-volume "top-3" is a money-loser.
- **viktorurolog16**: durability #1 → **#4 conditional** (loses on holds; edge is exit-timing).
- **DegenKingBetter**: durability #6 / "cooling" → **realized #1** (+$97.6k, cleanest legs).
- **ox1star84 / CVCM**: durability #4/#2 → confirmed as the genuine clean holders (#2/#3).

Two of the six PROMOTE survivors are realized-negative or hold-negative; only **three** (DegenKingBetter, ox1star84, CVCM) have a genuinely positive realized *and* value-creating exits *and* positive clean-hold edge — i.e., an edge that copy-trading (which replicates entries and holds) can actually capture. viktorurolog16 is copy-fragile; papuas and marchonnow are out.

**Net:** promote from **{DegenKingBetter, ox1star84, CVCM}** only, in that order, if promoting at all. The screening pipeline should move off `compute_polymarket_stats` (held) onto `build_audit_report` (realized) before it selects — the held mark ranked a −$61k realized loser as a top pick.

---

### Provenance
Read-only. Harness `/tmp/poly_audit.py` = real `build_audit_report` over exhausted `/activity` (max_pages=10, target=exhaustion), `fetch_market_resolutions` for 1,661 cids; no DB/agent_state writes. marchonnow run as a validation control and reproduced the operator's figures exactly. Baseline `watch_only_whales` untouched. Hold stands; roster unchanged.
