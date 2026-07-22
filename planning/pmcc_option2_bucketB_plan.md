# PMCC Division — Option 2: Bucket B Fixes (phased build plan)

## Context

The 2026-07-21 read-only audit of the `robinhood_pmcc` division (memory:
`pmcc-logic-audit-2026-07-21.md`) found that the LLM's reasoning is trusted (it runs
against the uploaded LEAP + covered-call skill), but **every pathology lives downstream
of the LLM in deterministic guards and order assembly** — so the recommendations the
operator sees can contradict, or fail to execute, the LLM's own verdict. Eleven Bucket-B
findings were identified, quantified over 279 order legs / 157 recommendations
(2026-05-01 → 07-21). This plan sequences all 11 into a phased build. **Nothing is live**
(auto_execute:false, all paper); there is no urgency clock. Goal: make PMCC
recommendations trustworthy. Out of scope by explicit direction: the C1 intraday loop,
any LLM/prompt/strategy/universe redesign, the auto_execute flip, and any prod deploy.
**auto_execute stays false at plan exit.** Implementation happens in separate authorized
sessions; this document is the build spec.

## Note — open-path leg-integrity (STRC), CLOSED by B4; account-scanning is endorsed design

The earlier "position-discovery scope failure" framing is RETRACTED (a wrong inference).
Scanning every held stock (`get_universe`, `pmcc_robinhood.py:1769`) and adopting any long+short
call pair (`detect_existing_legs`, `:1836`) are INTENTIONAL — the division deliberately manages
sold calls against held assets; the absent allowlist is by design and `position_exclude` (empty)
is the valid opt-out. AMD/NVDA new-opens and adopted roll pairs are normal activity, not
anomalies; the Phase-0 baseline stands frozen (no provenance re-cut); **no scope-filter phase is
needed.** The narrow remaining STRC defect — `_propose_open_pmcc` shipped a lone short when
`_find_best_leap` returned None without verifying backing — is a leg-integrity gap already
**CLOSED by Phase-1 B4** (aborts `leap_unavailable`). Coverage note: there is no stock-share
coverage check; the PMCC short is LEAP-covered 1:1 (B4-enforced), and `position_min_shares`
(:1806) / sizing `max(1, int(stock_qty/100))` (scan:2276) are lower bounds, not coverage
guarantees. NOTE: approval/rejection counts are NOT evidence of trade quality (operator ignored
most recs, approved some to test the UI) — do not treat the rejection rate as a control.

## Testing philosophy (no labeling)

Labeling is dropped entirely. The LLM's judgment is trusted; retrospective labeling was
rejected as epistemically unfair (missing point-in-time IV, regime, and direction). Every
B fix is validated by **synthetic construction**: build the input state (a `PMCCPosition`
+ a frozen `PMCCAnalysis` + a `MockOptionBroker` chain), run the deterministic path, assert
the output legs/action. This is already how `tests/test_pmcc_logic.py` works — the LLM is
never called in tests; `propose_orders_for_pair(broker, symbol, analysis)` and the guards
run against a hand-built `PMCCAnalysis`.

The **157-row history is a STRUCTURAL REGRESSION BASELINE**, not an acceptance oracle: it
records the "before" rates of each pathology (re-derivable from the CSV output columns),
which the fixes must drive to zero. Bit-exact replay is not required — the raw
`PMCCAnalysis` inputs (`target_delta/dte/strike`) were never persisted, only the selected
legs. Scenario-reconstruction from the output columns is sufficient for structural checks.

### Reusable assets (do not rebuild)
- Test fixtures (`tests/test_pmcc_logic.py`): `MockOptionBroker` (:77-128), `_pmcc_with_short`
  (:302-312), `_broker_with_chains` (:335-355), `_FakeCalendar` (:627-644), `_0dte_pmcc`
  (:651-674), `_leg_with_leap` (:912-929), `strategies_yaml`/`risk_yaml`/`agent` fixtures.
  80 existing tests are the regression floor — they must stay green where behavior is
  unchanged.
- `trading_corp/utils/market_hours.default_calendar()` — NYSE session/close calendar,
  already used by `_terminal_dte_time_release` (pmcc_robinhood.py:2393-2394). Reuse for B11.
- `trading_corp/utils/iv.calc_iv_rank` / `calc_atm_iv` — existing IV utilities, for A3/B5.
- `trading_corp/scripts/ic_paper_run_readiness.py` — template + reusable `CheckResult` /
  `ReadinessReport` dataclasses (:49-68) for the exit-gate.
