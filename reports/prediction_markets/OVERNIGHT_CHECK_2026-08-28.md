# PM OVERNIGHT-CHECK — first unattended 05:00 cron cycle (2026-08-28)

**Read-only pass. Nothing was written, deployed, or run manually; the cadence was not touched.** This records what
the first unattended `refresh 05:00 -> adjudicate 05:40 -> rollup 05:50` cycle (with the `*/30` poller through it)
actually did, established from the box logs and DB — not inference. Governs: `PM_REQUIREMENTS.md`. Prior handoff:
`TRANSITION_STAGE2_COMPLETE_2026-08-28.md` §1 (this task's checklist).

- **Verified SHAs (not on faith):** phase-3 branch tip `2d9a700` (via `rev-parse`); `origin/prod-live` = `7220e32`
  (via `ls-remote`, authoritative); `95e78c4` reachable from prod-live (MACE fork base preserved). Box PM package
  re-hash == prod-live `7220e32` **29/29, 0 mismatches** — **no code drift overnight.**
- **Evidence:** read-only runners (sanctioned channel, Jack-executed) `cc\pm_overnight_check_ro.*` (pass 1, 14:05Z)
  + `cc\pm_overnight_check2_ro.*` (pass 2, 14:10Z). Box `tc-prod-vm`.

---

## VERDICT — the cycle ran CLEAN.

All four jobs fired and completed. **No `SQLITE_BUSY`** — the designed 05:00 poll/refresh overlap held on its first
real test. Refresh 14/14; the 08-27 PK-collision did **not** recur. **The first-ever live LOST path was exercised —
13 losses booked, and every one is correct** (arithmetic exact + independently gamma-confirmed). Rollup updated
`pcs` 7->9 with R1 intact. `/farm/ufc` reconciles. Write-audit clean. Services stable (no restarts). **Nothing
requires a fix.** Two items are flagged for Jack's judgment (product signal + one historical log entry), not defects.

---

## a. Did all four jobs fire?

| Job | Sched | Fired / done (log mtime) | Result JSON |
|---|---|---|---|
| **refresh** | `0 5` | 05:00 -> **05:20:45Z** (~20 min; big whale `0xa6a8` = 17,056 rows) | `complete: 14, partial: 0, failed: []` |
| **adjudicate** | `40 5` | **05:40:01Z** | `pending_in 20 -> closed 19, still_pending 1, voided 0, staled 0`; subset `{n_pinned 14, n_refreshed 14, unrefreshed []}` |
| **rollup** | `50 5` | **05:50:02Z** | `{"rolled_pairs": 9}` |
| **poller** | `*/30` | ongoing, last **14:00:06Z** | ~38 runs, `errors: []` every run |

**Limitation (honest):** the `pm_cli` subcommands print only a result JSON — no self-timestamp, exit-status, or
runtime. Completion time is taken from the per-job log mtime; success is inferred from a well-formed result JSON
(a crashed run prints a traceback instead — see the historical one in §b). Exact runtimes are not logged.

## b. Any `SQLITE_BUSY` from the designed 05:00 poll/refresh overlap?

**None.** No `SQLITE_BUSY` / `database is locked` / `table is locked` token in any `pm_*.log`. The overlap guard
(schedule spacing + WAL + `busy_timeout=5000`, no flock) held on its first real test. The `*/30` poller ran at 05:00
concurrently with the 05:00 refresh (which held write locks for ~20 min doing large `executemany`s) and no writer
collided past the busy timeout. The poll-log "ERROR" grep hits are all benign `"errors": []` / `"errors": 0`.

**Two non-BUSY grep hits, both in `pm_refresh.log`, both PRE-OVERNIGHT (positively dated by append-only run-order):**
The 816-line log holds 6 runs + 1 crash, oldest-first:
1. **line 1–21 — a `backfill` crash** (`_cmd_backfill` -> `stats.rollup` -> `executemany`) with
   `sqlite3.OperationalError: attempt to write a readonly database`. **Oldest entry**, a manual `backfill` (not the
   cadence `refresh`, not cron), predating the 08-27 12:00 ad-hoc below. Historical; the DB has been writable by the
   cron user in every run since (05:00 refresh, 05:40 adjudicate, 05:50 rollup, 14:00 poll all wrote successfully).
2–4. three clean `complete: 14` runs.
5. **line 421–548 — the `0x767a` (MadeiraIsland) PK-collision** run: `complete: 13, failed: [0x767a IntegrityError
   "1208 pulled -> 1207 distinct PKs; 1 collapsed"]`. This is the **08-27** event (immediately precedes the ad-hoc
   marker), already recorded/triaged transient.
6. **line 550 — `ADHOC_REFRESH_START 20260827T120012Z`** -> clean `complete: 14` (the documented 08-27 12:00 triage
   that cleared the collision).
7. **line 684–815 — the 05:00 cron refresh (LAST run): clean `complete: 14, partial: 0, failed: []`.** Last-run ->
   EOF scan = **zero** readonly / collision / traceback tokens.

So the readonly error and the collision are both **older append-only entries; neither is from the overnight cycle.**

## c. Did refresh complete for all 14 whales? PK-collision / partial?

**14/14 `complete`, 0 partial, 0 failed.** `0x767a` stored **1,217 rows, verdict complete, 0 anomaly — the
2026-08-27 PK-collision did NOT recur.** All 14 roster wallets show `pm_closed_position.updated_ts = 05:00:01Z`.
Two whales carried refresh-internal data-quality flags but verdict `complete` (not failures): `0xd1acd` suspect 65 /
anomaly 1, `0x43e0` anomaly 3. `pm_roster active=1` = 14 distinct wallets; pinned active = 14. Consistent.

## d. ★ Did adjudicate find anything, and did any trade book a LOSS?

**Yes — 19 of 20 pending resolved (1 still pending), and 13 booked as LOSSES. This is the first-ever live exercise
of the LOST path, and it is correct.** Closed rows overall: **21 total (8 WON, 13 LOST)** = 2 pre-existing ufc wins
(08-27) + 19 new from the 05:40 adjudicate. `void=0, stale=0`. All 13 losses `close_source='gamma_resolution'`.

**Correctness — checked three independent ways:**
1. **Arithmetic (all 13):** `realized_pnl == -cost_basis` to 1e-6, sign negative. (`_paper_realized`: a lost leg pays
   0 -> `-cost_basis`; a won leg pays $1/contract -> `size_basis - cost_basis`. size_basis = 100 for every row.)
2. **Independent gamma re-confirmation (all 13):** each market re-fetched `resolved`, and our `outcome_index !=
   winning_outcome_index`. Examples: `0xdd12…` "Dodgers vs Braves O/U 8.5" winner idx 1 (Under), we held idx 0
   (Over) -> loss; `0x2800…` "Dodgers vs Braves" ML winner idx 1 (Braves), we held idx 0 (Dodgers) -> loss.
3. **Orientation sanity:** on shared markets held by two whales on opposite sides, the sides resolved win-vs-loss as
   expected — `0xc91b` (Cerundolo/Buse, winner Buse): `0x1f71` on Buse WON +28.01, `0x767a` on Cerundolo LOST −51;
   `0xab33` (Fery/Kovacevic, winner Fery): the Fery leg WON +46.05, the Kovacevic leg LOST −50. Not inverted.

**This is the §B gamma re-base working as designed:** losses that `/closed-positions` systematically omits are booked
from gamma (the resolution authority), so the paper lane's loss-completeness is independent of that omission.

Loss clusters (paper): **mlb `0x16bb` — 8L / 2W, net −236.91**; **atp `0x767a` — 5L / 1W, net −241.95**.

## e. Did the rollup change `pm_paper_category_stats`? R1?

**`pcs` 7 -> 9 rows; `rolled_pairs = 9`; R1 HELD — 0 pcs rows on a non-active/non-pinned pair.** The 19 closed rows
resolved this cycle map to exactly 5 pairs: mlb/`0x16bb` (10), atp/`0x767a` (6), atp/`0x1f71` (1), cs2/`0xc3e5` (1),
nfl/`0x767a` (1) = 19. New pcs rows are the freshly-decided pairs (atp/`0x1f71`, cs2/`0xc3e5`, nfl/`0x767a`,
atp/`0x767a`); the two ufc rows are unchanged. All pcs rows `updated_ts = 05:50:02Z`. Key rows:
`mlb/0x16bb n_closed=10 W2/L8 net −236.91 n_open=4`; `atp/0x767a n_closed=6 W1/L5 net −241.95`;
`ufc/0x52f454 net +89.54`, `ufc/0x43e0 net +11.39` (unchanged).

## f. Does `/farm/ufc` still reconcile?

**Yes, unchanged.** `/farm/ufc` 200, **19,403 B** (identical to the 05:38 baseline), renders **Kh4mz4t +89.54** and
**evanng +11.39**. The two ufc closed rows still show `won=1` / +89.54 (`0x52f454`) and +11.39 (`0x43e0`),
`resolved 2026-08-27T17:39:54Z` — nothing ufc adjudicated overnight, so nothing moved.

## g. Confirm nothing else wrote the PM DB overnight.

**Clean.** Every write maps to a cron window: `pm_closed_position` 05:00:01 (refresh), `pm_category_stats` 05:20:33
(refresh rollup), `pm_paper_category_stats` 05:50:02 (paper rollup), `pm_paper_trade` 14:00:02 (the 14:00 poll,
ongoing). **`pm_watchlist` untouched since 2026-08-25** (no writes overnight). DB file mtime 14:00:06Z = last poll.
No writes at an unexpected time; no writer other than the four cron jobs.

## h. Leave-it-running snapshot (2026-08-28 14:05–14:10Z, actual)

- **engine** PID **676** NRestarts 0 active/running · **pm_web** PID **42343** NRestarts 0 active/running (both
  identical to the 05:38 baseline — no restarts overnight).
- `/healthz` 200 (59 B) · `/` 200 (2,306 B) · `/farm` 200 (4,339 B) · `/farm/ufc` 200 (19,403 B) — all match baseline.
- **schema 9** · `pm_watchlist` **114 / 92 / 22** · `pm_paper_trade` **135** (21 closed / 112 open / 2 pending) ·
  `pm_paper_category_stats` **9 rows**.
- Ledger re-hash: box PM package == prod-live `7220e32` **29/29, 0 mismatches**; 5 deleted templates absent; shared
  `pm_cli.py` + data client MATCH; one benign EXTRA (`db.py.pre_cp3a_…bak`).

`pm_paper_trade` moved 129 -> 135: adjudicate resolved 19 of 20 pending (pending 20 -> 1); the `*/30` polls since
05:40 captured new opens (+5) and moved one open -> pending (pending 1 -> 2). All consistent.

---

## For Jack to judge (NOT a defect; not mine to act on)

1. **Two pinned whales just posted heavy paper losses** — mlb `0x16bb` (net −236.91) and atp `0x767a` (net −241.95).
   This is the **system working as intended**: paper trading is now surfacing whales' real records including the
   losses `/closed-positions` used to hide. It is a **product signal** (Analyze / demote candidates), not a bug.
2. **The historical `readonly database` backfill crash** (oldest `pm_refresh.log` entry, pre-08-27-12:00, a manual
   `backfill` not the cadence) — noted so it is not rediscovered as alarming. Not overnight-relevant; DB writable in
   every run since. No action proposed.

## Not done (scope)
No fix applied, no Stage 3 started, no cadence touched, no manual cron run. Stage 3 remains unauthorized and unbuilt.
