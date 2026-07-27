# PMCC tiles mislabeled "COVERED CALL" — read-only diagnosis (fix proposed, not built)

**Date:** 2026-07-27 · **Code:** actual prod (`claude-2026-07-26`/`prod-live d553a3e`; classifier is in `data.py`, untouched by the tile-status fix). **Scope:** READ-ONLY. Broker / auto_execute / halt untouched.

## TL;DR

The tile strategy badge (`PMCCPair.structure_type`) classifies a long-call+short-call as `covered_call`
whenever the long call has **< 180 days remaining** — a proxy for "is this a LEAP" that **breaks as a real
LEAP ages**. The 5 mislabeled names all hold **2027-01-15** LEAPs (~**172 DTE**, just under the cutoff); the
5 correct names hold **2028-01-21** LEAPs (~1639 DTE). It is **not** a shares/assignment signal — the
classifier never looks at equity shares. **The label is DISPLAY-ONLY** (sole consumer: `pmcc_pair.html`); it
does **not** feed roll logic, B-AE assignment/exercise monitoring, cover/naked detection, or any risk gate,
so the mislabel is cosmetic and does **not** mis-handle assignment.

---

## 1. The classifier (exact code)

`trading_corp/web/data.py:190-211` — `PMCCPair.structure_type`:

```python
@property
def structure_type(self) -> str:
    if self.leap and self.short_call:
        return "pmcc" if (self.leap.dte or 0) >= 180 else "covered_call"   # <-- line 203, the bug
    if self.leap and not self.short_call:
        return "uncovered_leap" if (self.leap.dte or 0) >= 180 else "naked_call"
    if self.short_call and not self.leap:
        return "short_only"
    return "other"
```

Pairing (`data.py:5824-5871`, `_group_pmcc_pairs`): `leap = max(long_calls, key=lambda l: l.dte or 0)`
(the longest-dated long call), `short = min(short_calls, key=dte)`. The old "qualify as PMCC by DTE" filter
was **removed** (docstring 5830-5832), so `structure_type` is now the **sole** classifier, and it keys
entirely off `leap.dte`. `leg.dte` is populated from the broker: `data.py:3436` `dte=op.get("dte")` (days to
expiry from `get_option_positions_detail`).

**The classifier never reads equity shares** — so "covered_call" here is a misnomer; it actually means
"long call with < 180 DTE + a short call," not "shares-backed short call."

## 2. The deciding field — TSLA (mislabeled) vs IREN (correct)

The single divergent field is **`leap.dte`**, straddling the hard-coded `180`:

| | leap (longest long call) | `leap.dte` (today = 2026-07-27) | `(dte or 0) >= 180` | `structure_type` |
|---|---|---|---|---|
| **TSLA** (mislabeled) | 2027-01-15 $310C long (`639b5a25`) | **≈172** | False | **covered_call** |
| **IREN** (correct) | 2028-01-21 long (`98eea714`) | **≈1639** | True | **pmcc** |

No other field differs: both have a long call + a short call, neither consults shares, `opening_strategy`,
a stored `strategy_type`, or per-symbol config. The split across your whole book is **100% explained by LEAP
expiry**:

- **2027-01-15 LEAPs → 172 DTE < 180 → covered_call:** HOOD (`db262f20`/`33dc8eaf`), TSLA (`639b5a25`),
  MSTR (`809ab4a3`/`3fed1233`/`30b66242`), OPEN (`1de32405`), BULL (`a87e0b54`). *(CIFR `1261e295` is also a
  2027-01-15 LEAP → would mislabel identically if its tile is shown.)*
- **2028-01-21 LEAPs → ~1639 DTE ≥ 180 → pmcc:** IREN, RKLB (`b80d9f5c`), SMR (`e2e72d5f`), BLSH
  (`bb33639a`), RIOT (`345d6018`).

Root cause: these 2027-01-15 calls were **opened as LEAPs** but have **aged below the 180-day cutoff**, so
the classifier now treats them as covered calls. It conflates "long call with ≥180 DTE remaining" with
"LEAP," and "not-a-LEAP long call" with "covered call."

