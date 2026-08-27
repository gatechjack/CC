# PM PK-COLLISION TRIAGE — 2026-08-27 (read-only)

**Task:** triage the 2026-08-27 03:20Z cron `pm_cli refresh` failure (`complete: 13, partial: 0, failed: 1`)
— whale `0x767a7964deeea63dddd0cba6db39503f328d8ac5` (**MadeiraIsland**), `IntegrityError` PK-COLLISION from
the P1-era `_assert_no_pk_collision` guard in the `pm_closed_position` ingest path.
**Mode:** READ-ONLY. No fix, no deploy, no restart, no write. Board-authorized atomic run of
`pm_pkcollision_triage_ro.ps1` (streamed CR/BOM-stripped `.sh` to `azureuser` `bash`).
**Snapshot:** UTC 2026-08-27 04:35:30Z; engine `trading-corp.service` MainPID **89366** (NRestarts 0),
pm_web `prediction-markets-web.service` MainPID **40483** (NRestarts 0), both active; live PM DB schema **8**.
Raw machine output in the appendix.

---

## Findings

### (a) NEW, not recurring — first occurrence is the 2026-08-27 03:20Z run
- `~/pm_refresh.log` (12,974 bytes, mtime Aug 27 03:41) contains **exactly ONE** `PK COLLISION` line, **one**
  `IntegrityError` line, and **one** `readonly database` line (the last is the known Aug-23 P1 ownership bug,
  already resolved — unrelated).
- Per-run JSON parse of the append-only log: **4 parsed cron summaries.** Runs 0–2 = `complete 14 / failed 0`.
  Run 3 (the most recent, 03:20Z 2026-08-27) = `complete 13 / failed 1`, the failed wallet being the target.
- `TARGET_FAILED_IN 1 of 4 parsed_runs`. **The collision appeared for the first time last night.** The prior
  three cron cycles refreshed all 14 whales cleanly.

### (b) What collides — a TRANSIENT duplicate that is NO LONGER REPRODUCIBLE (settlement/pagination race)
- The logged error is `repr(e)[:200]`; it truncates at `sample=[{'key': ['` — the colliding `condition_id`
  is **cut off** and unrecoverable from the log. Header survives: `1208 pulled rows -> 1207 distinct PKs;
  1 key(s) would be SILENTLY collapsed`.
- **PK = `(wallet, condition_id, outcome_index)`** — the migration-002 3-tuple. A collision on the full
  3-tuple means two pulled rows share the same market AND the same side — a *genuine* duplicate, a different
  class from the old Kickstand7 two-sided (2-tuple) collapse migration 002 fixed.
- **Read-only reproduction pull at 04:35Z (≈75 min after the cron), identical params (`limit=50 cap=50000`),
  same quarantine order, same `_pk_of` grouping:** `pulled_positions: 1208 | records_after_quarantine: 1208
  | distinct_pk: 1208 | dup_keys: 0`. **The duplicate is GONE** — the wallet now pulls 1208 fully-distinct
  rows. Same pulled count (1208) both times, distinct went 1207 → 1208.
- **Mechanism (evidence-backed):** the duplicate was a **transient snapshot inconsistency**, not corrupt
  persistent data. `_pull_closed` paginates in 50-row pages; if the wallet's closed-position set mutates
  mid-pagination (a market **resolving/settling right at the 03:20 cron**), a row can straddle a page
  boundary and surface twice, or a mid-settlement row can momentarily key onto an existing PK. ~75 min later
  the market has fully settled, pagination is stable, and the set is 1208-distinct. The guard did exactly its
  job: it **refused to write** a batch it could not store faithfully (fail-safe, no silent collapse) — it did
  not corrupt anything. **I cannot show the two colliding rows because the transient state has cleared;** the
  non-reproducibility IS the finding. (A specific `condition_id` would be a guess — deliberately not made.)

