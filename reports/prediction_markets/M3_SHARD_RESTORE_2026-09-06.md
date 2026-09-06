# M3 Shard-Snapshot Restore — Audit + Plan (2026-09-06)

Branch `pm-m3-shard-restore-2026-09-06` (worktree `cc-pm-m3-restore-wt`, base liveness `0eb93a1`).
Finishes repairing the 2026-09-04 wholesale-`main.py` clobber: the driver block was restored 09-06 00:30Z;
the **M3 shard-snapshot writer block** was found dropped too and is still missing.

---

## ★ TOP — ACTIVATION NEEDS AN ENGINE RESTART (bounces EVERY division)
Restoring M3 is a `main.py` edit. It does not activate until the engine (`trading-corp`) restarts, and that
restart bounces every co-tenant division (MACE, bitunix, PEAD, coinbase, poly_kalshi). **Warn co-tenants before
the restart.** The graft itself (disk edit) touches nothing at runtime; only the restart activates it. The M3
block is fail-safe-wired (its own `try/except`, never breaks boot) and fail-soft per account.

---

## 1. AUDIT — "what else is missing," established against the live box (read-only)
Runner `cc/pm_m3audit_ro.{ps1,sh}` (read-only), observed **2026-09-06 18:43Z**, engine PID 208950
(NRestarts=0, active), pm_web 211803, schema head **20**.

Reference for "carries EVERY PM block" = liveness/multicat `main.py` (CR-stripped `bba046e8f1ce9801`), which git
history confirms is the complete union of every PM `main.py` commit ever made:
- driver block: `4af6fd1` (R7.e wiring) -> `fc089ff` (N1/N2 roster) -> `5d104a3` (Option C per-account)
- M3 block: `e3f5665` (M3 5-min shard-snapshot writer)
There are exactly **two** `from trading_corp.prediction_markets import ...` lines in the reference (one per
block) — no third PM block exists.

### main.py PM accounting on the box (CR-stripped `236a6be054268278` = MACE-p2 + driver graft, unchanged since 09-06):
| PM block | box status | evidence |
|---|---|---|
| **Driver** (per-account roster + `scheduled_pm_live_loop`) | **PRESENT (intact)** | `scheduled_pm_live_loop`=2, `plan_driver_tasks`=2, `PM LIVE DRIVER WIRED`=1, `active_driver_subdivisions`=2 — all match reference |
| **M3 shard-snapshot writer** | **ABSENT (clobbered)** | `scheduled_shard_snapshot_loop`=0, `shard_snapshot_task_handle`=0, `M3 shard-snapshot writer WIRED`=0; its import line gone |

`prediction_markets` import lines on the box = **1** (driver only); reference-complete = 2. So **exactly one PM
block is missing = M3.** (The lone residual `M3 shard-snapshot` string on the box is the driver block's own
back-reference comment "Mirrors the M3 shard-snapshot block below" — the comment promises a block that is no
longer there; a clean confirmation of the gap.)

## 2. WIDEN — main.py was NOT the only shared file MACE deployed wholesale; the other two lost NOTHING
The box copies of the three genuinely-shared engine files MACE's Phase-2 branch touched all hash **exactly** to
MACE's stale-branch (`mace-ui-phase2-2026-09-04`) versions — MACE wholesale-deployed all three:

| shared file | box CR-stripped | == | PM wiring | dropped anything? |
|---|---|---|---|---|
| `trading_corp/main.py` | `236a6be054268278` | (grafted) | driver ✓ / **M3 ✗** | **YES — PM's two blocks** (driver restored, M3 pending) |
| `trading_corp/web/app.py` | `d0fdde0373f69805` | mace-p2 | **0 refs** | **NO** — pure superset of prod-live (+PnL routes, `^-` lines = 0) |
| `trading_corp/persistence/db.py` | `177d834a69ea5a55` | mace-p2 | **0 refs** | **NO** — pure superset of prod-live (+`mace_candle`/`mace_pnl_snapshot`, no `-` DDL) |

