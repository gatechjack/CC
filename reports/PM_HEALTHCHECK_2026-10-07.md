# PM DAY HEALTH CHECK -- 2026-10-07 (full trading day, 04:18Z arm -> 2026-10-08 03:08Z). READ-ONLY.

VERDICT: **YES, today went well.** All four accounts traded the full day (not just the arm cycle); 96 entry
fills across 9 categories; leg-audit 96/96 CLEAN (0 inversion/soft/uneval -> no fire-first, no wrong leg);
reconcile 0 DIFFS on all four; 0 scalar settlements (the defect did not re-trigger); the 23:30 restart boot-
reconciled CLEAN (Phase-2 fix proven under a restart); 0 funding/cap/disarm rejects; net realized P&L -$2.61.
Caveats (none blocking): marc/trey shard-thin (fund proactively), the Polymarket 429 storm continues (~7.5k/hr,
not worse, not degrading), pm_open_position ingest still stale (farm-only, live copy unaffected).

## 1 -- DID IT TRADE (full day since 04:18Z)
Entry fills (is_exit=0, dry_run=0), per account:  jack 24 filled / 5 no_fill ; karen 21 / 5 (+1 error);
marc 13 / 6 (+1 error); trey 17 / 3.  Contracts: jack 200, karen 115, marc 29, trey 43.
Categories traded: boxing, bun, cfb, cs2, itf, lal, mlb, nfl, wnba. Fills-per-hour were event-driven and held
across the day (04Z backlog burst 29, then steady 1-11/hr through 01Z) -- NOT a first-cycle-then-decay shape.
Per-cycle gate behavior (driver cycle summaries): n_reject=0, n_shard_underfunded=0, n_disarm_blocked=0 on every
account; errors=3 all day (marc/itf, karen/itf, jack/cs2 -- post-match 404 market_not_found, benign). The large
n_skip (gate-3 strike/depth + idempotency) is the normal filter; everything that would-place DID place.
ARMED-BUT-SILENT: 28 of 57 placed nothing -- atp(x4), ufc(x4), mex(x4), f1, bra/epl/mls/ucl/wta (jack+karen),
nfl(marc,trey). All NO-SIGNAL (0 rejects/errors for them): those sports had no events / whales opened nothing
copyable overnight. Not a gate.

