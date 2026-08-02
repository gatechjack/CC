# Rider A — Coinalyze fine-flow rolling archive

**Goal:** accumulate our own 1min/5min/15min flow archive (CVD components, OI,
funding, liquidations, long/short ratio) *before* Coinalyze's free-tier
retention drops it, so future S4 versions can test fine-grained flow features
across a growing history that the API alone cannot provide.

## Retention floors (measured 2026-08-02, stable across repeated reads)

| interval | retained back | floor | note |
|---|---|---|---|
| 1min  | ~26 hours  | **26.0h** | binding constraint |
| 5min  | ~7 days    | 167.7h | |
| 15min | ~21 days   | 501.0h | |

`probe_coinalyze_retention.py` re-measures on demand.

## Required cadence — never lose a day

Each re-pull must OVERLAP the previous one, i.e. the re-pull interval must be
**shorter than the retention floor** (26h for 1min). To keep a safety margin:

- **Recommended: run every 12 hours.** (26h floor / 2 → comfortable margin; a
  single late/failed run still recovers on the next.)
- Daily (24h) *technically* works for 1min (24 < 26) but leaves only ~2h buffer
  — one late run loses data. 5min/15min tolerate daily easily.
- Simplest operable rule: **schedule all fine intervals every 12h.**

## Command

```
python research/kalshi_crypto_v2/loaders/coinalyze.py --fine-only
```

`--fine-only` pulls 1min/5min/15min with `refresh=True` (bypasses the raw cache
so it always fetches the freshest tail).

## Accumulation semantics (why re-runs grow the archive, never shrink it)

`lab_coinalyze` PK = `(asset, ts_ms, interval, metric)`; the loader only ever
`INSERT OR REPLACE`s — there is **no DELETE**. A re-run re-writes the overlapping
recent minutes (identical values) and appends the newest minutes that appeared
since the last run. Distinct `ts_ms` therefore accumulate monotonically. The
per-interval coverage row in `lab_coverage` will show the archive span growing
past the API's retention floor as runs stack up.

## Deployment note (operator-gated)

The lab DB lives on the research host (local). Scheduling the 12h job is the
operator's call — options: Windows Task Scheduler on the research host, or fold
a fine-flow pull into the existing prod kcv2 cadence and sync to the lab archive.
Not auto-deployed. First capture (2026-08-02) is already in `lab/kcv2_lab.db`.