Why the asymmetry: PM's blocks were added to `main.py` LATE via box-grafts on the PM branch line, which MACE's
branch forked BEFORE — so MACE's `main.py` lacked them and the wholesale copy deleted them. `web/app.py` and
`persistence/db.py` had no such late cross-division graft; the box's pre-clobber versions equalled prod-live,
and MACE's versions are prod-live + mace-only additions, so those two wholesale copies dropped nothing — not
for PM, and (checked) not for any other division's engine schema/routes. **pm_web is fully isolated: MACE never
touched any `trading_corp/prediction_markets/` file, and pm_web runs a separate app
(`trading_corp/prediction_markets/web/app.py`).**

**Conclusion: the ONLY still-missing PM wiring anywhere is the M3 shard-snapshot block in `main.py`.**

## 3. The M3 callee survives — restore = re-add the main.py wiring only
`trading_corp/prediction_markets/shard_snapshot_task.py` is present on the box with all three defs
(`resolve_kalshi_keys`, `snapshot_once`, `scheduled_shard_snapshot_loop`). Migration 016 table
`pm_shard_balance_snapshot` is present (schema 20). So only `main.py`'s call-site was clobbered; the code it
calls is intact. This is a low-risk re-graft, identical in discipline to the driver restore.

## 4. Live symptom (baseline for the post-check delta)
`pm_shard_balance_snapshot` newest rows are **47.8h stale** for BOTH accounts (writer's last snapshot ~09-04
19:00Z, the clobber-activation restart). Frozen values still displayed:
- `kalshi_jack`  total $480.81  by_shard `{0:122.82, 1:0, 2:0, 3:357.99}`  (862 rows of history)
- `kalshi_karen` total $460.36  by_shard `{0:101.41, 1:0, 2:0, 3:358.95}`  (880 rows)

Trading is unaffected (the driver reads the venue directly); only the balance display + shard-0-direction line
are stale.

---

## 5. THE RESTORE PLAN (graft, never revert — box is truth)
- **Target:** box `main.py` CR-stripped `236a6be054268278` (drift-check ABORTS on any movement).
- **Insertion anchor:** immediately after the driver block's end at box line 1625
  (`log.exception("PM live driver wiring FAILED ...")`, line 1626 blank), BEFORE the "Phase 2a boot invariant"
  roster_split block at line 1627 — exactly where it sits in the reference (between driver-end and roster_split).
- **Payload:** the M3 block verbatim from the reference (`bba046e8`), base64-streamed (it contains non-ASCII
  em-dashes; base64 keeps the runner pure-ASCII and preserves exact bytes).
- **Gate-A:** after graft — new hash == expected; M3 markers now present (`scheduled_shard_snapshot_loop`=1,
  `M3 shard-snapshot writer WIRED`=1, import line back), `prediction_markets` refs = 2; **other divisions'
  markers survive BY COUNT** (dxfeed/tastytrade/mace/kalshi-arb/poly_kalshi + the driver block unchanged);
  `import trading_corp.main` compiles pre=0/post=0.
- **On failure:** restore from backup, do NOT restart.
- **Do NOT restart** — the engine restart is Jack's, after he warns co-tenants.

## 0. OUTCOME — M3 RESTORE COMPLETE + LIVE + VERIFIED (2026-09-06 ~19:23Z)
Applied 19:11Z (`pm_m3_apply`, Gate-A green, added=35/removed=0, MACE survives by count, NO restart). Jack
restarted the engine 19:14Z (**PID 208950 → 217030**, NRestarts=0). Post-check GREEN
(`pm_m3_postcheck` + follow-ups):
- **M3-specific:** `M3 shard-snapshot writer WIRED (2 account(s): [kalshi_jack, kalshi_karen]; 5-min timer)`.
  Both accounts now producing FRESH snapshots — **ages ~48h → <1 min**. jack total $480.81(stale) → **$486.23**
  (shard0 $122.82, shard3 $363.41); karen $460.36 → **$463.22** (shard0 $101.41, shard3 $361.82). shard-0-direction
  current again. ★ jack's FIRST cycle (19:17:43) hit a transient `Server disconnected` and was fail-soft SKIPPED
  (karen wrote fine); the display correctly showed jack's stale AGE (the design's whole point), and the next
  5-min tick (19:22:43) wrote jack clean — self-healed, no intervention.
