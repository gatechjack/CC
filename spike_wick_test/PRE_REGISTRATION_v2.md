# Wick-Test v2 — pre-registration (locked before code). FINAL SPEC, supersedes all prior v2 drafts.

Pre-positioned limit + body-close invalidation as a first-class exit mode. GROSS R only. k=1 causal, 3m
bars, all 4 coins. Read-only; no prod/live/SFP writes. Branch wick-test-spike-2026-07-02, new files.

## Setup / level / trigger / fill (exactly 3 candles)
- SETUP bars 1,2 (c1=k-2, c2=k-1): two consecutive same-direction momentum BODIES. LONG = both bullish
  (close>open); SHORT = both bearish.
- LEVEL from the impulse: LONG `L = max(high[c1], high[c2])`; SHORT `L = min(low[c1], low[c2])`.
- TRIGGER, intrabar bar 3 (k), WITH the impulse: LONG `high[k] > L` -> limit BUY rests AT L; SHORT
  `low[k] < L` -> limit SELL rests AT L. (Trigger dir = trade dir; the limit anticipates the tap.)
- FILL, bar 3 ONLY: LONG fills iff `high[k] > L AND low[k] <= L`; SHORT iff `low[k] < L AND high[k] >= L`.
  Entry = L. No fill by bar-3 close -> limit cancels (log bar-4-would-have-filled count, info only).
  Same-bar stop+fill (fill bar also reaches the line) resolves STOP-FIRST (conservative); count as
  intrabar-sequence ambiguity. Fill bar awards NO target (its trigger high/low is pre-fill; conservative
  -> target checked from bar k+1).

## Invalidation line & R-unit (shared by both exit modes)
LONG line = `L - 0.001*entry`; SHORT line = `L + 0.001*entry`. entry=L -> **R-unit = 0.001*L = constant
0.1% of price** (same fee-dominance caveat as v1; flag $-equiv).

## TWO EXIT MODES (the central comparison; run BOTH on IDENTICAL fills)
- HARD: any touch of the line -> exit at line, loss = -1R (capped). Win = target touch = +targetR.
- BODY-CLOSE: a wick through the line is NOT an exit; exit at the CLOSE of the first 3m bar whose BODY
  closes beyond the line. Realized loss = (close_exit - entry)/rp, can be worse than -1R. Wins/targets
  unchanged. On a bar that both body-closes beyond the line AND touches target, resolve LOSS-first
  (conservative, mirrors HARD stop-first); count those "TP-would-have-filled-first" bars.

## Targets
Fixed-R grid {1R, 1.5R, 2R} off the shared R-unit. Win R = +target; HARD loss = -1; BODY loss = realized;
timeout (MAX_HOLD 100 bars / 5h) = mark-to-market (last close).

## Momentum filter — LOCAL (run WITH-filter and NO-filter control)
with-momentum = (a) sign of net change over the last 10 3m closes matches impulse direction
(`close[k-1]-close[k-11] > 0` for long, `< 0` for short) AND (b) each of bars 1,2 bodies
`>= median body of the prior 20 bars` (median of `abs(close-open)` over [c1-20, c1-1]).
15m ema200_pos_slope regime = LOGGED COLUMN ONLY (informational split), not a filter. Signals require
k>=22 (filter warmup) so filter and control share the same population.

## One-open-at-a-time / identical fills
Fill set gated one-open-at-a-time by the HARD exit index (mode-independent, shared) so HARD and
BODY-CLOSE run on IDENTICAL fills. (Body-close may hold past the next fill -> mild overlap; documented.)

## Null (per exit mode)
Direction-matched random-entry null with the SAME trigger-fill geometry (entry=random bar close, line
0.1%, tp target*R), simulated in the SAME exit mode (body-close null carries the fat-tail loss profile).
200 runs, p95 of avgR, at the cell's n. beats_null = cell avgR >= null p95.

## Report (per coin x side x exit-mode x target, GROSS)
n, WR, avgR, totalR, beats_null; fill rate on TRIGGERED setups (vs v1's 58-68%);
**SHAKEOUT-SURVIVAL COUNT** = trades stopped under HARD that reach TARGET under BODY-CLOSE + their net R
contribution (body_R - hard_R over those trades = the direct value of the discipline); realized-loss
distribution under BODY-CLOSE (median/worst R); local-momentum filter vs control; regime column split
(informational); intrabar-ambiguity + bar-4 counts; signals/week.

## Verdict
(1) Does pre-positioned-limit + body-close clear the null where v1 didn't? (2) Is body-close the
edge-carrier (shakeout survival paying for the deeper realized losses)? (3) Does the LOCAL momentum
filter separate? Honest negatives welcome; 47-81d one-bear-regime caveat throughout.
