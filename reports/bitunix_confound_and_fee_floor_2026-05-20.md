# BitUnix confound check + fee-floor diagnostic

**Window:** 2026-05-17 05:14 UTC (trade-plan v2 flip) → 2026-05-20 04:36 UTC. ~71 h.
**Source:** read-only `sqlite3` against prod `/home/azureuser/trading_corp/data/trading_corp.db` via `az vm run-command invoke` + local compute on the pulled payloads.
**Scope:** funnel/distribution diagnosis. No win-rate or PF conclusions drawn from n=2 v2-era fires.

Two questions:
1. Is the heavy sell-side skew regime-driven or scorer-driven? (Confound check — gates everything else, since H2 weight re-tune and v2 placement landed within 24 h.)
2. Is the trade-plan v2 fee floor over-killing legitimate edge, or correctly reporting that swing SLs produce R below cost?

---

## Step 1 — sell-skew attribution (CONFOUND CHECK)

### 1.1 Webhook side mix is upstream of scoring

Webhooks carry `signal` only, not `side`. The scorer derives side from yaml config (`bitunix_futures.scoring.factors.<name>.side`). I mapped each signal to its intrinsic side using the canonical name convention (`*_buy`/`*_sell`, `*_bull`/`*_bear`, `*_top`=sell, `*_bottom`=buy, `mc_a_red_*`=sell, `mc_b_buy_*`=buy, etc.) — this is config, not scorer state, so it's invariant across H2.

Counts by intrinsic side, raw webhook arrivals:

| Window | Sell | Buy | Ambiguous (mc_a_bluetriangle / mc_a_longema / mc_a_yellow_x) | Sell ÷ (Sell+Buy) |
|---|---:|---:|---:|---:|
| **Pre-H2** (5/10 → 5/16 19:21, ~6.8 d) | 713 | 270 | 106 | **72.5%** |
| **Post-v2** (5/17 05:14 → now, 2.96 d) | 404 | 121 | 77 | **77.0%** |

Webhooks are pre-scorer. The bear tilt was already present at 72.5% pre-H2. Post-v2 ticked up to 77.0%. Both windows are unambiguously bear-tilted at the input layer. The H2 re-tune did not — could not — change webhook arrival mix.

### 1.2 Scorer-side mix tracks the input mix

FRESH-only `bitunix_score_decided` (`trigger_source IN (market_cypher, lord_otter)` — excludes redeem-loop reevaluations):

| Window | sell | buy | flat | sell ÷ (sell+buy) |
|---|---:|---:|---:|---:|
| Pre-H2 (5/10 → 5/16 19:21) | 595 | 262 | 41 | **69.4%** |
| H2-live, pre-v2 (5/16 19:21 → 5/17 05:14) | 63 | 22 | 2 | 74.1% |
| Post-v2 (5/17 05:14 → now) | 425 | 131 | 46 | **76.4%** |

Scorer-side drift (69.4% → 76.4%, +7pp) closely tracks webhook-side drift (72.5% → 77.0%, +4.5pp). The scorer is *responding to a slightly more bear-tilted input*, not amplifying it.

### 1.3 PREMIUM tier (fresh-only) — the only place H2 visibly changed behavior

| Window | PREMIUM sell | PREMIUM buy | days | per-day |
|---|---:|---:|---:|---:|
| Pre-H2 | 25 | 3 | 6.8 | 4.1/d |
| Post-v2 | 24 | 0 | 3.0 | 8.0/d |

PREMIUM-tier per-day count nearly doubled post-H2 (4.1 → 8.0). The 3 pre-H2 buy-PREMIUMs all disappeared post-v2 (small n; not actionable on its own). H2's heavier weighting of the precision-family signals (per memory: `mc_a_red_diamond` 4→3 cap + `spoon_bear` 2→3 up-weight, etc.) is producing more PREMIUM events — but only on the side that already dominates.

### 1.4 BTC regime — HTF NEUTRAL × 39 reads correctly

