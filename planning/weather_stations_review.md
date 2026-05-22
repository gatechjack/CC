# Weather Stations — Phase 2 Verification Review

**Status:** in progress · **Total entries:** 39 · **Verified so far:** 0

This document is your read-only reference for verifying each Kalshi
weather-series → settlement-station mapping in
`config/weather_stations.yaml`. Per design Q2/Q5, **only verified
entries can drive live trades** — the `require_verified_station_for_live`
risk gate (P5) will reject any series with `verified: false` once
wired in. P3 (loader wiring with audit diff vs legacy dict) is
blocked on Phase 2 completion.

## How to verify one entry

1. Read its **Rules primary** excerpt below. Confirm the station name
   Kalshi cites matches the `settles_at` row in the metadata table.
2. If `Rules secondary` is populated, cross-check the CLI product code
   (e.g. `CLISEA`) and WFO (e.g. `wfo=sew`) against the metadata table.
3. If everything matches, run the **Verify command** shown for that
   entry. The helper prints a diff and waits for your `y` confirmation.
4. Commit each verification individually:
   ```
   git diff config/weather_stations.yaml
   git add config/weather_stations.yaml
   git commit -m "weather_stations: verify KXHIGHTSEA"
   ```
   You can batch a few small ones into one commit if reviewing them in
   the same session — your call.

## How to flag an entry as wrong

If the rules excerpt does NOT match the `settles_at` mapping:
- Do NOT run the verify command.
- Open an issue or note in this doc and ping the next session — the
  audit JSON or the YAML may need correction before verification can
  resume.

## Reading order

Two sections, in order:

- **Section A — Needs careful review (7 entries):** the 6 mappings we
  corrected on 2026-05-22, plus the KXTEMPNYCH disable.
- **Section B — Unaudited (32 entries):** the remaining series. Order
  is alphabetical by series prefix.

---

## Section A — NEEDS CAREFUL REVIEW (7 entries)

These entries were corrected by commit `e02258d` on 2026-05-22 or are
otherwise non-standard. Read each one carefully before running any
verify command.

---

### 1. KXLOWTNYC

> **⚠ NEEDS CAREFUL REVIEW — recently corrected**
> Previously mapped to `KJFK` (~12 mi off). Corrected to `KNYC` (Central Park) by commit `e02258d` on 2026-05-22. Read the rules excerpt below and confirm Central Park is the actual settlement station. Central Park is +3°F warmer for highs than KJFK in 30-day ASOS.

| Field | Value |
|---|---|
| settles_at | `KNYC` (Central Park, New York) |
| nws_wfo | `OKX` |
| cli_product | `CLINYC` |
| cli_location_name | `Central Park NY` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at New York City for May 22, 2026, is greater than 56° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLINYC can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=okx, navigating to the "Observed Weather" tab, and choosing the location "Central Park NY" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords in code use JFK (40.6413,-73.7781); Kalshi resolves at Central Park, not JFK. Significant station mismatch.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTNYC --via KXLOWTNYC-26MAY22-T56
```

---

### 2. KXHIGHNY

> **⚠ NEEDS CAREFUL REVIEW — recently corrected**
> Previously mapped to `KJFK` (~12 mi off). Corrected to `KNYC` (Central Park) by commit `e02258d` on 2026-05-22. Read the rules excerpt below and confirm Central Park is the actual settlement station. Central Park is +3°F warmer for highs than KJFK in 30-day ASOS.

| Field | Value |
|---|---|
| settles_at | `KNYC` (Central Park, New York) |
| nws_wfo | `OKX` |
| cli_product | `CLINYC` |
| cli_location_name | `Central Park NY` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the highest temperature recorded in Central Park, New York for May 22, 2026 as reported by the National Weather Service's Climatological Report (Daily), is less than 63°, then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Not all weather data is the same. While checking a source like AccuWeather or Google Weather may help guide your decision, the official and final value used to determine this market is the highest temperature as reported by the corresponding NWS Climatological Report (Daily) linked in the rules above.

**Notes (from audit):** CRITICAL MISMATCH: Older-format market, no CLI code. Rules cite Central Park; _CITY_COORDS_FALLBACK['NY'] uses JFK coords (40.6413,-73.7781). Central Park is ~12 miles from JFK — significant divergence possible. KXLOWTNYC (newer) cites CLINYC/Central Park via WFO okx, confirming Central Park is correct resolution station.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHNY --via KXHIGHNY-26MAY22-T63
```

