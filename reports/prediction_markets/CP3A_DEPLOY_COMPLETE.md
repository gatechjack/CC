# CP3a DEPLOY — CLOSE-OUT

**Deployed code:** `b593b39` (branch `prediction-markets-cp3a-2026-08-24`).
**prod-live ledger entry:** `2fc9173b6238da350dcac120a095eab0ca917be0` — `deploy(pm-cp3a): record CP3a
paper-farm artifacts on prod-live (== box)`, atop `435db7f`. Records **db.py + paper.py + pm_cli.py**,
byte-identical to the box (sha256 re-hashed on the box at commit time: db.py `85de7572`, paper.py `f27016`,
pm_cli.py `62238b` — all MATCH b593b39). Excludes `config/pm_farm_pin_provenance.yaml` (repo-only, not read
at runtime). `main` UNTOUCHED. Every number below is read back from the box, not asserted from a plan.

## The three gates — actual numbers

- **Gate 1 (migrations 005/006) — 2026-08-25 14:06Z.** live PM DB schema **4 → 6**; `pm_closed_position`
  **29,709 rows intact**; 4 new tables created — `pm_paper_trade`/`pm_roster`/`pm_watchlist` = 0,
  `pm_paper_config` = 3 (config defaults 300/172800/100). Engine PID 969439 unchanged, legacy mtime unchanged.
- **Gate 2 (roster seed) — 2026-08-25 15:25Z** (Option-A in-place pm_cli.py overwrite + seed-only resume after
  a run-1 arg-order abort). `pm_roster` = `pm_watchlist` = **114 pairs**, **14 wallets**, **all `status='pinned'`**;
  `pm_paper_trade` still 0; closed 29,709; schema 6. Pair-identity vs the approved box-scratch preview: matched
  by determinism + sampling (frozen `pm_category_stats`; 12 distinguishing non-round counts exact) — **not** a
  literal set-diff. pm_cli.py stayed `azureuser:644`, scripts/ dir stayed `197609:197121` (in-place file write).
- **Gate 3 (poller one-shot) — 2026-08-25 15:48Z.** poller EXIT 0, wall-clock **3s**, **14 HTTP GETs** (one
  un-paginated `/positions` per pinned wallet). **917 positions returned, 815 excluded (88.9%), 102 booked**
  (all `status='open'`, all within the 114 — NON-114 check NONE). Engine/legacy/roster/watchlist/closed/schema
  all unchanged.

## F / G / H — first live measurement

- **F `n_skipped_category` = 0.** No genuinely-open position derived to a non-pinned category (expected under
  the reversed seed — every category a migrated whale trades is pinned).
- **G `last_polled_ts`: 114/114 pairs polled, 0 NULL, 0 fetch errors.** Every whale fetched cleanly.
- **H: `/positions` caps at 100 — MEASURED, six whales returned exactly 100** (SDTrading, 4751346, pako,
  MadeiraIsland, BetMechanic, kutsumiakia). The amendment-H round-number signature fired as designed.

## The evanng correction (stated plainly)

The Gate-3 acceptance criterion "evanng must book ZERO or the filter is broken" was **wrong**, and the error is
recorded as ours. "Zero" came from the 2026-08-24 probe (57 returned / 57 resolved-unredeemed / 0 genuinely
open) — a **timestamped measurement of a whale that happened to have no live bets that day**, mistakenly written
into a pass/fail gate as if it were a property of the whale. It was also backwards as a test: had evanng booked
zero, that would have told us only that it opened nothing in 24h, not that the filter works. In the run evanng
returned **60 / excluded 57 / booked 3**; verification of the three booked rows showed live bets on 2026-08-25/26
events (2 EFL matches, 1 UFC fight), priced 0.81–0.89, unresolved — the filter kept them **because they are real**.
**The filter's evidence is the 815/917 (88.9%) exclusion rate plus row inspection of the survivors (forward dates,
mid-range prices) — not the evanng count.**

## The measured cap and its UNMEASURED bias

`/positions` returns at most 100 rows per whale (confirmed: 6/14 hit exactly 100). For those six the poller is
**blind to genuine open positions past #100** (e.g. 4751346 booked 50 opens *inside* a capped-100 book; its true
open count may be higher). **The direction of the blindness is unmeasured.** If the API orders by recency, size,
or anything else, the missing positions are systematically the older or the smaller ones — and by the platform's
first principle an unmeasured bias cannot be resolved conservatively, only measured. Do not guess the direction.
`cap_suspect` today means "possibly truncated"; it should eventually mean "known truncated, N unknown." Fix is a
paginated `/positions` path that does not touch the shared client — **CP3b**.

## Backups on the box (all kept this session)

- Gate 1 DB: `~/pm_cp3a_db_backup_20260825T140605Z.db`; runtime db.py: `.../db.py.pre_cp3a_20260825T140605Z.bak`
- Gate 2 run-1 DB: `~/pm_cp3a_gate2_db_backup_20260825T151528Z.db`; **orig CP2 pm_cli.py (code-rollback):**
  `~/pm_cp3a_gate2_pmcli_backup_20260825T151528Z.py.bak`
- Gate 2 resume DB: `~/pm_cp3a_gate2resume_db_backup_20260825T152544Z.db`
- Gate 3 DB: `~/pm_cp3a_gate3_db_backup_20260825T154847Z.db`; poller JSON: `~/pm_cp3a_gate3_pollout_20260825T154847Z.json`

Rollback of the paper data: `DELETE FROM pm_paper_trade; UPDATE pm_roster SET last_polled_ts=NULL;`

## Open items going forward

1. **Ruling-B refresh-source flip** (ingest → `pm_roster WHERE active=1`) — not implemented; benign only while the
   seed and refresh share a source. CP3b.
2. **FARM LEAGUE / WATCHLIST vocabulary collision** — Jack's FARM LEAGUE == shipped `status='watchlist'`; Jack's
   WATCHLIST == shipped `status='pinned'`. Do not rename during a reseed. CP3b.
3. **cap-at-100** — paginated fetch + measure the truncation-bias direction. CP3b.
4. **48h grace window on a false premise** — `grace_window_sec=172800` was chosen assuming a WEEKLY refresh
   cadence; the actual refresh is DAILY (03:20 UTC cron). Revisit the grace when the adjudicator is first exercised.
5. **Record-only (CP2, not CP3a):** SDTrading detail `481 vs 477` drill mismatch; two position rows with
   future-dated (2026-08-25) resolution timestamps.

## NOT done here (Jack's sequencing) — separate authorizations

- **No cron / no timer.** Deferred deliberately: the adjudicator has never run on live data. Sequence = let the
  102 sit → daily refresh pulls their closed positions as they resolve → **one manual, gated adjudicator run**
  (first real test of two-phase pending→closed|stale + grace + subset assertion) → only then discuss a schedule.
- **Adjudicator not run.** Pre-adjudicator readiness (ready, not run): a read-only query of `pm_closed_position`
  for the 102 `(wallet, condition_id, outcome_index)` triples to see which have resolved.
- **CP3b not built.** No merge to main.
