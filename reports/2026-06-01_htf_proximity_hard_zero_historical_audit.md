# HTF proximity hard-zero rule — historical audit
**Window:** 2026-05-16 14:45 UTC → 2026-05-31 16:42 UTC (~16 days)
**Corpus:** 157 `htf_gate_decision` rows with `hard_zero_reason ∈ {proximity_to_support, proximity_to_resistance}` (all in `mode=enforce`, all on `BTCUSDT.P`)
**Prod:** `tc-prod-vm` @ origin/main `f110c74`
**Branch:** `htf-proximity-audit-2026-06-01` (worktree-isolated, NOT merged)
**Author:** Claude (read-only diagnostic; no code/config/prod writes)

This memo answers BACKLOG F2 from the 2026-05-31 Stage 1 post-deploy review (`reports/2026-05-31_stage1_first_17h_paper_mode_review.md` §9): *"audit `proximity_to_support` / `proximity_to_resistance` hard-zero behavior over the existing 3m bar history; tag each rejected setup with realized 30/60min directional outcome; report whether the rule has positive selectivity, is neutral, or is consistently wrong."*

---

## 1. Executive summary

- **The rule is approximately NEUTRAL at the aggregate level.** On 155 sell-side `proximity_to_support` rejections, mean Δ+60m = **+0.0158%** (median **−0.0051%**), MFE = **0.26%**, MAE = **0.21%**. **26.5%** of blocks were "wrong" by a loose threshold (≥0.10% adverse move in 60min); **9.0%** by a strong threshold (≥0.30%, clears the typical fee floor with margin). Convention: positive Δ = block was wrong.
- **The F2 concern is partially supported by the broader data, not refuted.** The original review's N=2 `cvd_bull_flip` events both moved against the block; the N=8 `cvd_bull_flip` cohort here shows mean Δ+60m = **+0.30%**, 50% wrong-loose. Small but directionally consistent.
- **Two cohorts stand out as worst-fit:** `cvd_bull_flip` triggers (N=8, 50% wrong-loose) and BEAR/PREMIUM sells (N=11, 36.4% wrong-strong). Both are too small to act on; flag for ongoing monitoring.
- **The rule is NOT broken for the bulk of fires.** The two largest trigger cohorts (`mc_a_red_diamond` N=54, `mc_a_redx` N=31) show median Δ+60m of −0.01% and −0.03% — blocks held up.
- **No code or config change is recommended from this audit.** The aggregate selectivity is neither clearly positive nor clearly negative; the small-N "worst cohorts" need more samples before a parameter or rule change is justifiable. Two ongoing watch-items filed below.

---

## 2. Scope, methodology, and data integrity

### 2.1 Scope

| | |
|---|---|
| **Rejection corpus** | All `audit_event` rows with `kind='htf_gate_decision'` AND `payload_json.hard_zero_reason ∈ {proximity_to_support, proximity_to_resistance}`. |
| **Window** | First seen 2026-05-16 14:45 UTC; last seen 2026-05-31 16:42 UTC. |
| **Bar source** | `bitunix_bar_history`, timeframe `3m`, single-symbol (`BTCUSDT.P`). 7680 bars from 2026-05-15 05:30 UTC → 2026-05-31 19:51 UTC. |
| **Bar-pointer coverage** | 157/157 rows carry `bar_h1_last_close_ms` (100%); PR 5f predates the window. |
| **Mode** | 157/157 in `mode=enforce` (no `shadow` rows in scope). |
| **Volatility tier at rejection** | 157/157 `vol_tier=high` (the window was uniformly elevated volatility). |

### 2.2 Methodology

- For each rejection at decision timestamp T:
  1. Find the 3m bar whose `ts_ms` is the largest multiple of 180_000 ≤ `T_ms`. Use its `close` as `decision_price`.
  2. Look up bars at index `+10` (30min forward close) and `+20` (60min forward close).
  3. Compute MFE/MAE over the 20-bar forward window: MFE_sell = `decision_price − min(low)`, MAE_sell = `max(high) − decision_price`. Sign-flipped for the 2 buy-rejections.
- "Block was wrong" thresholds are arbitrary heuristics for reporting; the raw deltas are the primary evidence:
  - **loose:** Δ+60m ≥ 0.10% in the rejected direction. Calibration: ~0.10% is well below the typical fee-floor break-even (~0.18%) — captures "the model would have moved meaningfully against the block."
  - **strong:** Δ+60m ≥ 0.30% — "the block held back a setup that would have cleared the fee floor with margin."
