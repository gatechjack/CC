# kalshi_weather — Tier 1 DATA FOUNDATION

> **Plan only — no code, no prod, no deploy. Board-gated execution per CLAUDE.md §4.**
> **All three components are BUILD-NOW-SAFE during the observation week** (additive data capture, write-only, no decision logic touched — same rationale as the Bucket 1 logging deploy).
> **CONSUMING this data into σ / bracket probabilities is a logic change and stays GATED until observation week closes (~2026-05-29) AND autopsy anomaly #2 is confirmed to repeat on independent station-dates.**

> **Probe-resolved (2026-05-25):** NBM source path and TXNSD units resolved against live endpoints; see §"Probe results" below. C2 schema gains a `logic_era` contamination-tag field per Board direction.

> **First-run findings registered (2026-05-25, post-build):**
> - **C3 rounding artifact RULED OUT as anomaly-#2 driver.** Backtest on the 4 unique autopsy tail-loss events: 0/4 had `risk_flag=True` at entry OR settlement. Forecast misses were 4-8°F; the F→C→F rounding mechanism caps at 1°F and cannot mechanically explain misses of this magnitude. Anomaly #2's fat tails are **genuine synoptic forecast misses** (the 5/23 cold push + KSEA warm event), NOT settlement-rounding microstructure.
> - **C3 re-scoped** from "anomaly-#2 explanation candidate" to "**boundary-bet guard tool**." Stays in the codebase; do not feed into σ-widening for anomaly #2 (the mechanism doesn't fit). Future use: per-bet entry-time flag for trades sitting in the rounding band, regardless of anomaly-#2 status.
> - **Item 2.1 (boundary-σ widening) DEMOTED to secondary.** Same logic: 2.1 is also a boundary treatment, capped at the same boundary scale; cannot address a 6°F miss either. Stays in queue but no longer the lead anomaly-#2 candidate.
> - **NBM-σ substitution PROMOTED to primary anomaly-#2 candidate.** Corroborated independently by C1 probe: NBM σ at 48h MaxT is **~56% wider** than the current `sigma_for_horizon` heuristic. The heuristic systematically under-estimates global σ across all horizons, which IS the kind of mechanism that can shift |z|≥2 frequency from 3.1× theoretical toward 1×. The gated-consumption work that swaps `_weather_math.py:165` `sigma_total` to read `weather_nbm_observations.temp_sigma_f` is now the lead fix for anomaly #2 (still gated until observation week closes and anomaly #2 confirms on independent station-dates).

---

## Context

`kalshi_weather_arb` is in an observation week through ~2026-05-29 measuring the fully-corrected logic (entry-price floor + 6 station-coordinate fixes + P3 xref loader). The 2026-05-24 24h autopsy
(`reports/2026-05-24_kalshi_weather_post_xref_24h_autopsy.md`) surfaced two live anomalies:

- **#1** — book is 100% NO / 87% NO-on-`between` → losses are correlated synoptic events, not independent samples.
- **#2** — σ_used is ~17% under-estimated with fat tails (empirical |z|≥2 at 3.1× theoretical, |z|≥3 at 10×).

Root cause suspected: σ is hand-built from model spread + a fixed buffer (`SOURCE_DIVERGENCE_SIGMA_F = 2.0`, `_weather_math.py:20`) plus a 4-bucket horizon table (`sigma_for_horizon`, `_weather_math.py:289-311`), with no calibration against the actual Kalshi settlement product (NWS CLI Daily Climate Report).

This plan builds the **data foundation** for every later σ/forecast improvement to be MEASURABLE against the real settlement product — not against Open-Meteo reanalysis (the only ground-truth used to date, explicitly flagged in the autopsy as a directional proxy, not authoritative).

**The "ground-truth backbone" argument is load-bearing.** Every σ-calibration claim we ever make hinges on the truth source. IEM-mirrored NWS CLI is the settlement product Kalshi reads. Choosing it as ground truth — and verifying it accessible / addressable per settlement station — is the prerequisite to all downstream calibration work being valid.

---

## Probe results (2026-05-25, gating decisions resolved)

The brief mandated verifying endpoints before designing around them. Three live WebFetch probes were run; results below.

### NBM source selection

| Candidate | Result | Verdict |
|---|---|---|
| **Path A** (IEM `mos/csv.php?model=nbe` per-station CSV — already in `weather_stations.yaml.feeds.nbm_bulletin` for every station) | Returns deterministic NBE MOS CSV. Columns: `station,model,runtime,ftime,n_x,tmp,dpt,cld,wdr,wsp,p06,p12,q06,q12,t06,t12,...`. **No `TXNP*`. No `TXNSD`. No percentile or uncertainty fields whatsoever.** | **REJECTED.** Despite the `nbm_bulletin` URL name in YAML, this endpoint is deterministic-only. The YAML feed-URL is misleading; the plan does not consume it. |
| **Path B-via-IEM-AFOS PIL** (`https://mesonet.agron.iastate.edu/wx/afos/p.php?pil=NBP{wfo}`) | Probed `NBPLOT` (Chicago), `NBPOKX` (NYC), `NBPSEW` (Seattle). Each returns exactly ONE per-station block, but that block is for the **WFO office identifier** (KLOT, KOKX, KSEW), NOT the airport ICAO (KMDW, KNYC, KSEA). Schema is correct: all `TXNMN`/`TXNSD`/`TXNP1`/`TXNP2`/`TXNP5`/`TXNP7`/`TXNP9` rows present. | **REJECTED as primary** (per-WFO-office, not per-airport — 30-100km off the airports Kalshi settles on). **RETAINED as documented fallback** for any station the bulk file is missing, with `nbm_source='iem_afos_wfo_fallback'` audit tag. |
| **Path B-via-NOMADS bulk text** (`https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.YYYYMMDD/CC/text/blend_nbptx.tCCz`) | File is 33 MB — exceeds WebFetch 10 MB cap, so per-airport block presence could not be DIRECTLY verified in plan mode. **Strong inference (acceptable for proceed):** the file contains 9,000+ station blocks (Phase 1 finding) using the same `K{ICAO}    NBM V5.0 NBP GUIDANCE` per-station header pattern observed in the per-WFO AFOS bulletins. 9,000+ ≫ ~140 WFOs implies per-airport granularity. Real Python `httpx.stream` ingestion will handle the 33 MB cleanly. | **CHOSEN AS PRIMARY.** First ingestion-script run must verify all 19 airport ICAOs appear as separate blocks. Any missing → log + hold + Board review (see open Q2). |

