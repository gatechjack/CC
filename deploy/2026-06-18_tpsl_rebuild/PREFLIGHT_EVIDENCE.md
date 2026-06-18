# Pre-flight evidence — tpsl-rebuild deploy (2026-06-18, ~22:44 UTC)

All read-only (md5sum, systemctl show/cat, journalctl read, sqlite mode=ro). No prod write.

## Engine state — PASS
- `MainPID=2926399` — UNCHANGED from prep (no restart since prep), `active/running`, `NRestarts=0`.
- Boot 2026-06-17 23:44:49 (operator restart ~24m after the 23:20 deploy; explains PID vs memory's 2923769).
- `BitunixBroker connected (account=bitunix-futures, equity=$259.79, 0 positions)` at boot → REAL broker, paper=False.
- ExecStart re-arm intact: `--live --brokers bitunix --live-divisions bitunix_futures`.

## execution_mode=live — PASS
Last bitunix_futures filled orders all `execution_mode=live` (06-18 01:39 onward); 06-17 pre-23:20 entries were `paper` (the cutover paper-wrap, fixed by the 23:20 re-arm). Confirms live arming holding.

## Staleness gate firing — PASS
`bitunix_futures/entry_rejected_stale_bar` repeatedly through the day (12:59, 13:08, 13:20, 13:26, 14:53, 18:38) — gate loaded + rejecting stale redeem entries.

## CLEAN-FLAT — PASS (airtight)
- `position` table COUNT(*) = **0** → flat, no tracked open position, no orphan.
- Last bitunix entry: 2026-06-18T15:18:56 (BTC/USDT.P sell 0.000762, filled 15:18:58, live) = trade 7d1a78dc.
- 7d1a78dc closed ~20:35: reconciler `_halt_new_orders RELEASED (two consecutive clean reconcile ticks)` 20:35:34; NO bitunix exit/trail/fill activity since → flat ~2h.
- No active halt / divergence / orphan in last 2h.

## Live-confirmed failure VISIBLE (the deploy's justification)
- `modify_position_sl failed … BitUnix API error 404 … /api/v1/futures/tpsl/modify_position_tp_sl_order` repeated 20:25–20:33 (the SL-trail 404).
- `bitunix_futures/live_exit_order_rejected` ×many for `7d1a78dc-…-exit-tp`, 19:46–20:32 (the rejected managed TP exits).
- B1 entry-stop + auto-book + P2 self-resume saved it. This is exactly what the rebuild fixes.

## Drift re-check — PASS (prod == base, NO drift)
| file | prod-current | base | target |
|---|---|---|---|
| brokers/bitunix.py | 7a3da849cadfe32940649c9aba514ef3 | =base | 74aa1b424dcb73840f9f636151098348 |
| observer | 13469b104894dfea0e727fe9a495c13d | =base | 19da15ff4401996ba31e50cf6f3d59a0 |
| reconciler | 386cc6c243347dce65c60f55c3480ae6 | =base | 707c682858f40245d06aee9dc8f94e00 |

## main.py / db.py baselines (re-check unchanged post-restart)
- main.py = `f16e9c24f81e65c9eb9d98019eea4e23`
- db.py   = `a2c2ff46b89ec3d30640552db19b962c`

## Note (race): a live entry could fire before the restart
Staleness gate is rejecting redeem entries, but a fresh entry could fill. Stage+apply don't affect the running process (files swap on disk; old code stays in memory until restart). The restart is the mid-position-sensitive moment → re-confirm flat immediately before the operator restart.
