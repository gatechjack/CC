# Kalshi K5 Go-Live Build — Session Close-Out (2026-06-30)

Both workstreams BUILT, TESTED, COMMITTED. UNMERGED, INERT, operator-gated for deploy.
Nothing live was touched (kalshi stays paper / out of `--brokers`+`--live-divisions`).

## Workstream A — live execution path (branch `kalshi-k5-golive-2026-06-30`, off main 9bfd7ff)

7 commits (`f3e01cb` re-anchor → `0609fef` deploy prep). **54 new tests + 120 adjacent
regression all green.** See `RUNBOOK.md` (same dir) for the full slice table, the 4 flagged
deviations, INERT-deploy steps, go-live gates, and kill-switch/rollback.

Headline: `KalshiLiveBroker` over pykalshi 1.0.6 (marketable IOC, ceiling = whale ± 2¢,
USD→contracts, `KalshiNoFill`, `reduce_only` exits, idempotent `client_order_id`); factory
anti-half-flip; loop gated live placement + per-trade risk BYPASS + entry/exit write-back;
feed-health/mass-exit circuit breaker; demo-smoke script.

## Workstream B — dashboard (branch `kalshi-k5-dashboard-2026-06-30`, off main 9bfd7ff)

3 commits (`82be137`, `dd48ff9`, `92f676b`) — touches ONLY `web/routes.py`, `web/data.py`,
`web/templates/partials/pm_dashboard_body.html` (+345/−22). Independently shippable (web
reload, no engine restart). **Verified two ways** — orchestrator independently (compiles,
85 web tests pass, valid imports incl. `deep_seed_watchlist`, in-scope) AND the sub-agent's
own final report (117 dashboard/kalshi tests pass, 0 new failures; full-suite 30 failures
ALL pre-existing at base 9bfd7ff = 0 new regressions). Sub-agent report:
`cc-kalshi-k5-b-wt/reports/2026-06-30_k5_workstream_b.md` (+ Desktop copy).

- **Defect A** — demote now re-adds the whale to `watch_only_whales` (auto-finalists that
  were never in it no longer vanish from both panels). (`routes.py:2144`)
- **Defect B** — sortable Kalshi watch list: `_KALSHI_WATCH_SORT_KEYS` whitelist
  (`data.py:4894`) + `kalshi_watch_sort_link` macro (`pm_dashboard_body.html:709`) +
  threaded `kalshi_watch_sort`/`kalshi_watch_desc`.
- **Defect C** — promote/demote robust to bad handles: `{% if w.handle %}` guard +
  `|urlencode` on `hx-post` + `[data-whale-id='{{ w.handle|e }}']` attribute target.
- **Addition D** — async **Run Discovery** button (`POST /api/kalshi/watchlist/discover`
  → background `deep_seed_watchlist`, `GET …/status` polls every 3s, disabled-while-running,
  `hx-confirm`, cost telemetry). Drives the **Sunday seed only**.
  **Daily-refresh decision HONORED:** `routes.py:2191` carries a COST NOTE flagging the
  daily `trading-corp-watchlist-stats.timer` as the dominant ~$196/period sink — left
  AS-IS per operator (2026-06-30); no dedup/cadence change made. Paired op (disable
  `trading-corp-watchlist-deep.timer`, root) noted as operator-only.

## Operator next steps (all gated — NOT done this session)

1. Review both branches; merge when satisfied (B can ship on its own track — web reload).
2. INERT deploy of A (code-only; flat-window restart bounces bitunix+pead live;
   targeted-hunk vs prod blob — NOT file-copy). See RUNBOOK.
3. DEMO validation: `python -m trading_corp.scripts.kalshi_demo_smoke` (KALSHI_USE_DEMO=1 + demo creds).
4. Live flip only after ALL gates green — **Apify live open_positions feed restored +
   budget-isolated** (today HTTP-400, cap-exhausted) is the hard blocker; plus roster,
   funding, kill-switch test, Board/Backtester sign-off.

No `$1` shakedown / live flip performed — explicit operator go required.