---

### 3. KXHIGHCHI

> **⚠ NEEDS CAREFUL REVIEW — recently corrected**
> Previously mapped to `KORD` (~17 mi off). Corrected to `KMDW` (Midway) by commit `e02258d` on 2026-05-22. Read the rules excerpt below and confirm Midway is the actual settlement station. Midway and ORD are within ~0°F for highs but Midway is +1°F warmer for lows.

| Field | Value |
|---|---|
| settles_at | `KMDW` (Chicago Midway International Airport) |
| nws_wfo | `LOT` |
| cli_product | `CLIMDW` |
| cli_location_name | `Chicago - Midway, IL` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the highest temperature recorded at Chicago Midway, IL for May 22, 2026, is greater than 71° according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Not all weather data is the same. While checking a source like AccuWeather or Google Weather may help guide your decision, the official and final value used to determine this market is the highest temperature as reported by the corresponding NWS Climatological Report (Daily) linked in the rules above.

**Notes (from audit):** CRITICAL MISMATCH: Older-format market, no CLI code. Rules cite Midway; _CITY_COORDS_FALLBACK['CHI'] uses ORD coords (41.9742,-87.9073). KXLOWTCHI (newer) explicitly cites CLIMDW/Chicago-Midway (WFO lot). ORD vs MDW difference of ~17 miles, temps can differ 2-4F.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHCHI --via KXHIGHCHI-26MAY22-T71
```

---

### 4. KXLOWTCHI

> **⚠ NEEDS CAREFUL REVIEW — recently corrected**
> Previously mapped to `KORD` (~17 mi off). Corrected to `KMDW` (Midway) by commit `e02258d` on 2026-05-22. Read the rules excerpt below and confirm Midway is the actual settlement station. Midway and ORD are within ~0°F for highs but Midway is +1°F warmer for lows.

| Field | Value |
|---|---|
| settles_at | `KMDW` (Chicago Midway International Airport) |
| nws_wfo | `LOT` |
| cli_product | `CLIMDW` |
| cli_location_name | `Chicago - Midway, IL` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Chicago for May 22, 2026, is between 46-47° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIMDW can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=lot, navigating to the "Observed Weather" tab, and choosing the location "Chicago - Midway, IL" with Daily Climate Report selected.

**Notes (from audit):** CRITICAL MISMATCH: Fallback coords (41.9742,-87.9073) are ORD, but Kalshi resolves at Midway (MDW, approx 41.7868,-87.7522). KXHIGHCHI also cites Midway. Code fallback for CHI and TCHI is consistently wrong station.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTCHI --via KXLOWTCHI-26MAY22-B46.5
```

---

### 5. KXHIGHTHOU

> **⚠ NEEDS CAREFUL REVIEW — recently corrected**
> Previously mapped to `KIAH` (~24 mi off). Corrected to `KHOU` (Hobby) by commit `e02258d` on 2026-05-22. Read the rules excerpt below and confirm Hobby is the actual settlement station. Hobby and IAH within ~0°F for highs/lows on average but with high day-to-day variance from sea-breeze regime.

| Field | Value |
|---|---|
| settles_at | `KHOU` (Houston William P. Hobby Airport) |
| nws_wfo | `HGX` |
| cli_product | `CLIHOU` |
| cli_location_name | `Houston-Hobby, TX` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Houston for May 22, 2026, is between 87-88° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIHOU can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=hgx, navigating to the "Observed Weather" tab, and choosing the location "Houston-Hobby, TX" with Daily Climate Report selected.

