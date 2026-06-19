# Fee-model reconciliation (expected vs actual) + the maker/taker lever

**Date:** 2026-06-19
**Branch:** `fee-model-reconciliation-2026-06-19` (off origin/main `1c12d5c`)
**Mode:** READ-ONLY. NO prod/config change, NO deploy. Actual fees from already-recorded prod data (read-only SSH, `mode=ro`, 82fda13) — **no signed/live venue call**. Per §4.
**Why:** three diagnostics concluded scalp-longs lose net-of-fees; the operator challenged the fee MODEL. Verify it against what BitUnix ACTUALLY charged before trusting those conclusions.

> **VERDICT: the model OVERSTATES the fee drag — but the core conclusion survives.** The headline "net-taker −0.24R" used the wrong leg: real winning exits are MAKER (confirmed exact 0.0140%), and the entry leg is ~39% below published VIP3 (Fee Discount Card, real). Corrected **actual-effective net ≈ −0.13R** (vs the −0.24R taker headline). **Still negative.** The edge is NOT recovered by fixing the fee model: gross +0.06R < actual drag 0.19R. And even best-case **all-maker fees still net −0.06R** — because at a 0.30% stop, *any* fee schedule is a large R-drag. **The real lever is the stop distance, not the fee rate.** (Interim: one bear/quiet window; N=5 real-fill trades for the actuals — an exact per-trade accounting check, not a statistical one.)

---

## Part A — Expected vs Actual fee reconciliation

### What the model charges (`trading_corp/agents/strategies/trade_plan.py:FeeConfig`)
```
taker 0.0400% · maker 0.0140% · slippage 0.0050%/leg
round_trip = entry_fee + exit_fee + 2×slippage
  _RT_TK (taker entry + taker EXIT) = 0.0900%   ← the "net-taker" headline
  _RT_MK (taker entry + maker EXIT) = 0.0640%   ← the "net-maker" line
net_r = gross_r − round_trip × (entry / risk_per_unit)
```
- Fee is on **NOTIONAL** (price), expressed in R via `/risk_per_unit` — **no margin/leverage conflation** (the 25× never enters). ✓
- Entry assumed **taker always** (B2 maker-entry off). Exit modeled as taker (_RT_TK) or maker (_RT_MK).
- Rates = **published VIP3** (0.0400/0.0140). Docstring claims "VIP3 with Experience Card" but the taker value is the *un-discounted* published rate — mislabeled.

### What BitUnix actually charged (5 real-fill live trades, `extra_json.entry_fee_usd/exit_fee_usd` via the #1 signed-fetch auto-book)
| order | side | entry notional | entry fee $ | **entry rate** | exit role | exit fee $ | **exit rate** |
|---|---|---:|---:|---:|---|---:|---:|
| e1758fc9 | SHORT | $24.36 | 0.00775 | 0.0318% | **MAKER** (TP1) | 0.00271 | **0.0140%** |
| 679c15e2 | SHORT | $24.42 | 0.00767 | 0.0314% | TAKER (SL) | 0.00769 | **0.0400%** |
| a919d1f5 | SHORT | $24.21 | 0.00767 | 0.0317% | TAKER (SL) | 0.00768 | **0.0400%** |
| 7d1a78dc | SHORT | $48.41 | 0.01770 | 0.0366% | MIXED (TP1+TP3) | 0.01276 | 0.0289% |
| cb6b4d4a | SHORT | $237.86 | 0.04635 | 0.0195% | TAKER (SL) | 0.04652 | 0.0200% |

### The four checks
1. **Maker/taker assignment — model overstates winning exits 2.9×.** Real TP exits (reduce-only LIMITs) fill at **exactly 0.0140% (maker)**; SL exits (market) at **exactly 0.0400% (taker)**. The clean exact-rate match confirms the roles. The model's headline `_RT_TK` assumes a *taker* exit for everything → for the ~62% of trades that exit at TP, it overstates the exit leg 2.9× (0.0400 vs 0.0140).
2. **Fee Discount Card — confirmed, real.** Every entry leg is BELOW published VIP3 taker: blended **0.0243%** vs 0.0400% (~39% off). The model uses 0.0400% → overstates the entry leg. (Caveat: the entry discount is *inconsistent* across trades, 0.0195–0.0366%, plausibly a min-fee/rounding artifact on the tiny ~$24 notionals — see data-quality.)
3. **Notional vs margin — clean.** fee/notional yields 0.01–0.04% (sane); if it were on margin (notional/25) the rate would be ~25× higher. Both model and actuals are notional-based. ✓
4. **Round-trip — structure correct**, the error is the per-leg RATE (entry not discounted; exit assumed taker not maker).

