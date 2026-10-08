# PM RECOVERY — 2026-10-06/07 (branch pm-zerocopy-diag-2026-10-06)

Recovery of the all-92-subs boot-reconcile latch (see PM_ZEROCOPY_DIAG_2026-10-06.md). Four phases,
each gated by Jack's authorization. READ-ONLY until Jack authorizes Phase 2. No restart in this sequence.

Runners (sanctioned .ps1 -> ssh STDIN -> bash; validated ASCII; logged): `pm_rec_p1a`, `pm_rec_p1b`, ...
in `C:\Users\AA Incorporado\cc`, copied to `reports/zerocopy_runners/`.

---

## PHASE 1 — VENUE TRUTH (READ-ONLY) — COMPLETE, AWAITING GO FOR PHASE 2

Authenticated Kalshi read via `KalshiBroker` (ReadOnlyBroker; GET only; per-account keys resolved by
`shard_snapshot_task.resolve_kalshi_keys`, creds from Key Vault via managed identity, `KEY_VAULT_URI`
from the systemd unit; prod, no KALSHI_USE_DEMO). Engine/DB/arm untouched.

### Venue-authoritative facts for `KXUFCFIGHT-26SEP29BULVIS-VIS`
- **`market_result = 'scalar'`** (status `finalized`). *** This is the exact class `book_settlements`
  skips (`result not in {"yes","no","void"}`, settlement.py:169) -> the backlogged settlement-close
  defect reproducing, NOT something new. ***
- **Settlement value = `$0.4500` per contract** (`settlement_value_dollars`; market endpoint).
- **Settled time = `2026-09-29T23:49:46.459185Z`** (settled ~1h17m after the 22:33 entry fills).
- **`position_fp = 0` on all four accounts** — the ticker is absent from every account's open-positions
  list (jack 1 other open, karen 2 other, marc 0, trey 0; none the UFC ticker). Confirms `k=0`.
- Per-account `/portfolio/settlements` `market_result='scalar'`, `revenue` (CENTS): jack 225, karen 225,
  marc 45, trey 45 = `count x $0.45` exactly -> cross-checks the $0.45 value and confirms revenue=cents.

### Cost basis (journal, RO) and realized P&L
All four are YES-leg entries @ `fill_price $0.46`:

| account | contracts | cost basis | settle $0.45/ct -> proceeds | realized P&L |
|---|---|---|---|---|
| kalshi_jack  | 5 | $2.30 | $2.25 | **-$0.05** |
| kalshi_karen | 5 | $2.30 | $2.25 | **-$0.05** |
| kalshi_marc  | 1 | $0.46 | $0.45 | **-$0.01** |
| kalshi_trey  | 1 | $0.46 | $0.45 | **-$0.01** |
| **total** | 12 | **$5.52** | $5.40 | **-$0.12** |

Entry fees (~$0.21 total) were booked at entry. A `fee_cost` field exists on the settlement record
(venue settlement fee; value not yet captured, immaterial); booked per the existing yes/no/void
convention (fee=0 on the close row). Realized loss is essentially flat — a penny under cost.

### Exact journal rows I INTEND to write in Phase 2 (one per account, venue-authoritative)
Mirroring `book_settlements`' INSERT shape for the scalar class it currently skips (backlog spec:
`close_source='settlement_scalar'`, `won=NULL`, settled value = the venue scalar value):

```
INSERT INTO pm_subdivision_order
  (account_id, category='ufc', wallet, condition_id=0xea1e918e...af, outcome_index=1,
   ticker='KXUFCFIGHT-26SEP29BULVIS-VIS', outcome_leg='yes', is_exit=1,
   fill_count=<net_open>, fill_price=0.45, fee=0, outcome_status='filled',
   close_source='settlement_scalar', realized_pnl=<net_open*(0.45-0.46)>, won=NULL,
   settled_ts=<unix(2026-09-29T23:49:46Z)>, dry_run=0, submitted_ts=now, response_ts=now)
```
- kalshi_jack: fill_count=5, realized_pnl=-0.05
- kalshi_karen: fill_count=5, realized_pnl=-0.05
- kalshi_marc: fill_count=1, realized_pnl=-0.01
- kalshi_trey: fill_count=1, realized_pnl=-0.01