**Notes (from audit):** MISMATCH: Fallback coords (29.9844,-95.3414) are IAH (Houston Intercontinental), but Kalshi resolves at Hobby (HOU, approx 29.6454,-95.2789). IAH and HOU are ~24 miles apart.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTHOU --via KXHIGHTHOU-26MAY22-B87.5
```

---

### 6. KXLOWTHOU

> **⚠ NEEDS CAREFUL REVIEW — recently corrected**
> Previously mapped to `KIAH` (~24 mi off). Corrected to `KHOU` (Hobby) by commit `e02258d` on 2026-05-22. Read the rules excerpt below and confirm Hobby is the actual settlement station. Hobby and IAH within ~0°F for highs/lows on average but with high day-to-day variance from sea-breeze regime.

| Field | Value |
|---|---|
| settles_at | `KHOU` (Houston William P. Hobby Airport) |
| nws_wfo | `HGX` |
| cli_product | `CLIHOU` |
| cli_location_name | `Houston-Hobby, TX` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Houston for May 22, 2026, is between 72-73° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIHOU can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=hgx, navigating to the "Observed Weather" tab, and choosing the location "Houston-Hobby, TX" with Daily Climate Report selected.

**Notes (from audit):** MISMATCH: Fallback coords (29.9844,-95.3414) are IAH (Houston Intercontinental), but Kalshi resolves at Hobby (HOU, approx 29.6454,-95.2789). IAH and HOU are ~24 miles apart.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTHOU --via KXLOWTHOU-26MAY22-B72.5
```

---

### 7. KXTEMPNYCH

> **⚠ DISABLED — AccuWeather settlement, no feed**
> KXTEMPNYCH resolves on AccuWeather, not NWS CLI. We have no AccuWeather data feed, so this series is permanently disabled (`disabled: true` in YAML, also in `_DISABLED_SERIES_PREFIXES` in the strategy). The `verify_weather_series.py` helper refuses to mark disabled series. **No verification action needed — review for completeness only.**

| Field | Value |
|---|---|
| settles_at | `null` |
| nws_wfo | *(none — AccuWeather source)* |
| cli_product | *(none — AccuWeather source)* |
| cli_location_name | *(none — AccuWeather source)* |
| settles_what | `hourly_temp_at_hour` |
| source | `accuweather` |

**Rules primary (verbatim from Kalshi):**

> If the temperature recorded at Central Park, New York City for May 22, 2026 11 AM EDT as reported by Accuweather (for coordinates 40.7812,-73.9665), is above 70.99°, then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> The official, final value for this market is the temperature reported by the AccuWeather, not any other weather service. NWS Climatological Reports, Google Weather, etc. may be useful references, but are not authoritative for resolution. Preliminary AccuWeather data may be subject to rounding and conversion differences from the final reported value. Use caution when interpreting preliminary AccuWeather readings.

**Notes (from audit):** UNIQUE: Only Kalshi series using AccuWeather instead of NWS CLI. Hourly markets, not daily. No markets found for 26MAY22 via event_ticker query — event tickers include hour suffix (e.g., KXTEMPNYCH-26MAY2211). Coordinates match _CITY_COORDS_FALLBACK['NYC_CENTRAL'] exactly. No 26MAY21/26MAY20/26MAY23 event-level markets found via series_ticker; sample from 26MAY2211.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXTEMPNYCH --via KXTEMPNYCH-26MAY2211-T70.99
```

---

## Section B — Unaudited (32 entries)

Remaining 32 series, alphabetically by series prefix.

---

### 8. KXHIGHAUS

| Field | Value |
|---|---|
| settles_at | `KAUS` (Austin-Bergstrom International Airport) |
| nws_wfo | `EWX` |
| cli_product | `CLIAUS` |
| cli_location_name | `Austin Bergstrom` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the highest temperature recorded in Austin Bergstrom for May 22, 2026 as reported by the National Weather Service's Climatological Report (Daily), is between 89-90°, then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Not all weather data is the same. While checking a source like AccuWeather or Google Weather may help guide your decision, the official and final value used to determine this market is the highest temperature as reported by the corresponding NWS Climatological Report (Daily) linked in the rules above.

**Notes (from audit):** Older-format market, no CLI code. KXLOWTAUS (newer) cites CLIAUS at Austin Bergstrom (WFO ewx). Station is ABIA/KAUS. Fallback coords (30.1975,-97.6664) match ABIA — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHAUS --via KXHIGHAUS-26MAY22-B89.5
```

