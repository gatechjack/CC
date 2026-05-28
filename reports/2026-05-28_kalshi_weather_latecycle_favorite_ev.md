# kalshi_weather late-cycle ULTRA-high-confidence favorite test — NULL (refuted on real prices)

**Date:** 2026-05-28
**Engine:** `scripts/weather_latecycle_favorite_ev.py` (model-free; pure numpy + the real-price candle corpus). Run capped.
**Corpus:** `tmp/kalshi_realprice_candles.jsonl` — 14,336 settled yes/no weather markets, 19 ICAOs, 68 target dates 2026-03-20 → 2026-05-26 (the frozen spring-2026 holdout; Kalshi retains ~2 months of settled candles).

## Hypothesis tested

A NARROWER slice than the prior favorite-buying scans: at **very late cycle AND very high confidence (implied ≥ 0.90)**, are favorites underpriced enough to clear fees + spread + tail risk? Mechanism proposed: market-makers keep wider spreads / lower prices late when the outcome is near-determined, leaving 1–2¢ for taking the last-mile risk. Bands: 0.90-0.93, 0.93-0.96, 0.96-0.99 (+ 0.99-1.00 reported separately).

## Bottom line

**Refuted.** No band, at any available late window, is +EV net of a taker fill + the Kalshi fee once the loss distribution is paid for. The favorite-longshot underpricing is real but small (~2–3% at the 0.90-0.93 edge) and is **exactly absorbed by the taker half-spread + 1¢ fee** — the same conclusion the broader market-structure scan reached, now confirmed on the ultra-favorite subset. Critically, the edge **decays as the cycle gets later** (overnight ≈ 0 → midday clearly negative): the opposite of the proposed mechanism. The real-money 89¢ trade sits in (just below) the **worst** band/window cell — selection bias / variance, not edge.

## ⚠ Data-window caveat (surfaced, load-bearing)

The corpus **caps candles at target-midnight + 18h UTC and stores no market close timestamp**, so the literal "final 6-12h before *settlement*" window is **not directly in the data**. For US stations d0+18h UTC ≈ midday/early-afternoon local on the target day. I proxied "late cycle" with offset-from-target-midnight windows; `midday_12_18` (d0+[12,18]h) is the closest-to-settlement price the corpus holds.

This matters less than it looks, because the **monotonic trend already answers the hypothesis**: edge falls overnight → morning → midday as the spread widens and the mispricing shrinks toward settlement. Pushing into the missing true-last-mile window (d0+18–30h) would, on this trend, be **even worse** for a taker, not better. A re-pull (extend `kalshi_realprice_pull.py` end-time + capture `close_time`) would test the literal window; the prior is strongly negative. **Flagging as a fork — say the word and I'll run the re-pull.**

## Result 1 — Band × window grid (ALL favorites; net/ct after taker-ask + fee)

`gap = actual WR − break-even WR(incl. fee)` = the net edge. `2·SE` uses n_days (intra-day bets are correlated).

| window | band | n | n_days | impl | actual WR | be+fee | **gap** | **net/ct** | 2·SE | losses | $tot |
|--------|------|---|--------|------|-----------|--------|---------|-----------|------|--------|------|
| overnight 00-06 | 0.90-0.93 | 1061 | 67 | 0.916 | 0.945 | 0.943 | **+0.003** | **+0.0027** | 0.056 | 58 | +2.83 |
| overnight 00-06 | 0.93-0.96 | 1500 | 67 | 0.946 | 0.966 | 0.968 | −0.002 | −0.0021 | 0.044 | 51 | −3.14 |
| overnight 00-06 | 0.96-0.99 | 2878 | 68 | 0.975 | 0.991 | 0.992 | −0.001 | −0.0014 | 0.023 | 27 | −3.93 |
| morning 06-12 | 0.90-0.93 | 909 | 67 | 0.916 | 0.939 | 0.950 | −0.010 | −0.0100 | 0.058 | 55 | −9.10 |
| morning 06-12 | 0.96-0.99 | 2748 | 67 | 0.976 | 0.991 | 0.994 | −0.002 | −0.0023 | 0.023 | 24 | −6.27 |
| **midday 12-18** | **0.90-0.93** | 731 | 68 | 0.916 | 0.932 | 0.948 | **−0.016** | **−0.0159** | 0.061 | 50 | −11.63 |
| **midday 12-18** | **0.93-0.96** | 886 | 67 | 0.946 | 0.965 | 0.970 | −0.005 | −0.0045 | 0.045 | 31 | −4.00 |
| **midday 12-18** | **0.96-0.99** | 2157 | 68 | 0.975 | 0.986 | 0.994 | **−0.008** | **−0.0081** | 0.029 | 31 | −17.56 |
| midday 12-18 | 0.99-1.00 | 6333 | 68 | 0.995 | 0.998 | 1.000 | −0.002 | −0.0016 | 0.010 | 10 | −10.00 |

