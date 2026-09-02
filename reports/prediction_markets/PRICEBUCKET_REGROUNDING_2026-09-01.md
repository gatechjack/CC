# Price-Bucket Re-Grounding (Stage-5 loss_grounding) — FINDINGS + VERDICT

**Date:** 2026-09-01 (23:45–00:01Z). **Author:** code agent. **Mode:** READ-ONLY. Nothing built, deployed,
restarted, or written. JACK-MLB remained ARMED + TRADING throughout; not disarmed.
**Branch:** `pm-pricebucket-reground-2026-09-01` (off `pm-multiaccount-2026-09-01 @ f1e28cc`).
**Runners (cc\ scratch, untracked operational record):** `pm_reground_state_ro`, `pm_reground_recon_ro`,
`pm_reground_ground_ro` (pass 1), `pm_reground_ground2_ro` (pass 2).

---

## ★ VERDICT — stated plainly

**The price-bucket "edge" does NOT survive loss-grounding. It is an F-1 measurement artifact, not a real
per-dollar edge. This kills the price-filter line of work.**

The 2026-08-31 finding — return-per-dollar (rpd) falls monotonically with entry price, longshots appearing to
pay +1.6…+2.6 per dollar — was filed as a HYPOTHESIS explicitly gated on this re-grounding. Re-grounded, the
apparent edge collapses to ~0 or negative in **every whale whose /activity I could fully cover**, and the
correction is monotonic in price (largest at the low buckets) — the exact signature of the F-1
held-to-worthless loss omission, not of a price edge. The method is trustworthy: the calibration whale
**evanng reproduces the documented ~63% loss omission (I measure 67%)**.

