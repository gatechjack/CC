# Next-session pickup prompt — post bias-offset v1 deploy (2026-05-26)

*Updated 2026-05-26 ~01:25 UTC after the bias-offset v1 deploy session (which included one rolled-back attempt + a corrected re-deploy).*

---

## Read first (in this order)

1. **`BACKLOG.md` top EOS snapshot (2026-05-26 ~01:25 UTC)** — canonical handoff. Lists today's 5 commits + prod state + open items + memory updates.
2. **`runbooks/deploy_log.md` 2026-05-26 01:10 UTC entry** — exact prod state, the prior failed attempt's root cause, and the rollback recipe.
3. **`reports/2026-05-25_sigma_three_way_calibration.md`** — the 654K-row apples-to-apples measurement that drove the reprioritization (raw NBM-σ substitution rejected; RC-NBM σ is new primary candidate). The bias-offset shipped today is the LOCATION fix; σ work remains held.
4. **Memory (auto-loaded; load-bearing):**
   - `project_kalshi_weather_bias_offset_v1_live.md` — live state + watch-item
   - `project_nbm_sigma_calibration_measurement.md` — the data that shifted priorities
   - `reference_nbm_historical_archive.md` — AWS NODD endpoint + recipe for re-running the data foundation
   - `feedback_deploy_import_graph_audit.md` — pre-deploy checklist (LEARN FROM TODAY'S CRASH-LOOP)

---

## What just shipped (2026-05-26 01:10:33 UTC)

`trading-corp.service` PID 1448692, mode PAPER. Three modifications:

1. **`_weather_math.py`** — adds `BIAS_OFFSETS_V1` (22-cell dict of per-(station, season) additive forecast corrections), inlined `derive_season` byte-equivalent to `residual_logic.derive_season`, `lookup_bias_offset()` helper.
2. **`kalshi_weather_arb.py`** — `_resolve_coords` now returns `station_id`; `_evaluate_market` applies the offset to `forecast.temp_f` before `evaluate_weather_market`; audit gains 6 new fields (`forecast_temp_f_pre_offset`, `bias_offset_applied_f`, `bias_offset_source`, `bias_offset_validation`, `bias_offset_season`, `bias_offset_station_id`).
3. **`data.py`** — `DASHBOARD_RT_CUTOFFS['kalshi_weather']` advanced to `2026-05-26T01:08:00+00:00` so the dashboard tile measures the bias-corrected model from a clean baseline.

Backup tag on prod: `pre-bias-offset-20260526-0018`. Rollback recipe in `deploy_log.md`.

**Live-eval verified:** KAUS spring offset `-2.464°F` applied to `KXLOWTAUS-26MAY26-T71` at 01:16:30 UTC (forecast 70.0 → 67.54, arithmetic exact). KMSP spring `-1.266°F` also verified.

---

## Picking a track today — keyed to elapsed time since deploy

### TRACK A (any time after the next scan cycle, ~10 min post-restart) — sanity-check more rows fired

The first eval cycle hit KAUS + KMSP. The other 7 spring fully_validated cells (KDEN, KDFW, KHOU, KMSY, KNYC, KOKC, KSAT) should fire as their tickers come up in subsequent cycles. Pull recent `kalshi_weather_evaluated` audit rows from prod, group by `bias_offset_station_id`, confirm each of the 9 spring cells appears with the right offset value at least once. Read-only.

### TRACK B (~1-2 weeks) — first round-trip resolutions on bias-corrected forecasts

The whole point. Bets placed post-cutoff need their target date to pass for resolution. After ~1 week, pull the dashboard tile + `kalshi_round_trips` to start measuring WR / PnL of the bias-corrected model vs the pre-cutoff baseline (still in `kalshi_round_trips`, just filtered out of the tile). Don't expect statistical-power answers yet at n<50 trades.

### TRACK C — pivot to the cron poller deploy (the next deliberate step)

Held for separate Board approval. Required for forward data accumulation. Specs in `plans/tier1-data-foundation-kalshi-weather.md` §"Next deliberate step." This bundles:
- Push `residual_logic.py` + `nbm_client.py` + `iem_cli_client.py` + updated `weather_stations.py` + `db.py` schema additions to prod (the C2 files that crash-looped us this session — push them properly THIS time, with the import-graph audit per `feedback_deploy_import_graph_audit.md`).
- Add systemd `nbm-ingest.timer` + `iem-ingest.timer` units. NOT a `trading-corp.service` restart per se — the timers run standalone scripts. But a deploy + permissions check.
- Pre-deploy gate: hash-compare + **import-graph grep** (the lesson from today). For every `+from`/`+import` in the diff, ls-check the module exists on prod. NO REPEAT of the residual_logic ModuleNotFoundError.

### TRACK D — RC-NBM σ build (the σ-side work the bias-offset can't address)

Per `reports/2026-05-25_sigma_three_way_calibration.md`: RC-NBM σ gets |z|≥2 to 1.72× (near 1×), |z|≥3 to 6.58× (Gaussian-assumption ceiling). The data is fully populated in the LOCAL `weather_forecast_residuals` table (1.36M rows from 2021-2026 backfill). Existing-plan Item 2.2 part 2 is the build. Note: until the cron poller ships (Track C), forward data doesn't accumulate; the LOCAL backfill is the source of truth for σ measurements until that's running on prod.

### TRACK E — non-spring nbm_only cross-source re-validation (recurring)

13 cells deployed but cross-source-unvalidated. Pull when summer-class tickers (Jun 1+) start producing nws_blend audit rows; re-run `tmp/_offset_train_test.py` cross-source for summer's nbm_only cells. Pull any that don't hold. Same procedure for fall and winter as those seasons arrive. This is recurring throughout the year.

---

## Hard discipline (carry forward)

- **Import-graph audit pre-deploy.** Today's lesson, in memory. Hash-compare alone is not enough.
- **Stability watch ≥ 2-3 cycles past prior crash-loop interval before declaring deploy success.** PID-stable + active + healthz-green at T+30s/60s/90s/120s minimum. Today's crash-loop period was ~50s; the watch caught nothing because the crash WAS the failure mode, not the absence of healthz. PID stability past 2× crash-loop interval is the real proof.
- **Bias-offset is per-(station, season).** When the season turns, the active cell set changes. Spring active today; summer Jun 1+. The 13 nbm_only cells are watch-items, not blessed.
- **Cron poller deploy includes the C2 file backlog.** Don't push individual C2 files mid-flight; bundle into the poller deploy with the full audit + import-graph check.
- **Local ≠ prod database state.** `weather_nbm_observations` (668K rows) + `weather_forecast_residuals` (1.36M) are LOCAL only. Re-running the historical backfill on prod (via the poller deploy) would re-populate from scratch — that's expected.
- Delegate mechanical pulls to Sonnet. Stop-and-report at forks. Tight commits.

---

## Where to start

- **Default (no operator direction):** TRACK A — sanity-check more bias-offset cells firing on prod. Cheap, read-only, gives confidence the deploy is healthy after sitting overnight.
- TRACK B if it's been a few days.
- TRACK C if operator wants the next deliberate deploy.
- TRACK D if operator wants more σ work.
- TRACK E if it's past Jun 1 / Sep 1 / Dec 1 and a season has just turned.

---

## Prod access reference

`tc-prod-vm` in `rg-shared-prod` (NOT `trading-corp-rg`). `az vm run-command invoke` per `reference_prod_vm_access`. Stdout cap ~4 KB tail-truncated (`reference_az_run_command_stdout_cap`). For file pushes: gzip+base64 in `--scripts @file` form (Windows cmd.exe ~8KB cap on `--scripts` inline). For data pulls: chunked-binary via dd+base64 (see `scripts/pull_prod_kalshi_weather_evaluated_corpus.py`).
