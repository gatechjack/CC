# kalshi_weather hourly re-evaluation — replay design + data-availability report

**Status:** investigation-first design (per `BACKLOG.md` P2 — kalshi_weather intraday work, Item 2). **No replay code yet.** Gated on operator review.

**Source-of-truth note:** this doc captures the design and the honest data-availability picture. Decisions surfaced at the bottom are operator's call — none are pre-resolved here.

---

## 1. Context

Operator picked Item 2 (hourly re-evaluation of open kalshi_weather positions using intraday data) over Item 1 (settlement-certainty arbitrage). The physical reason for edge in Item 2 is clean — weather forecast accuracy converges hard as the day progresses — while Item 1 likely dies on fillable-size against sophisticated counter-parties.

The BACKLOG entry specifies: **investigation-first; build as a logging/signal layer eventually; before any code touches positions, historical replay must show positive EV net of costs.** This doc is the design of that replay. The replay's acceptance gate is the door to any production work.

---

## 2. Data availability (corrected from initial pass)

Four data streams are needed for the replay. Verdicts below are direct prod observations, not speculation.

### Stream 1 — Resolved round-trips (entry decision + settlement outcome)

**Verdict: AVAILABLE NOW.** Replay corpus is ready today.

| Source | Count | Date range |
|---|---|---|
| `audit_event` kind=`would_have_placed`, actor=`kalshi_weather_arb` | **636** | 2026-05-15 → 2026-05-22 |
| `audit_event` kind=`kalshi_weather_evaluated` | 54,966 | same |
| `audit_event` kind=`kalshi_weather_scan` | 2,125 | same |
| `kalshi_round_trips` (resolved) | **556** | 2026-05-15 → 2026-05-21 |
| `proposed_order` rows for `kalshi_weather_arb` | 658 | same |

- ~85 paper proposals per day across a 7-day window. ~80 unresolved (recent/in-flight); replay against the 556 resolved set.
- Each `would_have_placed` row carries the entry-time decision in full (forecast inputs, METAR snapshot, prob_yes, ticker, side, qty, entry ask).
- `kalshi_round_trips` carries `market_result`, `won` (0/1), `realized_pnl`, `roi_pct`, `resolved_ts`. Join on `order_id`.
- Paper-mode → no real fills → entry "price" is the recorded limit_price ask, not an executed fill. Replay treats entry as model-perfect at that ask. This is a known degradation vs. live-mode data.

Code refs: `trading_corp/agents/strategies/kalshi_weather_arb.py:686-722` (eval_payload), `:827-874` (would_have_placed extras), `trading_corp/persistence/db.py:232-267` (kalshi_round_trips schema), `trading_corp/agents/kalshi_resolver.py` (already populating round-trips on prod).

### Stream 2 — Intraday Kalshi market quotes (bid/ask between entry and settlement)

**Verdict: MISSING — neither stored nor backfillable. Make-or-break input.**

- Quotes are pulled at decision time only (`kalshi_quote_dollars` in `trading_corp/agents/strategies/_weather_math.py:42-65`, called at `kalshi_weather_arb.py:378-390`).
- The four prices (`yes_bid`, `yes_ask`, `no_bid`, `no_ask`) at entry are captured in the `would_have_placed` payload — single snapshot only.
- **No `quote_snapshot` / `market_quote_history` table exists.** Confirmed.
- Kalshi public API exposes current quotes only; no historical-quote archive. `KalshiBroker.get_market_trades()` returns recent trade tape but not full bid/ask history.

**Consequence:** the replay cannot compute "what would the actual fill have been at hour H?" The exit-price question requires either a model-based fair-value assumption (Tiers A/B below) or 30+ days of waiting after building quote persistence (Tier C below).

**Forward fix (not part of this design, but worth scheduling in parallel):** add a `quote_snapshot(ts, ticker, yes_bid, yes_ask, no_bid, no_ask)` table + hourly cron snapshotting open kalshi_weather market quotes. Cheap; pays back only after a month of accumulation.

### Stream 3 — Intraday METAR/ASOS station readings

**Verdict: BACKFILLABLE from NWS Aviation Weather API.**

