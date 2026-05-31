# Stage-1 redeploy attempt #3 — transfer manifest

**Generated:** 2026-05-31 (Phase 2.9 of redeploy session)
**Source:** UNION of (Item 5 sweep DIFFER-STALE-ON-PROD ∪ MISSING_ON_PROD) ∪ {config/strategies.yaml per operator pre-decision}
**Origin/main HEAD:** `7352f8f` (merge of items3-4-5)
**Prod pre-deploy pointer (logical):** `4985bbe` (last whole-deploy reference; prod's filesystem has drifted from this)

## Manifest summary

| Bucket | Count |
|---|---|
| DIFFER-STALE-ON-PROD (overwrite existing prod file) | 51 |
| MISSING_ON_PROD (create new prod file) | 14 |
| Operator-added: config/strategies.yaml (TIER_SIZING canonical on main via 9fd9022) | 1 |
| **TOTAL** | **66** |

`git diff 4985bbe..origin/main` would yield only 15 files — confirms the diff-derived approach misses 50 stale-on-prod files. Using sweep-derived baseline per `[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]`.

## Transitive import coverage (verified)

Per analysis report `reports/2026-05-30_prod_vs_main_file_level_sweep_findings_analysis.md`:

| File | Reachability | In manifest? |
|---|---|---|
| `trading_corp/agents/divisions/tasty_options.py` | main.py:1234 unconditional import | ✓ (MISSING) |
| `trading_corp/agents/strategies/tasty_options_iron_condor.py` | main.py:1235 unconditional import | ✓ (MISSING) |
| `trading_corp/brokers/tastytrade.py` | main.py:1867 broker-construction | ✓ (MISSING) |
| `trading_corp/brokers/bitunix_exceptions.py` | transitive via bitunix.py + data_exec.py + observer | ✓ (MISSING) |
| `trading_corp/brokers/bitunix_symbols.py` | transitive via bitunix.py | ✓ (MISSING) |

## Rollback set

- **Backup tag for existing files:** `pre-stage1-redeploy3-20260531-<HHMM>` (51 + 1 strategies.yaml = 52 backup files)
- **Rollback strategy for net-new files (14 MISSING):** `rm -rf` on rollback (no prior version to restore)

## Files — DIFFER-STALE-ON-PROD (51, overwrite-with-backup)

config/divisions.yaml
config/risk.yaml
config/weather_stations.yaml
trading_corp/agents/backtester.py
trading_corp/agents/data_exec.py
trading_corp/agents/divisions/bitunix_futures_observer.py
trading_corp/agents/divisions/fidelity_options.py
trading_corp/agents/logger.py
trading_corp/agents/risk.py
trading_corp/agents/strategies/_weather_math.py
trading_corp/agents/strategies/bitunix_confluence.py
trading_corp/agents/strategies/bitunix_pa_validation.py
trading_corp/agents/strategies/btc_accumulator.py
trading_corp/agents/strategies/ic_candidate_grader.py
trading_corp/agents/strategies/kalshi_llm_arbitrage.py
trading_corp/agents/strategies/kalshi_sports_scout.py
trading_corp/agents/strategies/polymarket_arbitrage.py
trading_corp/brokers/bitunix.py
trading_corp/brokers/kalshi.py
trading_corp/comms/bitunix_lifecycle_notifier.py
trading_corp/comms/telegram_commands.py
trading_corp/data/bitunix_bar_archiver.py
trading_corp/data/bitunix_htf_context.py
trading_corp/data/kalshi_market_map.py
trading_corp/data/kalshi_whale_stats.py
trading_corp/data/weather_stations.py
trading_corp/graph/ceo_graph.py
trading_corp/main.py
trading_corp/persistence/db.py
trading_corp/persistence/models.py
trading_corp/utils/divisions.py
trading_corp/utils/secrets.py
trading_corp/web/app.py
trading_corp/web/data.py
trading_corp/web/routes.py
trading_corp/web/static/icons/apple-touch-icon-152.png
trading_corp/web/static/icons/apple-touch-icon-167.png
trading_corp/web/static/icons/apple-touch-icon-180.png
trading_corp/web/static/icons/favicon-16.png
trading_corp/web/static/icons/favicon-32.png
trading_corp/web/static/icons/icon-192.png
trading_corp/web/static/icons/icon-512.png
trading_corp/web/static/icons/icon-maskable-512.png
trading_corp/web/templates/division.html
trading_corp/web/templates/home.html
trading_corp/web/templates/iron_condor_live.html
trading_corp/web/templates/partials/bitunix_score_panel.html
trading_corp/web/templates/partials/stat_cards.html
trading_corp/web/templates/partials/trade_flow.html
trading_corp/web/templates/research.html
trading_corp/web/webhooks.py

## Files — MISSING_ON_PROD (14, create-new)

trading_corp/agents/divisions/tasty_options.py
trading_corp/agents/strategies/tasty_options_iron_condor.py
trading_corp/brokers/bitunix_exceptions.py
trading_corp/brokers/bitunix_symbols.py
trading_corp/brokers/tastytrade.py
trading_corp/data/iem_cli_client.py
trading_corp/data/nbm_client.py
trading_corp/data/residual_logic.py
trading_corp/path_logger/__init__.py
trading_corp/path_logger/__main__.py
trading_corp/path_logger/logger.py
trading_corp/path_logger/main.py
trading_corp/path_logger/store.py
trading_corp/scripts/analyze_polymarket_whale.py

## Files — Operator-added (1)

config/strategies.yaml

  Rationale: TIER_SIZING overlay (PREMIUM 0.015/25, STANDARD 0.0075/25)
  is now canonical on main via merge commit 9fd9022 (Sat May 30 01:56:59 -0400).
  Underlying overlay commit: 41ee5e6.
  Prod and main md5 both align on these values; the 5 prod-only lines are
  comment-text drifts (no behavioral divergence) per 2026-05-30 audit
  (which used the same prod md5, confirmed byte-identical via this session's
  audit-not-stale re-probe).
  Whole-file transfer is now correct policy.
