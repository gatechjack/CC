# ORB Round-1 — bare mechanical core, ETHUSDT (pre-registered, read-only)

**Verdict up front: NO-GO. The bare 9:30-ET opening-range breakout has NO standalone edge on ETH.**
Pooled net-R ≈ 0 (+0.003R over n=646), long slightly negative / short slightly positive (they roughly
cancel), and **neither side is sign-stable across half-years**. The box breaks ~every day but the breakout
does not continue to 2R any better than a coin flip net of fees. Outcome **(b)** — if published ORB edges
are real, the edge lives in the discretionary confluence (VWAP/SFP/order-blocks) this round deliberately
excluded, not in the box itself.

## Method (pre-registered; locked — no optimization, no filter, no confluence)
- **Asset** ETHUSDT. **Confirmation TF = 15m** (the box IS the 09:30 ET 15m candle; breakouts on 15m closes).
  *(TF was an operator-left choice; picked once, not optimized.)*
- **Box** = high/low of the **09:30 ET** 15m candle. ★**DST-aware US-Eastern wall-clock** via `zoneinfo`
  (`America/New_York`), NOT a fixed UTC offset. Weekdays only (US equity-open days; weekends excluded).
  *(US market holidays NOT excluded — minor round-1 noise.)*
- **Entry** breakout-CLOSE, first valid per day, entered at the **next 15m bar's open** (k=1). Stop = opposite
  box side. Target **2R primary** (+ N×box-height secondary, N=1/2).
- **Exit** intraday; **EOD timeout at 16:00 ET** (mark-to-close). *(The 16:00-ET timeout was NOT in the
  operator pre-reg — it is a necessary exit rule, stated explicitly.)* Both-hit bar → SL-first (conservative).
- **Fee model** (Bitunix corrected, same as the SFP p6 model): entry taker 0.000243, TP exit maker 0.00014,
  SL/timeout exit taker 0.0004, slippage 0.0001; `net_R = gross_R − (entry+exit+slip)/(R_dist/entry)`.

## Data + correctness proofs
- **ETHUSDT 15m, 87,075 bars, 2023-12-31 → 2026-06-25 (~2.5 yr)** via the Bitunix public REST kline endpoint
  (the same `_bitunix_kline_fetcher` LiveBarCache/replay use). **Well-powered: n=646 trades (304 long / 342
  short), both >> the 30/side floor** — this is verdict-grade, not the recent-months n-caveat anticipated.
- **k=1 PROOF:** 646 trades, `violations=0` — box built only from the 09:30 ET bar, scan starts 09:45 ET,
  entry = next-bar open. No look-ahead.
- **DST PROOF** (box anchor; ET fixed at 09:30, UTC shifts across the switch):
  `2024-02-01 → 14:30 UTC = 09:30 EST` · `2024-04/07/11-01 → 13:30 UTC = 09:30 EDT`. A fixed-offset bug would
  have mis-anchored the EDT season by 1h; `zoneinfo` handles it correctly.

## 1. Per side (SEPARATE — never pooled), 2R primary, fee-net
| side | n | win@2R | avg net-R@2R | median net-R | avg net-R N=1box | N=2box |
|---|---|---|---|---|---|---|
| **long** | 304 | 18.1% | **−0.0543** | −0.5505 | −0.0539 | −0.0288 |
| **short** | 342 | 21.3% | **+0.0540** | −0.2840 | −0.0260 | +0.0122 |

Win@2R 18–21% is **well below the ~33% breakeven** for a 2R target — the breakout doesn't reach 2R often
enough; the small avg-R comes from timeouts, not directional follow-through. Median is negative both sides
(the typical trade loses).

**Walk-forward (avg net-R@2R per side, by half-year) — SIGN-UNSTABLE:**
| side | 2024H1 | 2024H2 | 2025H1 | 2025H2 | 2026H1 |
|---|---|---|---|---|---|
| long | +0.038 | −0.268 | −0.126 | −0.073 | +0.157 |
| short | +0.087 | −0.036 | −0.056 | +0.136 | +0.148 |

Both sides flip sign across periods; the faint short-side positive is **recency-driven (2025H2/2026H1)**, not
a stable edge. No robust directional signal either way.

## 2. ORB-size → outcome (box height as % of entry; avg net-R@2R) — characterization only, NO filter added
| bucket | long (n, avgR) | short (n, avgR) |
|---|---|---|
| <0.25% | 7, +0.514 | 1, −1.20 *(tiny n)* |
| 0.25–0.5% | 36, −0.030 | 38, **+0.276** |
| 0.5–0.75% | 99, −0.151 | 93, +0.084 |
| 0.75–1.0% | 56, −0.194 | 71, −0.050 |
| >1.0% | 106, +0.064 | 139, +0.035 |

No clean monotonic box-size gradient. The largest-box bucket (>1%, where most trades sit) is modestly
positive both sides; small/mid buckets are noisy. **No obvious data-driven size filter jumps out** — and per
the pre-reg, none is added in round 1.

## 3. Breakout stats (of 649 eligible weekday boxes)
- **Entry rate 99.5%** (646/649) — the box almost ALWAYS breaks during the session. Near-**unconditional**
  entry; essentially no selectivity in the bare rule.
- **Break UP 46.8% / DOWN 52.7%** — faint down-bias. **Box held all session 0.5%** (3 days).

## 4. Verdict — (b), NO-GO on the bare core
The 9:30-ET box is a **real volatility structure** (it produces a breakout ~every day, with a faint down-bias
and a faint short-side R-tilt) — so it's not pure noise — but the **breakout has no standalone edge to 2R**:
pooled net-R ≈ 0, win@2R far below breakeven, and sign-unstable across periods. **The bare-core go/no-go is
NO-GO: do not build the strategy on the box alone.** If ORB works, the edge is in the discretionary confluence
that was deliberately excluded — exactly the thing round-1 isolates out.

**Honest caveats:** (i) **no control box** — I did not compare the 09:30-ET box against a random-time / other-
session box, so I cannot fully separate **(b)** "edge is in the confluence" from **(c)** "the equity-open box
is unremarkable on ETH." Both are consistent with a flat bare core; the round-1 conclusion (no standalone
edge) holds under either. (ii) The 16:00-ET EOD timeout is an added exit rule. (iii) Holidays not excluded.
(iv) k=1 + DST verified clean. **Result reported as-is, including the negative — no optimization, no filter,
no confluence.**

Script: `orb_round1_eth.py`. Reproduce: `.\scripts\run_capped.ps1 <py> orb_round1_eth.py`.
