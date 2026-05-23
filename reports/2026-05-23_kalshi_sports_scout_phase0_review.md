# kalshi_sports_scout — Phase-0 observation gate review

**Author:** session 2026-05-23
**Status:** Gate decision **BLOCKED** — corpus is invalid due to units bug
**Observation window:** 2026-05-14 22:34 UTC → 2026-05-23 22:55 UTC (~9 days)
**Cycles observed:** 187 (~1.15h cadence; matches the post-22:28 1h poll)
**Quota usage:** 21 calls / 500 monthly free-tier ceiling (4.2%) — non-binding

---

## TL;DR

The 9-day observation pass produced 461 `kalshi_sports_observed` rows. Every
one of them is a data-quality artifact of a **100× units bug** in the Kalshi
implied-probability calculation. The bug makes every signed divergence equal
to `bookmaker_implied × 100 − ~0.5pp` to two decimals, which is why every
league shows 100% hit-rate at the 10pp threshold and `would_fire_buy = "yes"`
on 461/461 rows.

**Phase-0 gate (full division / scope-down / shelve) cannot be decided from
this corpus.** Fix the bug, restart the observation window. Recommend
deferring the three-way fork until valid data exists. The bug is one line
(plus a sibling line for `no_ask`); the strategy itself is otherwise sound.

---

## 1. The bug

`trading_corp/agents/strategies/kalshi_sports_scout.py:232–240`:

```python
yes_ask_cents = getattr(m, "yes_ask", None)
no_ask_cents  = getattr(m, "no_ask", None)
kalshi_yes = (yes_ask_cents / 100.0) if yes_ask_cents else None
kalshi_no  = (no_ask_cents  / 100.0) if no_ask_cents  else None
kalshi_implied_yes = None
if kalshi_yes is not None and 0 < kalshi_yes < 1:
    kalshi_implied_yes = kalshi_yes
elif kalshi_no is not None and 0 < kalshi_no < 1:
    kalshi_implied_yes = 1.0 - kalshi_no
```

The variable name and the `/100.0` divisor assume `m.yes_ask` is in **cents**.
It is not. On the discovery-snapshot Market shape that
`kalshi_broker.list_markets(...)` returns, `yes_ask` is already in **dollars
(0–1)**, normalized through `kalshi_quote_dollars()` in `brokers/kalshi.py`.

Two independent confirmations the field is in dollars:

1. **`brokers/kalshi.py:275`** — `(yes_bid + yes_ask) / 2 / _CENTS_PER_DOLLAR`
   for orderbook mid-pricing converts to dollars at that boundary.
2. **`agents/strategies/kalshi_weather_arb.py:669`** — uses the same field
   with the predicate `0 < yes_ask < 1` and the comment *"Use yes_ask as buy
   YES cost → implied_yes ≈ yes_ask_dollars"*. The sibling strategy
   explicitly relies on the 0–1 scale.

So a market with YES quoted at ~$0.50 produces `kalshi_implied_yes = 0.005`
instead of `0.50`, and the divergence becomes
`(book_implied − 0.005) × 100 = book_implied × 100 − 0.5`pp.

### Numerical confirmation (predicted vs observed)

Per-league signed-divergence means, computed from the data, against what the
bug hypothesis predicts (≈ `book_mean × 100 − 0.5`):

| league | side       | mean book_yes | mean kalshi_yes | predicted div | observed div | match |
|--------|------------|---------------|-----------------|---------------|--------------|-------|
| MLB    | yes_is_home=1 | 0.515      | 0.005           | 51.0          | 51.01        | ✓     |
| MLB    | yes_is_home=0 | 0.485      | 0.005           | 48.0          | 47.94        | ✓     |
| NFL    | yes_is_home=1 | 0.577      | 0.006           | 57.1          | 57.07        | ✓     |
| NFL    | yes_is_home=0 | 0.423      | 0.005           | 41.8          | 41.86        | ✓     |
| NBA    | yes_is_home=1 | 0.642      | 0.006           | 63.6          | 63.68        | ✓     |
| NBA    | yes_is_home=0 | 0.358      | 0.005           | 35.3          | 35.30        | ✓     |
| MLS    | yes_is_home=1 | 0.491      | 0.005           | 48.6          | 48.62        | ✓     |
| MLS    | yes_is_home=0 | 0.274      | 0.003           | 27.1          | 27.14        | ✓     |
| NHL    | yes_is_home=1 | 0.588      | 0.006           | 58.2          | 58.24        | ✓     |
| NHL    | yes_is_home=0 | 0.412      | 0.005           | 40.7          | 40.72        | ✓     |

