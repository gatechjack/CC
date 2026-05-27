# Bitunix PA-validation replay — synthesis (2026-05-27)

**Scope:** read-only replay of options 1, 5, 6 (score↔PA internal-consistency
only) against historical audit rows since the 2026-05-23 15:52 UTC deploy.
Tightness options 3 (`htf_regime.proximity_block_pct`) and 4
(`trade_plan.tp1_min_profit_multiplier`) are explicitly OUT OF SCOPE —
deferred to the 2026-06-19 midpoint tripwire per Board decision
(memory `[[bitunix-paper-clock]]`).

**Artifacts:**
- `scripts/replay_pa_validation_alt.py` — read-only replay (committed)
- `reports/2026-05-27_bitunix_pa_replay.txt` — raw replay output (committed)

**No live config touched. No restart. No YAML edit. Tripwire respected.**

---

## Universe (since 2026-05-23 15:52 UTC, captured 2026-05-27 22:50 UTC)

| | |
|---|---|
| Total score_decided rows | 3,336 |
| Total pa_validation rows | 2,896 |
| PA-rejected score rows | 2,869 |
| Outcomes | 2,869 skipped_pa_validation · 437 skipped_score · 17 skipped_htf_gate · 7 skipped_trade_plan · **3 placed** · 3 skipped_cooldown |

---

## Per-option fire-rate estimates

### Option 1 — `pa_validation.require_all: true → false` (≥2 of 3 must pass)

Failed-validator distribution across the 2,869 PA rejects:

| validators failed | rows |
|---|---|
| 1 of 3 | 527 |
| 2 of 3 | 848 |
| 3 of 3 | 1,494 |

- **527 PA rejects would convert to PA passes** under option 1 (those with exactly 1 failure).
- PA-pass rate jumps from 0.94% → **18.4%** on the same input stream.
- Projected to placements via historical downstream rates (HTF hard-zero 17/27=63%, trade_plan reject 7/10=70% on STANDARD):

  ```
  527 → 195 (after HTF) → ~59 placements
  ```

- **Estimated additional fires under option 1: ~59 over the 4-day window** (vs. baseline 3). ~15/day. Caveat: HTF and trade_plan rates were measured on a PREMIUM-heavy survivor set; STANDARD-heavy option-1 survivors may face worse trade_plan rejection (fee floor).

### Option 5 — `tier_thresholds.standard: 5 → 7`

This tightens the score side. From the same audit corpus:

- Drops **1,021 rows** from STANDARD → WEAK/SKIP (they never reach PA).
- Of the 27 currently-PA-pass rows: only **13** still reach PA (cut by 52%).
- Of the 3 placed trades: **only 1 (the PREMIUM) survives**; the 2 STANDARDs are cut.

### Option 6 — `min_score_to_fire: 5 → 7`

- Drops **1,459 rows** below the new floor.
- Of the 27 currently-PA-pass rows: **13** survive (same as option 5).
- Of the 3 placed trades: **only 1 survives**.

---

## All-three-failed bucket — composition (n=1,494)

This is the critical bucket for the "score over-generous vs. PA too strict"
diagnosis.

| | |
|---|---|
| **Stack size: solo (1 sig)** | **0** (0.0%) |
| **Stack size: pair (2 sigs)** | **188** (12.6%) |
| **Stack size: three+ sigs** | **1,306** (87.4%) |
| Tier | 973 STANDARD · 521 PREMIUM |
| Side | 1,387 sell · 107 buy |
| net_score median | ~8 (range 5–17) |

