# Plan — Forecast-Quality Improvements for kalshi_weather

> **Superseded for Items 2.1 + 2.2 data prerequisites by Tier 1 (2026-05-25):** see [`plans/tier1-data-foundation-kalshi-weather.md`](tier1-data-foundation-kalshi-weather.md). The Tier 1 plan supplies the data prerequisite (calibrated NBM σ + per-station IEM CLI residuals DB) that Items 2.1 and 2.2 below lacked, and surfaces a deterministic F→C→F rounding model (C3) that may park Item 2.1 entirely after backtest. Read Tier 1 first; then read this file for the consumption-layer specs that Tier 1's data enables.

> **Document status: RECONSTRUCTED-FROM-SESSION-CONTEXT (2026-05-25).**
> The original plan was worked out in the 2026-05-24 session and
> referenced from `BACKLOG.md` / `runbooks/deploy_log.md` /
> `runbooks/session_start_2026_05_25_post_kalshi_weather_autopsy.md`
> as `plans/forecast-quality-improvements-for-kalshi-prancy-porcupine.md`,
> but the file was never committed (phantom-pointer pattern; see
> memory `feedback_session_committed_phantom_pointer`). This file is
> reconstructed from `docs/Deployment notes.txt` lines 8620–9119
> (a save of the originating session transcript) plus the autopsy
> at `reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md`
> and the deploy_log 2026-05-24 21:47 UTC entry. Bucket 1 was
> shipped (commit `75ba7c5`); Bucket 2 specs were never re-reviewed
> against this canonical file. Treat any divergence from chat memory
> as authoritative HERE, but flag inconsistencies for operator
> review.

**Status:** Bucket 1 = DEPLOYED (commit `75ba7c5`, 2026-05-24
21:47 UTC). Bucket 2 = GATED INVESTIGATION-FIRST SPECS, no code, no
prod, no deploy until observation week closes (~2026-05-29) AND
operator explicit go AND backtest pass/fail gate met per item.

---

## Context

`kalshi_weather_arb` is in an observation week through ~2026-05-29
measuring the fully-corrected logic (P3 YAML xref + station fixes +
entry-price floor). The 2026-05-24 24h post-xref autopsy
(`reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md`) found:

- σ_used appears under-estimated — empirical |z|≥2 at 3.1× theoretical,
  |z|≥3 at 10×, stdev z = 1.168 (~17% wider than σ_used). Directional
  only; based on Open-Meteo proxy; 12 tail rows collapse to 5 unique
  (station, date) events.
- Book is 100% NO / 87% NO-on-`between` — short-vol posture,
  geographically diverse but directionally one-dimensional.

The two together describe a short-vol strategy that underprices its
own tails. Forecast quality and σ calibration are the suspected root.

**Hard constraint:** during the observation week, no changes to
forecast / σ / decision logic — they would contaminate the
measurement. Additive write-only data capture IS fine and desirable
(every day un-captured is later un-backtestable; same rationale as the
queued quote_snapshot design).

This plan splits the work:

- **Bucket 1** — data capture, designed for near-term operator-reviewed
  deploy (additive, write-only, paper-safe, no decision logic touched).
  **STATUS: DEPLOYED 2026-05-24 21:47 UTC, commit `75ba7c5`.**
- **Bucket 2** — logic changes, designed as gated backlog specs only.
  None deploys until the observation week closes and the
  current-system edge question has more data.

---

## Bucket 1 — Additive data capture — DEPLOYED

### Item 1.1 — HRRR latest-run forecast logging — LIVE

**What:** Capture the HRRR-only forecast for the target lat/lon/time
alongside the existing NWS + Open-Meteo ensemble blend, at the moment
of entry. Logged to the `kalshi_weather_evaluated` audit payload as
new fields. NOT fed into σ or temp blend; decision logic untouched.

**Why:** HRRR is 3km resolution, refreshes hourly, strongest 0–12h
skill. Logging it in parallel during the observation week creates the
backtest corpus needed for Bucket 2 items that depend on HRRR-vs-blend
calibration data.

