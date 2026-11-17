# DRAFT deploy_log entry — append to runbooks/deploy_log.md AFTER a green pv2_postverify (Jack)

## 2026-11-XX — PM: Polymarket Data API v1→v2 client migration

- **Branch/commits:** `pm-polyv2-migration-2026-11-17` — `00a205c4` (client), `75dbd048` (tests); prod-live
  FF'd to branch tip after verify; tag `pv2-deploy-2026-11-17`.
- **Files deployed (2):** `trading_corp/data/polymarket_data_api_client.py`
  (`ad9a31922becfbe1`→`c88cfce6a16cc4a0`), `trading_corp/prediction_markets/loss_grounding.py`
  (`ce22b4d26de6faf4`→`42d166575b2f39bd`) — CR-stripped sha256-16. Backup kept at
  `/home/azureuser/backups/pv2_<ts>/`.
- **Features shipped:** Polymarket Data API v2 (v1 retired 2026-11-02, HTTP 410 since — whale signal feed dead
  division-wide). v2 endpoints + `{data,pagination}` envelope + cursor-only paging (offset contract translated
  in-client); field remap to v1 semantics incl. the paper.py extra-key contract (outcomeIndex/curPrice/
  redeemable/endDate); loud HARD-FAIL error path (rate-limited ERROR + typed raise; 429 jitter backoff);
  activity/grounding depth caps deliberately unchanged.
- **Restarted:** `trading-corp` + `prediction-markets-web` (pm_cli/one-shots pick it up on next run).
- **Verification:** box-scratch A/B pytest BASE vs TARGET — 34 pre-existing failures identical, zero
  regressions; TARGET 792 passed/34 failed; all new tests green (migration suite + 16 matcher-equivalence).
  Live v2: 42 attached wallets' books complete (incl. 7,238 rows/15 cursor pages), 333 stored-row parity joins
  → 312 value-equal, partial-sale identity 14,343/14,343, zero errors. Post-restart: pv2_postverify green
  (zero 410s, boot-reconcile clean ×4, latch set unchanged, heartbeats fresh incl. jack:nba).
- **Notable code changes:** malformed envelope now RAISES (the v1 silent-[] → phantom-whale-exit path is dead);
  terminal = absence of next_cursor, never row count; `total_bought←total_size` (shares, §13A(g));
  `timestamp←last_event_at` (seconds); v2 drops apostrophes in outcome labels (watch first fills' leg_audit).
- **Reports:** `00_PV2_BUILD_REPORT.md` (build/verify evidence) on the branch; scope reports
  `00_PM_STOPPED_REPORT.md` / `01_V2_MIGRATION_AND_REARM.md` on `pm-health-stopped-2026-11-16`.
