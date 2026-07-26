# Kalshi Settlement-Convergence Edge — Feasibility Study (read-only)

**Date:** 2026-07-26 · **Scope:** feasibility only — no division, no code, no config, no orders. All live queries via the Karen read-only Kalshi client on prod.
**Bottom line:** The original hypothesis is **REFUTED**. A *different*, marginal effect (favorite-underpricing ~1h pre-settlement) survives the fill-quality kill-test but is **statistically indistinguishable from zero** and sits in a venue where every forward strategy loses money. **Verdict: not worth building on current evidence.**

---

## STEP 1 — Data availability

| Dataset | Available? | Source |
|---|---|---|
| Settled market outcomes (yes/no) | ✅ | `get_markets(status=SETTLED, series_ticker=…)` → `result` |
| Near-settlement price | ✅ (via candlesticks) | `get_candlesticks_batch()` hourly; **`last_price_dollars` is null on settled list objects** — price must come from candles/tape |
| Trade tape (price, size, side, time) | ✅ | `market.get_trades(min_ts,max_ts)` → `count_fp`, `yes_price_dollars`, `taker_side`, `created_time` |
| **Historical orderbook depth** | ❌ | Kalshi serves **current book only** → **STEP 4 fill quality is tape-approximated, not proven** |
| Internal stored history | partial | `kalshi_round_trips` (3,687 copy-trade sports rows); no stored tape/candles/snapshots |

**Limitation carried into the verdict:** no historical book → fill quality is inferred from realized trade tape (optimistic: assumes you could have taken the traded volume). Weighted accordingly below.

---

## STEP 2 — Tail-loss table (the core result)

**Sample:** 5,727 settled sports markets, ~6-week lookback (MLB 1,762 · ATP 1,870 · WTA 1,883 · MLS 150 · NBA 32 · NHL 30). Near-settlement price from hourly candlesticks.

### The measurement-time finding (refutation)
**At T0 (last trade before settlement), markets have fully converged:**
| bucket | n | realized YES |
|---|---|---|
| 0.99–1.00 | 2,825 | **100.00%** (zero tail losses) |
| 0.97–0.98 / 0.98–0.99 | 3 / 8 | 100% |
| ~0.01 (losers) | 2,890 | 0.03% |

The 0.95–0.98 last-trade band is **empty (13 markets)**: the book snaps to 0.99/0.01 before the final trade. **There is no lazy-exit supply of decided-but-unsettled contracts sitting below fair.** The hypothesis — "holders exit early at 0.96–0.99 on decided markets" — is **refuted**.

### What exists instead: favorite underpricing at T-1h
Measuring the price **1 hour before settlement** (outcome not yet decided) populates the band and shows a consistent positive gap:
| bucket (T-1h) | n | realized YES | avg price | edge |
|---|---|---|---|---|
| 0.95–0.96 | 78 | 97.44% | 0.9500 | +2.44pp |
| 0.96–0.97 | 96 | 98.96% | 0.9600 | +2.96pp |
| 0.97–0.98 | 105 | 99.05% | 0.9700 | +2.05pp |
| 0.98–0.99 | 121 | 99.17% | 0.9800 | +1.17pp |
| 0.99–1.00 | 620 | 99.52% | 0.9900 | +0.52pp |

**T-3h:** edge gone/noisy (0.90–0.95 → −0.12pp). The effect is a **final-hour** phenomenon and is **favorite-underpricing with real outcome risk**, NOT settlement-convergence.

**Statistical caveat (decisive):** the 0.95–0.99 band = 400 markets with **only 5 losses**. Expected losses if fairly priced ≈ 11; observed 5 → suggestive (~2% level) but per-bucket 95% CIs comfortably include "no edge."

---

## STEP 3 — Net-of-fee EV per bucket (T-1h)

Fee = `ceil(0.07·C·P·(1−P)·100)/100` per order (e.g. P=0.97 → $0.21). EV/100-block = `100·W − 100·P − fee`.

| bucket | W (realized) | avg P | edge$ | fee | **EV / 100** | EV % |
|---|---|---|---|---|---|---|
| 0.95–0.96 | 0.9744 | 0.9500 | +2.44 | 0.34 | **+$2.10** | +2.2% |
| 0.96–0.97 | 0.9896 | 0.9600 | +2.96 | 0.27 | **+$2.69** | +2.8% |
| 0.97–0.98 | 0.9905 | 0.9700 | +2.05 | 0.21 | **+$1.84** | +1.9% |
| 0.98–0.99 | 0.9917 | 0.9800 | +1.17 | 0.14 | **+$1.03** | +1.1% |
| 0.99–1.00 | 0.9952 | 0.9900 | +0.52 | 0.07 | **+$0.45** | +0.5% |