Bug hypothesis matches all 10 cells to ≤0.07pp. The "divergence" the scout has
been logging is, to extremely high accuracy, just the bookmaker's
vig-removed implied probability re-expressed in percentage points minus
half a point. Kalshi prices are *not* part of the signal.

### Why no validation tripped it

- `if kalshi_yes is not None and 0 < kalshi_yes < 1` — `0.005` passes.
- No bilateral check that `kalshi_yes_dollars + kalshi_no_dollars ≈ 1.0`. With
  the bug, sum-of-asks across both sides of a binary market is ~0.010 instead
  of ~1.0 (or slightly over 1 if including vig). That sanity gate would have
  caught this on the first scan.

---

## 2. Phase-0 metrics that would have been the gate inputs (NOW INVALID)

For the record, the matrix Deploy_log L3714 asked for, computed from the
bug-corrupt corpus. **Do not use these for any decision.**

### Per-league hit-rate at thresholds (corpus invalid)

| league | n_obs | mean_\|div\| | ≥1pp | ≥2pp | ≥3pp | ≥5pp | ≥7pp | ≥10pp |
|--------|-------|--------------|------|------|------|------|------|--------|
| MLB    | 96    | 49.48        | 100% | 100% | 100% | 100% | 100% | 100%   |
| MLS    | 113   | 37.98        | 100% | 100% | 100% | 100% | 100% | 99.1%  |
| NBA    | 24    | 49.49        | 100% | 100% | 100% | 100% | 100% | 100%   |
| NFL    | 210   | 49.47        | 100% | 100% | 100% | 100% | 100% | 100%   |
| NHL    | 18    | 49.48        | 100% | 100% | 100% | 100% | 100% | 100%   |

Note also `divergence_log_threshold_pct: 1.0` is set in the deployed
`config/strategies.yaml`. Once the units bug is fixed, this threshold will
do real work — only markets with genuine |divergence| ≥ 1pp would be
logged, so the post-fix corpus is downward-truncated for analysis
purposes. Either lower the threshold to 0.0 for the rerun OR remember the
filter when computing edge-magnitude distributions.

### Daily observed-row counts (corpus invalid)

```
day        |MLB|MLS|NBA|NFL|NHL|
2026-05-16 |   | 23|   |   |  6|
2026-05-17 | 22|   |   | 30|   |
2026-05-18 |   | 18|   | 30|   |
2026-05-19 | 36| 18|   | 30|   |
2026-05-20 |   |   | 12| 30|  8|
2026-05-21 |   |   | 12| 60|   |
2026-05-22 | 38| 18|   | 30|   |
2026-05-23 |   | 36|   |   |  4|
```

NFL is over-represented (210/461 = 45.6%) despite being off-season for most
of the corpus window. Likely cause: Kalshi maintains forward-looking 2026-season
NFL markets year-round and the-odds-api has placeholder lines on those games.
Those lines are likely thinner/staler than in-season — would need real-data
re-evaluation post-fix.

---

## 3. Secondary anomalies (worth keeping on file)

### a. 33-hour gap between first scan and first observed row

First `kalshi_sports_scout_scan`: 2026-05-14 22:34:57 UTC.
First `kalshi_sports_observed`: 2026-05-16 06:16:23 UTC.

That's ~32 hours of scans producing *zero* observed rows. Plausible causes
(not investigated):
- Initial Kalshi Sports discovery returned no quoted markets,
- An earlier higher `divergence_log_threshold_pct` was relaxed mid-window,
- The 30-min sport-key cache populated empty initially.

The gap doesn't block the gate decision but might be worth a one-line
explanation in the post-fix rerun.

### b. Unmapped rows dominate by a factor of 4

