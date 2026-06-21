# VERIFY — Issue #1 managed-exit suppression (2026-06-21). Read-only; agent verifies.

## A. CONFIRMS-AT-APPLY (no restart)
- [ ] File at TARGET — `md5sum trading_corp/agents/paper_trade_replay.py` == `5619910dab44b053124fbbc2e7671cec`.
- [ ] Backup exists — `paper_trade_replay.py.bak-pre-issue1-2026-06-21`.
- [ ] py_compile clean (apply asserts it).
- [ ] main.py/db.py/config untouched (this deploy is one file).

## B. CONFIRMS-AT-RESTART (whenever the activating restart happens)
- [ ] Engine boots clean on the new file (no ImportError/Traceback referencing `paper_trade_replay` /
      `bracket_managed`).
- [ ] **The 20008 loop is GONE:** on the next live bracket-managed trade, NO `live_exit_order_rejected` with
      `20008 'Insufficient amount'` (and no `live_exit_order_placed` for `-exit-tp`/`-exit-sl` on a bracketed row).
- [ ] **Exit still works:** the position still closes via the /tpsl/ bracket + `auto_book_server_side_close`
      (result booked by the reconciler, not the replay loop). The trade still books a result.
- [ ] **Telegram exit-flood gone:** no exit-rejection pushes for bracketed live trades.
- [ ] (optional) audit shows the replay tick counting `suppressed_bracket_managed` for the open live row.

## Rollback if A fails
Restore `*.bak-pre-issue1-2026-06-21` + restart. (Server-side bracket exits the position either way — no money risk.)
