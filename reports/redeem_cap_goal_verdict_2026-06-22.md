# BitUnix PA-redeem-cap — /goal walk-forward VERDICT (2026-06-22, FIRMED)

**Question:** does capping the PA-redeem wait (0/1/2/3 bars vs uncapped) improve
bitunix_futures net-R? Secondary: does a max-slippage entry guard recover more
than the bar cap, and does the finding flip in a bull / high-vol regime?

**Tool:** `scripts/run_redeem_sim.py` (validated, look-ahead-honest, fire-bar
pricing, real VIP3 fees, refuses the contaminated `trading_corp.db`). Clean
corpus = `btc_scalping.db` `bars_3m` (Bybit BTC, 2026-03-30 → 06-19). Decision
metric = **net-R per trade** (taker SL exits, 0.09 %rt). Win % diagnostic only.
Pooled aggregates are **trade-weighted** (Σnet_R / Σn), not a mean of per-window
means. Branch `bitunix-redeem-sim-2026-06-22`.

> This FIRMS UP and in part OVERTURNS the earlier 2-window tentative verdict.
> The earlier draft claimed "cap≈2 held across windows"; a proper train/validate
> lockbox split (below) shows it does NOT — it flips to cap0 in the train half
> and in both regime windows. The headline NULL stands and hardens.

---

## VERDICT (one line)

**NULL.** No redeem cap creates a profitable edge on ANY window or regime tested.
Every window is net-negative at every cap. The tentative "cap≈2 least-bad
per-trade" optimum **does NOT survive the lockbox** — it flips to cap0 in the
train half and in both regime windows. Best-cap wanders 0↔2 with no stability →
**noise, not a lever.** The slippage guard does not rescue it. The money lever is
gross edge / regime, not the redeem cap.

---

## TASK 1 — More windows (N), train vs lockbox

Six non-overlapping ~2-week windows, split chronologically: TRAIN = first 3,
VALIDATE/LOCKBOX = last 3. Per-window **net-R/trade** by cap:

| window | cap0 | cap1 | cap2 | cap3 | inf | best | N(inf) |
|---|---:|---:|---:|---:|---:|:--:|---:|
| W1 2026-04-01..04-15 [TR] | −0.569 | −0.562 | −0.531 | −0.563 | −0.551 | **2** | 34 |
| W2 2026-04-15..04-29 [TR] | −0.357 | −0.473 | −0.376 | −0.376 | −0.404 | **0** | 32 |
| W3 2026-05-01..05-15 [TR] | −0.205 | −0.206 | −0.206 | −0.206 | −0.305 | **0** | 25 |
| W4 2026-05-15..05-29 [VA] | −0.385 | −0.266 | −0.243 | −0.243 | −0.254 | **2** | 40 |
| W5 2026-05-20..06-03 [VA] | −0.371 | −0.325 | −0.284 | −0.284 | −0.292 | **2** | 44 |
| W6 2026-06-03..06-17 [VA] | −0.221 | −0.269 | −0.232 | −0.269 | −0.295 | **0** | 35 |

(W1 and W5 reproduce the prior 2-window verdict numbers EXACTLY → tool is stable.)

### Pooled (trade-weighted)

| split | cap0 | cap1 | cap2 | cap3 | inf | best | total N (cap 0→inf) |
|---|---:|---:|---:|---:|---:|:--:|---|
| **ALL 6** | −0.357 | −0.347 | **−0.311** | −0.323 | −0.346 | **2** | 152 / 181 / 197 / 199 / 210 |
| **TRAIN (1-3)** | **−0.386** | −0.425 | −0.387 | −0.400 | −0.431 | **0** | 63 / 77 / 84 / 85 / 91 |
| **VALIDATE (4-6)** | −0.337 | −0.290 | **−0.255** | −0.266 | −0.280 | **2** | 89 / 104 / 113 / 114 / 119 |

**Lockbox result: cap≈2 does NOT hold.** Best-per-trade cap = **0 in TRAIN, 2 in
VALIDATE.** Across the 6 windows the per-window winner is {2,0,0,2,2,0} — a coin
flip between cap0 and cap2. The cap≈2 pooled-all optimum is an artifact of the
validate half dominating the pool; it is **noise, not a stable optimum.**

Two robust facts that DO hold across all windows and both halves:
- **Every cap is net-NEGATIVE** (−0.21 to −0.57 R/trade). Strategy is underwater
  regardless of cap.
- **cap=inf (uncapped) is never best** and is usually among the worst — deep
  redeems add net-losing late entries. A *finite* cap weakly dominates uncapped,
  but which finite cap (0 vs 2) is regime-dependent. gross_R/trade is mildly
  POSITIVE on the later windows (≈ +0.01 .. +0.20) yet net is negative
  everywhere → **fees are the binding constraint, not the cap.**

---

## TASK 2 — Slippage-guard (max |fill − signal| entry reject)

**Built** (additive, default-OFF, unit-tested): `run_redeem_cap_backtest(...,
max_slip_pt=N)` rejects a *redeem* entry when
`|fire_price − signal_bar_close| > N` price points. First-pass fires have slip 0
by construction (signal bar == fire bar) so are never affected — the guard only
ever trims latency-drifted redeems. Look-ahead-honest (both prices ≤ fire bar).
5 new tests pass (default-off == baseline, exact boundary, first-pass-immune,
monotone in threshold, no-look-ahead). The sweep runs the guard IN-ENGINE (a
post-hoc filter over a fixed trade list is invalid — dropping a redeem changes
downstream cooldown state and thus which later signals fire; a validation
assertion caught this, hence in-engine).

### Pooled (trade-weighted) net-R/trade by (cap, slip), 6 windows

