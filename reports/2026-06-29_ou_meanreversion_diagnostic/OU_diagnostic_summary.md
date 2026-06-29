# OU / Mean-Reversion Diagnostic — BTC/ETH/SOL/XRP 1H (in-sample) — VERDICT: NEGATIVE

Date: 2026-06-29. Backtest-only, no prod path touched. Data: `*_scalping.db` `bars_1h`,
validated lossless aligned grid, common window 2024-08-04 20:00 → 2026-06-19 01:00 (16,398 bars).
Toolkit: numpy 2.4.4, pandas 3.0.2, scipy 1.18.0, statsmodels 0.14.6 (pinned in requirements.txt).

## 1. Single-asset OU (raw level)
BTC 1H raw/log level: θ≈3.0e-4/bar, half-life ~94 d, ADF p=0.43 → random walk, not mean-reverting. KILLED.

## 2. Single-asset OU (causal trailing-SMA detrend; windows 24/48/96/168/336 h)
- Residual ADF p=0.0000 at every window BUT random-walk null also p=0.0000 → stationarity = detrend artifact.
- half-life ≈ 0.40 × window at every window (mechanical, filter-set).
- Real residual AR(1)≈0.93 vs null ~0.37 → real residual is MORE persistent than random = trend/momentum,
  the adverse direction for a fade. No exploitable reversion. KILLED.

## 3. Pairs cointegration + null gate (6 pairs, EG on 1H log-prices)
| pair | beta | coint p | half-life | AR(1) | beats null? |
|------|------|---------|-----------|-------|-------------|
| btc/eth | 0.515 | 0.211 | 86 d | 1.000 | False |
| btc/sol | 0.413 | 0.157 | 80 d | 1.000 | False |
| btc/xrp | 0.344 | 0.633 | 53 d | 0.999 | False |
| eth/sol | 0.615 | 0.690 | 90 d | 1.000 | False |
| eth/xrp | 0.204 | 0.825 | 106 d | 1.000 | False |
| sol/xrp | 0.210 | 0.925 | 163 d | 1.000 | False |

- No pair cointegrates (coint p 0.157–0.925, none < 0.05).
- Spread residual AR(1) ≈ 1.000 = unit-root random walk; half-lives 53–163 days.
- Real reverts no better than shuffle/RW null (frac_null_faster 0.27–0.99); null spurious-coint rate ≈ chance.
- z-excursions: 1–19 |z|≥2 events over 682 d, mostly below Gaussian → no count, no reversion. KILLED.

## Overall
OU mean-reversion does not fit BTC/ETH/SOL/XRP at 1H — single-asset OR pairwise. Reason: the four are
crypto-beta (correlated returns) but their relative valuations drift, so spreads are themselves random walks
(spurious-cointegration trap, caught by the null). Evidence points toward trend/momentum/structure
(aligns with existing SFP/BOS work), away from reversion.