Daily candles for the window:

| Day | Open | Close | Net % | Range | Trendiness (\|net\|÷range) |
|---|---:|---:|---:|---:|---:|
| 5/14 | 79,288 | 81,049 | +2.22% | 3,121 | 0.56 |
| 5/15 | 81,049 | 79,074 | −2.44% | 3,011 | 0.66 |
| 5/16 | 79,074 | 78,104 | −1.23% | 1,598 | 0.61 |
| **5/17** | **78,104** | **77,430** | **−0.86%** | **1,887** | **0.36** |
| 5/18 | 77,430 | 76,963 | −0.60% | 1,741 | 0.27 |
| 5/19 | 76,963 | 76,800 | −0.21% | 1,275 | 0.13 |

Five consecutive negative daily closes, momentum fading (trendiness 0.66 → 0.13). 3m bars over the v2 window: $76,017 → $78,564 range = 3.35% — gentle drift down with slowly compressing volatility. The HTF classifier reading `NEUTRAL × 39/39` is textbook-correct: directionally down but not strongly trending; below the breakout threshold.

### 1.5 Step 1 decision

**Input signal distribution is trustworthy AS regime-reflective.** Webhook side mix was already 72.5% bear pre-H2 — the H2 re-tune did not introduce structural bear bias to the *input*. The scorer's 76% sell read is appropriate response to a slightly more bear-tilted feed in a 5-down-day BTC drift. HTF NEUTRAL is the correct read of a slow grind-down.

**The H2 re-tune did increase PREMIUM-tier rate ~2× and eliminated the (tiny) pre-H2 buy-PREMIUM stream.** This is on the order of the intended H2 effect — the precision-family up-weights deliver more PREMIUM events — but the buy-side disappearance is worth keeping an eye on across a longer/more-mixed regime.

**Recommendation: do NOT revert H2 weights based on this evidence.** The data does not show the re-tune malfunctioning. Downstream reads (including Step 2) are on a regime-reflective input set, not a structurally biased one. Step 2 proceeds.

**Watch-item, not action-item:** if a sustained bullish window arrives and PREMIUM-buy count stays at zero, that would be evidence the H2 caps cut too deeply into the buy-side stream — worth revisiting then with the comparable-volume bull-regime data.

---

## Step 2 — fee-floor R-distribution diagnostic

### 2.1 What the floor formula computes

From prod `config/strategies.yaml`:
- `taker_pct: 0.0004`  `maker_pct: 0.00014`  `slippage_pct: 0.00005`
- `entry_is_taker: true`  `tp_is_maker: false` (MVP — market exits)
- `tp1_min_profit_multiplier: 2.0`

Round-trip cost = 2 × taker + 2 × slippage = **0.090% of entry price**.
Fee floor = 2.0 × RT cost = **0.180% of entry price**.

The skip condition is `fee_floor_pct × entry > TP2_distance`, where TP2 distance = 1.0 × R (the SL distance). So in R% terms: **skip if R < 0.180% of entry**.

### 2.2 Per-row computation (all 11 trade_plan_decision rows)

Reconstructed R from each payload using the v2 placement logic (swing-preferred ± buffer; ATR-fallback if `swing/atr > 2.5` or `< 0.5`):