**Mechanism (as shipped in `75ba7c5`):**

- `OpenMeteoClient.fetch_hrrr_only(lat, lon, target_iso, kind=None)`
  with separate `_hrrr_cache`. Single-model Open-Meteo response parsed
  via UNSUFFIXED `hourly.temperature_2m` (NOT `hourly.temperature_2m_<model>`).
- HRRR model identifier: `ncep_hrrr_conus` (Open-Meteo's name; CONUS
  only; every current weather station is CONUS).
- `kalshi_weather_arb.py:_evaluate_market` calls `fetch_hrrr_only` AFTER
  the ensemble fetch, passing the same `lat, lon` locals (bound at
  line 549 from `coord_info["lat"], coord_info["lon"]`).
- New `eval_payload` fields: `hrrr_temp_f`, `hrrr_source`
  (`"open_meteo_hrrr"` or `"unavailable"`), `hrrr_fetched_at`.
- Config: `hrrr_enabled: true` under `kalshi_weather_arb` in
  `config/strategies.yaml`. Hot-reloadable (mtime-checked per cycle).

**Coord-discipline guarantee (load-bearing):**

The 2026-05-22 NYC/CHI/HOU correction (KJFK→KNYC, KORD→KMDW, KIAH→KHOU)
lives in `config/weather_stations.yaml` and is resolved into `(lat,
lon)` by `_resolve_coords` at `kalshi_weather_arb.py:230`. Inside
`_evaluate_market`, `lat, lon = coord_info["lat"], coord_info["lon"]`
at line 549 is the single source of truth. ALL fetch calls (NWS,
Open-Meteo ensemble, HRRR) use those locals by name. There is no
city-name lookup inside `OpenMeteoClient`; there is no separate
`_resolve_hrrr_coords` helper. The xref correction is inherited
structurally, not by runtime check.

Failure modes actively prevented in implementation review (and
confirmed absent):

- ❌ A separate `_resolve_hrrr_coords` helper
- ❌ Passing `cand["city_code"]` to `fetch_hrrr_only`
- ❌ A new city→gridpoint dict in `open_meteo_client.py`
- ✅ One line, in `_evaluate_market`, using the existing `lat, lon` locals

**Post-deploy verification (load-bearing for future audits):**

```sql
SELECT
  json_extract(payload_json, '$.ticker')           AS ticker,
  json_extract(payload_json, '$.lat')              AS lat,
  json_extract(payload_json, '$.lon')              AS lon,
  json_extract(payload_json, '$.yaml_coords')      AS yaml_coords,
  json_extract(payload_json, '$.coord_source')     AS coord_source,
  json_extract(payload_json, '$.hrrr_temp_f')      AS hrrr_temp_f,
  json_extract(payload_json, '$.hrrr_source')      AS hrrr_source
FROM audit_event
WHERE actor='kalshi_weather_arb' AND kind='kalshi_weather_evaluated'
  AND ts > '<deploy_ts>'
  AND json_extract(payload_json, '$.hrrr_temp_f') IS NOT NULL
LIMIT 20;
```

For every NYC / CHI / HOU row: `coord_source MUST be 'yaml_verified'`;
`(lat, lon)` MUST equal `yaml_coords` (the corrected station);
`hrrr_temp_f` MUST be populated.

**NOTE — JSON key name correction (2026-05-25):** The actual top-level
JSON keys in the payload are `lat` and `lon` (see
`kalshi_weather_arb.py:298-299, 591, 723`). The deploy_log and BACKLOG
entries call these `audit_lat`/`audit_lon` — those were SQL ALIASES,
not JSON key names. Use `$.lat` / `$.lon` in any json_extract.

### Item 1.2 — Forecast run-age logging — LIVE

**What:** Log the model-init / observation-time / fetch-time of each
forecast source used at entry. One additional field per source.

**Why:** Lets us later test whether stale-run forecasts correlate with
the autopsy's fat-tail losses.

**As shipped:**