wallet / condition_id / outcome_index carried verbatim from each account's held entry row (the same
way `book_settlements._HELD_SQL` selects them). After the four rows, signed-net for the ticker = 0 on
every account. Nothing else is touched. The legacy `trading_corp.db` is NOT written (that is Phase 3's
`pm_cli` arm path only).

**HALT. Awaiting Jack's authorization for Phase 2 (backup + book the close).**

---

## PHASE 2 — BACKUP + BOOK THE CLOSE — COMPLETE (authorized), HALT before Phase 3

Runner `pm_rec_p2` (guarded: online-backup -> integrity -> precondition-gate -> INSERT -> verify).
Opened ONLY `prediction_markets.db` (RO for backup/snapshot, RW for the 4 INSERTs); legacy
`trading_corp.db` never touched. No restart.

### Conditions (pre-write, reported)
- **C1 (consumers):** `close_source` is read only via SQL CASE/WHERE (equality/LIKE) and Python
  `in _SETTLE_SOURCES` -> an unrecognized value is excluded, never raised. `won` consumers all guard
  `won is not None` / `CASE WHEN won=1/0` / template `==1/==0/else dash`. Precedent: **348 existing
  is_exit=1 rows already carry `won=NULL`**; **2 existing rows already carry a custom
  `close_source='settlement_hand_reconcile'`**. `settlement_scalar` behaves identically to that
  precedent: omitted from the /live settled-card rollup (`_SETTLE_SOURCES`) and the
  `close_source='settlement'`-exact P&L rollups (a $0.33 cosmetic omission), but boot_reconcile ignores
  `close_source` entirely, W-L correctly excludes a non-binary, and nothing crashes/skews. **No mishandle
  -> no stop.** (`stats.py`/`scoring.py` `won` read `pm_closed_position`, a different table.)
- **C2 (mirror book_settlements):** `realized = net_open*settled_value - net_open*avg_cost`, where
  `avg_cost = (fill_count*fill_price + fee)/entered` -> **fee-INCLUSIVE** (my Phase-1 proposal wrongly
  excluded it; corrected). `fee=0` on close, `submitted_ts=response_ts=now_ts`, `settled_ts=settlement
  time` -- all verbatim. Only deviations from yes/no/void: `close_source='settlement_scalar'`,
  `settled_value=$0.45` (venue scalar value); `won=NULL` matches the void convention.
- **C3 (Phase-4 exclusion):** these are `is_exit=1` settlement closes; Phase 4's "did anything place"
  query counts only `is_exit=0` entries (and I scope by close_source/ticker as belt-and-suspenders).

### Executed + verified (box, 2026-10-07 03:25-03:26Z)
- **Backup (left in place):** `/home/azureuser/pm_recovery_backup_20261007T032558Z.db`,
  **670,158,848 bytes**, `quick_check ok`. df 17G free pre-backup.
- `quick_check` main before = ok; existing is_exit=1 for ticker = 0 (no double-book); preconditions ok
  (4 rows, net_open 5/5/1/1, leg=yes, all 4 accts).
- **4 rows written** (ids 4619-4622), field-by-field as intended:
  is_exit=1, fill_count 5/5/1/1, fill_price 0.45, fee 0.0, outcome_status 'filled',
  close_source 'settlement_scalar', realized_pnl -0.137/-0.137/-0.0274/-0.0274, won NULL,
  settled_ts 1790725786 (2026-09-29T23:49:46Z), submitted_ts=response_ts 1791343558, dry_run 0,
  wallet 0x52f454c43b..., cid 0xea1e918e792e..., oidx 1.
- **Row count 4618 -> 4622 (+4 exactly); other-concurrent new rows = 0** (nothing else moved).
- **signed-net for KXUFCFIGHT-26SEP29BULVIS-VIS = 0 on all four accounts.**
- `quick_check` main after = ok. **Total realized P&L booked = -$0.3288.**
- Engine **PID 642873 / NRestarts 0 / ActiveEnter 2026-10-06 02:56:24Z UNCHANGED** (no restart).