**Top dominant signals (the #1 contributor on each row):**

| count | % | signal |
|---|---|---|
| 742 | 49.7% | mc_a_red_diamond |
| 576 | 38.6% | mc_a_blood_diamond |
| 69 | 4.6% | mc_b_sell_circle_div |
| 43 | 2.9% | mc_b_buy_circle_div |

**Top full signal stacks (concrete confluence sets):**

| count | stack |
|---|---|
| 305 | mc_a_blood_diamond + mc_a_red_diamond + mc_a_redx |
| 111 | mc_a_blood_diamond + mc_a_red_diamond + mc_a_redx + mc_b_sell_circle_div |
| 87 | mc_a_red_diamond + mc_b_sell_circle |
| 85 | mc_a_red_diamond + mc_a_redx + mc_b_sell_circle |
| 79 | cvd_bear_flip + mc_a_red_diamond + mc_a_redx |

---

## Hypothesis verdict — what the data actually says

The script's automated hint reads "top-1 dominant 49.7% → score over-generous,
favor options 5/6." **That hint is wrong here, and the verdict requires
reading more carefully than the hint allows.**

### Why "score over-generous" is REFUTED

1. **Zero solo-signal rows in the bucket.** Every single one of the 1,494
   all-three-failed rows had ≥2 contributing signals on the winning side.
   87.4% had 3+. The single most common stack
   (`mc_a_blood_diamond + mc_a_red_diamond + mc_a_redx`, 305 rows) is a
   genuine 3-signal Cypher A-panel sell confluence with combined weight 11.
   These are not "one signal over-tiering."

2. **Options 5 and 6 would CUT the fire rate, not improve it.** Both shrink
   the candidate pool entering PA without producing any new fires. Of the 3
   placed trades, both options preserve only **1** — a 67% reduction in
   placement rate. The structural problem is downstream of score, not at it.

3. **The 49.7% mc_a_red_diamond dominance is regime-driven.** BTC has been
   sell-biased throughout the window (1,387 sell vs 107 buy in this bucket;
   HTF regime was NEUTRAL/BEAR/SAFE_MODE with zero BULL days). The
   Cypher A-panel sell weights (4-5) are the heaviest hitters in the
   weight table; in a sustained sell environment they will naturally
   dominate. That's the market talking, not the engine over-counting.

### Why "PA too strict on legitimate confluence" is the better-supported hypothesis

The bucket is dominated by multi-signal stacks of 3-5 Cypher A/B sell
signals (real confluence in the score's framing), and PA still rejects
all three validators. The most likely explanations are:

**a. Timeframe horizon mismatch.** Score awards points from 3m-bar
TV signals; `structure_alignment` checks `higher_highs_4h` /
`lower_lows_4h` on a 4-hour horizon. A 3m-bar score stack can fire
before 4h structure has confirmed. This is a real architectural
disagreement, not a bug — the gates were designed to be independent
horizons. But during a tight-range week, the score lights up while
4h structure stays flat.

**b. PA computes from different inputs than score.** VWAP from the
broker quote stream; structure from a separate 4h aggregator; volume
from another window. If any input is degraded (cache miss, late tick,
window-edge issue) the PA validator silently fails even when the
signal stack is real.

**Either way, the fix is on the PA side, not the score side.**

### Mixed-evidence caveat

527 rows had exactly 1 validator fail. Those are unambiguous "PA was
*almost* willing to let it through" cases — and option 1 is precisely
the discriminator that lets them through. But 1,494 had all three fail —
even option 1 still rejects them. So option 1 recovers ~18% of rejects,
not 100%. The remaining 82% is consistent with hypothesis (a):
multi-signal score stacks that the PA's 4h-structure check has not yet
confirmed.

---

## Recommendation for next session

**Ship option 1 alone** as a 1-week diagnostic. Specifically:

```yaml
# config/strategies.yaml
bitunix_futures:
  pa_validation:
    require_all: false   # 2026-05-27 diagnostic: was true; loosen to ≥2 of 3
                         # Expected +59 fires/4d vs baseline; revert after 1
                         # week if the placed trades trend negative R-multiple.
```

Followed by:
- Restart `trading-corp.service` (~5min strategy pause).
- Watch `pa_validation_decision` decision rate (expect ~18% pass vs 0.94%).
- Watch `would_have_placed` rate (expect ~15/day vs ~0.75/day baseline).
- Compare PR-of-fires under loose-PA to the 3 baseline fires after a week.

### What to NOT ship

- **NOT option 5 or 6.** Data refutes the over-generous-score hypothesis;
  these would cut the fire rate without addressing the PA mismatch.
- **NOT options 3 or 4.** Tripwire-deferred per `[[bitunix-paper-clock]]`
  — re-decide at 2026-06-19.

### Stretch follow-up (not gating)

Investigate **PA input correctness** as a code change, not a YAML knob:

1. Add a per-validator audit field showing the raw INPUT computed
   (e.g., for `structure_alignment` on sell: log `lower_lows_4h_observed=true/false` and the bars used). Right now we only know which validator failed, not why its input said no.
2. Once that audit is in place, a follow-up replay can split the
   "PA validator computed wrong" from "PA validator correctly says
   the 4h horizon disagrees with the 3m signal stack." That's the
   real diagnostic, not require_all.

This is a code change, not a knob. File it for after the option-1
diagnostic week.

---

## Tripwire boundary — explicit acknowledgment

This analysis covers **score↔PA internal consistency only** (options 1, 5, 6).
Tightness options remain deferred:

- Option 3 (`htf_regime.proximity_block_pct`, currently 0.30) — DEFERRED
  to 2026-06-19 tripwire.
- Option 4 (`trade_plan.tp1_min_profit_multiplier`, currently 2.0) —
  DEFERRED to 2026-06-19 tripwire.

The bitunix paper clock (2026-05-20 → 2026-07-19, midpoint 2026-06-19)
was specifically designed to prevent gate-tightness re-litigation on
partial-sample evidence. This analysis is consistent with that
discipline: the only proposed change (option 1) is an *internal
consistency* fix between two gates that disagree about what
"confluence" means, not a tightness call.

## Regime caveat — explicit acknowledgment

Signal mix is 2:1 sell-to-buy and HTF regime has been NEUTRAL/BEAR/SAFE_MODE
with **zero BULL or STRONG_BULL days** since the 2026-05-23 deploy.
This is the market, not a bug. **No buy-side-specific changes proposed** —
the signals aren't there to warrant any. Any buy-side gate tuning should
wait until BULL regime days are observed.