### TXNSD unit and magnitude verification (LIVE DATA, KOKX 2026-05-25T13:00 UTC)

NBM v4.2 card quote: **`"TXNSD = QMD Standard Deviation minimum/maximum temperature, F"`** — explicitly stdev, explicitly °F. Live KOKX block extracted:

```
TXNSD   2|  3   3|  5   2|  4   4|  6   5|  7   6|  7   5|  6   5|  6   5|  6
```

Format: `MinT_stdev | MaxT_stdev` per horizon column. Values range 2-7°F across 24-192h horizons — exactly the magnitude expected for forecast σ in °F. **Unit confirmed. Type confirmed (stdev, not variance, not range). Magnitudes sanity-check passed.**

**Comparison to current heuristic** at `_weather_math.py:289-311` (`sigma_for_horizon`: 1.5 / 2.5 / 3.5 / 5.0°F for 24/48/72/>72h, plus `SOURCE_DIVERGENCE_SIGMA_F = 2.0` in quadrature):

| horizon | NBM σ observed | current heuristic σ_total = sqrt(heur² + 4) | NBM vs heuristic |
|---|---|---|---|
| 24h MaxT | 3°F | sqrt(1.5² + 4) ≈ 2.50°F | NBM ~20% wider |
| 48h MaxT | 5°F | sqrt(2.5² + 4) ≈ 3.20°F | NBM ~56% wider |
| 72h MaxT | 4°F | sqrt(3.5² + 4) ≈ 4.03°F | matches |
| 168h MaxT | 6°F | sqrt(5.0² + 4) ≈ 5.39°F | NBM ~11% wider |

**Heuristic systematically under-estimates σ at the 24-48h horizon range — exactly where most weather bets are placed.** Direct corroboration of autopsy anomaly #2 (`|z|≥2` at 3.1× theoretical → heuristic σ is too narrow → tail probabilities under-priced). NBM substitution is a real candidate fix, not just a competitor idea.

### Percentile field correction (brief was wrong about field names)

NBM v4.2 card defines `TXNP1`/`TXNP2`/`TXNP5`/`TXNP7`/`TXNP9` as **deciles 1/2/5/7/9**, i.e., **P10/P20/P50/P70/P90**, NOT P10/P25/P50/P75/P90 as the brief assumed.

KOKX 24h MaxT decile vector verified:
- P10=79, P20=80, P50=81, P70=84, P90=86, mean (TXNMN)=82
- P90−P50 = 5°F; P50−P10 = 2°F → **asymmetric / left-skewed**
- Implies: substituting σ alone discards information about distributional shape. Schema captures all five percentiles to preserve the option for Tier-2 percentile-direct bracket-probability consumption (see open Q5).

Schema (C1 storage section) uses `temp_p10_f / temp_p20_f / temp_p50_f / temp_p70_f / temp_p90_f` to match what NBM actually publishes.

---

## Architecture

Three components; one dependency chain; zero live-logic touches.

```
config/weather_stations.yaml  (39 verified series → 19 ICAO stations)
          │
  WeatherStationsRegistry.list_verified_series()    ◀── NEW PUBLIC METHOD (prerequisite)
          │
    ┌─────┴─────────────────────────────────────────────┐
    │                                                   │
[C1] scripts/ingest_nbm.py                  [C2] scripts/ingest_iem_cli_residuals.py
   NBM probabilistic σ + percentiles            IEM CLI daily MaxT/MinT actuals
   4x daily (01z/07z/13z/19z)                  1x daily (incremental) + 90d backfill
          │                                                  │
   weather_nbm_observations (new table)        weather_forecast_residuals (refined schema)
          │                                                  │
          └──────────────────────────────────────────────────┘
                                │
                       [C3] _weather_math.cli_rounding_risk()
                       Pure function — F→C→F rounding-artifact predictor
                       + scripts/backtest_rounding_flip.py (read-only)
```

| Component | Location | Storage | Build-now-safe? | Gated-consumption path |
|---|---|---|---|---|
| C1 NBM ingestion | `scripts/ingest_nbm.py` + new client | `weather_nbm_observations` | **YES** (write-only) | Replace `_weather_math.py:165` `sigma_total` to consume `temp_sigma_f` |
| C2 Residuals + IEM CLI | `scripts/ingest_iem_cli_residuals.py` | `weather_forecast_residuals` | **YES** (write-only) | New `sigma_for_city_horizon` lookup replacing `sigma_for_horizon` |
| C3 Rounding-artifact fn | `_weather_math.py` (new pure fn) + `scripts/backtest_rounding_flip.py` | none | **YES** (pure function + read-only script) | Feed `risk_flag` into σ widening or threshold-prob adjustment |

---

## Prerequisite (must land before C1 or C2 starts)

### `WeatherStationsRegistry.list_verified_series()` — new public method

**File:** `trading_corp/data/weather_stations.py`

The registry currently exposes only `lookup_series(prefix)` and `lookup_station(icao)`. Both ingestion scripts need to ITERATE the bet-on station set; the iteration source `_doc.series` is private today. Required addition (READ-ONLY, no behavior change, no YAML schema change):

```python
def list_verified_series(self) -> list[tuple[str, SeriesEntry, StationEntry]]:
    """Yield (prefix, series_entry, station_entry) for every series where
    verified=True and disabled is falsy and settles_at resolves to a known
    station. Excludes the one disabled entry (KXTEMPNYCH). Callers
    iterate this to get the canonical bet-on station set."""
```