### (c) Staleness — exactly ONE refresh cycle (~24h) behind its 13 peers
- Target `MadeiraIsland`: `last_refresh_ts` = 2026-08-26 03:20Z, **hrs_ago = 25.3**, `pulled=1194 stored=1194`
  (its last *successful* refresh).
- All **13** other whales: `last_refresh_ts` = 2026-08-27 03:20Z, **hrs_ago = 1.3** (exactly 86,400s = 24h
  newer than the target).
- So MadeiraIsland's stored rows reflect the **Aug-26** pull; last night's collision meant its ~14 newly
  resolved positions (1208 available vs 1194 stored) were **not** ingested. Staleness = one cycle now, and it
  **grows +24h every night the collision recurs** (a permanent silent skip if unaddressed — same failure
  shape as a stuck high-water-mark, though here it is a nightly hard-fail, not an HWM advance).

### (d) Reach — YES, it touches 12 of the 15 tiles Jack will look at
MadeiraIsland is a mega-whale **pinned (`status='pinned', active=1`) in all 14 of its categories**, and its
`pm_category_stats` rows were **recomputed last night** (`updated_ts` 2026-08-27 ~03:41) from **stale**
`pm_closed_position` data — so those tiles show a fresh-looking `updated_ts` over 24h-old inputs. Stored
closed-position spread, in-15-tile flag, and current (stale) category roi:

| category | in 15-tile | n_resolved (cat_stats) | stored cp rows | note |
|---|---|---|---|---|
| atp | **yes** | 382 | 382 | largest exposure |
| mlb | **yes** | 367 | 367 | in-season, largest live |
| ufc | **yes** | 135 | 135 | |
| soccer | **yes** | 50 | 50 | |
| nba | **yes** | 34 | 34 | |
| wnba | **yes** | 22 | 22 | |
| wta | **yes** | 19 | 19 | |
| nhl | **yes** | 16 | 16 | |
| epl | **yes** | 10 | 10 | |
| nfl | **yes** | 3 | 3 | |
| ucl | **yes** | 2 | 2 | |
| tennis | **yes** | 1 | 1 | |
| fifwc | no (Stage-0 removal) | 100 | 100 | inactive after rung 3 |
| unknown | no (Stage-0 removal) | 53 | 53 | inactive after rung 3 |

So the staleness reaches **12 in-tile tiles** — most materially **atp / mlb / ufc**. The per-tile magnitude is
small right now (~14 missing rows spread across categories, low single-digit % of each), but it is real and
compounds if the collision recurs, and any Analyze run on this pinned whale is fed 24h-old inputs.

### (e) Does it block rung 2? — NO. It is orthogonal to the ladder.
- Rung 2 deploys the **gated readers** (`prediction_markets/db.py`, `paper.py`, `farm.py`, `stats.py`) and
  restarts pm_web. It **does not touch `ingest.py`**, the guard, the cron, or `pm_closed_position` writes. The
  collision lives entirely in the nightly `pm_cli refresh` INGEST path. **Zero surface of interaction.**
- Rung 2 is behaviour-neutral (all 114 rows `active=1`); its proof is a byte-identical `/farm`. MadeiraIsland's
  staleness is **already baked into the current `/farm`** and will be identical before/after rung 2 — it does
  not perturb rung 2's verification.
- The guard **failed safe** (refused to write a collapsible batch); MadeiraIsland's stored rows are intact and
  self-consistent (1194, `pulled==stored`), merely a day old. No corruption to unwind before rung 2.
- **Prior agent's view:** "independent of the ladder, but it is 'something off' so I am not declaring it
  non-blocking on my own." **I agree it is independent, and the reproduction lets me go further:** proving the
  duplicate is *transient and non-reproducible* removes the "something structurally off" worry that made them
  hedge. **My verdict: does NOT block rung 2.**
- ⚠ **One thing to carry into rung 2:** if the *next* 03:20 cron collides again on MadeiraIsland, that is this
  pre-existing ingest anomaly, **not** a rung-2 regression — do not misattribute it.

---