- Audit extraction scripts (`cc/pmcc_pull2.py`, `cc/cifr_intraday.py`) — reuse the pathology
  detectors to compute baseline vs post-fix rates from output columns.

---

## Phase 0 — Regression baseline harness (no behavior change)  · ~1 session — DONE 2026-07-21 (harness live at `tests/pmcc_regression/`, 13 tests green)

Codify the audit's pathology detectors as a reusable test utility and snapshot the baseline.
- **Deliverable:** `tests/pmcc_regression/` — the pathology detectors (naked/close-without-
  recover, HOLD-overridden roll, same-expiry roll, net-debit roll, cost-ignorant LEAP roll,
  holiday scan, ITM target_strike bypass) as pure functions over a recommendation record,
  plus a baseline snapshot fixture built from `planning/pmcc_rec_history.csv`.
- **Baseline (the numbers to beat):** analysis-vs-execution divergence 50/157 = **32%**;
  close-without-recover **51** = 32.5% of recs / 40.5% of rolls (31 uncovered + 20
  fully_naked-flat + **0 naked_short**; CIFR 16/25 = 64%); **37** net-debit rolls; **18**
  same-expiry rolls; **24** short deltas ≥0.40; LEAP-roll cost not computable on **all 38**
  roll_leaps (33 priced 0.0 + 5 no sell-leg); **6** holiday scans across 3 dates; halfway
  mechanic **2 of 3** firings produced no new short. (Denominator reconciliation: the
  earlier "~30%" = 32.5% of all recs; "40.5%" is the same count over rolls — not a
  discrepancy. Verified Phase 0.)
- **Acceptance:** detectors reproduce the audit counts exactly against the CSV; no code in
  `pmcc_robinhood.py` touched.

## Phase 1 — Leg integrity + HOLD contradiction + holiday guard (B4, B1, B11)  · ~2 sessions

Highest-severity for CORRECTNESS, fully structural, zero external dependencies. **B4 placed
first: it is the highest-frequency leg-integrity gap — a close leg with no re-open leaves the
position without its covering short (uncovered long) or collapses the roll to a full close.**
Severity annotation (Phase-0 drill): **naked_short = 0** — no rec ever sold the LEAP while
leaving a short open, so B4 is a coverage/bookkeeping-integrity finding, NOT a live
unbounded-risk one. It stays Phase-1 first for correctness, not because of open risk.
- **B4** — `_find_best_weekly` (:3221) / `_find_best_leap` (:3195) return None on sparse
  chains and the assembly silently `continue`s, emitting a close with no matching open
  (`_propose_roll_short`:3170-3189; scan roll_leap :2213; scan roll :3189). Fix makes
  short-roll and LEAP-roll **atomic**: no close leg ships unless its re-open leg is
  present, else fail loud (audit + no proposal). Files: `pmcc_robinhood.py`
  `_propose_roll_short:3120`, scan roll_leap block :2117-2221, `_propose_open_pmcc:2969`.
- **B1** — `_should_roll` (:2288, DTE≤2 or ≥50% profit) fires regardless of the LLM's
  Terminal-DTE HOLD verdict. Fix teaches the deterministic roll trigger to respect the
  terminal-DTE ATM HOLD condition (the same condition `_terminal_dte_time_release`:2314
  encodes), so a HOLD at 1-2 DTE ATM is not silently rolled. (The "justified HOLD override"
  escape hatch is defined by Phase 2's Justification contract; B1's Phase-1 fix is the
  structural "respect HOLD" behavior.) Files: `_should_roll:2288`, scan roll branch :2241.
- **B11** — `_scheduled_pmcc_scan_loop` (main.py:2703-2705) has no market-calendar guard;
  it fired on Juneteenth and July-3. Fix reuses `default_calendar()` to skip closed days.
  Files: `trading_corp/main.py:2687-2760`.
- **Tests:** synthetic units — sparse-chain scenario asserts no close-without-recover;
  HOLD@1-DTE-ATM scenario asserts no roll order; holiday date asserts scan no-op.
- **Acceptance:** regression detectors report **0** close-without-recover, **0**
  HOLD-overridden rolls, **0** holiday scans on the synthetic corpus; all Phase-0 detectors
  + existing 80 tests green.

## Phase 2 — Roll-quality cluster (B2, B7, B9 — B6 WITHDRAWN)  · ~2 sessions

**Detailed build spec (approved 2026-07-21): `planning/pmcc_phase2_plan.md`** — the
`PMCCAnalysis.override={kind,reason}` contract, B2 conservative (bid/mark) pre-fee credit gate,
B7 roll-out (+ `_WEEKLY_FALLBACK_MAX_DTE` ceiling), B9 earnings gate + fail-open observability,
the gate evaluation order, tests, and acceptance gates.

