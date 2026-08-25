# CP3a Contamination Gate - Pre-Build Verification

**Branch:** `prediction-markets-cp3a-2026-08-24`
**Base:** `c122ede` (tip of `prediction-markets-p3-2026-08-24`; confirmed authoritative on origin == local ref == the SHA Jack gave)
**Date:** 2026-08-24

Per Jack's standing rule after the CP3a fabrication episode, every factual claim in the CP3a
design was traced to provenance before any code was written. Claims whose only support was the
three fabricated session reports were re-probed or flagged. This file records what was verified,
with evidence, and what remains unestablished.

## Discipline note on this document
This report states only what was empirically verified or traced to a pre-fabrication artifact.
Where an origin or cause is not established, it says so rather than supplying a plausible story.
(An earlier draft asserted the migration-number error was "laundered from the fabricated reports";
that was an unverified causal claim and has been struck - the same failure mode the gate exists to
catch, one level up.)

## Base tip - confirmed
`git ls-remote origin refs/heads/prediction-markets-p3-2026-08-24` =
`c122ede5b37dbcb2a75645d9a20d9acb6f01795e`, matching the local ref and the SHA Jack provided.
Branched off it; no remembered SHA was used.

## Finding 1 - /activity is alive (first contamination re-verified FALSE)
The design inherited a claim that `/activity` is "404/dead." Re-probed read-only 2026-08-24:
`GET https://data-api.polymarket.com/activity?user=0x71ed0bc9...&limit=5` -> HTTP 200, rows
returned, `types = TRADE,TRADE,REWARD,SPLIT,TRADE`. The endpoint is alive and returns TRADE rows;
the "dead" claim is false (as the handoff already corrected). `/positions` remains the poller
source for the correct reason (Finding 3), not because `/activity` is dead.

## Finding 2 - the paper migration number is 005, not 006 (ORIGIN OF ERROR NOT ESTABLISHED)
The CP3a spec named the paper-table migration "006" with a "schema_version bump to 5." That is
internally impossible in this codebase; the correct number is **005**. Evidence, all at c122ede,
none of it from the fabricated reports:
- `P2_PLAN.md §5.2` = "Migration 005 - paper trading"; §5.3 = "Migration 006 - roster + watchlist
  + search-run." 006 is a different migration.
- `db.py` carries a reserved stub: "migration 005 (paper trading ... reserved here so the
  definition CANNOT drift, e7 ruling)"; its `MIGRATIONS` list ends at `(4, MIGRATION_004)`.
- `names.py`: "migration 005 stays reserved for pm_paper_trade.size_basis."
- This codebase ties migration-number == schema_version (migration 4 -> schema 4). Schema is 4; a
  single migration to schema 5 is migration 005. "006 bumping to 5" cannot both be true.

**Origin of the error is not established.** The fabricated reports, the handoff, and the build
prompt all said "006"; that is a correlation, not a traced chain of custody. The finding stands on
the artifacts above without it.

**Ruling (Jack, C2.1): do not bundle. Two migrations, both land in CP3a:**
- 005 = `pm_paper_trade` + `pm_paper_config`  -> schema_version 5
- 006 = `pm_roster` + `pm_watchlist`           -> schema_version 6