- Live path: `MetarClient.get_nowcast()` (`trading_corp/data/metar_client.py:80-144`) fetches recent METAR, holds in 5-min in-memory cache, discards.
- The METAR-blended temperature used at decision time is stored in audit (`metar_latest_temp_f`, `metar_extrap_f`, `nowcast_blend_w`, `metar_station`) — entry-time only.
- Backfill: NWS `/api/data/metar?ids=<station>&hours=48` returns hourly observations for any US station for the historical window. Free, no auth. Stations are stable retroactively.
- For each of the 556 resolved trades: pull METAR series for `metar_station` from `entry_ts - 1h` through `target_iso`. Reuse `metar_client._parse_metar` to keep parsing identical to live.

### Stream 4 — NWS / NBM / Open-Meteo forecast updates (mid-position)

**Verdict: PARTIAL — entry-time snapshot in audit, mid-position re-issue gated on Open-Meteo historical-forecast-api capability.**

- Entry-time forecast snapshot is in `would_have_placed` and `kalshi_weather_evaluated` payloads (`forecast_temp_f`, `forecast_sigma_f`, `sigma_source`, `ensemble_n_members`, `ensemble_std_f`).
- NWS gridded-forecast API does NOT archive forecasts by issue time — you cannot retrieve "what was the NWS forecast issued at 2026-05-15T18:00Z for 2026-05-15T23:59Z."
- **Open-Meteo runs a separate `historical-forecast-api.open-meteo.com/v1/forecast` endpoint that DOES return historical forecast issues by date going back years.** This needs a 15-minute probe to confirm against one known past date+coord pair before designing for it. If it works, mid-position forecast is fully backfillable. If it doesn't, the replay degrades to "what did the updated METAR observation alone change in the signal?" — weaker but still informative.

Code refs: `trading_corp/data/weather_forecast.py:42-235` (NWS), `trading_corp/data/open_meteo_client.py:71-150` (Open-Meteo current-forecast — model the historical client on this).

### Summary table

| Stream | Stored on disk? | Backfillable? | Verdict | Blocker? |
|---|---|---|---|---|
| 1. Resolved round-trips | Yes (636 proposals + 556 resolutions) | n/a | **AVAILABLE NOW** | No |
| 2. Intraday Kalshi quotes | No | No (Kalshi has no archive) | **MISSING** | **Kills full-fidelity exit PnL** |
| 3. Intraday METAR | Entry-time only | Yes (NWS METAR archive) | **BACKFILLABLE** | No |
| 4. Mid-position forecast | Entry-time only | Probably yes via Open-Meteo historical-forecast-api; verify before commit | **PROBABLY-BACKFILLABLE — verify** | Resolved by 15-min probe |

---

## 3. Audit-event payload reference (for replay joins)

`kalshi_weather_evaluated` (per-market evaluation, 54,966 rows):
```
strategy, division, ticker, title, category,
lat, lon, target_iso,
coord_source, yaml_coords, legacy_coords,
horizon_hours, threshold_f, threshold_high_f, direction,
forecast_temp_f, forecast_sigma_f, sigma_used_f, sigma_source,
ensemble_n_members, ensemble_std_f,
nowcast_blend_w, metar_station, metar_latest_temp_f, metar_extrap_f,
delta_f, implied_yes, prob_yes, edge_pct, divergence_pct,
fired, skip_reason, forecast_source
```

`would_have_placed` extras (per fired position, 636 rows; on top of order fields):
```
outcome, implied_prob_at_entry, forecast_temp_f, forecast_sigma_f,
sigma_used_f, sigma_source, ensemble_n_members, ensemble_std_f,
nowcast_blend_w, metar_station, metar_latest_temp_f, metar_extrap_f,
threshold_f, threshold_high_f, direction, horizon_hours, delta_f,
prob_yes, divergence_pct, expires_at, target_iso,
max_dollar_risk, kelly_fraction_used, kelly_full_pct, applied_cap,
account_equity_at_size, tier, source_signal, bucket_guard
```

Join: `kalshi_round_trips.order_id = audit_event.payload_json.order_id` (extracted via `json_extract`).

---

## 4. Replay design

The replay asks, for each of the 556 resolved positions, at each hour `H` between `entry_ts` and `target_iso`: **what would the signal have said (HOLD / CLOSE / ADD / NEW), and would acting on it have improved net P&L over holding to settlement, after spread + fees?**