- All deltas are signed so **positive = block was wrong**.

### 2.3 Data integrity

| Check | Result |
|---|---|
| Rejection rows with no joinable decision bar | 0 / 157 |
| Rejection rows with no +60m close | 0 / 157 |
| Bar pointer coverage on rejections | 157 / 157 |
| 3m bar gaps requiring backward-step lookup | Linear scan back ≤5 slots; only used as fallback (0 observed in this run) |
| `mode` field consistency | 157 / 157 enforce |
| Rejection corpus matches Probe B totals | 94 + 25 + 25 + 11 + 2 = 157 ✓ |

No data anomalies surfaced.

---

## 3. Phase 1 — extraction summary

| Cohort (reason × regime × side × tier) | N |
|---|---|
| `proximity_to_support` × NEUTRAL × sell × STANDARD | 94 |
| `proximity_to_support` × BEAR × sell × STANDARD | 25 |
| `proximity_to_support` × NEUTRAL × sell × PREMIUM | 25 |
| `proximity_to_support` × BEAR × sell × PREMIUM | 11 |
| `proximity_to_resistance` × NEUTRAL × buy × STANDARD | 2 |
| **Total** | **157** |

- **155 (98.7%) are `proximity_to_support`**; the resistance rule fired only twice — not analyzable in isolation.
- **All 155 support-blocks are sell-side** (per the rule definition: `if side == "sell" and verdict.distance_to_support_pct < proximity_block_pct`).
- **Regime distribution:** 119 NEUTRAL + 36 BEAR. The NEUTRAL rows all have `h4_regime=transitional`; the BEAR rows all have `h4_regime=bear`. D1 regime: 142 transitional + 15 range. No BULL/STRONG_* composite regimes appeared.
- **Session distribution:** 52 asia / 43 london / 33 overlap / 29 new_york. Geographically diversified.
- **Trigger distribution:** see §4.5; dominated by `mc_a_red_diamond` (54) + `mc_a_redx` (31).

---

## 4. Phase 2 — outcome analysis

### 4.1 Overall — `proximity_to_support` sell rejections (N=155)

| Metric | Value |
|---|---|
| Mean Δ+30m | +0.0042% |
| Median Δ+30m | −0.0086% |
| Mean Δ+60m | +0.0158% |
| Median Δ+60m | −0.0051% |
| Mean MFE (sell-side reward window) | 0.2556% |
| Mean MAE (sell-side risk window) | 0.2119% |
| % block-wrong @ +60m, loose (≥0.10%) | 26.5% |
| % block-wrong @ +60m, strong (≥0.30%) | 9.0% |

**Read:** central tendency is zero. The block has near-symmetric forward outcomes — MFE ≈ MAE ≈ 0.21–0.26%. About 73.5% of the time, the block held up loosely; about 9.0% of the time it stopped a setup that would have decisively cleared the fee floor.

### 4.2 By reason × regime × side × tier (matches Probe B)

| reason | regime | side | tier | N | Δ+30m | Δ+60m | MFE | MAE | wrong-loose@60m | wrong-strong@60m |
|---|---|---|---|---|---|---|---|---|---|---|
| proximity_to_support | NEUTRAL | sell | STANDARD | 94 | −0.021% | −0.007% | 0.241% | 0.235% | 24.5% | 8.5% |
| proximity_to_support | NEUTRAL | sell | PREMIUM | 25 | +0.044% | +0.044% | 0.232% | 0.121% | 32.0% | 4.0% |
| proximity_to_support | BEAR | sell | STANDARD | 25 | +0.036% | −0.023% | 0.254% | 0.210% | 24.0% | 4.0% |
| proximity_to_support | BEAR | sell | PREMIUM | 11 | +0.058% | **+0.237%** | 0.440% | 0.226% | 36.4% | **36.4%** |
| proximity_to_resistance | NEUTRAL | buy | STANDARD | 2 | −0.008% | −0.093% | 0.276% | 0.307% | 50.0% | 0.0% |

**Reads:**
- **The largest cohort (NEUTRAL STANDARD sell, N=94)** shows essentially zero mean and median Δ at both horizons. Neither systematically right nor wrong.
- **NEUTRAL PREMIUM sells (N=25)** show a positive Δ tilt of ~0.04%. Wrong-loose 32% but wrong-strong only 4% — small misses, not big ones.
- **BEAR PREMIUM sells (N=11)** are the stark outlier: mean Δ+60m = +0.24%, wrong-strong = 36.4%. Tiny sample but worth a separate watch. The 11 fires were on 2026-05-28 between 08:21–14:15 UTC — i.e., a single trend day. Confounded.
- **Resistance buys (N=2)** are not analyzable. One block held (Δ+60m = −0.09%), one was wrong (Δ+60m = +0.17%). Not enough to comment.

