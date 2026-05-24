# Next-session pickup prompt (kalshi_weather: autopsy + plan + Bucket 1 LIVE)

*Updated 2026-05-24 ~22:15 UTC after a multi-segment session that: (1) ran the 24h post-xref autopsy, (2) wrote the forecast-quality plan, (3) shipped Bucket 1 (HRRR + run-age logging) to prod. Supersedes the earlier 20:30 UTC version of this file.*

**Why one prompt covers three things:** they're the same thread — the autopsy raised questions, the plan answered them as work items, Bucket 1 ships the data capture that the rest of the plan depends on. Read top-to-bottom; the order matters.

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-24 kalshi_weather session (autopsy → plan → Bucket 1 deploy). Three things landed today; one is now data-collecting on prod, two are gates for later. **Prod state changed once** (Bucket 1 deploy). **All commits pushed to `origin/main`.**

## Where things stand (read first)

**Prod:** `kalshi_weather_arb` is running:
- `f5a5fd5` (P3 YAML xref loader) since 2026-05-22T16:25 UTC.
- **`75ba7c5` (Bucket 1: HRRR + forecast run-age logging) since 2026-05-24T21:47:13 UTC** — write-only additive audit fields, NO decision logic change.
- PID `1300124` (xvfb-run). healthz `{"status":"ok","mode":"PAPER"}`.
- Observation window runs through ~2026-05-29.

**Three artifacts today:**
1. **Autopsy** (`reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md`) — verdict: NO defect, variance. n=75, WR 70.7%, net −$28.15 (NOT the −$81 by resolved_ts — ~$53 of that was pre-deploy carryover; filter `entry_ts` going forward).
2. **Plan** (`plans/forecast-quality-improvements-for-kalshi-prancy-porcupine.md`) — Bucket 1 (additive data capture, ship-now-safe) + Bucket 2 (logic specs, gated until observation week closes).
3. **Bucket 1 LIVE on prod** (`runbooks/deploy_log.md` entry 2026-05-24 21:47 UTC) — 8 new audit fields on `kalshi_weather_evaluated` from 21:53:23 UTC onward.

**Two anomalies the autopsy flagged for observation-week watch (NOT acted on):**
- **#1: book is 100% NO bets, 87% NO-on-`between`** — short-vol posture, geographically diverse but directionally one-dimensional.
- **#2: σ_used appears under-estimated** — empirical |z|≥2 at 3.1× theoretical (directional only; Open-Meteo proxy; 12 tail rows collapse to 5 unique events on 2026-05-23 cold push).

Together → short-vol with underpriced tails. Bucket 2 Item 2.2 (NBM-σ work) addresses both, justified if signal repeats.

## Read first (in this order)

