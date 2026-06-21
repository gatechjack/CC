# Post-restart verification — 2026-06-21 combined window

Run after `sudo systemctl restart trading-corp`. Do NOT un-halt bitunix until
all GREEN. If any RED → investigate; rollback path at the bottom.

## A. Engine booted clean
- [ ] `systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState` → new MainPID, ActiveState=active.
- [ ] `sudo journalctl -u trading-corp --since '3 min ago' | grep -iE 'Traceback|ImportError|ERROR'` → empty (no boot errors).
- [ ] `curl -fsS localhost:8000/healthz` → 200.

## B. Bitunix reconciles clean (watch the managed-exit path — issue1)
- [ ] `sqlite3 "file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro" "SELECT count(*) FROM paper_trade_record WHERE division='bitunix_futures' AND result IS NULL;"` → 0 (flat) OR a clean reconciled open.
- [ ] journal shows `position_state_reconciled` / no `position_state_divergence_detected`.
- [ ] bitunix still HALTED from our deploy flag (expected): `from_persistence('bitunix_futures').halted` → True.

## C. Issue #1 suppression ACTIVE (A/B vs the trade we just watched)
- [ ] On the NEXT bracketed live trade's exit: the managed virtual-exit dispatch does **NOT** fire and there is **NO new 20008 loop** in the journal (before-state = the 29b1610b/e9c35907 trades on the OLD code). Grep: `journalctl -u trading-corp | grep -iE '20008|managed.?exit|virtual.?exit'`.

## D. Metrics-epoch split active, epoch UNSET (intended)
- [ ] `sqlite3 ...?mode=ro "SELECT * FROM agent_state WHERE key='metrics_epoch';"` → **no row** (epoch stays unset until the D1 cutover).
- [ ] The live-winrate panel renders an **amber "epoch not set"** label, and paper vs live are **SPLIT** (not the old blended 30/64/66).

## E. PEAD read-layer live
- [ ] `sqlite3 ...?mode=ro ".tables" | tr ' ' '\n' | grep -E 'data_feed_status|scan_evaluation'` → both present (init_db created them at boot).
- [ ] `curl -fsS localhost:8000/telemetry/pead | grep -c PAPER` → ≥1 (page renders; unmissable PAPER pill, empty book, tri-state feeds).

## F. Known-present, NOT regressions (do not flag)
- [ ] D2 / D3 still present — expected, not fixed in this window.

## G. Re-arm + resume
- [ ] Re-arm a fresh D4 GUARD behaviour-watch (to catch post-manual-flatten).
- [ ] `bash unhalt_bitunix.sh` → bitunix entries resume.

## Rollback (if any boot/bitunix RED)
- PEAD: restore `*.bak-pre-pead-2026-06-21` (routes.py, db.py) + remove the 5 new files, restart.
- issue1: restore `paper_trade_replay.py.bak-pre-issue1-2026-06-21`, restart.
- metrics: restore `web/data.py.bak-pre-metrics-epoch-2026-06-20` (+ division.html backup), restart.
- bitunix stays halted until clean; un-halt only when resolved.