| source | field | nullable? | notes |
|---|---|---|---|
| NWS | `nws_forecast_issued_at` | yes | Captured from `Last-Modified` header; Akamai CDN strips on a fraction of requests (NULL is normal, not a bug — first 16h window showed 100% NOT NULL, but baseline is "expect some NULL") |
| NWS | `nws_fetched_at` | no | Wall-clock fetch time, always populated as fallback |
| Open-Meteo ensemble | `open_meteo_fetched_at` | no | Open-Meteo doesn't expose model init time; fetch time is the freshness proxy |
| Open-Meteo HRRR | `hrrr_fetched_at` | no | Same caveat |
| METAR | `metar_obs_age_min` | yes — NULL for daily HIGH/LOW markets by design | Only populated for sub-6h hourly markets |
| METAR | `metar_latest_obs_iso` | yes — same gating | Raw timestamp; age computable from it |

**Code changes shipped:**
- `_weather_math.py` — `ForecastPoint` gains optional `issued_at` and `fetched_at` (default None; preserves all existing callers).
- `weather_forecast.py` — `_get_periods` captures NWS `Last-Modified` header + wall-clock fetch time; cache extended; both `get_forecast_at` and `get_daily_extremum` populate the new ForecastPoint fields.
- `open_meteo_client.py` — `EnsembleObservation.fetched_at` added; `_fetch_payload` returns `(payload, fetched_at_iso)` with cache extended.

### Item 1.3 — Bucket 1 deploy ordering (HISTORICAL)

Recommended order at plan-time was Item 1.2 first then Item 1.1. As
shipped, both bundled into commit `75ba7c5` per in-session
`AskUserQuestion` confirmation. Operator chose bundled because the
two items share the audit payload extension and the deploy/verify
ceremony.

---

## Bucket 2 — Logic-change specs — GATED INVESTIGATION-FIRST

Each item below is INVESTIGATION-FIRST. The deliverable is a backtest
design with explicit pass/fail gates run against NWS-CLI ground truth
(NOT Open-Meteo reanalysis — the 2026-05-24 autopsy explicitly flagged
Open-Meteo as a directional proxy, not authoritative).

**None ships until ALL of:**
- Observation week closes (~2026-05-29)
- Current-system edge question has more data (TRACK B/C autopsies)
- Operator explicit go on the specific item
- Backtester sign-off per CLAUDE.md § 4 (default assumption: yes —
  this is a deterministic-strategy change to risk-sized money)

**Items 2.1 and 2.2 are candidate fixes for autopsy anomaly #2** (σ_used
appears under-estimated; empirical |z|≥2 at 3.1× theoretical on the
day-one sample). Treat them as gated-fixes-for-a-known-problem, NOT as
standalone competitor-inspired ideas. They unlock only if the
observation week (TRACK B 2026-05-25 ~22:00 UTC + TRACK C ~2026-05-29
NWS-CLI autopsy) confirms the σ defect repeats on independent settle
dates across multiple stations (memory:
`project_kalshi_weather_24h_post_xref_autopsy` flags KMSP / KSAT /
KAUS / KSEA as the watchlist). If the defect doesn't repeat, the
day-one finding was a single-day single-event artifact and neither
2.1 nor 2.2 is justified. Items 2.3 and 2.4 are derivative — 2.3
folds into 2.2's residual table; 2.4 may be obviated entirely by
Item 1.1's HRRR data (Step A is a SHELVE test).

### Item 2.1 — Boundary-proximity σ widening — DEMOTED 2026-05-25 to secondary

