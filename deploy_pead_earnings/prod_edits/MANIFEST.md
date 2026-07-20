# STEP 2 (restart-gated) — deployed prod-edit snapshots

These are the EXACT files deployed to prod on 2026-07-20 for the PEAD dashboard panel + scan_evaluation
write-path. Prod is NOT a git checkout and its engine source is AHEAD of git main, so these snapshots
(base prod md5 -> deployed md5) are the authoritative record — the same convention as `deploy_rh_auth/`.
Edited FROM the prod copies (drift-safe). Staged on prod + validated; **the engine restart that makes
them ACTIVE is HELD pending an operator RH-pickle refresh** (device approval — see deploy_log 2026-07-20).

| file | prod path | base prod md5 | deployed md5 | drift note |
|---|---|---|---|---|
| pead_view.py | trading_corp/web/pead_view.py | bc7e1b58 | 43c32c02 | base was CRLF-only vs git main (content-identical) |
| pead_strategy.py | trading_corp/agents/strategies/pead_strategy.py | ecd1cad7 | 663a9fe4 | base genuinely prod-ahead of git main |
| pead_live_sections.html | trading_corp/web/templates/partials/pead_live_sections.html | 4d771c0e | 5ab68dc5 | base was CRLF-only vs git main (content-identical) |

routes.py was NOT edited — the panel rides the existing `/telemetry/pead/partials/live` fragment, so the
most-drifted file stays untouched.

## Changes
- **pead_view.py**: `query_upcoming_earnings()` reads the isolated `~/pead_earnings/earnings_watch.db`
  STRICTLY mode=ro (graceful if absent), returns the watchlist (upcoming screen-passers we don't already
  hold) + recent-reported tail + anticipation-funnel stats + a stale flag; wired into `build_pead_view`
  via `asyncio.to_thread` (never a sync fetch on render — the 6/26 lesson); added `"upcoming"` to the view.
- **pead_live_sections.html**: a full-width "Upcoming Earnings" section above the main grid. Pre-report
  values are labelled **"SUE plausibility"** (never "SUE"); the EXACT computed SUE appears only on the
  post-announcement "Just reported" rows. Graceful empty when the watcher DB is absent/undefined.
- **pead_strategy.py**: `_log_scan_funnel()` persists the per-name signal funnel to `scan_evaluation`
  (verdict passed/rejected + reason_code + metrics{sue, wave_size, quintile_cutoff}) right after
  `rank_wave`, FORWARD-ONLY, wrapped so observability NEVER breaks the scan. Imports `passes_screen`,
  `_percentile`, `insert_scan_evaluation`.

## Pre-restart validation (PASSED 2026-07-20, engine untouched)
`_pead_panel_validate.py` (throwaway on prod): pead_strategy imports clean; `query_upcoming_earnings`
returns 60 watchlist + 12 reported from the real DB; `build_pead_view` + real partial render = RENDER_OK
76KB, panel present, "SUE plausibility" present. (Harness stubs the engine's custom Jinja filters — our
section uses plain `%`-formatting, no filters.)

## Rollback (per file, on prod)
Restore `<file>.bak_peadpanel_20260720` over the deployed file, then (if already restarted) restart again.
Backups exist on prod alongside each of the 3 files. The engine is unaffected until the restart.
