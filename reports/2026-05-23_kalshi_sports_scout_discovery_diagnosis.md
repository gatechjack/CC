# kalshi_sports_scout — one-observation-per-ticker root-cause diagnosis

**Supplements:** the v2 review + addendum
(`reports/2026-05-23_kalshi_sports_scout_phase0_review.md`,
`reports/2026-05-23_kalshi_sports_scout_phase0_addendum.md`).

**Question:** Why does each in-scope game ticker appear in exactly one
scan (88 of 92 MLB tickers observed once) when hourly scans run for
9 days and games are mostly 5–30 hours pre-commencement at observation?

**Verdict:** It is **rotation of the 50-series cap against pykalshi's
non-stationary `get_all_series` ordering**, not post-commencement
exit. Minimal fix is a small change to `discover_by_categories` to add
a series-prefix filter, configured to the 5 in-scope `KX<LEAGUE>GAME`
prefixes.

---

## 1. Post-commencement exit — ruled out

```
league | n   | mean_hrs_to_game | min_hrs_to_game | max_hrs_to_game | n_post_commence
MLB    | 96  | 17.02            | 4.82            | 29.35           | 0
MLS    | 113 | 62.59            | -0.98           | 179.2           | 2
NBA    | 24  | 20.93            | 4.92            | 37.44           | 0
NFL    | 210 | 2789.1           | 2659.04         | 2883.89         | 0
NHL    | 18  | 49.58            | 10.7            | 113.74          | 0
```

- **0 post-commence rows** for MLB, NBA, NFL, NHL. **2 of 461** total (MLS).
- MLB observations sit ~5–30 hours pre-commencement. With a 1h poll
  cadence, that's a 5–30-hour window per game during which the scout
  could in principle re-observe. Yet 96% of MLB markets are observed
  exactly once.
- NFL is the outlier: 2659–2884 hours = ~110–120 days pre-game. These
  are 2026-season placeholder lines. (Already flagged in v2 addendum.)

Post-commencement market exit is not the dominant mechanism.

---

## 2. Scan-cycle inventory — rotation confirmed

```sql
SELECT COUNT(*), SUM(CASE WHEN n_observed=0 THEN 1 ELSE 0 END),
       AVG(markets_pre_filter), MAX(markets_pre_filter)
FROM (audit_event scan rows since 2026-05-14 21:42)
```

| metric                          | value |
|---------------------------------|-------|
| total scans                     | 188   |
| **scans with zero in-scope obs**| **167 (88.8%)**|
| scans with any in-scope obs     | 21 (11.2%)|
| mean markets returned per scan  | 333.9 |
| max markets returned per scan   | 654   |

A typical scan looks like:

```
ts                          | pre  | unm  | nomatch | obs
2026-05-19T02:17:17+00:00   | 495  | 495  | 0       | 0
2026-05-19T03:17:36+00:00   | 396  | 396  | 0       | 0
2026-05-19T04:17:52+00:00   | 350  | 350  | 0       | 0
2026-05-19T05:18:09+00:00   | 337  | 307  | 0       | 30  ← in-scope leagues land
2026-05-19T06:18:26+00:00   | 338  | 338  | 0       | 0   ← gone next hour
2026-05-19T07:18:44+00:00   | 278  | 278  | 0       | 0
...
2026-05-19T15:21:51+00:00   | 403  | 361  | 6       | 36  ← reappear
2026-05-19T16:22:06+00:00   | 229  | 229  | 0       | 0
```

The pattern is sparse appearance, not continuous coverage. Each scan
DOES return hundreds of Sports-category markets, but the in-scope 5
leagues are present in only ~11% of scans. When they ARE present,
~30 in-scope markets arrive together (one scan picks up most of
today's games + tomorrow's pre-posted lines, then they vanish next
hour).

This pattern is consistent with the in-scope leagues being **rotated
in and out of the returned set by the 50-series cap**.

---

## 3. Discovery / pagination logic in the path

The scout calls (`agents/strategies/kalshi_sports_scout.py:144-154`):

```python
self._discovery_cache = await kalshi_broker.list_markets(
    categories=("Sports",),
    max_series_per_category=max_series,    # 50 (from strategies.yaml)
    max_markets_per_series=max_markets,    # 50
)
```

`KalshiBroker.list_markets` (`brokers/kalshi.py:333-364`) is a thin
passthrough to:

```python
return await discover_by_categories(
    self._client, categories=categories or DEFAULT_DISCOVERY_CATEGORIES,
    max_series_per_category=max_series_per_category,
    max_markets_per_series=max_markets_per_series,
)
```

`discover_by_categories` in `data/kalshi_market_map.py:324-378` is
where the cap binds:

```python
all_series_tickers: list[str] = []
for cat in categories:
    series = await client.get_all_series(
        category=cat, limit=max_series_per_category,
    )
    ...
    cat_count = 0
    for s_obj in series:
        if cat_count >= max_series_per_category:   # ← 50 cap binds HERE
            break
        t = getattr(s_obj, "ticker", None)
        if t:
            all_series_tickers.append(t)
            cat_count += 1

log.info(
    "kalshi_market_map: enumerated %d series (capped at %d/category × %d categories)",
    len(all_series_tickers), max_series_per_category, len(categories),
)
```