> **Status update (2026-05-25, post-C3 backtest):** Item 2.1 was originally
> the lead candidate fix for autopsy anomaly #2's fat-tail σ. The C3 backtest
> (`scripts/backtest_rounding_flip.py`, run against the 4 unique autopsy
> tail-loss events) showed **0/4 events had `risk_flag=True`** at either
> entry-time or settlement-time. The same physics applies to Item 2.1:
> both 2.1 and C3 are **boundary treatments capped at ~1°F**; the autopsy's
> tail losses were **4–8°F genuine synoptic forecast misses**, which no
> boundary-scale treatment can absorb mechanically. **Item 2.1 is therefore
> demoted to a secondary anomaly-#2 candidate.** It remains in queue —
> there may still be a smaller boundary contribution worth widening σ for,
> measured against the residuals DB once enough samples accumulate — but it
> is no longer the lead. The lead is now NBM-σ substitution (corroborated
> by Tier 1 C1 probe showing NBM σ at 48h MaxT is ~56% wider than the
> heuristic — exactly the kind of mechanism that can shift |z|≥2 frequency
> from 3.1× theoretical toward 1×). See
> `plans/tier1-data-foundation-kalshi-weather.md` for the gated-consumption
> path that swaps `_weather_math.py:165` to read NBM `temp_sigma_f`.

**Hypothesis:** Bets where the forecast sits within ~1°F of a bracket
edge (between markets where forecast is close to either `threshold_f`
or `threshold_high_f`) have higher actual variance than `σ_used`
predicts, because ASOS real-time vs NWS CLI settlement differs by up
to ~1°F (rounding + time-window differences). This is what produced
the 22/22 NO-on-`between` losses landing inside the window in the
2026-05-24 autopsy.

**Mechanism (spec):**

- New constants in `_weather_math.py`:
  `BOUNDARY_PROXIMITY_THRESHOLD_F = 1.0` and
  `BOUNDARY_SIGMA_BUFFER_F = 1.0` (operator-tunable via config).
- In `evaluate_weather_market` (currently `_weather_math.py:155`):

  ```python
  sigma_total = sqrt(forecast.sigma_f² + source_divergence_sigma_f²)
  if direction == "between":
      min_distance_to_edge = min(abs(forecast.temp_f - threshold_f),
                                 abs(forecast.temp_f - threshold_high_f))
      if min_distance_to_edge < BOUNDARY_PROXIMITY_THRESHOLD_F:
          sigma_total = sqrt(sigma_total² + BOUNDARY_SIGMA_BUFFER_F²)
  ```

- Audit payload (new fields): `boundary_proximity_applied: bool`,
  `min_edge_distance_f: float`.

**Effect:** widens σ for boundary-proximity bets → reduces prob_yes
confidence → fewer marginal NO-on-`between` trades clear the
`min_divergence_pct` gate → reduces the bet-shape concentration in #1
of the autopsy AND addresses the σ under-estimation in #2.

**Backtest design (pass/fail gate):**

- Re-score all post-xref `kalshi_weather_evaluated` audit rows with
  the new sigma logic.