> **B6 WITHDRAWN 2026-07-21.** The `target_strike` "OTM guard" was a mischaracterized finding —
> the ITM target_strike is the intentional halfway-roll mechanism (skill L103-104/L111/L135-141;
> `_select_weekly_strike` docstring L523-527). B6's fallback would have silently defeated every
> halfway roll. Third finding to collapse under "an unenforced rule is only a defect if
> enforcement was intended" (after STRC/held-stock and position-discovery). See
> `memory/pmcc-logic-audit-2026-07-21.md`.

All constrain the short-roll path (`_propose_roll_short` / `_find_best_weekly` /
`_select_weekly_strike`), so they land together to avoid churn.
- **B2** — no credit-required gate in `_propose_roll_short` (:3120); rolls ship at any net
  sign. Fix adds a credit/EV gate on the assembled roll (net = open credit − close debit);
  a net-debit roll is blocked/flagged unless `override.kind == "net_debit_justified"` (see
  Justification contract below).
- **B6 — WITHDRAWN** (see banner above): ITM `target_strike` is the intentional halfway-roll
  mechanism, not a bypass. No code change; `itm_target_strike_bypass` dropped as an acceptance
  gate; the two target_strike tests are correct and untouched.
- **B7** — `_find_best_weekly` (:3242-3259) picks the **earliest** qualifying expiry and
  `max(3, target_dte-7)` collapses the floor to 3, so the current expiry re-qualifies (18
  same-expiry rolls). Fix enforces "roll OUT": the new expiry must be strictly later than
  the current short's expiry.
- **B9** — `_blocked_by_earnings` (:812) is called only at `_propose_open_pmcc` (:2990);
  rolls/cover are not earnings-gated. Fix extends the gate to the roll/cover paths.
- **Justification contract (defines "LLM-justified" for B1 + B2 — machine-readable, not
  free-text).** Extend the `PMCCAnalysis` dataclass (pmcc_robinhood.py:382-402) and its
  JSON parse in `_llm_analyze_position` (:1046-1058) with an additive structured field:
  `override = {kind: "net_debit_justified" | "hold_override" | null, reason: str}`. This is
  minimal LLM-adjacent plumbing (one optional output key), **NOT** a `_STANDARD_RULES` /
  `_BLACK_SHEEP_RULES` redesign (out of scope). The deterministic layer inspects
  `override.kind` only; free-text reasoning is never parsed. Detector semantics:
  a net-debit roll is **unjustified** iff `override.kind != "net_debit_justified"`; a
  HOLD→roll is **unjustified** iff `override.kind != "hold_override"`. After this phase, a
  HOLD→roll occurs ONLY when the LLM sets `override.kind == "hold_override"` — this finalizes
  B1's exit metric.
- **Tests:** behavioral synthetics — net-debit roll blocked/flagged unless
  `override.kind == "net_debit_justified"`; same-expiry roll rejected (new > current expiry);
  roll inside earnings buffer gated; HOLD→roll only with `override.kind == "hold_override"`.
- **Acceptance:** regression detectors report **0** unjustified net-debit rolls, **0**
  unjustified HOLD overrides, **0** same-expiry rolls; earnings-window rolls gated.
  (`itm_target_strike_bypass` removed — B6 withdrawn.)

## ★★ OPEN ITEM (discovered in Phase 2) — B9/B2 do NOT gate the roll_leap path
**Coverage hole, needs an operator decision on sequencing.** The B9 earnings gate and the B2 credit
gate were built into `_propose_roll_short` only. The **roll_leap** path assembles its 4-leg compound
in `propose_orders_for_pair` roll_leap and scan roll_leap, and its **4th leg opens a new short** — so
a LEAP roll can currently ship a **net-debit short** or a **short into an earnings window** with
neither gate firing. This is the SAME CLASS of bug as B4's original close-without-recover: a rule
applied to one path but not its sibling that does the same risky thing. **What already covers
roll_leap:** B4 atomic legs (Phase 1) + B7 roll-out filter & the 60-DTE ceiling (`after_dte` is wired
at both roll_leap `_find_best_weekly` calls). **What does NOT:** B9, B2. **B2/B3 boundary (fix
nuance):** B9 ports cleanly; B2's short-leg credit (`open_bid - close_mark`) ports to the
close-old-short/open-new-short pair — but roll_leap also swaps the LEAP (legs 2+3), so the full
4-leg compound cost is **B3's** domain. A B2-on-roll_leap fix should gate the short-leg credit +
earnings only and must NOT re-derive the compound cost (avoid double-counting with B3).
**Decision needed:** fold the
B9/B2 extension into **Phase 3** (LEAP cluster — natural home) OR give it its own Bucket-B number. Do
NOT build before that decision. Also filed to `BACKLOG.md` + `planning/pmcc_phase2_handoff_2026-07-22.md`.

