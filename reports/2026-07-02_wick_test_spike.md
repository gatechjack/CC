# Wick-Test spike — does the mechanized 3m momentum-continuation scalp have +EV GROSS?

**Date:** 2026-07-02 · **Scope:** read-only research; no prod/live/SFP writes · **GROSS R only** (operator
handles fees) · k=1 causal, no lookahead. Branch `wick-test-spike-2026-07-02` (unpushed).
Harness: `spike_wick_test/wick_test_backtest.py`; full run `spike_wick_test/wick_run.log`; spec locked in
`spike_wick_test/PRE_REGISTRATION.md` (committed before any result).

## VERDICT (short)

**No robust, regime-general, coin-consistent edge — and the author's central claim is REFUTED.** The skill
says the edge "lives entirely in the preconditions (with-trend), not the candle shape." The mechanization shows
the opposite: the **unfiltered control ≥ the with-trend filter everywhere it matters** (control beats its null in
**23/48** cells vs **10/48** for with-trend), the counter-trend bucket is **not** punished (counter-trend longs
were often the *better* bucket), and the only cells that clear the null in BOTH configs are **alt SHORTS in a 47d
bear window** (SOL/XRP) — almost certainly **bear-beta, not wick-test alpha**. What positive gross exists is
dominated by **tight-stop momentum-drift geometry** (random entries with the same 0.1% stop clear a high null
bar), and the **constant 0.1%-of-price risk unit makes this fee-dominated** — most gross edges (+0.05…+0.3R) sit
below a realistic ~0.4R round-trip cost. **Recommendation: NO build.** One narrow, low-priority follow-up noted.

## Data & mechanization (see PRE_REGISTRATION.md for the full locked spec)
- Trade TF `bars_3m` (BTC 81.0d/38,899 bars; ETH/SOL/XRP 46.8d/~22,467), **0 gaps** all coins.
- Regime + L1 swings from native `bars_15m` (~230–238d, full EMA-200 warmup), read **causally** (last 15m bar
  *closed* at/before the candle-3 close — `regime_at` would have been lookahead). Regime = `ema200_pos_slope`
  copied **verbatim** from the live-parity `regime_filter.py`.
- **Windows are bear-heavy** (3m causal regime counts): ETH up 6,474 / down 13,273; SOL up 7,969 / down 12,210;
  XRP up 6,124 / down 13,599; BTC more balanced (up 18,340 / down 16,034). **One-bear-regime caveat is strong.**
- Pattern: exactly 3× 3m candles (2 momentum + tap). Entry = c3 wick extreme, **honest limit fill** only if a bar
  in {c3+1..c3+3} retests it. Stop = c3 extreme − 0.001·entry. Targets {1R,1.5R,2R}, stop-first. L1 = two-candle
  swing (15m), L2 = 00:00-UTC session VWAP.