**Why this is the gate:** `_resolve_coords` in `kalshi_weather_arb.py:230` returns ONLY `lat`/`lon`/`coord_source`/`yaml_coords`/`legacy_coords` — it deliberately does NOT carry a station identifier back to callers (Phase 1 finding). The new ingestion path therefore CANNOT use `_resolve_coords` to recover the ICAO; it must go through the registry directly. Adding `list_verified_series()` makes the registry-direct path the only path, structurally preventing any future ingestion from accidentally inheriting the no-station gap.

**Build-now-safe.** Pure registry-internal addition; no callers change.

---

## Component 1 — NBM ingestion (calibrated uncertainty)

### Source endpoint — RESOLVED via 2026-05-25 probe

**Path B (NOMADS bulk text) — CHOSEN.** `https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.YYYYMMDD/CC/text/blend_nbptx.tCCz`. ~33 MB flat text, all 9,000+ NWS stations concatenated, each as a separate per-station block delimited by `K{ICAO}    NBM V5.0 NBP GUIDANCE    M/DD/YYYY  HHMM UTC`. Contains `TXNMN`, `TXNSD`, `TXNP1`/`TXNP2`/`TXNP5`/`TXNP7`/`TXNP9` per station per cycle.

**Why not Path A (IEM `mos/csv.php?model=nbe`)** — probed live 2026-05-25. Returns deterministic NBE-flavored MOS CSV only (columns: `station,model,runtime,ftime,n_x,tmp,dpt,cld,wdr,wsp,p06,p12,q06,q12,t06,t12,...`). **No percentile or stdev fields whatsoever.** Despite the `nbm_bulletin` name in `weather_stations.yaml.feeds`, this endpoint is unusable for σ capture. **The feed-URL field is misleading; the plan does not consume it.**

**Why not Path B-via-IEM-AFOS PIL pattern (`NBP{wfo}`)** — probed live 2026-05-25 on three WFOs:
- `NBPLOT` → returns KLOT (Chicago WFO office), NOT KMDW/KORD
- `NBPOKX` → returns KOKX (NYC WFO office), NOT KNYC/KJFK/KLGA
- `NBPSEW` → returns KSEW (Seattle WFO office), NOT KSEA

The IEM AFOS PIL pattern is **per-WFO-office, not per-airport.** Kalshi settles on airport ICAOs 30-100km from their WFO. Office-σ ≠ airport-σ (different elevation, microclimate, marine influence). Unusable as the primary source. Retained as a **structural fallback** if the NOMADS bulk file is unreachable: log a `nbm_source_fallback` audit event and use office-σ as a documented proxy.

**Path C (GRIB2 gridded) — out of scope, parked.** Sub-degree spatial interpolation; future option if Path B per-station blocks prove insufficient for any of our 19 airports.

### Per-station extractability — verification on first ingestion run

The NOMADS bulk file is too large for WebFetch's 10 MB cap, so per-airport-block presence cannot be confirmed in plan mode. **Strong inference:** the 9,000+ station population (Phase 1 finding) and the same per-station header pattern observed in the per-WFO AFOS bulletins (each shows exactly one `K{ICAO}    NBM V5.0 NBP GUIDANCE` block) together imply that the bulk file contains one block per AFOS-distributed station — which includes every major airport ICAO Kalshi settles on.

**Mandate on first ingestion-script run:** verify that each of the 19 verified-series airport ICAOs (KSEA, KMDW, KNYC, KHOU, KBOS, KDCA, KATL, KDFW, KPHL, KOKC, KMIA, KAUS, KMSP, KSAT, KSFO, KLAX, KDEN, KPHX, KMSY) appears as a separate station block in the parsed bulk file. Any missing ICAO → block ingestion for that ICAO, log `nbm_block_missing` audit event, hold for Board review before falling back to WFO-proxy. **Do not silently substitute.**

### Update cadence + scheduling

NBM probabilistic bulletins (NBP product) release **4x daily at 01z / 07z / 13z / 19z**. Scheduling: external cron / systemd timer on prod VM, **NOT** in-process at strategy entry time. Strategy hot path stays untouched. Suggested cron: `5 1,7,13,19 * * *` UTC (5-minute buffer for NOMADS upload lag).

### Parsing

- Path A: stdlib `csv` reader; per-station response is small; idiomatic dict-per-row.
- Path B: stream-parse the 33 MB file once per cycle; scan for known ICAO headers; extract the `TXNP*` and `TXNSD` rows per station; accumulate `dict[ICAO, dict]`; single SQLite batched-write transaction after all stations parsed.

### Storage schema — percentile names corrected per probe

**Probe correction:** the brief assumed P10/P25/P50/P75/P90 percentiles. NBM v4.2 actually publishes **deciles** P10/P20/P50/P70/P90 (`TXNP1`=10th, `TXNP2`=20th, `TXNP5`=50th, `TXNP7`=70th, `TXNP9`=90th). Schema uses the actual NBM percentile values.

New table appended to `SCHEMA` string in `trading_corp/persistence/db.py` (after `kalshi_equity_history`, ~line 285):

```sql
CREATE TABLE IF NOT EXISTS weather_nbm_observations (
    station_id    TEXT NOT NULL,    -- ICAO from registry ('KSEA', 'KMDW', ...)
    cycle_iso     TEXT NOT NULL,    -- NBM cycle init: ISO-8601 UTC ('2026-05-25T13:00:00+00:00')
    valid_iso     TEXT NOT NULL,    -- forecast valid date: ISO-8601 UTC day
    kind          TEXT NOT NULL,    -- 'daily_max' | 'daily_min'
    horizon_hours REAL NOT NULL,    -- valid_iso − cycle_iso, hours
    temp_p10_f    REAL NOT NULL,    -- TXNP1 (10th decile)
    temp_p20_f    REAL NOT NULL,    -- TXNP2 (20th decile) -- NOT P25 per probe
    temp_p50_f    REAL NOT NULL,    -- TXNP5 (median)
    temp_p70_f    REAL NOT NULL,    -- TXNP7 (70th decile) -- NOT P75 per probe
    temp_p90_f    REAL NOT NULL,    -- TXNP9 (90th decile)
    temp_sigma_f  REAL NOT NULL,    -- TXNSD = stdev in °F (PROBE-VERIFIED unit, 2-7°F across horizons)
    temp_mean_f   REAL NOT NULL,    -- TXNMN (NBM publishes explicit mean — non-nullable)
    nbm_source    TEXT NOT NULL,    -- 'nomads_bulk' | 'iem_afos_wfo_fallback' (drift sentinel; default 'nomads_bulk')
    icao_source   TEXT NOT NULL,    -- 'registry_yaml' always (DRIFT SENTINEL)
    ingested_at   TEXT NOT NULL,    -- ISO-8601 UTC wall-clock
    PRIMARY KEY (station_id, cycle_iso, valid_iso, kind)
);
CREATE INDEX IF NOT EXISTS ix_weather_nbm_station_valid
    ON weather_nbm_observations(station_id, valid_iso);
```

