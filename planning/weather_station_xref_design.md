# Weather Station Cross-Reference — Design Document

**Status:** DRAFT — pending Board review, 2026-05-22.
**Author:** Claude (Opus 4.7) with operator (jack).
**Scope:** kalshi_weather_arb strategy only. Read-only investigation
+ written design — no code changes proposed in this doc.

## 0. TL;DR

Today the strategy maps Kalshi weather tickers → lat/lon via a 28-entry
hardcoded dict (`_CITY_COORDS_FALLBACK`) and a regex parse of
`market.rules_primary` that succeeds for only 1 of 39 series in
practice. Of the 28 fallback entries, **6 (21%) point at the wrong
settlement station**, affecting **~18% of all weather trades** placed
in the last 14 days. We propose:

1. A **versioned, validated YAML cross-reference** at
   `config/weather_stations.yaml` for verified ticker-series →
   settlement-station mappings.
2. A **researched-but-unverified staging area** in the existing
   `agent_state` table for series the strategy discovers on the fly.
3. A **confidence gate** that lets researched entries drive paper
   trades immediately but **blocks live trades on unverified mappings**.
4. A **human verification flow** to promote staged entries into the
   committed YAML.

This document describes structure and behavior only. No code is
modified.

---

## 1. Current-state audit

### 1.1 Code: how ticker → station works today

Code paths (all in
[`trading_corp/agents/strategies/kalshi_weather_arb.py`](../trading_corp/agents/strategies/kalshi_weather_arb.py)):

| Step | Line | What it does |
|---|---|---|
| Parse city from ticker | 138, 269–284 | Regex `^KX(HIGH\|LOW\|TEMP)([A-Z]+?)-` extracts `city_code` (non-greedy match up to first dash). |
| Coords: primary path | 412, 923–933 | `_parse_coords(rules_primary)` — regex `coordinates LAT,LON` on the market's `rules_primary` text. |
| Coords: fallback | 414, 61–97 | `_CITY_COORDS_FALLBACK[city_code.upper()]` — 28-entry hardcoded dict. |
| METAR station | 514, 104–134 | `_CITY_TO_METAR_STATION[city_code.upper()]` — parallel 28-entry dict for nowcast blend. |
| Non-US skip | 139, 274 | `_NON_US_CITIES = {"TLV"}` and a `.endswith("TLV")` heuristic. |

### 1.2 Where the "primary" path fires in practice

The primary path (coords from `rules_primary`) succeeds **only for
`KXTEMPNYCH`**, which is the one series whose Kalshi rules embed
explicit `coordinates 40.7812,-73.9665` (AccuWeather hourly). The
other 38 daily NWS-CLI series have rules text that names a city in
prose (e.g. *"the maximum temperature recorded at Seattle"*) without
embedded coordinates, so the regex always misses and the fallback
dict is the de facto primary source for **38 of 39 series**.

### 1.3 Audit data

The full per-series audit is at
[`weather_station_xref_audit.json`](./weather_station_xref_audit.json)
— one entry per series with the verbatim rules_primary excerpt, the
cited NWS CLI product code, the WFO office, and a verdict on whether
our fallback coords match.

### 1.4 Universe of series (last 14 days, 700+ trades)

39 distinct series; the top 10 by trade count:

| Rank | Series | Trades | City code parsed | Resolves at | Current fallback | Match? |
|--:|---|--:|---|---|---|:--:|
| 1 | KXLOWTNYC | 30 | TNYC | **Central Park (KNYC)** | KJFK (40.6413, -73.7781) | ❌ |
| 2 | KXHIGHTSEA | 27 | TSEA | KSEA | KSEA (47.4502, -122.3088) | ✅ |
| 3 | KXHIGHTBOS | 27 | TBOS | KBOS | KBOS | ✅ |
| 4 | KXLOWTAUS | 26 | TAUS | KAUS | KAUS | ✅ |
| 5 | KXHIGHTDC | 25 | TDC | KDCA | KDCA | ✅ |
| 6 | KXHIGHLAX | 25 | LAX | KLAX | KLAX | ✅ |
| 7 | KXLOWTSATX | 24 | TSATX | KSAT | KSAT | ✅ |
| 8 | KXHIGHTMIN | 23 | TMIN | KMSP | KMSP | ✅ |
| 9 | KXHIGHNY | 22 | NY | **Central Park (KNYC)** | KJFK | ❌ |
| 10 | KXHIGHCHI | 22 | CHI | **Chicago Midway (KMDW)** | KORD | ❌ |

