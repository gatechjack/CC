# Range-Fade Method — Skill-Faithful Falsification (3m MTF → cross-regime XTF)

**Date:** 2026-06-19   **Status:** CONCLUDED — range-fade parked.   **Type:** read-only backtest research. No live code, no wiring, no deploy.

> **READ THIS FIRST — METHOD, not BOT.** The cross-regime run executes on **15m**, not 3m. 15m
> execution ≠ 3m scalp (different hold, fee-in-R, trade count). A positive result would say *"the
> AlexO range-fade **method** can pay in a non-bear regime"* — it would **never** say *"the 3m
> bitunix bot will pay."* There is no native 3m bull data and resampling cannot manufacture it.
> Nothing in this report transfers to the 3m strategy.

## Question

The `/goal` search mechanized the operator's SMC-chart thesis — "bounce between the boxes" (fade
multi-touch S/R, stop on the break). The first mechanical version (`range_scalp.py`, 3m structure)
came back NULL. The AlexO **market-structure skill** says that null was *expected*: confirming
structure on 3m is a strawman (the author's floor is 15m). So we asked: **does range-fade lose
because the IDEA is wrong, or because the TIMEFRAME / REGIME was wrong?**

Two skill-faithful runs answer it.

## Run 1 — MTF (define 1H / structure+confirm 15m / execute 3m), bear tape (Mar 30 – Jun 19 2026)

`range_scalp_mtf.py`. Honesty rails: non-repainting two-candle 15m swings (known only at the
confirming candle's close), ER regime gate, **acceptance** entry (15m body-close back inside, not
the deviation), median/far TP with 50% partials + breakeven runner, corrected fees, walk-forward
TRAIN≤May15 / VAL≤Jun1 / LOCKBOX>Jun1, **k=0 vs k=1 repaint headline**.

- Regime mix: 63% ranging (ER≤0.30) / 84% (ER≤0.45) — the tape *did* range.
- **0 positive-both** candidates. Trustworthy buf0.10% k=1 net ≈ **−0.30 to −0.45R**, stable across
  k=0/k=1 → **not repaint**.
- Break-rate ~38–46%. Longs win 44–48% in TRAIN then **collapse to 27–30% in VALIDATE** (net −1.5
  to −1.7R) — the §5 faint-gross-edge problem resurfacing in range clothing. Neither side real.
- Verdict: **repaint-clean but REGIME-INCONCLUSIVE** — a bear tape can't answer "does the idea pay."

## Run 2 — XTF cross-regime (define 1H / confirm 30m / execute 15m), Nov 2025 – Jun 2026

`range_scalp_xtf.py`. Same logic lifted one notch; **every constant bar-count-identical to Run 1, no
grid expansion**. Window bound by the shortest leg (15m execution starts 2025-11-01) → ~7.5 months
spanning a full up/down cycle. Walk-forward TRAIN≤Mar1 / VAL≤May1 / LOCKBOX>May1. Self-gated on
regime mix before any PnL.

**Regime mix (load-bearing — reported before PnL):**

| ER threshold | bull | range | bear |
|---|---|---|---|
| ≤0.30 | **18.1%** | 62.6% | **19.3%** |
| ≤0.45 | 7.6% | 83.1% | 9.4% |

Directional split **balanced (bull ≈ bear ≈ 19%)** — a genuinely regime-fair tape. The bear-only
confound is removed. Gate passed (non-bear 80.7%).

**Result — still 0 positive-both, now REGIME-FAIR:**

| config (k=1, buf0.10%) | full net | TRAIN | VAL | break% |
|---|---|---|---|---|
| ER≤0.30 **sell** | −0.507 | −0.604 | −0.564 | ~50–54 |
| ER≤0.45 **sell** | −0.564 | −0.642 | −0.613 | ~52–53 |
| ER≤0.30 **buy** | −1.045 | −1.623 | −0.303 | ~45–52 |
| ER≤0.45 **buy** | −0.901 | −1.341 | −0.339 | ~45–52 |

(Ignore the 0.05%-buffer rows and the −3.1/−5.4 outliers: tiny-risk/tight-stop fee-in-R degeneracy,
same artifact flagged in Run 1. Read buf0.10% only.)

- **Not repaint:** net is already negative at k=0 and stays/worsens at k=1.
- **Shorts are the clean read:** ~−0.5 to −0.65R/trade, consistently negative in *both* train and
  val. No positive cell anywhere.
- **Break-rate ROSE to ~50%** (vs ~40% on the bear tape) — the mechanism.

## Verdict

The question is answered cleanly. The mechanical range-fade loses and it is **not** the timeframe
(3m→30m/15m didn't help), **not** the regime (balanced bull/range/bear didn't help), and **not**
repaint (negative at k=0 already).

**Mechanism:** even ER-filtered "ranges" break out ~50% of the time, so a mechanical fade cannot
reach the median often enough to clear fees. The *mechanizable skeleton* of the AlexO range-fade
method — two-candle swings + ER gate + acceptance entry + median/far TP — **has no edge on BTC
across a full cycle.**

**What this does and does not falsify:**
- ✅ Falsifies the **rules-complete mechanical** range-fade on BTC, regime-fair, repaint-clean.
- ❌ Does **not** falsify the **discretionary** method as a skilled trader runs it. The skill itself
  says the edge lives in judgment it calls essential — volume-profile/POC confluence, reading "real
  range vs about-to-break," manual range definition — which a rules-only backtest structurally
  cannot capture. The skill stays a **framing** tool, not an automatable signal (exactly what its
  own "Boundaries" section states).
- ❌ Says **nothing** about the 3m bot (METHOD-not-bot pin).

## Artifacts
- `scripts/range_scalp/range_scalp.py` — original 3m mechanical (prior null).
- `scripts/range_scalp/range_scalp_mtf.py` — Run 1 (3m-exec MTF, bear tape).
- `scripts/range_scalp/range_scalp_xtf.py` — Run 2 (15m-exec cross-regime).

## Follow-ups considered, NOT taken (parked)
- **Flip the thesis — test breakouts/continuation.** The ~50% break-rate hints the edge may be on
  the *break*, not the fade (the skill's whole TREND regime is untested). Genuinely new study.
- **Native bitunix robustness re-run.** Low information: 34d, no native 15m/1h, same bear regime.
  Run only if a future result is worth venue-checking.
- **Add discretionary-proxy confluence** (volume-profile POC, hard 1H trend filter). Rejected as
  p-hacking risk — each added factor is a mechanical proxy for a judgment the skill calls
  discretionary; treat any pass skeptically.
