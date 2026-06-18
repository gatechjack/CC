# Deploy-prep — bitunix /tpsl/ bracket rebuild (2026-06-18)

**Status:** PREPARED only. NO prod write, NO deploy, NO restart. Per CLAUDE.md §4.
**Branch:** `bitunix-tpsl-rebuild-2026-06-18`, rebuild tip **626e959** (steps 1-4 + path-fix; 651 tests green, zero regressions).
**Deployed base:** **7e7a2e1** (= `bitunix-bracket-exit-rebased-2026-06-17` tip = the 2026-06-17 bracket+E2.5 deployed state).
**Why:** Fixes the live-confirmed failure on trade `7d1a78dc` — SL-trail **404** + **~22 rejected managed TP exits** on the deployed bracket code (B1 entry-stop + auto-book saved that trade). The rebuild makes managed exits + the SL-trail actually work via the native BitUnix `/tpsl/` order family.

## Deploy set — derived from the diff `7e7a2e1..626e959` (NOT assumed)

3 production `.py` files:

| # | file |
|---|------|
| 1 | `trading_corp/brokers/bitunix.py` |
| 2 | `trading_corp/agents/divisions/bitunix_futures_observer.py` |
| 3 | `trading_corp/agents/divisions/bitunix_position_reconciler.py` |

- **`bitunix_bracket.py` is NOT in the diff** → confirms the build's "kept unchanged/pure"; NOT in the deploy set.
- Non-deploy files in the branch diff (excluded): `tests/test_bitunix_tpsl_rebuild.py`, `tests/test_bitunix_bracket_integration.py`, `reports/2026-06-18_tpsl_rebuild_session_handoff.md`.
- **Forbidden-file check: PASS** — none of main.py / db.py / models.py / logger.py / data_exec.py / cutover / polymarket are in the set.
- **Derivation caveat:** the `5caeb2f..626e959` sub-range alone *misses the reconciler* (it changed in `5caeb2f`, the steps-1-4 commit). The set must come from the base diff `7e7a2e1..626e959`. Confirmed.

## md5 table + drift result (verified 2026-06-18, read-only prod md5sum)

| file | base (7e7a2e1) | prod-current | target (626e959) | drift |
|------|----------------|--------------|------------------|-------|
| brokers/bitunix.py | `7a3da849cadfe32940649c9aba514ef3` | `7a3da849cadfe32940649c9aba514ef3` | `74aa1b424dcb73840f9f636151098348` | **NONE** |
| ..._observer.py | `13469b104894dfea0e727fe9a495c13d` | `13469b104894dfea0e727fe9a495c13d` | `19da15ff4401996ba31e50cf6f3d59a0` | **NONE** |
| ..._reconciler.py | `386cc6c243347dce65c60f55c3480ae6` | `386cc6c243347dce65c60f55c3480ae6` | `707c682858f40245d06aee9dc8f94e00` | **NONE** |

**DRIFT GUARD: PASS — prod == base for all 3.** Prod matches the verified 2026-06-17 bracket+E2.5 deployed state exactly; prod has not drifted since the rebuild branched off.

Cross-check: each base md5 (at 7e7a2e1) equals the 2026-06-17 bracket deploy's *target* md5 for that file — i.e. the rebuild branched off exactly what the bracket deploy installed. Apply blob is LF (repo blobs LF-clean; `autocrlf=true` affects working-tree checkout only).

## Pre-deploy baseline (read-only, for VERIFY reference)

- `MainPID=2926399`, `ActiveState=active`, `SubState=running`, `NRestarts=0`.
  - (Differs from the 2026-06-17 deploy PID 2923769 with NRestarts=0 → an operator restart occurred since that deploy. Expected; recorded so VERIFY confirms a *new* PID != 2926399.)
- ExecStart re-arm intact: `--live --brokers bitunix --live-divisions bitunix_futures` (bitunix_futures present).

## Execution flow (LATER — not now; operator is remote-mobile)

1. **Stage delivery** (agent drives scp over its own SSH): copy the staged tree to prod `$STAGE = /home/azureuser/trading_corp/_tpsl_rebuild_stage`, mirroring `trading_corp/...`. The 3 stage files are committed under `stage/` at target md5 (LF, pinned via `.gitattributes`).
   - Streamer (paste-safe): `scp -r deploy/2026-06-18_tpsl_rebuild/stage/* azureuser@trading.jacksumner.com:/home/azureuser/trading_corp/_tpsl_rebuild_stage/`
2. **Apply** (agent drives, no restart): `Get-Content deploy_apply_tpsl_rebuild_2026-06-18.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash"`
   - Gates: staged==target → preflight py_compile → drift guard prod==base (ABORT on any drift) → backup `*.bak-pre-tpsl-rebuild-2026-06-18` → md5-gated atomic mv → re-verify md5==target → py_compile all. NO restart.
3. **Restart** (operator, ONE step, az run-command — ssh+sudo does NOT work on this box):
   `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart trading-corp"`
4. **VERIFY** — run `VERIFY.md`.

## Rollback (operator)

```
cd /home/azureuser/trading_corp
for f in trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py; do mv "$f.bak-pre-tpsl-rebuild-2026-06-18" "$f"; done
```
then restart via az run-command (above). Restores the current safe-as-is bracket (B1 entry-stop + auto-book; managed exits stay non-working, as today).

## Validation-window flags (NOT deploy blockers — operator decides at validation; fail-soft, B1 guards)

These ride the **first post-deploy live entry**; they are validation-time, not deploy-time:
- **(a) B1 entry-stop vs the separate Position SL coexistence** — confirm the entry-attached B1 stop and the new managed Position SL coexist with **no 30038** rejection.
- **(b) 3-leg validation needs ≥ 0.0012 BTC** — the TIER_SIZING decision; below this the 3-leg TP ladder can't be fully exercised.

Both are fail-soft and B1-guarded; neither blocks the deploy.

## What confirms at restart vs needs a live trade

- **At restart:** engine up / new PID / 3 md5s==target / paper=False / `--live-divisions` has bitunix_futures / execution_mode:live / DD-cap 0.99 / B2 OFF / main.py+db.py md5 unchanged / reconciler clean / flat-no-orphan / staleness gate loaded.
- **Needs a live entry (the point of the rebuild):** TP legs REST as `/tpsl/` orders (`get_pending_orders` shows them, no 30038); SL-trail uses `/tpsl/position/modify_order` (NO 404); Position SL places + auto-reduces on a partial.