### Corrected net-per-fire
Fee-drag in R = round_trip% / stop% (0.30% stop → ×333). Actual-effective round-trip = discounted entry 0.0243% + win-weighted exit (62% maker + 38% taker = 0.0237%) + slippage 0.0100% = **0.0582%** → drag **0.194R**.

**Corrected net ≈ +0.063 − 0.194 = −0.13R** (vs model taker −0.24, model maker −0.15). **Still negative.** Direction stands; magnitude was overstated by ~0.11R at the taker headline.

---

## Part B — Maker/taker lever (recompute)

`scripts/fee_recon/fee_recompute.py` (gross +0.063R, win 62%, 0.30% stop):

| scenario | round-trip% | drag (R) | **net/fire (R)** |
|---|---:|---:|---:|
| (d) all-taker [model headline `_RT_TK`] | 0.0900 | 0.300 | **−0.237** |
| model maker-exit [`_RT_MK`] | 0.0640 | 0.213 | −0.150 |
| **(b) ACTUAL-effective** [disc entry + win-weighted exit] | 0.0582 | 0.194 | **−0.131** |
| (c) all-maker [B2 entry ON, both legs maker] | 0.0380 | 0.127 | **−0.064** |

- **Recoverable by fee-CORRECTNESS** (taker-headline → actual-effective): **+0.106R**. This is pure model overstatement — using the real discounted entry + maker TP exits.
- **Further by all-maker entry (B2 on)**: **+0.067R** more — *if* maker entries fill.
- **Residual = genuine stop/edge problem.** Even all-maker nets **−0.064R**: the +0.063R gross is smaller than even the cheapest realistic round-trip (0.127R). At a 0.30% stop, fees are 1/0.003 = 333× the per-unit risk, so *no* fee schedule makes this gross edge profitable. **The dominant lever is the stop distance** (a wider stop cuts fee-drag-in-R proportionally), not the fee rate.

### B2 maker-entry flag (NOT a recommendation)
Maker entries would save ~**0.067R/fire** on fees — material — but the FeeConfig itself notes the tradeoff: maker (POST_ONLY) entries risk **non-fill / missed entry** (worsening the late-entry drag). And even with it, best-case is still −0.064R on this window, so **B2 is a fee-efficiency win, not a profitability fix here.** Decision flagged for the operator; not flipping.

---

## Data-quality findings (flagged — affect trust in live PnL/fees going forward)
- **Maker/taker role is NOT recorded** in any DB field — only inferable from the exact fee rate. Worth capturing explicitly.
- **Fee capture is partial:** only the 5 Jun-18–19 trades have real entry+exit fees (signed-fetch). Jun-14–16 have entry-only; **Jun-17 trades recorded fee=0** (resolved via position-polling, not signed-fetch). Pre-E2.5 live trades are mislabeled `execution_mode='paper'`.
- **Entry vs exit qty dust gap** (e.g. 0.000377 entered, 0.0003 closed) — exchange min-lot rounding leaves untracked dust.
- **e1758fc9's TP1 maker fill was auto-booked as a "stop"** (same P2 sign-bug territory) — it was a 0.0140% maker fill at the TP1 limit price, not a stop.

---

## Caveats
- **N=5** real-fill trades for the actual side — exact per-trade accounting, but the single "effective entry rate" (0.0243%) is uncertain (inconsistent 0.0195–0.0366%, min-fee artifact possible on ~$24 notionals). The maker/taker EXIT rates are exact (0.0140/0.0400) and robust.
- Slippage kept at the model's 0.0050%/leg; real SL slippage can be larger on fast bars (per the stop-slippage analysis) — so the actual-effective drag could be modestly higher on SL-heavy outcomes.
- The net-expectancy recompute is still **one bear/quiet window** — interim, not a cross-regime verdict.

**Hard stops honored:** read-only; no prod/config/deploy; no signed/live venue API (actuals from recorded DB only); the model was not declared right/wrong without the per-trade reconciliation (shown above — it's overstated, quantified); no cross-regime verdict (interim, regime caveat flagged); no git stash; no polymarket.