- Partition into (a) boundary-proximity rows (`min_distance_to_edge < 1°F`) and (b) non-boundary rows.
- **Calibration gate (reframed 2026-05-25):** the boundary-proximity
  subset must show a MEASURABLE REDUCTION in |z|≥2 frequency
  (recomputed with widened `σ_used`) vs the pre-widening baseline on
  the same rows. The non-boundary subset acts as the CONTROL — its
  |z|≥2 frequency must NOT degrade. Don't pre-commit to a numerical
  target; measure the actual effect once observation-week data is in.
  Rationale: the 3.1× tail multiplier from the day-one autopsy is a
  small (12 rows → 5 independent station-date events) sample and may
  be driven partly by global σ under-estimation (Item 2.2's territory)
  rather than boundary-proximity per se. How much of the defect Item
  2.1 alone absorbs is itself a question the backtest answers — don't
  bake the answer into the gate.
- **EV gate:** simulate the gate-filtering impact: how many trades does
  the wider σ suppress, and what would their RT P&L have been? Net EV
  must not turn negative.
- **Ground truth:** NWS CLI scrape for actuals (each station's
  `feeds.cli_observed_html` in `config/weather_stations.yaml`). NOT
  Open-Meteo.

### Item 2.2 — Per-city forecast-bias + σ calibration (the NBM-σ work)

**Hypothesis:** Each station has its own forecast bias and residual
stdev. The current heuristic `sigma_for_horizon` is a global
one-size-fits-all table (1.5 / 2.5 / 3.5 / 5.0°F by horizon bucket —
`_weather_math.py:279`). `SOURCE_DIVERGENCE_SIGMA_F = 2.0` (line 20)
is a global fixed buffer. Both should be replaced by MEASURED per-city
per-horizon residual statistics. The 5/24 autopsy hinted at this: all
12 |z|≥2 losses concentrated in 5 stations (KMSP, KSAT, KAUS, KSEA,
near-miss KHOU).

**Mechanism (spec):**

- New table `weather_forecast_residuals`:

  ```sql
  CREATE TABLE weather_forecast_residuals (
      station_id TEXT NOT NULL,
      target_date TEXT NOT NULL,
      kind TEXT NOT NULL,         -- 'daily_max' | 'daily_min' | 'hourly_at'
      target_iso TEXT,
      forecast_temp_f REAL NOT NULL,
      actual_temp_f REAL NOT NULL,
      forecast_source TEXT NOT NULL,  -- 'nws' | 'open_meteo_ensemble' | 'hrrr' | 'blend'
      horizon_hours REAL NOT NULL,
      residual_f REAL NOT NULL,   -- actual - forecast (signed)
      ingested_at TEXT NOT NULL,
      PRIMARY KEY (station_id, target_date, kind, forecast_source)
  );
  ```

- New ingestion job (`scripts/ingest_nws_cli_residuals.py`, separate cron):
  - Reads each station's NWS CLI HTML (URL in
    `config/weather_stations.yaml:feeds.cli_observed_html`)
  - Extracts daily max / min
  - Joins to historical `kalshi_weather_evaluated` audit rows (which
    carry `forecast_temp_f`, `lat`, `lon`, `target_iso`,
    `horizon_hours`) and to Item 1.1's HRRR rows
  - Writes one residual row per (station, date, kind, source)
- New function `sigma_for_city_horizon(station_id, horizon_hours, source) → float`
  that reads from the residual table (rolling 60–90 day window) and
  returns measured stdev. Falls back to the heuristic if <20 samples.
- Replace `_weather_math.py:155`:

  ```python
  sigma_total = sqrt(forecast.sigma_f² + source_divergence_sigma_f²)  # OLD
  ```

  with:

  ```python
  sigma_total = sigma_for_city_horizon(station, horizon, source) or sqrt(...)  # NEW
  ```

- Optional: forecast bias correction. Subtract per-(station, source)
  mean residual from `forecast.temp_f` before evaluation.

**Backtest design (pass/fail gate):**

- **Time-split:** train on `entry_ts < (T_today − 30 days)`, test on
  `(T_today − 30 days ≤ entry_ts < T_today)`.
- **Calibration gate:** test-set |z| band frequencies match
  theoretical normal within ±20% across all bands (vs current 3.1× /
  10× over-representation in tails).
- **WR gate:** test-set WR ≥ heuristic-σ WR (no degradation; ideally
  improvement).
- **Ground truth:** NWS CLI residuals table (the same table this work
  builds — bootstrap from existing audit rows + CLI scrape on the
  closed weeks 2026-05-22 onwards).
- **Required input:** Item 1.1 must be live (✓ as of 2026-05-24) AND
  have accumulated ≥30 days of HRRR data before the `source='hrrr'`
  partition of this work is evaluable.

### Item 2.3 — Horizon-dependent model weighting

**Hypothesis:** HRRR has highest skill at 0–12h; ensemble
(GFS/ICON/ECMWF) is more useful at 24–72h. The blend should weight by
horizon rather than treating all sources equally.

**Mechanism (spec):**

- Folds INTO Item 2.2 — the `sigma_for_city_horizon(station, horizon,
  source)` lookup gets called per-source, and the blend weighting
  becomes:

  ```python
  if horizon_hours < 12:  weight HRRR heavily (e.g., 0.7 HRRR + 0.3 NWS)
  elif horizon_hours < 36: weight ensemble heavily
  else: NWS only (heuristic σ)
  ```