### 4.1 Per-position loop

For position `P` with `entry_ts`, `target_iso`, `lat/lon`, `metar_station`, `threshold_f`, `direction` (high/low), `outcome` (yes/no), `qty`, `entry_price`, `realized_pnl`:

1. **Hour grid.** `H ∈ {entry_ts+1h, entry_ts+2h, …, target_iso−1h}`. Snap to actual METAR observation times rather than idealized clock hours (METAR stations report once/hour, sometimes more via SPECI; using clock-hour times when no obs exists produces fictional "extrapolated" temps). Typically 5–17 hourly checks per position depending on horizon.
2. **At each H, reconstruct three inputs:**
   - **METAR-latest:** most recent METAR observation at `metar_station` with `obsTime ≤ H`. From NWS backfill.
   - **METAR-extrapolated:** the H-hour observation passed through the same extrapolation function the live strategy uses. **Reuse `_weather_math.py` extrapolation — don't reimplement.**
   - **Forecast at H:** Open-Meteo historical-forecast-api result for `(lat, lon, target_iso)` issued at `H`. Gated on the §2.4 verification probe. If the API doesn't yield forecast-by-issue-time, fall back to entry-time forecast aged toward observed METAR — flag this in the output as a weaker signal.
3. **Recompute signal.** Call the existing strategy's probability logic with the H-hour inputs. Reuse `_weather_math.prob_yes_for_threshold` + the entry-edge thresholds from `kalshi_weather_arb.py`. **Do not reimplement the math.** Produce `prob_yes_at_H`, `divergence_at_H`, and the discrete signal `{HOLD, CLOSE, ADD, NEW}`.
4. **Score against settlement.**
   - **CLOSE** decision: was the actual settlement adverse to the position (in which case closing at H would have avoided loss) or favorable (in which case closing forfeited profit)?
   - **ADD / NEW** decision: did the actual settlement favor the new size? (Counterfactual additional exposure scored against same outcome the original position resolved at.)
   - **HOLD** decision: implicit success if `realized_pnl > 0`; failure if not.

### 4.2 Three PnL-modeling tiers

Without intraday quote data, exit-price simulation needs assumptions. Three tiers, increasing fidelity:

| Tier | Exit price model | What it claims | Cost |
|---|---|---|---|
| **A — Decision-quality** | No exit-price model. Score "did the signal change in the correct direction relative to settlement?" via match-with-settlement. | "Intraday data changes the signal X% of the time, and the new signal would have been correct Y% of the time." | Free. Existing audit data + METAR backfill + Open-Meteo historical (if §4 probe succeeds). |
| **B — Model-based PnL** | Exit at H modeled as `prob_yes_at_H` (treat updated fair value as exit mid). Subtract a calibrated spread cost (see §5). Subtract Kalshi fee schedule. | "Assuming we could close at fair value minus spread + fees, acting on the signal would have improved net PnL by $X over Y trades." | Free if Tier A runs — Tier B is an extra column in the output. |
| **C — Real-data PnL** | Wait. Build `quote_snapshot` table + hourly cron now; accumulate 30+ days; replay against real bid/ask. | Same as B but with real fill cost rather than 2¢ assumption. | 30+ days of waiting + small persistence work. |

**Recommendation:** **start Tier A + B in parallel** (same data pipeline, B adds one cost column); **start `quote_snapshot` persistence simultaneously** so Tier C is ready when data accumulates. If A+B show no signal, Tier C is moot.

### 4.3 What the replay must address (per BACKLOG spec)

- **`max_per_day_pct` interaction.** When the replay's signal says ADD or NEW at hour H, check the historical daily-exposure state at H against `max_per_day_pct` from `config/strategies.yaml`. Counterfactual adds that would have blown the cap don't count — drop them or flag them as "would_exceed_cap=true" and exclude from Tier B aggregates.
- **"Add-to-winner" sizing-discipline risk.** Track separately: PnL from CLOSE-on-loser decisions vs PnL from ADD-to-winner decisions. The former is risk reduction; the latter is leverage and degrades sizing discipline. Report as separate columns; do not aggregate. Operator decides whether ADD is allowed before any implementation step beyond the replay.

### 4.4 Replay output schema (one CSV row per (position, hour) check)