| ts (UTC) | result | side | entry | ATR | swing R | sw/atr | pick | R ($) | R (%) | verdict |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---|
| 5/17 16:51 | SKIP | sell | 78,014 | 45.0 | 154.6 | 3.43 | atr_fb | 67.6 | **0.087%** | floor blocks |
| 5/17 16:54 | SKIP | sell | 78,019 | 46.0 | 181.4 | 3.94 | atr_fb | 69.0 | **0.088%** | floor blocks |
| 5/17 18:24 | SKIP | sell | 78,009 | 58.3 | 243.6 | 4.18 | atr_fb | 87.5 | **0.112%** | floor blocks |
| 5/18 05:21a | SKIP | sell | 76,965 | 70.6 | 129.2 | 1.83 | swing | 129.2 | **0.168%** | floor blocks (margin) |
| 5/18 05:21b | SKIP | sell | 76,965 | 70.6 | 129.2 | 1.83 | swing | 129.2 | **0.168%** | floor blocks (margin) |
| 5/18 08:40 | SKIP | sell | 76,957 | 68.7 | 116.0 | 1.69 | swing | 116.0 | **0.151%** | floor blocks |
| **5/18 16:24** | **FIRE** | sell | 76,407 | 131.2 | 203.5 | 1.55 | swing | 203.5 | **0.266%** | clears |
| **5/18 18:30** | **FIRE** | sell | 76,319 | 98.0 | 444.8 | 4.54 | atr_fb | 147.0 | **0.193%** | clears (margin) |
| 5/19 04:04 | SKIP | sell | 76,706 | 64.0 | 163.0 | 2.55 | atr_fb | 96.0 | **0.125%** | floor blocks |
| 5/19 04:25 | SKIP | sell | 76,686 | 71.8 | 268.8 | 3.74 | atr_fb | 107.8 | **0.141%** | floor blocks |
| 5/19 13:22 | SKIP | sell | 76,665 | 66.8 | 222.9 | 3.34 | atr_fb | 100.2 | **0.131%** | floor blocks |

**Reconstruction matches actual outcomes 11/11.** All 9 skips have R% < 0.180%. Both fires have R% > 0.180%. No outliers; no errors in the floor logic.

### 2.3 R distribution

- **Skip R%** (n=9): 0.087, 0.088, 0.112, 0.125, 0.131, 0.141, 0.151, 0.168, 0.168
- **Fire R%** (n=2): 0.193, 0.266
- Median skip R%: 0.131 — well below the 0.180 floor.
- Gap between highest skip (0.168) and lowest fire (0.193) is 0.025 pp — a tight floor, but the floor isn't binding on a noisy boundary; it's separating two clearly different distributions.

### 2.4 Root mechanism — why R% is structurally low in this window

BTC is in a low-volatility down-drift. ATRs are 45–72 most of the time; only the 5/18 afternoon (when fires happened) saw ATR jump to 98–131. The swing/ATR ratios:

