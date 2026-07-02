# Wick-Test spike — pre-registration (locked before code)

Backtest whether the mechanized "wick test" (3-candle momentum-continuation scalp, 3m) has **+EV
GROSS R** on BTC/ETH/SOL/XRP. Read-only research; no prod/live/SFP writes. GROSS R only (operator
handles fees). k=1 causal, no lookahead.

## Data
- **Trade TF:** `bars_3m` from `C:/Users/AA Incorporado/cc/data/{coin}_scalping.db` (BTC ~81d,
  ETH/SOL/XRP ~47d). `ts` in seconds → ×1000 = ms. Bars keyed by OPEN time.
- **Regime + L1 swings:** native `bars_15m` (~230–238d, full EMA-200 warmup), read CAUSALLY.
- **L2 VWAP:** computed on `bars_3m`, session-anchored 00:00 UTC (own calc from typical-price×volume,
  not the DB `vwap` column, to match the pre-registered anchor).

## Regime — `ema200_pos_slope` (verbatim from live-parity `regime_filter.py`)
On 15m closes: `em = EMA(close, span=200)`, `K=32`. For bar i≥32: `rising = em[i] > em[i-32]`;
`up` if `close[i]>em[i] and rising`; `down` if `close[i]<em[i] and not rising`; else `range`.
**Causal use:** at a 3m signal (candle-3 CLOSE ts = c3.open+180000ms), regime = label of the last 15m
bar whose CLOSE (open+900000ms) ≤ signal ts (bisect). Never the still-forming 15m bar.

## L1 key level — two-candle rule (15m, causal)
- bearish body = close<open; bullish = close>open.
- Swing HIGH confirmed at 15m bar j (j≥2) iff bars[j-1],[j] both bearish → `level=max(high[j-2],high[j-1])`.
- Swing LOW confirmed at bar j iff bars[j-1],[j] both bullish → `level=min(low[j-2],low[j-1])`.
- confirm-ts = close of bar j. LONG uses most recent prior swing HIGH with confirm-ts ≤ signal ts;
  SHORT uses most recent prior swing LOW. (Retest-from-above / retest-from-below.)

## L2 key level — session VWAP (00:00 UTC)
typical = (h+l+c)/3; running Σ(typical·vol)/Σ(vol), reset on UTC date change. Level at c3 = VWAP through
c3 (causal — uses c1..c3 only).

## Pattern — exactly 3 consecutive 3m candles (c1,c2,c3)
- Momentum c1,c2 same-direction bodies. LONG: both bullish. SHORT: both bearish.
- Setup c3 (LONG): (a) tap = `|c3.low − level| ≤ 0.0005·level`; (b) lower wick present
  `c3.low < min(c3.open,c3.close)`; (c) close back above level `c3.close > level`. SHORT = mirror
  (c3.high tap, upper wick, close below).
- 4+ candle sequences never counted (detector only ever uses a 3-candle window).

## Entry — honest limit fill (k=1)
Limit at c3 wick extreme (LONG=c3.low, SHORT=c3.high). Filled only if a bar in {c3+1,c3+2,c3+3} trades
back to it (LONG: low≤entry; SHORT: high≥entry); fill at the limit price on that bar. No fill in 3 bars →
SKIP (counted). One-open-at-a-time (no overlapping entries while a trade is live).

## Stop / target — GROSS
- Stop (LONG) = c3.low − 0.001·entry; entry=c3.low ⇒ **risk rp = 0.001·entry = constant 0.1% of price**
  (FLAGGED: %-stop-distribution is degenerate by construction; $-equiv varies with price).
- Targets grid {1R, 1.5R, 2R}: TP = entry ± target·rp. Stop-first on same-bar stop+TP (conservative).
- Sim from fill bar inclusive; MAX_HOLD_3M = 100 bars (5h) — necessary resolution cap; timeout →
  mark-to-market R = (last_close−entry)/rp. Timeout rate reported (expected ≈0 given 0.1% stop).

## Configs (run all; no shopping)
coin ∈ {BTC,ETH,SOL,XRP} × side ∈ {long,short} × level ∈ {L1_swing, L2_vwap} × target ∈ {1,1.5,2}.
- **with-trend** (the skill's claimed edge): LONG only in `up`, SHORT only in `down`; `range`=no trade.
- **no-filter control**: all qualifying signals regardless of regime, tagged by regime for the split.

## Reporting (GROSS)
Per coin×side×level×target: n, win-rate, avgR, totalR; regime split (with-trend vs control; does the
counter-trend bucket bleed as the skill claims?); stop-distance distribution (median/quartiles, %-of-
price + $-equiv); fill/skip rate; wick-pierced-but-body-held count (hard-stop-loss bars whose close came
back above the stop — materiality of the body-close rule); signal frequency per coin per week.

## Null-gate (decisive)
Every positive cell vs a **random-entry null with the same stop geometry** (0.1% stop, same target·R),
**side- and regime-matched** (random 3m bars drawn from the same regime), 200 runs, p95 of avgR.
`beats_null` = cell avgR ≥ null p95. Positives that skip the null die later (prior-arc lesson).

## Verdict frame
Decisive comparison = with-trend-at-level vs the unfiltered control. State plainly: does the mechanized
wick test clear the null anywhere — which coins, side, level def — and is the with-trend filter doing the
work the author claims. Honest negatives welcome. 47–81d one-bear-regime caveat applies throughout.