Net-of-fee EV is positive across the band at the **point estimate** — but rests entirely on W exceeding P, which is not statistically established (above).

---

## STEP 4 — Fill quality (Tier C kill-test)

**Method:** 201 markets at 0.96–0.98 at T-1h; pulled trade tape in the final-hour window (402 API calls). Simulated a 100-contract taker fill from T-1h forward. *(No historical book — tape-based, optimistic: assumes you capture traded volume.)*

| metric | result |
|---|---|
| median window volume | ~107,600 contracts *(count_fp units unverified — see caveat)* |
| markets with ≥100 contracts in window | **100%** |
| markets where 100 filled from tape | **100%** |
| realistic fill VWAP (median) | **0.9668** (mean 0.951, p90 0.98) |
| realized YES rate (subset) | 0.990 (199/201) |
| **EV at realistic fill** | **+$2.09 / 100** *(corrected; script had a fee-scaling bug that understated it)* |

**The fill-quality kill-test did NOT kill the edge.** Liquidity near settlement is **deep** — 100 contracts is negligible against ~100k of window volume, and the realistic fill VWAP (~0.967) is at/below the nominal entry. The "climb the book" failure mode does not occur.

**Caveat:** `count_fp` came back fractional (e.g. "56.50"); exact contract semantics unverified, so the absolute "fill 100" threshold is soft. The *directional* conclusion (ample liquidity, fills ~0.967) is robust because VWAP is scale-invariant and volume is clearly large.

### Why the edge still fails despite good fills — the statistical wall
Entry/fill ≈ **0.967**; breakeven (incl. fee) ≈ **0.969**. Observed win rate **0.990 (199/201)**. **Wilson 95% CI = [0.964, 0.997].** The lower bound (0.964) is **below breakeven (0.969)** → **the edge is NOT statistically distinguishable from zero.** Two losses in 201 is the entire result; a handful more upsets flips it negative. The point estimate is +$2/100; the honest interval spans "small edge" to "no edge / slight loss."

---

## STEP 5 — Opportunity supply & settlement lag

- **Supply:** ~400 markets in the 0.95–0.99 T-1h band over ~6 weeks ≈ **~9/day** (add 0.90–0.95 → ~18/day). Concentrated in **MLB + tennis (ATP/WTA)**; NBA/NHL offseason (thin). "Dozens/week."
- **Settlement lag** (3,687 internal copy-trade rows): **91.5% settle <1h** (avg ~14 min); T-1h entry → ~1–1.5h capital lockup; thin tail (51 trades) >24h (avg 74h).
- **Internal cross-check** (whale-follow entries, biased sample): 0.95–0.99 realized win ≈ price ±0.6pp — i.e. roughly *fair* when entered at arbitrary times, consistent with the effect being a narrow final-hour one.

---

## STEP 6 — Forced-exit signature