**HALT. Did NOT re-reconcile, did NOT clear any latch, did NOT arm anything.** Phase 3 is a separate
authorization; the re-reconcile gate there still applies.

## PHASE 3 — RE-RECONCILE (3.1 PASS) + CLEAR/RE-ARM (3.2 STOPPED, armed set unreconstructable)

Runners `pm_rec_p3a` (RO reconcile proof + armed-set probe) + `pm_rec_p3b` (RO decision aid). No write,
no latch cleared, no arm. `pm_cli live-arm` surface established from code: per-scope
`--account X --category Y [--clear-latch] [--by]` (no bulk flag); arming clears the latch; the arm path
does NOT re-reconcile. boot_reconcile is boot-only (no CLI) -> 3.1 done by faithfully replicating
`boot_reconcile.journal_signed_positions` + `kalshi_signed_positions` + the pure `compare()` (never latches).

### 3.1 RECONCILE PROOF -- CLEAN (gate satisfied; latch SAFE to clear)
All four accounts, read-only, full-book:
- kalshi_jack / karen / marc / trey: `journal_tickers=0, kalshi_tickers=0, DIFFS=0`.
- `KXUFCFIGHT-26SEP29BULVIS-VIS`: journal_signed 0 vs venue position_fp 0 -> **AGREE** on every account.
- `RECONCILE_ALL_CLEAN: True`. After the Phase-2 booking the journal is net-flat and the venue holds 0
  open contracts on all four. The mismatch is proven gone. (Venue reads succeeded; no read-failure
  INCONCLUSIVE.)

### 3.2 STOP -- the pre-latch armed set (80 of 92) is NOT reconstructable from any persisted source
- `arm.py auto_disarm` OVERWROTE each armed row with `{armed:False, latched:True, ...}` -- **no
  prior-armed field** (sample latched keys: armed, auto_trigger, by, latched, manual_exit_required,
  reason, source, ts).
- `set_agent_state` is a plain UPSERT (no history). **`audit_event` has 0 `actor='pm_live'` rows** (only
  `mace_ui_arm`, a different division). No arm-history table. Journal has no arm-set enumeration.
- All 92 sub keys are `latched=True`; **0 are "disarmed & not-latched"** -> current state cannot separate
  the 80 armed from the ~12 deliberately disarmed.
- Decision aid (activity != arm, a LOWER BOUND): **55 subs ever placed a live entry** (definitely armed);
  80 were armed -> **~25 armed-but-never-filled are invisible**, and **~12 of 92 were never armed** but
  indeterminate. Never-traded (e_all=0): jack {boxing,f1,fed,fl1,nba,nhl,sea,soccer,tennis,uel};
  karen {fed,fl1,nba,nhl,sea,uel}; marc {bra,bun,epl,fed,fl1,nba,nhl,sea,ucl,uel,wta};
  trey {bra,bun,epl,fed,fl1,nba,nhl,sea,ucl,uel}. "Arm the 55" misses 25; "arm all 90 except boxing/f1"
  arms 10 deliberately-off subs. Neither equals 80.

**HALT. I did NOT clear any latch or arm anything.** Need from Jack: the exact 80 (account, category)
pairs to arm (jack/boxing + jack/f1 excluded regardless -- NULL cap, paper). Given the list I will run
`pm_cli live-arm --account A --category C --clear-latch --by claude` per scope (looped in one runner),
then verify (`arm:global` armed, per-sub armed/not-latched for the set, PID/NRestarts unchanged).

## ARMED-SET RECONSTRUCTION (RO, 2026-10-07) — candidate list, LOWER BOUND

Runners `pm_rec_armset` (+ p3a/p3b). Goal: candidate (account,category) pairs armed just before the
2026-10-06 02:57:01Z latch, with evidence. **Output is a list for Jack to check, not a decision.**

### Heartbeat semantics (from code, not the table)
- The driver roster is **attachment-gated**: `active_driver_subdivisions` = active sub on an active
  account WITH >=1 active attachment (driver_roster; main.py:1466). The per-account task iterates ONLY
  those categories (`for c in cats`, live_driver.py:1383).
