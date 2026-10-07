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

## PHASE 2 — backup + book the close — PENDING AUTH
## PHASE 3 — re-reconcile, clear latch, re-arm prior set — PENDING
## PHASE 4 — verify fills resume — PENDING
