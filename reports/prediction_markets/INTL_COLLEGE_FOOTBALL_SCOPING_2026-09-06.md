# International College Football — Non-Kalshi Category Scoping (READ-ONLY)

**Date:** 2026-09-06 ~19:02Z
**Branch:** `pm-intl-cfb-research-2026-09-06` (worktree `cc-pm-cfb-research-wt`)
**Scope:** READ-ONLY research. Nothing deployed/restarted/written. One read-only SQLite
`mode=ro` query against `~/trading_corp/data/prediction_markets.db` (runner
`cc/pm_cfb_probe_ro.{ps1,sh}`). **Zero Polymarket API calls** — no load on the live poller.
**Question:** is INTERNATIONAL COLLEGE FOOTBALL worth commissioning a `/plan` as a
NON-Kalshi-copyable category? (Execution venue / matching / routing is explicitly OUT of scope.)

---

## TL;DR / RECOMMENDATION: **worth a /plan — qualified.**

- **Q1 (does Polymarket carry it):** **YES, decisively, in the US-NCAA reading.** US college
  football is a real, large, currently-active Polymarket family under the clean `cfb-` slug
  prefix — **2,778 positions across 1,630 distinct markets, ~$9.05M cumulative cost-basis, 37
  wallets, Aug-2024→now**, and it is **100% sitting in our `unknown` bucket** (5th-largest token
  in unknown). It is clean single-game moneyline shape (plus spreads/totals/futures). The
  **association-football-at-university reading is a NULL market** — it does not exist on Polymarket.
- **Q2 (tradeable whales):** **A real double-digit population exists, but every performance
  number is a loss-omission upper bound and the DB only sees an incidental subset.** 37 wallets
  bet CFB; **10 clear the platform's ≥50-scoreable bar**; several are active *this* season. But
  the strongest-looking ones (95–100% "win rates") are exactly the loss-omission mirage profile,
  and the true CFB-native whale universe is **not enumerable from our data** (ingest is
  roster-scoped — see caveat B).
- **Why "qualified" and not a clean yes/no:** the *signal side* (Polymarket) unambiguously
  clears the bar. The *whale side* is promising but **unverifiable with the data we have**. The
  cheapest way to get trustworthy whale numbers is to admit `cfb` as a category so the platform's
  own `/activity`-grounded Search/Prospects/Analyze can screen the CFB-native universe honestly —
  which is itself the natural first deliverable of a `/plan`. So: commission the plan, but its
  **first** step is classification + grounded screening, not execution-venue work.

---

## LOAD-BEARING CAVEATS (read before any number below)

**A. LOSS-OMISSION (F-1 bias) — every win% and ROI here is an UPPER BOUND.**
Polymarket's `/closed-positions` feed *systematically drops held-to-worthless losses*
(wallet-dependently), so our ingested `pm_closed_position` inherits the omission: a whale's win
rate reads over-stated and its ROI is an upper bound. The platform's poster child is **SDTrading**
(`0x16bb…8492`): screens **~94% win but drops ~94% of its losses → truly ~coinflip**
(`reports/prediction_markets/MULTICATEGORY_PLAN_2026-09-02.md:270`). The *true* per-whale bias is
**not measurable from this DB** — it requires cross-referencing the `/activity` feed
(`loss_grounding.py`), which this read-only pass did not touch. So: **treat every win% below as
fiction and every cost-ROI as a ceiling.** (Note: the `n_excl` column is the *separate* §3A
`pnl_suspect` quarantine — negRisk phantoms — NOT the F-1 loss omission; it is mostly 0 here.)

**B. INGEST IS ROSTER-SCOPED — the DB answers "do OUR whales also bet CFB", not "who bets CFB".**
`pm_closed_position` only ever contains the histories of wallets we have already added
(seed roster + Search-discovered + attached); we do **not** ingest the Polymarket universe
(`trading_corp/scripts/pm_cli.py:_seed_wallets`, `rosters.load_seed_roster`). So the 37 CFB
wallets below are **by construction a subset of our 102 tracked wallets** — an *incidental* view.
Enumerating the real CFB whale population needs a live leaderboard/Search sweep, which today is
**gated on `cfb` being in `CATEGORY_ALLOWLIST`** (it is not). Everything below is therefore a
**lower bound** on population and volume.

