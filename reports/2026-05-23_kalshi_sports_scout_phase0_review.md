# kalshi_sports_scout — Phase-0 observation gate review

**Author:** session 2026-05-23
**Status:** Gate **DECIDABLE** from the recovered corpus (see §0); leading
recommendation is **scope-down with caveats**, not full division and not shelve
**Observation window:** 2026-05-14 22:34 UTC → 2026-05-23 22:55 UTC (~9 days)
**Cycles observed:** 187 (~1.15h cadence; matches the post-22:28 1h poll)
**Quota usage:** 21 calls / 500 monthly free-tier ceiling (4.2%) — non-binding

> **Revision history**
> - v1 (initial): incorrectly concluded the 9-day corpus was unusable due to
>   a 100× units bug; framed the gate as BLOCKED.
> - v2 (this version): bug is deterministically reversible from the stored
>   field; recovery is exact for the entire 461-row corpus; gate IS
>   decidable. Bug-fix recommendations preserved for the post-decision PR.

---

## 0. The bug is reversible — recovered corpus

The 100× units bug at `kalshi_sports_scout.py:232-240` (see §1) reduces to
a single formula because:

- All 461 `kalshi_sports_observed` rows took the `yes_ask` branch
  (verified: `SELECT COUNT(*) WHERE kalshi_implied_yes >= 0.5` returns 0;
  stored values range 0.0009 to 0.0084).
- The bug is `stored = real_kalshi_yes / 100`, so
  **`recovered_kalshi_implied_yes = stored × 100`** for every row.
- Recovery is exact at integer-cent Kalshi prices (the entire normal
  trading grid). The 4-decimal-place rounding in the audit write is
  below the precision threshold of the cents grid.

(The `no_ask` fallback branch was not used by any row in this corpus.
Had it fired, recovery would have been `100 × stored − 99` instead, also
deterministic. Branch is detectable per-row by whether stored < or > 0.5.
For future sessions: a mixed-branch corpus is still fully recoverable.)

Sanity checks against the recovered values:

| ticker                                  | book   | recovered kalshi | true div (pp) |
|-----------------------------------------|--------|-------------------|---------------|
| KXMLSGAME-26MAY23STLATX-ATX             | 0.0866 | 0.09              |  -0.34        |
| KXMLSGAME-26MAY23STLATX-STL             | 0.7063 | 0.72              |  -1.37        |
| KXMLSGAME-26MAY23CINORL-CIN             | 0.5592 | 0.57              |  -1.08        |
| KXMLBGAME-26MAY201310BALTB-BAL          | 0.4303 | 0.67              | -23.97        |
| KXNBAGAME-26MAY22OKCSAS-SAS             | 0.3182 | 0.54              | -22.18        |

These look like plausible book-vs-Kalshi divergences. The corpus is real
edge data, not artifacts.

---

## 1. Recovered Phase-0 gate matrix

Per Deploy_log L3714, the gate inputs are median absolute divergence per
league + hit-rate at various thresholds. **These are the recovered
numbers and they ARE the gate inputs.**

### Per-league summary (corrected)

| league | n   | median \|div\| | mean \|div\| | min   | max    | mean_signed |
|--------|-----|----------------|--------------|-------|--------|-------------|
| MLB    | 96  | 3.08           | 4.17         | 0.05  | 23.97  | **−2.27**   |
| MLS    | 113 | 0.92           | 1.31         | 0.03  | 6.43   | −0.99       |
| NBA    | 24  | **12.78**      | 10.10        | 0.18  | 22.18  | −1.21       |
| NFL    | 210 | 2.44           | 3.24         | 0.01  | 16.80  | **−3.07**   |
| NHL    | 18  | 1.50           | 3.48         | 0.10  | 25.28  | −2.28       |

### Hit-rate at threshold (corrected)