## Phase 3 — LEAP cluster (B8, B3)  · ~1-2 sessions

**B8 and B3 are independent** — co-located only because both touch LEAP paths. B8 (long-leg
strike selection) does NOT gate B3 (LEAP-roll cost/benefit gate); listing "B8" first is
scheduling convenience only — they may land in either order or in parallel within the phase.
B3's ONLY dependency is its own internal old-LEAP-mark-0.0 fix.
- **B8** — `_select_leap_strike` (:486) hard-codes delta≥0.80; config
  `long_leg.delta_min:0.55`/`delta_high_conviction:0.70` (strategies.yaml:294-296) are
  unused. Fix reads the configured range. Files: `_select_leap_strike:486`,
  `_find_best_leap:3195`.
- **B3** — **internal dependency: the old-LEAP sell leg is stored at mark 0.0** (scan:2143,
  `propose_orders_for_pair`:1200), so any cost/benefit gate would be blind. Fix that first
  (populate the real old-LEAP mark), **then** add the cost/benefit gate on
  `_promote_to_roll_leap_if_hard_rule` (:2443) so a negative-value LEAP roll is caught.
- **Tests:** synthetic — LEAP strike lands in configured range; roll_leap records non-zero
  old-LEAP cost; cost/benefit gate blocks a manufactured negative-EV LEAP roll.
- **Acceptance:** LEAP-roll cost recorded on **100%** of roll_leaps (from 0 today = 33 priced 0.0 + 5 no sell-leg); LEAP strike
  within config band.

## Phase 4 — Delta-vs-IV wiring (A3 → B5)  · ~1-2 sessions