## Signal frequency & fill
Raw pattern fire-rate **~29–61 /week** per coin·side·level; **fill rate 58–68%** (skip 32–42%). This is a genuine
scalp cadence. ⚠ The honest limit-retest **selects against the best trades** — the ~35–42% that *skip* are the
setups that ripped away without retesting the wick (the author's "boom, it takes off"); the fills are the ones
that came back, biasing toward choppier continuations.

## Stop-distance (the R-unit) — the decisive live-viability fact
**rp is a constant 0.100% of price BY CONSTRUCTION** (entry = c3 extreme, stop = extreme − 0.001·entry) → the
%-distribution is degenerate (q25=med=q75=0.100%). $-equivalent per 1R: **BTC ≈ $75, ETH ≈ $2.0, SOL ≈ $0.081,
XRP ≈ $0.00129**. A round-trip taker fee alone (~0.019%×2 = 0.038% ≈ **0.38R**) plus spread/slippage ≈ **~0.4–0.5R
cost per trade** — i.e., **on the order of, or larger than, almost every gross edge below.** (Operator handles
fees; flagging because it governs whether any gross cell can survive.)

- **Body-close rule materiality:** of hard-stop losses, only **2.8% (BTC) / 5.8% (ETH) / 9.9% (SOL) / 6.2% (XRP)**
  were "wick pierced stop but body closed back inside." Swapping the hard stop for the discretionary body-close
  rule would change few outcomes — not a meaningful lever.
- **Path-ambiguity (honesty check):** resolving bar held BOTH stop & tp (OHLC can't order them) in only
  **1.3% / 2.2% / 5.6% / 2.6%** of resolved trades → the sub-bar-range concern is real but small; the stop-first
  OHLC resolution is reliable. (This *supports* trusting the gross numbers.)

## Null-gate — the decisive comparison
Every positive cell vs a **random-entry null with the same stop geometry, side- and regime-matched** (200 runs,
p95 of avgR). The null p95 is **high (+0.05…+0.35R)**: a 0.1% stop + fixed-R target on drifting crypto is *itself*
mildly +EV for random entries. So a positive avgR is not evidence of pattern edge — it must clear this bar.

**Beats-null tally (n≥30 throughout):** control **23/48**, with-trend **10/48**. Control beats ~2.3× more often
and with larger n. **The explicit ema200 with-trend filter does not add value** — it shrinks n and raises the
(regime-matched) null bar, so cells that look positive fail the harder test.

### BTC (author's favored coin, best data 81d) — with-trend adds nothing
| side·level·tgt | with-trend | control |
|---|---|---|
| long L1 1.0 | +0.085 (null +0.098) **no** | +0.180 (null +0.083) **YES** |
| long L1 1.5 | +0.066 (+0.154) no | +0.173 (+0.108) **YES** |
| long L2 1.0 | +0.105 (+0.081) **YES** | +0.123 (+0.069) **YES** |
| short L2 1.0 | +0.113 (+0.114) no | +0.131 (+0.061) **YES** |
On BTC, with-trend clears the null in **1** cell; control in **4**. The regime gate hurts.

### Regime split (control, tgt 2.0 L1) — counter-trend is NOT punished
| coin·side | aligned (L-up/S-dn) | counter |
|---|---|---|
| BTC long | +0.029 (n=140) | **+0.176** (n=110) |
| ETH long | −0.083 (n=36) | **+0.212** (n=71) |
| SOL long | +0.256 (n=43) | **+0.347** (n=49) |
| XRP long | −0.027 (n=37) | −0.034 (n=59) |
| SOL short | **+0.603** (n=58) | +0.200 (n=35) |
Counter-trend **longs** were consistently *as good or better* than aligned — the direct opposite of the skill's
"counter-trend will punish you." Only the alt **shorts** favor the aligned (bear) direction — see below.

### The one standout — and why it's probably bear-beta
**SOL short L1_swing** clears the null in **all 3 targets, BOTH configs** (with-trend +0.333/+0.379/**+0.603**R at
1/1.5/2.0R, n≈58; control +0.282/+0.288/+0.394, n≈99). XRP short L1 also clears at 1.0/2.0R. But SOL/XRP windows
are **47d and bear-dominant**, and these are **shorts with the dominant downtrend** → the signal is very likely
**short bear-beta**, not a wick-test property. It does not generalize to BTC-short (barely clears) or to any long.

## Answers to the pre-registered questions
- **Does the mechanized wick test clear the null anywhere?** Yes but scatteredly — mostly in the **control**
  (23/48), concentrated at **1R** (highest WR ~55–61%) and in **alt shorts**. No clean cross-coin/side pattern.
- **Is the with-trend filter doing the author's claimed work?** **No.** Control ≥ with-trend in breadth (23 vs 10)
  and on BTC specifically (4 vs 1); counter-trend is not punished (often better for longs). The claimed edge
  source is not present in this mechanization/sample.
- **Best on Bitcoin (author's claim)?** Not in the null-gated sense — BTC's positive cells are thin and mostly
  control-only at 1R; the strongest gross cells are SOL/XRP short (bear-confounded).

## Recommendation
**NO build.** The apparent gross positives are (a) tight-stop momentum-drift geometry that random entries also
capture, and (b) a bear-window alt-short bias — not a distinct, regime-general wick-test edge; and the constant
0.1% R-unit makes the whole thing **fee-dominated** (most gross < the ~0.4R round-trip hurdle). The author's core
thesis (edge = with-trend precondition) is refuted here.

**Optional narrow follow-up (low priority):** isolate **SOL/XRP short in DOWN regime** with (1) a realistic fee
model and (2) a **longer multi-regime window** (native 15m spans ~230d; a 3m-parity extension or a coarser-TF
proxy) to separate genuine short alpha from 47d bear-beta. Only worth doing if the fee math can be beaten, which
the 0.1% stop makes unlikely without widening the stop (a different setup = a new spike, not a tweak).

## Caveats
47–81d, one-bear-regime (applies to everything); honest-limit fill selects against the best rip-away
continuations; constant-0.1%-stop degeneracy; gross-only (fees would erase most cells). Path-ambiguity checked and
small (1–6%). No lookahead: regime/level read from the last *closed* 15m bar; entry/fill/sim strictly forward.