| league | ≥1pp  | ≥2pp  | ≥3pp  | ≥5pp  | ≥7pp  | ≥10pp |
|--------|-------|-------|-------|-------|-------|-------|
| MLB    | 75.0% | 61.5% | 51.0% | 32.3% | 20.8% | 7.3%  |
| MLS    | 46.9% | 15.9% | 9.7%  | 3.5%  | 0.0%  | 0.0%  |
| NBA    | 66.7% | 62.5% | 62.5% | 58.3% | 58.3% | 58.3% |
| NFL    | 87.6% | 62.9% | 34.8% | 15.7% | 8.6%  | 3.8%  |
| NHL    | 50.0% | 50.0% | 33.3% | 16.7% | 5.6%  | 5.6%  |

### Two structural signals worth naming

**(a) Mean signed divergence is negative on every league.** Book implied
< Kalshi implied across MLB / MLS / NBA / NFL / NHL. That's a one-sided
structural pattern: Kalshi systematically prices YES higher than the
vig-removed bookmaker consensus. NFL shows the strongest tilt (mean
−3.07pp). If that bias is fillable on the NO side, it's directional alpha
even before considering tail divergences.

This is a stronger signal than tail-divergence hit-rate because it's
sign-consistent — it survives averaging instead of cancelling out.

**(b) NBA divergences are concentrated and wide.** Median 12.78pp,
58.3% of rows above 10pp. But n=24 concentrated in 2 series (OKC/SAS
×3 dates, NYK/CLE ×1 pair) means this is potentially:

- Real edge — Kalshi mispricing playoff series-leg games, OR
- Liquidity wedge — low-volume Kalshi NBA markets with stale ~50¢ asks
  (the top NBA rows have kalshi_true in 0.49–0.65 range; many close
  to 50¢ suggests possible empty-book defaulting), OR
- Sample artifact — 2 series isn't enough to call statistical edge.

Treat NBA as "needs explicit validation step" not "ship".

### Per-league daily observed-row counts

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

NFL is 210/461 = 45.6% of rows despite the season being months away.
These are mostly forward-looking 2026-season placeholder lines — both
on Kalshi and the-odds-api. Lines are thin/early and prices update
infrequently; the −3.07pp mean signed div on NFL is probably driven
by both sides being stale rather than by genuine alpha. **Discount NFL
weight in the gate decision until in-season data is in.**

---

## 2. The bug evidence (preserved from v1)

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

The variable name and the `/100.0` divisor assume `m.yes_ask` is in
**cents**. It is not. On the discovery-snapshot Market shape that
`kalshi_broker.list_markets(...)` returns, `yes_ask` is already in
**dollars (0–1)**, normalized through `kalshi_quote_dollars()` in
`brokers/kalshi.py`.

Two confirmations of the dollars scale:

1. `brokers/kalshi.py:275` — `(yes_bid + yes_ask) / 2 / _CENTS_PER_DOLLAR`
   for orderbook mid-pricing — explicit cents→dollars conversion at that
   boundary.
2. `agents/strategies/kalshi_weather_arb.py:669` — uses the same field
   with `0 < yes_ask < 1` and the comment *"Use yes_ask as buy YES cost
   → implied_yes ≈ yes_ask_dollars"*. The sibling strategy relies on
   the 0–1 scale.

### Why no guard tripped

- `if kalshi_yes is not None and 0 < kalshi_yes < 1` — 0.005 passes.
- No bilateral check that `kalshi_yes + kalshi_no ≈ 1.0`. Under the bug,
  yes_ask_dollars/100 + no_ask_dollars/100 ≈ 0.010 instead of ~1.0. A
  sum-to-1 guard would catch this on the first scan.

This is a fix worth adding so future scout-style strategies with similar
units mistakes fail loudly instead of silently logging 9 days of
inverted-scale data.

---

## 3. Secondary anomalies

### a. Unmapped rows dominate by a factor of 4

`kalshi_sports_scout_unmapped`: 1950 rows over 187 cycles ≈ 10.4 per cycle.
The agent caps unmapped audits at 10 per cycle, so the **true unmapped
count is at least 10/cycle and likely far higher**.

