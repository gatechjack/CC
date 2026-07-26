# kalshi_copy_trading — Whale Roster Performance Review

**Date:** 2026-07-26 (prod 23:31 UTC) · **Scope:** read-only; no code/config/roster changes. Live window = 2026-07-01 go-live → present.
**Headline:** The live sample is **far too thin for roster surgery** (15 resolved round-trips total across 3 whales, none ≥30; division dormant since 07-19). Both instrumentation gaps flagged 07-20 are **still broken on live** (whale_handle absent from round-trips; copyability frozen at go-live), so dashboard per-whale live metrics are unreliable — this review reconstructs them from the audit layer. **Recommendation: HOLD AND ACCUMULATE + fix S2 instrumentation before any cut/promote.**

---

## STEP 0 — Metric integrity (GATES everything) — BOTH STILL BROKEN on live

| check | status | detail |
|---|---|---|
| `whale_handle` on live `kalshi_round_trips` | **STILL ABSENT** | **0 / 15** live rows carry structured `whale_handle` in extra_json; the handle exists only in free-text `rationale` (`"copy entry: @AI.EDGE opened …"`) |
| Autopause keying | **NO-OP on Kalshi live** | autopause matches on structured `whale_handle` in round-trips → 0 live matches (confirmed: **0** kalshi `would_auto_pause` since shadow deploy) |
| Copyability metric | **STILL BROKEN** | `would_have_placed` (dashboard numerator) **froze at 2026-07-01T20:41** (paper-only); live copies register as `kalshi_copy_placed_live` — dashboard copyability is paper-only |

**Workaround used in this review:** the `kalshi_copy_placed_live` **audit payload** *does* carry structured `whale_handle`, `fee`, `order_id`, `fill_price/qty` — so I reconstructed per-whale live copies, fees, and copyability from audit, and per-whale P&L by parsing the round-trip rationale. **These are reconstructions, not the operator's dashboard** — the dashboard remains unreliable for live per-whale metrics until S2 lands.

**What this means for the review:** per-whale *live* copyability and P&L ARE reportable (reconstructed below), but (a) they don't match what the dashboard shows, (b) autopause is blind on Kalshi live, and (c) every number is at trivially small n. **No roster recommendation can rest on live per-whale metrics until instrumentation is fixed AND a larger sample accrues.**

---

## STEP 1 — Division health

- **Service:** `active/running`, **NRestarts=0**, PID 404132, last start Sat 2026-07-25 00:56 UTC (= the planned PMCC-bundle deploy). No crash-restarts.
- **Copy division:** **0** error/traceback audit kinds; **0** `kalshi_copy_feed_anomaly` since 07-21 (last ever = 07-01 go-live day, 72×). No circuit-breaker fires. Feed healthy.
- **⚠️ Dormant:** since 07-21 the copy trader has only done watch-list refreshes + **4** `no_side` skips; **zero placements since 2026-07-19**. Likely event-supply driven — 10/15 live copies were **World Cup** markets (KXWC*/KXMENWORLDCUP soccer), which concluded ~mid-July. Recent detections are being skipped (`no_side` / `side_detection_low_confidence`, e.g. AI.EDGE KXSPACEXCOUNT 07-26).
- **⚠️ Engine-wide (NOT copy-division) log defect:** **1,721× `TypeError: not all arguments converted during string formatting`** in the journal since 07-21 (~14/hr) — a `%`-formatting bug in a logging call somewhere in the engine; **0 are copy-related**, non-fatal (NR=0). Plus 18 Robinhood auth blips (`load_portfolio_profile … not logged in`, 429/Unauthorized — PMCC/PEAD, not copy). **Flag for separate attention; does not affect this division.**

---

## STEP 2 — Autopause shadow observation (5 days)

- kalshi `would_auto_pause` since the 07-21 shadow deploy: **ZERO.** Only `polymarket_copy_trader` fired (6,944 events; superbeter007 etc.).
- **This is the structural NO-OP, not a clean bill of health** — the Kalshi autopause query keys on `whale_handle` which is absent from live round-trips (STEP 0), so it evaluates **no** Kalshi whale. It has flagged nothing because it *can* flag nothing.
- **Zero real auto-pauses** (correct — shadow mode; roster unchanged at {MaggieTheEagle, AI.EDGE}).
- **Cannot conclude any Kalshi whale is safe or unsafe from autopause** until the S2 recorder restores structured `whale_handle`.

---

## STEP 3 — Selected roster (reconstructed from audit; **all n ≪ 30**)

Selected = `{MaggieTheEagle, AI.EDGE}`. Net-of-fee uses the per-order Kalshi fee (`ceil(0.07·C·P·(1−P))`) as stored in the placed_live audit.

| whale | copies (placed) | detections | copyability | resolved RT (n) | gross P&L | fees | **net P&L** | WR | last copy | category mix | **REC** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **AI.EDGE** | 12 | 60 | 20% | 11 | −$2.42 | ~$0.66 | **−$3.08** | 36% (4/11) | 07-19 | World Cup soccer (7), KXMVE parlay (2), politics (1), cricket-adj | **WATCH** |
| **MaggieTheEagle** | 3 | 4 | 75% | 3 | −$3.54 | $0.13 | **−$3.67** | 33% (1/3) | 07-18 | 100% World Cup advance (soccer) | **WATCH** |
| *pritz786 (rotated off)* | 6 | 11 | 55% | 1 | +$0.06 | ~$0.05 | +$0.01 | 100% (1/1) | 07-01 | cricket T20 | n/a (not selected; go-live only) |