**C. RANKING METRIC.** Per platform ruling, ranking is on **cost-based ROI = net_realized /
SUM(cost_basis)** where `cost_basis = total_bought × avg_price` (`stats.py:112`). Notional ROI
and win rate are **never** ranked (win rate is fiction per caveat A; notional ROI ignores price).

---

## Q1 — DOES POLYMARKET CARRY IT, AND IN WHAT SHAPE?

### The ambiguity, resolved from the data (both readings reported)

Jack's phrase "international college football" is ambiguous. The data resolves it cleanly:

| Reading | Exists on Polymarket? | Evidence |
|---|---|---|
| **US college football (NCAA / CFB)** — distinct from the NFL | **YES — large & active** | 2,778 positions, `cfb-` prefix, US team names (Georgia, USC, Notre Dame, Oregon…) |
| **Association football (soccer) at university level, outside the US** | **NO — null market** | `university`=72 rows (all US-CFB context); `college`=166 (136 CFB, 23 college *basketball*, 5 cs2, 2 soccer); the 2 "soccer+college" hits are **Peruvian pro clubs** whose names contain "College" (`per1-ajp-…` = "ADC Juan Pablo II College"), **not** university soccer. No university/college-soccer family exists. |

**Note on "international":** Polymarket itself is the offshore/"international" venue; the *sport*
is US NCAA college football. There is no separate "international college football" family — the
answerable market is US CFB. I answered the **US-NCAA reading** (the only one with data) and
confirmed the association-football reading is empty.

### Existence, slugs, tags, classification