- Weights ARE the per-(source, horizon) residual stdev inverses —
  natural Bayesian update.
- Implementation lives in the same module as Item 2.2.

**Backtest design (pass/fail gate):**

- Same time-split as 2.2.
- **Calibration gate:** improvement specifically on the 0–12h horizon
  subset (where HRRR is supposed to help).
- **WR gate:** same as 2.2.
- **Dependencies:** Item 1.1 (HRRR data ingestion) AND Item 2.2
  (residual table + lookup function) live.

### Item 2.4 — Pace-adjusted same-day ENTRY forecast

**Distinct from killed Item 2** (hourly re-evaluation of OPEN
positions). That tested position management on existing positions and
found no signal (see memory
`project_kalshi_weather_hourly_reeval_closed.md`). Item 2.4 here is
about adjusting the ENTRY forecast for NEW same-day market entries
using intraday observed-vs-expected hourly temperature curve.
Different feature, never tested.

**Hypothesis:** For same-day daily-high/low markets, the entry
forecast at e.g. 10am should incorporate how the morning has actually
run vs. the expected hourly curve. If the morning is running 3°F
warmer than expected, that has implications for the day's expected
high.

**Mechanism (spec):**

- Compute observed-vs-expected hourly delta for each candidate
  same-day market at evaluation time.
- Adjust `forecast.temp_f` by some fraction of that delta.

**HOWEVER** — this is **subordinated to Item 1.1's measured outcome.**
HRRR natively incorporates intraday conditions (that's what 3km
hourly-refresh models do). The first question is: **does HRRR alone
capture most of pace's value?** Test this BEFORE building hand-rolled
pace logic.

**Backtest design (pass/fail gate):**

- **Step A (must run first, after Item 1.1 has accumulated data):**
  compare HRRR-latest vs our-blend on same-day market entries. If
  HRRR-latest is materially better calibrated (|z| reduction OR WR
  improvement) → most of pace's value is in HRRR; building hand-rolled
  pace gives marginal gain.
- **Step B (only if Step A shows residual pace signal):** build the
  hourly-curve adjuster, backtest as residual improvement on top of
  HRRR.
- **Gating:** Item 2.4 is a candidate for SHELVE if Step A satisfies,
  freeing the hand-rolled work to be replaced by relying on HRRR.

### Item 2.5 — Honest competitor-claim assessment

| Competitor claim | Honest verdict |
|---|---|
| "HRRR is 3km, hourly, strongest 0–12h" | Verifiable model fact. HRRR's resolution and refresh cadence are documented (NOAA). |
| "HRRR divergence from consensus = market mispricing" | Marketing. Requires the assumption that market-makers don't also use HRRR. Testable hypothesis, NOT a given. Backtest required. |
| "Per-city residual calibration improves accuracy" | Verifiable meteorological practice. Standard in forecast verification (Brier score, reliability diagrams). Real. |
| "Horizon-dependent model weighting" | Verifiable. Standard ensemble post-processing technique. Real. |
| "Pace adjustment" | Verifiable concept, BUT may be substantially captured by HRRR ingestion alone — test before building. |

The competitor's concepts are real meteorological practice. The
competitor's claim that doing them automatically produces market edge
is unbacked marketing — the math has to clear the backtest gates
against NWS CLI ground truth, and we have to assume Kalshi
market-makers aren't already doing the same things.

---

## Critical files to modify (when Bucket 2 unlocks)

- `trading_corp/agents/strategies/_weather_math.py:155` — `σ_total`
  computation (Items 2.1, 2.2, 2.3)
- `trading_corp/agents/strategies/_weather_math.py:279`
  (`sigma_for_horizon`) — replaced by `sigma_for_city_horizon`
  (Item 2.2)
- `trading_corp/persistence/db.py:232` (after `kalshi_round_trips`) —
  add `weather_forecast_residuals` table (Item 2.2)
- `scripts/ingest_nws_cli_residuals.py` (new) — NWS CLI scraper +
  residual writer (Item 2.2)
