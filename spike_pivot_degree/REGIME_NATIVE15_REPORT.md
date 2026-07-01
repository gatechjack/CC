# Regime-Conditional SFP on the FULL Native 15m History (~230 days)

*Thesis-test run, 2026-07-01. NOT a deploy candidate. The entry is a **15m proxy**
(15m SFP → **15m** BOS), not the live 3m-BOS trigger. Read-only research on native
`bars_15m`. Harness: `spike_pivot_degree/regime_native15.py` (commit c3bdd64).*

## 0. What changed vs the prior (46-day) run
- **Data:** reads the **native `bars_15m` table** from each `*_scalping.db`
  (**~230 days**, 2025-11-01 → 2026-06-19/26, ~22k bars/coin, **0 gaps**) instead of
  resampling the short 47–81d `bars_3m` slice. This is the one change that matters —
  it puts **real up-legs** (not just bear bounces) into the sample.
- **Detector:** `SfpDetector` (Mode A, 15m SFP → 15m BOS). `SfpModeBDetector` (live
  Mode B) *embeds this exact `SfpDetector` as its fire engine*, so the **SFP-fire
  logic is byte-identical to live**; only the BOS/entry timeframe differs (15m proxy
  vs live 3m). This is a deliberate, acknowledged proxy — see §4.
- **Regime:** 15m EMA-200 + slope over 32 bars (8h). UP=close>EMA200 & rising;
  DOWN=close<EMA200 & falling; else RANGE. (Robustness: also ran 5-day momentum and
  SMA-100 slope.)
- **Costs:** taker 0.019% × 2 legs + a **2 bp slippage stub** × 2 legs =
  **0.078 % notional/round-trip**, converted to R via the real entry/stop (shorts
  recover real prices from the reflection midpoint). Expectancy reported **NET**.
- **Null-gate:** within-side regime-label permutation, **200 runs, 95th pct**.
- **k=1 / causal:** pivots confirm only 50 bars forward; EMA/slope at bar t reads
  closes ≤ t.

## 1. SELF-CHECK GATE (passed — native data is trustworthy)
Native `bars_15m` vs `resample_15m(bars_3m)` over each coin's overlap:

| coin | overlap | bars | OHLC match | worst rel. err | SFP fires identical |
|---|---|---|---|---|---|
| BTC | 2026-03-30 → 06-19 | 7779 | **100.000%** | 0.00e+00 | yes (397=397) |
| ETH | 2026-05-11 → 06-26 | 4493 | **100.000%** | 0.00e+00 | yes (262=262) |
| SOL | 2026-05-11 → 06-26 | 4492 | **100.000%** | 0.00e+00 | yes (227=227) |
| XRP | 2026-05-11 → 06-26 | 4493 | **100.000%** | 0.00e+00 | yes (259=259) |

Native 15m is **bit-identical** to resampled 3m over the overlap, and the 15m
detector fires identically on both. Known 2026-06-28 sweeps (ETH 1555.72, SOL 69.68)
re-detected. → The 230d extension is safe to trust.

**Regime distribution (230d, EMA200):** UP 33 931 / RANGE 11 362 / DOWN 45 157
15m bars. DOWN still dominates (~50%) but there are now **~34k genuine UP bars** vs
the bear-only 46d slice's ~7.5k.

## 2. RESULTS — (side × regime) expectancy, 230d, NET of fees (primary EMA200)

|            | UP | RANGE | DOWN |
|---|---|---|---|
| **Long SFP**  | −0.148R (n=476) | **+0.079R** (n=120) | −0.269R (n=845) |
| **Short SFP** | −0.002R (n=554) | **+0.204R** (n=158) | −0.030R (n=612) |

**Aggregates (net R):**
- **Trend-aligned** (long-up + short-down): **−0.082R** (n=1088) — null p95 −0.026 → **does NOT beat null**
- **Counter-trend** (long-down + short-up): −0.163R (n=1399)
- **Unconditional long:** −0.200R (n=1441)
- **Unconditional short:** +0.010R (n=1324)

**Null-gate (200× within-side regime-shuffle, 95th pct):** the ONLY cells that beat
null are **Long-RANGE** (+0.079R > p95 −0.001) and **Short-RANGE** (+0.204R > p95
+0.183). Every UP/DOWN cell and the trend-aligned aggregate **fail** the gate.

**Robust across all 3 regime formulas:** trend-aligned negative in every one
(EMA200 −0.082, mom5d −0.096, SMA100 −0.130); none beat null. Short-in-UP ≈ 0 in
every one (−0.002 / −0.008 / +0.050; none beat null).

**Per-coin (primary, net R, pivots {5,8,10} pooled):**

| coin | L-up | L-rng | L-dn | S-up | S-rng | S-dn |
|---|---|---|---|---|---|---|
| BTC | −0.41(125) | −0.15(29) | −0.19(180) | −0.32(143) | −0.18(39) | +0.00(134) |
| ETH | −0.01(127) | −0.21(28) | −0.26(224) | +0.16(159) | −0.16(25) | +0.00(141) |
| SOL | −0.11(148) | +0.43(28) | −0.25(201) | −0.28(144) | +0.25(41) | −0.05(140) |
| XRP | −0.03(76)  | +0.22(35) | −0.35(240) | +0.56(108) | +0.63(53) | −0.06(197) |

