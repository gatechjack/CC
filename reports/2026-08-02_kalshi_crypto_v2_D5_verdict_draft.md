# D5 — Phase-2 Verdict DRAFT (kalshi_crypto_v2)

**Date:** 2026-08-02 · **Status:** recommendation draft — **the operator rules.** Evidence only; no autonomous decision. Read-only research throughout; no order/placement surface; old `kalshi_crypto` division untouched; branch `claude-2026-08-02` off prod-live `dafe60b` (pushed, UNMERGED).

## The question
Does the Bitunix SFP/regime signal — or any signal we could build on the historical corpus — carry **executable EV** on Kalshi crypto binaries (15m up/down + hourly ladders)?

## One-line answer (recommendation, operator rules)
**No, for the 15m up/down instrument.** The model ≈ the market (no Brier skill), and **every apparent EV edge died once look-ahead and realistic execution frictions were removed.** Kalshi 15m crypto binaries are, at the retail-accessible level, **~efficiently priced.** The hourly **ladder** is a different instrument and is **internally consistent** (no B-L arbitrage); its distributional structure is the only corner not yet closed, but nothing found there is an edge either. **Recommendation: do NOT stand up a directional 15m trading division on this signal.** The deliverables that hold value are the clean negative (capital not deployed into an efficient market), the diagnostic infrastructure, and the canonical corpus.

## Evidence ledger
| Study | Tested | Finding | Status |
|---|---|---|---|
| T4 retro / S4 v1 (CatBoost+Platt) | model vs market, Brier | Brier_model ≈ Brier_market; skill ±0.02 = noise (ETH only marginally +) | **model ≈ market** |
| EV forensic (taker) | +EV at executable traded prices | taker@traded NEG/~0 (BTC/SOL/XRP neg, ETH ~0); taker@quote +ve everywhere | **artifact ruled** |
| Maker resolution 1a | is the maker +ve spread capture? | null controls all LOSE; model side adds +0.05..+0.10 → NOT spread capture | signal-dependent |
| Maker resolution 1b | survives pessimism? | only **ETH** survived (A +0.030 t2.5); BTC~0, SOL/XRP neg | one survivor |
| Mid-window calibration | overreaction-fade? | **refuted by its opposite**: strong UNDER-reaction/continuation; survives window-clustered SE (t_clus 10-21) | diagnostic fact |
| Executable continuation | is the under-reaction harvestable? | taker@traded +0.06..0.13 (t2.8-4.9) holdout, all 4 | looked harvestable |
| Continuation latency | survive 1-min delay? | **collapses to ~0/neg at m+1**, neg at m+2/3 — intra-minute artifact | **edge dead** |
| ETH maker realism gate | survive no-lookahead + realism? | ETH +0.030 → +0.022 (t1.8, realism only) → **−0.002 (t−0.2)** (+pessimism) | **survivor dead** |
| T5 basis | Binance→RTI label noise | directional-window disagreement 8-10%; ~3% on big moves | measured; attenuates only |
| S5 ladder B-L | ladder arbitrage / consistency | overround ~0.96-1.00; **0 arbitrageable** sum/monotonicity violations | consistent |
| S5 ladder density | implied vs realized vol | first read implied 1h vol ~0.6-0.8× realized (both biases unquantified) | inconclusive |

## Every death, with its mechanism
1. **Taker "+$0.03–0.09/contract" (S4 v1) → the stale first-minute quote band.** The entry candle's OPEN quote is degenerate (yes_ask ~0.999 / yes_bid ~0.000 = no two-sided market at the open tick) while real trades printed normally. Buying "at the ask" of that stale quote looked profitable; at real traded prices (`price_high`/`1−price_low`) the taker is negative/zero. **Operator-ruled dead.**
2. **Continuation taker edge (+$0.06–0.13, all 4, holdout) → intra-minute ordering.** The qualifying Binance move completes at minute m's close, but the minute-m fill price can predate it. Entering one minute later (strictly post-signal) collapses the edge to ~0/negative on all four; by m+2/m+3 it's the delayed fade. The contract catches up within ~1 minute ⇒ any Binance→RTI lead is **sub-minute, untradeable.**
3. **ETH maker survivor (+$0.030, t2.5) → same-minute-close knowledge in the resting level.** The resolution study rested at the *entry minute's own close* — unknowable when you place. Resting at the *prior* minute's close (honest) drops it to +0.022 (t1.8, already not significant); the full pessimism stack finishes it at −0.002 (t−0.2). **Operator-ruled dead.**
4. **Maker "spread capture" hypothesis → refuted (a rare death by promotion).** Null controls (random/always-yes/always-no) all LOSE, so the maker positive was *not* signal-independent spread capture — it was the model's weak directional signal. That signal then died at #3, but the mechanism is worth recording: the maker positive was real signal, just not executable.

The through-line: **every edge rode either a look-ahead (stale open quote, same-minute close, intra-minute fill) or the optimistic queue-free fill, and none survived honest constraints** — exactly what an efficiently-priced market produces.

## Surviving diagnostic facts (real, robust, NOT tradeable)
- **Model ≈ market** (Brier), settled and independent of the EV work.
- **The stale-first-minute-quote artifact** — a real microstructure feature (degenerate open quotes), and a permanent caveat for any open-tick analysis here.
- **Mid-window UNDER-reaction / momentum-continuation** (calibration): after a move in minutes 1–3 the contract-implied prob is less extreme than the outcome (gap ±0.16–0.18 at min 1–3, decaying to settlement), survives window-clustered SE (t_clus 10–21). Real as a *calibration* fact; non-executable (catches up <1 min).
- **T5 label noise measured:** Binance↔RTI direction disagreement 8–10% on directional windows, ~3% on large moves — every 15m study carries this; it attenuates edges, never manufactures them.
- **Ladder internal consistency:** overround ~1, zero arbitrageable B-L violations.

## Data assets built (durable, reusable)
Full 15m corpus with **traded-price OHLC** re-pulled (26,104 mkts, 0 err); Binance/Coinbase 1m (0-gap), Coinalyze 1h flow; **34k ladder strike-snaps**; S1 settlement engine; the S2 harness; leakage-safe S4 pipeline; and the S3 coverage accounting. Isolated lab DB; prod untouched.

## Recommendation (operator rules)
1. **Do not build a directional 15m trading division on this signal.** The efficiency result is strong and multiply-confirmed; deploying capital would be paying the spread into a fair market.
2. **Bank the diagnostic infrastructure + corpus** as the durable output. The clean negative *is* the deliverable — it prevents a bad allocation.
3. **Fine-flow: keep on HOLD / effectively moot** — it was a feature upgrade for a directional model the market beat four ways.
4. **If (and only if) you want to keep going, the frontier is the LADDER distribution, not 15m direction** — S5 showed consistency but the density-vs-HAR and B-L are first reads (thin snapshot coverage: 21–40/70 events; open-tail σ bias; crude realized proxy). A proper HAR-RV fit + tail handling + a longer forward ladder snapshot accrual would be the only remaining place a structural (variance/skew) edge could hide. No claim it's there — just the one untested corner.
5. **Maker-shadow: keep PARKED** — its arbiter question (does the ETH maker survive live fills?) no longer exists after the realism gate. T2 remains valuable for corpus/RTI accrual on its own clock.

**Nothing here is a decision — it is the evidence assembled for your ruling.**