Followed by, per series, `client.get_markets(series_ticker=st,
status=MarketStatus.OPEN, limit=max_markets_per_series)`.

**Two confirmations of the failure mode from this code:**

1. The source comment at lines 354-356 names the issue explicitly:
   *"pykalshi's `get_all_series` silently fetches all pages for the
   category despite limit + fetch_all=False. We must cap consumption
   ourselves; never trust the param to bound output."*

   So pykalshi returns the FULL list of Sports-category series in
   whatever order Kalshi's API supplies. Our local truncation picks
   the first 50. If Kalshi's ordering is anything other than stable +
   "in-scope first" (it's not — likely volume-ranked or recent-activity-
   ranked), the first 50 changes scan-to-scan.

2. The `break` at line 368 happens BEFORE any filtering for what we
   care about — every returned series ticker counts toward the cap,
   even UFC / golf / tennis / NCAA / Liga MX which we know we will
   discard at parse time.

The 1868 ticker-parse-failure unmapped rows (flagged in v2 §3a as
95.8% of unmapped) are the smoking gun for this: the cap is being
**spent on series we'll throw away**.

---

## 4. Why this produces n_obs=1 per in-scope ticker

Steady-state arithmetic:

- Kalshi's Sports category contains many more than 50 series. Includes
  MLB, NBA, NHL, MLS, NFL (in scope) plus EPL, Champions League,
  international soccer leagues, UFC, ATP/WTA tennis, PGA golf,
  NASCAR/F1, NCAA football/basketball, eSports series, parlay/spread
  series, futures series. Likely 100–300+ active series at any time.
- pykalshi's `get_all_series` returns them in some ordering that is
  not "in-scope first." From the data, the ordering is not stable
  scan-to-scan either: 21/188 scans have any in-scope series make the
  top 50.
- Within a single scan: when an in-scope league DOES make the cut,
  ~30 of its markets get observed. Then in the next scan, that league
  is rotated out and a different mix of out-of-scope series fills the
  50 slots.
- Result: each in-scope game ticker has roughly an ~11% chance of
  landing in any given hourly scan. Across a ~10-hour pre-game window,
  E[observations per ticker] ≈ 1.1.

This matches the observed corpus: 92 distinct MLB tickers / 96 rows =
1.04 observations per ticker.

---

## 5. Minimal fix

There are two viable minimal fixes; recommended is **B** (small code
change), **A** is acceptable as an emergency-only knob:

### Option A — config bump only (no code change)

Edit `config/strategies.yaml` on prod (hot-reloads via mtime check):

```yaml
kalshi_sports_scout:
  discovery:
    max_series_per_category: 50  → 500   # absorb the entire Sports category
    max_markets_per_series: 50           # leave as-is (not the binding cap)
```

**Pros:**
- Zero code change. Lives in the deploy-log diff alone.
- pykalshi has already fetched the full series list (per the source
  comment) — the local cap is the only enforcement. Bumping it is
  essentially free for the cap step itself.

**Cons:**
- `get_markets(series_ticker=st)` is called per-series with 150ms
  delay. If Sports has, say, 200 series, that's 30s per scan plus
  network. At 1h cadence this is fine, but it's wasteful — we're
  paying for UFC/golf/tennis series we'll discard at parse time.
- Audit-row volume balloons proportionally: `n_unmapped` per cycle
  would rise from ~300 to ~1000+.

### Option B — series-prefix filter (recommended)

Add a `series_prefix_filter` parameter to
`discover_by_categories` in `data/kalshi_market_map.py` and have the
scout pass it. Loop change is ~3 lines:

```python
async def discover_by_categories(
    client, *,
    categories: tuple[str, ...] = DEFAULT_DISCOVERY_CATEGORIES,
    max_series_per_category: int = 50,
    max_markets_per_series: int = 50,
    series_prefix_filter: tuple[str, ...] | None = None,
    inter_call_delay_sec: float = 0.15,
):
    ...
    for s_obj in series:
        if cat_count >= max_series_per_category:
            break
        t = getattr(s_obj, "ticker", None)
        if not t:
            continue
        if series_prefix_filter is not None and not any(
            t.startswith(p) for p in series_prefix_filter
        ):
            continue
        all_series_tickers.append(t)
        cat_count += 1
```

Plus passthrough on `KalshiBroker.list_markets` (one more kwarg) and
the scout call site:

```python
self._discovery_cache = await kalshi_broker.list_markets(
    categories=("Sports",),
    max_series_per_category=100,    # comfortable headroom for in-scope leagues
    max_markets_per_series=max_markets,
    series_prefix_filter=("KXMLBGAME", "KXNBAGAME", "KXNHLGAME",
                          "KXMLSGAME", "KXNFLGAME"),
)
```