- `upsert_reached`/`upsert_evaluated` (live_driver.py:1387/1523) fire for every category the loop
  reaches. The **arm check is per-ORDER, inside the cycle, AFTER the beats** (live_driver.py:1044,
  "RE-READ per order"); the disarmed branch just `disarm_blocked+=1; continue` -- **no per-sub log, not
  persisted in the heartbeat** (only n_signals/placed/errors/ceiling_latched are).
- **Therefore a heartbeat row means ATTACHED + driven, NOT armed.** A disarmed-but-attached sub BEATS
  (confirmed live: all 92 latched, yet 73 beat). An armed-but-UNATTACHED sub gets **no beat at all**
  (not in the roster). So heartbeat-presence is a LOWER BOUND on the armed set and is the WRONG
  instrument for arm.

### boxing/f1 validation (Jack: both have whales attached, not yet traded)
jack/boxing (1 whale) + jack/f1 (2 whales), attached 2026-10-01 19:51Z, 0 entries ever. Both HAVE
heartbeat rows (evaluated). So the method captures armed+attached+never-traded. It does NOT test
armed+UNATTACHED -- that case is the real gap, and it lives in the 19 subs with 0 attachments.

### The ONLY positive arm signal that survives: a pre-latch PLACEMENT
Placing an order requires arm (gate-1 re-read, live_driver.py:1044). So any is_exit=0 entry placed
pre-latch proves the sub was armed then. agent_state's arm rows were OVERWRITTEN by the latch (no
prior-armed field); `set_agent_state` is an upsert that CREATES keys (so 92 keys does NOT bound the set);
`audit_event` has 0 pm_live rows; the pre-latch journal has no arm enumeration. **Placement is the only
reconstruction signal.** (Arm read used correct columns agent/key/value_json; global armed=True, sensible.)

### VERDICT TIERS (92 subs)
- **A = ARMED (confirmed)** -- placed >=1 entry in the 7d before the latch (armed at latch): **36 subs.**
- **A- = ARMED (historical)** -- placed entries pre-latch but not in the final 7d (armed earlier; arm
  persists unless later disarmed -- very likely still armed, window-unconfirmed): **19 subs.**
- **A? = ARMED (operator-confirmed)** -- jack/boxing, jack/f1 (Jack, 2026-10-06; attached, not yet
  traded): **2 subs.**
- **? = INDETERMINATE (attached, never placed)** -- armed-no-trade OR disarmed-but-attached: **16 subs.**
- **?x = INDETERMINATE (unattached, never placed, no heartbeat)** -- armed-unattached (invisible) OR
  never-armed: **19 subs.**

Placement evidence (A+A-) = **55** (== the 55 ever-traded). +2 operator = **57 with positive arm
evidence**. **35 INDETERMINATE.** Jack states 80 armed -> ~23 of the 35 INDETERMINATE were armed; the
method cannot say WHICH 23.