Final schema_version at the CP3a checkpoint = **6**. `pm_search_run` (P2_PLAN's original 006) moves
to its own later migration; P2_PLAN §5.3 is amended in the migration-006 commit, and §5.2 is amended
to record the observation-time entry schema (Finding 3). Any "before the stats table exists"
degradation check binds to the presence of the stats TABLE (`pm_paper_category_stats`), never to a
schema_version integer.

## Finding 3 - /positions genuinely-open filter is essential (unattributed in plans, empirically true)
Neither P1_PLAN nor P2_PLAN characterizes `/positions` beyond "current open positions (live mark)";
the specific claim that it returns resolved-unredeemed rows requiring a filter had no traceable
provenance. Re-probed read-only 2026-08-24, three real G0 wallets from `rosters.py`:

| wallet          | positions returned | resolved-unredeemed (redeemable=true OR curPrice in {0,1}) | genuinely open |
|-----------------|--------------------|-----------------------------------------------------------|----------------|
| 0x43e0f8 (evanng) | 57               | 57                                                        | 0              |
| 0x805618 (csgod)  | 5                | 0                                                         | 5              |
| 0x71ed0b (d1k21)  | 46               | 26                                                        | 20             |

Without the filter, evanng alone would book **57 phantom entries** (all settled bets:
curPrice=0, redeemable=true, currentValue=0). The claim is true; it is not contamination. Fields
confirmed on a live row: `avgPrice, curPrice, redeemable, size, conditionId, outcomeIndex, endDate,
title, slug, eventSlug, outcome, totalBought, realizedPnl, negativeRisk`. `/positions` carries
**no fill timestamp** - confirming entry capture is observation-time, not fill-time (labelled BIAS
in the schema).

**Filter ruling (Jack, D1):** the redeemable<=>curPrice biconditional held on n=3 only; not
load-bearing. **Exclude a row if `redeemable=true` OR `curPrice in {0,1}`.** Over-excluding drops a
genuine open (bias-down, allowed); under-excluding books a phantom (bias-up, forbidden) - the
asymmetry decides it.

`/positions` is preferred over `/activity` because `/activity` has the **5000-row deep-paging
truncation** - which IS validly P1-attributed (2026-08-21 scout session,
`reports/2026-08-21_whale_scouts/CLOSED_POSITIONS_API_FINDINGS.md` @ f140bca) - plus `/positions`
cleanly detects a whale exit (a tracked position vanishing pre-resolution). Not because `/activity`
is dead.

## Open verification carried into the build
**/positions pagination is UNTESTED (D2).** The probe returned 57/5/46 rows - nothing near a
plausible page cap, so it cannot prove no cap exists. `/closed-positions` has a hard limit<=50.
Before the poller is built, pagination is probed empirically; the poller then paginates defensively
(explicit limit + offset until a short page) and records whether it hit a full page (cap signal) on
each poll. Bias-down: assume a cap may exist.

## Rulings integrated (addendum items 1-5 + C/D)
- **Status enum:** `open | pending_adjudication | closed | stale | void`. A tracked position that
  vanishes -> `pending_adjudication` (record `exit_observed_ts`), never straight to stale.
  Adjudication runs off the existing weekly `/closed-positions` refresh: a matching
  `pm_closed_position (wallet, condition_id, outcome_index)` -> `closed`/`resolution`/book
  realized_pnl+won; none by `market_end_date` + grace window (`pm_paper_config`, default 48h) ->
  `stale`/`whale_exit`/excluded from realized stats.
- **Subset assertion (C2.3):** at seed time and at every adjudicator run, assert that pinned-paper
  whales are a SUBSET of the weekly-refresh set; else FAIL LOUD naming the offenders and stop
  (no warn-and-continue). Reported either way. Rationale: a pinned-but-unrefreshed whale would leave
  vanished positions in `pending_adjudication` forever - silent limbo that reads as healthy.
- **Idempotency / keys (addendum 2):** idempotency key `(wallet, condition_id, outcome_index)` +
  NULL-safe open guard. `entry_observed_ts` is the only entry-time column (no `entry_ts` alias).
  PK `(wallet, condition_id, outcome_index, entry_observed_ts)`. Supersedes P2_PLAN §5.2's
  `entry_ts`-from-/activity schema (amended in the migration-005 commit).
- **Scale-ins (addendum 3):** a size increase on a tracked open row is not a new entry; record
  `n_observed_adds` + `last_add_observed_ts`. A size decrease is a partial exit; record
  `n_observed_reductions` + `last_reduction_observed_ts`. Both diagnostic-only, never feed realized
  stats. Only full disappearance triggers `pending_adjudication`.
- **Roster seeding (C2.4):** seed `(wallet, category)` from SCOUT PROVENANCE (the recorded reason a
  whale was pinned), never from `pm_category_stats` (cross-category by construction -> phantom pairs
  the poller would trade). Unresolvable category -> list + halt on that whale. `pm_category_stats`
  may validate a pair, never generate one. The full seeded `(wallet, category, pinned)` table is
  reported for Jack's eyeball before the poller's first run; actual pinned count/composition is
  reported from the box (14-vs-10 is unresolved until looked at).
- **Live division language (addendum 4):** `poly_kalshi_mlb` is described as **ARMED AND UNPROVEN on
  the order path** - never "can place real orders." No order has fired since the 08-24 restart; both
  hypotheses (stale key reloaded / order-path defect) predict silence, so order capability is
  inferred, not demonstrated.
- **Same-poll BIAS-UP (addendum 5):** asserted, not measured. Carried as an available cross-check
  (the legacy `/activity` 7s poll on an overlapping whale set makes the 5-min miss rate measurable
  against a live feed). Not built in CP3a.

## Scope
CP3a = migrations 005 + 006, the `/positions` poller, the adjudicator, and roster seeding, with
honest degradation when the CP3b paper stats table is absent. NOT in CP3a: `pm_paper_category_stats`
+ `paper_rollup` (CP3b), the farm UI (CP3b), pin lifecycle + Analyze (CP3c), any cron, any deploy,
any `main` merge.

## ADDENDUM 2026-08-25 — advisor ruling C2.4 REVERSED by Jack (recorded, not softened)

**C2.4 was wrong and has been reversed.** The advisor ruling C2.4 (this report's original position: seed
`(wallet, category)` only from curated scout provenance, never from `pm_category_stats`, and HALT on any
unresolved pin) was made **without having read `P2_PLAN.md`**, **conflicted with P2_PLAN Ruling B** (which
seeds the roster from `pm_category_stats`), and was **partly justified by an example the advisor invented
rather than sourced**. Jack looked at the live `/scoreboard` and ruled.

**The ruling (Jack, 2026-08-25):** the watchlist (paper-traded set) is **EVERY `(wallet, category)` pair in
`pm_category_stats` for the migrated legacy whales** — every combo, not a curated 14. **No minimum-resolved
floor** (n=3 is a watchlist pair; curation is the board's job at pin time). **`'unknown'`-category pairs
stay** and paper-trade like any other — for the record: `'unknown'` is a tier-1 slug-derivation failure, not
a real category, and `kutsumiakia/unknown` (n=1429) is the largest single pair on the board, so this is not
a corner case (recording it, not acting on it). **All categories are live for paper** (MLB/UFC/NBA/Fed do
not restrict the farm; cs2/ucl/soccer/epl/nhl/fifwc/nfl/unknown all paper-trade). Nothing reaches real money
without a P3 account-category attachment, so paper breadth costs nothing.

**What changed in code (this branch):** `seed_farm_roster` now sources pairs from `pm_category_stats` for the
migrated whale set (`wallets=` from `rosters.load_seed_roster`); `load_pin_provenance` and the
halt-on-unresolved path are removed from the seed; `validate_pairs_have_history` was repurposed to
`seeded_pairs_table` (the eyeball reporter). `config/pm_farm_pin_provenance.yaml` STAYS on disk as the
historical scout attribution but nothing reads it at seed time. P2_PLAN §5.3 amended in the same commit.

**Why this is recorded and not quietly dropped:** a wrong ruling that disappears is how the next one gets
made. The failure mode here is the same one this gate exists to catch — a claim (C2.4's invented example)
stated as if sourced. It is left in the record so the next agent sees it was reversed and why.

## VOCABULARY COLLISION — flag for CP3b (do NOT rename in this pass)

Three parties use "watchlist" for two different objects. The mapping, so CP3b does not inherit the confusion:
- **Jack's FARM LEAGUE** = the dynamic, weekly-scanned candidate pool. **NOT paper-traded.** The greyed-out
  nav tab, still to be built (CP3b search).
- **Jack's WATCHLIST** = the permanent, board-locked list that **paper-trades** (what CP3a seeds).
- **Shipped code (`pm_watchlist.status`):** `status='watchlist'` == Jack's FARM LEAGUE (candidate, not paper);
  `status='pinned'` == Jack's WATCHLIST (paper-trading). CP3a seeds every migrated pair as `status='pinned'`.

Do NOT rename anything during a reseed (two changes at once); this note is the durable mapping for CP3b.