**`nbm_source` drift sentinel:** distinguishes the primary path (`'nomads_bulk'`) from the fallback (`'iem_afos_wfo_fallback'` — office-σ proxy). Any row with the fallback value flags that the calibration claim for that station/cycle is using an approximation. Calibration queries should partition by this column.

**Non-Gaussian asymmetry flag (informational):** NBM percentiles are NOT symmetric around the median (probe-observed: KOKX 24h MaxT has P50=81, P90−P50=5, P50−P10=2; left-skewed). Using `temp_sigma_f` alone for downstream probability assumes Gaussian. For Tier-1 build-now we capture all five percentiles so the gated-consumption phase can choose: (a) substitute σ into the existing Gaussian model (simpler), or (b) use the percentile vector directly for non-parametric bracket probability (sharper). Schema supports both options.

### Station discipline (load-bearing)

Ingestion flow:
1. `registry.list_verified_series()` → 39 verified series.
2. Deduplicate by `station_entry.icao` → 19 unique ICAOs (multiple series can settle at the same station, e.g., KXHIGHBOS + KXLOWBOS both → KBOS).
3. Fetch only for those 19 ICAOs. **Never** fetch for an ICAO absent from the registry. **Never** hardcode an ICAO list. **Never** map series prefix → station by string heuristic.
4. Write `icao_source = 'registry_yaml'` on every row — drift sentinel mirroring the `coord_source` pattern from `scripts/check_weather_coord_drift.sql`. Any future ingestion path that resolves ICAO differently will surface as a different value in this column.

### Anomaly #2 framing

**This component IS the candidate fix for autopsy anomaly #2.** The `temp_sigma_f` field is NWS-published, calibrated uncertainty — derived from the NBM ensemble's actual spread plus model-error climatology. It replaces both the heuristic `sigma_for_horizon` table (`_weather_math.py:289-311`) AND the fixed `SOURCE_DIVERGENCE_SIGMA_F = 2.0` buffer that was compensating for the calibration gap. When gated-consumption eventually wires NBM σ into `sigma_total` at `_weather_math.py:165`, the empirical |z|≥2 over-representation (currently 3.1× theoretical) should approach 1× if NBM σ is correctly calibrated for these stations.

### Relationship to existing plan

This C1 ingestion fills the data prerequisite that the existing plan's **Bucket 2 Item 2.2** lacks — Item 2.2 is the σ-calibration consumption layer; C1 is the source of one of its inputs (the others being HRRR from Bucket 1 + NWS + Open-Meteo). Item 2.2's pass/fail gate (test-set |z| band frequencies within ±20% of theoretical) becomes computable once C1 + C2 are populated. C1 also fills the role the existing plan's `sigma_for_city_horizon` lookup expected to fill on its own — but reframed: NBM is the baseline, not the whole story.

---

## Component 2 — Per-station residuals DB + IEM ground-truth ingestion

### Framing — residuals as a CORRECTION LAYER, not from-scratch σ

The existing plan (Bucket 2 Item 2.2) framed residuals as the whole calibration story — replace the heuristic σ with measured per-city residual stdev. **This plan reframes:** NBM is the baseline σ distribution (C1); residuals are a correction layer on top of NBM. The residual table answers: *"given what NBM forecast, how far off was actual settlement historically per station/season?"* The correction is two-part:
- **Bias offset** = mean of (`actual − forecast`) per (station, source, season).
- **Dispersion multiplier** = stdev of residuals divided by NBM-published `temp_sigma_f`.

This is structurally sounder than a from-scratch σ because NBM already incorporates ensemble spread, model disagreement, and climatological uncertainty. The residuals DB exists to catch systematic LOCAL biases the global ensemble misses (e.g., KMSP cold pool effects, KSAT urban heat island, KSEA marine layer timing). The autopsy's anomaly #2 concentration in 5 stations (KMSP / KSAT / KAUS / KSEA / KHOU near-miss) is exactly the signature this layer is designed to detect and correct.

### Refined schema

The existing plan's `weather_forecast_residuals` schema is extended (NOT duplicated):

```sql
CREATE TABLE IF NOT EXISTS weather_forecast_residuals (
    station_id      TEXT NOT NULL,
    target_date     TEXT NOT NULL,    -- ISO date '2026-05-25'
    kind            TEXT NOT NULL,    -- 'daily_max' | 'daily_min'
    target_iso      TEXT,             -- NULL for daily (hourly use only)
    forecast_temp_f REAL NOT NULL,
    actual_temp_f   REAL NOT NULL,    -- IEM CLI value — THE GROUND TRUTH
    forecast_source TEXT NOT NULL,    -- 'nbm_p50' | 'nbm_mean' | 'nws_blend' | 'hrrr' | 'open_meteo_ensemble'
    horizon_hours   REAL NOT NULL,
    residual_f      REAL NOT NULL,    -- actual_temp_f − forecast_temp_f (signed)
    cycle_iso       TEXT,             -- NBM cycle ISO when source='nbm_*'; NULL otherwise
    season          TEXT NOT NULL,    -- 'winter'|'spring'|'summer'|'fall' (derived, see open Q3)
    logic_era       TEXT NOT NULL,    -- 'pre_station_fix' | 'post_station_fix' | 'native_post_fix'
    icao_source     TEXT NOT NULL,    -- 'registry_yaml' always (drift sentinel)
    ingested_at     TEXT NOT NULL,
    PRIMARY KEY (station_id, target_date, kind, forecast_source, cycle_iso)
);
CREATE INDEX IF NOT EXISTS ix_wfr_station_horizon
    ON weather_forecast_residuals(station_id, horizon_hours, season, logic_era);
CREATE INDEX IF NOT EXISTS ix_wfr_target_date
    ON weather_forecast_residuals(target_date, kind);
```

