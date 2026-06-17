# Bracket deploy — DRIFT BLOCKER (package build halted) — 2026-06-17

PREPARE-only. Deploy payload = 6 targeted files from rebased tip `b077b66` onto live base `a64a42f`.
Drift guard run BEFORE building the apply script (per §4 / operator instruction). **2 of 6 files drifted → STOP.**

## md5 drift table (LF-normalized; method calibrated: a64a42f:observer == prod eec6bda6)

| File | BASE `a64a42f` | PROD now | TARGET `b077b66` | verdict |
|---|---|---|---|---|
| observer.py | eec6bda6 | eec6bda6 | 13469b10 | ✅ prod==base → clean full-file |
| reconciler.py | bf048cd1 | bf048cd1 | 386cc6c2 | ✅ prod==base → clean full-file |
| brokers/bitunix.py | 70f7904f | 70f7904f | 7a3da849 | ✅ prod==base → clean full-file |
| bitunix_bracket.py | (new) | ABSENT | bd639224 | ✅ create |
| **data_exec.py** | 1804ef54 | **e3e4cca7** | 51281fbd | ❌ **DRIFT** |
| **logger.py** | e8b54f8f | **2938e089** | e625c388 | ❌ **DRIFT** |

## What prod actually runs on the 2 drifted files
- `data_exec.py` `e3e4cca7` = commit **c9e99cb (2026-05-30)** — far behind base.
- `logger.py` `2938e089` = commit **69c401a (2026-05-28)** — far behind base.
- ⇒ The polymarket cutover deployed `main.py`/`db.py` (superset) but **left these two at late-May**. Prod is *behind* `a64a42f` here.

## The drift delta (prod → base) — why a full-file overwrite is unsafe
- `data_exec.py`  c9e99cb..a64a42f = **`b0ae39d`** (bitunix 10006 — data_exec residual; broker part already live) + **`f692fa2` polymarket E2.5** (execution_mode column).
- `logger.py`  69c401a..a64a42f = **`f692fa2` polymarket E2.5** only.
- ⇒ A full-file overwrite from `b077b66` would ship the **un-deployed polymarket E2.5 change** onto prod alongside the bitunix edits. **That is a polymarket-affecting change → out of scope for this bitunix solo deploy (hard stop: polymarket → STOP).**

## Bitunix branch's own edits to these files (what we actually want)
- `data_exec.py`: +29/−10 (52 lines touched) — incl. the #5-C exit-exemption.
- `logger.py`: +44/−5 (49 lines touched).
- These were authored on top of `a64a42f` (which already has E2.5), so they are **not** cleanly separable from E2.5 without a re-test on prod's old blob.

## Secondary flag — cutover left E2.5 half-deployed (VERIFIED)
Prod `db.py` = `a2c2ff46` (E2.5 superset) and the **`execution_mode` column IS present** on both `proposed_order` and `paper_trade_record` (verified read-only). But the live writers — `data_exec.py` (05-30) / `logger.py` (05-28) — are **pre-E2.5 and never populate it**. So the cutover deployed the E2.5 *schema* but not the E2.5 *write-side*; the column currently takes its default. Shipping the target (a64a42f-based) `data_exec`/`logger` would complete that write-side. Schema-safe (column exists) but it is a **live-polymarket-path behavior change** → operator/polymarket call.

## What the data_exec/logger edits actually are (essentiality — VERIFIED)
- `logger.py`: makes `log_proposed_order` **lock-resilient** (retry-on-locked, never raise) = the **#3 orphan-prevention core**. NOT auxiliary.
- `data_exec.py`: #5-C exit-exemption + #3 "fill is real past `place_order`" wrapping = **core**.
- ⇒ Cannot defer these 2 files without shipping an incoherent bundle (bracket without the orphan-prevention it relies on). Their ONLY non-bitunix content is polymarket **E2.5** (`execution_mode` writes); the #3/#5-C edits are layered on top of it.

## Clean vs blocked
- **CLEAN (prod==base, deployable now):** observer.py, reconciler.py, brokers/bitunix.py + new bitunix_bracket.py.
- **BLOCKED (drift entangles polymarket E2.5):** data_exec.py, logger.py.

## DECISION 2026-06-17 — HOLD, coordinate E2.5 first (operator)
Operator chose to **HOLD the package build** and coordinate the E2.5 question before proceeding.
Reduced-scope "ship 4 clean files only" is OFF the table (data_exec/logger are #3/#5-C core — incoherent without them).

**Coordination question to resolve (polymarket/monitoring session + operator):**
> Is it wanted to activate the E2.5 `execution_mode` writes on the LIVE polymarket path now? Deploying the target `data_exec.py`/`logger.py` (needed for the bitunix #3/#5-C bundle) will start populating `execution_mode` on `proposed_order`/`paper_trade_record`. The column already exists on prod (schema-safe); this only completes the write-side the cutover left undone.

## RESUME PLAN (when E2.5-on-live is confirmed)
**If YES → full 6-file build:**
- Drift guard gates **prod-CURRENT** md5 (an operator-approved deviation from "prod==base") for the 2 drifted files, plus prod==base for the 3 clean files, plus create for the new module:
  - data_exec.py:  prod `e3e4cca7` → target `51281fbd`
  - logger.py:     prod `2938e089` → target `e625c388`
  - models.py:     prod `96cf31c4` → target `a781b495`   ← ADDED (see execution_mode reader audit): E2.5 coupled trio — the new logger INSERT binds :execution_mode against models.to_db_row(); prod models.py (f66722e) is pre-E2.5 → shipping logger without models breaks ALL proposed_order writes. **7-file set.**
  - observer.py:   prod `eec6bda6` (==base) → target `13469b10`
  - reconciler.py: prod `bf048cd1` (==base) → target `386cc6c2`
  - bitunix.py:    prod `70f7904f` (==base) → target `7a3da849`
  - bitunix_bracket.py: ABSENT (create) → target `bd639224`
- Then: stage from `b077b66` (LF) → md5-gate (prod==listed, staged==target) → backup `*.bak-pre-bracket-2026-06-17` → py_compile 6 → atomic-mv → re-verify == target. No restart in script.
- VERIFY + rollback per the package plan (TODO once unblocked).

**If NO (keep strictly separate) → targeted-hunk path:** hand-build data_exec/logger = prod May blob + only the #3/#5-C/10006 hunks (no E2.5; old SQL keeps execution_mode unwritten = today's behavior), re-run the full suite on the actual prod blobs, then build the apply with those hand-built targets. Lower confidence; more work.

Apply script + VERIFY + rollback are **NOT built** pending this decision (would have been built for the wrong base otherwise).