Detected a ≥3¢ drop-then-≥2¢-recovery signature in **137 / 201 markets (68%)**, median max-drop **4¢**. **But this is inconclusive / likely not exploitable:** these are *live* markets 1h before settlement — 4¢ swings are indistinguishable from **normal in-game repricing** (favorite dips when the opponent scores, recovers when it doesn't). There is no evidence the dips are **liquidity-driven forced selling** vs. **information-driven** repricing, and buying the dip is the *same directional favorite bet* with the same outcome risk. **No distinct liquidity-provision edge demonstrated.**

---

## Prior art — `kalshi_tail_price_arb` (already in the engine)

Characterized to rule out double-counting. It is a **market-neutral fee-rounding arb** (buys YES+NO when `yes_ask+no_ask < $0.99`), scopes **Politics/Elections/Economics/Financials only (no sports)**, has **no timing gate and no fair-value model**, is **paper/standby**, and has **0 completed round-trips ever** (99,550 evaluations, finds nothing). **No overlap** with the T-1h sports-favorite effect on mechanism, scope, timing, or capture. The signal studied here is genuinely untouched by existing strategies.

---

## Venue context (heavy negative prior)

Every Kalshi strategy with a real forward sample loses money:
| strategy | n | realized P&L |
|---|---|---|
| kalshi_weather_arb | 1,222 | −$968.79 |
| kalshi_llm_arbitrage | 2,798 | −$458.70 |
| kalshi_crypto_arb | 790 | −$69.58 |
| kalshi_copy_trader | 3,687 | −$42.49 |
| kalshi_temporal_bucket_arb | 260 | +$3,040.15 *(one-time backlog-drain artifact; not forward-repeatable — see 2026-07-26 diagnosis)* |

This venue has eaten every prior edge. A marginal, statistically-unconfirmed signal here warrants heavy skepticism.

---

## STEP 7 — Synthesis & verdict

1. **Original settlement-convergence hypothesis: REFUTED.** Markets fully converge to 0.99/0.01 before the last trade; there is no supply of decided-but-unsettled contracts below fair (T0: 2,825 at 0.99 → 100% yes; 0.95–0.98 last-trade band empty).
2. **What was found instead:** a **favorite-underpricing** effect at **T-1h** (0.96–0.98 favorites, +1–3pp point estimate, +$1–2.7/100 net EV) — a *different* phenomenon, bearing **real game-outcome risk**, concentrated in the **final hour**.
3. **Fill quality does NOT kill it** (honest): sports markets are **deeply liquid** near settlement; 100 contracts fill trivially at ~0.967. The execution failure mode I expected does not occur.
4. **What kills it is statistics + venue prior, not fills:** the win rate's **95% CI lower bound (0.964) is below breakeven (0.969)** → the edge is **not distinguishable from zero** at n=201/400. The forced-exit signature is just in-game volatility. And every forward strategy in this venue loses money.
5. **Verdict: not worth building on current evidence.** Not because it's un-executable — because the signal is **statistically unconfirmed**, **directional/high-variance** (each loss = −$97), a **different and smaller effect than hypothesized**, and lives in a **venue with a strongly negative prior**. To justify a build, it would need a **much larger out-of-sample validation** (thousands of markets, more sports, distinct time periods) confirming W > breakeven with tight CIs — and even then the venue track record argues for low priority.

### If (and only if) pursued later — the shape of a real validation (NOT a build)
- Pull **all** settled sports moneyline markets over 6–12 months (thousands per bucket), out-of-sample from this window; rebuild the T-1h tail-loss table with **per-bucket 95% CIs**; require the CI lower bound to clear breakeven+fee.
- Paper-track live at T-1h (no capital) for a forward sample before any real-money consideration.
- Only then weigh against the venue's negative prior.

---

### Data limitations & correction notes (read before reusing any number here)
1. **No historical orderbook.** Fill quality (STEP 4) is **tape-approximated and optimistic** — it assumes you could have taken the volume that actually traded, without competition or market impact. The "deep liquidity / 100 contracts fill trivially at ~0.967" finding is therefore a **lower bound on execution difficulty, NOT proof that fills are easy** — real fills could be worse. It is strong enough to say fills are *not the binding constraint*; it is **not** strong enough to certify them.
2. **`count_fp` is fractional (e.g. "56.50") — contract-unit semantics unverified.** This affects the **absolute liquidity-magnitude** claims (median window volume ~107k; the literal "≥100 contracts" threshold) but **NOT** the fill VWAP (~0.967, scale-invariant) or the statistical verdict (which rests on realized win-rate vs breakeven, independent of size units).
3. **Tier C EV-print had a fee-scaling bug (caught & corrected).** The script's `fee()` computed `ceil(0.07·C·P·(1−P))` in whole dollars instead of `ceil(0.07·C·P·(1−P)·100)/100` (round up to the cent), overstating the fee ~7× and **printing EV = +$1.32/100**. The **corrected** figure used throughout this report is **+$2.09/100** at the ~0.967 fill. A future session re-running the raw script will see **+$1.32 — that is the uncorrected number; +$2.09 supersedes it.** The correction makes EV *more* positive and **does not change the verdict**, which turns on the win-rate confidence interval, not the EV point estimate.
4. **T-1h price from hourly candles** (coarse); "1h before last trade" ≈ 1h before settlement.
5. **Sample size:** n=201 (Tier C) / 400 (T-1h band) is small for distinguishing a ~2pp edge; **"insufficient sample to confirm an edge" is the honest statistical state.**