### 1.5 The 6 misaligned series

| Series | Trades | Should resolve at | Currently points at | Error magnitude |
|---|--:|---|---|---|
| **KXLOWTNYC** | 30 | KNYC (Central Park) | KJFK | ~12 mi; min-temp diverges 2–5°F (Central Park is urban heat island; JFK is coastal) |
| **KXHIGHNY** | 22 | KNYC (Central Park) | KJFK | same as above, summer high diverges 3–5°F |
| **KXHIGHCHI** | 22 | KMDW (Midway) | KORD | ~17 mi; ORD is exposed prairie, MDW is south-side urban; high diverges 2–4°F |
| **KXLOWTCHI** | 18 | KMDW (Midway) | KORD | same; low diverges 2–4°F |
| **KXLOWTHOU** | 17 | KHOU (Hobby) | KIAH | ~24 mi; IAH is far north, HOU is south-side coastal — sea-breeze regime differs sharply |
| **KXHIGHTHOU** | 16 | KHOU (Hobby) | KIAH | same |

**Total mis-mapped trades: 125 of ~700 (≈18%).** None of these
mismatches would be detected by any test we currently run; the
strategy returns a valid coord, the forecast call succeeds, and the
trade fires on a station-misaligned forecast.

### 1.6 Other anomalies worth noting

- **`KXTEMPNYCH`** is the only series resolving on **AccuWeather**,
  not NWS CLI. Our pipeline is NWS-primary + Open-Meteo-σ. We have
  no AccuWeather feed today; the forecast we use is structurally
  wrong-source. Volume is low (1 trade in 14 days) so impact is
  minor, but the design must flag this as a separate-source case.
- **8 "older-format" series** lack a CLI product code in
  `rules_secondary` (just cite "NWS Climatological Report (Daily)").
  The station is unambiguous via sister series (e.g. `KXHIGHLAX`'s
  newer counterpart `KXLOWTLAX` cites `CLILAX`), but a naive parser
  would mark these as low-confidence. The design must handle this.
- **`_HANDLED_PREFIX_RE` is greedy enough to misclassify**:
  `KXHIGHTSEA` → `city_code = "TSEA"`, but a hypothetical new series
  like `KXHIGHT-newcity-...` could be mis-parsed silently. Not
  blocking today (no such ticker exists), but worth a sentinel.

---

## 2. Goals + non-goals

**Goals:**
- One source of truth for ticker series → settlement station.
- Make station mismatches **visible and reviewable** before they
  drive trades.
- Handle new ticker series gracefully — research them, stage them,
  but don't trade live on a guess.
- Backwards-compatible: existing 39 series ship with verified entries
  pre-populated from the audit JSON; no behavior change on day-one
  deploy for series we already had right.

**Non-goals:**
- Not solving forecast bias (separate work — NBM bulletins, MOS,
  bias-residual tracking). The cross-ref is a prerequisite for that
  work but doesn't try to do it.
- Not generalizing to non-Kalshi prediction markets.
- Not adding live AccuWeather feed for `KXTEMPNYCH`. We tag the
  series as wrong-source and skip in live mode until a feed exists.

---

## 3. Proposed schema

### 3.1 Two-tier storage

| Tier | Where | Mutability | Drives live trades? |
|---|---|---|:--:|
| **Verified** | `config/weather_stations.yaml` (in repo, code-reviewed) | Human-only, via PR | ✅ |
| **Researched** | `agent_state` table, `(agent='kalshi_weather_arb', key='station_research:<SERIES>')` | Strategy writes on first sight | Paper ✅ · Live ❌ |

Two tiers, not one, because the design needs both **stability under
review** (the verified set is what risk gates trust) and **resilience
to discovery** (a new series shouldn't crash a scan cycle or trigger
a maintainer page). The `agent_state` table is already the canonical
place for "state that affects future trade decisions" per
[CLAUDE.md § 1 / State + audit](../CLAUDE.md); no schema change
needed.

### 3.2 YAML schema (verified set)

