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

## PHASE 4 — verify fills resume — BLOCKED on 3.2 (no subs armed yet)
Will run immediately after the armed set is restored: placed vs rejected per cycle (is_exit=0 only,
excluding the 4 settlement_scalar closes), first real fill as evidence, gate any non-placed signals die
at, and the attachment timeline (when the 71->156 landed).
