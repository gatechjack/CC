# Phase 2 · CP6 — DEPLOYED LIVE + VERIFIED 2026-08-17 ~04:39 UTC

> **LIVE-MONEY STATUS (leads):** Phase 2a roster split is **LIVE**. `poly_kalshi_mlb` re-ARMED on the
> new code, reading the 4 whales from `live_whales`; the PCT paper farm is idle (`selected_whales`
> empty) and Telegram-silenced; the open BALTB-TB position rode the restart cleanly. No rollback.

## Deploy sequence (operator-run, all green)
1. **`pk_cp6_deploy.ps1`** — `BUNDLE_MD5 e6e7feaf39daa911aaf5ffd448f75808` OK; `DRIFT_GATE_OK` (11× MATCH,
   roster_split ABSENT); `BACKUP_SUFFIX .bak_cp6_20260817_043609`; `INSTALL_VERIFIED all 12`;
   `NO_RESTART … PID 760172 still OLD`; `SET_AGENT_STATE_MULTI_ON_DISK True`.
2. **`pk_cutover_seed.ps1`** DRY (confirmed the 4 wallets, live empty) → **`-Apply`**: `AFTER live=4 /
   selected=0 / pinned=0`, `INVARIANT_OK`, `CUTOVER_OK True`.
3. **`pk_cp6_restart_verify.ps1`** — restart at `2026-08-17 04:38:48`, online in 25s, **new PID 765455**:
   - **re-ARM:** `MLB copy WIRED (auto_execute=True -> dry_run=False, stake=$5.0, halt=$100.0)` +
     `loop online (poll=7.0s, dry_run=False)` + `ARM halted=False`.
   - **retarget live:** `CONFIG_RETARGET roster_actor=poly_kalshi_mlb roster_key=live_whales`;
     `LIVE_WHALES n=4` (the 4), `SELECTED n=0`, `PINNED n=0`.
   - **boot invariant:** `poly_kalshi roster invariant OK: 4 live / 0 paper, disjoint` +
     `INVARIANT_OK live=4 paper=0`.
   - **flag-3:** `OPEN_POSITIONS 1` (no re-order); dashboard `BALTB-TB` marking (yes_mid None / stale —
     EXPECTED pre-game, not yet quotable).
   - 0 `Traceback`/`CRITICAL`.

## The one anomaly — RESOLVED (my verify-grep bug, not a live issue)
`PAPER_TELEGRAM_CARDS_SINCE_RESTART 1` was a **false positive**: the verify used `grep -c "Polymarket
copy"`, which also matches the benign boot log `Polymarket copy trader scanner online`. Confirmed
read-only (`pk_cp6_telegram_check_ro.ps1`): the ONLY match was that boot line; the card signature
`Polymarket copy (ENTRY|EXIT)` grep was **empty**; the paper sim logged `no selected whales; no-op`
(04:41, 04:42). No paper card fired — impossible anyway with `selected_whales` empty. The `:5068`
Telegram kill is intact. **Fix applied:** `pk_cp6_restart_verify.ps1` grep tightened to
`Polymarket copy (ENTRY|EXIT)` (the actual card signature) for future honesty.

## Post-deploy state (box)
- Engine **PID 765455** on the CP2..CP5 code (12 files installed, LF-md5-verified).
- `agent_state`: `poly_kalshi_mlb/live_whales` = the 4 wallets; `polymarket_copy_trader/selected_whales`
  = `[]`; `pinned_whales` = `[]`.
- Backups `.bak_cp6_20260817_043609` retained on the box (the 11 modified). Rollback (if ever needed):
  `pk_cp6_rollback.ps1 -BackupSuffix .bak_cp6_20260817_043609 -CutoverWasApplied`.

## Remaining
- **prod-live-git catch-up** (git-only, no box) — the mirror lags (git `18db30e`; box now CP2..CP5).
  prod-live and the branch **diverged** (not a clean ff) → a direct deploy commit overlaying the runtime
  files + a `runbooks/deploy_log.md` hand-union (document-identity fork). Scoped separately; outward push.
- Shared byte-locked files (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`)
  untouched throughout Phase 2a.
