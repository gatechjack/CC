# SFP+BOS transfer test — SOL / ETH / XRP (pre-registered, 2026-06-26)

**Question:** does BTC's validated SFP→BOS long edge (Pooled-across-TF n=68, +0.267R @2R, WF
[STABLE+]) transfer to SOL/ETH/XRP under BTC's **exact** config — pivot(50,50), 15m/30m/1h pooled,
REAL+CONSIDERABLE, fixed 2R, stop = swept wick − 0.001·entry, long-only — with **ZERO per-coin
tuning**? Pre-registered rule: transfer = positive avgR@2R **AND** WF sign-stable **AND** n≥30.

Harness: `confluence_exp6_p6_sfp_bos_percoin.py` (byte-identical to the frozen 6e411762 oracle
except the DB-path arg; BTC re-run here as the reference row in the same execution). Each coin's
own `data/<coin>_scalping.db` (15m 2025-11-01→2026-06-26). btc_scalping.db + frozen oracle read-only.

## Summary — POOL A long (@2R), config fixed at BTC's validated settings
| coin | n | win@2R | avgR@2R | WF | transfers? |
|---|---|---|---|---|---|
| **BTC** (ref) | 68 | 38.2% | **+0.267** | STABLE+ | — (the edge) |
| SOL | 39 | 30.8% | **−0.073** | STABLE− | **NO** |
| ETH | 61 | 26.2% | **−0.242** | STABLE− | **NO** |
| XRP | 67 | 26.9% | **−0.211** | MIXED | **NO** |

k=1 PROOF mismatches=0 for every coin (no look-ahead per coin). CANDIDATES: NONE for any coin
(per-cell n<30 as on BTC; the edge, where it exists, is the pooled family).

## Per-coin detail
- **SOL** — long n=39 / 30.8% / −0.073R / [STABLE−]; short n=58 / 36.2% / +0.074R / [MIXED].
- **ETH** — long n=61 / 26.2% / −0.242R / [STABLE−]; short n=62 / 19.4% / −0.442R / [STABLE−].
- **XRP** — long n=67 / 26.9% / −0.211R / [MIXED]; short n=55 / 38.2% / +0.098R / [STABLE−]
  (the +0.098 is small-n quarters; the only n≥10 quarter, 26Q2, is −0.06 → flagged STABLE−, i.e.
  not a real edge).

## VERDICT (pre-registered, honest — negatives reported as negatives)
- **SOL: does NOT transfer.** Long is negative (−0.073R) and consistently so (STABLE−). Monitor-only.
- **ETH: does NOT transfer.** Long clearly negative (−0.242R, STABLE−) — the worst of the three.
  Monitor-only.
- **XRP: does NOT transfer.** Long negative (−0.211R), WF MIXED. Monitor-only.
- **Overall: BTC's SFP→BOS long edge is BTC-SPECIFIC. It does not generalize to SOL/ETH/XRP.**
  None is re-fit; none is promoted. This **confirms the current live configuration** (BTC the sole
  live-traded SFP coin; SOL/ETH/XRP capture-only / monitor-only). The short-side reference numbers
  are not stable-positive edges either and live is long-only — not pursued.

No goalposts moved: the pre-registered bar (positive + WF-stable + n≥30) is met by **BTC only**.
