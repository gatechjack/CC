# GLD no_wing diagnosis — 2026-08-13 (read-only; propose, don't implement)

Question: is GLD's 15:45 no_wing skip a config problem, and is GLD tradeable at ~$4k?
Data source: live Robinhood chain (MCP market-data reads, account-agnostic). Spot GLD $398.96,
IV ~22-23%, target expiry 2026-09-25 (highest DTE in [30,45] = 43 DTE, same as SPY today).

## 1. Strike-grid geometry (GLD 2026-09-25)
- **<= $345: $5 grid** (…335, 340, 345)
- **$346 -> $418: $1 grid** (346, 347, … 417, 418) — spans the money ($399) and the put-wing zone
- **> $418: $5 grid** (417, 418, **420, 425, 430, 435, 440…**; 419 not even listed)

So the near-the-money grid is FINE ($1) — a $3 wing IS listable at/below the money. GLD is NOT
structurally coarser than $3 near ATM. The coarseness is only in the **OTM call region (> $418)**.

## 2. Where the short strikes land (real deltas)
Short target |delta| 0.20, band [0.15, 0.25]:
- **Put short ~378** (delta -0.213). Wing (width 3) = 375 -> LISTED ($1 grid). Put spread builds fine.
- **Call short ~430** (delta 0.198, nearest 0.20). Wing (width 3) = 433 -> NOT LISTED (grid: 430, 435).
  Every call strike in the 0.15-0.25 band (425=0.238, 430=0.198, 435=0.163) is on the $5 grid; NONE
  has a listed $3 wing (428/433/438 all unlisted). -> **no_wing on the call side.**

A condor needs all four legs; the unlistable call wing skips the whole structure (put side is fine).

## 3. Frequency: EVERY eval, not incidental
The entire call short band [425-435] sits above the $418 $1-grid ceiling. For the call short to drop
into the $1 grid (<= 418) GLD would need ~13-14% IV (25-delta call < ~5% OTM at 43 DTE). GLD trades
~12-20% IV; sub-14% is rare and still wouldn't pull the 20-delta target (~430) below 418. Verdict:
**no_wing on GLD's call side at width 3 essentially every eval — effectively disabled-by-geometry.**

## 4. Width-5 risk math (the only geometrically-viable width)
$5 wings land on the $5 grid (short 430 -> long 435 LISTED; short 378 -> long 373 LISTED). Real mids:
- Put spread 378/373: credit = 3.825 - 2.880 = **0.945**
- Call spread 430/435: credit = 3.350 - 2.660 = **0.690**
- **Total credit ~ $1.635**; max_risk = (5 - 1.635) x 100 = **~$336 / contract** (range ~$330-350).

Against the gates at E = $3,840 (today's snapshot):
- credit_floor (0.30 x 5 = $1.50): 1.635 >= 1.50 -> passes (barely).
- **risk_band [50 x width, 250] = [250, 250]: max_risk $336 EXCEEDS the $250 absolute ceiling -> risk_band SKIP.**
  (The $250 risk_band_max_usd is a hard absolute cap; a width-5 20-delta condor can never satisfy it —
  it would need credit >= $2.50, i.e., a ~40-delta condor.)
- sizing: floor(0.055 x 3840 / 336) = floor(211 / 336) = **0 contracts -> budget skip.**
  1 contract needs 0.055 x E >= 336 -> **E >= ~$6,120.**

(Method check: SPY today filled credit 0.91, max_risk (3-0.91)x100 = $209 — matches the audit row.)

## 5. Verdict — GLD is effectively BENCHED at $4k, regardless of a width change
- **width 3** (current): call wing unlisted -> no_wing every eval. Fails safe (skips, logs, places nothing).
- **width 5**: only listable width, but max_risk ~$336 breaks BOTH the $250 risk_band ceiling
  (risk_band skip) AND the ~$211 rung budget (0 contracts). Not tradeable at $4k.
- No smaller fallback helps: below $418 only $5 multiples exist, so only width 5/10 give a listable
  call wing, and both blow the $250 ceiling. MACE uses one width per symbol (no asymmetric put/call width).

To trade GLD 1-contract would require ALL of: (a) width -> 5, (b) risk_band_max_usd -> >= ~$340,
(c) equity -> >= ~$6,120. At current $4k, no config change alone makes GLD tradeable.

## Options for the Board (no change made; awaiting ruling)
- **A. Leave as-is.** GLD logs a harmless no_wing skip daily; places nothing. Zero risk, but the daily
  audit line and "2-symbol universe" framing overstate GLD's activity (it is inert by geometry).
- **B. Disable GLD** (`symbols.GLD.enabled: false`) to reflect reality and stop the daily no_wing noise;
  re-enable at the equity milestone. Cleanest honest state; keeps universe truthful.
- **C. Defer to an equity milestone (~$6.5k+):** re-enable GLD with width 5 + risk_band_max_usd bump.
  Note the $250 ceiling is a Board-ratified risk parameter (width-scaling ruling) — bumping it is a
  risk-policy change, not just a per-symbol tweak, and would also affect any future width-5 name.

Recommendation: **B now, C later.** GLD cannot contribute at this account size; disabling it is the
honest state and removes the misleading daily skip. Revisit width-5 + ceiling only past ~$6.5k equity.
