# kalshi_weather — 24h Post-Xref Autopsy (2026-05-24)

**Scope:** First ~24h of resolved paper round-trips after the YAML
station-xref logic shipped on 2026-05-22T16:25 UTC (commit f5a5fd5).
Read-only forensic; no fixes proposed or applied.

**Headline verdict:** **No logic defect found.** The −$28.15 net under
the new logic is inside variance for n=75 / WR 70.7%. One soft signal
worth tracking across the observation week: σ_used_f tail frequency is
elevated relative to a standard normal, hinting that the model σ is
under-estimated — but the sample is too small and too single-day-clustered
to call.

---

## 1. Sample scope — and the carryover finding

Initial query filtered by `resolved_ts >= '2026-05-22T16:25'`:

| metric | value |
|---|---|
| n | 153 |
| wins | 105 |
| losses | 48 |
| voids | 0 |
| **net PnL** | **−$81.40** |
| time range | 2026-05-23T11:11 → 2026-05-24T12:16 |

**However** — 26 of those 48 "post-deploy" losses had `entry_ts` BEFORE
2026-05-22T16:25 (i.e., entered under the old logic with the
KJFK/KORD/KIAH coords) and simply settled into the window. Filtering by
`entry_ts >= '2026-05-22T16:25'` gives the true post-deploy sample:

| metric | value |
|---|---|
| **n** | **75** |
| wins | 53 |
| losses | 22 |
| voids | 0 |
| **net PnL** | **−$28.15** |
| WR | 70.7% |
| entry range | 2026-05-23T00:06 → 2026-05-24T12:16 |

**≈$53 of the headline −$81 is pre-deploy carryover, not new-logic
performance.** Future post-deploy P/L reads should filter on entry_ts,
not resolved_ts.

n=75 is too small for any rate-based edge or defect conclusion —
variance dominates. The remainder of this report is per-trade defect
hunting, not edge judging.

---

## 2. Defect-class scan — all clean

Rubric: five defect classes that would invalidate the new-logic
period. Findings on the 22 true-post-deploy losses:

| class | count | notes |
|---|---|---|
| STATION_MISMATCH | 0 | every losing trade's coords matched the xref's `settles_at` station |
| COORD_SOURCE_ANOMALY | 0 | all 8 trades on the 6 corrected cities show `coord_source=yaml_verified` (KXHIGHCHI×4, KXHIGHNY×2, KXHIGHTHOU×2; no KXLOWTNYC/KXLOWTCHI/KXLOWTHOU traffic in window) |
| DISABLED_SERIES_LEAK | 0 | KXTEMPNYCH fully suppressed (0 RTs, 0 evaluated rows) |
| FLOOR_OR_SIZING_ANOMALY | 0 | entry prices $0.50–$0.89 NO, within configured floors |
| FORECAST_WRONG (systematic bias) | 0 | loss-side mean Δ_f = +0.114°F, well within historical 1σ |

All 22 losses classify as **TAIL_LOSS** by structure: model placed
forecast outside the narrow `between` range with low prob_yes (mean
0.093), actual landed inside the range anyway.

---

## 3. Anomaly #1 — bet-shape concentration

Pulled the 53 true-post-deploy wins to check whether the book is varied
or single-pattern.

**Outcome × direction across ALL 75 RTs:**

| bet shape | wins | total | losses |
|---|---|---|---|
| NO × between | 46 | 67 | 21 |
| NO × less | 4 | 5 | 1 |
| NO × greater | 3 | 3 | 0 |
| YES × anything | 0 | 0 | 0 |

**100% of trades are NO bets. 87% are NO-on-`between`.** No YES bets in
the entire sample.

Geographic dispersion is fine — 31 distinct series across 18 physical
stations (KATL, KAUS, KBOS, KDCA, KDEN, KDFW, KHOU, KLAX, KMDW, KMIA,
KMSP, KMSY, KNYC, KOKC, KPHL, KSAT, KSEA, KSFO). But the bet shape is
one-dimensional: **the strategy is making a single directional claim —
"the market is overpricing the NO side of tight weather bins."**