---

### 9. KXHIGHDEN

| Field | Value |
|---|---|
| settles_at | `KDEN` (Denver International Airport) |
| nws_wfo | `BOU` |
| cli_product | `CLIDEN` |
| cli_location_name | `Denver, CO` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the highest temperature recorded in Denver, CO for May 22, 2026 as reported by the National Weather Service's Climatological Report (Daily), is between 60-61°, then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Not all weather data is the same. While checking a source like AccuWeather or Google Weather may help guide your decision, the official and final value used to determine this market is the highest temperature as reported by the corresponding NWS Climatological Report (Daily) linked in the rules above.

**Notes (from audit):** Older-format market, no CLI code. KXLOWTDEN (newer) cites CLIDEN at Denver, CO (WFO bou). Station is KDEN. Fallback coords (39.8561,-104.6737) match DEN airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHDEN --via KXHIGHDEN-26MAY22-B60.5
```

---

### 10. KXHIGHLAX

| Field | Value |
|---|---|
| settles_at | `KLAX` (Los Angeles International Airport) |
| nws_wfo | `LOX` |
| cli_product | `CLILAX` |
| cli_location_name | `Los Angeles Airport, CA` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the highest temperature recorded in Los Angeles Airport, CA for May 22, 2026 as reported by the National Weather Service's Climatological Report (Daily), is less than 64°, then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Not all weather data is the same. While checking a source like AccuWeather or Google Weather may help guide your decision, the official and final value used to determine this market is the highest temperature as reported by the corresponding NWS Climatological Report (Daily) linked in the rules above.

**Notes (from audit):** Older-format market: no CLI product code (e.g. CLILAX) in rules_secondary. KXLOWTLAX (newer) cites CLILAX at Los Angeles Airport, CA (WFO lox). Consistent with KLAX station. Fallback coords (33.9416,-118.4085) match LAX.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHLAX --via KXHIGHLAX-26MAY22-T64
```

---

### 11. KXHIGHMIA

| Field | Value |
|---|---|
| settles_at | `KMIA` (Miami International Airport) |
| nws_wfo | `MFL` |
| cli_product | `CLIMIA` |
| cli_location_name | `Miami, FL` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the highest temperature recorded at Miami International Airport for May 22, 2026 as reported by the National Weather Service's Climatological Report (Daily), is less than 85°, then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Not all weather data is the same. While checking a source like AccuWeather or Google Weather may help guide your decision, the official and final value used to determine this market is the highest temperature as reported by the corresponding NWS Climatological Report (Daily) linked in the rules above.

**Notes (from audit):** Older-format market, no CLI code. KXLOWTMIA (newer) cites CLIMIA at Miami, FL (WFO mfl). Station is KMIA. Fallback coords (25.7959,-80.2870) match MIA airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHMIA --via KXHIGHMIA-26MAY22-T85
```

---

### 12. KXHIGHPHIL

| Field | Value |
|---|---|
| settles_at | `KPHL` (Philadelphia International Airport) |
| nws_wfo | `PHI` |
| cli_product | `CLIPHL` |
| cli_location_name | `Philadelphia, PA` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the highest temperature recorded at Philadelphia International Airport for May 22, 2026 as reported by the National Weather Service's Climatological Report (Daily), is greater than 69°, then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Not all weather data is the same. While checking a source like AccuWeather or Google Weather may help guide your decision, the official and final value used to determine this market is the highest temperature as reported by the corresponding NWS Climatological Report (Daily) linked in the rules above.

**Notes (from audit):** Older-format market, no CLI code. KXLOWTPHIL (newer) cites CLIPHL at Philadelphia, PA (WFO phi). Station is KPHL. Fallback coords (39.8729,-75.2437) match PHL — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHPHIL --via KXHIGHPHIL-26MAY22-T69
```

---

### 13. KXHIGHTATL

