# The 'unknown' paper-trade concentration — FINDING (2026-08-27, read-only)

**Question (Jack):** 94 of 102 paper rows sit in the 22 deactivated pairs, all `unknown` (92%). Not plausibly
random. Is `unknown` a genuine permanent structural failure, or a derivation bug losing us the most active
markets? **This is a finding, not a work item — no fix opened.**

> **★ CORRECTION FOR THE RECORD (Jack, 2026-08-27) — the alarming version is WRONG; do not inherit it.**
> The 92% is **NOT a derivation failure concentrated on the most active markets.** All 102 rows are a single
> Aug-25 poll snapshot that over-sampled long-dated futures + novelty; **77 of 94 are `will-…` markets that
> genuinely are not game-line copyable.** The funnel has real volume — **253 resolved trackable trades in 7d,
> 939 in 30d, across 38 of 92 active pairs.** The only real (smaller) issue is the poller's tier-1-only
> categorization losing some trackable soccer/tennis — a separate, bounded gap, not "we're blind to the whales'
> activity." (My earlier read was the alarming version; Jack's — this — is correct.)

## The single biggest fact: all 102 paper rows are ONE poll snapshot
Every paper row (both the 94 unknown and the 8 eligible) has `entry_observed_ts = 2026-08-25 15:48` — **the CP3a
gate-3 one-shot poll.** So the 92% is the composition of the whales' OPEN `/positions` at *one instant*, NOT an
accumulated trend. That matters for interpretation (below).

## (a) What the 94 are — mostly long-dated FUTURES / NOVELTY, not game lines
Real examples from the 94: `will-reza-pahlavi-lead-iran-in-2026`, `2026-f1-drivers-champion` (F1 title),
`will-novak-djokovic-win-the-2026-mens-us-open` (tennis *futures*), `will-the-iranian-regime-fall...`,
`will-hype-flip-sol-by-december-31` (crypto), `democratic-presidential-nominee-2028`,
`cuban-regime-falls-in-2026`, plus 2 EFL soccer (`efl-wat-pet-2026-08-25`) and 1 LoL esports.
**Slug first-token: `will-` 77 / 94**, then singletons (efl×2, lol, bitcoin, trump, zelenskyy, cycling…).
Their `market_end_date`s are 2026-12-31 / 2027 / 2028 — **long-dated**.
**The 8 eligible are clean single-game/decision lines:** mlb (`mlb-sf-atl-…`), ufc (`ufc-ronhum-alemir-…`),
fed (`fed-rate-hike-…`) — slugs whose prefix tier-1 matches.

## (b) NOT a "new markets fail derivation" recency effect — a SNAPSHOT-COMPOSITION effect
Hypothesis tested, **rejected**: the 94 are not newer than the 8 (identical poll ts). The real mechanism: a
single-snapshot poll over-samples the **long-dated futures a whale HOLDS for months** (open at any instant) and
under-samples **fast-resolving game lines** (an mlb/ufc game opens and settles same-day, so it's rarely open at
a random poll instant). So the whales' open book at 15:48 was dominated by futures/props → `unknown`.

## (c) Concentrated in a few whales
The 94 span **5 wallets**, heavily 2: `0x6dd6314d…` (50) + `0x71edffd0…` (31) = 81 of 94; then Kickstand7 (10),
evanng (2), `0xc3e550…` (1).

## (d) The active funnel has REAL volume — not a trickle (IF the poller runs continuously)
Recent resolved whale activity in the 15 tracked categories, for the 92 active pinned pairs:
- **last 7d: 253 resolved rows across 27 of 92 pairs**
- last 14d: 512 across 35 pairs
- **last 30d: 939 resolved rows across 38 of 92 pairs**
So the whales actively trade the tracked game-line categories (~36 resolved/day). The current "8 eligible, all
open" is a **single-snapshot artifact**, not the steady state. A continuous poller (`*/30`) would capture these
game-line ENTRIES as they open (before same-day resolution) → meaningful paper volume. **This is the evidence
the cadence decision was waiting on: the funnel is not empty.**