## 2 -- FIRST FILLS IN NEVER/RARELY-TRADED CATEGORIES (the day's real risk) -- ALL CLEAN
leg-audit verdict distribution, ALL 96 is_exit=0 rows today: {clean: 96}. ZERO inversion / soft / uneval.
No REVIEW strip entry, no PM leg-audit WARNING. Fire-first NOT triggered.
First fill per (account,category), audit result:
  - *** boxing jack -- FIRST EVER: KXBOXING-26OCT08KHATAEMCCR yes, whale 'Imam Khataev', audit=ok (code KHATAE
    subsequences Khataev -- the independent check the family supports, passed). Matcher routes correctly.
  - bun jack/karen -- KXBUNDESLIGAGAME-26OCT09BV yes, whale 'Yes', audit=ok (Yes/No market).
  - cs2 -- FURIA / PARIVISION (jack 09:15, others 09:58), audit=ok (yesterday's TS=Spirit false-flag did NOT
    recur; today's cs2 subsequence checks passed).
  - itf -- Alkaya / Zhao / Sinclair / Bax, audit=ok (name subsequence passed).
  - lal -- Yes/No, audit=ok. mlb/nfl/cfb/wnba -- audit=na (structural, code-anchored -- correct by design).
No `na` from a family that should have produced a verdict; every name-family fill got an `ok` (the subsequence IS
the independent verification). Guards that fired (bias-down, working): OPPOSING-PAIR 276 (both-sides contested ->
skipped, 0 wrong positions taken), whale-EXIT 74. No abbrev_collision / winner_outcome_unresolved /
driver_ambiguous.

## 3 -- NO NEW MISMATCH ACCUMULATING; RESTART SURVIVED
RECONCILE (RO compare, no latch), per account: DIFFS=0 -- jack 13/13, karen 12/12, marc 5/5, trey 5/5 (journal
open tickers == venue position_fp exactly). The journal is in sync with the venue; nothing waiting for the next
restart. Venue settlements since arm: jack{yes7,no2} karen{yes8} marc{yes7} trey{yes9,no1} -- ALL in {yes,no},
ZERO scalar/void/refund today -> nothing book_settlements would skip. RESTART SURVIVAL: the one engine restart
during the trading day (2026-10-07 23:30:42Z, PID 664274) boot-reconciled reconciled=True latched=False
latched_categories=() on ALL FOUR accounts -> the 57 stayed armed, fills continued. The Phase-2 scalar booking is
proven under the exact condition (a restart) that created the outage. (Jack's "twice": the 10-06 02:56 restart
was the latching one; the 10-07 23:30 restart was clean.)

## 4 -- THE TWO THINGS THAT GOT WORSE BY DESIGN
FUNDING (gate 6b): **did NOT bind today -- n_shard_underfunded=0 on every account/cycle.** Placed comparison
jack 24 / karen 21 / marc 13 / trey 17 (filled). The marc/trey gap is CONFIG (fewer armed categories: 11/12 vs
jack 18 / karen 16; smaller contracts) + category coverage, NOT funding -- they had headroom (used ~$20 of ~$80).
Shards now jack $465 / karen $448 / marc $80.27 / trey $80.49 (marc/trey replenished slightly from settlements).
Still thin for future volume -> fund proactively, but it did not cost a fill today.
POLYMARKET 429: today 172,651 (since 04:18Z), ~7.5k/hr avg (peaks 14-16k/hr overnight 00-02Z, lows ~2k midday).
vs yesterday ~9k/hr / 491k cumulative -> rate COMPARABLE, not materially worse despite 13 more armed scopes. No
evidence it degraded detection or copy latency today: fills flowed normally all day, within ~1min of arm. Load/
noise concern (carry-forward), not a functional break.

## 5 -- LIVENESS, CAPACITY, P&L
LIVENESS: per-account shard-snapshot cadence (union-across-categories) steady ~5.0min all day, 273 snaps/account,
max gap 5.0min uniform -- NO stall (the distribution is uniform at the ~300s cycle, not bimodal). [NB my >=200s
flag counts every normal 300s cycle, so "272 gaps>=200s" is the cadence, not 272 trading gaps.]
CAPACITY: orders today jack 29 / karen 27 / marc 20 / trey 20 vs the 50/day account ceiling -- not binding; no
(account,category) over 40. Open-at-cost jack $40.75 / karen $25.61 / marc $3.21 / trey $3.21 vs $350 caps --
far from binding.
P&L today (settlement closes since arm, realized net of fees): jack -$11.6361 (7 closes), karen +$5.2533 (7),
marc +$1.5565 (6), trey +$2.2133 (10) -> **net -$2.6130**. Plus opposed/whale-exit closes (realized NULL by
design: 5 opposed + ~8 whale-exit) and open-at-cost still live. jack's -$11.64 is ordinary trading variance on
tiny stakes (positions that settled against the copied side), not a defect.

## WRONG-BUT-UNRELATED (one line each, not pursued)
- pm settlement PROCEEDS cross-check WARN x22 (e.g. "proceeds=5.0000 vs kalshi revenue=500.0000") -- the KNOWN
  revenue-is-CENTS units bug; settlements booked correctly, the WARN is cosmetic. Fix with the settlement-close path.
- pm_open_position ingest stale since 2026-09-28 (225h) -- Prospects/farm numbers stale; live copy unaffected.
- bitunix_sfp_research_log "can't subtract offset-naive and offset-aware datetimes" -- a bitunix-division bug.
- PMCC "PREVIEW ABORTED sparse_chain_no_weekly" (MSTR/BLSH) + MACE XLE mace_pt_mark_reject leg_inversion -- other
  divisions, both known guards working; not PM.

CARRY FORWARD unchanged from PM_RECOVERY_2026-10-06.md (settlement-close path #1; preserve prior-armed #2;
account-wide latch scope #3; close_source rollup filters #4; fund marc+trey #5; 429 storm #6; pm_open_position
#7; NULL caps jack boxing/f1 #8; 33 disarmed subs #9).
Runners (RO): pm_hc1..5. Evidence branch pm-zerocopy-diag-2026-10-06.