| Field | Value |
|---|---|
| settles_at | `KATL` (Hartsfield-Jackson Atlanta International Airport) |
| nws_wfo | `FFC` |
| cli_product | `CLIATL` |
| cli_location_name | `Atlanta, GA` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Atlanta for May 22, 2026, is less than 81° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIATL can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=ffc, navigating to the "Observed Weather" tab, and choosing the location "Atlanta, GA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (33.6407,-84.4277) match ATL — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTATL --via KXHIGHTATL-26MAY22-T81
```

---

### 14. KXHIGHTBOS

| Field | Value |
|---|---|
| settles_at | `KBOS` (Boston Logan International Airport) |
| nws_wfo | `BOX` |
| cli_product | `CLIBOS` |
| cli_location_name | `Boston (Logan Airport), MA` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Boston for May 22, 2026, is between 68-69° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIBOS can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=box, navigating to the "Observed Weather" tab, and choosing the location "Boston (Logan Airport), MA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (42.3656,-71.0096) match BOS — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTBOS --via KXHIGHTBOS-26MAY22-B68.5
```

---

### 15. KXHIGHTDAL

| Field | Value |
|---|---|
| settles_at | `KDFW` (Dallas/Fort Worth International Airport) |
| nws_wfo | `FWD` |
| cli_product | `CLIDFW` |
| cli_location_name | `Dallas/Fort Worth, TX` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Dallas for May 22, 2026, is between 88-89° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIDFW can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=fwd, navigating to the "Observed Weather" tab, and choosing the location "Dallas/Fort Worth, TX" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (32.8998,-97.0403) match DFW — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTDAL --via KXHIGHTDAL-26MAY22-B88.5
```

---

### 16. KXHIGHTDC

| Field | Value |
|---|---|
| settles_at | `KDCA` (Ronald Reagan Washington National Airport) |
| nws_wfo | `LWX` |
| cli_product | `CLIDCA` |
| cli_location_name | `Washington-National` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Washington DC for May 22, 2026, is between 64-65° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIDCA can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=lwx, navigating to the "Observed Weather" tab, and choosing the location "Washington-National" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (38.8512,-77.0402) match DCA — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTDC --via KXHIGHTDC-26MAY22-B64.5
```

---

### 17. KXHIGHTMIN

| Field | Value |
|---|---|
| settles_at | `KMSP` (Minneapolis-Saint Paul International Airport) |
| nws_wfo | `MPX` |
| cli_product | `CLIMSP` |
| cli_location_name | `Minneapolis/St Paul, MN` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Minneapolis for May 22, 2026, is between 62-63° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIMSP can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=mpx, navigating to the "Observed Weather" tab, and choosing the location "Minneapolis/St Paul, MN" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (44.8848,-93.2223) match MSP — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTMIN --via KXHIGHTMIN-26MAY22-B62.5
```

---

### 18. KXHIGHTNOLA

| Field | Value |
|---|---|
| settles_at | `KMSY` (Louis Armstrong New Orleans International Airport) |
| nws_wfo | `LIX` |
| cli_product | `CLIMSY` |
| cli_location_name | `New Orleans, LA` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at New Orleans for May 22, 2026, is less than 83° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIMSY can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=lix, navigating to the "Observed Weather" tab, and choosing the location "New Orleans, LA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (29.9934,-90.2580) match MSY airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTNOLA --via KXHIGHTNOLA-26MAY22-T83
```

---

### 19. KXHIGHTOKC

| Field | Value |
|---|---|
| settles_at | `KOKC` (Will Rogers World Airport) |
| nws_wfo | `OUN` |
| cli_product | `CLIOKC` |
| cli_location_name | `Oklahoma City Will Rogers Airport` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Oklahoma City for May 22, 2026, is between 79-80° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIOKC can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=oun, navigating to the "Observed Weather" tab, and choosing the location "Oklahoma City Will Rogers Airport" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (35.3931,-97.6007) match OKC airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTOKC --via KXHIGHTOKC-26MAY22-B79.5
```

---

### 20. KXHIGHTPHX

| Field | Value |
|---|---|
| settles_at | `KPHX` (Phoenix Sky Harbor International Airport) |
| nws_wfo | `PSR` |
| cli_product | `CLIPHX` |
| cli_location_name | `Phoenix, AZ` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Phoenix for May 22, 2026, is between 100-101° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIPHX can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=psr, navigating to the "Observed Weather" tab, and choosing the location "Phoenix, AZ" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (33.4373,-112.0078) match PHX airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTPHX --via KXHIGHTPHX-26MAY22-B100.5
```

