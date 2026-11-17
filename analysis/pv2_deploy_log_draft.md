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

## 2026-11-17 — PM: v2 client + maximum-chase rule (gate C) DEPLOYED (v2chase graft, GREEN)

- **Executed by:** Claude under Board authorization (Jack, 2026-11-17): global disarm, graft, both
  restarts, 12-check health verify. Re-arm, push, FF, tag reserved for Jack and NOT run.
- **Branch/commits:** `pm-polyv2-migration-2026-11-17` @ `776ff4a8` (8 commits over `f82d5be6`:
  `00a205c4` v2 client, `75dbd048` v2 tests, `24ee3992` deploy runners, `dbbc24bf` first-hour analysis,
  `5f8aad50` chase rule, `bddf049f` comment fix, `fba0f6dd` chase report/runners, `776ff4a8` instruments).
  NOT pushed at deploy time.
- **Files deployed (6, BASE→TARGET sha256-16 CR-stripped):** polymarket_data_api_client
  `ad9a3192…`→`c88cfce6…`, loss_grounding `ce22b4d2…`→`42d16657…`, execution `a8a03b59…`→`b4f75cc6…`,
  live_driver `4bbe3023…`→`69dc1040…`, heartbeat `57afdcc6…`→`227eda72…`, db `49f4f6d8…`→`a4d8ddfd…`.
  Backup kept: `/home/azureuser/backups/pvc_v2chase_20261117T210723Z`.
- **Timeline (ET, 2026-11-17):** 16:07:02 global disarm via `pm_cli live-disarm --global`
  (by=claude_deploy_agent; only `arm:global` changed, 92 sub keys byte-identical, marc/trey untouched);
  16:07:23–25 graft applied+verified (no rollback); 16:07:32–16:08:39 engine restart
  (MainPID 809209→991135, ActiveEnter 21:08:01Z); 16:08:46–16:09:19 pm_web restart (698062→991335).
- **Features shipped:** (a) Data API v2 client (see entry above). (b) Gate C maximum-chase rule:
  `chase = leg_ask + kalshi_taker_fee − whale_avg_price`; skip entry when > `max_chase_cents`
  (default 2.0¢ ON) or > `chase_upside_pct`%×(1−whale) (default 0.0 = OFF); per-sub overrides via
  migration 025 (NULL→code default, ≤0 disables); missing whale price fails closed
  (`skip:chase_unpriced`); per-cycle `chase_skips` in `pm_driver_heartbeat` + rate-limited (60s)
  gate-C WARNING with ticker and full arithmetic.
- **Verification (12/12 GREEN vs written pre-deploy prediction):** 410s 0 since restart (was
  ~118k/2h); 20,514 v2 success lines in 28 min; boot-reconcile reconciled=True ×4, latch census
  unchanged (14 boot_reconcile 10-06 + 22 trey 10-22 + 22 marc 10-28); migration 025 self-applied at
  the 21:30Z pm_cli cron (head 24→25); **farm back: 119 `pm_paper_trade` rows at 21:30:01Z — first
  writes since 2026-11-02** (poll log errors:0, no 410s); **gate C live in prod:** jack/cfb
  `chase_skips=2`/cycle, `KXNCAAFSPREAD-26NOV17BALLOHIO-OHIO8` blocked at +2.72¢
  (`chase_0.0272=ask_0.4400+fee_0.0172-whale_0.4300 gt_abs_cap_2.0c`), plus counterfactual-armed sim
  block at +4.69¢ with identical reason form; heartbeats fresh ×4 accounts (sig 899/618/580/579,
  placed=0, zero fills post-disarm); all other divisions cycling (MACE 4 loops, PMCC, PEAD, bitunix
  restart-resume orphan=0, coinbase resumed); 0 tracebacks engine+pm_web; /healthz 200; df 8.1G.
- **Deviations / notables (none rollback-class):** one boot-burst Polymarket 429 HARD-FAIL 21:10:14Z
  (single wallet/page, recovered next cycle, none after 21:11Z); counterfactual sim placed 1 sim-order
  at exactly +2.0¢ (strict-`>` boundary, correct semantics; pre-deploy sim had predicted 0);
  pre-existing robin_stocks option-read session stale since 2026-10-21 (MACE reconcile/PMCC judgment
  errors ~180/day) — unchanged by restart, reported to Jack, not touched.
- **State at close:** `arm:global` DISARMED (21:07:02Z) — re-arm is Jack's. marc/trey still
  auth_failure-latched (pm_rearm runner + attestation pending). Box scratch `/home/azureuser/pvc_verify`
  removed; all backups left in place.