- **Slug family:** per-game `cfb-<away>-<home>-YYYY-MM-DD` (e.g. `cfb-usc-nd-2025-10-18` "USC vs.
  Notre Dame"); season futures `college-football-champion-YYYY`; a Heisman event. Event tags:
  none map (no `cfb`/`college-football`/`ncaaf` entry in `TAG_SLUG_TO_CATEGORY`).
- **How `derive_category_from_slug` classifies them TODAY:** **`unknown`/`unknown` for
  2,778 / 2,778** — confirmed both from the *stored* category and by *live re-deriving* against
  the box's current `category.py`. `SLUG_PREFIX_MAP` has `cbb` (college **basketball**) but **no**
  college-football prefix; `CATEGORY_ALLOWLIST` (15 cats) omits it. This is **by design and
  already documented** (`EVENTS_TAG_SCHEMA.md:56` explicitly names "CFB games" as correctly landing
  in `unknown`). **CFB is the single largest coherent *sport* sitting unclassified in `unknown`**
  (`cfb` = 2,673, the 5th-largest first-token behind `lol` 7,552 / `btc` 6,578 / `highest` 5,429 /
  `dota2` 3,352).

### Market types — which are clean per-event binaries?

`classify_market_shape` over the CFB slice:

| Shape | Rows | Cost-basis | Copyable? |
|---|---:|---:|---|
| `single_game` | 2,631 | $8.73M (96.5% of $) | **the copyable core** |
| `futures` | 105 | $0.27M | no (season-long: champion, Heisman) |
| `ambiguous` | 42 | $0.05M | n/a |

The `single_game` bucket contains **match-winner moneylines** (dominant; same clean binary shape
as our live MLB/UFC/tennis) **plus spreads** ("Spread: LSU (-9.5)") **and totals** ("O/U 51.5").
The clean per-event binaries a copy platform could act on are the **moneyline match-winners**;
spreads/totals are per-event but a *different market_type* the platform does not currently copy;
futures are out. (A finer moneyline-vs-spread-vs-total split needs the market-level
`sportsMarketType`, which is **absent from `/closed-positions`** and deferred behind the
migration-004 seam per `category.py` — so 96.5%-single-game is a floor, not the moneyline share.)

### Volume / liquidity — real market or novelty?

**Real, mid-pack — not a novelty corner.** Within our *incidental* roster view, CFB's footprint
sits comfortably among the smaller *live* categories:

| Category | Rows | Cost-basis | Wallets |
|---|---:|---:|---:|
| nfl | 3,940 | $22.5M | 48 |
| ufc | 3,701 | $26.7M | 45 |
| cbb (college basketball) | 2,929 | $13.3M | 25 |
| **cfb (unknown bucket)** | **2,778** | **$9.05M** | **37** |
| golf | 718 | $2.4M | 19 |
| fed | 367 | $17.7M | 19 |

Recency: **396 positions resolved in the last 30 days** (the 2026 season opened 2026-08-29/30),
2,524 in the last 365 days. It is **active right now.** And remember (caveat B) this is only what
our tracked whales *incidentally* bet — the true Polymarket CFB market is larger.

---

## Q2 — ARE THERE TRADEABLE WHALES?

**37 distinct wallets bet CFB; all are already-tracked wallets (caveat B). 10 clear the
platform's ≥50-scoreable-in-category bar.** Screening applied per platform standard: ≥50 scoreable
(`pnl_suspect=0`) in-category, recency, ranked on **cost-based ROI**. **Win% and cost-ROI are
UPPER BOUNDS (caveat A).**

### The 10 that clear ≥50 scoreable (ranked by cost-based ROI, caveats inline)

| Wallet | Name | n_sc | win% (fiction) | cost-basis | net (UB) | **cost-ROI (UB)** | last resolved | read |
|---|---|---:|---:|---:|---:|---:|---|---|
| `0xf68a2819…` | — | 257 | **99.2%** | $0.87M | +$749k | 0.860 | 2026-08-30 | 99% win = **loss-omission mirage**; ROI wildly inflated |
| `0x526852797…` | — | 65 | **100.0%** | $0.32M | +$273k | 0.851 | 2026-09-06 | 100% win = mirage |
| `0x6d3c5bd13…` | — | 67 | **100.0%** | $0.09M | +$76k | 0.825 | 2026-09-06 | 100% win = mirage |
| `0x84cfffc3f…` | — | 121 | **95.9%** | $0.10M | +$76k | 0.770 | 2026-09-06 | ~96% win = mirage |
| `0x226bf1220…` | — | 78 | 89.7% | $0.32M | +$155k | **0.488** | 2026-08-30 | **plausible-ish**; still an upper bound |
| `0x2c335066f…` | — | 143 | 65.0% | $1.60M | +$623k | **0.390** | 2026-08-30 | **most credible high-volume**: 65% win is realistic |
| `0xd6966eb1a…` | — (**WE COPY THIS — WTA whale**) | 86 | 89.5% | $0.22M | +$88k | 0.393 | 2026-08-29 | a whale we *already trade* also bets CFB |
| `0xbca08c1bc…` | — | 69 | **100.0%** | $0.12M | +$46k | 0.395 | 2026-09-06 | 100% win = mirage |
| `0xa6a856a8c…` | **BetMechanic** | 1,563 | 55.7% | $3.54M | +$310k | 0.088 | 2026-01-20 | **honest-looking** (55.7% win), huge sample, **thin edge** |
| `0x2fb0f88ef…` | **AIisTheNewWD** | 74 | **97.3%** | $0.72M | +$1.3k | **0.002** | 2025-01-04 | **the caveat made flesh**: 97% "win", ROI ≈ 0 = coinflip; +23 quarantined; stale |

**How to read this table (the honest version):** the four/five wallets showing **95–100% win
rates are the textbook loss-omission profile** — their ROI (0.77–0.86) is fiction. The credible
signal is in the *low*-win-rate rows: **BetMechanic** (55.7%, ROI 0.088 — believable but a thin
edge), **`0x2c33`** (65%, ROI 0.39 on $1.6M — the best-looking real candidate), and the whale we
**already copy** (`0xd6966eb1…`, 89.5%, ROI 0.39). **AIisTheNewWD** is the on-the-nose warning:
97.3% "win" but a cost-ROI of **0.002** — grounding collapses it to a coinflip, exactly as the
loss-omission finding predicts.

### Same wallets we track, or different people?

**Both — structurally, they can only be wallets we already track (caveat B), but they split two
ways:**
- **Cross-sport whales we already track** for *other* categories also bet CFB: **BetMechanic**
  (pinned cs2/epl/nba/nfl/nhl/wnba), **AIisTheNewWD** (epl/fed/golf/nba/nfl/nhl), and the **WTA
  whale we actively copy** (`0xd6966eb1…`). SDTrading also appears but with only 3 CFB positions
  (negligible).
- **Un-named, un-pinned wallets** — the high-volume `0xf68a` (257), `0x2c33` (143), `0x226b`
  (78), `0xbca0` (69), `0x6d3c` (67), `0x5268` (65) carry no `user_name` and no live pins →
  Search-discovered wallets we ingested but never promoted; these are the closest thing we have
  to **CFB "natives."** Whether they are CFB-concentrated vs broad wasn't broken down this pass
  (cheap follow-up).

### Deep enough to matter?

**Yes, within the incidental view — not "three wallets."** 37 bettors, 10 over the bar, several
active this season, spanning both cross-sport trackers and discovered specialists. And this is a
**lower bound** — the roster-scoped DB cannot see CFB-native whales we've never ingested. A proper
population read needs the live grounded sweep (which is the plan's job).

---

## SO — PLAN OR NO PLAN?

**Recommend: commission the /plan, qualified.** This is *not* a stretch-to-look-promising and
*not* a clean no:

- The **market is unambiguously real** (Q1): large, active-now, clean single-game shape, 100% in
  our `unknown` bucket, and completely uncopyable today only because it isn't classified/on a
  venue. On the signal-availability axis it clears the bar comfortably.
- The **whales are promising but unverified** (Q2): a genuine double-digit population with several
  credible low-win-rate candidates — but every performance number is a loss-omission upper bound,
  the flashiest whales are almost certainly mirages, and we can only see an incidental subset.

**The plan's first deliverable should be classification + grounded screening, not execution
venue.** Concretely, the cheapest path to trustworthy whale numbers is to admit `cfb` as a
category (add the `cfb-` prefix to `SLUG_PREFIX_MAP` + `cfb` to `CATEGORY_ALLOWLIST`) so the
existing Search → Prospects → Analyze machinery can run an `/activity`-grounded screen over the
CFB-native universe. Only after that grounded screen says the edge survives loss-omission
correction does the non-Kalshi execution-venue question (out of scope here) become worth spending
on. If the grounded screen collapses the ROIs the way it collapsed AIisTheNewWD and SDTrading,
that is your clean no — reached cheaply, before any venue work.

---

## Method / provenance

- Runner: `cc/pm_cfb_probe_ro.{ps1,sh}` (sanctioned channel; single `sqlite mode=ro` query; no
  writes; no Polymarket API). DB `~/trading_corp/data/prediction_markets.db` @ 2026-09-06 19:02Z.
- DB scope observed: **196,469 closed positions across 102 wallets** (the task's "~121k / 61
  whales / ~22k unknown" was stale; `unknown` is now **41,021** rows). `category_source`:
  slug_prefix 128,456 / gamma_tags 26,992 / unknown 41,021.
- Code read (branch `pm-driver-liveness-2026-09-06`): `category.py` (`derive_category_from_slug`,
  `SLUG_PREFIX_MAP`, `TAG_SLUG_TO_CATEGORY`, `classify_market_shape`), `search.py`
  (`CATEGORY_ALLOWLIST`, `DEFAULT_MIN_RESOLVED_FLOOR=50`, `DEFAULT_RECENCY_DAYS=30`), `stats.py`
  (cost-based ROI, `is_upper_bound`), `farm.py`, `db.py` (`pm_closed_position` schema),
  `loss_grounding.py` / `analyze.py` (F-1 bias), `pm_cli.py` / `rosters.py` (roster-scoped ingest).
- **Not done this pass (deliberate, to keep box load nil):** `/activity` loss-omission grounding
  per whale; per-whale CFB-vs-other-sport concentration; live leaderboard enumeration of
  CFB-native whales. All are the plan's job.