---

### 21. KXHIGHTSATX

| Field | Value |
|---|---|
| settles_at | `KSAT` (San Antonio International Airport) |
| nws_wfo | `EWX` |
| cli_product | `CLISAT` |
| cli_location_name | `San Antonio` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at San Antonio for May 22, 2026, is between 85-86° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLISAT can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=ewx, navigating to the "Observed Weather" tab, and choosing the location "San Antonio" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (29.5337,-98.4698) match SAT airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTSATX --via KXHIGHTSATX-26MAY22-B85.5
```

---

### 22. KXHIGHTSEA

| Field | Value |
|---|---|
| settles_at | `KSEA` (Seattle-Tacoma International Airport) |
| nws_wfo | `SEW` |
| cli_product | `CLISEA` |
| cli_location_name | `Seattle-Tacoma, WA` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at Seattle for May 22, 2026, is less than 72° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLISEA can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=sew, navigating to the "Observed Weather" tab, and choosing the location "Seattle-Tacoma, WA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (47.4502,-122.3088) match SEA airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTSEA --via KXHIGHTSEA-26MAY22-T72
```

---

### 23. KXHIGHTSFO

| Field | Value |
|---|---|
| settles_at | `KSFO` (San Francisco International Airport) |
| nws_wfo | `MTR` |
| cli_product | `CLISFO` |
| cli_location_name | `San Francisco Airport` |
| settles_what | `daily_max_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the maximum temperature recorded at San Francisco for May 22, 2026, is between 72-73° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLISFO can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=mtr, navigating to the "Observed Weather" tab, and choosing the location "San Francisco Airport" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (37.6213,-122.3790) match SFO — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXHIGHTSFO --via KXHIGHTSFO-26MAY22-B72.5
```

---

### 24. KXLOWTATL

| Field | Value |
|---|---|
| settles_at | `KATL` (Hartsfield-Jackson Atlanta International Airport) |
| nws_wfo | `FFC` |
| cli_product | `CLIATL` |
| cli_location_name | `Atlanta, GA` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Atlanta for May 22, 2026, is greater than 69° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIATL can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=ffc, navigating to the "Observed Weather" tab, and choosing the location "Atlanta, GA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (33.6407,-84.4277) match ATL — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTATL --via KXLOWTATL-26MAY22-T69
```

---

### 25. KXLOWTAUS

| Field | Value |
|---|---|
| settles_at | `KAUS` (Austin-Bergstrom International Airport) |
| nws_wfo | `EWX` |
| cli_product | `CLIAUS` |
| cli_location_name | `Austin Bergstrom` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Austin for May 22, 2026, is greater than 71° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIAUS can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=ewx, navigating to the "Observed Weather" tab, and choosing the location "Austin Bergstrom" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (30.1975,-97.6664) match ABIA — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTAUS --via KXLOWTAUS-26MAY22-T71
```

---

### 26. KXLOWTBOS

| Field | Value |
|---|---|
| settles_at | `KBOS` (Boston Logan International Airport) |
| nws_wfo | `BOX` |
| cli_product | `CLIBOS` |
| cli_location_name | `Boston (Logan Airport), MA` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Boston for May 22, 2026, is greater than 45° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIBOS can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=box, navigating to the "Observed Weather" tab, and choosing the location "Boston (Logan Airport), MA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (42.3656,-71.0096) match BOS — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTBOS --via KXLOWTBOS-26MAY22-T45
```

---

### 27. KXLOWTDAL

| Field | Value |
|---|---|
| settles_at | `KDFW` (Dallas/Fort Worth International Airport) |
| nws_wfo | `FWD` |
| cli_product | `CLIDFW` |
| cli_location_name | `Dallas/Fort Worth, TX` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Dallas for May 22, 2026, is between 64-65° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIDFW can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=fwd, navigating to the "Observed Weather" tab, and choosing the location "Dallas/Fort Worth, TX" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (32.8998,-97.0403) match DFW — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTDAL --via KXLOWTDAL-26MAY22-B64.5
```

