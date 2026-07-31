# OptiTrade AI -- spec-diff of implemented signals vs vendor Pine source

Vendor source now on hand (`vendor_optitrade_ai.pine`; PII license block redacted).
Diff of the ENTRY-signal rules only (the repainting exit/label layer is not
reproduced, by design -- see the vendor-methodology note).

## Four audited areas -- ALL MATCH

| area | vendor | mine | match |
|---|---|---|---|
| EMA source | all ribbon EMAs on `hlc3`; MACD on `close` | same | ✓ |
| Lengths (Normal) | `o1..o10 = ema(hlc3, 30,40,50,60,70,80,90,100,110,120)` | `[30..120]` | ✓ |
| Lengths (Very High) | `q1..q10 = ema(hlc3, 60,80,100,120,140,160,180,200,220,240)` | `[60..240]` | ✓ |
| isbull 3-bar chain | `isbull(s)=> s>s[1] and s[1]>s[2] and s[2]>s[3]` | identical | ✓ |
| freshness | `buy2 = buy1 and not buy1[1..5]` (5 prior bars) | `allbull[i] and not allbull[i-1..i-5]` | ✓ |
| MACD filter | 12/26/9 on close; buy needs `hist[1]<hist and hist>=0` | `hist[i]>hist[i-1] and hist[i]>=0` | ✓ |

## The one residual -- CONTINUATION spacing -- is MATERIAL (not just off-by-one)

**Vendor** (`data()`): `buy = buy2 and ta.barssince(buy2[1])>30` (and the `sell`
mirror; reversal uses the same construct `buy4 = buy3 and ta.barssince(buy3[1])>15`).

`ta.barssince(buy2[1])` at bar `i` = `i - p - 1`, where `p` = the most recent
**prior fresh event** (`buy2`). So a fresh event emits iff `(i - p - 1) > 30`
(i.e. **gap-from-previous-FRESH >= 32**), and **the spacing clock resets on every
`buy2` (fresh) event, whether or not it emitted**. (First fresh event: `barssince`
is `na` -> does not emit.)

**Mine** (`optitrade_ai_signals.py`, `gen_signals` emission loop): emit a fresh
event iff `(i - last_EMITTED) > 30` -- **the clock resets on EMISSION**, not on
every fresh event, and the threshold is `>30` from the last emission (gap >= 31).

Two differences: (1) **reference point** -- vendor spaces off the previous *fresh
event*, I space off the previous *emitted* signal; (2) a **+1 threshold** (vendor
gap>=32 from last fresh, mine gap>=31 from last emission). (1) dominates: because
mine's last-emitted is always <= vendor's last-fresh, **my emitted set is a strict
superset of the vendor's** -- I never emit fewer, and I emit extras wherever a
fresh event fell within 32 bars of a prior (unemitted) fresh event.

## Quantified impact -- ETH 1h Normal/continuation (`spec_diff_spacing.py`)

| venue | raw fresh (in-window) | MINE emitted | VENDOR emitted | overlap | only-mine | only-vendor |
|---|--:|--:|--:|--:|--:|--:|
| Binance | 587B/605S | 842 | 733 | 733 | 109 | **0** |
| Bybit | 344B/380S | 515 | 447 | 447 | 68 | **0** |

Vendor emits **~13-15% fewer** signals; mine ⊃ vendor exactly (0 only-vendor).
Through the bracket (SL 2.5ATR, RR3.5, sl-first, 5 windows):

| venue | metric | MINE (as-run) | VENDOR-exact |
|---|---|--:|--:|
| Binance | n / gross / net06 / net06+ | 352 / +43.8 / **+24.1** / 3/5 | 335 / +28.4 / **+9.5** / 3/5 |
| Bybit | n / gross / net06 / net06+ | 202 / +53.3 / **+43.0** / 5/5 | 196 / +37.8 / **+27.7** / 4/5 |

The ~17 trades vendor drops were **net-positive** (they were the extra signals my
looser clock let through), so vendor-exact net06 is **~60% lower on Binance**
(+24.1 -> +9.5) and **~36% lower on Bybit** (+43.0 -> +27.7), and **Bybit
windows-positive falls 5/5 -> 4/5**.

## Verdict + scope

- **MATERIAL.** The ETH 1h continuation lead **survives directionally** (still net06-
  positive on both venues under vendor-exact) but is **substantially weaker** than
  the numbers reported in `AI_RESULTS.md` / `VALIDATION.md`.
- **Scope beyond ETH 1h:** every **continuation** config in the prior transplant +
  validation studies used my looser spacing, so their continuation net06 is
  **overstated**. **Reversal** configs share the same class of bug
  (`barssince(buy3[1])>15`, clock on crossover events vs my last-emitted), though
  the effect is smaller there (crossovers are naturally spaced). MACD-on configs
  have an additional order-of-operations nuance (vendor applies MACD *after*
  spacing; the spacing clock ignores MACD) -- irrelevant to the ETH 1h lead (MACD
  off).

## Recommendation

Run item 3 on the **vendor-exact** signals (the corrected numbers above are the
honest baseline). Held for your go per the "material divergence = stop and show me"
gate. On go, I'll also flag that the prior continuation numbers should be read as
the looser-spacing variant.