## 3. Scope — DISPLAY-ONLY (every consumer of the label)

`structure_type` (and its `struct_label`) is consumed in exactly **one** place:

- `trading_corp/web/templates/partials/pmcc_pair.html:16-17,52` — `{% set struct = pair.structure_type %}`
  → `struct_label` map → `{{ struct_label }}` renders the small type pill on the collapsed tile. **Pure
  display.**

It is **NOT** read by any decision/execution path:
- **Roll logic** — `pmcc_robinhood.py` has **zero** references to `structure_type`; it manages LEAP+short
  structurally ("uncovered LEAP" = a long call with no covering short: `:1006/:2385/:3410/:3426`), never via
  the web label.
- **B-AE assignment/exercise monitoring** (`pmcc_robinhood.py:4142-4214`, MONITORING-ONLY) reads the broker
  short-leg fields `pending_assignment_quantity` / `pending_exercise_quantity` / `pending_expiration_quantity`
  (`:4181-4182`) and ITM-near-expiry — **not** `structure_type`, and it has **no** "shares-covered" concept.
- **Cover / naked-leg detection** — uses long-call-vs-short-call presence (`:2762/:3302/:3596`), never the
  label or shares.
- **Risk gate** — `risk.py:184-194` branches on `is_option` (from `order.extra`); the words "covered call"
  at `:185/:189` are an **explanatory comment** for why option sells skip counter-trend sizing, not a read of
  the label.
- **Unrelated `covered_call` code** — `data.py:1807-1896` (`build_ira_view` / `CoveredCallPosition`),
  `routes.py:884-907/3842/4339`, `ira_*.html` are the **separate `robinhood_ira`** dashboard for *real*
  share-backed covered calls; a different model and code path, not the PMCC tile.

**Conclusion:** the feared failure mode ("a PMCC mislabeled as a covered call mis-handles assignment") does
**not** occur — assignment handling never reads the label. The bug is cosmetic (a misleading tile pill +
minor operator confusion). Low-risk to fix. (Note: `priority_score`, which does drive tile **sort** order,
also uses `leap.dte` thresholds but **not** `structure_type`; sort-only, still display.)

## 4. Proposed fix (don't build yet)

The correct discriminator is **what covers the short**, not the long call's remaining DTE:
**long call (LEAP) + short call = PMCC; equity shares + short call = covered call.** A `PMCCPair` is built
from **option legs only** and always has a *long call* as the cover — so within this view it is **always a
PMCC**, never a covered call.

**Primary fix (minimal, correct, low-risk):** in `structure_type`, drop the `>= 180` downgrade — a long
call + short call is a PMCC regardless of the long leg's remaining DTE:

```python
if self.leap and self.short_call:
    return "pmcc"                      # the long call covers the short — remaining DTE is irrelevant
if self.leap and not self.short_call:
    return "uncovered_leap"            # (optionally keep a 'diagonal'/'short-LEAP' nuance, but NOT 'covered_call')
```

This immediately corrects all five (TSLA/HOOD/MSTR/OPEN/BULL → `pmcc`) and leaves the genuine PMCCs
unchanged. Since only STRC holds real shares (164.69) and it has **no** short call, there is **no** real
covered call in this division today — so the `covered_call` branch is effectively unreachable-and-wrong here
and should be removed from the PMCC classifier.

**Optional (only if a real shares-backed covered call could ever appear in this division):** make the
classifier shares-aware — thread the underlying's share count (already available as `stock_holdings` in
`build_division_view`) into `PMCCPair`, and reserve `covered_call` for *no long call + shares ≥ 100×|short
qty| + short call*. Not needed for the current book; the primary fix is sufficient and safe.

**Also worth fixing (same root, lower severity):** the `uncovered_leap` vs `naked_call` split at `:204-208`
has the identical aging artifact (an aged, still-long LEAP with no short flips to `naked_call` at 180 DTE).
Either collapse to `uncovered_leap` or gate on "was opened as a LEAP" rather than remaining DTE.

**Risk:** display-only change, no engine/broker/risk behavior affected; keep the diagnosis's read-only
guarantee. No SQL, no auto_execute/halt, no re-roll.
