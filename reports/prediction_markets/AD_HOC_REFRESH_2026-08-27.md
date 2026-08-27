# Ad-hoc refresh — 2026-08-27 (authorized live write)

**Purpose (Jack's call):** run the identical nightly `pm_cli refresh` now, to (1) TEST the PK-collision
immediately instead of waiting for tonight's cron, and (2) SETTLE `/farm` before rung-2's `B0` baseline is
taken. **Authorized live write; rung 2 NOT authorized; no PK-collision fix.**

**Invocation — identical to the 03:20 cron (verified from `crontab -l`):**
`cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py refresh --cap 50000 >> ~/pm_refresh.log 2>&1`
Run **detached** (`setsid`, immune to the ssh session ending) — the only variation from the cron is the launch
wrapper; the `pm_cli` command + `--cap 50000` are byte-identical. Launched 12:00:12Z, `ADHOC_DONE exit=0` at
**12:22:54Z** (~22 min). Sentinel `~/pm_adhoc_refresh_sentinel_20260827T120012Z.txt`.

## Before → After

| Metric | Before (11:58Z) | After (12:23Z) | note |
|---|---|---|---|
| refresh result | (03:20 cron: 13 ok / 1 failed) | **complete=14 / partial=0 / failed=0** | all 14 OK |
| MadeiraIsland `0x767a…d8ac5` | failed (PK collision) / stored 1194 | **ok, pulled=1210 stored=1210 verdict=complete** | **succeeded** |
| MadeiraIsland `last_refresh_ts` | 1787714401 (2026-08-26 03:20Z) | **1787832012 (2026-08-27 12:00Z)** | ~32.7h → current |
| pm_closed_position TOTAL | 29,815 | **29,839** | **+24** rows |
| whale spread (all 14) | target 32.7h behind 13 | **0.000h** (all identical lr) | in sync |
| `/farm` bytes | 228,566 | **228,569** | +3 (refresh wrote rows — EXPECTED) |
| schema | 8 | 8 | unchanged |
| pm_watchlist / active1 / active0 | 114 / 114 / 0 | 114 / 114 / 0 | funnel untouched |
| pm_paper_trade | 102 | 102 | unchanged |
| engine PID | 89366 | 89366 | unchanged |
| pm_web PID / NRestarts | 40483 / 0 | 40483 / 0 | not restarted |
| cat_stats MAX(updated_ts) | ~1787802051 (last night) | 1787833359 (12:22Z) | **rollup ran** |

## Reports (a)-(f)

**(a) MadeiraIsland succeeded; all 14 complete.** `complete=14 / partial=0 / failed=0`. MadeiraIsland
`pulled=1210 stored=1210 verdict=complete`. Every other whale `complete` too (BetMechanic 17,056 under the
50k cap).

**(b) No re-collision** → the conditional reproduction pull was **not** triggered. (Had it collided, the runner
would have captured the `condition_id` + dup rows read-only and I would have declared it NOT transient and
stopped, moving the ingest ticket ahead of rung 2. That did not happen.)

**(c) CONFIRMED TRANSIENT.** The same wallet that hard-failed the 03:20 cron on a PK collision (`1208 pulled →
1207 distinct`) now pulls **1210 fully-distinct** rows and stores them cleanly (`pulled==stored==1210`,
`verdict=complete`). MadeiraIsland's new `last_refresh_ts` = 2026-08-27 12:00Z; **row delta 1194 → 1210
(+16)**. **All 14 whales now share the identical `last_refresh_ts` (spread `0.000h`)** — the staleness is fully
closed and the settlement/pagination-race read from `PK_COLLISION_TRIAGE_2026-08-27.md` is confirmed
empirically.

**(d) Writes / rollup / schema / funnel.** pm_closed_position **29,815 → 29,839 (+24)** — MadeiraIsland +16,
kutsumiakia +6, 000why000 +2, all others +0. **Rollup ran** (`cat_stats MAX(updated_ts)` advanced to 12:22Z).
Schema still **8**; pm_watchlist **114 / active1 114 / active0 0** (Stage-0 funnel untouched — rung 3 not done).

**(e) `/farm` 200, byte size CHANGED (correct).** `228,566 → 228,569 (+3)`. Expected — the refresh wrote rows
and the rollup rewrote `pm_category_stats`, which `/farm` renders. **NOT a regression.** (This is a third
concrete data point that `/farm` moves for ingest reasons alone — reinforcing the load-bearing rung-2 timing
rule; see PM_REBUILD_PLAN PRE-1.)

**(f) Invariants held.** engine PID **89366** unchanged; pm_web PID **40483** unchanged (NRestarts 0, not
restarted); pm_paper_trade **102** unchanged.

## Consequence for rung 2
`/farm` is now **settled at 228,569 bytes** post-refresh. Per PM_REBUILD_PLAN PRE-2, the rung-2 `B0` baseline
must still be captured **fresh in the deploy session** (not from this reading) — but this refresh removes the
"stale-whale mid-rung" risk: all 14 whales are current, so the next scheduled cron is the only remaining
refresh to keep the deploy window clear of.

**Leftovers on box (harmless):** `~/pm_adhoc_refresh_sentinel_20260827T120012Z.txt` and an
`ADHOC_REFRESH_START …` marker line in `pm_refresh.log` (documents the run). Nothing deployed, no restart, no
prod-live movement, no code change. See `PK_COLLISION_TRIAGE_2026-08-27.md`.