```yaml
# config/weather_stations.yaml
# Verified Kalshi-weather ticker-series → settlement-station mappings.
# Each entry requires human review of the verbatim rules_primary
# excerpt before being added here.
schema_version: 1

stations:
  KSEA:
    icao: KSEA
    name: "Seattle-Tacoma International Airport"
    nws_wfo: SEW
    cli_product: CLISEA
    cli_location_name: "Seattle-Tacoma, WA"     # exact string on weather.gov
    coords: { lat: 47.4502, lon: -122.3088 }
    feeds:
      nws_points:           "https://api.weather.gov/points/47.4502,-122.3088"
      nbm_bulletin:         "https://nbm.weather.gov/bulletin/?sta=KSEA"
      mos_mav:              "https://www.weather.gov/mdl/avnmav.txt?sta=KSEA"   # GFS MOS
      mos_mex:              "https://www.weather.gov/mdl/avnmex.txt?sta=KSEA"   # ECMWF MOS
      metar_obs:            "https://aviationweather.gov/api/data/metar?ids=KSEA&format=json&hours=24"
      asos_history:         "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=SEA&data=tmpf"
      cli_observed_html:    "https://www.weather.gov/wrh/Climate?wfo=sew"       # tab: Observed Weather, location: Seattle-Tacoma, WA

  KNYC:
    icao: KNYC
    name: "Central Park, New York"
    nws_wfo: OKX
    cli_product: CLINYC
    cli_location_name: "Central Park NY"
    coords: { lat: 40.7794, lon: -73.9692 }
    feeds: { ... }

  KMDW:
    icao: KMDW
    name: "Chicago Midway International Airport"
    nws_wfo: LOT
    cli_product: CLIMDW
    cli_location_name: "Chicago - Midway, IL"
    coords: { lat: 41.7868, lon: -87.7522 }
    feeds: { ... }

  # ... one block per distinct settlement station

series:
  KXHIGHTSEA:
    settles_at: KSEA                      # reference into stations.*
    settles_what: daily_max_temp          # daily_max_temp | daily_min_temp | hourly_temp_at_hour
    source: nws_cli                       # nws_cli | accuweather | other
    rules_excerpt: |
      If the maximum temperature recorded at Seattle for {date},
      is {op} {temp}° fahrenheit according to the National Weather
      Service's Climatological Report (Daily), then the market
      resolves to Yes.
    verified: true
    verified_by: jack
    verified_at: "2026-05-22"
    verified_via_market: KXHIGHTSEA-26MAY22-T72  # the specific ticker whose rules were read

  KXLOWTNYC:
    settles_at: KNYC                      # NOT KJFK
    settles_what: daily_min_temp
    source: nws_cli
    rules_excerpt: |
      ... at New York City ... CLINYC ... Central Park NY ...
    verified: true
    verified_by: jack
    verified_at: "2026-05-22"
    verified_via_market: KXLOWTNYC-26MAY22-T56
    correction_note: |
      Migrated from legacy _CITY_COORDS_FALLBACK['TNYC']=KJFK on
      2026-05-22. KJFK→KNYC station change shifts forecast point
      ~12 mi; expected min-temp delta 2–5°F.

  KXTEMPNYCH:
    settles_at: ACCU_CENTRAL_PARK         # not an ICAO — see stations.* block
    settles_what: hourly_temp_at_hour
    source: accuweather                   # downstream pipeline must skip in live mode
    rules_excerpt: |
      ... reported by Accuweather (for coordinates 40.7812,-73.9665) ...
    verified: true
    verified_by: jack
    verified_at: "2026-05-22"
    verified_via_market: KXTEMPNYCH-26MAY2211-T70.99
    live_trading_blocked: true            # explicit — no AccuWeather feed today

city_code_aliases:                        # legacy compatibility, optional
  # Maps the ticker's parsed city_code → series prefix for fast lookup
  # during the cache-miss research path. Used only as a hint; the
  # authoritative mapping is series.* above.
  TSEA: KXHIGHTSEA
  TNYC: KXLOWTNYC
  # ...
```

### 3.3 `agent_state` shape for researched entries