```
order_id, ticker, entry_ts, target_iso, hour_H,
metar_latest_at_H, metar_extrap_at_H,
forecast_at_H, forecast_source_at_H,   -- 'open_meteo_historical' or 'aged_entry' if probe fails
prob_yes_at_entry, prob_yes_at_H, divergence_at_H,
signal_at_H,                            -- {HOLD, CLOSE, ADD, NEW}
realized_pnl_actual,                    -- from kalshi_round_trips
tier_a_correct,                         -- decision matched settlement direction (bool)
tier_b_pnl_if_acted,                    -- modeled PnL of acting at H (B model)
tier_b_pnl_delta_vs_hold,               -- improvement over holding to settlement
exceeds_max_per_day_pct_at_H,           -- bool, ADD/NEW only
is_add_to_winner                        -- bool, ADD/NEW when position already winning
```

Aggregate report: per signal type, % correct, mean PnL delta, distribution of PnL delta (5/25/50/75/95 percentiles), count of trades where signal changed at all, count of trades where mid-position forecast was unavailable (fell back to METAR-only).

---

## 5. Cost model (explicit, per operator's "be explicit" instruction)

Tier B's whole point is to make the replay's PnL number defensible. Underspecified cost models flatter the signal. Specifications:

- **Spread cost on closes.** Assume the strategy CROSSES the spread on close — sells YES into the bid (loses ½ spread), or buys-back YES at the ask (loses ½ spread). Total round-trip cost = full spread, not half.
- **Spread magnitude.** Calibrate empirically from the 636 entry snapshots. Sample `yes_ask - yes_bid` and `no_ask - no_bid` distributions; default Tier B uses median entry spread per ticker series (KXHIGH*, KXLOW*, KXTEMP*) since spreads differ by category. **Don't use a flat 2¢ assumption — measure it.**
- **Realistic fill, not mid.** When the replay simulates an exit at H, exit price = the ask (or bid, depending on side) — not the mid. The mid is what fair-value pricing produces; realistic execution loses the half-spread to the counterparty.
- **Kalshi fee schedule.** Per the Kalshi fee table (current as of the trading session): trading fees are charged per-contract on EITHER side that crosses; verify against the `KalshiBroker` adapter or `brokers/kalshi.py` constants before any Tier B claim. This is verification item §6.3.
- **Slippage on ADD/NEW.** Adding mid-position crosses the spread the same way closing does — full spread cost; not half. No "free" entries.
- **Survival test.** Headline Tier B PnL only "exists" if it survives full spread + fees on every counterfactual close/add. If the signal's edge is smaller than the spread, it's not real edge.

---

## 6. Anomalies / verifications required before kickoff

Each affects replay execution but not the design itself. Resolve before running the replay.

1. **Open-Meteo historical-forecast-api** (Stream 4). Hit `historical-forecast-api.open-meteo.com/v1/forecast` for one known past date+coord pair (e.g., one of the 556 trades; ask for the forecast issued at `entry_ts` and compare to the audit-recorded `forecast_temp_f`). ≤15 min. Outcome:
   - ✅ Returns sensible forecast → mid-position forecast is fully backfillable; design proceeds as written.
   - ❌ Returns nothing useful → replay degrades to METAR-only signal updates; flag in output as `forecast_source_at_H='aged_entry'`.
2. **Spread calibration** (cost model §5). Sample `yes_ask - yes_bid` and `no_ask - no_bid` across the 636 entry snapshots, segmented by series prefix (KXHIGH/KXLOW/KXTEMP*) and by hour-of-day. Pick a defensible median per segment. ≤30 min.
3. **Kalshi fee schedule sanity check.** Confirm current fee structure (per-contract flat or %-of-contract) before adding to Tier B. Look in `brokers/kalshi.py` constants and/or `config/risk.yaml`. ≤15 min.
4. **METAR coverage during settlement window.** METAR observations are ~hourly but sometimes interspersed with SPECI reports during weather changes. Snap the H-grid to actual observation times per station, not idealized clock hours. ≤30 min during backfill design.

None of these blocks the design itself. They shape execution.

---

## 7. Files involved (read-only references for the replay)

The replay reuses live-strategy functions to keep math identical:

