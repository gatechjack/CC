# ETH + XRP — SFP excursion-vs-stop root cause (pre-registered, 2026-06-26)

**Why:** SOL failed because median favorable excursion (~0.6R) < stop (~1R) → reward<risk (no move to
chase). ETH (deepest book after BTC) and XRP (sharp large moves) are different instruments — do they
share SOL's geometry, or do they have the excursion? Measured directly via the frozen percoin harness
on eth_/xrp_scalping.db (BTC config fixed), SFP-direct entry (healthy n), 15m primary + 3m/30m/1h.
Read-only. Pre-registered gate: **MFE ≥ 1R → earns the full battery; MFE < 1R → earned 'dead'.**

## ★ SUMMARY — 15m SFP-DIRECT (the root-cause metric)
| coin | n | **MFE (R)** | entry→stop R% | avgR@2R | WF | MFE ≥ 1R? |
|---|---|---|---|---|---|---|
| BTC (ref) | 56 | 1.04 | 0.52% | −0.066 | MIXED | YES |
| SOL (dead) | 55 | **0.62** | 0.52% | −0.424 | STABLE− | **no (sub-1R)** |
| **ETH** | 55 | **1.52** | 0.64% | −0.140 | STABLE− | **YES** |
| **XRP** | 62 | **1.25** | 0.45% | −0.144 | MIXED | **YES** |

k=1 mismatches = 0 for every coin/TF.

## The finding: ETH/XRP do NOT share SOL's dead-by-no-excursion cause
- **SOL: MFE 0.62R < 1R** — the move dies *before* the stop distance. Structurally dead (and we proved
  tight-stop made it worse). Earned 'dead'.
- **ETH: MFE 1.52R** — the HIGHEST of all four, above even BTC (1.04R). In % terms ETH's median
  favorable move is ~0.78% (vs SOL's 0.38%, ~2×). The move is genuinely there.
- **XRP: MFE 1.25R** — also clears 1R (median move ~0.55%).
- So the raw material (follow-through) that SOL lacks, ETH and XRP HAVE. They are **not dead for SOL's
  reason.**

## But they are NOT a current edge (honest)
ETH/XRP @2R direct are still negative (−0.140 / −0.144) and their BOS views are worse (ETH BOS −0.371
STABLE−, XRP BOS −0.231 STABLE−). Why negative despite MFE ≥ 1R? **The 2R target sits above the median
move** (MFE 1.25–1.52R < 2R) → only ~33% reach 2R. This is a **target/geometry** shortfall, NOT the
no-excursion structural death SOL has — i.e. exactly the situation a tight-stop and/or a target ≤ MFE
could convert. (Note: the BOS-wait does not help ETH/XRP — unlike BTC, where BOS is the edge.)

Cross-TF: ETH/XRP excursion is best at **15m** (ETH 1.52R, XRP 1.25R) and 30m (ETH 1.40R), weakest at
3m (0.46/0.72R) — consistent with the live 15m TF.

## VERDICT (per coin, pre-registered — SOL's verdict did NOT pre-judge these)
- **ETH — EARNS THE FULL BATTERY.** MFE 1.52R ≥ 1R (best of all coins). Not SOL-dead. Currently −0.14
  @2R, but that's a target/geometry gap over real excursion → run the deeper tests (target optimization
  with target ≤ MFE, tight-stop, BTC-confirm) as SOL got.
- **XRP — EARNS THE FULL BATTERY.** MFE 1.25R ≥ 1R. Not SOL-dead. Same deeper-test path.
- **SOL — earned 'dead'** (MFE 0.62R < 1R), confirmed by the failed battery. Stays monitor-only.

Net: ETH and XRP have the excursion SOL lacked — they pass the root-cause gate and merit the full
battery (NOT run here; this was the excursion measurement). Reporting them negative-at-2R as negative,
but the structural reason that killed SOL does **not** apply to ETH/XRP. n≥30 satisfied (no positive
to gate; none positive yet).
