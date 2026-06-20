# VERIFY — metrics-epoch (2026-06-20). Read-only; agent verifies.

## A. CONFIRMS-AT-APPLY (no restart needed — file state)
- [ ] **Files at TARGET** — `md5sum trading_corp/web/data.py trading_corp/web/templates/division.html`:
      data.py == `dae49424521a0586adecb32ccf1da614`, division.html == `b6e23456a1cfcec484f41c5b3ce6e61e`.
- [ ] **Backups exist** — `*.bak-pre-metrics-epoch-2026-06-20` for both files.
- [ ] **No epoch row yet** — `SELECT * FROM agent_state WHERE agent='bitunix_futures' AND key='metrics_epoch'`
      returns nothing (epoch unset until the D1 cutover; PM's row is unrelated).
- [ ] py_compile data.py clean; jinja parse division.html clean (the apply already asserts both).

## B. CONFIRMS-AT-RESTART (whenever the activating restart happens — standalone or batched with D1)
- [ ] Engine boots clean on the new data.py (no ImportError/Traceback referencing `paper_trade_summary` /
      `_ptr_window_totals` / `_get_metrics_epoch`).
- [ ] **Dashboard renders both panels for bitunix_futures:** "Paper-trade win rate" (now paper-only — no live rows
      blended) AND a new "Live-trade win rate" panel. Live panel shows the "all-time · epoch not set (includes
      pre-fix bookings)" label until the epoch is set.
- [ ] **Split sanity (read-only, vs DB):** the live panel's W/L/E counts == live rows only
      (`SELECT result,COUNT(*) FROM paper_trade_record WHERE division='bitunix_futures' AND execution_mode='live'
      GROUP BY result`); the paper panel == non-live rows. They must NOT sum into one number (the old bug).
- [ ] Other divisions (e.g. coinbase_spot) render the paper panel exactly as before (no live panel — `has_live` false).

## C. CONFIRMS-AT-EPOCH-SET (after the D1-cutover agent_state INSERT — separate step)
- [ ] Live panel label flips to "since <YYYY-MM-DD> · current logic only".
- [ ] Live W/L counts drop pre-epoch resolutions (scoped `result_ts >= epoch`); paper panel UNCHANGED (unbounded).

## Rollback if A/B fails
Restore the 2 `*.bak-pre-metrics-epoch-2026-06-20` + restart (PLAN.md). Display-only — no trade-path impact.
