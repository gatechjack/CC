# P2 classifier — historical record correction (operator-gated, NOT applied)

**Date:** 2026-06-19 · **Status:** PREPARED, dry-run reviewed below; **apply = Board-gated, NOT run.**
LABEL-ONLY: corrects `result` + `exit_kind`/`autobook_level_type` on 2 records the P2 auto-book mis-signed.
**The PnL values (`actual_pnl_dollars`, `actual_r_multiple`, `net_realized_usd`) are CORRECT and are NEVER
touched.**

## Scope = exactly 2 records (both live)
| order_id | result old→new | exit_kind old→new | gross / net (UNCHANGED) | why |
|---|---|---|---|---|
| `e1758fc9` | loss → **win** | stop → **tp** | +0.03489 / +0.02443 | maker TP fill — fill 64478.8 == tp1 64478.83 |
| `7d1a78dc` | loss → **win** | stop → **tp** | +0.29822 / +0.26776 | favorable multi-fill TP-driven close (past tp1/tp2) |

## Dry-run finding (important — why NOT a blanket sign-flip)
A naive "result disagrees with PnL sign" rule flags **5** bitunix records, but **3 are paper `'expired'`
trades** (0 PnL, no-fill max-hold expiry) — `'expired'` is a **valid** non-PnL state, NOT a loss, and is
**out of scope**. The correction is therefore scoped to the **2 explicit live order_ids** (and guarded by
`result='loss' AND actual_pnl_dollars > 0`), never a blanket flip. `dryrun.sql` section B proves no other
`win`/`loss` record disagrees with its PnL sign.

## Operator procedure (all on prod `data/trading_corp.db`; agent does NOT run these)
1. **Review (read-only):** `sqlite3 -readonly …/trading_corp.db < dryrun.sql`
   → confirm section A shows the 2 records and section B returns **only** those same 2.
2. **Backup:** `sqlite3 …/trading_corp.db < backup.sql` → confirm `live_rows == backup_rows`.
3. **Apply:** `sqlite3 …/trading_corp.db < apply.sql` → the post-apply VERIFY must show both rows
   `result=win` / `exit_kind=tp`, with `gross`/`r` unchanged.
4. **Rollback (if needed):** `sqlite3 …/trading_corp.db < rollback.sql` → restores both rows from the
   backup table.

## Notes
- `apply.sql` is **idempotent** (the `result='loss' AND actual_pnl_dollars>0` guard means a second run is a
  no-op) and adds a `$.label_corrected` provenance marker recording the old labels.
- No engine restart is needed — this is a stats/record correction, not code. (The CODE classifier fix that
  prevents NEW mis-signs is a separate commit + redeploy.)
- The DB has a WAL; run while the engine is quiet if possible (the UPDATE touches only 2 rows by PK-ish
  guard, so contention is minimal, but a quiet moment is cleanest).