`kalshi_sports_scout_unmapped`: 1950 rows over 187 cycles ≈ 10.4 per cycle.
The agent caps unmapped audits at the first 10 per cycle
(`unmapped_audits[:10]`) — meaning **every scan saturates the cap** and the
true unmapped count is at least 10/cycle and likely far higher.

Breakdown by reason:

| reason                                | n      | share |
|---------------------------------------|--------|-------|
| ticker_parse_fail_or_unsupported_league | 1868 | 95.8% |
| no_game_match_in_odds_api             | 80     | 4.1%  |
| team_code_not_in_mapping              | 2      | 0.1%  |

**95.8%** of unmapped fall to ticker-parse failures, not team-code-mapping
gaps. The 155-team mapping is functioning; the **Kalshi ticker grammar
parser is what's losing markets**. Could be event tickers from other Sports
sub-categories (UFC, golf, tennis, college sports) that pass the "Sports"
category filter but fail `parse_sports_ticker`. Recommend a per-cycle
sampling of unparsed ticker strings before the post-fix rerun, so the next
9-day window doesn't burn another phase characterizing a fixable parser
issue.

### c. would_fire_buy = "yes" on 100% of rows

Direct consequence of the units bug: signed `divergence_pct =
book_yes_implied * 100 − ~0.5` is always positive. After the fix, expect a
roughly even split between buy and sell directions if the bookmaker median
is unbiased relative to Kalshi mid.

### d. Three-hour quiet trailing gap

Last `kalshi_sports_observed`: 2026-05-23 19:54 UTC. Last
`kalshi_sports_scout_scan`: 2026-05-23 22:55 UTC. Three hours of scans
with no qualifying observed rows. On a Saturday evening across MLB/MLS/NHL
that is plausibly the natural market closure pattern between in-progress and
next-day cards; not necessarily an anomaly.

---

## 4. Action items (proposed; nothing executed)

These are the steps that would unblock the Phase-0 gate decision. **No code
has been changed and no deploy has been initiated.**

1. **Fix the units bug** in
   `trading_corp/agents/strategies/kalshi_sports_scout.py:232–240`. Remove
   the `/100.0` divisor; rename `yes_ask_cents` → `yes_ask_dollars` to make
   the units explicit; keep the `0 < x < 1` guard.
2. **Add the sum-to-1 sanity gate**: if `yes_ask_dollars + no_ask_dollars`
   is < 0.5 or > 1.5, skip the row and emit a `kalshi_sports_scout_invalid`
   audit row instead. This would have tripped on row #1 with the current bug.
3. **Decide threshold-for-rerun**: lower `divergence_log_threshold_pct` to
   0.0 (or 0.1) for the rerun window so we see the true divergence
   distribution, not the post-1pp tail.
4. **Restart the 9-day observation gate** from deploy ts. The new corpus is
   the input to the actual full-division / scope-down / shelve decision.
5. *(Stretch — defer if it expands scope)*: characterize the
   `ticker_parse_fail_or_unsupported_league` unmapped class before the
   rerun, so we don't burn another phase on a fixable parser issue.

The three-way fork (full division B/C, scope-down, shelve) remains the
post-rerun decision. It is not yet decidable.

---

## 5. Verification queries used

For reproducibility (run from `/home/azureuser/trading_corp` on tc-prod-vm):

```sql
-- Counts per kind
SELECT kind, COUNT(*) AS n, MIN(ts), MAX(ts)
FROM audit_event
WHERE kind LIKE 'kalshi_sports_scout%' OR kind = 'kalshi_sports_observed'
  AND ts >= '2026-05-14 21:42:00'
GROUP BY kind;

-- Per-league summary
SELECT json_extract(payload_json,'$.league') AS league,
       COUNT(*) AS n,
       AVG(json_extract(payload_json,'$.abs_divergence_pct')) AS mean_abs,
       AVG(json_extract(payload_json,'$.bookmaker_yes_implied')) AS mean_book,
       AVG(json_extract(payload_json,'$.kalshi_implied_yes')) AS mean_kalshi
FROM audit_event
WHERE kind = 'kalshi_sports_observed'
  AND ts >= '2026-05-14 21:42:00'
GROUP BY league;
```

Each query fits well under the 4 KB `az run-command` stdout cap and returns
in <2 s on the prod DB.