Implication for variance: with 100% NO concentration and many cities
sharing correlated regional weather, a synoptic-scale event (e.g.,
day-23's cold outbreak) can drive synchronized losses across many
tickets. The 22 losses today are not 22 independent samples — they are
fewer underlying weather events repeated across multiple tickers per
station × date.

---

## 4. Anomaly #2 — σ calibration (directional signal, NOT a verdict)

Pulled real settlement temperatures for all 75 RTs via Open-Meteo
archive at the YAML settle-station lat/lon and computed
`z = (actual_temp − forecast_temp_f) / σ_used_f`.

**Source caveat:** Open-Meteo is gridded reanalysis, not the official
NWS CLI Daily Climate Report that Kalshi settles against. Z-scores here
approximate real forecast error but are not guaranteed to replicate
authoritative settlements. For an observation-week formal calibration,
NWS CLI scrape would be required.

### Empirical |z| band frequencies vs. standard normal

| band | empirical | theoretical | ratio |
|---|---|---|---|
| \|z\| < 1 | 69.3% (52/75) | 68.3% | 1.01× |
| 1 ≤ \|z\| < 2 | 14.7% (11/75) | 27.2% | **0.54×** |
| 2 ≤ \|z\| < 3 | 13.3% (10/75) | 4.3% | **3.1×** |
| \|z\| ≥ 3 | 2.7% (2/75) | 0.27% | **10×** |

Summary statistics across all 75 RTs:
- **mean z = −0.422** (actuals ran cooler than forecasts — single-day
  weather event, not necessarily a model bias)
- **stdev z = 1.168** (realized dispersion ≈ 17% wider than σ_used)

The center is fine (|z|<1 hits its theoretical share). The shape is
**hollowed-out middle, fatter tails** — depleted 1–2σ, inflated 2–3σ
and 3σ+. That pattern is consistent with σ_used being too small.

### Per-row |z| ≥ 2 table

| ticker | forecast | σ_used | actual | z | won |
|---|---|---|---|---|---|
| KXHIGHTMIN-26MAY23-B69.5 | 72.0 | 2.63 | 63.7 | **−3.16** | Y |
| KXHIGHTMIN-26MAY23-T69   | 72.0 | 2.76 | 63.7 | **−3.01** | N |
| KXLOWTSATX-26MAY23-B69.5 | 72.0 | 2.10 | 65.8 | **−2.95** | Y |
| KXLOWTSATX-26MAY23-T65   | 72.0 | 2.14 | 65.8 | **−2.90** | Y |
| KXLOWTSATX-26MAY23-B65.5 | 72.0 | 2.14 | 65.8 | **−2.90** | N |
| KXLOWTSATX-26MAY23-B67.5 | 72.0 | 2.14 | 65.8 | **−2.90** | Y |
| KXHIGHTSEA-26MAY23-B72.5 | 71.0 | 2.57 | 77.4 | **+2.49** | Y |
| KXLOWTAUS-26MAY23-T65    | 71.0 | 2.42 | 66.1 | **−2.03** | Y |
| KXLOWTAUS-26MAY23-B67.5  | 71.0 | 2.42 | 66.1 | **−2.03** | Y |
| KXLOWTAUS-26MAY23-B69.5  | 71.0 | 2.42 | 66.1 | **−2.03** | Y |
| KXLOWTAUS-26MAY23-B65.5  | 71.0 | 2.42 | 66.1 | **−2.03** | N |
| KXHIGHTSEA-26MAY23-B70.5 | 72.0 | 2.67 | 77.4 | **+2.02** | Y |

### Important: the 12 tail rows collapse to 5 distinct events

The 12 |z|≥2 rows come from only **5 unique (station, date) pairs**:
KMSP, KSAT (cold), KAUS (cool), KSEA (warm), and a KHOU near-miss.
Multiple tickers per station × date inflate the row count without
adding independent observations. Independent-event tail count is more
like 5/75 distinct, not 12/75. Cuts the apparent fat-tail multiplier
substantially — but doesn't eliminate it.

### Directional read

σ_used **looks under-estimated** for this 24h sample, but with two
strong caveats:

1. **Single day, single synoptic event.** The 2026-05-23 Midwest/Texas
   cold push drove most of the tail. One weather event over a NO-heavy
   book inflates both the tail count and the mean-z bias.
2. **Ticker × station clustering.** 12 high-|z| rows = 5 independent
   events. Don't multiply the σ.

**What to watch:** if KMSP, KSAT, KAUS, KSEA — or any 2σ+ station —
shows |z| > 2 again on independent settle dates across the rest of the
observation week (through ~2026-05-29), the under-estimation finding
survives and σ_used_f deserves recalibration. If they don't, this was
one cold day plus a NO-concentrated book.

---

## 5. What was NOT done (intentional scope limits)

- **No fixes.** Read-only forensic only. Board decides.
- **No NWS CLI scrape.** Z-scores use Open-Meteo reanalysis as a proxy
  for authoritative settlement temps. Fine for directional signal; not
  fine for a formal calibration verdict. Run the CLI scrape if/when
  the observation-week pattern persists.
- **No bucket_guard deep-dive.** All 22 losses had bucket_guard null;
  no flips. Did not audit whether bucket_guard should have fired and
  didn't.
- **No correlated-loss sizing audit.** With 100% NO and synoptic
  weather, the "n=22 independent" framing is wrong, but quantifying
  the effective independent-sample count is out of scope here.

---

## 6. Recommendation

Continue the observation week. Re-run this autopsy at end of week
(~2026-05-29) with:

- entry_ts-filtered sample (avoid carryover contamination)
- NWS CLI ground-truth temps (not Open-Meteo) for σ calibration
- Per-station independent-event count for the tail multiplier
- A second look at whether the strategy should be running YES bets at
  all, or whether NO-only is structurally correct given the bet-shape
  concentration finding

Do not advance P4 on this day's data alone.

---

## 7. Connected hypothesis — observation-week watch (#1 + #2 together)

The two anomalies from §3 and §4 are not independent loose ends. Read
together they describe one structural posture:

- **#1 (book is 100% NO, 87% NO-on-`between`)** — the strategy is
  selling option-like payouts that win in the modal "actual lands
  outside the narrow window" case and lose in the tail. **Short vol.**
- **#2 (σ_used under-estimated; |z|≥2 at 3.1× theoretical, ≥3 at
  10×)** — the model is **underpricing the tails it sells.**