- `trading_corp/agents/strategies/kalshi_weather_arb.py` — `_resolve_coords`, signal-construction logic, audit shapes.
- `trading_corp/agents/strategies/_weather_math.py` — `prob_yes_for_threshold`, `kalshi_quote_dollars`, extrapolation. **Replay imports from this; does not reimplement.**
- `trading_corp/data/metar_client.py` — `_parse_metar`, `MetarObservation` dataclass. Backfill reuses parser.
- `trading_corp/data/weather_forecast.py` — NWS gridded-forecast shape (for entry-time forecast joining).
- `trading_corp/data/open_meteo_client.py` — model the historical-forecast-api client on the existing live client.
- `trading_corp/persistence/db.py:232-267` — `kalshi_round_trips` schema.
- `trading_corp/agents/kalshi_resolver.py` — already populating round-trips on prod.

**Eventual replay artifact (NOT writing yet — gated on operator go):** `scripts/replay_kalshi_weather_hourly_reeval.py` — standalone script, queries prod (or a local snapshot of audit_event + kalshi_round_trips), writes CSV to `tmp/`, prints summary. No side effects on prod or live strategy.

---

## 8. Verification of the replay (end-to-end)

Once the replay script exists (after operator go), validate it:

1. **Spot-check 3 trades by hand.** Pick one clean winner, one clean loser, one close call. Run the replay against just those 3 and verify the H-hour signals look sensible against the audit-event timeline.
2. **Round-trip parity at H=entry_ts+0min.** The replay's `prob_yes_at_H` at exactly the entry-time hour should equal the audit-recorded `prob_yes`. Any off-by-anything is a bug.
3. **Coverage.** One row per (position, hour) for all 556 resolved positions; ~5–17 rows per position; total ~5000-7000 rows. Missing rows = backfill gap.
4. **Bias check.** Distribution of `tier_a_correct` should be neither ~100% nor ~0%. 100% suggests data leak from future state (e.g., METAR-extrap inadvertently using post-H data); 0% suggests signal-direction is inverted.
5. **Headline.** Print one summary: Tier A % correct, Tier B mean PnL delta with confidence interval (bootstrap is fine), count of (position, hour) pairs where signal changed.

---

## 9. Out of scope (explicit)

- No replay code in this design's scope. Next step is operator review + direction.
- No quote-persistence (`quote_snapshot` table + cron) — separate forward-fix, schedule independently; design recommends doing it in parallel with the replay regardless of replay outcome.
- No changes to `kalshi_weather_arb.py`. Strategy stays untouched until the replay's acceptance gate is met.
- No production touches beyond read-only SQL probes (same posture as the daily drift-check).
- Item 1 (settlement-certainty arb) not being worked. BACKLOG entry stands for later if Item 2 produces a clear signal first.

---

## 10. Open operator decisions

These shape the replay's first 1–2 hours of execution. Replay execution would pause at each.

1. **Tier A + Tier B in parallel, or Tier A only?** Plan recommends both (free if A runs).
2. **Probe Open-Meteo historical-forecast-api first, or commit to Tier A only?** Plan recommends probe (15 min cost, big information value).
3. **Run replay against a SQL dump of prod or against a read-replica connection?** Dump avoids network noise and is reproducible; connection is faster to iterate on. Plan recommends dump → `tmp/audit_kalshi_weather_*.sqlite` for development; ad-hoc prod queries for spot-checks.
4. **Start `quote_snapshot` persistence in parallel with the replay, or wait for replay results first?** Plan recommends parallel (cheap; useful regardless of outcome). Operator may want to wait until Tier A+B show enough signal to justify the operational footprint.

---

## Cross-references

- `BACKLOG.md` — "P2 — kalshi_weather intraday work (2026-05-22, re-added after EOS loss)" Item 2. This doc is the design for that item.
- `runbooks/deploy_log.md` 2026-05-22 16:25 UTC entry (P3 YAML loader) — kalshi_weather is currently on `f5a5fd5` in paper-mode + observation week through ~2026-05-29.
- `scripts/check_weather_coord_drift.sql` — runs daily during the observation week; this design assumes weather xref P4 has NOT advanced (and per the standing rule it must not advance from a single clean day).
- Linked memory: `[[feedback-session-committed-phantom-pointer]]` — this design doc is committed precisely so it doesn't suffer the same fate as the original Item 1 + 2 BACKLOG entries.