| reason                                  | n      | share |
|-----------------------------------------|--------|-------|
| ticker_parse_fail_or_unsupported_league | 1868   | 95.8% |
| no_game_match_in_odds_api               | 80     | 4.1%  |
| team_code_not_in_mapping                | 2      | 0.1%  |

95.8% are ticker-parse failures — the Kalshi ticker grammar parser is
what's losing markets, not the 155-team mapping. Probably non-game
Sports-category tickers (UFC, golf, tennis, college sports, futures)
passing the category filter but failing `parse_sports_ticker`. Worth
sampling the unparsed ticker strings before a scope-down PR to confirm
they're not all in-scope leagues being lost to a parser quirk.

### b. 33-hour gap between first scan and first observed row

First scan: 2026-05-14 22:34:57 UTC. First observed: 2026-05-16 06:16:23 UTC.

Plausible causes (not investigated): initial Kalshi Sports discovery
returned no quoted markets, threshold relaxed mid-window, or 30-min
sport-key cache populated empty. Not blocking the gate.

### c. would_fire_buy = "yes" on 100% of rows (BUG ARTIFACT)

Direct consequence of the units bug. After the fix, given the
recovered mean signed divergence is negative, expect `would_fire_buy`
to skew toward "no" (Kalshi YES > book YES → sell Kalshi YES).

### d. Three-hour quiet trailing gap

Last observed: 2026-05-23 19:54 UTC vs last scan: 22:55 UTC. Likely
just Saturday-evening game-card closure; not necessarily anomalous.

### e. Threshold filter was no-op under the bug

`divergence_log_threshold_pct: 1.0` filters out genuine sub-1pp markets.
Under the bug every divergence was ≥17pp so nothing was filtered — the
corpus is complete. Post-fix, this threshold would do real work; for
the rerun (if any) lower it to 0.0 or 0.1 so the post-fix distribution
isn't downward-truncated.

---

## 4. Gate options + what the data says about each

The three-way fork from Deploy_log L3714 was: full trading division
(option B/C), scope-down, or shelve. Reading the recovered matrix
against each:

### Option A — Shelve

**What would justify it:** All leagues with median |div| < 1pp AND no
sign-consistent bias. Would be the right call if MLS were representative.

**What the data shows:** Median |div| > 1pp on 4 of 5 leagues; mean signed
divergence is structurally negative across all 5 leagues. Shelve is the
wrong call on this corpus.

### Option B — Full division build (autonomous)

**What would justify it:** Median |div| ≥ 3pp across multiple leagues with
consistent direction, validated fillability, fees + slippage modeled into
EV-at-fill, and confidence the corpus is representative.

**What the data shows:** Median |div| ≥ 3pp on MLB and NBA only.
NFL is mostly off-season placeholder lines — discount until in-season.
NBA is n=24 concentrated in 2 series — needs validation, not commitment.
Fees + fillability + EV-at-fill are not yet modeled by this strategy
(scout is pure divergence in pp). Full division is premature.

### Option C — Scope-down

**What would justify it:** Some leagues show edge candidates, others
clearly don't; the directional bias is consistent enough to warrant a
narrower paper-trading pass with EV-at-fill modeling layered on.

**What the data shows:**
- **MLS: drop.** Median 0.92pp, max 6.43pp, zero rows ≥7pp. Books
  agree with Kalshi. No edge candidate.
- **MLB: keep for next phase.** Median 3.08pp, 7.3% above 10pp, mean
  signed −2.27pp. Sample n=96 across 4 days is reasonable; in-season
  baseball data is what we have most of.
- **NBA: keep but validate first.** Wide divergences but n=24 and
  concentrated in 2 playoff series. Pull broader NBA history before
  treating as edge — could be liquidity-wedge artifact.
- **NHL: keep with reservation.** n=18 too thin to conclude. Continue
  observation to build sample.
- **NFL: park until in-season.** Off-season lines are stale; the
  apparent −3.07pp signed div is not actionable until live-season
  data is in.