Short-vol + underpriced-tails is the textbook way a strategy looks
profitable on most days and gives back disproportionately on rare ones.
The 70.7% WR on a 24h window is consistent with this; the question is
whether the realized tail draws are wider/more frequent than the model
expects, which is exactly what #2 is asking.

**Watch through 2026-05-29 (end of observation week):** do KMSP, KSAT,
KAUS, KSEA — or any other station — repeat |z| > 2 on *independent
settle dates*? Independent = different days, not multiple tickers on
the same (station, date). If YES → σ_used is genuinely under-estimated
and the strategy's short-vol posture is materially underpriced. If NO →
this day was a single weather event over a NO-heavy book.

**Connected queued work — moves from "speculative" to "justified" if
the above repeats:** BACKLOG already carries an **Empirical σ-scaling
factor** item (see BACKLOG.md `## P2 Empirical σ-scaling` / NBM-σ
calibration work). That work would: (a) replace the heuristic
`sigma_for_horizon(h)` in `_weather_math.py` with an NBM-derived
forecast σ that scales correctly with horizon and station, (b)
back-test the resulting σ against the same pre-fix RT corpus, (c) ship
a fix that widens tails on the bet-side selection. **Addresses both #1
and #2 together** — wider σ tightens the no-edge gate, suppresses
marginal NO bets, and the book diversifies on direction as fewer
tight-`between` markets clear the divergence threshold.

Do not start that work on this day's data. Re-evaluate at end of
observation week.