**Key additions vs existing plan's schema:**
- `cycle_iso` joins the PK — multiple NBM cycles for the same target date produce distinct rows (07z cycle at 36h horizon ≠ 19z cycle at 20h horizon). NULL for non-NBM sources.
- `season` (derived, TEXT) — enables seasonal partition. Per autopsy: late-spring/early-summer KMSP/KSAT/KAUS tail-loss pattern lands on the spring/summer boundary; convention matters (open Q3).
- `logic_era` (NEW per Board direction 2026-05-25) — contamination tag for backfilled residuals. Values:
  - `'pre_station_fix'`: forecast row's `forecast_temp_f` was generated by the live strategy BEFORE the 2026-05-22 6-station coordinate corrections shipped (commit `f5a5fd5` at 2026-05-22T16:25 UTC). For NYC/CHI/HOU specifically, the lat/lon used for the forecast was the WRONG STATION (KJFK/KORD/KIAH instead of KNYC/KMDW/KHOU). These rows are joinable to the historical audit corpus but **must be filtered out of any per-station calibration baseline** — they would silently re-introduce the exact station-mismatch bug we fixed into the σ recalibration.
  - `'post_station_fix'`: forecast row was generated by the live strategy AFTER 2026-05-22T16:25 UTC. Coords are correct. Safe for calibration.
  - `'native_post_fix'`: forecast row was generated by the new NBM ingestion (C1) — not from historical audit, but ingested directly. Always uses registry-resolved coords by construction. Safe for calibration. Used for any NBM-source residual computed from a fresh NBM cycle joined to a same-day IEM CLI actual.
- `icao_source = 'registry_yaml'` always — drift sentinel for the ingestion path itself.
- `forecast_source` values cleaned: `'nbm_p50'` and `'nbm_mean'` distinguished from `'nws_blend'`; legacy plan's `'nws' | 'blend'` becomes `'nws_blend'` (the strategy's blended forecast); HRRR and Open-Meteo ensemble preserved.

**Backfill default filter:** any calibration query, lookup function, or backtest gate reads `WHERE logic_era != 'pre_station_fix'` by default. Pre-fix rows remain in the table for forensic comparison (e.g., "how badly did the wrong-station forecast miss vs how badly does the right-station forecast miss?") but never enter a calibration product. Document this filter in the `sigma_for_city_horizon` consumption-layer spec.

### Ground truth endpoint

**`https://mesonet.agron.iastate.edu/json/cli.py?station={ICAO}&year=YYYY`**

HTTP 200 verified. Response: `{"results": [{"valid": "YYYY-MM-DD", "high": int, "low": int, ...}, ...]}`. ICAO-keyed (matches registry); 25+ years of history (backfill confirmed to 2010); daily granularity. Commercial use permitted; `robots.txt` Crawl-delay: 120s (irrelevant for once-daily 19-station polling, but observe per-request courtesy).

**CF6 NOT used.** CF6 is a monthly summary (post-month-close, lag); CLI is the daily product Kalshi settles on.

### Ingestion script — `scripts/ingest_iem_cli_residuals.py`

Argparse: `--station ICAO` (omit for all 19), `--year YYYY`, `--db PATH`, `--backfill N` (days; default 90), `--incremental` (fetch only `valid >= max(target_date) − 2 days`).