Bucket-A tightening pulled in **only because B5 requires it** (matches the plan's "A only
where a B fix needs it" rule).
- **A3** — wire an IV source into the picker path using `trading_corp/utils/iv.calc_iv_rank`
  / `calc_atm_iv`; surface the `iv_filters` config (strategies.yaml:159-162) that is
  currently declared-but-unread.
- **B5** — `_short_target_delta` (:727) returns a static 0.25. Fix conditions the delta
  target on IV level + regime, landing it inside the **already-configured** ranges
  (aggressive 0.30-0.45 / balanced 0.20-0.30 / defensive 0.10-0.20, strategies.yaml:306-308).
  This is **structural**: the acceptance test asserts the picker *respects the configured
  range* for a given synthetic IV/regime — it does not judge whether the range value is
  "right." Files: `_short_target_delta:727`, `_find_best_weekly:3273`.
- **Tests:** synthetic IV values × regime → assert selected delta ∈ configured range.
- **Acceptance:** **0** short deltas outside the configured regime band across the synthetic
  IV sweep.

## Phase 5 — Terminal-DTE afternoon seam (B10) + readiness gate + exit  · ~1-2 sessions

- **B10** — the terminal-DTE afternoon release (15:00/15:30 ET) only runs on a human trigger
  via `propose_orders_for_pair` (:1114). Fix makes `_terminal_dte_time_release` (:2314)
  invokable + idempotent (it already accepts `now_et_dt`/`calendar`) and adds a **second
  daily scan-loop invocation at 15:00 ET** in `_scheduled_pmcc_scan_loop` (main.py:2687).
  **Refactor-only seam — see C1 seam below.**
- **B10 test coverage — pending-proposal interaction.** The B10 suite MUST include a case
  where a pending PMCC proposal is already in the HITL approval queue when the 15:00 ET scan
  fires, and assert the chosen behavior is intentional — either the pending proposal
  **suppresses** re-evaluation of that position, or the 15:00 scan **re-evaluates
  independently**. Either is defensible; the test pins whichever the implementer chose (Phase
  5 does not pre-decide it). Reuse the approval-queue routing the scan path already uses
  (`_run_order` / `_group_orders_by_pair_id`, main.py:820-844).
- **Readiness gate** — `trading_corp/scripts/pmcc_paper_run_readiness.py` mirroring
  `ic_paper_run_readiness.py`: BLOCK checks (divisions.yaml `robinhood_pmcc` wiring,
  strategies.yaml loads clean, `PMCCAgent(db_url=...)` instantiates, `agent_state` r/w,
  `audit_event` reachable) + SOFT checks (IV utils import — now load-bearing for B5,
  calendars, VIX). Tests mirror `tests/test_paper_run_tooling.py`.
- **Exit verification:** full regression re-run (below); readiness gate green;
  **auto_execute remains false**.

---

## Explicit dependencies
- **B3 ⇐ old-LEAP-price-0.0 fix (internal to B3)** — the cost/benefit gate is meaningless
  until the LEAP-roll legs carry a real mark. Sequenced inside Phase 3, price-fix first.
- **B5 ⇐ A3** — delta-vs-IV needs an IV feed; A3 wires it. Sequenced Phase 4, A3 first.
- **B1 + B2 ⇐ Justification contract (Phase 2)** — both "unjustified" exit metrics key off
  the `PMCCAnalysis.override.kind` structured field introduced in Phase 2.
- **B2 / B7 co-located** — both mutate the short-roll path; landing them together avoids
  separate rewrites of `_propose_roll_short`/`_find_best_weekly`. (B6 was the third here — now
  withdrawn.)
- **B8 / B3 independent** — co-located by area (LEAP), not by dependency (see Phase 3).
- **B10 ⇉ C1 seam** — see below.

## Phase-2 (C1) seam — do not paint into a corner
B10 exposes the terminal-DTE release as an **idempotent entry point** callable by the daily
scheduler at 15:00 ET. A future C1 intraday-management loop would call **that same entry
point** at higher frequency. Constraint: B10 must NOT restructure `_scheduled_pmcc_scan_loop`
into anything that presumes an intraday architecture — it stays a daily scheduler with a
second fixed 15:00 invocation. The release logic is the C1 attach surface; the loop is not.

## Regression protection
- Phase 0 pins the pathology detectors + baseline counts. Every subsequent phase runs them
  and asserts the targeted pathology is **absent** while the others are **unchanged**.
- The existing 80 `test_pmcc_logic.py` tests are the "behavior unchanged where not
  intended" floor and must stay green each phase.
- Each fix ships with synthetic acceptance tests constructed from the *mechanism* (sparse
  chain, HOLD@terminal-DTE, net-debit roll, same-expiry chain, ITM target_strike, zero-cost
  LEAP roll, holiday date, IV×regime sweep) — not from labeled history.

## Exit criteria (completion of this plan)
All 11 B fixes landed + tested, and the regression re-run hits these targets:

| Pathology | Baseline | Target |
|---|---|---|
| Close-without-recover (B4) | 51 = 32.5% recs / 40.5% rolls (31 uncov + 20 flat + 0 naked-short; CIFR 64%) | **0** (each subtype) |
| Analysis-vs-execution (HOLD→roll) | 32% (50/157) | **0** with `override.kind != "hold_override"` |
| Net-debit rolls | 37 | **0** with `override.kind != "net_debit_justified"` |
| Same-expiry rolls | 18 | **0** |
| Short delta outside configured band | 24 ≥0.40 | **0** outside band |
| LEAP-roll cost recorded | 0/38 (33 @0.0 + 5 no-leg) | **100%** of roll_leaps |
| Holiday scans | 6 (3 dates) | **0** |
| ~~ITM target_strike bypass~~ | ~~present~~ | **B6 WITHDRAWN 2026-07-21** — ITM target_strike is the intentional halfway-roll mechanism; no longer an exit gate |

Plus: `pmcc_paper_run_readiness.py` returns exit 0; **auto_execute STILL false** (no
automation flip is part of this plan's exit — that is a separate future decision).

## Verification (how to run)
- Unit + regression: `pytest tests/test_pmcc_logic.py tests/pmcc_regression/ -q` (per phase).
- Readiness gate: `python -m trading_corp.scripts.pmcc_paper_run_readiness --skip-network`
  (exit 0 = ready), with `tests/test_pmcc_paper_run_readiness.py` mirroring the IC tests.
- Baseline re-derivation: rerun the audit pathology detectors over `planning/
  pmcc_rec_history.csv` to confirm the "before" numbers, then over the synthetic post-fix
  corpus to confirm the "after" targets.

## Estimated total: ~8-11 build sessions across 6 phases (0-5).

---
_Persisted from plan session 2026-07-21. Companion labeling/baseline artifacts:
`planning/pmcc_rec_history.csv`, `planning/pmcc_recommendation_history_20260721.md`. Audit
memory: `pmcc-logic-audit-2026-07-21.md`. No code/config/prod/deploy touched by planning._