### 4.3 By distance-to-support bucket (sells only, N=155)

| bucket | N | Δ+30m | Δ+60m | MFE | MAE | wrong-loose | wrong-strong |
|---|---|---|---|---|---|---|---|
| <0.10% | 40 | +0.015% | **−0.052%** | 0.268% | 0.270% | 20.0% | 7.5% |
| 0.10-0.20% | 39 | −0.032% | +0.040% | 0.260% | 0.242% | 25.6% | 15.4% |
| 0.20-0.30% | 76 | +0.017% | +0.039% | 0.247% | 0.166% | 30.3% | 6.6% |

**Read:** the rule looks **best-calibrated at the tightest distances (<0.10%)** — mean Δ+60m is slightly negative (block was right), wrong-strong rate lowest at 7.5%. The middle bucket (0.10-0.20%) has the highest strong-wrong rate at 15.4%. The widest bucket (0.20-0.30%) — the bulk of the corpus — sits between, with wrong-loose 30% but wrong-strong only 6.6%. **No clean monotonic gradient supports tightening the threshold.**

### 4.4 By regime (collapsing tier)

| regime | N | Δ+30m | Δ+60m | MFE | MAE | wrong-loose | wrong-strong |
|---|---|---|---|---|---|---|---|
| BEAR | 36 | +0.043% | +0.057% | 0.311% | 0.215% | 27.8% | 13.9% |
| NEUTRAL | 119 | −0.008% | +0.004% | 0.239% | 0.211% | 26.1% | 7.6% |

**Read:** BEAR rejections show a slight positive directional bias (wrong-strong 13.9% vs 7.6%); the rule blocks setups in BEAR that would have moved further than NEUTRAL setups. Consistent with the theoretical concern: when the composite regime is BEAR, the "mean-reversion preferred" matrix base is weakest, but the proximity hard-zero still fires identically. 36 samples — too small to act on but enough to flag.

### 4.5 By `trigger_signal` (sells only)

| trigger | N | Δ+60m | MFE | MAE | wrong-loose |
|---|---|---|---|---|---|
| mc_a_red_diamond | 54 | +0.013% | 0.254% | 0.207% | 29.6% |
| mc_a_redx | 31 | −0.030% | 0.242% | 0.243% | 19.4% |
| mc_b_buy_circle | 16 | −0.068% | 0.153% | 0.213% | 12.5% |
| cvd_bear_flip | 11 | +0.026% | 0.315% | 0.190% | 36.4% |
| mc_a_blood_diamond | 10 | +0.118% | 0.284% | 0.103% | 30.0% |
| **cvd_bull_flip** | **8** | **+0.304%** | **0.525%** | **0.152%** | **50.0%** |
| mc_b_sell_circle_div | 6 | +0.029% | 0.290% | 0.210% | 16.7% |
| mc_b_sell_circle | 5 | +0.074% | 0.265% | 0.195% | 40.0% |
| spoon_bear | 4 | −0.030% | 0.169% | 0.316% | 25.0% |
| mc_a_bluetriangle | 3 | −0.249% | 0.043% | 0.547% | 0.0% |
| mc_a_longema | 2 | −0.064% | 0.061% | 0.249% | 0.0% |
| mc_b_buy_circle_div | 2 | +0.111% | 0.339% | 0.121% | 50.0% |
| spoon_bull | 2 | −0.055% | 0.253% | 0.183% | 0.0% |
| otter_sell | 1 | +0.236% | 0.259% | 0.116% | 100.0% |

**Reads:**
- **`cvd_bull_flip` (N=8) is the most striking misalignment.** Mean Δ+60m = +0.30% (3× the loose threshold); 50% wrong-loose; MFE 0.53% vs MAE 0.15% (asymmetric — setups that broke broke decisively). The 2-event sample in the F2 source review showed the same pattern; N=8 is still small but consistent. The original Stage-1 review's N=2 was not just noise.
- **The bulk of the corpus (`mc_a_red_diamond` N=54, `mc_a_redx` N=31, `mc_b_buy_circle` N=16) holds up.** Δ+60m at or near zero; wrong-loose rates ≤ 30%.
- **`cvd_bear_flip` (N=11) is borderline:** mean Δ+60m essentially zero, but wrong-loose 36.4% (the count of misses is higher than the average severity).