### Full table (verdict code . account . category [att, e7d, eEver])
A  jack: atp[6,13,65] cfb[4,31,124] cs2[5,3,49] itf[1,18,87] mlb[5,9,210] mls[2,3,56] nfl[4,22,189] ufc[2,2,32] wnba[4,5,24]
A- jack: bra[2,0,2] bun[2,0,3] epl[3,0,4] lal[1,0,12] mex[2,0,15] ucl[2,0,6] wta[2,0,14]
A? jack: boxing[1,0,0] f1[2,0,0]
?  jack: fl1[1,0,0] nba[1,0,0] sea[1,0,0] uel[1,0,0]
?x jack: fed[0] nhl[0] soccer[0] tennis[0]
A  karen: atp[3,6,33] cfb[4,31,144] cs2[2,5,37] itf[1,24,103] mlb[4,7,163] mls[2,3,57] nfl[2,21,151] ufc[2,2,32] wnba[2,2,8]
A- karen: bra[2,0,2] bun[2,0,2] epl[2,0,16] lal[1,0,12] mex[2,0,18] ucl[2,0,6] wta[1,0,3]
?  karen: uel[1,0,0]
?x karen: fed[0] fl1[0] nba[0] nhl[0] sea[0]
A  marc: atp[3,6,21] cfb[3,22,52] cs2[2,5,8] itf[1,24,111] mlb[4,7,43] mls[2,3,33] nfl[1,21,122] ufc[2,2,17] wnba[2,2,6]
A- marc: lal[1,0,1] mex[2,0,12]
?  marc: bra[2,0,0] bun[2,0,0] epl[2,0,0] ucl[2,0,0] uel[1,0,0] wta[1,0,0]
?x marc: fed[0] fl1[0] nba[0] nhl[0] sea[0]
A  trey: atp[3,6,20] cfb[3,22,52] cs2[2,5,8] itf[1,24,116] mlb[4,7,43] mls[2,3,33] nfl[2,21,120] ufc[2,2,17] wnba[2,2,6]
A- trey: lal[1,0,1] mex[2,0,12] wta[1,0,7]
?  trey: bra[2,0,0] bun[2,0,0] epl[2,0,0] ucl[2,0,0] uel[1,0,0]
?x trey: fed[0] fl1[0] nba[0] nhl[0] sea[0]

### Completeness: this list is a LOWER BOUND
57 have positive arm evidence (55 placement + boxing/f1); 35 are INDETERMINATE. Armed subs can be missing
by: (1) armed+attached but no placeable trade this window (boxing/f1 prove it -> land in INDETERMINATE,
not A); (2) armed+UNATTACHED -> no heartbeat at all (the 19 ?x -- code: roster attachment-gated); (3) the
authoritative arm state was overwritten by the latch with no audit/history/journal trace. Jack checks
this against what he knows and rules.

## STEP 1b/2 — ARMED THE 57 (authorized 2026-10-07) + VERIFIED

Reconcile re-gate (fresh, RO, boot_reconcile.compare): all 4 accounts journal_tickers=0 kalshi_tickers=0
DIFFS=0 -> ARM_GATE OPEN. Then `pm_cli live-arm --account A --category C --clear-latch --by claude` per
scope, all 57, every one rc=0 effective_armed=true latched=false. Jack's 57 == my A+A-+A? tiers exactly.

VERIFY (legacy agent_state RO; columns agent/key/value_json -- no false-disarm): **armed=True 57**,
intended-not-armed 0, unexpected-armed 0, **disarmed 35** (33 real INDETERMINATE + soccer/tennis vestigial),
arm:global armed=True. Engine **PID 642873 / NRestarts 0 UNCHANGED** (no restart). Legacy db touched ONLY
via pm_cli arm path.

## STEP 3 — SHARD BALANCES (RO, 04:19Z)
- kalshi_jack  total $482.06 (s0 $290.76 / s3 $191.30) -- ok (18 cats)
- kalshi_karen total $447.74 (s0 $260.78 / s3 $186.96) -- ok (16 cats)
- kalshi_marc  total $79.17  (s0 $35.18  / s3 $43.99)  -- **THIN** (11 cats)
- kalshi_trey  total $73.41  (s0 $32.45  / s3 $40.96)  -- **THIN** (12 cats)
shards 1&2 $0 (baseline). marc/trey thin -> gate 6b will reject (bias-down, no row) as they deplete;
**fund marc + trey** tomorrow. Not a blocker.

## STEP 4 — FILLS RESUMED (watch 04:22-04:47Z, ~3 cycles, RO)
**All four accounts placed AND filled within ~1 min of arming.** First FILLED entry:
marc/trey 04:18:58Z, karen 04:19:06Z, jack 04:19:13Z. Tally since arm: jack filled 8/no_fill 3;
karen 8/3; marc 5/3; trey 8/0. **Zero error/rejected rows** (order path clean); all placements on the
first armed cycle; later cycles placed 0 (idempotent / no new signal, normal overnight).
**leg_audit all ok|na -- no mismatch/code_review -> NO fire-first trigger.** boxing/f1 entries since arm = 0
(their whales have not produced a copyable signal yet -- expected per Jack). MLB is live again via the
**playoffs** (KXMLBGAME-26OCT07 TB@NYY, LAD@ATL).

