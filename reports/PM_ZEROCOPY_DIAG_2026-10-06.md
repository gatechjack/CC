# PM DIVISION — ZERO COPY TRADES DIAGNOSIS (2026-10-06/07)

**Scope:** read-only diagnosis of why the Prediction Markets division placed zero Kalshi copy
trades across all four accounts for ~2 days. No fixes, no arm/disarm, no restart, no deploy.

**Branch:** `pm-zerocopy-diag-2026-10-06` (worktree off `b4b2c089`, the running engine code).
**Box:** `tc-prod-vm` (`azureuser`); all data pulled read-only (`sqlite mode=ro` + `journalctl` read).
Report time: **2026-10-07 02:06Z**.

---

## VERDICT (one line)

**Cause #4 — NOT RUNNING / DISARMED-VIA-LATCH.** Since the **2026-10-06 02:57Z** engine restart,
**all 92 sub-divisions are latched** (`armed=False, latched=True, auto_trigger=boot_reconcile_mismatch`)
from the **known-unfixed scalar-settlement defect**: an unbooked UFC settlement
(`KXUFCFIGHT-26SEP29BULVIS-VIS`) left a journal-vs-venue mismatch, and boot-reconcile fail-safe-latched
every scope on all four accounts. The driver is alive and seeing **abundant** signal (972/615/593/592
signals/cycle) but **gate 1 (disarm) rejects every one → placed=0, no order row** — a pre-submit
absence (§0.2 cause #2 mechanism) driven by the latch (§0.2 cause #4 state). **Not a signal drought.**
Action (re-arm + book the settlement) is **Jack's** — see Recommendations.

A **precursor** window (Phase 1, 10-02 19:50Z → 10-06 02:57Z) exists where entries had already stopped
while the driver ran healthy and un-latched; its precise sub-cause is INCONCLUSIVE (see Phase 1) but is
**moot** — the latch now blocks everything regardless.

---

## TIMELINE

| when (UTC) | event |
|---|---|
| 2026-09-29 22:33 | UFC `KXUFCFIGHT-26SEP29BULVIS-VIS` entries FILLED: jack 5, karen 5, marc 1, trey 1 (YES) |
| 2026-09-30 05:27 | that market resolves (Polymarket `cur_price=0.425`, won=0 — **non-binary / scalar-shaped**) |
| **2026-10-02 19:50Z** | **LAST copy entry fill** (jack cfb). All 4 accounts' last entry within a ~12-min window |
| 2026-10-02 ~19:50 → 10-06 02:57 | **Phase 1**: driver alive + polling hard, booking whale EXITs, but **zero new entries** |
| **2026-10-03 20:28Z** | **Polymarket `/positions` HTTP 429 storm begins** — 491,292 to date, still firing now |
| 2026-10-04 06:15–07:26 | last settlement-close bookings (is_exit=1); after this, book empties |
| **2026-10-06 02:56:24Z** | **engine restart** (MACE PT-held-dedupe deploy; platform-wide bounce) |
| **2026-10-06 02:57:01–03Z** | **boot-reconcile LATCHES all 92 subs** on all 4 accounts (`journal_only` UFC mismatch) |
| 2026-10-07 02:06Z | now: driver cycling, 972/615/593/592 signals, **placed=0, errors=0**, all subs latched |

Drought is **~4.3 days** (since 10-02 19:50Z), longer than the reported "~2 days".

---

## A. SIMULTANEITY (§0.4) — STOPPED TOGETHER ⇒ SYSTEMIC

Last entry fill (`pm_subdivision_order`, is_exit=0, filled, dry_run=0):

- jack 2026-10-02 19:50:05Z · karen 19:49:58Z · marc 19:38:40Z · trey 19:38:38Z — **all four within ~12 min.**
- Busy in-season categories all stopped the same day: cfb 19:50, cs2 18:29, mlb 14:15, itf 12:37, atp 05:39, nfl 02:26, mls 01:55.
- Fill buckets: **2d = 0, 3d = 0**, 7d = 263, 14d = 1021, 30d = 1818.

Four accounts + multiple in-season categories stopping together ⇒ **systemic**, not per-sub/seasonal.

## B. ARM + LATCH — ROOT CAUSE

`agent_state` (legacy db, actor `pm_live`): `arm:global` = **armed:true, latched:false**. All **92**
per-sub `arm:{acct}:{cat}` keys = **armed:False, latched:True, auto_trigger=boot_reconcile_mismatch**,
`updated_ts` **2026-10-06T02:57:01–03Z** (immediately after the 02:56:24Z restart). Identical reason on
every sub:

`boot-reconcile mismatch on kalshi_<acct>: 1 ticker(s) disagree: KXUFCFIGHT-26SEP29BULVIS-VIS j=5 k=0 [journal_only]`
(jack/karen j=5, marc/trey j=1, all k=0).

Journal confirms the latch event (live_driver.py:1316):
`pm_live_driver: boot-reconcile account=kalshi_jack reconciled=False latched=True latched_categories=(...)`
at 02:57:01–03 for all four accounts.

**Triggering position** (`pm_subdivision_order` WHERE ticker LIKE `KXUFCFIGHT-26SEP29BULVIS%`):
- 4 FILLED entries 2026-09-29 22:33 (jack 5, karen 5, marc 1, trey 1), `close_source=None`, `settled_ts=None`.
- **`is_exit=1` rows for this condition_id: 0** → the settlement close was **never booked**.
- `pm_closed_position` for the condition_id: `won=0, cur_price=0.425, resolved_ts=2026-09-30 05:27Z` —
  a **non-binary (scalar/refund-shaped) resolution**, exactly the class `book_settlements` skips
  (`market_result not in {yes,no,void}`), so no close row was written → journal still shows the position
  open (j=5/1) while Kalshi settled it to 0 (k=0) → boot-reconcile mismatch → fail-safe latch.

This is the precedent in §0.4 / `pm-settlement-close-path-backlog` and the 2026-09-22 jack+trey latch,
reproduced. Boot-reconcile is **boot-only** (live_driver.py:33) — it fires at each restart; the mismatch
sits harmless until a restart adjudicates it.

## C. DRIVER LIVENESS — ALIVE, CYCLING, ABUNDANT SIGNAL, 0 PLACED

- Engine `trading-corp` MainPID **642873**, ActiveState active/running, **ActiveEnterTimestamp 2026-10-06
  02:56:24Z**, NRestarts 0, WorkingDirectory `/home/azureuser/trading_corp`.
- **Running code = b4b2c089** (engine untouched): box `execution.py` csha16 `a8a03b59`, `live_driver.py`
  `4bbe3023`, `settlement.py` `a1abe0e3`, `arm.py` `b542e9ff` — all byte-match local b4b2c089; file mtimes
  2026-09-22 << boot 2026-10-06 (trap 7 satisfied). (`player_props_match.py` absent on box AND in b4b2c089
  — consistent, not a divergence.)
- `pm_driver_task_heartbeat`: all 4 accounts last_cycle **02:06Z (age 0.0h)** — loop alive.
- `pm_driver_heartbeat` (73 rows, all evaluated 02:06Z): per-account **union-across-categories** (trap 8):
  jack sum_sig **972** plc 0 err 0; karen **615**/0/0; marc **593**/0/0; trey **592**/0/0;
  `ceiling_latched=0`, `state=evaluated` everywhere.
- **Abundant signal, zero placed, zero errors = gate-1 disarm rejecting every signal** (all subs latched).

## D. SIGNAL SUPPLY — MLB-SEASON HYPOTHESIS KILLED

- Signal is **not** the problem now: 972/615/593/592 detected entry-signals/cycle across 22/17/17/17
  categories (f1 177, bun 162, lal 176, bra/sea 72, boxing 35, wnba 33, cfb 30, nfl 10, mlb 26, itf 6...).
- Historical fill mix (30d): nfl 483, cfb 329, mlb 323, itf 230, atp 92, cs2 87, mls 78, ufc 67…
  The categories that were actively filling and all stopped 10-02 — **cfb, nfl, itf, atp, cs2** — are
  **in season**. A seasonal MLB-end drought cannot explain in-season categories stopping together.
- Attachments: **156 active** (44 distinct wallets), vs baseline 71 — Jack has been actively attaching
  (attach events by `jack` as recent as 10-05 20:11Z), i.e. he expected copies. None of the four accounts
  detached wholesale.

## E. GATE STACK — OTHER SYSTEMIC SUSPECTS RULED OUT

- **Gate 1 (disarm):** FIRING on every signal (the latch). This is the active blocker.
- **Gate 2 (cap/contracts):** NO sub has effective per-contract ceiling < $0.99. Highest contracts =
  jack/itf 25 → $2.00; jack/mlb 15 → $3.33. ITF contracts **unchanged** from 09-27 (jack 25, karen 7,
  marc 5, trey 5), cap $50 on all. **Not it.**
- **Gate 6 (exposure):** approx open exposure $0.46–$2.90/account vs $350 cap. **Not it.**
- **Gate 6b (shard funding):** snapshot fresh (02:04Z); shards 0 & 3 funded on all four
  (jack 312/209, karen 275/196, marc 36/46, trey 34/49), shards 1 & 2 = $0 (baseline condition, not new).
  **No drain. Not it.**
- Gates 3/4/5/7/8: not reached in a quantity that matters — nothing is placed because gate 1 blocks first.

## F. ORDER PATH

- Last entry POST was 10-02 (fills + `no_fill`s through ~19:50; only **2 errors in 14d** — no 401/403
  storm). Order path was demonstrably **working** through 10-02.
- Since the 10-06 restart: **0 entry POSTs attempted** (latch blocks at gate 1 before any POST), so the
  order path is **UNPROVEN-since-restart, not broken**. (The "10-04 07:26 filled" is an is_exit=1
  settlement-close booking, not a copy.)

## G. WHAT CHANGED SINCE 2026-09-27 BASELINE

- Engine code: **unchanged** (b4b2c089, byte-identical). Schema head: **24** (no migration).
- Sub-divisions: **90 → 92** — +jack/boxing and +jack/f1, both created **2026-10-01 19:51Z**
  (fixed mode, `per_order_usd_cap` NULL — see secondary findings).
- Attachments: **71 → 156 active**.
- `per_order_usd_cap`: $50 on 90, NULL on the 2 new subs. `contracts` ITF unchanged.
- **Restarts:** the **only** engine restart since 09-28 is **2026-10-06 02:56:47Z** (one "MACE wired"
  marker; the MACE PT-held-dedupe deploy; PM engine code untouched). PID 619024 ran continuously through
  all of Phase 1 (booted before the 09-28 scan window) → **no restart during Phase 1**. The 10-06
  boot-reconcile latched **account-wide** (M3, live_driver.py:1311–1316): one `journal_only` UFC ticker
  per account → **every** category on that account latched, hence all 92 subs from a single position.
- pm_web `prediction-markets-web` PID 586747, boot 2026-09-27 23:44:22Z — unchanged from baseline.

## PHASE 1 (10-02 19:50Z → 10-06 02:57Z) — precursor, INCONCLUSIVE sub-cause

Entries stopped at 10-02 19:50 **before** the latch (which did not exist until the 10-06 restart;
boot-reconcile is boot-only and the engine did not restart in this window — journal shows no
boot-reconcile between 10-02 12:00 and 10-06 02:57).

Evidence the driver was **healthy** throughout Phase 1:
- `pm_shard_balance_snapshot` continuous: ~287–289/account/day every day 09-29→10-06; largest gap in 8
  days = **5.1 min**. No stall.
- Settlement closes (is_exit=1) booked through 10-04 06:15–07:26; `pm_subdivision_order` writes continued.
- Journal: **322,077** `pm_live_driver`/`prediction_markets` log lines in the window — constant
  Polymarket position polling and processing of whale **EXITs** ("confirmed whale-EXIT(s)… reductions…
  sells…", "OPPOSING-PAIR guard … new_cids=[]").
- By 10-06 02:54 the driver was hitting **HTTP 429** on `/positions` (Polymarket rate-limit); it
  paginates very deep for some whales (offset 5000+).

Finer structure: entries stopped **10-02 19:50**; the **429 storm did not begin until 10-03 20:28**
(~24.5h later); the restart/latch was 10-06 02:57. So neither the 429 nor the latch explains the initial
10-02 cliff. And the 429 only **degrades** — positions are still fetched through it (972 signals detected
post-restart despite 429s continuing), so it did not fully block detection either.

Best-supported read: during Phase 1 the attached whales were predominantly **exiting / net-closing**
(constant "confirmed whale-EXIT(s)… reductions… sells…", `new_cids=[]`), so there were few/no **new
copyable opens** to enter; nothing is placed and nothing is rejected-with-a-row. The precise sub-cause of
the 10-02→10-03 pre-429 cliff is **INCONCLUSIVE** from persisted state (signal-absence and silent
pre-submit absence both leave no row; `pm_driver_heartbeat` is latest-only; the per-cycle entry-decision
phrase was not isolable in the journal). It is a **separate** condition from the current latch and is
**moot** for "resume copies" — the latch blocks everything regardless, and once cleared, position
detection is demonstrably working (972 signals).

Note: a "0 confirmed whale-ENTRY / 0 new_cids" journal grep over the window is a **phrase mismatch**
(entries are not logged with that string — entries demonstrably occurred through 10-02), **not** evidence
of zero entries. Not relied upon.

---

## RECOMMENDATIONS (present only — every one is Jack's call; I did NOT act)

1. **Durable fix (root):** book the missing UFC settlement close so boot-reconcile stops seeing the
   `journal_only` mismatch, then re-arm. Until the scalar/refund settlement-close path is built
   (`pm-settlement-close-path-backlog`, ~10–15 lines), **every restart with an unbooked non-binary
   settlement will re-latch the whole account.**
2. **To resume copies now:** re-arm the latched subs (clears `latched`/sets `armed=True`; gate 1 then
   passes — the `journal_only` mismatch does not block live trading, only boot-reconcile). Note: without
   (1), the next restart re-latches. Re-arm is `pm_cli live-arm` — **Jack's action.**
3. Decide whether the booked position for `KXUFCFIGHT-26SEP29BULVIS-VIS` should be reconciled to the
   venue's settled value (j=5/5/1/1 → 0) as part of (1).

Kill switch (named only, not run): `PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py live-disarm --global`

## SECONDARY FINDINGS (one line each — not pursued)

- **Polymarket `/positions` HTTP 429 storm, ONGOING:** first 2026-10-03 20:28:21Z, **491,292** to date,
  still firing (02:22Z now, ~9k/hr). Driver paginates to offset 5000+ for large-book whales across 44
  attached wallets every cycle — heavy load, likely aggravated by attachment growth (71→156). Not the
  copy-drought root cause (positions still detectable), but a real, sustained degradation that will throttle
  copy latency even after the latch is cleared. Worth Jack's attention.
- `pm_open_position` (farm/analysis ingest table) **stale since 2026-09-28 17:38Z** (~200h); the live
  copy path is unaffected (it fetches positions live — 972 signals prove it), but the farm/UI view is stale.
- 2 new subs (jack/boxing, jack/f1, created 10-01) have **NULL `per_order_usd_cap`** (fixed mode, no cap set).
- Recurring ~01:12 tracebacks (10-04, 10-07) buried in the position-fetch storm — transient, not the copy path.

---
*Runners (read-only, logged): `pm_diag0..4.ps1`/`.sh` in `C:\Users\AA Incorporado\cc`. Evidence citations
are box pulls at 2026-10-07 02:02–02:14Z unless noted.*