**Read:** the only non-negative cell (overnight 0.90-0.93, +0.0027/ct) is within ±0.056 of zero (2·SE) — noise. Every later/higher cell is negative. The time-of-day gradient is the opposite of the hypothesis.

## Result 2 — Loss distribution (the decisive view)

At these prices one loss ≈ −$0.94 to −$1.00 and erases ~10–60 penny wins. Example — midday `0.96-0.99` (n=2157): 31 losses × ≈ −$0.99 ≈ −$30.7 of losses vs ≈ +$13 of penny wins → **net −$17.56**. Break-even needs WR ≥ 0.994; actual is 0.986 — short by 0.8%, and that 0.8% costs ≈ −$0.99 each. The rare tail dominates exactly as predicted.

Across all 10,107 late-window favorites pooled: WR 0.988 vs be+fee 0.992 → gap −0.004, net −0.0043/ct, **122 losses, mean loss −$0.96, total −$43.19.**

## Result 3 — YES-favorites only (matches the real trade: a bucket the market thinks WILL hit)

Pooled late YES-favorites: n=737, WR 0.984 vs be+fee 0.986 → **gap −0.002, net −0.0025/ct, −$1.82.** Essentially break-even, slightly negative. The few flicker-positive YES cells are tiny-n and do not replicate or survive holdout:
- `morning 0.96-0.99` n=33 flagged "ROBUST+EV?" — **false positive**: 0 losses in 33 bets understates variance (2·SE collapses to 0.003), it's the wrong window (morning not late), and the same band at midday is −0.0027. Textbook multiple-testing artifact across ~48 cells; not cherry-picked.

## Result 4 — Kind split (late window): daily_max is actually *over*priced midday

- `daily_min` (realized at dawn → genuinely post-realization at midday): 0.90-0.93 gap +0.005 (noise, 2·SE 0.045); higher bands negative.
- `daily_max` (peak mid-afternoon → **not** fully realized at midday): 0.90-0.93 **gap −0.032, net −0.0324** — the midday max-favorite wins only 90.5% vs implied 91.6%, i.e. **overpriced**, because the high can still move. The "last-mile" isn't last for daily_max in this window.

## Result 5 — Holdout (edge must survive both splits)

Late window, all favorites. **Every band is net-negative on BOTH the chronological halves and even/odd days.** No band survives.

| band | chrono TRAIN net/ct | chrono HOLDOUT net/ct | EVEN net/ct | ODD net/ct |
|------|------|------|------|------|
| 0.90-0.93 | −0.0121 | −0.0195 | −0.0128 | −0.0193 |
| 0.93-0.96 | −0.0003 | −0.0085 | −0.0018 | −0.0074 |
| 0.96-0.99 | −0.0126 | −0.0039 | −0.0094 | −0.0069 |
| 0.99-1.00 | −0.0012 | −0.0019 | −0.0022 | −0.0010 |

## Verdict

The expected null held. Late-cycle 0.90+ favorites are well-calibrated-to-slightly-rich for a taker: the ~2–3% favorite-longshot underpricing is fully eaten by spread + fee, and it gets worse — not better — the later you go (spread widens, mispricing shrinks). No 0.93-0.96 or any other band shows actual WR exceeding implied net of fees on holdout. The single 89¢ real-money fill lands in the worst region of the grid; it was variance/selection, not a repeatable edge.

This is the market-structure avenue (#5 in the 2026-05-28 ev-discovery report) refined to the late ultra-favorite subset — same conclusion, now band-resolved: **no robust +EV system. Shelve.**

## Reproduce

```
.\scripts\run_capped.ps1 python scripts\weather_latecycle_favorite_ev.py
```

Open follow-up (only if the Board wants the literal window): extend `scripts/kalshi_realprice_pull.py` end-time past target-day evening and capture `close_time`/`expiration_time`, then re-band by true hours-to-close. Prior from the time-of-day gradient: more negative, not positive.