## 4. CONDITIONAL tail mean-reversion (NOT unconditional OU) — ≥2.5σ from slow anchor
Anchors: 200d MA + 200d trailing VWAP (causal); σ = 30d trailing std of (price−anchor); onset |z|≥2.5
(rearm 2.0); outcome = price first-passage ±1.5σ_entry bracket within 30d; null = 100 shuffled-return draws.
- BINDING LIMIT: 2.5σ events are RARE. BTC only 3–8 events/cell (no power); ETH/SOL/XRP 9–16/cell.
- 15 of 16 cells FAIL the null (revert% ≈ random-walk's ~44–50%). Only pass = ETH MA200-above
  (85.7% rev, exp +1.07σ, n=14) — consistent with multiple-testing chance (~0.8 FP expected over 16 cells),
  not robust (same events appear in both anchors; small n).
- Continuer tail (the fade-killer): conMAE 2.2σ–27.3σ; XRP-overbought continuers ran 27σ, ETH 6.7σ.
  A single such continuer erases dozens of 1.5σ winners; clean-stop expectancy is optimistic (crypto gaps).
- Asymmetry: oversold continuers run less far than overbought (every coin) — directionally as hypothesized,
  but revert rates still don't beat null → doesn't convert to edge.
- Verdict: underpowered + null-failing. Not a clean theoretical kill like §1–3, but does NOT pass to build.

## 5. CONDITIONAL tail reversion at SCALPING scale (fixes §4's power problem)
Anchors: 200-BAR MA + session VWAP (reset 00:00 UTC), causal; σ=200-bar trailing std of (price−anchor);
onset |z|≥2.5 (fresh crossing); outcome = ±1.5σ_entry price bracket within 200 bars; null = 30 shuffled draws.
TFs 3m+15m, all 4 coins → 32 cells (coin×TF×anchor×side).
- POWER: SOLVED — 98–376 events/cell (vs 3–16 at 1H). No power excuse.
- NULL GATE: only 2/32 cells beat null (xrp 3m VWAP above, sol 15m VWAP above) = chance rate (~1–2 expected).
  Revert rates 54–64% are matched by the random-walk null (42–54%) → intraday regression is mostly mechanical.
  ETH-overbought is the most consistent raw signal (rev 61/62/53/62%) but FAILS its own null every time.
- CONTINUER TAIL (fade-killer): conMAE mean 4–10σ, p95 8–32σ, max 10–47σ (close-based; intrabar worse).
  expHold (no-clean-stop, run to horizon) goes NEGATIVE across most cells even where expBrk (clean ±1.5σ stop)
  is positive (e.g. sol 3m MA above +0.28→−0.23; btc 15m VWAP below −0.10→−1.58). Edge needs a perfect stop.
- FEES: net_bps (clean-stop, 8bps RT) thin on 3m (sig% 0.37–0.74%); realistic (expHold) basis mostly negative.
- ASYMMETRY: 1H oversold-resilience did NOT replicate; weak reversion is on the overbought side at scale.
- Verdict: strongest negative — well-powered and STILL no robust, null-beating, tail-/fee-surviving edge.

## 6. MOMENTUM/CONTINUATION mirror (enter WITH the 2.5σ stretch; stop on reversion; harvest tail)
Same instruments/anchors/TFs/σ/null as §5. Entry: above→LONG, below→SHORT at 2.5σ crossing. Stop=price
retraces {1.0,2.5}σ_entry. Targets: 2R/3R/5R fixed, trailing, hold-to-horizon. n=98–376/cell (well-powered).
- ★ ROBUST DIRECTIONAL ASYMMETRY: SHORT (downside-continuation, below-anchor) > LONG (above) in ALL 8
  anchor/TF/stop blocks. Short-side expectancy positive 7/8 blocks; long negative 6/8. Economically sensible
  (downside cascades/liquidations; upside stalls). Opposite of the reversion hint; consistent across cells.
- FEES: SURVIVES (unlike reversion). Big winners (avgWin 1–2.4R vs avgLoss ~−0.8R) clear 8bps RT easily on
  strong cells: eth 15m MA below 3R/5R net +149/+147bps; sol 3m MA2.5 below +53; btc 15m VWAP2.5 below hold +123.
  Best harvest = far fixed targets (3R/5R) or hold, NOT tight trailing; wider 2.5σ stop cleaner; 15m >> 3m.
- ★ NULL GATE: only ~17/320 config-cells beat null = ~chance rate. BUT null-beaters CLUSTER on downside/15m/
  strong-trend coins (not random), AND the null is too conservative to adjudicate (N_NULL=20 + fat tails →
  95th-pct bar too high; under-credits real +0.18R edges). Much of raw continuation = fat marginal dist, not
  proven serial momentum.
- VERDICT: the FIRST "maybe" — robust, fee-surviving, coherent downside asymmetry, but null gate largely unmet
  & too noisy to settle. NOT a pass, NOT a clean fail. Needs one adjudicating diagnostic (stronger null +
  paired short-vs-long test), NOT a build. Script: momentum_scalp_diag.py (colocated).

## 7. MOMENTUM ADJUDICATION (N=200 stable null + direction-randomized null; focus 15m/short/2.5σ/3R-5R-hold)
Two nulls bracket the question. Shuffled null keeps fat marginal (incl. neg skew), destroys order → tests serial
momentum. Sign-flip null (±1 per-bar) removes skew+sign-momentum → tests directional asymmetry. (sign-flip null
diff-mean ≈ −0.00..−0.07 = valid zero-asymmetry null.)
- (a) MAGNITUDE GATE: only 2/24 cells beat the stable shuffled null (btc & eth, VWAP, hold) ≈ chance. Big
  net-bps (eth MA200 +149/+147/+138; btc VWAP +123) DO NOT beat it — shuffled crypto reproduces them.
  ⟹ magnitude = fat marginal distribution, NOT serial momentum.
- (b) PAIRED ASYMMETRY: short>long beats the sign-flip null robustly for ETH (both anchors) and BTC (VWAP);
  NOT for SOL, XRP, or BTC-MA. So §6's "8/8 blocks" asymmetry is REAL but concentrated in the two majors.
- MECHANISM (beats sign-flip but not shuffled): the downside asymmetry is the negatively-skewed MARGINAL
  return distribution (down candles bigger/more violent), NOT "down-follows-down" serial momentum.
- VERDICT: NOT a certifiable serial-momentum edge. Asymmetry real (BTC/ETH) = marginal skew; the 2.5σ trigger
  adds no certifiable value over random entry into the same fat-tailed instrument (fails fat-marginal null 22/24).
  Do NOT scope a standalone "short at 2.5σ, harvest" build. Usable residue = a real BTC/ETH downside-violence
  RISK/SIDE bias (short follow-through fatter than long) → feeds existing SFP/BOS sizing/side-selection, not a
  new system. Script: momentum_adjudicate.py (colocated).

## Overall (5 reversion/momentum angles — FINAL)
Mean-reversion (§1–5): clean negative for BTC/ETH/SOL/XRP at 1H/3m/15m — not the edge.
Momentum/continuation (§6–7): the downside asymmetry is REAL for the majors but is MARGINAL SKEW, not a
certifiable serial-momentum edge; no standalone build justified. Net: OU/reversion is dead here, and the
continuation "edge" is the instrument's down-skew, not a tradeable signal-trigger. The only actionable residue
is a risk/side bias into the existing SFP/BOS continuation work, which this whole arc corroborates over reversion.

Scripts (all colocated in this folder): ou_assess.py + ou_assess2.py (Step-1 data assessment), ou_smoke.py (§1),
ou_detrend_diag.py + ou_detrend_null.py (§2), pairs_coint_diag.py (§3), tail_revert_diag.py (§4),
tail_scalp_diag.py (§5), momentum_scalp_diag.py (§6), momentum_adjudicate.py (§7). Promoted from tmp/ 2026-06-29.
Caveats: in-sample. Tail test is power-limited at 2.5σ (rare events). Untested: lower σ threshold (2.0σ),
lower TF (more events, but §1 found trend-persistence + higher costs), longer history (BTC only to 2024-08;
needs TV dump), rolling/Kalman hedge for pairs. All low-odds given the consistent negatives.
