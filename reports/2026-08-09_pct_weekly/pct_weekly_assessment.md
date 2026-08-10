# PCT Weekly Assessment — REALIZED-basis roster review (decision-support)

**Date:** 2026-08-09 (data pulled 2026-08-10 02:43–02:59 UTC) · **Mode:** READ-ONLY. No prod writes, no `--algo-select`, no roster/watchlist/pin changes. Operator actions all UI changes.
**Method:** Every whale ranked on **`build_audit_report`** (REDEEM-grounded realized PnL over exhausted `/activity`), NOT the watchlist screen's held-basis `compute_polymarket_stats`. Held shown only as a secondary/inflation column. Copy-list also cross-checked against our **actual copied-trade record** (`polymarket_round_trips`, prod DB).
**Reproducibility:** `viktorurolog16` re-audit reproduced its 07-25 figures to within noise (clean-hold −$11,131 vs −$10,287; realized +$27,192 vs +$27,996) — harness is faithful.

---

## TL;DR

- **The copy list shrank 14 → 7** since 07-25; you actioned the prior PROMOTE trio (DegenKingBetter / ox1star84 / CVCM — all now copied + pinned) and cut ~10 whales incl. kitten147.
- **The three prior PROMOTE picks all HELD UP on realized basis** (DegenKing +$97.6k→+$110k, ox1star84 +$17.2k→+$20.1k, CVCM stable). Good calls.
- **`llllllII` — prior KEEP #1, our biggest copied earner — has gone DORMANT (9d) and its own realized edge decayed to breakeven-negative.** It and Hakei were **99.3% of our copied PnL**; with llllllII silent, the book now rides on **Hakei alone**. This is the headline risk.
- **Watchlist audit found 4 genuine clean-holders worth promoting** (rollobravado, Kosherlocks, GreatestTrader, olddirtyfighter) and **2 strong prior-cuts worth reconsidering** (Moond, kitten147 — both off-watchlist, need manual pinning). It also caught 3 held-basis impostors (imnice −$67k realized vs +$35k screen; hurrican clean-holds −$123k; Cuco84).

---

## GROUP A — current copy list (7), realized decomposition

Own-whale `build_audit_report` (full recent window) + our actual copied record. `wr` = decision WR (n_win/n_res). "our$" = our copied realized PnL post-epoch. Dollars are the whale's own book unless prefixed "our".

| Whale | own realized$ | held$ | infl | **clean-hold$** | partial$ | own WR (n) | avg/ fav85 | our$ (our WR, n) | last copy | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| **DegenKingBetter** | **+109,824** | 129,763 | 0.15 | **+64,078** | +45,746 | 0.60 (134) | 0.54 / **0.00** | +3.98 (0.88, 8) | 0d | **KEEP #1** elite |
| **ox1star84** | +20,067 | 20,344 | 0.01 | **+15,414** | +4,654 | **0.87** (82) | 0.71 / 0.17 | +1.49 (0.75, 4) | 1d | **KEEP #2** holder |
| **Hakei.** | +61,912 | 110,946 | 0.44 | +6,515 | +55,397 | 0.69 (615) | 0.67 / 0.36 | **+210.87** (0.77, 213) | 0d | **KEEP #3** (workhorse; flag: exit-driven+fav-lean) |
| **CVCM** | +13,702 | 11,348 | **−0.21** | +3,970 | +9,732 | 0.50 (338) | 0.51 / 0.08 | −3.81 (0.67, 12) | 0d | **KEEP #4** (cleanest signature; tiny copied n) |
| **potatobrahh** | +14,624 | 46,341 | 0.68 | +1,693 | +12,932 | 0.60 (115) | 0.53 / 0.18 | +0.55 (0.67, 3) | 10d | **REMOVE** (edge in exits; no new entries this wk) |
| **ChadStarmer** | +3,628 | 17,747 | 0.80 | +204 | +3,423 | 0.59 (85) | 0.49 / 0.16 | +0.84 (0.75, 4) | 16d | **REMOVE** (clean-hold ~$0; quiet) |
| **llllllII** | **−36,081** | −2,434 | 13.83 | **−33,040** | −3,041 | 0.59 (694) | 0.57 / 0.03 | +201.54 (0.57, 549) | **9d dormant** | **REMOVE** (dormant + edge decayed) |

