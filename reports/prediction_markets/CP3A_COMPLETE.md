# CP3a COMPLETE + box-scratch VERIFIED

**Branch:** `prediction-markets-cp3a-2026-08-24`
**Verified code tip:** `b593b39` (this capstone is a doc-only commit on top; no code/test change since the green run).
**Status:** CP3a built + box-scratch green. **HALT at CP3a.** Deploy is a SEPARATE authorized step (see NEXT).

Every claim here is traceable to `git show` on this branch or to the box-scratch output pasted 2026-08-25.

## Box-scratch result (tc-prod-vm, 2026-08-25T13:19Z, code b593b39) — actual, not predicted
- **pytest:** `<testsuite errors="0" failures="0" skipped="1" tests="145">`, `PYTEST_RC=0` -> **144 passed, 0 failed, 0 errors, 1 skipped.**
- **Chain of custody:** staged tarball sha256 box == local (`0996b11f…`).
- **Migration on REAL data (WAL-safe copy of live; live untouched):** copy `schema 4 -> 6`, `closed_rows 29709` intact, `pm_paper_trade/pm_paper_config/pm_roster/pm_watchlist` all created.
- **Seed preview (reads real agent_state mode=ro; writes only the /tmp copy):** `n_seeded=114`, `n_wallets=14`, `subset_after {n_pinned:14, n_refreshed:14, unrefreshed:[]}`, `MIGRATE_ROSTER_RC=0`.
- **Isolation invariants (this run):** live `schema_version=4` before AND after; engine `MainPID 969439` unchanged; legacy DB mtime unchanged; no `*.db` under scratch; scratch/stage/preview proven gone.

## What CP3a shipped (branch commits)
- `85cbe6b` contamination gate (C1-corrected).
- `614d803` migration 005 — `pm_paper_trade` full lifecycle + `pm_paper_config`.
- `c8d7375` migration 006 — `pm_roster` + `pm_watchlist`; P2_PLAN §5.2/§5.3 amended.
- `3476ec5` `/positions` paper poller.
- `1e370c7` adjudicator (two-phase, subset assertion, bias-down).
- `dfeb213` box-scratch runner.
- `e92d31b` **reseed from `pm_category_stats` (advisor ruling C2.4 REVERSED by Jack)** + docs — supersedes the earlier scout-provenance seed (`a8d4036`).
- `81d7279` poller amendments F/G/H + `pm_roster.last_polled_ts`.
- `653057f` box-scratch seed-preview follows the reversed seed.
- `b593b39` bump 4 stale `schema==4` test literals to 6 (CP3a added 005/006).

Final `schema_version` = **6**. `main` untouched. `config/pm_farm_pin_provenance.yaml` retained on disk as historical scout attribution; nothing reads it at seed time.

## The seeded watchlist (approved as-is by Jack 2026-08-25) — 114 pairs, 14 whales
Every `(wallet, category)` in `pm_category_stats` for the migrated legacy whales: no minimum-resolved floor,
`unknown` included, all categories paper-trade. Real money still requires a P3 account-category attachment
(does not exist yet), so nothing on this list can go live. Curation is a board action at the pin lifecycle,
not a migration rule. The full 114-pair table is in the box-scratch `[4d-iv]` output.

**Two edge cases — acknowledged, both STAY (Jack):**
1. **`4751346/nfl` `rows_in_category=0`** — all its nfl closed positions are §3A-quarantined, so it seeds under
   no-floor and shows n=0 on the scoreboard. Kept. *The first person to see a paper record with no scoreboard
   baseline: this is why.*
2. **`unknown` across 11 of 14 whales** (BetMechanic 1630, kutsumiakia 1444, 4751346 1151, Kickstand7 1119,
   AIisTheNewWD 463, pako 262, …). Kept, per the ruling. `unknown` is a tier-1 slug-derivation miss, not a real
   category; nothing repaired in this pass.

Largest pair overall = **`BetMechanic/nba` n=6782** (then `BetMechanic/mlb` 4639, `nhl` 2284). (`kutsumiakia/unknown`
n=1444 is the largest *unknown* pair, not the largest overall — a prior "largest single pair" screenshot figure was
filtered/paginated and wrong; the box-scratch figures are authoritative.)

## F / G / H — UNIT-TESTED, NOT MEASURED
The poller has NOT run against live `/positions`. `n_skipped_category` (F), `last_polled_ts` (G), and
`cap_suspect` (H) are exercised by `test_paper.py` only. **"Implemented" is not "observed."** The first real
numbers come at the poller one-shot, which is a separate authorized step. Expectation (not a measurement):
`n_skipped_category ~ 0` under this seed, because every category a migrated whale trades is pinned.

## OPEN ITEM carried into CP3b — the Ruling-B refresh-source flip
P2_PLAN Ruling B says ingest's roster source flips to `pm_roster WHERE active=1`. **That flip is NOT
implemented:** `pm_cli` refresh/backfill call `rosters.load_seed_roster()` (legacy `agent_state` + seed yaml);
`ingest.py` reads no roster; the only reader of `pm_roster WHERE active=1` is `paper.assert_pinned_subset_of_refresh`.
Benign under CP3a's seed (pm_roster and pm_watchlist are written in the same pass from the same source, so they
agree — `unrefreshed=[]`). **Belongs in CP3b** (Jack's placement): the moment search adds whales to `pm_roster`
that legacy `agent_state` never had, the refresh must read `pm_roster` and the subset assertion stops being
tautological. Do not implement before then.

## VOCABULARY COLLISION — mapping for CP3b (do NOT rename during a reseed)
- **Jack's FARM LEAGUE** (dynamic weekly-scanned candidate pool, NOT paper-traded) == shipped `pm_watchlist.status = 'watchlist'`.
- **Jack's WATCHLIST** (permanent, board-locked, paper-trading) == shipped `pm_watchlist.status = 'pinned'`.
CP3a seeds every migrated pair as `status='pinned'`. Full note in `CP3A_CONTAMINATION_GATE.md`.

## Record-only (CP2, untouched — NOT CP3a, do not fix here)
1. SDTrading detail page self-reports `showing 481 rows … drill 481 != aggregate 477` (CP2 reconciliation
   catching itself). The seed reflects the same `SDTrading/mlb n_resolved=481`.
2. Two position rows show resolved dates in the future (`2026-08-25`) — possibly `end_date` standing in for
   resolution time / timezone.

## NEXT — deploy is a SEPARATE authorized step (NOT done in CP3a)
1. Apply migrations 005+006 to the LIVE PM DB as azureuser (`runuser -u azureuser`, never root); verify schema
   4->6, rows intact, ownership azureuser.
2. `migrate-roster` against the live DB (seeds the 114 pairs into live `pm_roster`/`pm_watchlist`).
3. Restart `prediction-markets-web.service` only (never the engine); `/healthz` 200 at schema 6.
4. Poller ONE-SHOT (`pm_cli paper-poll`) — first live `/positions` capture; review per-whale captured rows +
   `n_skipped_category` + any `cap_suspect` BEFORE any cron. No cron until Jack reads the one-shot.
Each step is deploy-phase, gated on Jack's go. `poly_kalshi_mlb` remains ARMED AND UNPROVEN on the order path;
the paper farm is a separate standalone app and never touches it.