| cap \ slip(pt) | off | 25 | 50 | 75 | 100 |
|---|---:|---:|---:|---:|---:|
| **cap=2** | −0.311 | −0.357 | −0.314 | −0.309 | **−0.303** |
| **cap=inf** | −0.346 | −0.366 | −0.334 | −0.338 | **−0.309** |

(N shrinks with a tighter guard: cap=inf off N=210 → slip25 N=163; cap=2 off
N=197 → slip25 N=182. `slip_drop` counts the redeems the guard removed.)

**Does the slip guard recover MORE net-R than the bar cap? NO.**
- The best guarded result is **cap=2 + slip=100 at −0.303** — only **+0.008 R**
  better than cap=2 alone (−0.311), and still solidly net-negative. The bar cap
  itself moves net-R more (cap spread −0.31 → −0.43) than the guard does.
- A TIGHT guard (25 pt) HURTS at both caps (−0.357 / −0.366) — it cuts good
  redeems along with bad ones. Only a LOOSE guard (100 pt) helps, and only
  marginally. Net-R improves ~monotonically as the guard loosens toward off+,
  i.e. the guard only shaves the worst-drift tail; it does not add edge.

**Do they compose? Weakly / redundantly.**
- The guard helps MORE at cap=inf (−0.346 → −0.309, +0.037) than at cap=2
  (−0.311 → −0.303, +0.008): cap=inf has the deep, high-drift redeems for the
  guard to trim. But cap=2 already bounds the wait to ≤2 bars, so drift is
  already small and the guard has little left to remove → cap=2+slip100 (−0.303)
  ≈ cap=2 alone. The two controls are largely **redundant**, not additive.
  Neither rescues the NULL: every (cap, slip) cell is net-negative.

---

## TASK 3 — Regime flip (bull / high-vol)

Scanned all rolling 2-week windows for close-to-close % change and realized vol
(`scripts/redeem_regime_scan.py`). The corpus is **mixed, not pure bear** —
5/10 windows net-up. Strongest of each regime (NOT manufactured):

- **Most BULLISH: 2026-04-13..04-27, +11.39 %** (vol/bar 0.092 %, 68 % win) — a
  genuine bull leg.
- **Highest VOL: 2026-06-01..06-15, vol/bar 0.144 %** but **−10.87 %** (a
  high-vol *down*-leg; the corpus has NO high-vol *up*-leg — stated plainly).

Cap sweep net-R/trade:

| regime window | cap0 | cap1 | cap2 | cap3 | inf | best |
|---|---:|---:|---:|---:|---:|:--:|
| BULL 2026-04-13..04-27 (+11.4 %) | **−0.173** | −0.276 | −0.278 | −0.278 | −0.248 | **0** |
| HIVOL 2026-06-01..06-15 (vol 0.144 %) | **−0.134** | −0.180 | −0.179 | −0.210 | −0.231 | **0** |

**The finding does NOT flip.** In the most bullish AND the highest-vol window:
- ALL caps stay **net-NEGATIVE** (uncapped/redeem does NOT become positive).
- **cap=0 (no-redeem) is the clear least-bad**, and net-R degrades monotonically
  as the cap loosens — the OPPOSITE of a cap≈2 optimum. High vol / strong trend
  makes redeem latency-drift WORSE (bigger adverse fills on the delayed entry),
  so the tightest cap wins. gross_R/trade is positive in both (+0.20–0.22) but
  fees still sink net. `plan_skip` reason `fees_too_high_for_risk` dominates the
  funnel — fees, not the cap, are the gate.

---

## Confidence & N

- **Confidence: HIGH on the NULL** (no profitable cap), **MODERATE on the
  optimal-cap claim** (cap is regime-noise). 8 windows total (6 lockbox-split +
  2 regime), bull AND high-vol regimes covered, prior 2-window numbers
  reproduced exactly.
- **N: modest per window** (walked trades 19–44 per cap; pooled 152–210 across 6
  windows). The funnel is dominated by `plan_skip` (v2 fee/risk gate) — most
  score+PA fires never become R-trades, which is itself the fee story.
- Corpus is a single asset (BTC) over ~11 weeks at one vol regime band
  (0.07–0.14 %/bar). A truly different vol regime (≥0.3 %/bar sustained) is NOT
  in this corpus; the high-vol slice here is the closest available.

## Final recommendation

**Do NOT ship a redeem cap as a profit lever.** If a cap is set at all, set it to
*bound latency* (cap small, 0–2) for risk-hygiene reasons, NOT for expectancy —
the data does not support any cap as net-positive. cap=0 is the safest default
(least-bad in train + both regimes, removes all latency-drift). The redeem cap is
**confirmed NOT the money lever** (prior memos `bitunix-bull-starvation` /
`fee-model-reconciliation` stand: the lever is gross edge / regime, gated by
fees). **Slippage guard: does NOT beat the bar cap and does not rescue the NULL**
(best cell cap=2+slip=100 = −0.303, +0.008 R over cap=2; tight guards hurt;
redundant with the cap). Additive tool retained for hygiene, not as a lever.

## Artifacts (branch bitunix-redeem-sim-2026-06-22)

- `scripts/redeem_goal_batch.py` — 6-window cap-sweep driver
- `scripts/redeem_goal_aggregate.py` — train/validate pooling
- `scripts/redeem_regime_scan.py` — bull/vol window finder
- `scripts/redeem_slip_sweep.py` — slippage-guard sweep (IN-ENGINE; `--cap 2|inf`)
- `scripts/backtest_bitunix_confluence.py` — `max_slip_pt` guard added (additive, default-off)
- `tests/test_run_redeem_sim.py` — +5 slippage-guard tests
- per-window JSON: `scripts/_redeem_goal_out/`