**Copy-list reads:**
- **DegenKingBetter** — elite and improving. +$110k realized, +$64k from clean holds (58%), **0% favorite-farming**, sub-70 0.99 (sharpest in the whole set), active now. The best whale on the roster; copy captures its edge (holds make money).
- **ox1star84** — genuine holder. +$20k realized, **77% from clean holds**, WR 0.87, inflation ~0, +$2.3k own realized this week. Edge is in entries = exactly what copy captures. Favorite-lean (avg 0.71) is the one watch-item.
- **Hakei.** — the current workhorse **empirically** (our +$210.87 over 213 decisions, WR 0.765, +$14.9 last week, active daily). BUT the realized decomposition flags fragility: clean-holds only +$6.5k of +$62k realized (edge is in exits/round-trips +$55k) and **36% of decisions above 85¢** (favorite-lean). Our copied result is genuinely good, so KEEP — but it is a **thin, exit-dependent edge** and now the book's single point of failure. Monitor.
- **CVCM** — cleanest integrity signature: **negative inflation −0.21** (realized ≥ held), clustering 1.28 (≈1 fill/decision, no masking), 338 decisions, positive on both legs, active. Small dollars and low WR 0.50 (longshot style, not a defect). We've copied only 12 decisions (−$3.81) — not enough to judge our capture yet, but the whale is clean. Keep.
- **potatobrahh** — **REMOVE.** Realized +$14.6k but clean-holds only +$1,693 (inflation 0.68 — edge is in exits, which copy does not replicate). No new entries this week; last copy 10d. We've booked +$0.55 on 5 fills. No copy-capturable edge.
- **ChadStarmer** — **REMOVE.** Clean-holds +$204 (~zero), inflation 0.80 (80% of headline is paper/churn), quiet 16d since last copy. Nothing to capture.
- **llllllII** — **REMOVE (or demote-to-watch).** The big change. Prior KEEP #1 on our +$304 copied result; never realized-audited before. Its own recent realized is **−$36,081** with **clean-holds −$33,040** — i.e., even its hold baseline is roughly breakeven (held −$2.4k on $2.6M) and its active trading destroys value. Critically it is **DORMANT ~9 days** (last copy 07-31, zero activity/entries this week) — you cannot copy silence. Our booked +$201.54 is now a **frozen** number; its June copies were already −$22.76. *Caveat:* esports/LoL markets have between-tournament gaps, so the 9d silence could be schedule, not death — but paired with the negative own-realized, it no longer earns its slot. If you want to hedge the esports-schedule possibility, demote to watch rather than hard-cut.

---

## GROUP B — watchlist (106), realized decomposition of the judgeable pool

**Screen composition:** 106 total · 46 with n≥50 (judgeable) · 20 with n∈[30,50) · **40 too thin (<30) — not assessable yet** · 60 provisional · **24 favorite-farmers (avg>0.85, excluded from PROMOTE)** · 98 traded within 7d.
Audited the 12 strongest non-favorite, non-artifact candidates (excluded lifetime≈0 unverifiables `oss/ronaldk/pkz` and span<2d hot-windows `ZhengYing9999/LSB1/PTCRL/gollaza`).

