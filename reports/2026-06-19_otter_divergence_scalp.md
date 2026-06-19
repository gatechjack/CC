# Otter divergence-scalp — the operator's real model (tight stop, scalp R:R)

**Date:** 2026-06-19
**Branch:** `otter-divergence-scalp-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY research. NO prod/deploy/live. §4. Corrected fees. Otter-primary, Cypher banned.
**Setup tested = the operator's:** enter at the Bear/Bull Divergence signal, **stop just beyond the local low/high**, scalp R target — NOT the loose ATR/2.5R model the prior null used.

> # VERDICT: your *read of the signal is correct* — but it is not bot-able from this data.
> Step 0 proves the divergence genuinely **catches the local extreme** (adverse excursion ≈ −0.01%, R:R ~3.6–4.3) — exactly what you described. The operator-model backtest is **robustly net-positive** at the signal bar. **BUT** the corpus `bull/bear_divergence` column is a **future-confirmed pivot (100% of fires are the ±3-bar extreme)** — it repaints: the edge lives entirely in the pre-confirmation window (entry k=0–1), and **dies at k=2**, which is the earliest a "once per bar close" alert can actually fire. So **no MECHANICAL / Otter-Bot fee-clearing edge survives honest entry timing.** Your live profitability is consistent with a **discretionary** entry at the real-time low + your **structure** filter — which a mechanical backtest on a repainting column cannot replicate or validate. (Interim: one bear/neutral window.)

---

## Step 0 — model-free MFE/MAE (your claim, measured): CONFIRMED
Entry at signal close, median excursions:

| signal | stop% (local extreme) | MAE @h1 | MFE @h10 | **R:R @h10** |
|---|---|---|---|---|
| bear_divergence | 0.053 | **−0.01** | +0.23 | **4.30** |
| bull_divergence | 0.062 | **−0.01** | +0.23 | **3.65** |
| super_buy_high | 0.054 | −0.06 | +0.19 | 3.59 |
| otter_buy/sell, top/bottom | ~0.20 | ≈ −MFE | — | ~0.6–0.8 (no edge) |

The divergences (and super-high) **barely go against you** with a stop just beyond the local extreme — a genuine, measurable property. The other Otter triggers don't have it.

## Step 1 — operator-model backtest: robustly positive at the signal bar
Tight local-extreme stop + scalp R, corrected fees, net-per-fire. Across K∈{1,3,5,10} × buffer∈{0.03,0.05,0.10%} × R∈{1.5,2,3}, **net is positive on BOTH train and validate** at entry=close *and* entry=next-bar-open (≈ identical on liquid 3m). E.g. bear_div next_open K5/0.05%/R2: **train +0.38 / validate +0.39**. A robust neighborhood, not a peak. (Note K1=K3=K5 give identical results → the signal bar *is* the local extreme — the first repaint tell.)

## Step 2 — the repaint proof (decisive)
- **(A) Pivot structure:** **100%** of divergence fires are the local extreme over ±3 bars (81% over ±5). The column marks a bar that is, by construction, a pivot confirmed by *future* bars — not knowable in real time at that bar.
- **(B) Entry-delay (pivot-anchored stop):** positive at k=0 **and k=1**, then **collapses at k=2** (win rate 60–71% → 25–47%; net → negative) and craters by k=3–5. The pivot needs ~2–3 right bars to confirm — exactly when the live "once per bar close" alert can first fire — and the edge is gone by then.

## Step 3 — LOCKBOX (Jun 1→19, one touch)
| signal | k (entry) | n | win% | net R | |
|---|---|---|---|---|---|
| bull_divergence | 0–1 (look-ahead) | 79 | 73.4 | **+0.79** | repaint, non-tradeable |
| bull_divergence | **2 (honest)** | 79 | 34.2 | **−0.10** | dead |
| bear_divergence | 0–1 (look-ahead) | 56 | 75.0 | **+0.89** | repaint, non-tradeable |
| bear_divergence | **2 (honest)** | 56 | 48.2 | **+0.42** | positive — **but** train/validate-NEGATIVE (k2 train −0.19 / val −0.09) ⇒ fails selection, uncorroborated (N=56) |

bull = dead at honest entry; bear's honest-entry lockbox positive is the same uncorroborated-lockbox trap (lost on the optimization set), not a validated edge.

## Step 4 — S/R confluence (your structure edge, proxied): doesn't rescue it
Requiring the divergence to fire at a prior swing S/R (20–200 bars back, <0.3%), honest k=2 entry:
- **bull_divergence:** still negative (train −0.22 / val −0.30 / lockbox −0.30).
- **bear_divergence:** train **−0.006** (≈ breakeven) / val +0.15 / lockbox +0.08 — *better*, but train still <0 and **N collapses to 35/17/12** (too small to trust). Does not clear the discipline.

## Why you can be profitable while the bot can't (the honest reconciliation)
This is not a claim that your trading is fake — the data says the opposite about the *signal*. The gap is **timing + discretion**:
1. **The column repaints; your alert lags it.** The corpus marks the pivot bar; your "once per bar close" alert can only fire ~2–3 bars later, at confirmation — where the mechanical edge is gone.
2. **You enter at the real-time low; a bot enters at confirmation.** A skilled trader *anticipates* the divergence as price makes the low (with your structure read) and enters near the k=0 price — which is exactly where the edge (+0.4 to +0.9R) lives. A mechanical rule cannot anticipate; it must wait for confirmation (k≥2, negative).
3. **You filter by structure; the test takes every fire.** Your order-block / supply-demand / S&R selection (only a crude proxy here) plausibly keeps the good setups — and the bear_div+S/R result *moved toward* positive (train −0.19 → −0.006), hinting your real filter does work, just below the N/validation bar this corpus can support.

So: **no validated, mechanizable, fee-clearing divergence-scalp edge in this data** — but the signal genuinely marks extremes, and your discretionary, structure-filtered, real-time-low entry is the part the backtest cannot model and is the likely source of your live edge.

## What would make it validatable / bot-able
- A **non-repainting real-time signal** for the same idea (an indicator that fires AT the low without future-bar confirmation — e.g. a momentum/CVD cross at a swing low), so the entry is honestly k=0.
- A **faithful structure filter** (real order-block / supply-demand zones, not the swing proxy) — the bear_div+S/R nudge suggests this is the lever; needs the actual zones + more data.
- A **non-bear / transition regime** window (this is one bear/neutral tape) and **more samples** (the native ETL accumulates forward data).

**Scope:** one bear/neutral window; small N on the honest/filtered subsets. Candidate-search + honest read, **not live-blessed; nothing applied.**

**Hard stops honored:** research only, nothing deployed/traded; ZERO Cypher; LOCKBOX is the reported headline (no train number as the verdict); corrected effective fees (not all-taker); no cross-regime/live verdict (interim, one window); no git stash; no signed/live API; no polymarket.
