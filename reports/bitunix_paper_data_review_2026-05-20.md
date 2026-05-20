# BitUnix paper-data review — since trade-plan v2 flip

**Window:** 2026-05-17 05:14 UTC (Phase 1E flag flip / trade-plan v2 became active) → 2026-05-20 04:13 UTC. ~70 hours of live paper-mode behavior.

**Source:** read-only `sqlite3` against prod `/home/azureuser/trading_corp/data/trading_corp.db` via `az vm run-command invoke`. Query scripts archived at `tmp/bitunix_paper_analysis*.sh`.

**Caveat — small sample.** Only 2 trades fired in 70 hours. Statistical reads on win-rate / R / lifecycle are not yet possible at n=2. The funnel-shape data (signal → score → PA → HTF → trade-plan → fire) is the load-bearing finding.

---

## 1. Top line — the funnel

```
598  webhooks (cypher 488 + otter 110)
        │
        ▼  observer fan-in + redeem-loop re-evaluates
1,767  bitunix_score_decided rows  (598 fresh + 1,169 bar_tick_redeem)
        │  Q1b
        ▼  tier ≥ STANDARD (net_score ≥ 5)
1,378  reach PA gate                       PREMIUM 146 + STANDARD 1,232 — 389 dropped at SKIP
        │  Q2a
        ▼  PA enforce-mode
39     PA passes      (1,339 rejects, 97.2% reject rate)
        │  Q2b — failed validators (overlapping)
        │   volume_confirmation 957
        │   structure_alignment 839
        │   vwap_alignment      734
        ▼
39     HTF gate decisions
        │  Q2e — size_multiplier
        │   0.0 (hard-zero)  28   ← Q2f: proximity_to_support 27, proximity_to_resistance 1
        │   0.5 (half-size)  11
        ▼
11     trade_plan_decision   (Q2h / Q6)
        │   should_trade=0, skip="fees_too_high_for_risk"   9
        │   should_trade=1, sl_method="swing"               1
        │   should_trade=1, sl_method="atr_fallback"        1
        ▼
2      would_have_placed       (both sell, STANDARD)
        ▼
2      paper_trade_record v2-era rows
        ▼
0W / 2L / 0E      win rate 0%   (n=2, uninformative)
```

**Read of the funnel.** Score engine is healthy and prolific. PA gate is heavily restrictive at 97.2% rejection — consistent with the standing memory `feedback_pa_gate_well_calibrated.md` ("100% reject rate = hostile regime, NOT 'gate too strict'"). HTF gate is doing real work: 28/39 hard-blocks were `proximity_to_support` rejections on sell-side signals. Of the 11 score-events that survived score+PA+HTF to reach the trade-plan layer, **9 of 11 (82%)** failed the new v2 fee floor (`fees_too_high_for_risk`). Only 2 trades fired.

---

## 2. 5f / confluence accumulator — side + tier distribution

Side and tier counts since SINCE (`bitunix_score_decided`, Q1d):

| side | tier | n | avg net_score |
|---|---|---:|---:|
| sell | PREMIUM | 146 | 10.86 |
| sell | STANDARD | 1,146 | 6.21 |
| sell | SKIP | 220 | 2.55 |
| buy | STANDARD | 86 | 5.63 |
| buy | SKIP | 120 | 2.00 |
| flat | SKIP | 49 | 0.00 |

**Direction bias is severe.** 88% sell-side: 1,512 sell evaluations vs 206 buy evaluations. **Every PREMIUM in the window was sell-side** (146/146). The "5f" net-score thresholds — premium 10, standard 5, weak 3 — are firing as designed; the question of whether the *signal mix itself* is biased (BTC was trending down, or Otter/Cypher were short-skewed in this window, or the H2 weights tilt the scorer that way) is a follow-up.

### Trigger-source mix (Q1c)
- `bar_tick_redeem` — 1,169 (the 60s redeem-loop re-scoring cached PA-rejected signals)
- `market_cypher` — 488
- `lord_otter` — 110