| Whale | realized$ | held$(screen) | infl | **clean-hold$** | own WR (n) | avg/ fav85 | last trade | verdict |
|---|---|---|---|---|---|---|---|---|
| **GreatestTrader** | **+141,923** | 155,971 | 0.09 | **+132,582** | 0.66 (238) | 0.62 / 0.32 | 0d | **PROMOTE** (biggest; flag short screen-span) |
| **rollobravado** | +91,547 | 105,848 | 0.14 | **+85,404** | 0.70 (335) | 0.69 / 0.15 | 0d | **PROMOTE** (durable holder) |
| **Kosherlocks** | +76,204 | 89,112 | 0.14 | **+75,739** | 0.60 (190) | 0.56 / 0.05 | 1d | **PROMOTE** (purest copy profile) |
| **olddirtyfighter** | +8,976 | 8,078 | −0.11 | +11,640 | 0.71 (589) | 0.67 / 0.15 | 0d | **PROMOTE** (Tier-2, clean diversifier) |
| **Moond** (cut) | +76,789 | 102,352 | 0.25 | **+65,640** | 0.67 (270) | 0.63 / 0.30 | 0d | **RECONSIDER** (manual pin) |
| **kitten147** (cut) | +129,494 | 19,002 | −5.81 | **+133,135** | 0.75 (306) | 0.69 / 0.48 | 0d | **RECONSIDER-caution** (fav-lean; manual pin) |
| ppxtu | +34,967 | 50,437 | 0.31 | +30,172 | 0.84 (74) | 0.76 / 0.49 | 10.7d | conditional (dormant + fav-lean) |
| sabsabinxz | +67,542 | 66,922 | −0.01 | +2,999 | 0.65 (68) | 0.60 / 0.21 | 5d | reject-copy (edge in exits) |
| viktorurolog16 | +27,192 | 43,102 | 0.37 | **−11,131** | 0.70 (236) | 0.59 / 0.14 | 2d | reject (copy-fragile; = 07-25) |
| Cuco84 | **−6,352** | −3,585 | 0.77 | −7,489 | 0.77 (145) | 0.80 / 0.48 | 1d | reject (neg realized, fav) |
| bordyugaqq | +3,334 | 5,252 | 0.37 | **−28,116** | 0.55 (184) | 0.56 / 0.08 | 0d | reject (clean-hold −$28k) |
| hurrican | +12,875 | 22,386 | 0.42 | **−123,105** | 0.74 (571) | 0.73 / 0.31 | 0d | reject (clean-hold −$123k, all exits) |
| imnice | **−67,041** | 41,625 | 2.61 | −71,457 | 0.76 (115) | 0.69 / 0.51 | 0d | **reject — screen said +$35k, realized −$67k** (papuas-style) |
| Marsache | +3,637 | 12,500 | 0.71 | +2,738 | 0.67 (92) | 0.73 / 0.48 | 0d | weak (small + fav-lean) |

---

## THE THREE LISTS

### 1) KEEP — realized clean-hold edge holding up (ranked)
1. **DegenKingBetter** — +$110k realized, +$64k clean-holds, 0% favorite, sharpest, active. Copy-capturable, best on the roster.
2. **ox1star84** — +$20k realized, +$15.4k clean-holds (77%), WR 0.87, inflation ~0, positive this week. Entry-edge = copy-ideal.
3. **Hakei.** — KEEP on **empirical copied evidence** (our +$211 / WR 0.765 / active daily / +$14.9 last week). ⚠ Flag: realized decomposition shows the edge is exit-driven (clean-holds only +$6.5k) + favorite-lean 0.36. It is now the book's single active carrier — durable enough to keep, thin enough to watch.
4. **CVCM** — cleanest signature (negative inflation, no masking, 338 decisions, both legs positive), active. Small and only 12 copied decisions so far; keep and let the sample build.