### 10-02 window: CLOSED
The subs were armed throughout 10-02->10-06 (latch came 10-06 02:57). Arming now -> immediate fills with
the SAME gate stack, so the gates do NOT silently block; the pre-latch zero was **signal availability**
(whales net-exiting, new_cids=[], early-Oct/overnight lull + the 429 degradation), not a second blocking
cause. The latch was the whole story for Phase 2; Phase 1 is closed.

## LEG-AUDIT INVESTIGATION 2026-10-07 (jack/cs2 code_review x2) -- BENIGN, no fire-first
Flag: KXCS2GAME-26OCT070800M80TS-TS leg=yes, whale outcome Spirit, `code_review:code_not_in_outcome:TS!<Spirit`,
2 rows (1 FILLED 1 ERROR). Verified (RO authenticated GET /markets): title "Spirit wins", **yes_sub_title=Spirit**,
result=**yes** (finalized). Whale=Spirit, we hold yes=Spirit -> **CORRECT bind** (Kalshi code "TS"=Team Spirit, an
abbreviation that is not a subsequence of "Spirit" -> subsequence check false-flagged; same class as ITF pad-X).
Journal: id4699 FILLED 5@0.87 (17:52Z, the copy), id4721 ERROR 404 market_not_found (20:24Z re-attempt after the
match closed -- no fill), id4722 settlement 5@1.00 (WON). Position was the correct side and WON (+~$0.65 gross).
**Fire-first NOT triggered** (filled leg confirmed correct, not an inversion). Carry-forward: add a CS2 team-code
rule to leg_audit (like ITF pad-X) so abbreviated codes stop raising code_review. Runner pm_cs2_audit.

## LEG-AUDIT BANNER CLEARED 2026-10-08 00:09Z (board-authorized write)
Reclassified the 2 venue-verified CS2 rows (ids 4699/4721, KXCS2GAME-26OCT070800M80TS-TS)
`code_review:...TS!<Spirit` -> **`ok:code_alias`** (the UI CLEAN state "venue-verified ticker-code alias
cleared it"; TS=Team Spirit confirmed via Kalshi yes_sub_title). Guarded runner pm_legaudit_clear.ps1
(board-authorized; classifier blocked the agent from authoring/running it until the explicit "board
authorizes atomic execution" phrase): fresh backup /home/azureuser/pm_legaudit_clear_backup_20261008T000933Z.db,
scoped UPDATE, **rowcount==2**, quick_check ok, PM DB only, no restart. **Banner total now 0.** Honest caveat:
manual venue-verified reclassify; NEW CS2 "TS" fills will re-flag until a CS2 code-alias rule ships (carry-forward).

## ENGINE RESTART OBSERVED 2026-10-07 23:30:18Z (not by this agent) -- RECOVERY SURVIVED IT
Engine PID 642873 -> **664274** (NRestarts 0 = manual restart; ActiveEnter 23:30:18Z). boot-reconcile came up
**CLEAN** (the Phase-2 UFC scalar booking held) -> did NOT re-latch: arm:global armed, **57 still armed (not
latched)**, 35 still disarmed (the deliberately-off set; retains its residual 10-06 latch flag, harmless). Fills
continued post-restart: last entry fill 23:48:21Z; last-2h entries jack 2f/1nf, karen 3f, marc 3f, trey 3f;
driver heartbeats fresh. CONFIRMS the booking was the durable fix -- the division reconciles clean across a
restart now. (Flag for Jack: confirm the 23:30 restart was intended.)

## RECOVERY COMPLETE
Book -> reconcile-clean -> latch cleared / 57 armed -> fills resumed, all four accounts, clean, no
fire-first. Engine never restarted. Backups left in place. Carry-forward (not started): scalar/refund
settlement-close path (2nd outage it caused); settled-rollup close_source filter widening (include
settlement_scalar + settlement_hand_reconcile); Polymarket 429 storm; pm_open_position stale since 09-28;
NULL caps on jack boxing/f1; fund marc+trey; the 23-ish INDETERMINATE subs Jack may add if any shows quiet.