Per-coin is noisy and inconsistent in sign (e.g. Short-UP: BTC −0.32, XRP +0.56) —
no coin carries a clean regime-aligned edge; the pooled picture is the honest one.

## 3. THE CRITICAL READOUT — Short-in-UP
> On 46 bear-days the Mode-B run reported Short-UP **+0.55R** and flagged it as
> bear-bounce-fading, not real bull. Does it survive when real up-legs enter?

**Under the 15m proxy on 230d: Short-UP = −0.002R (n=554) — a WASH.** It does **not**
stay positive. It is ~0 across all three regime formulas and fails the null gate.
**Consistent with the bear-beta hypothesis:** the apparent short-in-UP edge does not
survive once ~34k genuine up-leg bars are in the sample.

**Window isolation (detector held FIXED = Mode-A proxy, so this isolates the regime
SAMPLE, not the entry change):**

| cell | 46d-overlap (bear-heavy) | 230d (multi-regime) |
|---|---|---|
| Short-UP | −0.015R (n=146) | −0.002R (n=554) |
| Short (all) | +0.077R (n=319) | +0.010R (n=1324) |
| Short-DOWN | +0.056R (n=128) | −0.030R (n=612) |
| Trend-aligned | −0.101R (n=244) | −0.082R (n=1088) |

Under the proxy the short side was already weak on 46d and **fades toward zero** on
230d — exactly the bear-beta signature.

## 4. KEY CAVEAT — the 15m proxy UNDERSTATES the live (Mode-B) strategy
Running **both** detectors on the **identical 46d-overlap** data (GROSS R):

| short cell | Mode-A 15m-proxy | Mode-B live 3m-BOS |
|---|---|---|
| Short-UP | +0.089R (n=146) | +0.143R (n=273) |
| Short-RANGE | +0.533R (n=45) | +0.304R (n=46) |
| Short-DOWN | +0.159R (n=128) | +0.398R (n=177) |
| Short (all) | +0.180R (n=319) | **+0.249R** (n=496) |

The live **3m-BOS entry is systematically stronger** than the 15m proxy (tighter,
earlier entry → better R; Short-DOWN nearly 2.5×). So the 230d proxy results are a
**weakened / lower-bound stand-in** for live Mode-B. A negative proxy result does
**not** definitively kill the live thesis — it says the *15m detection + 15m-proxy
entry* does not carry the regime edge on 230d.

**⚠ Anomaly (flagged, not reconciled):** my Mode-B replication here gives Short-UP
**+0.143R (n=273)**, not the prior doc's **+0.55R (n=188)**. Most likely a window
difference — my per-coin 3m overlap includes **BTC's 81 days back to 2026-03-30**
(which contains up-legs where short-UP bleeds), diluting the pooled short-UP. That is
*consistent with* the bear-beta finding (adding up-leg data lowers short-UP), but I
did **not** fully reconcile it. A clean reconciliation = re-run Mode-B on the common
intersection window (all 4 coins 2026-05-11 → 06-19).

## 5. VERDICT
On 230 days of native 15m data, **under the 15m proxy the regime-aware bidirectional
SFP thesis does not hold up**:
1. Trend-aligned expectancy is **negative** (−0.082R) and **fails** the null gauntlet
   the prior split never faced. Robust across 3 regime formulas.
2. **Short-in-UP is a wash (~0)** — the +0.55R was bear-beta / small-sample, not
   durable alpha, exactly as feared.
3. Longs bleed in **every** regime (worst in DOWN), not the clean "fine in up/range"
   the 46d Mode-B run implied.
4. The **only** null-surviving edge is the **RANGE** regime (both sides positive) —
   an SFP-mean-reversion-in-chop hint, but thin (13% of bars, n≈120–160) and
   deserving its own test, not a bidirectional-trend build.

**Two reasons this is not a kill-shot for the live thesis:**
- The **15m proxy understates live Mode-B** (§4). The live 3m-BOS strategy on
  multi-regime data is **untested** (no 3m history pre-2026-03-30).
- The definitive test still needs **3m data accumulated across regimes** to run the
  *live* entry — the same data gap the prior spikes named.

**Recommendation:** do not read the 46d +0.29R aligned / +0.55R short-UP as bankable.
The 230d proxy actively *weakens* the short-alpha claim. Next: (a) keep accumulating
3m data toward a Mode-B 230d re-run; (b) if anything is worth a standalone look, it is
the **RANGE-regime** SFP edge, not trend-aligned side-switching; (c) reconcile the
+0.55R↔+0.143R window discrepancy before quoting either.

*Costs are optimistic (2bp slippage stub; live SFP-reversal slippage can be several×
the fee — see stop-slippage memory), so the true net is if anything worse — which only
strengthens the negative read. Single regime era is still just ~8 months (one bear +
its bounces); a genuine extended bull remains absent from the data.*