### 2) REMOVE — cut, with the numbers
1. **llllllII** — **dormant 9d** (no entries this week; can't copy silence) **AND** own realized decayed to −$36k / clean-holds −$33k. Was ~50% of our copied PnL — now frozen. *(Soft option: demote-to-watch to hedge the esports-schedule gap.)*
2. **potatobrahh** — clean-holds +$1,693 (inflation 0.68, edge in exits copy can't capture); no new entries this week; our +$0.55 over 5 fills. No demonstrated copyable edge.
3. **ChadStarmer** — clean-holds +$204 (~zero), inflation 0.80, quiet 16d. Nothing to capture.

### 3) PROMOTE — positive realized AND positive clean-hold AND not favorite-farmed AND enough n (ranked by durability)
**Tier 1 — genuine clean-holders (currently on the watchlist, no pinning needed):**
1. **rollobravado** — +$91.5k realized, **+$85.4k clean-holds (93%)**, WR 0.70, 335 decisions, avg 0.69 (not fav-farmed), active, +$17.6k own this week. Most durable.
2. **Kosherlocks** — +$76.2k realized, **+$75.7k clean-holds (99%!)**, sharp (avg 0.56 / 5% fav / sub-70 0.76), 190 decisions, active. **Purest copy fit** — edge almost entirely in holds.
3. **GreatestTrader** — **+$142k realized, +$132.6k clean-holds (93%)**, WR 0.66, active, +$52.8k own this week. Biggest edge. ⚠ Screen `window_days_span` was only ~6.9d — its full 238-decision history audits clean, but confirm it isn't a single hot run before sizing up.

**Tier 2 — clean but smaller:**
4. **olddirtyfighter** — +$9.0k realized, **+$11.6k clean-holds** (negative inflation), 589 decisions, clustering 1.32 (cleanest signal), not fav-farmed, active. Low-dollar but the most diversified, durable profile — a good breadth diversifier.

**RECONSIDER — prior cuts whose OWN edge is strong (⚠ OFF the watchlist → require MANUAL PINNING to re-add):**
- **Moond** — +$76.8k realized, **+$65.6k clean-holds (85%)**, WR 0.67, moderate fav-lean 0.30, active. Cleaner than kitten; strong genuine holder that was cut as "probation." Worth pinning back.
- **kitten147** (wallet now labeled *antoinelegwr*) — +$129k realized, +$133k clean-holds, negative inflation, active. ⚠ **favorite-lean 0.48** (borderline fails the "not favorite-farmed" bar) and a large loser-cutting exit book we would NOT replicate → copy-capture is less certain than the headline. Reconsider with caution.

**Do NOT promote (audited, failed):** hurrican (clean −$123k), imnice (realized −$67k, papuas-style), bordyugaqq (clean −$28k), viktorurolog16 (clean −$11k, confirmed copy-fragile), Cuco84 (neg + fav), Marsache (small + fav-lean), sabsabinxz + ppxtu (edge-in-exits / dormant).

---

## WEEK-OVER-WEEK vs 2026-07-25

- **Prior PROMOTE survivors held up on realized basis — all three:** DegenKingBetter +$97.6k→**+$110k** (clean +$57.6k→+$64.1k); ox1star84 +$17.2k→**+$20.1k** (clean +$12.6k→+$15.4k); CVCM +$13.6k→**+$13.7k** (stable, inflation −0.23→−0.21). The 07-25 realized-decomposition calls were correct and you actioned them (all now copied + pinned).
- **KEEP-whale deterioration:** **llllllII deteriorated sharply** (first-ever realized audit: −$36k own / clean −$33k, now dormant 9d). **Hakei** did NOT deteriorate in copied terms (still +$211 / active) but its realized decomposition newly reveals an exit-driven, favorite-leaning edge (clean-holds only +$6.5k). **kitten147** was cut post-07-25, so no longer a KEEP — and ironically its own edge is strong (see reconsider).
- **Prior REMOVE/CUT reconsideration:** **Moond and kitten147 (both cut) now audit as strong genuine holders** (+$65.6k / +$133k clean-holds) — reconsider (manual pin). Other prior REMOVEs are inactive/negligible (only LJa7io23 barely active, 8 fills) — no reconsideration warranted.

---

## CONCENTRATION / EXPOSURE PROFILE

**The roster's realized edge is extremely concentrated — and the concentration just collapsed to a single whale.**

- Our copied book post-epoch = **+$415.46** net. **Hakei (+$210.87) + llllllII (+$201.54) = 99.3%** of it. The other five net ~+$3 combined (CVCM −$3.81).
- **llllllII is now dormant**, so the *forward* copied edge is effectively **Hakei alone** (last-7d roster net +$10.91 was ~entirely Hakei's +$14.9). One active carrier = one point of failure, and its edge is the thin/exit-driven one.
- **Structural mismatch:** the whales with the best *own* realized edge (DegenKing +$110k; promote-candidates rollobravado/Kosherlocks/GreatestTrader at +$76k–$142k) trade **low-frequency / chunky**, so the copy system has sampled them only 8–16 decisions each → they contribute ~$0 to our book so far. The book fills up on **high-frequency** whales (Hakei, llllllII) whose edge is thinner. We are not yet capturing the best available edge.
- **Implication for you:** cutting llllllII without adding frequency leaves Hakei as a solo carrier. Promoting the Tier-1 holders (rollobravado / Kosherlocks / GreatestTrader) diversifies the *quality* of the edge, but because they fill slowly, expect them to contribute gradually — they won't replace llllllII's volume overnight. If book breadth matters near-term, **olddirtyfighter** (589 decisions, high frequency, clean +$11.6k) is the best frequency+quality diversifier in the set.

---

### Provenance
Read-only. Prod DB via `sqlite3 -readonly` (agent_state + polymarket_round_trips); realized via committed `trading_corp.data.polymarket_whale_audit.build_audit_report` over exhausted Polymarket `/activity` + `fetch_market_resolutions`, run with an in-memory harness (no cache, no LLM, no DB/state writes). Transport = `az vm run-command` (read-only, no sudo, no SSH). Raw outputs: `raw_groupA_audit.jsonl`, `raw_ourcopies.txt`, `raw_watch_pool.txt`, `raw_watch_audit.jsonl`. Whales that hit the 5000-fill fetch cap (llllllII, Hakei, hurrican, kitten147, Moond) are audited over their most-recent ~5000 fills = current-edge window. Roster unchanged; all changes are yours to action in the UI.