## (e) MY READ: `unknown` is MOSTLY structurally-correct, with a REAL but smaller poller derivation GAP
Two distinct things are conflated in the paper-lane `unknown`:
1. **The bulk (the `will-…` futures / politics / novelty / crypto / championship-futures) are genuinely NOT
   game-line copyable markets.** tier-2 gamma ALSO returns `unknown` for 13/15 sampled (F1, tennis-futures,
   regime/politics, crypto). The platform's tile set is single-game/decision lines (§F-2) — these aren't it.
   Calling them `unknown` is essentially CORRECT. The earlier "structural" ruling holds for this majority.
2. **BUT a smaller slice IS a fixable poller gap.** The **poller (`poll_pinned`) categorizes tier-1 ONLY**
   (`derive_category_from_slug`, paper.py:41) — it does NOT run the tier-2 gamma fallback that `ingest` does.
   The 2 EFL soccer markets re-derive to **`soccer` via tier-2 gamma** but land `unknown` in the paper lane
   purely because the poller never asked gamma. `efl-` is a real soccer prefix missing from `SLUG_PREFIX_MAP`,
   and tennis-*futures* slugs (`2026-mens-us-open-winner-tennis`) carry the category as a *suffix* tier-1 can't
   match. So the poller loses a real (if minority) set of trackable soccer/tennis markets.

**Cross-check:** of the 90 distinct 94-cids, only 10 appear in `pm_closed_position` at all; ingest categorized
those 9 `unknown` + 1 `soccer` — consistent with "mostly genuinely-untrackable + a thin trackable slice."

**Verdict:** the earlier "`unknown` = permanent structural failure" ruling is **broadly right for the majority**
but **slightly overstated** — the paper-lane `unknown` also hides a **poller/ingest categorization
INCONSISTENCY** (poller tier-1-only vs ingest tier-1+tier-2) that loses genuinely-trackable soccer/tennis. That
inconsistency is a **real ticket** (not opened here): either give the poller the tier-2 fallback, or add the
missing tier-1 prefixes (`efl`, tennis-futures shapes). It does NOT change Stage 1's correctness — R1 correctly
excludes all `unknown` today — but it's why the poller's paper volume is thinner than the whales' true
game-line activity, and it should be weighed before the poller cadence is set.

## THE POLLER-GAP LOSS SIZE (measured 2026-08-27) — for the cadence decision
Of the trackable recent activity in the 92 active pairs, how much would the **tier-1-only poller MISS** (re-derive
tier-1 on each row's slug → `unknown`)?
- **7d: 16 of 253 (6.3%)** — soccer 9, tennis 7.
- **30d: 80 of 939 (8.5%)** — tennis 38, soccer 37, ufc 4, golf 1.
- It would **CATCH ~92%** (237/7d, 859/30d — the slug-prefix categories). `category_source` agrees: only 16/253
  and 80/939 rows are `gamma_tags`.
**So under "start polling now, tier-1-only" (path b), the loss is ~6–8.5%, bounded to soccer + tennis** (plus a
handful of ufc/golf edge cases). Not a blind spot on the whales' activity — a modest, well-scoped leak until the
poller gets tier-2 (or the missing tier-1 prefixes are added).

## Is the tier-2 gap a Stage-1 item or a separate ticket? — SEPARATE TICKET (agree with Jack)
The gap is in `poll_pinned`'s categorization (tier-1 only, no gamma fallback) — a **pre-existing poller behavior
Stage 1 did not touch**. Stage 1's deliverables (gamma-adjudicator re-base, `pm_paper_category_stats` rollup, the
pinned→paper wiring) are correct and orthogonal to it; R1 correctly excludes `unknown` regardless. Closing the gap
is a self-contained ingest/poller change (give the poller the tier-2 fallback, or add `soccer`/tennis-futures
prefixes) — **its own ticket, not part of the paper-lane close-out.** It does not block Stage 1's value; it caps
paper volume ~6–8.5% below the whales' true game-line rate until fixed.

**Read-only. No fix opened. Engine/PM DB untouched.**
