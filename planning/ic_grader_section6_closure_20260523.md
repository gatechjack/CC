# IC Grader — §6 Live-Verification CLOSED Locally (2026-05-23)

**Status:** §6 ship gate **CLOSED** locally for IC candidate grader
(committed at `112aef3`, not yet deployed). Gate [3] (CRLF-normalized
deploy + prod §6 re-run) remains open.

**Adjudication recorded for the next session so this fork is not
relitigated.**

---

## What §6 was checking

The intent: prove the real Tastytrade SDK feeds **real numbers** into the
grader's **gate-7 (term_structure)** comparison, and that the comparison
actually executes — not the `NEEDS_LIVE_DATA` None branch which the
existing mock-based unit tests already cover. This closes the exact blind
spot escalated in `[[feedback-mocks-dont-catch-sdk-shape]]`.

## Spec source

`runbooks/session_start_2026_05_23.md` lines 76–95, cross-checked against
the actual gate implementations in
`trading_corp/agents/strategies/ic_candidate_grader.py`. The
phantom-pointer plan doc `.claude/plans/planning-session-ic-hashed-kettle.md`
referenced in commit `112aef3`'s message does NOT exist (never
committed) — see `[[session-committed-phantom-pointer]]`.

## Corrected §6 acceptance criterion

**The runbook restatement at `runbooks/session_start_2026_05_23.md`
lines 76–95 carries an incomplete acceptance criterion.** It reads
"PASS or FAIL with `failed_gate=='term_structure'`" — which anticipates
only the two outcomes at gate 7 itself and silently rejects a third
valid outcome: **FAIL at a gate later than 7 (currently only `credit`,
gate 8)**. Reaching gate 8 is positive proof gate 7 ran AND passed,
because gate ordering is strictly sequential and first-failure-wins.

**Corrected criterion (use this in any future §6 re-run, including the
prod re-run that's part of gate [3]):**

§6 is satisfied when, against the LIVE TastytradeDataProvider:

1. The provider class used by the grader is `TastytradeDataProvider`
   (real SDK) — assert by class name, do not assume.
2. The verdict is one of:
   - `PASS` (all 8 gates passed — gate 7 ran on real numbers and
     cleared, gate 8 also cleared), OR
   - `FAIL` with `failed_gate == "term_structure"` (gate 7 ran on real
     numbers and produced a real-numbers FAIL — front−back > max_diff), OR
   - `FAIL` with `failed_gate` at any gate **≥ 7** (currently only
     `credit`, gate 8). Reaching any gate ≥ 7 requires gate 7 to have
     executed and produced a verdict.
3. **Disqualifying outcomes:** `NEEDS_LIVE_DATA` at gate 7 (None branch
   — gate 7 didn't actually compare real numbers), OR FAIL at any gate
   **< 7** (the run never reached gate 7 in the first place).
4. One `kind='ic_grader_run'` audit row written per grader invocation,
   payload shape matches the spec keys, **no raw paste content in the
   payload** (privacy invariant).

The general principle: **§6 is satisfied when the run reaches or passes
gate 7 on real data.** Any failure at gate ≥ 7 proves gate 7 ran;
NEEDS_LIVE_DATA at gate 7 or any failure at gate < 7 does not.

## §6-closing evidence (2026-05-23 ~00:00 UTC, local run)

**Candidate (constructed against live SPY chain via real TT provider):**

```
SPY  06/30/26 (39)  699/702  776/779  35%
```

- Expiration 2026-06-30, live DTE 39 (within 45±7 target)
- Short put 702 (live delta −0.1590, within ±0.05 of −0.16)
- Short call 776 (live delta +0.1570, within ±0.05 of +0.16)
- Long wings 699 / 779 at $3.00 width (config-aligned, verified on chain)
- Live IVR 33.78% (above 30% min)

**Five-point assertion results:**

| # | Criterion | Result | Detail |
|---|-----------|--------|--------|
| 1 | Provider class == `TastytradeDataProvider` (both direct + route) | ✅ PASS | Direct call resolves to `TastytradeDataProvider`; route's `_get_configured_provider()` returns the **same singleton** (`is provider` → True). No test-time mock substitution. |
| 2 | Verdict reaches gate ≥ 7 (corrected criterion) | ✅ PASS | Verdict = `FAIL`, `failed_gate=credit` (gate 8). Reaching gate 8 requires gate 7 to have PASSED. |
| 3 | Direct gate-7 probe: real front/back ATM IV floats | ✅ PASS | `get_atm_iv("SPY", 45, ±7) = 0.1500`, `get_atm_iv("SPY", 75, ±15) = 0.1651`, spread = **−0.0151** (contango as predicted; well below `max_diff=0.05` → gate 7 PASSED). |
| 4 | NOT `NEEDS_LIVE_DATA` on gate 7 | ✅ PASS | Verdict path is FAIL@credit, not the None branch. |
| 5 | `ic_grader_run` audit row(s), shape correct, no paste content | ✅ PASS | Payload keys match spec verbatim. Candidate-line tokens (`699/702`, `776/779`, `06/30/26`) absent from payload. Privacy invariant intact. |

**Per-leg mids (FAIL@credit detail):** short_put $3.84, long_put $3.54,
short_call $2.44, long_call $1.99 → net credit $0.75 = 25% of $3 wing,
below 33% floor. The credit-gate FAIL is itself a **correct real
result** — SPY 16Δ $3-wing iron condors do not currently clear the
strategy's own 33% credit floor. This is real information about why the
strategy is not finding SPY trades right now, not a test defect.

## 2-audit-row note (test artifact, not double-write bug)

The local §6 verification produced **2** `ic_grader_run` audit rows in
the test DB. Source confirmed by inspection:

- `trading_corp/web/routes.py:1633` calls `grade_paste()` **once** per
  POST.
- `grade_paste()` has two `_emit_summary_audit(...)` call sites
  (`ic_candidate_grader.py:1022` for the empty-candidates early-return
  branch; `:1058` for the normal exit). They are in mutually exclusive
  branches — a single `grade_paste` invocation writes exactly **one**
  audit row.

The 2-row outcome is purely a verification artifact: the test harness
invoked the grader twice (PATH (a) direct `grade_paste()` call, PATH
(b) TestClient POST). In production, **1 POST → 1 grade_paste → 1
audit row**. No double-write bug to address in gate [3].

## Open: gate [3]

Gate [3] (CRLF-normalized deploy + prod §6 re-run) is still open.
Apply the **corrected acceptance criterion** above for the prod re-run:
PASS or FAIL at any gate ≥ 7 closes the gate; NEEDS_LIVE_DATA at gate 7
or failure at any gate < 7 does NOT.

For the prod re-run, the candidate selected here may not survive
gates 1–6 if market drift renders the strikes off-chain or the 16Δ
target shifts. Re-run the B2 candidate-construction script
(`tmp/b2_construct_candidate.py`) against the live chain at deploy
time to produce a fresh, on-chain candidate. Apply corrected §6
criterion to the re-run result.

## Related

- `[[ic-grader-shipped]]` — what's committed, what isn't
- `[[project-data-provider-deploy]]` — the AM SDK fix this depends on
- `[[feedback-mocks-dont-catch-sdk-shape]]` — the discipline driving
  the live-SDK §6 gate
- `[[session-committed-phantom-pointer]]` — why the original §6 plan
  doc isn't where the commit message says it is
