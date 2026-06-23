# Win-rate Paper/Live toggle — STAGED (Board-gated, NOT deployed)

## What
Replace division.html's TWO win-rate panels ("Paper-trade win rate" + "Live-trade
win rate") with ONE panel + a client-side **Paper/Live toggle** (default **LIVE**).
Fixes the confusion that prompted this: the operator set the metrics epoch expecting
the win-rate panel to reset, but the panel he saw was the PAPER slice (unbounded by
design); the epoch-scoped LIVE slice was a separate panel that vanishes when empty.

## How (pure front-end — no data.py, no restart)
Both slices are already computed by `data.py:paper_trade_summary` and arrive in the
one `view.paper_trade_summary` dict (`totals` = paper/unbounded; `live_totals` +
`has_live` + `metrics_epoch` = live/`result_ts >= epoch`). The toggle is ~12 lines
of inline vanilla JS swapping `hidden` between two pre-rendered sub-views + a
`localStorage` memory (default LIVE). NO re-query, NO `data.py` change.

- **LIVE view** (default): epoch-scoped grid; when empty shows
  "No live trades resolved since <epoch> yet." + "since <date> · current logic only".
- **PAPER view**: all-time grid (105W/49L) + "all-time · signal replay, not epoch-scoped".
- **Toggle gate**: shown only when `has_live or metrics_epoch` → **bitunix_futures only**
  today. Paper-only divisions (kalshi/polymarket) render exactly as before (no toggle),
  verified by the offline render test (CASE C).

## Files
- `trading_corp/web/templates/division.html` — win-rate block (prod L258–347) replaced.
  Built against the PROD blob (worktree copy is STALE — see drift note). No other file.
- `render_test.py` — offline Jinja test (full-file parse + 3 branch cases), all PASS.

## Validation done
- `python render_test.py` → FULL-FILE PARSE OK + CASE A/B/C all OK.
- Staged template: 969 lines, LF, md5 **367ae47693ff8ff49026d92fc8bd6688**
  (built from prod md5 **b6e23456a1cfcec484f41c5b3ce6e61e** / 910 lines).

## Apply (operator-run; agent SSH read-only; NO restart, NO flat window)
Dashboard-only, no trading-path code — safe any time.
```
scp -r "<...>\bitunix_reports\wr_toggle_deploy" azureuser@trading.jacksumner.com:~/
ssh azureuser@trading.jacksumner.com "bash ~/wr_toggle_deploy/apply_wr_toggle.sh"
ssh azureuser@trading.jacksumner.com "bash ~/wr_toggle_deploy/VERIFY.sh"
```
apply: drift-gate (prod md5 b6e23456) → verify staged md5 367ae476 → backup
`*.bak-pre-wrtoggle-2026-06-23` → swap → verify new md5 → jinja-parse → self-rollback.
**No restart** (Jinja `auto_reload`); refresh the page. Rollback: `cp <bak> <tpl>` (hot).

## DRIFT NOTE (do not re-trip)
Prod `web/data.py` AND `web/templates/division.html` are NEWER than the worktree
(`cc-tpsl-rebuild-wt`): worktree `division.html` has only the old single paper panel
(no live panel / epoch badge); worktree `data.py` predates the `_get_metrics_epoch` /
execution_mode split. This stage was BUILT and drift-gated against the PROD files.