- 6 of 9 skips have `swing/atr > 2.5` → forced into ATR-fallback (R = 1.5×ATR ≈ 0.08–0.13%) → below floor.
- 3 of 9 skips have `swing/atr` in-bounds but the swing distance itself is small (~115–129) → R% just below floor.
- Both fires happened in the 5/18 afternoon ATR-expansion window: the swing-fire had ATR 131 (3× the morning's), the atr-fb-fire had ATR 98 (still 2× morning).

**The fee floor isn't over-killing — it's correctly reporting that in low-vol BTC, swing-based SL placement produces R too small to clear venue costs.** The floor disqualifies edges where the expected move (≤R per Option C arithmetic) doesn't exceed the cost of the round-trip.

### 2.5 What-if simulations (data-driven, not recommendations)

**Relax `max_stop_atr_mult` from 2.5 → 4.0:** five skips that fell to ATR-fallback would instead use swing SLs (which are larger than the ATR-fallback in these cases). All five would have R% > 0.180% and FIRE: 5/17 16:51, 5/17 16:54, 5/19 04:04, 5/19 04:25, 5/19 13:22. Cost of this change: wider absolute stops → larger absolute loss when hit. Also TP3 (at 2.5R) moves further away, lowering TP3 hit rate. *Calibration tradeoff, not a fix.*

**Flip `tp_is_maker: false → true`** (taker entry, maker exits — closer to original VIP3 fee-math memo): RT cost drops 0.090% → 0.064%, floor drops 0.180% → 0.128%. 5 of 9 skips would FIRE under this floor (5/17 18:24, 5/18 05:21a/b, 5/19 04:25, 5/19 13:22). But maker exits depend on price *coming to* your limit — fill rate isn't 100%. This is a venue-execution decision, not strategy tuning. Per the design memo, MVP deferred maker-exits; this is a candidate for a Phase 2 conversation but needs a fill-rate model first.

**Both together (max=4.0 AND maker exits):** all 9 skips would fire on the floor check, but the trade plan would still hit other gates downstream. The combined effect is significant fire-rate expansion at the cost of two simultaneous design changes.

### 2.6 Step 2 decision

**The fee floor is not over-killing legitimate edge.** It is correctly reporting that swing-based SLs in low-volatility BTC produce R below the round-trip cost threshold of 0.180% (a structural statement about the market regime, not the floor's calibration). All 9 skips have R% below the floor; both fires have R% above. The boundary is clean.

**The real question is structural, not calibration:** is BitUnix Futures + 3 m timeframe + Option C SL placement a venue/timeframe combination that has reliable edge after costs in low-vol BTC?

- The current data (9/11 skips on fees) says **not in this regime**.
- The 2 fires that survived the floor were both losses (n=2, uninformative for outcome — but the loss-rate isn't the question; the question is whether the *system was offered a tradeable edge* and it wasn't).
- Outcomes if structure persists: fire rate stays ~0.7/day, and the 60-day paper-cutover window yields ~42 trades — borderline-usable for n≥30 WR confidence intervals.

**Recommended follow-ups (not action items in this report):**
1. **Don't loosen the floor.** It's working as designed.
2. **Track ATR regime explicitly.** When BTC ATR(14, 3m) ≥ 90 (i.e., volatility consistent with the 5/18 afternoon), the fee floor clears swing-based SLs. When ATR < 75, it doesn't. This is a tradeable signal about *when v2 will actually fire*, useful for setting expectations on shadow-data accumulation pace.
3. **Re-evaluate `max_stop_atr_mult` as a Phase 2 conversation** after sufficient mixed-regime data accumulates. The current 2.5 cap was chosen to bound absolute downside in stress regimes; relaxing it in low-vol regimes is a different conversation than relaxing it everywhere.
4. **Re-evaluate maker exits (`tp_is_maker: true`) as a Phase 2 conversation** with a fill-rate model. Memo's MVP scope explicitly deferred this.
5. **Sustained-bull regime check.** When BTC reverses into a bull leg, re-pull this analysis to confirm the input mix flips correctly (PREMIUM-buy count returns) and the H2 weights don't structurally suppress the buy side.

---

## Artifacts

- Query scripts: `tmp/bitunix_confound_discovery.sh`, `tmp/bitunix_confound_analysis.sh`, `tmp/bitunix_confound_part1.sh`
- Local R-distribution compute: `tmp/compute_fee_floor.py`
- Prior funnel review: `reports/bitunix_paper_data_review_2026-05-20.md`

## Honest assessment

**Step 1 result is unambiguous.** The 88% sell skew in the prior funnel review is regime-driven; the H2 re-tune did not invent it. Pre-H2 webhooks were already 72.5% bear; BTC has had 5 consecutive negative daily closes during this window. The HTF classifier reads NEUTRAL because momentum is fading even as price drifts down — that's a correct read of the regime.

**Step 2 result is also unambiguous.** The fee floor is doing exactly what its memo specifies. The 82% post-HTF skip rate (9/11) isn't a calibration miss — it's a structural statement that low-vol BTC + 3m timeframe + taker-both round-trip costs produces edges below the venue's break-even point. The system is reporting "the market isn't offering tradeable edge right now," which is the right answer to be reporting if it's true.

**Both diagnoses converge on one operational reality:** the v2 placement design is functioning. The market is offering 2 fires in 71 hours, both in a brief ATR-expansion window. The system is paper-mode and `auto_execute: false` for good reason — there's nothing here to validate yet. Continue paper-mode; let the regime expand the data set; revisit calibration questions after a mixed-regime sample arrives.