- roster back: `{jack:[atp,mlb,ufc,wta], karen:[atp,mlb,ufc,wta]} skipped=[]`.
- **all 9 arm rows unchanged** from persisted ts (global 08-31; jack/karen mlb/ufc/atp/wta), 9/9 armed, 0 latched.
- boot-reconcile clean both (reconciled=True latched=False latched_categories=()).
- **liveness panel ALL 8 RUNNING, any_alarm=False.**
- every division back incl MACE (`config_hash=c382c9370f9b, 4 loops online`), bitunix, PEAD, Donchian, poly_kalshi,
  web command center.

### ★ NON-PM FINDING TO ROUTE TO MACE (not caused by this graft, not blocking) — noisy log bug in MACE's candle feed
Post-restart the engine logs ~25 tracebacks/min, ALL `TypeError: not all arguments converted during string
formatting`, from **`mace/candle_feed.py: run_feed` → `tastytrade/streamer.py` (DXLink websocket `_reader`)** — a
bad log-format string in the vendored `tastytrade` SDK exercised by MACE's Phase-2 candle feed. **Zero PM/shard
frames in any traceback; impossible to originate from the M3 block** (self-contained, MACE markers survived by
count). It is a **logging-only** exception (caught by `logging.handleError`, does not propagate → the feed keeps
running), so it is log SPAM, not a functional break — but it masks real errors and inflates error counts.
Pre-existing w.r.t. this graft (candle_feed unchanged). **Route to MACE**: patch/upgrade the tastytrade SDK or
raise the streamer's logger level. Not PM's to fix.

## 5b. BUILD STATUS — graft built + box-scratched GREEN (2026-09-06 ~19:04Z), HALTED for apply auth
- **Base (box):** `main.py` CR-stripped `236a6be054268278` (pure LF; raw==CR-stripped; pulled exact bytes, read-only).
- **Payload:** M3 block = reference `bba046e8` lines 1627–1660, 34 lines / 2558 bytes LF (sha16 `d3c784e6121574d9`),
  base64 in the runner (block carries em-dashes; base64 keeps the runner pure-ASCII).
- **TARGET after graft:** CR-stripped `408b2a415a1da18b` (raw==CR; +2559 bytes = block + 1 blank line).
- **Local proof:** byte-level splice proof (bytes before the anchor unchanged AND bytes from the anchor onward
  unchanged → ONLY the block+blank inserted); MACE + PM-driver markers equal by count; M3 markers 0→present;
  `prediction_markets` imports 1→2; local py_compile OK.
- **★ BOX-SCRATCH GREEN (runner `cc/pm_m3_scratch.{ps1,sh}`, writes only /tmp, live main.py untouched):** the
  box-side splice **reproduced TARGET `408b2a41` on the LIVE box file**; py_compile OK on the box venv; MACE
  survival grep -ic **tastytrade 20=20, mace 119=119, KalshiTailPriceArbAgent 2=2**; driver markers unchanged;
  M3 markers restored; PM module imports resolve on the box venv; live main.py still `236a6be0`.
- **Apply runner staged (GATED, `cc/pm_m3_apply.{ps1,sh}`):** drift-check live==`236a6be0` (ABORT on drift) →
  box-side splice hash-gated to `408b2a41` → backup `~/pm_m3_restore_backup_<TS>` → cp → verify applied==`408b2a41`
  → **MACE/base markers survive by grep -ic count backup-vs-applied (any deletion → restore+abort)** → diff shows
  added-only/0-removed → Gate-A (py_compile + PM-module import + import trading_corp.main, restore on break) →
  **NO restart.** Rollback on every failure branch.

## 6. POST-CHECK (M3-specific), after Jack's engine restart — runner `cc/pm_m3_postcheck.{ps1,sh}` (run ~5 min post-restart)
- `M3 shard-snapshot writer WIRED (2 account(s): [kalshi_jack, kalshi_karen]; 5-min timer)` in the boot log.
- The writer produces FRESH snapshots for BOTH accounts on its 5-min timer: `pm_shard_balance_snapshot`
  newest_age drops from ~48h to **minutes** for jack AND karen; new rows accumulating.
- The shard-0-direction line reads current again (return-to-3 continuously verifiable).
- Plus the usual: roster line back with four categories per account
  (`{jack:[atp,mlb,ufc,wta], karen:[atp,mlb,ufc,wta]}`), all **eight arm rows unchanged** from persisted values,
  boot-reconcile clean (reconciled=True latched=False both), the **liveness panel still all-RUNNING**, and every
  division back including MACE.
