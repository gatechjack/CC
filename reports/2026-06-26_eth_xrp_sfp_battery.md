# ETH + XRP — full SFP battery (pre-registered), 2026-06-26

ETH/XRP passed the excursion gate (ETH MFE 1.52R, XRP 1.25R, both ≥1R) and were negative @2R only
because 2R sits above their median move. **Phase 1:** does a target matched to their real excursion
(≤ MFE) convert the raw material into a positive + WF-stable edge? Frozen percoin harness, SFP-direct,
15m, honest taker entry (ENTRY_FEE) + maker TP / taker stop. **Gate: avg-R > 0 AND WF STABLE+ AND
n≥30.** Stop at the first phase a coin fails. Read-only.

## PHASE 1 — target-to-excursion ladder (15m SFP-direct)
### ETH (n=55, k=1 0/28)
| target | n | win | avg net-R | WF | gate |
|---|---|---|---|---|---|
| 1.0R | 55 | 50.9% | −0.096 | MIXED | fail |
| 1.25R | 55 | 49.1% | −0.010 | MIXED | fail |
| **1.5R** | 55 | 45.5% | **+0.020** | MIXED | **fail** (positive but WF-MIXED) |
| 2.0R (ref) | 55 | 32.7% | −0.140 | STABLE− | fail |

ETH 1.5R per-quarter: **25Q4 −0.47 (n12)**, 26Q1 +0.14 (n26), 26Q2 +0.18 (n17). The only n≥10 quarters
are these three; **25Q4 is negative** → MIXED, not STABLE+. So the +0.020 is two recent quarters over
one negative well-sampled quarter — near-breakeven and not stable.

### XRP (n=62, k=1 0/21)
| target | n | win | avg net-R | WF | gate |
|---|---|---|---|---|---|
| 1.0R | 62 | 53.2% | −0.086 | STABLE− | fail |
| 1.25R | 62 | 45.2% | −0.138 | STABLE− | fail |
| 1.5R | 62 | 38.7% | −0.189 | MIXED | fail |
| 2.0R (ref) | 62 | 33.9% | −0.144 | MIXED | fail |

XRP: every target negative.

## What the ladder shows (honest)
- **Target-to-excursion worked DIRECTIONALLY** — it validated the diagnosis. ETH improved monotonically
  as the target dropped toward its MFE: 2R −0.140 → 1.5R +0.020 (crossed zero); the wrong (too-far)
  target WAS the main drag. XRP also improved (2R −0.144 → 1.0R −0.086) but stayed negative.
- **But neither passes the gate.** ETH's best (1.5R) is +0.020 with MIXED WF (25Q4 negative) — economically
  ~breakeven and not WF-stable. XRP never turns positive at any target. **No WF-stable positive target
  exists for either coin.**

## VERDICT — STOP at Phase 1 (gate failed for both)
- **ETH — FAIL.** Closest of all alts (1.5R +0.020) but WF-MIXED + ~breakeven; not a stable edge. No
  WF-stable target → **Phase 2 does not trigger** (it runs only on a WF-stable Phase-1 target). Stop.
- **XRP — FAIL.** All targets negative. Stop.
- **Neither earns Phase 2 (tight-stop) or Phase 3 (BTC-confirm)** — per the discipline, a coin that
  fails the gate is not pushed forward.
- **ETH + XRP stay monitor-only.** Live config unchanged (BTC-only).

## Consolidated SFP conclusion across all coins
- **BTC** — the edge (BOS-confirmed, wide stop, +0.267R pooled / +0.368 15m). Live.
- **SOL** — structurally dead (MFE 0.62R < 1R; no excursion; battery confirmed).
- **ETH / XRP** — HAVE the excursion (MFE 1.25–1.52R), and matching the target to it closes most of
  the gap (ETH to ~breakeven), but it does NOT convert to a positive + WF-stable edge at n≥30.
- **Net: the SFP→BOS edge is BTC-specific.** ETH is the nearest miss (target-matched ~breakeven, not
  stable) but not promotable. All three alts remain monitor-only. No live change.
