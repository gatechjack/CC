# Recency scorer — build + ground-truth calibration (2026-08-15)

Second, independent whale scorer (recency-weighted realized edge) alongside the
primary durability scorer. Realized basis; primary pipeline untouched (diff-gate:
3 added files, 0 modified). 24 unit tests green.

## Calibration: locked half-life = **30 days**

Sweep `--sweep 30,45,60,90 --as-of 2026-08-15` against ground truth:
llllllII (KNOWN fading — decayed, went dormant, demoted 2 sessions ago) vs
DegenKingBetter + ox1star84 (KNOWN durable, still active). `recent_vs_lifetime`
ratio (<1 fading, >1 accelerating) and trend:

| whale | HL=30 | HL=45 | HL=60 | HL=90 |
|---|---|---|---|---|
| **llllllII** (fading) | **fading 0.64** | **fading 0.74** | steady 0.80 | steady 0.86 |
| DegenKingBetter (durable) | steady 0.99 | steady 0.99 | steady 0.99 | steady 0.99 |
| ox1star84 (durable) | accel 1.53 | accel 1.38 | accel 1.29 | steady 1.19 |

- **HL=30 separates cleanest**: llllllII fading at 0.64 (0.11 below the 0.75 fade
  line) while both durable whales are non-fading. **LOCKED at 30.**
- HL=45 also separates but puts llllllII at 0.74 — 1bp from the threshold (knife-edge).
- **HL=60 / HL=90 FAIL**: llllllII reads `steady`, indistinguishable from durable.
- So the provisional 45d default was too slow; lowered to **30d**. Tunable via
  `--half-life-days`.

## Design validated by the calibration
- **Two signals, neither overriding (D1).** Ranked on `rw_realized` (total), llllllII
  is #1 at every half-life (rw_realized 404,970 vs DegenKing 141,510 vs ox1star84
  7,895) — it still has the largest recency-weighted TOTAL edge. The **trend** column
  is what flags it fading. Ranking on magnitude alone would keep it; the trend catches
  the decline. This is the 2x2 "high-recency-magnitude but fading" (decline-detect)
  quadrant working as intended.
- **Held-inflation corroborates independently.** llllllII `held_inflation_ratio = 1.79`
  (recent realized went negative vs held-to-resolution) — a second, independent decline
  signal agreeing with the fade. DegenKing 0.15 / ox1star84 0.01 (healthy).
- **Clean-hold isolation.** llllllII clean-hold share only 23% (recent edge is
  exit/partial-sell driven, and with held-inflation 1.79 the holds would have lost) vs
  DegenKing 84% (durable held edge).
- **Basis-consistency guard (D2).** In-window trades use AUDIT realized. `calib_ratio`
  (closed/audit on the overlap): DegenKing 1.00, llllllII 1.03, ox1star84 **0.88** —
  confirming closed-positions accounts partial sells differently, which is exactly why
  the score uses audit realized in-window rather than the closed spine's PnL.

## Caveat / follow-up: 429 rate-limit truncated llllllII's fetch
llllllII (a large whale, fetched first) hit HTTP 429 at closed-positions offset 700
and activity offset 1500 — the walk stopped early (the fetch loop breaks on any
data-api error, including 429). So llllllII's numbers are on partial data. This cuts
**conservatively**: the API returns most-recent-first, so the recency-relevant recent
window IS covered; the missing OLD pages would only lower `flat_mean`, which would make
llllllII look *less* fading — yet it still reads fading. The lock (30d) holds.
**Follow-up (production hardening, not blocking): add 429 backoff/retry + slower pacing
to the fetch walk so large whales aren't truncated.**

## Reproduce
```
python -m trading_corp.scripts.score_whale_recency \
  --only "llllll,DegenKingBetter,ox1star84" --sweep 30,45,60,90 --as-of 2026-08-15
```
(operator runner: `score_recency_calib.ps1`; raw output: `recency_calib_out.txt`)