**Pros:**
- 5–10 lines total. Cap counts only in-scope series. Out-of-scope
  series are skipped before they consume a cap slot — and before any
  `get_markets` call is spent on them.
- `n_unmapped` per cycle drops to ~0 (no more 1868 parse failures
  polluting the audit log).
- Cost per scan drops: 5 in-scope series × ~30 markets × ~150ms ≈
  22.5s per scan, fixed and predictable.
- Forward-compatible: adding a new league = adding its prefix to the
  YAML / call site.

**Cons:**
- Three files touched: `data/kalshi_market_map.py`,
  `brokers/kalshi.py`, `agents/strategies/kalshi_sports_scout.py`. All
  changes are small and additive (optional kwarg with `None` default).
- Strategies-yaml grows a new `series_prefix_filter:` list under
  `kalshi_sports_scout.discovery`. (Or hardcoded — see below.)

### Recommended config shape for Option B

```yaml
kalshi_sports_scout:
  discovery:
    max_series_per_category: 100
    max_markets_per_series: 50
    series_prefix_filter:
      - KXMLBGAME
      - KXNBAGAME
      - KXNHLGAME
      - KXMLSGAME
      - KXNFLGAME
```

Mirrors `LEAGUE_TO_SPORT_KEY` in `data/sports_team_mapping.py` and
the `leagues:` filter already in the scout's strategies.yaml block.
A future cleanup could derive prefixes from the same source of truth
(don't repeat-yourself), but for the minimal fix the list is fine.

---

## 6. Why this is sufficient to produce n_books ≥ 6 repeat observations

With Option B in place:

- Every scan iterates EVERY in-scope series (no rotation cap pressure).
- Within each series, `max_markets_per_series=50` is non-binding for
  MLB-style schedules (15 games × 2 sides = 30 markets per day; even
  with multi-day forward visibility, well under 50). For very high-
  schedule windows (NCAAB, NBA All-Star) it could become binding —
  monitor `n_observed` per scan as a watchpoint and bump to 100 if
  needed.
- Each pre-commencement market would appear in every scan from when
  Kalshi posts it (typically a few days to weeks ahead) through
  commencement. At 1h cadence and ~5–30h MLB pre-game window, that's
  5–30 observations per market.
- `n_books` is independent of the discovery fix — it's the count of
  bookmakers from the-odds-api per game. But the late observations
  (within ~1–4h of game start) will naturally see `n_books ≥ 6` once
  DK/FD/MGM/Caesars have all posted. The `n_books ≥ 6` filter
  recommended in the addendum should be applied at analysis time,
  not at logging time — log everything, filter at gate computation.

---

## 7. Sanity-check items the user might want before shipping

These don't block; they reduce surprise post-fix:

- **Probe `pykalshi.get_all_series("Sports")` once locally** to confirm
  the Sports category series count and whether MLB/NBA/NHL/MLS/NFL
  series tickers exactly match `KX<LEAGUE>GAME`. Specifically, MLB
  series might be `KXMLB-2026` or similar instead of `KXMLBGAME`.
- **Probe how many KXMLBGAME-prefixed markets are OPEN at once** to
  confirm `max_markets_per_series=50` is non-binding. If it's binding,
  observations will still be incomplete even after the series-prefix
  fix.
- **Note for the deploy**: the scout's `_discovery_cache` lives in
  process memory. On config change (hot-reload via mtime), the cache
  invalidates on the next 15min boundary. No restart needed.

---

## 8. Verification queries used

```sql
-- Time-to-commencement per league (confirms post-commence is not the cause)
WITH r AS (
  SELECT json_extract(payload_json,'$.league') AS league,
         json_extract(payload_json,'$.commenced_at') AS commenced_at,
         ts AS obs_ts
  FROM audit_event
  WHERE kind = 'kalshi_sports_observed'
    AND strftime('%s', ts) >= strftime('%s', '2026-05-14 21:42:00')
    AND json_extract(payload_json,'$.commenced_at') IS NOT NULL
)
SELECT league, COUNT(*),
       ROUND(AVG((julianday(commenced_at)-julianday(obs_ts))*24),2) mean_hrs,
       SUM(CASE WHEN julianday(commenced_at)<julianday(obs_ts) THEN 1 ELSE 0 END) post_commence
FROM r GROUP BY league;

-- Scan-level zero-obs rate (confirms rotation)
SELECT COUNT(*) total_scans,
       SUM(CASE WHEN json_extract(payload_json,'$.n_observed')=0 THEN 1 ELSE 0 END) zero_obs,
       AVG(json_extract(payload_json,'$.markets_pre_filter')) avg_pre
FROM audit_event
WHERE kind = 'kalshi_sports_scout_scan'
  AND strftime('%s', ts) >= strftime('%s', '2026-05-14 21:42:00');
```

Both use `strftime('%s', ts)` rather than raw string compare to dodge
the SQLite ISO-T-vs-space trap (memory:
`feedback_sqlite_iso_datetime_comparison`).