- **Both = WATCH** (rule: n<30 → insufficient sample). Neither triggers **DEMOTE** (needs net-negative at n≥30 — both are n≤11 — or copyability <5% at detections>20 — both are ≥20%). Neither is structurally uncopyable.
- **Not INVESTIGATE, but noted:** AI.EDGE's P&L is *carried by one trade* (+$4.08 on KXWCMENTION-PENA NO); excluding it, AI.EDGE is ~−$6.5 over 10. At n=11 this is noise, not signal — flagging only for transparency.
- **Not "can't copy":** copyability 20–75% (all ≥5%). The issue is mild net-negativity at trivial n, over a now-concluded event (World Cup) — **not representative of forward quality.**

---

## STEP 4 — Watch bench (9 whales) — cannot assess by PROMOTE criteria

Watch-only handles: NovaRex, YoDog, teafordong, juggling.bags, aenews, ml123, decimal.beluga2440, c.f.frls, BitcoinTradingChallenge.

**We have ZERO copy history for any of them** (watch-only = not copied) → **our-copy P&L, copyability, and "would produce copyable trades" are all unmeasurable.** The operator's PROMOTE criterion (net-positive at n≥30 of *our* copies AND copyability ≥5%) **cannot be satisfied for any watch whale** — it requires copying them first.

Only **external leaderboard** stats exist (the whale's *own* public Kalshi performance, cached ~20 closed positions):

| whale | ext. resolved | ext. WR | ext. total P&L | top cats | note |
|---|---|---|---|---|---|
| NovaRex | 20 | 90% (18/2) | $92,143 | Sports/Entertainment | rank #15 Politics/mo |
| YoDog | 20 | 95% (19/1) | $4,617 | Entertainment/Mentions | rank #17 |
| ml123 | 0 | — | $0 | Sports/Entertainment | no closed-position data |
| *(others truncated)* | | | | | deep-scan sourced |

- **External whale success ≠ copyable edge.** Our *selected* whales are net-negative in copy despite presumably strong profiles when selected; external stats are a small cached window (n=20) with survivorship. **No watch whale is a confident PROMOTE.**
- **Cannot validly compare bench vs weakest Selected** — bench has external stats only, Selected has our-copy stats only (apples-to-oranges). If the operator wants to *start accumulating* a copy sample on the strongest external profiles, NovaRex/YoDog stand out — but that's an "accumulate to test," not a data-backed promotion.

---

## STEP 5 — Roster-level synthesis

**Sample-size honesty (the binding constraint):** live = **15 resolved round-trips total**, across AI.EDGE (11), MaggieTheEagle (3), pritz786 (1), all 07-01→07-19, then **dormant a week**. This is an order of magnitude below the n≥30/whale bar, and concentrated in a **concluded event (World Cup)** — so it says almost nothing about forward whale quality.

- **Demote: none.** No whale is net-negative at n≥30 (max n=11); none is structurally uncopyable (copyability 20–75%).
- **Promote: none.** No watch whale has copy history to assess; external profiles ≠ copyable edge.
- **Can't-copy vs loses-money:** neither failure mode is present at signal strength. Copyability is healthy; net-negativity is trivial-n noise. Do **not** conflate the two here.
- **The real problems are instrumentation + dormancy, not whales:**
  1. S2 still unshipped → whale_handle absent from live round-trips (autopause blind on Kalshi; dashboard copyability paper-only). **Fix before any roster surgery** so decisions rest on the operator's dashboard, not audit reconstructions.
  2. Division has placed **nothing in a week** and only **21 lifetime live copies** — at this rate, reaching n≥30/whale is *months* away. Worth diagnosing separately whether recent `no_side`/`low_confidence` skips are starving copy supply, or it's just post-World-Cup event drought.

**Recommendation: HOLD the roster as-is and ACCUMULATE. No cuts, no promotions this cycle.** Revisit after (a) S2 instrumentation is fixed and (b) a broader, multi-event live sample accrues (target n≥30/whale). Flag the 1,721× engine-wide `TypeError` log defect and the copy-supply dormancy as separate follow-ups.

---

### Caveats
- **All live per-whale metrics are audit-reconstructed** (dashboard is broken on live per STEP 0) — treat as directional, not authoritative.
- **n is tiny** (11/3/1) and event-concentrated (World Cup) — not forward-representative.
- Round-trips store **gross** P&L; net uses per-order fees from the placed_live audit (per-order model, not per-contract).
- **21 placed_live vs 15 resolved round-trips** — 6 placements (5 pritz786, 1 other) did not resolve into round-trips (unfilled/void/still-open); P&L reflects the 15 resolved only.
- Autopause is a Kalshi **no-op** — its silence is not evidence about whale health.
