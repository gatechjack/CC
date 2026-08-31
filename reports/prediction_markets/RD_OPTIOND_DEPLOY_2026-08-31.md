# Combined Option D + R-d DEPLOY -- LIVE + VERIFIED (2026-08-31)

Deployed in the pre-session quiet window (before first pitch 18:40 ET). Branch HEAD `c0d1176` (files) /
`e921c72` (docs). 5 steps, each on Jack's authorization; MIGRATION LED the restart.

## MANIFEST (4 engine files, hash-gated)
- `execution.py` 47676645.. (Option D exit path + F2 per-wallet + F1 entries-only seed)
- `live_driver.py` 97bb90ca.. (Option D exit wiring + R-d settlement-scan boot+throttle)
- `settlement.py` c4c2fd78.. (NEW, R-d)
- `db.py` d338c5ca.. (migration 015: close_source/realized_pnl/won/settled_ts)

## STEPS (all green)
1. Gate-1 DB backup: `~/pm_rd_deploy_backup_20260831T160923Z.db` sha 6c5b0783.. integrity ok (rollback).
2. 4 files: box==base -> shipped==HEAD -> per-file backup `~/pm_rd_deploy_files_backup_20260831T161414Z` ->
   copy -> hash-gate all MATCH -> 644 asserted -> Gate-A transitive imports OK -> py_compile OK.
3. Migrate: init_db -> schema 14->15, all 4 settlement columns present, order count unchanged.
4. Restart (Jack's restart_pmweb.ps1 + restart_tc.ps1): engine 107937->119559, pm_web 108138->119393. Bitunix bounced.
   Boot index build failed (Server disconnected -- known transient), then boot-reconcile ran.
5. Post-check (read-only) -- ★ boot_reconcile CLEAN (reconciled=True latched=False, 16:33:29).

## RESULT -- the settlement-drift gap RESOLVED on the way up
- Cubs settlement-close BOOKED: id=8, is_exit=1, filled, fill_count=1, fill_price=0.0, close_source='settlement',
  realized_pnl=-0.6084, won=0, wallet=SDTrading. journal_signed Cubs GONE; /live "Currently held" drops it.
- 6 OPEN positions remain, all +5, matching the venue exactly (boot_reconcile CLEAN confirms): SDCIN-CIN, DETMIN-DET,
  ATHTEX-ATH, PHIAZ-AZ, SDCIN-total-9, DETMIN-total-9. NO-leg fills = 0 (the -NO half stays inference-only).
- ARM armed=True latched=False -> R8 resumed before first pitch. schema 15.
- Process note: the FIRST post-check ran ~48s early (during the boot index-build) -> flagged as in-progress, read
  the driver log to confirm boot-reconcile clean, re-ran. Did not declare success on the premature read.

## STANDING / NEXT
- prod-live git NOT advanced (file-copy deploy to the box + migration, branch carries the ledger -- prior practice).
- TONIGHT (post-settlement): the shard-delta (return-to-shard-3 vs sweep-to-shard-0; baseline shard-3 $495.1893 at
  16:35Z -- note it drifted from the $499.5224 SHARD_PROCEEDS baseline as 2 more entries placed) + which of the 6
  won/lost + realized total. The R-d boot/periodic scan will BOOK tonight's settlements automatically (600s throttle,
  or the next boot); cross-check the booked realized vs the balance delta.
- Backups to keep (rollback path): the Gate-1 DB backup + the files backup dir. DANGEROUS to restore onto the live
  DB now (would revert schema 15 + drop the Cubs booking) -- disarm first if ever needed.
