# Stage 2 — ITEM 2 DEPLOYED LIVE 2026-08-19 10:34 UTC. Verified. Item 1 still pending.

Single-file deploy of `brokers/kalshi.py` (mark quote() fix), abort-safe, one restart. Agent-run under
Jack's "My go". Time 06:29 ET Wed — outside the 15:40-15:58 MACE window, no active games.

## DEPLOY (abort-safe; aborted-before-restart on any md5 mismatch — did not trigger)
- Local file md5 OK `7fb2688f`. Uploaded chunked base64 via `@file` (28256 chars, 1 chunk).
- **Drift-gate PASS:** `CUR_BEFORE_LFMD5 18626cf0` (box had the pre-fix baseline).
- Backup: `/home/azureuser/trading_corp/trading_corp/brokers/kalshi.py.bak_item2_20260819_103406`.
- **Install verified:** `INSTALLED_LFMD5 7fb2688f`.
- RESTART 10:34:06 UTC; **WIRED in 20s**; **engine 782881 -> 785523**.

## VERIFY AFTER RESTART
- **Re-armed (CRITICAL CHECK PASS):** `auto_execute=True -> dry_run=False`, `halted=False`, stake $5, halt $100.
- **Roster invariant:** `2 live / 4 paper wallet(s), disjoint` (matches MACE's boot-log format).
- **0 boot tracebacks** since restart.
- **Byte-locked 3 files byte-unchanged:** `kalshi_copy_trader af336db8` · `sports_team_mapping b715f341` ·
  `kalshi_live bbd851a6` (all == expected). Deployed `brokers/kalshi.py = 7fb2688f`.
- **⭐ ITEM 2 PROOF:** direct `quote(KXMLBGAME-26AUG212210PITLAD-PIT) = 0.345` — a real mid where the old
  code returned **0.0**. Fix works live. (Mark ticks in the journal are still the pre-restart 03:xx
  `open=1 marked=0 quote_miss=1`; a post-restart `marked>0` follows when the mark loop next runs against an
  open position on a quotable book. The direct quote() proof is definitive per the brief's fallback.)

## prod-live ADVANCE (git-only)
`4cf6eab -> fc78fc7` (clean FF): deployed blob `brokers/kalshi.py` + deploy_log entry. Temp worktree removed.

## ROLLBACK (if ever needed)
`powershell -ep bypass -f .\pk_item2_rollback.ps1` — restores `.bak_item2_20260819_103406` + restart.
Single file, no cutover/roster change — clean, no cross-dependency.

## NEXT — Item 1 is a SEPARATE deploy (staged, pending Jack's review)
Item 1 = `poly_kalshi_executor.py` + `mlb_poly_kalshi_match.py` (conflict gate, fail-closed). Box still has
the pre-fix versions (`d1f871f9` / `4b2a5c49`). Deploy on Jack's go, same pattern.