1. **`BACKLOG.md` top EOS snapshot (2026-05-24 ~22:00 UTC)** — canonical handoff. Lists today's 5 commits + prod state + open items + memory updates.
2. **`reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md`** — sections 3 (defect-class scan), 4 (σ calibration), 7 (anomalies #1+#2 → NBM-σ connection) are load-bearing.
3. **`plans/forecast-quality-improvements-for-kalshi-prancy-porcupine.md`** — the plan. Bucket 1 status now = DEPLOYED. Bucket 2 specs (2.1–2.4) wait on operator go after observation week closes.
4. **`runbooks/deploy_log.md` 2026-05-24 21:47 UTC entry** — what's actually running + audit field shape + rollback recipe.
5. **Memory (auto-loaded):** `project_kalshi_weather_bucket1_deployed.md` (new), `project_kalshi_weather_24h_post_xref_autopsy.md` (updated), `project_kalshi_weather_xref_p3_live.md` (updated).

═══════════════════════════════════════════════════════════════════════════
## What to work on next — Board picks ONE
═══════════════════════════════════════════════════════════════════════════

You're picking from three real options. The session is currently in a **data-collection wait** — Bucket 1 just started logging; the observation week runs to ~2026-05-29. Most-leverage work is gated on time-based accumulation, not Claude.

### TRACK A — Forward-watch on Bucket 1 deploy (~15m, read-only, recommended FIRST)

Just sanity-check that the 21:47 deploy is healthy across the spectrum, not just the HOU rows that the post-deploy spot-check happened to sample. Useful if at least a few hours have passed since deploy so multiple scan cycles have run.

1. SSH to prod and query for fresh `kalshi_weather_evaluated` rows since 21:47:13 UTC, sampling the corrected-city tickers:
   ```sql
   SELECT ts, json_extract(payload_json, '$.ticker'),
          json_extract(payload_json, '$.coord_source'),
          json_extract(payload_json, '$.lat'), json_extract(payload_json, '$.lon'),
          json_extract(payload_json, '$.yaml_coords'),
          json_extract(payload_json, '$.hrrr_temp_f'),
          json_extract(payload_json, '$.hrrr_source'),
          json_extract(payload_json, '$.nws_forecast_issued_at')
     FROM audit_event
    WHERE actor='kalshi_weather_arb' AND kind='kalshi_weather_evaluated'
      AND julianday(ts) > julianday('2026-05-24T21:47:13')
      AND (json_extract(payload_json,'$.ticker') LIKE 'KXHIGHCHI%'
        OR json_extract(payload_json,'$.ticker') LIKE 'KXHIGHNY%'
        OR json_extract(payload_json,'$.ticker') LIKE 'KXLOWTNYC%'
        OR json_extract(payload_json,'$.ticker') LIKE 'KXLOWTCHI%'
        OR json_extract(payload_json,'$.ticker') LIKE 'KXHIGHTHOU%')
    ORDER BY ts DESC LIMIT 20;
   ```
2. For every row: confirm `coord_source = yaml_verified`, `audit_lat/lon == yaml_coords`, `hrrr_temp_f` non-null, `hrrr_source = open_meteo_hrrr`. Any row failing these is a defect.
3. Bulk health: count HRRR availability rate (% of post-deploy rows with non-null `hrrr_temp_f`) and NWS issued_at populate rate (% non-null). HRRR expected ~100%; NWS expected most-but-not-all (Akamai per-request behavior).
4. If anything looks off, report; do not auto-fix. The rollback recipe is in the deploy_log entry.

**Read-only.** Should take ~15 minutes. Report findings + stop.

### TRACK B — Mid-week σ-defect watch (~30m, read-only, AFTER ~24h of new data)

Defer until at least 24h after Bucket 1 deploy (so ≥2 settle dates beyond 2026-05-23 cold push are in the corpus). The σ-defect signal from the autopsy is "do KMSP / KSAT / KAUS / KSEA repeat |z|>2 on a *different* settle date?" — this needs more time to answer than TRACK A.

1. Use `scripts/fetch_kalshi_weather_corpus.py` to pull post-deploy RTs from prod (committed `0ab8daa`, stdlib only, chunked dd+base64 driver).
2. Filter by `entry_ts >= '2026-05-22T16:25'` (NOT `resolved_ts` — the carryover trap that bit the headline last time).
3. For the 4 watch stations, compute z = (actual − forecast) / σ_used per RT. Open-Meteo as proxy is fine for the mid-week check (formal week-end run uses NWS CLI).
4. Report: any of those 4 stations show |z| > 2 on a *different* settle date than 2026-05-23? If YES on 2+ independent dates → NBM-σ work (Bucket 2 Item 2.2) moves from speculative to justified. If NO → keep watching.

### TRACK C — End-of-observation-week formal autopsy v2 (~2h, gated on 2026-05-29 OR LATER)

The big one. Only run when on/after ~2026-05-29.

1. Same corpus pull as TRACK B, full week's data.
2. **Use NWS CLI HTML scrape, not Open-Meteo, for actuals** — each station's `feeds.cli_observed_html` URL in `config/weather_stations.yaml`. That's what Kalshi settles against. (Open-Meteo proxy was last week's first-pass; not authoritative for verdict.)
3. Recompute the full §3 defect-class scan + §4 σ calibration with NWS CLI as ground truth.
4. **Bonus available now (was not in v1 autopsy):** compare HRRR-only forecast vs the existing blend on same-day market entries — this is Bucket 2 Item 2.4 Step A in the plan. If HRRR-only is materially better calibrated, Bucket 2 Item 2.4 (pace adjustment) becomes a SHELVE candidate (HRRR captures most of pace's value natively).
5. Write `reports/2026-05-29_kalshi_weather_obs_week_verdict.md` (or whatever the date is).
6. **P4 advance gate:** operator explicit go required. Do NOT advance the legacy `_CITY_COORDS_FALLBACK` removal without clean-week verdict.

### TRACK D — Pivot to other open work (if you don't want to think about kalshi_weather)

Today's session was mostly kalshi_weather. Other open threads:
- **kalshi_sports_arb_observer corpus accumulation** (Phase 0 MLB live since 2026-05-24 15:40 UTC). First decision point ~10–15 cycles in. See parallel session's EOS snapshots 2026-05-24 ~17:00 / ~21:30 UTC in `BACKLOG.md`.
- **Security tracks:** C-2 and C-6 BOTH deployed today (parallel session). C-1 secret rotation unchanged. See `reports/2026-05-21_security_review.md`.
- **PM watchlist cadence-change** filed BOARD-GATED. See `project_pm_watchlist_windowed_live.md`.
- **Tastytrade env vars KV fix** (parallel session WIP — uncommitted modification to `trading_corp/utils/secrets.py` adding `TASTYTRADE_PROVIDER_SECRET` + `TASTYTRADE_REFRESH_TOKEN` to `_SECRET_KEY_NAMES` per `feedback_tastytrade_env_vars_bypass_kv.md`).

═══════════════════════════════════════════════════════════════════════════
## Hard discipline reminders for this work
═══════════════════════════════════════════════════════════════════════════

- **Filter by `entry_ts`, NOT `resolved_ts`.** Carryover trap. Last session: ~$53 of −$81 headline was pre-deploy carryover.
- **Observation windows are durations, not samples.** One clean day is the start, not the end. (`feedback_observation_window_no_early_advance.md`)
- **Tail multipliers use distinct (station, date) tuples**, not row counts (multi-ticker-per-station inflates by ~2.4×).
- **Open-Meteo proxy ≠ NWS CLI ground truth.** Directional for first-pass; authoritative only via CLI scrape.
- **Coord-discipline is structural.** Any new data source MUST pass the `lat, lon` locals from `_evaluate_market` line 549; never re-resolve from city names. The HRRR fetch did this correctly.
- **Delegate mechanical SQL / data pulls to Sonnet sub-agents.** Today's pulls (autopsy, bet-shape, σ calibration) were all done by Sonnet under Opus framing. Worked well; do it again.
- **Stop-and-report at forks.** Today's forks (deploy cadence, HRRR flag default) were resolved via AskUserQuestion. Don't auto-resolve.
- **Tighter commits than feels normal.** Each artifact (autopsy, plan, deploy, deploy_log entry) was its own commit.

═══════════════════════════════════════════════════════════════════════════
## Prod state snapshot at session end (2026-05-24 ~22:15 UTC)
═══════════════════════════════════════════════════════════════════════════

- `kalshi_weather_arb`: f5a5fd5 (P3 YAML xref) + 75ba7c5 (Bucket 1) — both live. PID 1300124.
- `kalshi_sports_arb_observer`: Phase 0 MLB live since 2026-05-24 15:40 UTC (cap 150). Sibling division track.
- `kalshi_sports_scout`: discovery-rotation fix b880b66 + MLB aliases d6d54d3 — corpus accumulating.
- `polymarket_arbitrage`: per-condition_id cap shipped 2026-05-21 paper-only.
- `IC grader`: 112aef3 live, paste-and-grade at /telemetry/iron_condor.
- `bitunix_futures`: bias TTL 30 + flip-detection observe-only.
- `requirements.lock`: C-6 corrected + deployed 2026-05-24 15:14 UTC.
- C-2 webhook risk-gate fix: deployed 2026-05-24 16:55 UTC.

Local working tree clean except `docs/Deployment notes.txt` (operator's pre-existing notes) and any parallel-session WIP. Local HEAD = `origin/main`.

**Where to start (operator):** **TRACK A** if it's been at least an hour since 21:47 UTC deploy (enough for multiple scan cycles to have hit NYC/CHI tickers). **TRACK B** after ~24h. **TRACK C** after ~2026-05-29. **TRACK D** if you want a context-switch away from weather.
