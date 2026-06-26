# 15m-SFP × lower-TF-BOS confirm (3m & 1m) — 4 coins, pre-registered, 2026-06-26

Scalper mechanic: 15m SFP event (liquidity sweep), but BOS confirmation on a LOWER TF (3m, 1m) so it
confirms fast — entry near the level (tight R), momentum live. BTC = positive control (its
15m-SFP→15m-BOS = +0.368). Frozen percoin harness per coin; 1m loaded read-only. Stops: (a) 15m wick,
(b) LTF struct low. Targets 1.0/1.5/2.0R. Confirm window N=20 LTF bars. Read-only.

## ★ Correctness note (caught + fixed)
First pass had an alignment flaw: 15m SFPs that fired *before* the LTF data window were `bisect`-matched
to the START of the LTF dataset (months later) → garbage (SOL 3m showed R%=13.82%, 0% win). **Fixed:**
the LTF bar at the 15m fire-close must be contiguous (open within one LTF interval of t0); otherwise
the event has no valid LTF confirm and is EXCLUDED (`outrange`). All numbers below are post-fix; R% are
now physical (~0.3–0.8%). k=1: LTF bars open ≥ 15m-fire-close; BOS on their close; entry = next LTF open.

## ★ The binding constraint: LTF history is shallow → tiny n
3m starts Mar-30 (BTC) / May-11 (alts); 1m Apr-30 / Jun-08. So **34–60 of each coin's 15m SFPs are
out-of-range** (predate the LTF data). Confirmable n: BTC 12–13 (3m), alts 4–7 (3m), 1–3 (1m). **Every
cell is n<30** → SUGGESTIVE at best; none verdict-grade.

## Results (avg net-R; win%; WF; q = #quarters with n≥10)
| coin | 3m/wick (R%, MFE) | 3m/struct | 1m |
|---|---|---|---|
| **BTC** | R%0.75 MFE1.57R: 1R +0.141 · 1.5R +0.254 · **2R +0.291** (STAB,q1,n13) | R%0.49 MFE1.98R: **2R +0.372** (STAB,q1,n12) | 1m negative (−0.2 to −0.88) |
| SOL | R%0.80 MFE0.27R: all 0% win, −1.09 (n5) | MFE0.28R: 0% win −1.10 (n4) | negative (n1–2) |
| **ETH** | R%0.81 MFE1.85R: 1R +0.251 · **1.5R +0.585** (67%) · 2R −0.102 (thin,q0,n6) | R%0.43 MFE1.16R: 1.5R +0.103 (n6) | 1m/wick 1.5R +0.542 (67%,n3) |
| XRP | R%0.69 MFE0.87R: 1.5R −0.019 · 1R −0.233 (n7) | 1R −0.117 (n6) | negative (n1) |

## KEY COMPARISONS
- **★ POSITIVE CONTROL — PASSES on 3m, FAILS on 1m.** BTC 15m-SFP→**3m**-BOS @2R = **+0.291 (wick) /
  +0.372 (struct)**, matching the known 15m-SFP→15m-BOS **+0.368**. The 3m mechanic *reproduces BTC's
  edge* — and delivers the intended geometry: tighter R (0.75% vs the 15m-BOS 1.40%) and higher MFE-in-R
  (1.57–1.98R vs 1.05R). So the mechanic is VALID. **BTC 1m is negative** (−0.2 to −0.88) → 1m is too
  noisy; 3m is the usable LTF. (But BTC 3m n=12–13, single qualifying quarter → suggestive, not
  verdict-grade even for the control.)
- **ETH — most promising alt (suggestive).** 3m/wick @1.5R **+0.585** (67% win, MFE 1.85R); 1m/wick
  @1.5R +0.542 (67%). Consistent with ETH's real excursion. **But n=3–6, zero n≥10 quarters** →
  SUGGESTIVE only, far from verdict-grade.
- **SOL — dead, confirmed again.** MFE 0.27R, 0% win, all stop out, every cell ≈ −1.1. No excursion.
- **XRP — weak.** Best 3m/wick @1.5R −0.019 (breakeven), n=7. No positive.
- 1m fails everywhere (too noisy); LTF-struct stop tightens R but doesn't beat the wick stop net.

## VERDICT
- **No verdict-grade (positive + multi-quarter WF-stable + n≥30) cell on ANY coin.** The LTF mechanic
  is sound — the 3m positive control reproduces BTC's +0.368 and delivers the tight-R/high-MFE geometry
  the operator wanted — but **LTF data depth starves every test** (n 1–13; the alts have ~46d of 3m).
- **ETH 3m is the single most promising configuration** (suggestive +0.585 @1.5R, 67% win, MFE 1.85R) —
  but n=6, not promotable.
- **Nothing promoted; no live change.** The blocker is sample, not mechanic. **Next:** a forward-track /
  deeper-3m export for BTC + ETH would let the 3m-LTF-BOS mechanic be tested at n≥30 — the one path that
  could turn ETH's suggestive 3m positive into a verdict. SOL stays dead; XRP weak; 1m abandoned (noisy).

## Consolidated alt-SFP standing
BTC = live edge (15m-BOS, +0.267 pooled; also reproduced via 3m-LTF-BOS — mechanic validated). SOL =
dead. XRP = no edge. **ETH = repeatedly the nearest miss (15m-BOS×1.0R ~breakeven; 3m-LTF-BOS×1.5R
+0.585 suggestive) — the sole alt warranting a data-deepening revisit.** Live config unchanged (BTC-only).
