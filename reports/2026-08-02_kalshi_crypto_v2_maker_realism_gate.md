# S4 ETH Maker — Latency / Realism Gate (shadow-build gate)

**Date:** 2026-08-02 · **Standing:** read-only; on-disk; lab DB only; evidence only — no verdict.

The maker-resolution survivor (model side, traded-close rest) re-run with the two optimisms removed: **(a)** resting level = the PRIOR minute's traded close (no same-minute-close look-ahead); **(b)** fills only from the minute AFTER placement; **(c)** the full 1b pessimism stack (2-tick through + fill 1 tick worse + skip entry min 1-2). Placement minute p = first-tradeable-minute + delay. **per-ATTEMPT** $/ct (no-fills@$0) with fill_rate beside it; the optimistic queue-free fill still stands (the shadow is the live arbiter).

> **Gate:** if the ETH per-ATTEMPT dies here (≤0 or within ~2 SE), the maker-shadow would soak a non-edge. Note: realism-only m+0 drops windows whose first tradeable minute is 1 (no prior-minute close exists) — n is smaller there by construction.

## BTC

### realism only (a+b)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 0 | n/a | n/a | n/a | n/a/n/a |
| m+1 | 1298 | -0.0120 (t=-1.0) | -0.0142 (t=-1.0) | 85% | 52%/100% |
| m+2 | 1298 | -0.0136 (t=-1.1) | -0.0162 (t=-1.1) | 84% | 51%/100% |

### realism + full pessimism (a+b+c)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 1298 | -0.0298 (t=-2.5) | -0.0363 (t=-2.5) | 82% | 50%/100% |
| m+1 | 1298 | -0.0234 (t=-2.0) | -0.0281 (t=-2.0) | 83% | 51%/100% |
| m+2 | 1298 | -0.0410 (t=-3.7) | -0.0512 (t=-3.7) | 80% | 49%/100% |

## ETH

### realism only (a+b)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 0 | n/a | n/a | n/a | n/a/n/a |
| m+1 | 1298 | +0.0218 (t=+1.8) | +0.0260 (t=+1.8) | 84% | 56%/100% |
| m+2 | 1298 | +0.0102 (t=+0.8) | +0.0123 (t=+0.8) | 83% | 56%/100% |

### realism + full pessimism (a+b+c)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 1298 | -0.0022 (t=-0.2) | -0.0026 (t=-0.2) | 82% | 55%/100% |
| m+1 | 1298 | -0.0014 (t=-0.1) | -0.0017 (t=-0.1) | 82% | 55%/100% |
| m+2 | 1298 | -0.0148 (t=-1.3) | -0.0185 (t=-1.3) | 80% | 54%/100% |

## SOL

### realism only (a+b)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 0 | n/a | n/a | n/a | n/a/n/a |
| m+1 | 1298 | -0.0282 (t=-2.3) | -0.0329 (t=-2.3) | 86% | 50%/100% |
| m+2 | 1298 | -0.0343 (t=-2.8) | -0.0408 (t=-2.8) | 84% | 49%/100% |

### realism + full pessimism (a+b+c)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 1298 | -0.0459 (t=-3.8) | -0.0551 (t=-3.8) | 83% | 49%/100% |
| m+1 | 1298 | -0.0451 (t=-3.8) | -0.0543 (t=-3.8) | 83% | 49%/100% |
| m+2 | 1298 | -0.0546 (t=-4.8) | -0.0674 (t=-4.8) | 81% | 47%/100% |

## XRP

### realism only (a+b)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 0 | n/a | n/a | n/a | n/a/n/a |
| m+1 | 1298 | -0.0221 (t=-1.8) | -0.0257 (t=-1.8) | 86% | 51%/100% |
| m+2 | 1298 | -0.0254 (t=-2.1) | -0.0296 (t=-2.1) | 86% | 50%/100% |

### realism + full pessimism (a+b+c)

| placement | n attempts | per-ATTEMPT (t) | per-fill (t) | fill_rate | filled/unfilled win% |
|---|---|---|---|---|---|
| m+0 | 1298 | -0.0417 (t=-3.5) | -0.0498 (t=-3.5) | 84% | 49%/100% |
| m+1 | 1298 | -0.0418 (t=-3.6) | -0.0503 (t=-3.6) | 83% | 49%/100% |
| m+2 | 1298 | -0.0456 (t=-4.0) | -0.0562 (t=-4.0) | 81% | 47%/100% |

## Reading this (evidence, not verdict)

- **ETH per-ATTEMPT under realism + full pessimism is the gate.** Positive & |t|≥2 across placement delays ⇒ the survivor tolerates the realism fixes and the shadow is worth soaking. ≤0 / within noise / decaying with delay ⇒ it was optimistic-fill or same-minute look-ahead, and the shadow soaks nothing.
- BTC/SOL/XRP shown as controls (were ~0 / negative in resolution).
- The queue-free fill remains the one optimism this on-disk test cannot remove — that is exactly what the live shadow measures.