**This is what the data points to.** Decision is yours.

---

## 5. Suggested action items if scope-down is chosen

These are **proposed**; nothing has been changed or deployed.

1. **Fix the units bug** at `kalshi_sports_scout.py:232–240`. Remove
   the `/100.0` divisor; rename `yes_ask_cents` → `yes_ask_dollars`.
2. **Add a sum-to-1 sanity gate** (`kalshi_yes + kalshi_no` in
   [0.5, 1.5]). Skip-and-audit rows that fail.
3. **Lower `divergence_log_threshold_pct`** to 0.0 (or 0.1) for the
   next observation window so the corpus isn't downward-truncated.
4. **NBA validation step before any commit to NBA edge:** pull a
   broader NBA history (more than 2 series), inspect orderbook
   liquidity on the wide-divergence markets to distinguish edge from
   liquidity wedge.
5. **Constrain `leagues` to MLB, NBA, NHL** for the next phase; park
   NFL until ≥1 in-season weekend's worth of data, drop MLS.
6. **Phase-1 design**: add EV-at-fill (using observed bid/ask spread,
   not just yes_ask), fees, and fillability gates. This is the gate
   step between "divergence observed" and "viable to trade."
7. *(Stretch)* Characterize the 1868 ticker-parse-fail unmapped rows
   to confirm they're out-of-scope sub-categories and not lost
   in-scope leagues.

The three-way fork is decidable. The data points to **scope-down + bug
fix + NBA validation step**, not full division and not shelve. But the
call is yours.

---

## 6. Verification queries

For reproducibility (from `/home/azureuser/trading_corp` on tc-prod-vm):

```sql
-- Per-league corrected hit-rate matrix
WITH r AS (
  SELECT
    json_extract(payload_json,'$.league') AS league,
    json_extract(payload_json,'$.bookmaker_yes_implied') AS book,
    json_extract(payload_json,'$.kalshi_implied_yes') * 100.0 AS kalshi_true,
    (json_extract(payload_json,'$.bookmaker_yes_implied') -
     json_extract(payload_json,'$.kalshi_implied_yes') * 100.0) * 100.0
      AS signed_div_pp
  FROM audit_event
  WHERE kind = 'kalshi_sports_observed'
    AND ts >= '2026-05-14 21:42:00'
)
SELECT league, COUNT(*) n,
  ROUND(AVG(ABS(signed_div_pp)), 2) mean_abs,
  ROUND(AVG(signed_div_pp), 2) mean_signed,
  ROUND(100.0*SUM(CASE WHEN ABS(signed_div_pp)>=3 THEN 1 ELSE 0 END)/COUNT(*),1) pct_ge3,
  ROUND(100.0*SUM(CASE WHEN ABS(signed_div_pp)>=5 THEN 1 ELSE 0 END)/COUNT(*),1) pct_ge5,
  ROUND(100.0*SUM(CASE WHEN ABS(signed_div_pp)>=10 THEN 1 ELSE 0 END)/COUNT(*),1) pct_ge10
FROM r GROUP BY league ORDER BY league;

-- Per-league median (window-function trick)
WITH r AS (
  SELECT json_extract(payload_json,'$.league') AS league,
    ABS((json_extract(payload_json,'$.bookmaker_yes_implied') -
         json_extract(payload_json,'$.kalshi_implied_yes')*100.0)*100.0) AS abs_div
  FROM audit_event
  WHERE kind = 'kalshi_sports_observed' AND ts >= '2026-05-14 21:42:00'
),
ranked AS (
  SELECT league, abs_div,
    ROW_NUMBER() OVER (PARTITION BY league ORDER BY abs_div) rn,
    COUNT(*) OVER (PARTITION BY league) n
  FROM r
)
SELECT league, n,
  ROUND(AVG(CASE WHEN rn IN (n/2, n/2+1) THEN abs_div END), 2) median
FROM ranked GROUP BY league;
```

Both queries fit under the 4 KB `az run-command` stdout cap.