---

## 5. Phase 3 — findings

### F1. Aggregate selectivity is approximately neutral

**Evidence:** N=155, mean Δ+60m = +0.016%, median = −0.005%. MFE ≈ MAE ≈ 0.21–0.26%. 73.5% of blocks held up by the loose threshold; only 9.0% were strong-wrong.

**Interpretation:** the rule is doing what a coin-flip mean-reversion gate would do at this granularity. It is *not* a clear edge contributor (positive selectivity) and *not* a clear edge destroyer (negative selectivity). On balance it removes some winners and some losers in roughly matched proportion.

**Counterfactual sanity check:** removing the rule entirely on the audit window would have admitted 155 sells. Mean Δ+60m of +0.016% × 155 = ~+2.5 cumulative percentage points across all 155, before fees. Per `[[project-bitunix-fee-floor-3rule-audit-2026-05-29]]` the fee-floor break-even is ~0.18%; per-trade Δ ≈ 0.016% falls 11× below it. Most admits would have been fee-floor rejected downstream anyway. The window's behavior is consistent with the broader "regime suppression" finding from the 5/31 review.

### F2. `cvd_bull_flip` trigger shows persistent negative selectivity

**Evidence:** N=8, mean Δ+60m = +0.30%, median +0.36% (above loose threshold), 50% wrong-loose. MFE 0.53% vs MAE 0.15% (asymmetric: when wrong, the rule is wrong by a lot; when right, the bounces are shallow). The original 5/31 §8.4 deep-dive on N=2 (5/30 22:09 + 5/31 13:57) showed the same direction and magnitude.

**Interpretation:** for this specific trigger, the rule's "bounce off support" assumption appears weakest. A `cvd_bull_flip` near a sell-side support level may indicate the bull-flip is occurring *because* support is about to be tested by absorption + selling pressure — the cvd flip and the proximity converge on the same micro-structure event. The block then opposes that.

**Caveat:** N=8 is small. The cohort spans 8 events across 16 days. A larger window (e.g. when more 3m bar history accumulates) is needed to confirm.

### F3. BEAR-regime + PREMIUM cohort shows the highest wrong-strong rate

**Evidence:** N=11, wrong-strong @ +60m = 36.4%, mean Δ+60m = +0.24%, MFE 0.44%. All 11 fired on 2026-05-28 in a single trend window (08:21–14:15 UTC) — single-day confound.

**Interpretation:** plausibly the same dynamic as F2 — high-conviction sells (PREMIUM) in a high-conviction regime (BEAR) near support are most likely to be testing-not-bouncing. But because all 11 fired on one trend day, the cohort is one event with eleven witnesses, not eleven independent observations.

**Caveat:** single-day confound; N effective ≈ 1. Cannot draw a generalizable conclusion.

### F4. Distance-to-support gradient does NOT support tightening the threshold

**Evidence:** wrong-loose rates: <0.10% → 20.0%; 0.10-0.20% → 25.6%; 0.20-0.30% → 30.3%. Wrong-strong: 7.5% / 15.4% / 6.6%. Non-monotonic.

**Interpretation:** the rule is best-calibrated at the tightest distances and gets noisier in the middle. Tightening the threshold (e.g. `proximity_block_pct` 0.3 → 0.2 or 0.1) would reduce the rejection count but would not preferentially trim the worst-calibrated cell. Loosening to 0.5 or 1.0 would admit more nominally-near-support setups, of which the 0.20-0.30% bucket suggests ~30% would have been wrong — comparable to the existing bucket distribution. **No clean parameter move emerges from this data.**

### F5. Resistance-side rule (buy blocks) has insufficient data

**Evidence:** N=2 in the full window. One block held (Δ+60m = −0.09%), one was wrong (Δ+60m = +0.17%).

**Interpretation:** the rule is rarely tested on the buy side because the audit window had no extended BULL-regime sub-period (composite regime never escalated above NEUTRAL, and BEAR-regime buys die earlier in the funnel before reaching the HTF gate). The asymmetry in firing counts (155 sell vs 2 buy) reflects the bear-leaning regime of the period, not an asymmetry in the rule itself. Cannot draw conclusions about the `proximity_to_resistance` side.