This is the good kind of negative result: it prevents building a copy-selection price filter that would have
been chasing dropped-loser inflation. **Do not build a longshot/price filter on the strength of the raw
number.** Keep loss-grounding where it already lives — feeding Analyze (Stage 5's actual purpose).

---

## Method (reuses the deployed Stage-5 machinery)

- **BEFORE** = `pm_closed_position` (the study's exact source; `pnl_suspect=0, won NOT NULL, cost_basis>0`),
  bucketed by `avg_price` into `[0,.2) [.2,.4) [.4,.6) [.6,.8) [.8,1.0]`, `rpd = Σrealized_pnl / Σcost_basis`.
  My pooled BEFORE reproduces the study exactly (mlb `[0,.2)=+1.65 … [.8,1]=+0.046`; ufc `+2.58 … +0.077`).
- **A_only (the dropped set)** = held-to-resolution decisions from **LIVE /activity**, resolved via **gamma**
  (the resolution authority, PM_REQUIREMENTS R3), that are ABSENT from `pm_closed_position`. This is
  `loss_grounding`'s method verbatim: aggregate BUY/SELL size per `(condition_id, outcome_index)`; a decision
  is HELD iff net long `> max(0.5, 1% of buy)`; category filter via the SAME `derive_category_from_slug`
  tier-1 deriver `_row_category` uses (unknown-slug rows fall OUT → A_only is a conservative LOWER bound).
- **Extension beyond the deployed module:** `loss_grounding` returns win/loss *counts*; I added the per-decision
  cost/return needed for rpd. For an A_only decision: `avg = Σusdc_size(BUY)/Σsize(BUY)`, `held = buy−sell`,
  `cost = held×avg`, `pnl = held×(1−avg)` if the gamma winner else `−held×avg` (= exactly the /closed-positions
  `size×(cur−avg)` convention: a held-to-worthless loser contributes rpd = −1). **AFTER** = BEFORE ∪ A_only.
- **Only /activity + gamma hit the network** (BEFORE comes from the DB) — this minimised load on the shared
  prod IP the armed engine's poller uses. No Cloudflare blocks; engine arm state verified unchanged after both
  passes.

---

## Calibration — the method is trustworthy

**evanng (ufc, 100% coverage, untruncated):** closed losses = 24, A_only losses = 48 →
**loss-omission = 48/72 = 67%**, matching the `loss_grounding.py` docstring's measured ~63% for this exact
wallet. Its per-bucket correction is textbook monotonic:

| bucket | BEFORE rpd | AFTER rpd | Δrpd |
|---|---|---|---|
| [0.0,0.2) | +0.105 | **−0.737** | −0.842 |
| [0.2,0.4) | +0.308 | −0.334 | −0.642 |
| [0.4,0.6) | +0.385 | −0.013 | −0.397 |
| [0.6,0.8) | +0.171 | +0.005 | −0.166 |
| [0.8,1.0) | +0.055 | −0.040 | −0.094 |

The correction is 9× larger at the low bucket than the high bucket — precisely what the mechanism predicts (a
dropped loser at entry 0.1 swings a bucket of 9×-payoff winners hugely; at entry 0.9 it barely moves a bucket
of 0.11×-payoff winners). This is the artifact, caught red-handed.

---

## Per-whale before→after (the well-covered whales carry the verdict)

"Coverage" = fraction of the whale's `pm_closed_position` keys re-found in its /activity window (the honest
completeness measure — better than the raw truncation flag, since a whale can exceed 5000 activity rows yet
still cover 95% of its closed era). **cov≥90% = a trustworthy, near-complete grounding.**

### mlb (LIVE category)

| whale | cov | A_only losses / closed losses (omission) | headline bucket move |
|---|---|---|---|
| **SDTrading** (LIVE) | 100% | 495 / 31 → **94%** | `[.4,.6) +0.885 → −0.003`; win-rate 0.93→0.48 |
| **xifutloong3** (LIVE) | 100% | 75 / 46 → 62% | `[.4,.6) +0.611 → +0.004` |
| **0x26b4** | 95% | 77 / 145 → 35% | `[.2,.4) −0.428 → −0.581`; `[.4,.6) +0.044 → −0.064` |
| **0x9a8c** | 100% | 15 / — → small | already NEGATIVE pre-grounding (not F-1-inflated) |
| 0x684baa57 (LIVE) | 30% ⚠ | 57 / 17 (lower bound) | `[.4,.6) +0.912 → +0.499` (under-corrected) |
| BetMechanic (giant) | 0% ⚠ | ungroundable | raw `[0,.2)`=−0.282 already realistic |

**SDTrading is the headline:** /closed-positions dropped **94% of its losses** — it screens as a ~93%-win-rate
god and is truly ~50%. xifitloong3 similar (62%). Both are LIVE-copied whales. This does not change the copy
decision (we copy the whale's *moves*, not its win rate), but it is why Stage-5 grounding exists.

### ufc (NEXT go-live category)

| whale | cov | A_only losses / closed losses (omission) | headline bucket move |
|---|---|---|---|
| **evanng** | 100% | 48 / 24 → 67% (calib) | `[0,.2) +0.105 → −0.737` |
| **Kh4mz4t** | 100% | 42 / 107 → 28% | `[0,.2) +0.868 → +0.041` |
| **STC14** | 100% | 27 / 17 → 61% | `[.6,.8) +0.444 → +0.006` |
| 4751346 (giant) | 7% ⚠ | 15 (lower bound) | raw `[0,.2)`=−0.328 already realistic |
| MadeiraIsland | 82% | 41 / — (near-complete) | `[0,.2) −0.262 → −0.699` |

**Every positive BEFORE bucket in a well-covered whale collapses toward 0 or negative. The direction is
unanimous** — of ~60 non-trivial (whale, bucket) cells, Δrpd ≤ 0 in all but a few tiny-n cells where one
recovered win nudged +0.002…+0.04 (noise).

---

## Completeness / coverage bound (the load-bearing caveat)

**Truncation is pervasive and it is why single-window /activity cannot ground the whole category.** A whale
active enough to have 100–600 scoreable closed positions is usually active enough (across all categories) to
exceed the /activity 5000-row window. Of 17 whales grounded, only **7 whale-category slices reached cov≥90%**
(SDTrading, xifutloong3, 0x9a8c, 0x26b4[95%], evanng, Kh4mz4t, STC14). The rest are LOWER BOUNDS.

**Crucially, truncation can only STRENGTHEN the verdict.** The unaccounted decisions are OLDER activity, and
F-1 drops *losers* (held-to-worthless), not winners (winners are captured by /closed-positions). So any
decision beyond the window that is absent from /closed-positions is overwhelmingly a LOSS → more grounding can
only push low-bucket rpd **lower**. Every grounded number here is therefore an **upper bound** on the true
edge, and the upper bound is already ~0 or negative.

**What I can and cannot claim, honestly:**
- I CAN claim: wherever coverage is high, the apparent edge is gone. Unanimous, mechanism-confirmed, calibrated.
- I CANNOT claim a specific grounded number for a category's `[0,.2)` bucket with full coverage — the giants
  (BetMechanic 687 of mlb's 948 longshot positions; 4751346) are un-groundable in one window. Reporting
  "mlb longshots grounded = X" as a fact would be the same unstated-coverage mistake in a new costume. The
  pooled numbers below are labelled ALL (lower bound) vs COMPLETE-only (cov≥90%) so the coverage is never
  hidden. To fully ground the giants you would need /activity paging past offset 5000 (the module caps at 10
  pages) — a deeper-window follow-up, not required to reach this verdict.

### Pooled over grounded whales (coverage stated, NOT the full category)

```
mlb  COMPLETE-only (cov>=90%)         mlb  ALL grounded (incl. lower-bound partials)
 [.2,.4) -0.565 -> -0.565  (n=7)       [.2,.4) -0.040 -> -0.181
 [.4,.6) -0.230 -> -0.241  (n=80)      [.4,.6) +0.101 -> +0.047
 [.6,.8) -0.215 -> -0.238  (n=84)      [.6,.8) +0.001 -> -0.073
                                       [.8,1]  +0.036 -> +0.019
ufc  COMPLETE-only (cov>=90%)         ufc  ALL grounded (incl. lower-bound partials)
 [.2,.4) +0.302 -> -0.357  (n=12)      [0,.2) +1.156 -> +0.874   (LOWER BOUND; giants uncovered)
 [.4,.6) +0.440 -> +0.159  (n=39)      [.2,.4) +0.746 -> +0.520
 [.6,.8) +0.444 -> +0.006  (n=54)      [.4,.6) +0.490 -> +0.413
 [.8,1]  +0.058 -> -0.018  (n=19)      [.6,.8) +0.099 -> +0.028
```
The COMPLETE-only mlb `[0,.2)` bucket is essentially empty — the fully-covered mlb whales don't bet mlb
longshots; the study's pooled +1.65 was a thin number dominated by a few whales, the largest of which
(BetMechanic) is already −0.28. The COMPLETE-only ufc buckets span low→high and all collapse.

---

## Category verdicts — which have enough data

- **mlb — SUFFICIENT for the kill verdict.** Multiple well-covered whales; the mid buckets `[.2,.6)` (where
  the mlb dollars and positions actually are) collapse unanimously; the study's longshot `[0,.2)` number was
  pool-dominated and every inspectable whale there is already negative or collapses. **No per-dollar price edge
  survives.** NOT sufficient to *resurrect* the `[0,.2)` bucket as a real number (giants un-groundable).
- **ufc — SUFFICIENT, and stronger at the low end.** Three fully-covered whales (evanng, Kh4mz4t, STC14) span
  the longshot buckets and all collapse; evanng goes negative. **No price edge survives; grounded ufc
  longshots trend negative.**
- Both categories: enough to say the edge does NOT survive. Neither: enough to state a positive, fully-covered
  grounded figure for the extreme-longshot bucket. Categories other than mlb/ufc were out of scope (per the
  task: mlb is live, ufc is next).

---

## The question behind the question — what would we DO with it?

**Because the edge does not survive, the price-filter capability is moot** — I am NOT designing it, and the
recommendation is not to build it. For completeness, its shape and collisions (had it survived):

- **Shape:** a copy-eligibility gate keyed on the copied leg's entry price — "skip copies with entry ≥ p*" or
  a longshot weight. It would sit in `live_driver`/`execution` alongside the existing gates.
- **What it would collide with:** (1) the **opposing-side rule** — a price gate that skips the favourite side
  could leave us systematically on the losing longshot side of a market the guard would otherwise balance;
  (2) the **sizing modes** (fixed/contracts/whale-proportional) — a price weight is a *second* size axis and
  would need a defined interaction, not two independent multipliers; (3) the **liquidity gate** — longshots
  are exactly where top-of-book is thinnest, so a longshot-tilt would fight the depth gate and raise no-fill/
  slippage skips. None of this matters now — the premise is dead — but it shows a price filter was never a
  clean bolt-on.

**The ACTUALLY actionable output** (already the platform's design, now empirically reinforced): loss-grounding
materially changes a whale's apparent quality (SDTrading 94% of losses hidden; true win-rate ~50% vs screened
~93%). That is a WHALE-RANKING / promotion signal, and it is precisely what Stage-5 `loss_grounding` feeds into
**Analyze**. Keep it there. If anything is worth a follow-up, it is: **surface the loss-omission % on the
Analyze/promotion view** so a whale that screens well only because its losers are dropped is visible before it
is pinned or copied — not a price filter.

---

## What did NOT graduate, and why that is a win
The "edge is in price, not size" hypothesis does not graduate to a capability. It was the single most
loss-omission-contaminated number in the whale-proportional study, contaminated in exactly the direction that
flattered it, and grounding confirms the contamination was the whole signal. Killing it removes a tempting but
false lead and saves the build. The whale-proportional backlog note should be updated from "gated on Stage-5
re-grounding" to "re-grounded 2026-09-01 — edge does NOT survive; do not build a price filter."