---

### 28. KXLOWTDC

| Field | Value |
|---|---|
| settles_at | `KDCA` (Ronald Reagan Washington National Airport) |
| nws_wfo | `LWX` |
| cli_product | `CLIDCA` |
| cli_location_name | `Washington-National` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Washington DC for May 22, 2026, is between 49-50° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIDCA can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=lwx, navigating to the "Observed Weather" tab, and choosing the location "Washington-National" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (38.8512,-77.0402) match DCA — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTDC --via KXLOWTDC-26MAY22-B49.5
```

---

### 29. KXLOWTDEN

| Field | Value |
|---|---|
| settles_at | `KDEN` (Denver International Airport) |
| nws_wfo | `BOU` |
| cli_product | `CLIDEN` |
| cli_location_name | `Denver, CO` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Denver for May 22, 2026, is greater than 46° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIDEN can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=bou, navigating to the "Observed Weather" tab, and choosing the location "Denver, CO" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (39.8561,-104.6737) match DEN — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTDEN --via KXLOWTDEN-26MAY22-T46
```

---

### 30. KXLOWTLAX

| Field | Value |
|---|---|
| settles_at | `KLAX` (Los Angeles International Airport) |
| nws_wfo | `LOX` |
| cli_product | `CLILAX` |
| cli_location_name | `Los Angeles Airport, CA` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Los Angeles for May 22, 2026, is less than 55° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLILAX can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=lox, navigating to the "Observed Weather" tab, and choosing the location "Los Angeles Airport, CA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (33.9416,-118.4085) match LAX airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTLAX --via KXLOWTLAX-26MAY22-T55
```

---

### 31. KXLOWTMIA

| Field | Value |
|---|---|
| settles_at | `KMIA` (Miami International Airport) |
| nws_wfo | `MFL` |
| cli_product | `CLIMIA` |
| cli_location_name | `Miami, FL` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Miami for May 22, 2026, is greater than 81° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIMIA can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=mfl, navigating to the "Observed Weather" tab, and choosing the location "Miami, FL" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (25.7959,-80.2870) match MIA airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTMIA --via KXLOWTMIA-26MAY22-T81
```

---

### 32. KXLOWTMIN

| Field | Value |
|---|---|
| settles_at | `KMSP` (Minneapolis-Saint Paul International Airport) |
| nws_wfo | `MPX` |
| cli_product | `CLIMSP` |
| cli_location_name | `Minneapolis/St Paul, MN` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Minneapolis for May 22, 2026, is between 45-46° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIMSP can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=mpx, navigating to the "Observed Weather" tab, and choosing the location "Minneapolis/St Paul, MN" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (44.8848,-93.2223) match MSP — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTMIN --via KXLOWTMIN-26MAY22-B45.5
```

---

### 33. KXLOWTNOLA

| Field | Value |
|---|---|
| settles_at | `KMSY` (Louis Armstrong New Orleans International Airport) |
| nws_wfo | `LIX` |
| cli_product | `CLIMSY` |
| cli_location_name | `New Orleans, LA` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at New Orleans for May 22, 2026, is greater than 76° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIMSY can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=lix, navigating to the "Observed Weather" tab, and choosing the location "New Orleans, LA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (29.9934,-90.2580) match MSY airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTNOLA --via KXLOWTNOLA-26MAY22-T76
```

---

### 34. KXLOWTOKC