- `trading_corp/agents/strategies/kalshi_weather_arb.py:580–620` —
  model-weighting blend logic (Item 2.3)

**Don't touch (during observation week):**

- Any σ formula or weight (`_weather_math.py:155`)
- Any gate constant (`MAX_HORIZON_HOURS`, `SOURCE_DIVERGENCE_SIGMA_F`,
  `MIN_THRESHOLD_DELTA_SIGMA`)
- Any decision branch in `_evaluate_market` that affects `fired` or
  `outcome`
- The legacy `_CITY_COORDS_FALLBACK` dict (P4 removal is
  operator-gated separately)

---

## Verification design

### Bucket 1 (post-deploy) — STATUS: DONE 2026-05-25T14:10 UTC

See `reports/2026-05-25_kalshi_weather_bucket1_forward_watch.md`.

1. Query the prod audit table for new payload fields populating
   correctly. PASS — 3,153 yaml_verified rows since 2026-05-24T21:53:23.
2. Cycle time degradation check — non-blocking new fetch should not
   add >1s. (Not directly measured this pass; no `agent_error` rows
   from `kalshi_weather_arb` observed.)
3. No new `agent_error` audit rows. PASS.

### Bucket 2 (when items unlock)

- Each item has its backtest gate spelled out above.
- Backtests run via standalone scripts under `scripts/` reading from
  `data/trading_corp.db`. No prod execution; no positions touched.
- **Pass** = gate met on out-of-sample test partition with NWS CLI
  ground truth.
- **Fail** = item parked back in backlog with the backtest results
  recorded; do NOT iterate on the spec without operator review of the
  failure data.

---

## Operator decisions baked in (2026-05-24 review)

1. **HRRR source:** Open-Meteo only for Bucket 1. Confirmed via
   operator review. NOMADS native GRIB client is explicitly NOT in
   scope now — defer to Bucket 2 only if Open-Meteo's HRRR proves
   unreliable or HRRR becomes a canonical decision input.
2. **Coord-discipline is a hard gate on Item 1.1.** HRRR fetch MUST
   inherit xref-resolved coords. If implementation review surfaces ANY
   path where HRRR could resolve different coords than the existing
   forecast fetch, Item 1.1 drops to Bucket 2 and only Item 1.2 ships
   now. (Confirmed clean — Bucket 1 shipped with both items.)
3. **NWS Last-Modified header — degrade gracefully.** NWS endpoints DO
   send Last-Modified but the Akamai CDN layer causes per-request
   inconsistency. Item 1.2 design captures both
   `nws_forecast_issued_at` (preferred but nullable) and
   `nws_fetched_at` (always populated wall-clock fallback). NULL on
   `issued_at` is normal, not a bug — documented inline and in the
   field comment. (Observed: 100% NOT NULL in first 16h post-deploy;
   the loose "expect some NULL" baseline still holds.)

---

## Open questions for operator (at observation-week close)

1. **Backtest priority order for Bucket 2.** Of items 2.1
   (boundary-proximity), 2.2 (per-city residuals), 2.3 (horizon
   weighting), 2.4 (pace) — which addresses the autopsy's #1+#2
   finding most directly? Plan-time read: 2.1 is the cheapest direct
   hit on the boundary-loss pattern; 2.2 is the most principled but
   largest. Operator picks the order when observation week closes.
2. **Bucket 2 deploy gate beyond "operator says go."** Should Bucket 2
   items require Backtester sign-off per the gate documented in
   `PROJECT_CONTEXT.md § 11` / `CLAUDE.md § 4`? Default: yes —
   strategy-parameter changes to risk-sized money.

---

## What this plan does NOT do (intentional)

- No P4 advance recommendation. Separate gate.
- No reopening of killed Item 2 (hourly re-eval of OPEN positions).
  Item 2.4 is explicitly distinct — entry forecast adjustment for NEW
  same-day entries.
- No backtest execution. Specs only; gates spelled out; running them
  is later work.
- No changes during observation week. Confirmed hard constraint.