## Recommendation (NOT actioned — separate authorization; likely NOT Stage 0)
The fix, if any, belongs to **ingest robustness**, not Stage 0 / rung 2. Options for Jack to rule on later:
1. **Leave the guard hard-failing** (status quo): accept an occasional 24h staleness for a whale that has a
   market resolving at cron time; acceptable IF it stays rare (1-in-4 so far) and self-clears — but risks a
   compounding permanent skip if this high-volume multi-sport whale recurs.
2. **Make ingest tolerant of transient pagination duplicates** — e.g. de-dupe the pulled batch on the storage
   PK (keep the newest/most-resolved row) *before* the guard, and reserve the hard-fail for a duplicate that
   *survives* de-dupe; or retry the wallet after a short delay when the guard trips (the reproduction shows a
   later pull is clean). This preserves the anti-silent-collapse intent while not dropping a whole whale for a
   snapshot race.

**Recurrence risk is non-trivial:** MadeiraIsland is the highest category-count whale in the roster, so it is
the most likely to have *some* market resolving near any given cron time. Worth a fix soon, on its own ticket.

**Optional targeted follow-up (read-only, not run):** re-pull the wallet and list markets whose `resolved_ts`
falls within ±30 min of the last two 03:20 crons — would identify the *candidate* market that was mid-settle,
strengthening the race diagnosis to a named market (still a candidate, not the proven colliding `condition_id`,
which the log truncated away).

---

## Appendix — raw machine output (2026-08-27 04:35:30Z)

```
=== S0: service context ===
engine MainPID=89366 NRestarts=0 ActiveState=active
pmweb  MainPID=40483 NRestarts=0 ActiveState=active

=== S1 (1a) refresh-log ===
-rw-rw-r-- 1 azureuser azureuser 12974 Aug 27 03:41 /home/azureuser/pm_refresh.log
PK_COLLISION_lines: 1 | IntegrityError_lines: 1 | readonly_db_lines: 1
line 544: "error": "IntegrityError(\"PK COLLISION for wallet 0x767a...d8ac5: 1208 pulled rows -> 1207 distinct PKs; 1 key(s) would be SILENTLY collapsed by INSERT OR REPLACE. sample=[{'key': ['"  [TRUNCATED]
parsed_json_runs: 4
  run 0 | complete 14 partial 0 ok 14 failed 0
  run 1 | complete 14 partial 0 ok 14 failed 0
  run 2 | complete 14 partial 0 ok 14 failed 0
  run 3 | complete 13 partial 0 ok 13 failed 1  -> FAILED 0x767a...d8ac5
TARGET_FAILED_IN 1 of 4 parsed_runs

=== S2 (1c) staleness (schema 8, now_epoch 1787805331) ===
0x767a7964...d8ac5  MadeiraIsland  lr=1787714401 hrs_ago=25.3 pulled=1194 stored=1194  <== TARGET
(the other 13)                     lr=1787800801 hrs_ago=1.3   (Kickstand7 1803, SDTrading 544, xifutloong3 203,
  Kh4mz4t 308, STC14 91, 000why000 815, 4751346 2664, kutsumiakia 2732, FordBronco 215, AIisTheNewWD 1675,
  BetMechanic 17056, pako 370, evanng 145)

=== S3 (1d) target categories: 14 pinned rows (all active=1); category_stats updated_ts 1787802051 ===
in-15-tile: atp mlb ufc soccer nba wnba wta nhl epl nfl ucl tennis   |   not: fifwc unknown

=== S4 (1b) read-only reproduction pull ===
pulled_positions: 1208 | records_after_quarantine: 1208 | distinct_pk: 1208 | complete: True | dup_keys: 0
NO_DUP_PK_IN_THIS_PULL  (03:20 cron saw 1208->1207; 04:35 repro sees 1208->1208 => transient, self-cleared)
```

Runner: `cc\pm_pkcollision_triage_ro.ps1` + `.sh` (read-only; DB via `mode=ro` stdlib sqlite3; S4 is a
public `/closed-positions` GET, no creds, no writes).