### F6. The single-window bias is the largest interpretive limit

**Evidence:** All 157 rejections fall inside a 16-day window during which BTC was in a high-volatility (`vol_tier=high` on every row) transitional/bear regime (D1 transitional or range; no D1 bull). The audit cannot answer "how does the rule perform across multiple volatility regimes?" or "across multiple D1 trend backgrounds?" because the corpus doesn't span them.

**Interpretation:** the conclusion that the rule is "approximately neutral overall" is *neutral on this regime*, not neutral universally. As bar history accumulates and the rule fires under different conditions (low-vol, BULL composite, D1-bull background), the same audit re-run later would be load-bearing.

---

## 6. Recommendations

### R1. No code or config change is recommended from this audit.

The aggregate signal is neutral; the worst-cohort signals are too small or too confounded to act on. Changing `proximity_block_pct` from 0.3 would not align with any clean gradient observed here. Removing the rule entirely would surrender 9% of strong-wrong-prevented and 17.5% (26.5 − 9) of loose-wrong-prevented in exchange for setups that, in this regime, mostly would have been fee-floor rejected anyway.

### R2. Two ongoing watch-items (no immediate action)

- **W1 — `cvd_bull_flip` selectivity.** Re-run this audit when the cohort reaches N≥30 (currently 8). If the N=30+ sample preserves Δ+60m ≥ +0.20% and wrong-loose ≥ 40%, a trigger-conditional proximity bypass (e.g. cvd_bull_flip waives the proximity hard-zero) becomes worth scoping. Do NOT scope it from N=8.
- **W2 — Regime-conditional behavior.** The BEAR/PREMIUM cohort (N=11) is confounded by a single trend day. When the next clean BEAR-regime sub-period accumulates (independent fires across ≥3 distinct trading days), re-cut F3. If the wrong-strong rate persists ≥30% on N≥20 independent setups, a regime-conditional threshold (e.g. proximity_block_pct halved in BEAR composite) is scope-worthy.

### R3. The audit's limits should be cited in any follow-up

Single-symbol (BTCUSDT.P), single-volatility-regime (high), no BULL-composite firings, all-enforce mode, single-window. Re-run when any of these break (e.g. when the 3m bar history spans a complete vol cycle, or when ETHUSDT.P starts emitting `htf_gate_decision` events).

---

## 7. Hard stops + constraints

| Stop | Status |
|---|---|
| Prod writes | None (`sqlite3 -readonly` used throughout) |
| Code changes | None (read-only analysis, audit doc only) |
| Config changes | None |
| Branch merge | None (worktree-isolated on `htf-proximity-audit-2026-06-01`) |
| Scope expansion | None — followed F2 charter from `[[project-2026-05-31-stage1-first-post-deploy-review]]` |
| Recommendations of code change | None (R1 explicitly "no change") |

All claims are backed by quoted cell counts + the per-row enriched TSV at `.scratch/htf_proximity_audit/rejections_enriched.tsv`. Reproducer artifacts:

- `.scratch/htf_proximity_audit/probe_scope.sql` — Phase 0 scope probe
- `.scratch/htf_proximity_audit/extract_rejections.sql` — Phase 1 rejection corpus pull
- `.scratch/htf_proximity_audit/extract_bars_3m.sql` — Phase 1 3m bar pull
- `.scratch/htf_proximity_audit/analyze.py` — Phase 2 analysis script
- `.scratch/htf_proximity_audit/analysis.md` — Phase 2 raw cohort tables (Markdown dump)
- `.scratch/htf_proximity_audit/rejections_enriched.tsv` — Phase 2 per-row enrichment

---

## 8. Cross-references

- `reports/2026-05-31_stage1_first_17h_paper_mode_review.md` §8–§9 (F2 source)
- `trading_corp/agents/strategies/bitunix_htf_regime.py:979-988` (rule code)
- `trading_corp/agents/strategies/bitunix_htf_regime.py:221, 243` (default `proximity_block_pct=0.3`)
- `trading_corp/agents/strategies/bitunix_htf_regime.py:927-931` (NEUTRAL matrix base — "mean-reversion preferred")
- `trading_corp/agents/divisions/bitunix_futures_observer.py:1097-1125` (htf_gate_decision audit payload write site, including PR 5f bar pointers)
- `trading_corp/persistence/db.py:18-25` (`audit_event` schema; `payload_json` column)
- `trading_corp/data/bitunix_bar_archiver.py:49-65` (`bitunix_bar_history` schema)