The deferred-fire mechanism is doing the bulk of the work — 66% of score events are re-evaluations of already-cached signals. That's the design intent (capture deferred-fire redemption), not a bug.

### Sample PREMIUM payload (Q4a-fix)
```json
{
  "tier": "PREMIUM", "side": "sell", "net_score": 10,
  "raw_sell_score": 10, "sell_guard_penalty": 0,
  "sell_contributions": [
    ["mc_a_blood_diamond", 3], ["cvd_bear_flip", 2],
    ["mc_a_redx", 2], ["mc_a_red_diamond", 3]
  ],
  "outcome": "skipped_pa_validation",
  "note": "REJECT: require_all (passed 2/3); failed=['structure_alignment']",
  "htf_size_multiplier": 0.5
}
```

This single payload illustrates the whole funnel:  4 factors stack to 10 → PREMIUM sell → PA rejects on structure_alignment → HTF would have halved the size if it had gotten that far. Net effect: a PREMIUM signal does not fire.

---

## 3. Denials by stage

| Stage | Action | Count (since v2 flip) | Notes |
|---|---|---:|---|
| Score | SKIP (net < 5) | 389 | sell 220 / buy 120 / flat 49 |
| PA enforce | reject | 1,339 | 97.2% of PA evals |
| HTF | hard-zero | 28 | proximity_to_support 27 / proximity_to_resistance 1 |
| HTF | half-size | 11 | still fire, just smaller |
| Trade-plan v2 | fees_too_high_for_risk | 9 | TP2 distance < fee floor |
| Trade-plan v2 | fire (sl=swing) | 1 | 2026-05-18 16:24 UTC |
| Trade-plan v2 | fire (sl=atr_fallback) | 1 | 2026-05-18 18:30 UTC |

### Deferred-fire (PA redemption)
- `pa_validation_redeem`: 18  (cached signals that eventually passed PA on a later bar)
- `pa_validation_expired`: 58 (all `score_decay` — none for opposite-side wins)

So of 1,339 PA-rejected signals, 18 were redeemed later and 58 explicitly expired by score-decay. Most of the 1,339 likely also decayed without an explicit expired-row (the cache clears silently on score SKIP per memory).

---

## 4. Trades fired + outcome

**v2-era only** (`extra_json.tp_plan_version='v2'`, since 2026-05-18 16:24 UTC):

| ts (UTC) | side | tier | sl_method | result |
|---|---|---|---|---|
| 2026-05-18 16:24:02 | sell | STANDARD | swing | **loss** |
| 2026-05-18 18:30:05 | sell | STANDARD | atr_fallback | **loss** |

**Lifecycle reconciler activity:** `position_sl_update` count since v2 flip = **0**. Neither trade reached TP1, so the SL-step-to-BE → TP1-floor → trail lifecycle never advanced. Reconciler ran, found no work, idled — consistent with `decide_sl_action()` being correctly idempotent.

**All-time `paper_trade_record` (Q3b'', for context):** 46 wins / 20 losses / 3 expired = 67% WR (n=69). But this is dominated by **legacy `_build_proposal` geometric trades from 2026-05-11 to 2026-05-16** (single-leg 2R TP, pre-v2). The v2 cutover happened 2026-05-17 05:14 UTC; v2-era trades = 2; non-v2 since SINCE = 0 (the legacy path is gone). The 2 v2 trades are not statistically separable from the all-time legacy record yet — they're a different placement design.

---

## 5. HTF gate

39 HTF gate evaluations:
- **Regime distribution (Q2g):** NEUTRAL × 39. Every gate eval in this 3-day window happened in NEUTRAL regime. No TRENDING_UP / TRENDING_DOWN / CHOP triggered in payloads — the classifier reads BTC as range-bound for the window.
- **size_multiplier:** 0.0 × 28, 0.5 × 11. No full-size (1.0) passes. The 11 half-size events are the only path that could have led to a fire under HTF — and 9 of those 11 then died at the trade-plan fee floor.
- **Hard-zero reasons:** `proximity_to_support` × 27, `proximity_to_resistance` × 1.

