# Kalshi Copy Dashboard Paper/Live/All Toggle — Deploy Runbook (operator-executed, §4)

Branch `kalshi-copy-dash-toggle-2026-07-01` @ `e7cfc6b` (off main `355513c`). **Web-only** (no
live-money code). **No prod touch by the agent.**

## What ships (3 web files)
A `wr_mode` toggle (**Paper / Live / All**, default **LIVE**) on the `kalshi_copy_trading`
prediction-markets page. LIVE = round-trips since the go-live epoch `2026-07-01T14:08:58Z`
(overridable via `agent_state(kalshi_copy_trader,'metrics_epoch')`); PAPER = pre-go-live;
ALL = both. Scopes the summary stats + round-trip history + open list. **Guarded to the copy
division only** — every other division and the "All" combined view force `mode=all` (byte-
identical). Mirrors the polymarket epoch/cutoff pattern. 58 tests pass.

- `trading_corp/web/data.py` — `KALSHI_COPY_LIVE_EPOCH` + `_get_kalshi_copy_live_epoch` +
  `_kalshi_copy_mode_clause`; threaded `kalshi_copy_mode`/`kalshi_copy_epoch` into
  `_query_pm_round_trips` / `_query_pm_open_trades` / `_query_pm_resolved_stats`; `PMDashboardView`
  gains `wr_mode` + `wr_live_epoch`.
- `trading_corp/web/routes.py` — `wr_mode` query param (whitelisted paper|live|all, default live)
  on `prediction_markets_one` + the partial route.
- `trading_corp/web/templates/partials/pm_dashboard_body.html` — the 3-button toggle (kalshi copy
  only; `_wr_qs` is a `{% set %}` defined before use — no macro-ordering trap).

**Known v1 limits (non-blocking):** the Open *tile count* + equity curve aren't mode-scoped (no
visible mismatch today — go-live just happened); other sort/filter controls don't carry `wr_mode`
(sorting resets scope to default LIVE). Easy follow-ups if wanted.

## Drift gate — RESULT
prod content == main base for all 3 files (CRLF-only line-ending drift; azureuser-owned). Clean
byte-exact deploy, LF-normalized verify. TARGET md5 (LF content): data.py `3717369324f0ec2db17cd7dbcd334d6f`,
routes.py `daa2f1b89e94319923531e66feb45180`, pm_dashboard_body.html `90750afe7b3101cdf8253f179f702170`.

## Steps (operator)
1. **Apply + verify:** `powershell -ep bypass -f "$HOME\Desktop\deploy_dashtoggle.ps1"`
   — backs up (`*.bak-pre-dashtoggle-2026-07-01`), scp-to-`.new`+`mv` (byte-safe), prints `OK x3`
   + `VERIFY_OK`.
2. **RESTART at a flat window** (templates are NOT hot-reloaded, so this is required to show):
   `ssh azureuser@trading.jacksumner.com "sudo -n systemctl restart trading-corp"`
   Bounces bitunix + PEAD + kalshi (RH pickle re-auth is yours).
3. **Verify (read-only):** load `/prediction-markets/kalshi_copy_trading` → HTTP 200, the
   Paper/Live/All toggle renders, defaults to LIVE with "since 2026-07-01". (Live may show 0
   resolved until the first post-fix copy settles — expected, not an error.)

## Rollback
Restore `*.bak-pre-dashtoggle-2026-07-01` (3 files) + restart. (Or, since it's inert visually,
just leave it — no live-money impact.)

## Parity (post-deploy)
Merge branch → `main` (`--no-ff`) + push so `main == origin == prod-content` (on operator's
clean-post-restart signal).
