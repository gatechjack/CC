# MACE Stop-Hotfix — Build Report (2026-10-08)

**Status: BUILT, TESTED, BOX-SCRATCH-VERIFIED, COMMITTED — READY FOR JACK'S DEPLOY.** Nothing
deployed/restarted/pushed. Branch `mace-stop-hotfix-2026-10-08` off the deployed exit-redesign.

## The regression
The 2026-10-07 exit-redesign's stop TRIGGER used `QuoteSnapshot.stop_mark` (shorts@mid, wings
floored to bid-or-0). On a healthy illiquid-wing OTM condor this **fabricated** cost-to-close ≥ 2×
credit → a **spurious STOP** → unfillable (dead wing → RH "empty response") → the exit-reject
ladder. Live: `XLE 57.5/56.5P 67/68C`, credit 0.32, **spot 64.77 safely between the shorts**
(winning), plain mark None (dead wing) → `stop_mark ≈ short mids alone ≥ 0.64` → fired.

## The fix (strategy.py + manager.py; 2 files)
- **`stop_triggered(rung, mark, spot, m)`** (new, pure): the stop fires on the plain MID
  `mark ≥ stop_multiple × credit`; when the plain mark is UNPRICEABLE (dead wing) it falls back to a
  **structural signal ONLY — a SHORT ITM** (spot at/beyond a short strike). Cannot fire on a healthy
  OTM condor (both shorts OTM) → kills the spurious stop; still fires a genuine dead-wing stop (short
  ITM). Dropped the `stop_mark` kwarg from `evaluate_management`. (`QuoteSnapshot.stop_mark` retained
  but now unused-by-the-trigger; `stop_natural` stays as the marketable-EXECUTION price of a genuine
  stop — the bug was the TRIGGER, not execution. domain.py/execution.py unchanged.)
- **Self-heal** (manager `_drive_closing`): a STOP-class CLOSING rung whose stop is no longer valid
  on fresh quotes (position healthy again), credit present, no booked fill, no working order →
  **REOPEN to open**. Clears the already-wedged 57.5 rung automatically (+ any others spuriously
  stopped before deploy). Fetches its own mark+spot; the CLOSING branch stays before the tick
  snapshot (executors without `quote_snapshot` unaffected — this is what kept `test_mace_halt_button`
  green). exdiv/gap are structural and keep driving.

## Tests — GREEN
New: spurious-stop replay (mark None + shorts OTM → **no stop**), dead-wing-short-ITM fires,
`stop_triggered` units, manager spurious-stop CLOSING rung **self-heals (reopen)** + genuine-stop
keeps driving. Updated: the manager dead-wing-stop test → a genuine (short-ITM) stop; committed-
redrive→park → EXDIV (same cap path, not the stop self-heal). Three-incident replay + byte-parity +
ride/expiry all still green.
- **Local** py3.12: 4 pre-existing stale failures only, 0 new.
- **BOX-SCRATCH** (tc-prod-vm py3.12.13, `-p no:pytest_ethereum`, live tree untouched): full mace
  suite = the same **5 pre-existing stale** (config/sizing/strategy_entry×2/migration), **0 new**,
  **`test_mace_halt_button` GREEN**; the spurious-stop replay + hotfix suite pass; config_hash
  931a8214be50.

## Deploy (Jack runs; 2-file graft)
- Graft target md5s (box must BECOME): strategy **b30db63f** / manager **dc04eb60** (domain
  8522dc3a / execution 9cc1c165 / config d441957f UNCHANGED).
- Drift-gate (box must BE): the deployed exit-redesign — strategy b147e59d / manager ccd02002 /
  domain 8522dc3a / execution 9cc1c165 / config d441957f + mace.yaml 931a8214be50.
- **Code-only — config_hash 931a8214be50 UNCHANGED.** Box-only, no FF-push.
- Market-hours deploy (manage loop live 09:35–15:55 ET) → the self-heal + acceptance fire on the
  first manage tick after restart (no Monday wait).

One-line runners (Jack, in order):
```
powershell -ep bypass -f .\reports\mace\mace_stophotfix_graft.ps1
powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"
powershell -ep bypass -f .\reports\mace\mace_stophotfix_bootverify_ro.ps1
powershell -ep bypass -f .\reports\mace\mace_stophotfix_accept_ro.ps1
```
Acceptance = the wedged `XLE 57.5/56.5/67/68` rung reopens (`closing → open`, `mace_closing_reopen`)
and the exit_rejected/"empty response" reject loop stops.