**Read:** in NEUTRAL regime on the recent 17-day window, the HTF gate is hard-zeroing 72% of post-PA-pass signals, half-sizing the remaining 28%. Consistent with "don't sell into established support" being the dominant intervention. This is doing the work it was designed to do.

---

## 6. PA gate calibration check

Q2b failed validators (one PA reject can fail multiple):
- volume_confirmation: 957
- structure_alignment: 839
- vwap_alignment: 734

These three failures co-occur frequently. The PA gate's `require_all` semantic means any one failure rejects; the failed-list ordering shows volume is the most frequent solo or co-cause. No `rush/fall` hard-rejects in the window (Q2a only lists `reject` and `pass`, no rush_fall_triggered counts emitted).

Standing rule from `feedback_pa_gate_well_calibrated.md`: a high reject rate in hostile regime is calibrated, not over-tight. NEUTRAL × all 39 HTF passes corroborates that this is the regime where PA rejection is expected. **Don't loosen PA based on rejection rate alone.**

---

## 7. What's unanswerable from this data

- **Whether the two v2 losses were placement-quality issues** (swing vs atr_fallback SL choice, fee floor too aggressive elsewhere) **vs random-walk bad luck on n=2.** Both losses fired in the same 2-hour bracket on 2026-05-18 — possible regime correlation; the payloads have entry/stop/TP details I haven't pulled.
- **Whether `fees_too_high_for_risk` is over-firing.** 82% of post-HTF signals skip on fee floor. The floor formula is `max(0.5R, 2.0 × round_trip_fee_cost_pct × entry_price)`. If R is small (stop close to entry, structure-preferred SLs near recent swings), the absolute-dollar fee floor blows up relative to R. Worth pulling per-skip fee_floor_pct + TP2 distance to confirm whether the calibration is right.
- **Whether the 88% sell-side skew is regime-driven (BTC trending down) or signal-source biased.** Need an OHLCV overlay against the score time series.

---

## 8. Honest assessment

**The infrastructure works.** Audit chain is intact end-to-end. Score engine, PA gate, HTF gate, trade-plan v2, paper_trade_record, reconciler — every layer is producing the audits the dashboard surfaces. No silent failures.

**The fire rate is very low.** 2 fires in 70 hours = ~0.7 fires/day. The original v3-report Phase C acceptance bar was 5% fire rate of webhook-arriving signals; we're at 2/598 = 0.3% of webhooks → fires. The gate stack is doing what it was designed to do (reject more aggressively in NEUTRAL regime; respect fee floors), but the throughput is below where shadow data can answer the v1.1 paper-cutover question on the `[1.14, 2.63]` PF prior in any reasonable timeframe.

**v2 record is 0/2 — uninformative.** Don't draw any conclusion from it. The 60-day paper-cutover window remains the gate per `trading_corp_bitunix_vision.md`. At current fire rate, 60 days yields ~42 trades — still below the n≥30 minimum for tight WR confidence intervals, but borderline-usable.

**Two open questions worth a follow-up (separate task, not in this report):**
1. Is the fee floor (`tp1_min_profit_multiplier: 2.0`) calibrated for this venue and tier sizing? 82% post-HTF skip on fees is high. A per-skip detail pull would show whether the floor is binding on legitimate trades or correctly killing micro-edge ones.
2. Is the 88% sell-side skew regime-correlated or signal-source-biased? If H2 weight re-tune (2026-05-16) up-weighted the wrong family for current regime, that's relevant. Look at Otter+Cypher webhook side-distribution to disambiguate.

Neither question is in-scope for this review; both are worth raising before the next session ends.