| Field | Value |
|---|---|
| settles_at | `KOKC` (Will Rogers World Airport) |
| nws_wfo | `OUN` |
| cli_product | `CLIOKC` |
| cli_location_name | `Oklahoma City Will Rogers Airport` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Oklahoma City for May 22, 2026, is between 53-54° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIOKC can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=oun, navigating to the "Observed Weather" tab, and choosing the location "Oklahoma City Will Rogers Airport" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (35.3931,-97.6007) match OKC airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTOKC --via KXLOWTOKC-26MAY22-B53.5
```

---

### 35. KXLOWTPHIL

| Field | Value |
|---|---|
| settles_at | `KPHL` (Philadelphia International Airport) |
| nws_wfo | `PHI` |
| cli_product | `CLIPHL` |
| cli_location_name | `Philadelphia, PA` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Philadelphia for May 22, 2026, is between 47-48° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIPHL can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=phi, navigating to the "Observed Weather" tab, and choosing the location "Philadelphia, PA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (39.8729,-75.2437) match PHL — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTPHIL --via KXLOWTPHIL-26MAY22-B47.5
```

---

### 36. KXLOWTPHX

| Field | Value |
|---|---|
| settles_at | `KPHX` (Phoenix Sky Harbor International Airport) |
| nws_wfo | `PSR` |
| cli_product | `CLIPHX` |
| cli_location_name | `Phoenix, AZ` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Phoenix for May 22, 2026, is between 65-66° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLIPHX can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=psr, navigating to the "Observed Weather" tab, and choosing the location "Phoenix, AZ" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (33.4373,-112.0078) match PHX airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTPHX --via KXLOWTPHX-26MAY22-B65.5
```

---

### 37. KXLOWTSATX

| Field | Value |
|---|---|
| settles_at | `KSAT` (San Antonio International Airport) |
| nws_wfo | `EWX` |
| cli_product | `CLISAT` |
| cli_location_name | `San Antonio` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at San Antonio for May 22, 2026, is between 64-65° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLISAT can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=ewx, navigating to the "Observed Weather" tab, and choosing the location "San Antonio" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (29.5337,-98.4698) match SAT airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTSATX --via KXLOWTSATX-26MAY22-B64.5
```

---

### 38. KXLOWTSEA

| Field | Value |
|---|---|
| settles_at | `KSEA` (Seattle-Tacoma International Airport) |
| nws_wfo | `SEW` |
| cli_product | `CLISEA` |
| cli_location_name | `Seattle-Tacoma, WA` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at Seattle for May 22, 2026, is between 48-49° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLISEA can be found by clicking the following URL: https://www.weather.gov/wrh/climate?wfo=sew, navigating to the "Observed Weather" tab, and choosing the location "Seattle-Tacoma, WA" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (47.4502,-122.3088) match SEA airport — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTSEA --via KXLOWTSEA-26MAY22-B48.5
```

---

### 39. KXLOWTSFO

| Field | Value |
|---|---|
| settles_at | `KSFO` (San Francisco International Airport) |
| nws_wfo | `MTR` |
| cli_product | `CLISFO` |
| cli_location_name | `San Francisco Airport` |
| settles_what | `daily_min_temp` |
| source | `nws_cli` |

**Rules primary (verbatim from Kalshi):**

> If the minimum temperature recorded at San Francisco for May 22, 2026, is less than 47° fahrenheit according to the National Weather Service's Climatological Report (Daily), then the market resolves to Yes.

**Rules secondary (verbatim from Kalshi):**

> Data for CLISFO can be found by clicking the following URL: https://www.weather.gov/wrh/Climate?wfo=mtr, navigating to the "Observed Weather" tab, and choosing the location "San Francisco Airport" with Daily Climate Report selected.

**Notes (from audit):** Fallback coords (37.6213,-122.3790) match SFO — consistent.

**Verify command (after reading the excerpts above):**

```
python scripts/verify_weather_series.py KXLOWTSFO --via KXLOWTSFO-26MAY22-T47
```

---

## Tracking

Update the header "Verified so far" count as you progress. When all 39
are verified (excluding KXTEMPNYCH which is permanently disabled), the
P3 wiring work is unblocked.

| Status | Count |
|---|--:|
| Verified | 0 of 38 |
| Disabled (KXTEMPNYCH) | 1 |
| Pending | 38 |