Flow per run:
1. `registry.list_verified_series()` → 19 unique ICAOs.
2. For each ICAO: GET IEM JSON → daily `(valid, high, low)` tuples.
3. Join to historical `kalshi_weather_evaluated` audit rows (already carry `forecast_temp_f`, `target_iso`, `horizon_hours`, `hrrr_temp_f` from Bucket 1) by `(station_id, target_date)`.
4. Join to `weather_nbm_observations` for matching `(station_id, valid_iso, kind)` to recover `nbm_p50` / `nbm_mean` / NBM `cycle_iso`.
5. For each `(station, target_date, kind)` with a CLI actual: write one residual row per available forecast source.
6. `icao_source = 'registry_yaml'`.
7. **`logic_era` assignment (contamination guard):**
   - If `forecast_source` starts with `'nbm_'` AND `cycle_iso` is populated → `'native_post_fix'` (the row was generated by C1's registry-direct ingestion).
   - Else if the source row came from `kalshi_weather_evaluated` audit AND that row's `ts >= '2026-05-22T16:25:00'` → `'post_station_fix'`.
   - Else (audit row with `ts < '2026-05-22T16:25:00'`, regardless of station — applies to ALL stations, not just NYC/CHI/HOU, because the strategy may have used legacy coords elsewhere) → `'pre_station_fix'`.
   - Additional safety: for NYC/CHI/HOU specifically (the 6 corrected stations: KNYC/KMDW/KHOU + 3 others if in xref corrections), if the audit row's `coord_source` field is not `'yaml_verified'`, force `logic_era = 'pre_station_fix'` regardless of `ts`. This catches edge cases where a row may have evaluated through a non-yaml-verified path post-fix.

**Backfill plan:** First run = 90-day backfill across all 19 stations. Matches the 60–90 day rolling window the existing plan's `sigma_for_city_horizon` calls for to hit ≥20 samples per (station, source, season) partition. 19 stations × 90 days × ~5 second inter-request sleep ≈ 25 min one-time. Incremental daily runs: 19 requests, ~2 minutes.

**BUILD-NOW-SAFE.** Writes only to a new isolated table; no live-strategy read path.

**GATED-CONSUMPTION** (out of scope here): the `sigma_for_city_horizon` lookup function the existing plan describes is the consumption layer for this table; remains board-gated.

---

## Component 3 — F→C→F rounding-artifact deterministic model

### Mechanism (the physical hypothesis)

ASOS sensors observe in **°C**, round to 1 decimal, then convert to °F for the NWS CLI report (the integer-valued daily MaxT / MinT Kalshi settles against). A small slice of the real-valued °C reading can map to either of two °F integers depending on rounding direction. The settlement risk is concentrated when the public-feed °F is close to a market threshold:

| underlying °C | °C→°F (raw) | CLI rounded °F |
|---|---|---|
| 22.7 | 72.86 | 73 |
| 22.6 | 72.68 | 73 |
| 22.5 | 72.50 | **72 or 73** (rounds-to-even may pick 72) |
| 22.4 | 72.32 | 72 |

A bet on "MaxT between 73 and 75" loses if the CLI settles at 72 — even when the public ASOS reading at end-of-day showed 73. This is **deterministic**, **predictable** from the underlying °C value, and (the hypothesis) is a non-trivial fraction of the autopsy's tail-loss inventory.

### Function spec

**Location:** `trading_corp/agents/strategies/_weather_math.py` (new pure function, bottom of file; no modifications to existing functions).

```python
def cli_rounding_risk(
    public_temp_f: float,         # the public-feed °F value (ASOS report or forecast)
    threshold_f: int,             # the Kalshi market threshold (integer)
    direction: Literal["max", "min"],
) -> dict:
    """
    Returns:
        {
          "risk_flag": bool,             # True iff F→C→F rounding could flip settlement vs public
          "delta_predicted_f": float,    # signed predicted deviation (typically -1.0, 0.0, or +1.0)
          "candidate_c_values": list[float],  # the °C readings in the ambiguity band
          "rationale": str,              # one-line human explanation
        }

    Algorithm:
      1. Convert public_temp_f → °C, round to 1 decimal: c = round((F − 32) × 5/9, 1).
      2. Enumerate the rounding-neighborhood °C candidates: {c − 0.1, c, c + 0.1}.
      3. For each candidate c_neighbor: f_neighbor = round(c_neighbor × 9/5 + 32).
      4. risk_flag = True iff any f_neighbor differs from round(public_temp_f)
         AND that difference crosses threshold_f (given direction).
      5. delta_predicted_f = the signed difference toward the threshold-crossing side.
    """
```

### Two use modes (same function, different inputs)

- **Entry-time use**: pass `public_temp_f = forecast_temp_f` (model forecast at bet decision). Output flags whether the forecast sits in a band where a 1°F settlement flip is mechanically possible — used to identify boundary-adjacent bets at decision time.
- **Settlement-time use**: pass `public_temp_f = ASOS observation just before CLI publishes`. Output predicts whether CLI will read a different integer than the public observation. Useful for last-mile risk monitoring on open positions.

Both modes share the same algorithm; the function is mode-agnostic.

### Measurability against the residuals DB

`scripts/backtest_rounding_flip.py` (read-only) joins:
- `weather_forecast_residuals` (C2 table) — has `forecast_temp_f` AND `actual_temp_f` AND the threshold derivable from the ticker
- the deterministic `cli_rounding_risk()` output computed per row

and reports:

1. **Predictive base rate**: among boundary-adjacent rows (`|forecast − threshold| < 1.0°F`), what fraction have `risk_flag = True`?
2. **Flip realization rate**: among `risk_flag = True` rows, what fraction settled at `actual_temp_f = round(forecast_temp_f) − 1`? Compare to the same rate for `risk_flag = False` boundary-adjacent rows (base rate).
3. **Headline number for anomaly #2**: of the 12 tail-loss rows in the autopsy (5 unique station-date events: KMSP, KSAT, KAUS, KSEA, KHOU-near-miss), how many had `risk_flag = True`? **This single percentage decides whether the rounding-flip hypothesis is the dominant driver of anomaly #2 or just a contributor.**

### Subsumption note (vs existing plan's Item 2.1)

If the C3 backtest shows `risk_flag = True` rows realize the 1°F-below outcome at substantially higher rates than the base, **C3 likely subsumes existing plan's Item 2.1 (boundary-proximity σ widening)**. Item 2.1 was a statistical widening at the boundary; C3 is a deterministic predictor. Direct prediction is sharper than blunt widening when the underlying mechanism is mechanical (it is). Re-evaluate Item 2.1 only AFTER C3 backtest results land — don't build both.

**BUILD-NOW-SAFE.** Pure function + read-only analysis script. No prod execution; no position effects.

**GATED-CONSUMPTION** (out of scope here): feeding `risk_flag` / `delta_predicted_f` into the σ computation or threshold-crossing probability at `_weather_math.py:165` — board-gated.

---

## Backtest design (load-bearing — ground truth = IEM CLI throughout)

**Time-split:** train on `entry_ts < (T_today − 30 days)`, test on `T_today − 30 days ≤ entry_ts < T_today`. Both partitions use IEM CLI `actual_temp_f` from C2.

### Gates

1. **NBM σ calibration gate.** Compute `z = (cli_actual − nbm_p50) / nbm_sigma_f` per test-partition row. Empirical |z|≥2 frequency (currently 3.1× theoretical from autopsy's Open-Meteo proxy) should approach 1× when NBM σ replaces heuristic σ. **Don't pre-commit a numerical target** (per the existing-plan Item 2.1 reframe earlier this session); measure the actual reduction. If NBM alone doesn't close the gap, residual-corrected NBM (NBM σ × per-station dispersion multiplier from train partition) should.

2. **Rounding-flip explanatory power gate.** `risk_flag = True` rows should show the 1°F-below settlement rate substantially above the `risk_flag = False` base on the same boundary-adjacent population. **Don't pre-commit** the multiplier; record both rates.

3. **WR non-degradation gate.** Re-simulate the observation-week trades with (NBM σ + rounding-flip suppressor) substituted. WR must be ≥ the autopsy's 70.7% baseline within 1 standard error. Catches calibration improvements that suppress too many winners.

### Why this is the only valid gate design

Every prior σ claim was against Open-Meteo reanalysis as proxy. Open-Meteo is gridded, hour-aligned, model-fit — not what Kalshi settles on. Substituting IEM CLI as truth is the prerequisite to ANY σ improvement claim being valid. The residuals DB is the apparatus that makes IEM CLI joinable to every forecast source on equal footing. Without C2, no σ change is measurable. C2 IS the gate.

---

## Open questions for Board

**Resolved by 2026-05-25 probe** (no longer open):
- ~~Source path A vs B~~ — Path A returns no percentiles; IEM-AFOS PIL pattern is per-WFO-office not per-airport; **NOMADS bulk text is the only viable per-airport source.** Plan rewritten accordingly.
- ~~TXNSD unit convention~~ — NBM v4.2 card quote: `"TXNSD = QMD Standard Deviation minimum/maximum temperature, F"`. Live KOKX values 2-7°F across horizons match expected stdev magnitudes. **Confirmed: stdev, °F.**
- ~~Percentile field meaning~~ — TXNP1/P2/P5/P7/P9 are **deciles** (10/20/50/70/90), not P10/P25/P50/P75/P90. Schema corrected.

**Still open:**

1. **Subsumption decision criterion for existing plan Item 2.1.** What evidence threshold from the C3 backtest parks Item 2.1? **Suggested:** if `risk_flag = True` rows explain ≥50% of the autopsy's 5 tail-loss events AND show ≥2× the base-rate flip realization, Item 2.1 is parked and C3 gated-consumption becomes the sole boundary treatment. Otherwise Item 2.1 stays in queue. Board confirms?

2. **Per-station bulk-file presence verification policy.** First ingestion run will check whether each of the 19 airport ICAOs has a per-station block in the NOMADS bulk file. If any is missing (e.g., KMSY isn't AFOS-distributed in NBP), the plan says: log + hold + Board review. **Confirm** that's the right escalation, vs auto-falling-back to office-σ for the missing station with a clear `nbm_source='iem_afos_wfo_fallback'` audit. Suggested default: log + hold (no silent substitution), but Board may prefer the fallback to preserve coverage.

3. **Season convention for the `season` field.** Meteorological convention is `[Dec,Jan,Feb]=winter, [Mar,Apr,May]=spring, [Jun,Jul,Aug]=summer, [Sep,Oct,Nov]=fall`. The autopsy's KMSP/KSAT/KAUS tail-loss event landed late May 2026 — under this convention, *spring*. If late-spring cold pushes are sometimes calibration-relevant alongside summer extremes, a different partition (e.g., 6-bucket by 2-month windows) might better surface the pattern. Default to meteorological 4-bucket unless Board says otherwise.

4. **IEM polling cadence for incremental updates.** 1x daily seems sufficient (CLI publishes daily). Should it run twice (once at 06z, once at 18z) to catch late-published CLI products? Operational simplicity says 1x; defensiveness says 2x. Default to 1x.

5. **Tier-2 consumption: σ-substitution vs percentile-direct.** The probe surfaced that NBM distributions are non-Gaussian (KOKX 24h MaxT: mean 82, median 81, P90−P50=5, P50−P10=2 → left-skewed). Two consumption-design options (gated-consumption, future board decision):
   - **(a)** Substitute `temp_sigma_f` for the heuristic σ in `_weather_math.py:165`. Simpler, preserves the Gaussian model, may miss skewness signal.
   - **(b)** Use the percentile vector P10/P20/P50/P70/P90 directly to compute bracket probability via linear interpolation between deciles. Sharper, but ~100 LOC more, requires changes to `evaluate_weather_market`, harder to backtest against the existing audit corpus.
   The Tier 1 schema captures all five percentiles + σ + mean, so this decision can be deferred. Surfacing now so the Board sees the option exists.

---

## Out of scope (named-parked — do NOT plan here)

- **Cross-platform CLI-vs-Wunderground arbitrage** — Wunderground reports vs official NWS CLI settlement; not in scope, not planned, not a near-term priority.
- **Premium commercial APIs** — Weatherbit, Tomorrow.io, IBM/TWC. No evidence of edge that justifies per-call pricing over the free NWS/IEM stack. Revisit only if NBM + residuals demonstrate uncaptured edge.
- **HMM / regime detection** — Hidden Markov regime models. Adds non-deterministic inference to a deterministic-first design. Parked indefinitely.
- **AI-ensemble forecast models** — GenCast, AIFS. Need bespoke API access / compute; the Kalshi Gaussian pricing model doesn't need a better point forecast, it needs a well-calibrated σ. NBM provides that without AI complexity.
- **NBM via AWS NODD S3** — alternative delivery for the same data NOMADS already serves free. Convenience play; not a capability upgrade.
- **NBM Path C (GRIB2 gridded)** — sub-degree spatial interpolation. Maximum precision, maximum implementation cost. Parked until text bulletins (Path A/B) prove insufficient.

---

## Verification (post-build, per component)

Per-component verification steps below. C1's mandatory 19-ICAO bulk-file presence check is described in the canonical place — C1 §"Per-station extractability" above; not repeated here.

**For C1 (post-first-cycle):**
- `SELECT station_id, COUNT(*) FROM weather_nbm_observations GROUP BY station_id` — expect 19 rows (or fewer if first run halted per §"Per-station extractability" escalation policy), all `icao_source = 'registry_yaml'`, all `nbm_source = 'nomads_bulk'`.
- Spot-check one row's `temp_sigma_f` against the NBM dashboard for the same station/cycle.
- Extend `scripts/check_weather_coord_drift.sql` to include `icao_source` + `nbm_source` fields — expect zero non-canonical values.

**For C2 (post-90-day-backfill):**
- `SELECT station_id, COUNT(*) FROM weather_forecast_residuals WHERE logic_era != 'pre_station_fix' GROUP BY station_id` — expect ~90 daily_max + ~90 daily_min per station per source available (NBM rows depend on C1 having accumulated some cycles first).
- Spot-check one row's `actual_temp_f` against the IEM CLI JSON for that station/date.
- Verify `logic_era` partition: `SELECT logic_era, COUNT(*) FROM weather_forecast_residuals GROUP BY logic_era` — expect a non-zero `pre_station_fix` bucket for NYC/CHI/HOU pre-2026-05-22 rows (proves the contamination tag is working), and `post_station_fix` + `native_post_fix` buckets dominant for everything else.
- Verify `forecast_source` distribution: each station should have rows for every source where forecasts existed.

**For C3 (post-build):**
- Unit tests on `cli_rounding_risk()` for known boundary cases (e.g., `(72.5, 73, "max") → risk_flag True`).
- Run `scripts/backtest_rounding_flip.py` against the residuals DB; report the three rates from C3 §"Measurability against the residuals DB" (predictive base rate, flip realization rate, autopsy-tail-loss explanation %).

---

## Cross-references (other plans this affects)

- **`plans/forecast-quality-improvements-for-kalshi-prancy-porcupine.md` (committed `bbe55a9`)** must get a top-of-file pointer to this Tier 1 plan explaining that its Bucket 2 Items 2.1 and 2.2 acquire their data prerequisite from C1 + C2 here, and Item 2.1 may be parked after C3's backtest. **This pointer must be added in the same commit as the Tier 1 plan, otherwise the two plans drift.**

---

## Build-now-safe vs gated-consumption — summary

| Piece | Build-now-safe | Gated-consumption |
|---|---|---|
| `list_verified_series()` registry method | ✓ | n/a |
| `weather_nbm_observations` schema + ingestion script | ✓ | Replace `_weather_math.py:165` sigma_total to read `temp_sigma_f` |
| `weather_forecast_residuals` schema + IEM ingestion script | ✓ | New `sigma_for_city_horizon(station, horizon, source, season)` lookup replacing `sigma_for_horizon` |
| `cli_rounding_risk()` pure function | ✓ | Feed `risk_flag` / `delta_predicted_f` into σ widening or threshold-prob suppression |
| `backtest_rounding_flip.py` read-only script | ✓ | Decide whether existing plan Item 2.1 (boundary-σ widening) is parked |

**The entire Tier 1 build is data foundation only. Every consumption path stays board-gated until (a) observation week closes and (b) anomaly #2 is confirmed to repeat.**

---

## Data-accumulation timeline (registered 2026-05-25, post prod-scale measurement)

Prod-scale ingestion run on the full 59,342-row `kalshi_weather_evaluated` audit corpus
(2026-05-14 → 2026-05-25) produced 27,068 unique residual rows after PK collapse. State
of the calibration data **today** and what changes over time:

| What | Today | When it unlocks | Why the wait |
|---|---|---|---|
| Clean calibration rows | **6,446** post-fix `nws_blend` residuals | already available | post_station_fix subset of current corpus |
| NBM-source residuals | **0** | **~2 days** | NBM forecasts cover 5/26+; IEM CLI only published through 5/24. Once IEM publishes 5/26+, the NBM cycles already ingested join in |
| `nbm_p50` / `nbm_mean` per-(station, horizon) sample size | 0 | ~30 days after NBM joins start | Item 2.2 σ-calibration gate requires ≥20 samples per (station, source, season) partition |
| Cross-season calibration (winter, summer, fall) | not possible | **~6 months** | Corpus only spans May 2026 = spring only. Need real winter/summer/fall data to validate seasonal residuals |
| Multi-source diversification (HRRR, Open-Meteo ensemble) | thin | weeks-to-months | HRRR audit-row joins started 2026-05-24 (Bucket 1 deploy); ensemble joins are already possible but not yet ingested into residuals |

**Practical schedule:**
- **~2026-05-27 (~T+2d):** first NBM-source residual rows appear. NBM-σ vs IEM-actual deltas become measurable.
- **~2026-06-24 (~T+30d):** Item 2.2's `sigma_for_city_horizon(station, horizon, source)` lookup becomes computable for spring-station-NBM partition at ≥20 samples — the threshold-of-no-fallback in the lookup function spec.
- **~2026-11-25 (~T+6mo):** cross-season residuals accumulated; full seasonal calibration becomes possible.

**What this means for anomaly #2 fix scheduling:**
- NBM-σ substitution (the primary anomaly-#2 candidate per the 2026-05-25 registration above) is **technically unlockable for spring in ~30 days** — assuming the cron poller is running and anomaly #2 has confirmed to repeat on independent station-dates by then.
- A fully **season-robust** NBM-σ replacement is a **multi-month accumulation problem**, not a code problem. Don't expect a season-robust calibration verdict before that data exists.
- The boundary-treatment candidates (Item 2.1 boundary-σ widening, C3 rounding-flip — both already demoted/ruled-out) needed even less data than NBM-σ; their fates are decided.

---

## Next deliberate step (GATED — separate prod deploy, Board says go)

**Cron / systemd poller for ongoing NBM + IEM ingestion.**

Without this, the data-accumulation clock above doesn't start. Both ingestion scripts are
one-shot today; for the timeline to actually elapse, the prod VM needs scheduled invocations:

- `scripts/ingest_nbm.py` — every 6h, aligned to NBM cycle release (01z / 07z / 13z / 19z + 5 min lag): `5 1,7,13,19 * * *` UTC
- `scripts/ingest_iem_cli_residuals.py --incremental` — 1x daily after IEM publishes prior-day CLI: suggested `15 14 * * *` UTC (gives IEM 14:00 UTC to settle yesterday's CLI publication)

**This is a prod deploy + service consideration.** Same hash-compare/backup/verify discipline
as prior deploys (per `runbooks/deploy_log.md` patterns):
- `scp` (or chunked az push) of the 6 new/modified Python files to prod
- md5-verify each against local
- Append systemd `*.service` + `*.timer` units (timers, not service-restart of trading-corp)
- `systemctl daemon-reload`, `systemctl enable --now nbm-ingest.timer iem-ingest.timer`
- **No restart of `trading-corp.service` required** — the new scripts run standalone, are not imported by the live strategy.

**Trading impact:** none if scoped to timers. The live strategy doesn't read from
`weather_nbm_observations` or `weather_forecast_residuals` (consumption stays board-gated).
The deploy is purely additive data collection.

**When ready:** plan as a standalone reviewed deploy. Use the existing deploy_log template;
backup tags on any modified existing files; rollback recipe; first-fire verification of
each timer's first invocation; row-count sanity-check the next day.

**Until then:** the foundation is complete and trustworthy at scale (per prod-scale measurement
above). The rest is data-accumulation time. No more building today.