```json
// row: agent='kalshi_weather_arb', key='station_research:KXHIGHNEWCITY', updated_ts=<>
{
  "series": "KXHIGHNEWCITY",
  "discovered_at": "2026-06-01T14:23:11Z",
  "rules_primary_excerpt": "If the maximum temperature recorded at Newcity ...",
  "rules_secondary_excerpt": "Data for CLINEW can be found at ...",
  "candidate_station": {
    "icao": "KNEW",
    "name": "Newcity Regional Airport",
    "nws_wfo": "XYZ",
    "cli_product": "CLINEW",
    "coords": { "lat": 12.34, "lon": -56.78 }
  },
  "settles_what": "daily_max_temp",
  "source": "nws_cli",
  "confidence": "high",                 // high | medium | low
  "confidence_reasons": [
    "rules_secondary cites CLINEW (matches NWS station registry)",
    "station_name 'Newcity Regional Airport' exact-matches NWS registry entry",
    "ICAO KNEW resolved via OurAirports.com"
  ],
  "verified": false,
  "blocks_live_trading": true
}
```

### 3.4 Why YAML for the verified tier, SQLite for the researched tier

| Property | YAML | SQLite |
|---|:--:|:--:|
| Code-reviewable diff | ✅ | ❌ |
| Mtime-cached hot reload | ✅ (existing pattern in `strategies.yaml`) | ✅ |
| Schema-validated load | ✅ (pydantic at load time — design closes the [sharp edge](../docs/sharp_edges.md#config-hot-reload-has-no-validation) for THIS file) | trivially |
| Frequent writes from strategy | ❌ | ✅ |
| Persists across deploys/repo cleanup | ✅ | ✅ (DB is persisted) |
| Matches existing convention | `risk.yaml`, `strategies.yaml`, `divisions.yaml` | `agent_state` per CLAUDE.md |

The verified set rarely changes (humans approve PRs). Researched
entries can be written multiple times per minute during a scan cycle.
Splitting them avoids `agent_state` becoming an unreviewable
production-state mix of authoritative + speculative data.

---

## 4. Cache-miss research workflow

### 4.1 Lookup path at evaluation time

```
For each candidate market in scan cycle:
  series := strip date/strike from market.ticker       # "KXHIGHCHI"

  entry := config/weather_stations.yaml::series[series]
  if entry: use it — done.

  entry := agent_state['station_research:'+series]
  if entry:
    if entry.confidence == 'high' and paper_mode:
      use it; tag order with `station_verified=false`
    else:
      skip market this cycle; emit `kalshi_weather_skipped_unverified_station`
      proceed to research refresh (4.2) if entry is older than refresh_ttl

  # No entry at all:
  emit `kalshi_weather_series_unknown` audit (with rules excerpt)
  skip market this cycle
  enqueue research task (4.2) — at most one per series per hour
```

### 4.2 Research task (background, idempotent)

Goal: take a series prefix + the Kalshi market's `rules_primary` +
`rules_secondary`, and return a `candidate_station` with a
confidence score.

```
Inputs: series, rules_primary, rules_secondary
Outputs: agent_state row with candidate_station + confidence

Steps:
1. CLI-product extraction:
   - regex `Data for (CLI[A-Z]{3,4})` in rules_secondary
   - if found, look up in NWS CLI product registry → station name + WFO
   - this is the highest-trust signal

2. WFO extraction:
   - regex `wfo=([a-z]{3})` in rules_secondary
   - cross-check vs CLI product registry

3. Station-name extraction:
   - regex `recorded at (.*?) for {date}` in rules_primary
   - regex `choosing the location "(.*?)"` in rules_secondary
   - normalize whitespace + casing; both should agree

4. ICAO resolution:
   - lookup station name in a packaged ICAO registry (OurAirports CSV,
     or NWS station list)
   - prefer ICAOs whose `municipality + name` substring-matches
   - if station name is "Central Park, New York" — special-case to KNYC

5. Coordinate lookup:
   - from ICAO registry, get authoritative lat/lon
   - DO NOT trust coordinates parsed from rules_primary alone (they
     may name a city center, not the airport)

6. Cross-validation:
   - WFO + CLI product + station name must all agree
   - if any disagree → confidence = medium or low

7. Score:
   HIGH    — CLI product + WFO + station name + ICAO all resolved
             and all cross-validate.
   MEDIUM  — CLI product missing OR station name fuzzy-match (Jaro
             distance ≥ 0.85 to a registry entry) — but other signals
             agree. The 8 "older-format" series live here on first
             discovery; promoted to HIGH after a human confirms via
             sister-series cross-check.
   LOW     — Multiple plausible candidates, OR non-NWS source
             (AccuWeather, private), OR no station-name regex match.

8. Persist to agent_state with `verified=false`,
   `blocks_live_trading = (confidence != high) or (source != nws_cli)`.
```

### 4.3 What "blocks live trading" means in practice

Risk-gate change (sketched, not implemented in this doc):

```yaml
# config/risk.yaml — new key
kalshi_weather:
  require_verified_station_for_live: true   # default true
```

```python
# trading_corp/agents/risk.py — sketch
if order.strategy == "kalshi_weather_arb":
    station_verified = order.extra.get("station_verified", False)
    if not station_verified and self._params["kalshi_weather"]["require_verified_station_for_live"]:
        if process_mode == "live":
            return Reject(reason="unverified_weather_station")
```

Paper-mode orders proceed regardless. The audit event will show
`station_verified=false` so the dashboard can highlight them for the
operator.

### 4.4 Refresh / re-research cadence

Researched entries are refreshed when:
- Series sees a market with a *materially different* `rules_primary`
  (≥5% character diff) — likely Kalshi rephrased; re-verify.
- Manual force-refresh from operator (dashboard button).
- TTL: 90 days, then re-run research and compare; alert if changed.

Verified YAML entries are *never* refreshed automatically — humans
own that file.

---

## 5. Human verification & promotion

### 5.1 Surface

Dashboard tile: **"Pending weather-station verifications"** (lives
under the kalshi_weather division activity rail). Lists every
`agent_state` row with `verified=false`, sorted by trade volume on
the series (so high-impact discoveries surface first).

Each row shows:
- Series prefix + observed trade count (last 14d, last 90d)
- Discovered station ICAO + name + coords
- Confidence + reasons
- Verbatim `rules_primary` and `rules_secondary` excerpts
- Two actions: **Verify** (promote) and **Reject** (mark wrong; the
  strategy will re-research and re-stage)

### 5.2 Promotion mechanics

`Verify` action does *not* directly mutate `config/weather_stations.yaml`
(which would bypass code review). Instead it:

1. Generates a PR-ready YAML diff for the new series block and (if
   the station is new) the new station block, including the
   `rules_excerpt`, `verified_by`, `verified_at`, and
   `verified_via_market` fields.
2. Opens a PR via `gh pr create` (or writes the diff to
   `tmp/pending_station_verifications/<series>.yaml.patch` for
   non-GitHub flows).
3. The PR description quotes the rules excerpt and the candidate's
   confidence reasons.
4. Reviewer merges → next strategy reload picks up the verified
   entry → the `agent_state` row is cleared on next strategy cycle
   (the strategy sees the YAML entry first and treats the
   `agent_state` row as superseded).

### 5.3 Reject mechanics

`Reject` writes `confidence=rejected` + `rejected_at` + `rejected_by`
to the `agent_state` row. The strategy stops treating it as a
candidate; the row stays for audit + to prevent immediate
re-research churn. A force-refresh from the operator can clear it.

### 5.4 Bootstrap of the 39 known series

Generate the first version of `config/weather_stations.yaml` from
the existing audit JSON (`tmp/weather_series_rules_audit.json`).
Pre-populate all 39 entries with the rules excerpts already
captured, mark all 39 `verified: false` initially, and require an
explicit human review pass before flipping each to `verified: true`.

The 6 known mismatches (KXLOWTNYC, KXHIGHNY, KXHIGHCHI, KXLOWTCHI,
KXHIGHTHOU, KXLOWTHOU) must be reviewed in that first pass — the
goal is to consciously approve KNYC/KMDW/KHOU and retire the wrong
ORD/IAH/JFK fallbacks.

The remaining 33 are mechanical confirmations but **still get a
human checkmark** — this is the moment to catch any rules-text
nuance I missed.

---

## 6. Validation & failure modes

### 6.1 YAML load-time validation (closes
[sharp-edge: "Config hot-reload has no validation"](../docs/sharp_edges.md#config-hot-reload-has-no-validation))

On every reload:
- Every `series.*.settles_at` must reference an existing
  `stations.*` key.
- `stations.*.coords.{lat,lon}` must be floats in valid ranges
  (lat ∈ [-90,90], lon ∈ [-180,180]).
- `stations.*.icao` matches `^[A-Z0-9]{4}$` or is one of the
  whitelisted synthetic IDs (e.g. `ACCU_CENTRAL_PARK`).
- `stations.*.cli_product` matches `^CLI[A-Z]{3,4}$` if `source=nws_cli`.
- `verified: true` requires `verified_by` + `verified_at` populated.

On failure: log a `weather_stations_yaml_invalid` audit event with
the first 3 error lines, **keep using the previous valid in-memory
copy**, and refuse to flip `verified=false → true` mid-flight. This
is the same fail-safe shape as `_restore_bias_state` in
[lord_otter.py](../trading_corp/agents/strategies/lord_otter.py).

### 6.2 Strategy-runtime failure modes

| Failure | Today's behavior | Proposed behavior |
|---|---|---|
| Series prefix never seen before | `_CITY_COORDS_FALLBACK.get()` returns None → `no_coords` skip | Research enqueued, market skipped this cycle with `kalshi_weather_series_unknown` audit (includes rules excerpt) |
| Series mapped to wrong station | Silent — forecast for wrong point, trade fires | Verified entry must be in YAML before live trade; mismatch visible in dashboard tile |
| Researched entry conflicts with later YAML add | YAML wins on next reload | YAML wins; agent_state row marked `superseded_by_verified` and ignored |
| AccuWeather source (KXTEMPNYCH) | Forecast pulled from NWS (wrong source!) | Series tagged `source: accuweather`; paper trades note source mismatch in audit; live trades blocked until AccuWeather feed exists |

### 6.3 What this design does **not** prevent

- A verified entry that's *itself* wrong (human error in PR review).
  Mitigation: PR template requires quoting the rules excerpt, and
  the `verified_via_market` field lets a reviewer cross-check the
  exact ticker.
- Kalshi changing the settlement station for an existing series.
  Mitigation: 90-day re-research TTL on researched entries will catch
  this for non-verified series; for verified series, the rules
  excerpt is preserved in YAML — a periodic background job can
  re-fetch a sample market's `rules_primary` and diff against the
  stored excerpt, alerting on drift.
- Coordinate drift (NWS gridpoint shifts when their grid is
  re-tiled). Mitigation: prefer `nws_points` URL (which re-resolves
  the gridpoint each time) over the raw lat/lon when possible.

---

## 7. Migration plan (when implementation is approved)

Phases sized so each is independently revertible.

| Phase | Work | Risk | Rollback |
|---|---|---|---|
| **P0** | Land this design doc + the audit JSON in repo. No runtime change. | None | git revert |
| **P1** | Add `config/weather_stations.yaml` with all 39 series pre-populated from the audit, all `verified: false`. Add a loader module that reads the YAML but is **not yet wired into** the strategy. Add YAML validation unit tests. | None — strategy still uses fallback dict. | Delete files. |
| **P2** | Human verification pass: a session where the operator (or Board) reviews and flips `verified: true` on each of the 39 entries, with the 6 mismatches explicitly approved/corrected. | None — still not wired into strategy. | None needed. |
| **P3** | Wire the loader into `kalshi_weather_arb.py`: lookup order = YAML verified → legacy `_CITY_COORDS_FALLBACK` (kept for one phase as a safety net). For each evaluated market, write an audit event comparing YAML-coord vs legacy-coord. | Low — fallback still active. | Revert P3 commit. |
| **P4** | Observe P3 audits for one week. Confirm every series resolves via YAML, no fallback hits. | Low. | Revert P3. |
| **P5** | Remove `_CITY_COORDS_FALLBACK` entirely. Add the cache-miss research path and the `agent_state` staging tier. Add the `require_verified_station_for_live` risk gate. | Medium — first time the strategy will skip unknown series rather than guess. | Revert; legacy dict restored from git history. |
| **P6** | Dashboard tile for pending verifications + `Verify`/`Reject` actions. | None — additive UI. | Revert. |

Phases P1–P4 ship strictly additive code; the strategy is unchanged
until P5. This deliberately matches CLAUDE.md § 1's pattern: "The
existing real-money pipelines must not be modified, refactored, or
'improved' without explicit, in-session human approval. New
functionality is added in parallel."

---

## 8. Open questions

1. **Is `agent_state` the right home for researched entries**, or
   should we add a dedicated `weather_station_research` table?
   Per CLAUDE.md, `agent_state` is the generic latch table — fine,
   but the row count could grow into the thousands over years. Lean
   toward keeping it in `agent_state` initially; revisit if row
   count exceeds 500.

2. **Synthetic IDs for non-ICAO stations**: `KNYC` is real (Central
   Park has an ICAO), so the only non-ICAO is AccuWeather. Is
   `ACCU_CENTRAL_PARK` the right shape, or should we drop the
   `accuweather` source entirely until a feed exists?

3. **Confidence-gate strictness for paper mode**: do we want the
   gate to also block paper trades on `confidence=low` entries, or
   only on `verified=false`? Leaning toward "paper unrestricted,
   live requires verified" — but the operator may want a stricter
   paper-mode gate during initial rollout.

4. **Re-research cadence**: 90-day TTL is a guess. Could be tighter
   (30 days) if Kalshi rephrases rules often, or looser (180 days)
   if churn is rare. Recommend starting at 90 and tuning from
   observed `rules_primary` diffs.

5. **Where do verifications actually happen** — dashboard button, or
   `gh pr create`-only? Dashboard is faster but requires writing
   YAML edits server-side (small attack surface). PR-only is safer
   but slower. Recommend PR-only for v1; dashboard sugar later.

---

## 9. Appendix — explicit table of all 39 series

See [`weather_station_xref_audit.json`](./weather_station_xref_audit.json)
for the verbatim audit. Summarized:

| Series | Currently maps to | Should map to | Status |
|---|---|---|:--:|
| KXLOWTNYC | KJFK | KNYC | ❌ FIX |
| KXHIGHTSEA | KSEA | KSEA | ✅ |
| KXHIGHTBOS | KBOS | KBOS | ✅ |
| KXLOWTAUS | KAUS | KAUS | ✅ |
| KXHIGHTDC | KDCA | KDCA | ✅ |
| KXHIGHLAX | KLAX | KLAX | ✅ |
| KXLOWTSATX | KSAT | KSAT | ✅ |
| KXHIGHTMIN | KMSP | KMSP | ✅ |
| KXHIGHNY | KJFK | KNYC | ❌ FIX |
| KXHIGHCHI | KORD | KMDW | ❌ FIX |
| KXLOWTMIN | KMSP | KMSP | ✅ |
| KXHIGHTSFO | KSFO | KSFO | ✅ |
| KXHIGHTATL | KATL | KATL | ✅ |
| KXHIGHAUS | KAUS | KAUS | ✅ |
| KXLOWTDC | KDCA | KDCA | ✅ |
| KXLOWTCHI | KORD | KMDW | ❌ FIX |
| KXLOWTPHIL | KPHL | KPHL | ✅ |
| KXLOWTHOU | KIAH | KHOU | ❌ FIX |
| KXHIGHTOKC | KOKC | KOKC | ✅ |
| KXHIGHDEN | KDEN | KDEN | ✅ |
| KXLOWTDEN | KDEN | KDEN | ✅ |
| KXHIGHTPHX | KPHX | KPHX | ✅ |
| KXHIGHTNOLA | KMSY | KMSY | ✅ |
| KXHIGHTHOU | KIAH | KHOU | ❌ FIX |
| KXLOWTSFO | KSFO | KSFO | ✅ |
| KXHIGHTDAL | KDFW | KDFW | ✅ |
| KXLOWTDAL | KDFW | KDFW | ✅ |
| KXHIGHMIA | KMIA | KMIA | ✅ |
| KXLOWTNOLA | KMSY | KMSY | ✅ |
| KXHIGHTSATX | KSAT | KSAT | ✅ |
| KXHIGHPHIL | KPHL | KPHL | ✅ |
| KXLOWTMIA | KMIA | KMIA | ✅ |
| KXLOWTOKC | KOKC | KOKC | ✅ |
| KXLOWTLAX | KLAX | KLAX | ✅ |
| KXLOWTATL | KATL | KATL | ✅ |
| KXLOWTSEA | KSEA | KSEA | ✅ |
| KXLOWTPHX | KPHX | KPHX | ✅ |
| KXLOWTBOS | KBOS | KBOS | ✅ |
| KXTEMPNYCH | (rules-coords path; AccuWeather) | ACCU_CENTRAL_PARK | ⚠️ source-mismatch |

**Tally:** 32 ✅ correct · 6 ❌ wrong station · 1 ⚠️ wrong source.
