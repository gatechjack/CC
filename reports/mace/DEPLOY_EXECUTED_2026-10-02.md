# MACE leg-sanity guard — DEPLOY EXECUTED 2026-10-02 (post-close, Jack-authorized)

Board authorized atomic execution; ran as a gated sequence (each step verified before the next).

## Executed
| step | runner | result |
|---|---|---|
| graft | mace_legsanity_graft.ps1 | OK — drift-gate base==box; 3 files applied; config.py untouched; config_hash 931a8214be50 UNCHANGED; py_compile OK. Backup `~/mace_legsanity_graft_backup_20261002T200126Z`. Running engine untouched (still old PID 534581 until restart). |
| restart | restart_tc.ps1 | OK — az ProvisioningState/succeeded; **PID 534581 -> 619011**, boot 2026-10-02 20:08:06 UTC, active/running. |
| bootverify | mace_legsanity_bootverify_ro.ps1 | **GREEN** — config_hash 931a8214be50 UNCHANGED (wire line `4 loops online`); on-box md5s == target (strategy 50dd6984 / execution 5b2307bc / manager b0e9e879; config 01117cca); 4 MACE loops online; 0 ImportError; /mace HTTP 200. **0 tracebacks since restart** (the 1 since-midnight = pre-restart 03:47Z web error). |
| orphan adopt | mace_orphan_adopt.ps1 | OK — inserted `mace-XLE-2026-10-30-59.5-58-70-71.5-20260916` (open, 2x, credit 0.45, max_risk 210, pt_debit 0.23, entry_order_id 6aaaf21e, entry_iso_week 2026-W38). Pre-state backup `~/mace_orphan_adopt_pre_20261002T201653Z.json`. |
| stuck-rung reset | mace_xle_reset.ps1 | OK — `mace-XLE-2026-10-30-60-59-70-71-20260917` **closing -> open** (exit fields NULL preserved). Backup `~/mace_xle_reset_backup_20261002T201711Z.json`. |

## Post-deploy state
XLE 10-30 = 3 rungs, all **open**: 59.5/58/70/71.5 (orphan, adopted), 60/59/70/71 (reset), 60/58/69.5/71.5 (B).
Census: 16 open / 19 closed / 3 abandoned / 0 closing.

## Acceptance — DEFERRED TO MONDAY (market closed)
`management.window_et = ["09:35","15:55"]` + weekday-only (loops.py:118) -> `manage_tick` does NOT fire
post-15:55 ET or on weekends. It is Fri 2026-10-02 ~16:1x ET, so **the next manage tick is Mon
2026-10-05 ~09:35 ET**. A 15-min RO acceptance poll (mace_accept_ro) confirmed: reset rung stays `open`,
live mark frozen at 14:13:00Z (loop dormant), **0 rejects / 0 exit events -> NO re-loop**.

**Monday ~09:35 ET expectation (first tick):** the reset rung marks the corrupted ~0.135 -> PT-eligible
-> the live leg-sanity guard rejects (`mace_pt_mark_reject` reason=`leg_inversion` + "PT held" alert) ->
rung HOLDS open, NO `exit_error` loop. The adopted orphan (mark ~0.32 > PT target) is marked + managed
normally. Re-run `mace_accept_ro.ps1` (RO) Monday after the open to capture the proof.

## Weekend safety
Both the reset rung and the orphan are `open`, defined-risk (max $140 / $210), shorts well OTM (XLE
~62), ~28 DTE, and the manage loop is dormant -> no PT attempts, no close-error loop, no naked legs.
Resetting closing->open (vs leaving closing) is what makes Monday safe: a `closing` rung would re-drive
the empty-response loop at the open; an `open` rung hits the now-live guard.

## Ledger
Box-truth is now **branch mace-leg-sanity-guard-2026-10-02** (box runs strategy 50dd6984 / execution
5b2307bc / manager b0e9e879; config_hash 931a8214be50). Rollback:
`~/mace_legsanity_graft_backup_20261002T200126Z` (3 files) + restart.

## WRAP + PUSH (2026-10-02, post-deploy)
- Final RO state re-confirmed GREEN (mace_wrapconfirm_ro): PID 619011, LIVE, config_hash 931a8214be50,
  md5s == target, XLE 10-30 = 3 open rungs, 0 tracebacks since restart. ET 16:40 Fri.
- **FF-push mace-leg-sanity-guard-2026-10-02 (4ec0e46c) -> origin/prod-live: REJECTED (non-fast-forward).**
  origin/prod-live before = after = **b464b729** (pm-markpoller 9/27); it + the MACE branch diverged at
  2362db46 (prod-live carries PM commits the MACE line lacks). NOT forced. The fix is LIVE ON THE BOX but
  NOT on origin/prod-live -- advancing prod-live needs a merge/cherry-pick reconcile (Jack's call).
- jacks-log appended: reports/prediction_markets/jacks-log.md @ pm-docs-jackslog-2026-09-12 (bb37c6e6).
- MACE closed for the weekend. Acceptance deferred to Mon 2026-10-05 ~09:35 ET.
