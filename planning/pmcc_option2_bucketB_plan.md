# PMCC Division — Option 2: Bucket B Fixes (phased build plan)

## STATUS — restated exit criteria (2026-07-22)

**★ BUCKET B CLOSED 2026-07-22.** Every item is terminal (BUILT or WITHDRAWN); nothing remains. The
readiness gate is green (`pmcc_paper_run_readiness.py` → exit 0); auto_execute still false. Full
built-vs-collapsed tally in `planning/pmcc_phase2_handoff_2026-07-22.md`.

The original spec below treated all 11 Bucket-B items as defects. That is no longer true: several
findings have collapsed under the endorsed-design test (a gap is a defect only if enforcement was
INTENDED — not if the behavior is deliberately delegated to a human or the LLM). This table is the
authoritative view of what is actually left; the per-phase detail below is retained for history.

| Item | Status | Measurable gate |
|---|---|---|
| B4 atomic legs | **BUILT** (P1) | close-without-recover = 0 (each subtype) |
| B1 HOLD precedence | **BUILT** (P1) | HOLD→roll = 0 unless `override.kind=="hold_override"` |
| B11 holiday guard | **BUILT** (P1) | holiday scans = 0 |
| B7 roll-out enforcement | **BUILT** (P2) | same-expiry rolls = 0 |
| B2 credit gate (short-roll) | **BUILT** (P2) | net-debit roll = 0 unless `net_debit_justified` |
| B9 earnings gate (short-roll) | **BUILT** (P2) | earnings-window roll = 0 unless `earnings_override` |
| B9 + B2 on the roll_leap path | **BUILT** (P2.5) | roll_leap net-debit short = 0 unless `net_debit_justified`; roll_leap earnings-window = 0 unless `earnings_override` |
| B3 old-LEAP-price fix (all 4 LEAP-sell legs) | **BUILT** (Final) | LEAP-roll cost recorded (old_leap_px ≠ 0); close_leap_urgent decoupled (market-sell preserved); unresolvable mark → flag not 0.0 |
| B8(a) docstring consistency | **BUILT** (Final) | `_find_best_leap` docstring matches the real hard-coded 0.80 |
| B8(b) dead-config | **BUILT — RETIRED** (Final) | `long_leg.delta_min/max/high_conviction/speculative` removed + `_leap_min/max_delta` props retired; 0.80 documented as intended |
| B10 afternoon terminal-DTE seam | **BUILT** (Final) | calendar-anchored (close−60m) 0-DTE-only pass: LLM-call → UNCHANGED release; suppress-pending. Scheduler-loop glue compile-only (see handoff) |
| Readiness gate | **BUILT** (Final) | `pmcc_paper_run_readiness.py` → exit 0 (11/11 blocking; report-only, not a promotion gate) |
| A3 IV wiring | **WITHDRAWN — no consumer** | — B5 was its only consumer; `pmcc_robinhood.py` reads no IV (`iv_filters` declared-but-unread); falls with B5 |
| B5 IV-conditioned short delta | **WITHDRAWN — endorsed design** | — delta targeting is the LLM's (`_BLACK_SHEEP_RULES` L100/L108, `_STANDARD_RULES` L193-196/L198-199); a *selected* delta ≥0.40 is impossible on the `target_delta` path (OTM<0.40 clamp `_select_weekly_strike` :559-571), so all 24 came from endorsed strike-targeting; static 0.25 is fallback-only (`:3587`) |
| B3 cost/benefit auto-gate | **WITHDRAWN — endorsed design** | — pre-empts the HITL accept/reject the promote guard exists to surface (`_BLACK_SHEEP_RULES` L178-184, `_STANDARD_RULES` L259-266, `_promote_to_roll_leap_if_hard_rule` docstring L2539-2545) |
| B8 force-LEAP-delta-to-config (0.55) | **WITHDRAWN — endorsed design** | — 0.80 deepest-ITM is intended conservatism at the top of the skill band (`_STANDARD_RULES` L270) |
| B6 target_strike OTM guard | **WITHDRAWN — endorsed design** | — intentional halfway-roll mechanism (skill L103-104/L111/L135-141) |
| STRC / held-stock adoption (outside numbering) | **WITHDRAWN — endorsed design** | — division deliberately manages sold calls vs held assets |
| position-discovery / `get_universe` scope (outside numbering) | **WITHDRAWN — endorsed design** | — scanning every held stock is intended; empty `position_exclude` is the opt-out |

**The pattern:** every item that SURVIVED is a data- or structural-integrity fix (atomic legs,
credit/earnings gates, a real recorded price, an honest docstring); every item that COLLAPSED was an
automatic-behavior change that would pre-empt a human or LLM decision (auto-block a LEAP roll, force a
delta, reject an LLM-chosen strike). **Both predictions confirmed 2026-07-22:** B5 collapsed (delta
targeting is the LLM's; the OTM<0.40 clamp makes a *selected* delta ≥0.40 impossible on the target_delta
path, so all 24 ≥0.40 recs came from the endorsed halfway-roll strike-targeting) — taking A3 with it;
B10 survives (a scheduling-only re-fire of an unchanged deterministic release).

## Method lesson — why the audit produced these (the transferable takeaway)

The 2026-07-21 audit's method was to map YAML and rule-block text against Python enforcement and flag
every gap. That method cannot distinguish an unenforced rule from a rule deliberately delegated to the
LLM or to the operator. In this system a substantial share of behavior lives in those layers by design,
so gap-detection alone produced a **~45% false-positive rate on the numbered items** — **six findings
collapsed** (B3's cost/benefit gate, B5, B6, B8's force-0.55, plus the unnumbered STRC and
position-discovery), with A3 falling as B5's dependency; roughly five of the ~12 actionable numbered
Bucket-A/B findings were endorsed design or automatic-behavior mischaracterizations. Any future audit of
this or another division should establish, per finding, **whether enforcement was INTENDED before
classifying a gap as a defect.**

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
**DECISION (operator, 2026-07-22): its own PHASE 2.5, ahead of Phase 3** — B2-on-roll_leap must
deliberately NOT re-derive compound cost while B3 is specifically about compound cost, so building them
together invites the double-counting flagged above. Scope: port B9 + B2-**short-leg-only** to both
roll_leap sites. Build spec: `planning/pmcc_phase2_5_plan.md`. Also filed to `BACKLOG.md` +
`planning/pmcc_phase2_handoff_2026-07-22.md`.

## Final phase — 2026-07-22 (collapses old Phases 3/4/5) — B3 price-fix + B8(a)/(b) + B10  · ~1 session

**The six-phase structure was sized for eleven defects; six collapsed under the endorsed-design test, so
the remaining work is ONE phase.** Old Phase 4 (A3+B5) is fully WITHDRAWN (see below); old Phase 5's B10
folds in here (the readiness-gate + exit verification is a SEPARATE follow-up step, NOT part of this
phase). What is actually left: a data-integrity fix (B3 old-LEAP price), a documentation fix (B8a
docstring), a config-hygiene retirement (B8b), and a scheduling seam (B10). **No new deterministic gate,
no delta behavior change, no auto-decision.** B8 and B3 remain independent (co-located by LEAP area only).

- **B3 — old-LEAP-price fix ONLY (data integrity). The auto-blocking cost/benefit gate is WITHDRAWN.**
  - **Price fix (REMAINING, unambiguous) — ALL FOUR LEAP-sell legs (operator, 2026-07-22).** The
    old-LEAP **sell** leg is emitted at `mark_price=0.0` at FOUR sites: roll_leap_close
    `pmcc_robinhood.py:1321` (site 1) + `:2303` (scan) — the compound-roll cost — AND close_leap_urgent
    `:1402` (close_all) + `:2211` (scan close_all urgent) — the same 0.0 defect on the full-exit path.
    Scope WIDENED to all four: the close_leap_urgent legs carry the identical defect with a
    one-line-identical fix; leaving two known 0.0 marks would optimize for the doc, not the system.
    **Source:** `pos.long_leg_mark` (`:306`, already populated in `detect_existing_legs` from the chain
    `:2003/:2023`; free — no extra query). **Unavailable handling:** `mark_price = float(pos.long_leg_mark)
    if not None else None` with a distinguishable `extra` tag — **never a silent 0.0** (an unresolvable
    mark must be distinguishable from a genuine zero). Nobody intends 0.0.
  - **Cost/benefit auto-gate — WITHDRAWN (endorsed design).** An automatic block on
    `_promote_to_roll_leap_if_hard_rule` would pre-empt the operator accept/reject the guard exists to
    SURFACE: `_BLACK_SHEEP_RULES` L178-184 ("surface a 4-leg compound … they can still reject the LEAP
    roll and approve only the short roll"), `_STANDARD_RULES` L259-266 ("the promotion ensures the
    recommendation card INCLUDES the LEAP roll legs"), and the `_promote_to_roll_leap_if_hard_rule`
    docstring (`:2539-2545`). Same category as B6. **★ Note the direction:** the price fix and the
    withdrawn gate point OPPOSITE ways — they are not merely independent. The price fix is what ENABLES
    the human decision (you cannot accept/reject a compound whose cost is recorded as 0); the gate would
    have PRE-EMPTED that same decision. Fixing the data serves the HITL design; gating it defeats it.
- **B8 — doc/config-consistency, NOT a behavior change.** `_find_best_leap` (`:3395`) calls the
  module-level `_select_leap_strike` (`:503`), which hard-codes `delta >= 0.80` (deepest qualifying ITM)
  and never reads config. The 0.80 choice is INTENDED and documented (module docstring L4,
  `_select_leap_strike` docstring L504, skill `_STANDARD_RULES` L270 "range 0.55-0.80" = the deep end,
  `_leap_min_delta` fallback default 0.80) — so "force it to 0.55" is WITHDRAWN (see STATUS table). Two
  sub-items remain:
  - **(a) Docstring fix (REMAINING — do regardless of the config decision).** `_find_best_leap`'s
    docstring (`:3369`) claims it filters "delta >= leap_min_delta", but **no code path reads
    `_leap_min_delta`** — the property (`:736`) is referenced only inside that docstring. A docstring
    asserting behavior the code does not have is exactly the failure mode that made this audit misfire
    repeatedly; correct it to describe the real 0.80 hard-code.
  - **(b) Dead-config — RESOLVED 2026-07-22: RETIRE (Option 1; was the deferred operator decision).**
    `long_leg.delta_min:0.55` / `delta_max:0.80` (+ any other dead `long_leg` delta keys —
    `delta_high_conviction:0.70` / `delta_speculative:0.55`) (`strategies.yaml:294-297`) are read into
    `_leap_min_delta`/`_leap_max_delta` (`:736`/`:743`) but **consumed by NO code path** — dead for LEAP
    selection. **Decision: RETIRE them** (not wire them) and document 0.80 as intended where the constant
    lives. Rationale: 0.80 is documented in four places (module docstring L4, `_select_leap_strike` L504,
    skill L270, the `_leap_min_delta` fallback default) and is the deep end of the skill's stated band;
    making it configurable would add a knob that could shallow the LEAP against the module's own design
    philosophy, for no benefit requested. Also retire/comment the unread `self._leap_min_delta`/
    `_leap_max_delta` attributes if nothing else reads them. **★ strategies.yaml path-scoping:** the
    parallel-session edits to that file live near L1690/L1739 (copy-trader blocks) and MUST stay
    untouched — show the full `strategies.yaml` diff and confirm ONLY the PMCC `long_leg` keys changed
    before commit.
- **★ KNOWN-DEAD-CONFIG FOLLOW-UP (found 2026-07-22, NOT retired — outside the approved B8b DELTA scope):**
  `long_leg.dte_max` (720) and `long_leg.roll_out_trigger_dte` (120) are ALSO dead — no code path reads
  them (`_leap_min_dte` reads `dte_min`; the 120-DTE promote threshold is HARD-CODED in
  `_promote_to_roll_leap_if_hard_rule`, not config). Left in place this phase (B8b was scoped to the DELTA
  keys); a future config-hygiene pass can retire them. `dte_min` (used) and `roll_down_trigger_delta` (read
  by `web/data.py`) are LIVE — keep.
- **Tests:** synthetic — both old-LEAP sell legs record a non-zero mark (B3 price); `_find_best_leap`
  docstring matches the 0.80 hard-code (B8a). No cost/benefit-gate test (withdrawn); no delta-band test
  unless Option 2 is chosen.
- **Acceptance:** LEAP-roll cost recorded on **100%** of roll_leaps (from 0 today = 33 priced 0.0 + 5 no
  sell-leg); `_find_best_leap` docstring no longer claims a `leap_min_delta` filter. **No auto-gate
  ships; no delta behavior changes** without a separate operator decision (B8 Option 2).

## ~~Phase 4 — Delta-vs-IV wiring (A3 → B5)~~ — WITHDRAWN 2026-07-22 (endorsed design)

**B5 and A3 both collapsed under the endorsed-design test (investigation 2026-07-22).**
- **B5 — WITHDRAWN (endorsed design).** The rule blocks give the LLM the regime delta table
  (`_BLACK_SHEEP_RULES` L100 delta 0.20-0.35 / L108 OTM roll 0.20-0.30 / L103-104 + L135-141 halfway-roll
  strike targeting; `_STANDARD_RULES` L193-196 regime table / L198-199 breach deltas / L218-224 strike
  targeting) and the LLM sets `target_delta`; `_short_target_delta` (0.25) is the fallback ONLY when the
  LLM omits it (`_find_best_weekly` :3587). **★ DECISIVE:** a *selected* delta ≥0.40 is structurally
  impossible on the `target_delta` path — `_select_weekly_strike` (:559-571) clamps selection to OTM<0.40
  whenever the chain has any sub-0.40 strike — so all 24 ≥0.40 recs came from the `target_strike`
  (halfway/breach) path, the endorsed high-IV mean-reversion mechanic; the static fallback CANNOT produce
  ≥0.40. The near-zero-delta cluster (25 recs) is largely the STRC held-stock / cover_leap artifact (15
  STRC + 16 cover_leap), already closed by B4 — not a delta-selection defect.
- **A3 — WITHDRAWN (no consumer).** Its ONLY consumer was B5; `pmcc_robinhood.py` reads no IV at all (no
  `calc_iv_rank`/`calc_atm_iv`; `iv_filters` declared-but-unread) → A3 falls entirely. `utils/iv` would be
  the hook if a future OBSERVABILITY item ever wants IV context — not proposed here.

## ~~Phase 5~~ — B10 folded into the Final phase; readiness gate is a SEPARATE follow-up

**B10 is part of the Final phase above** (the 15:00-ET scheduling seam). The readiness gate + exit
verification is a SEPARATE follow-up step AFTER this phase (do NOT start it here). B10 detail retained
for reference:

- **B10** — the terminal-DTE afternoon release (15:00/15:30 ET) only runs on a human trigger
  via `propose_orders_for_pair` (:1114). Fix makes `_terminal_dte_time_release` (:2314)
  invokable + idempotent (it already accepts `now_et_dt`/`calendar`) and adds a **second
  daily scan-loop invocation at 15:00 ET** in `_scheduled_pmcc_scan_loop` (main.py:2687).
  **Refactor-only seam — see C1 seam below.**
- **★ SCOPE DECISION (operator, 2026-07-22): NARROW — terminal-DTE-release ONLY at 15:00 ET.** The 15:00
  pass evaluates the terminal-DTE release only; it does **NOT** re-run the full roll logic. The full-scan
  variant (a second complete daily evaluation cycle) was CONSIDERED and DELIBERATELY NOT TAKEN: a full
  afternoon scan is materially closer to C1 (the intraday position-management loop, explicitly out of
  scope) than to a scheduling fix, and would surface afternoon rolls the system has never generated on a
  book we are rebuilding trust in — a behavior expansion in a scheduling change's clothes. The pathology
  is narrowly that the terminal-DTE release never fires autonomously; the narrow pass fixes exactly that.
  Independent per-day dedup, separate from the pre-open scan's dedup. The 15:00 pass must not alter what
  `_terminal_dte_time_release` decides. **If the narrow pass would require refactoring the scan so the
  full-scan path is the only reachable one, STOP and report — do not widen scope to ease the plumbing.**
  A future phase may revisit the full-scan variant as a distinct decision.
- **★ IMPLEMENTATION DECISION (operator, 2026-07-22): OPTION (b) — LLM-call the 0-DTE subset.** Three ways
  to feed the release were weighed. **synthesize-HOLD** (fabricate a HOLD baseline per 0-DTE leg, then
  apply the release) was CONSIDERED and DELIBERATELY NOT TAKEN: at 15:00 the P0 time gate fires on EVERY
  0-DTE HOLD, so it forces a roll on every 0-DTE short (incl. ones a fresh read would let expire) and makes
  `close_all` structurally UNREACHABLE — an automatic behavior overriding an LLM decision, the SAME shape
  as the six collapsed items. **reuse-morning-verdict** (option c) was rejected on staleness (a ~6h-old
  verdict defeats the reason a 15:00 look exists). **CHOSEN: (b)** — LLM-call ONLY the 0-DTE subset at
  15:00, then apply the UNCHANGED `_terminal_dte_time_release` to the real verdict; it overrides a genuine
  HOLD/WATCH (its designed role) while non-HOLD verdicts (`close_all`/`roll_leap`) pass through as the LLM
  decided. Cost is a Friday-clustered subset (book ≈16 names) of the already-daily pre-open LLM call.
- **★ P1 (cycle-continuity) confirmed SAFE:** P1 (`short_leg_mark ≤ $0.15` → roll_short, no time cond.) is
  subsumed by the P0 time gate at 15:00 (both → roll_short); the window is anchored at 15:00 (= release
  threshold) so P1 never fires "early." No new P1 schedule; `_terminal_dte_time_release` decision logic
  UNTOUCHED. Implemented as a `scan(zero_dte_only=True, skip_symbols=…)` subset filter + a second scheduler
  window; pending suppressed via `PendingApprovalRegistry.list_pending()`.
- **★ COVERAGE BOUNDARY (known verification gap, 2026-07-22).** UNIT-TESTED: the three pure/extractable
  pieces — `scan(zero_dte_only=…, skip_symbols=…)` subset filter, `_terminal_should_fire` (the
  calendar-anchored WHEN, tested on a normal 4pm day AND a 1pm half-day), and `_pmcc_pending_symbols`
  (the pending extraction, tested against the REAL ceo_graph `detail['order']['symbol']` shape).
  COMPILE-VERIFIED ONLY (NOT unit-tested — not exercisable in the pmcc test harness): the
  `_scheduled_pmcc_scan_loop` while-loop wiring and `_on_terminal_scan`'s broker-resolution + `_run_order`
  routing glue. VERIFICATION PATH: boot-smoke (import/wiring) + the FIRST live 15:00 fire (watch for the
  `terminal_dte_pass_done` audit and any `terminal_dte_order_result` rows). **Do NOT claim B10 is fully
  tested — the scheduler half is not.**
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
- **B3 = old-LEAP-price fix ONLY (re-scoped 2026-07-22)** — the cost/benefit auto-gate is WITHDRAWN
  (endorsed design; it would pre-empt the HITL accept/reject the promote guard exists to surface). The
  price fix is what ENABLES that human decision, so it is the whole of B3 now — not a precursor to a
  gate. See Phase 3.
- **~~B5 ⇐ A3~~ — WITHDRAWN 2026-07-22** — B5 collapsed (endorsed design); A3 (its only consumer) falls
  with it. No IV wiring is built.
- **B1 + B2 ⇐ Justification contract (Phase 2)** — both "unjustified" exit metrics key off
  the `PMCCAnalysis.override.kind` structured field introduced in Phase 2.
- **B2 / B7 co-located** — both mutate the short-roll path; landing them together avoids
  separate rewrites of `_propose_roll_short`/`_find_best_weekly`. (B6 was the third here — now
  withdrawn.)
- **B8 / B3 independent** — co-located by area (LEAP), not by dependency; both RE-SCOPED 2026-07-22 to
  integrity/doc fixes with their automatic-behavior halves withdrawn (see Phase 3).
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
**See the top STATUS table for the authoritative BUILT / REMAINING / WITHDRAWN view — the original
"all 11 are defects" framing is superseded.** The measurable regression targets below apply to the
BUILT + REMAINING data/structural-integrity items only; WITHDRAWN endorsed-design items have no target.

| Pathology | Baseline | Target |
|---|---|---|
| Close-without-recover (B4) | 51 = 32.5% recs / 40.5% rolls (31 uncov + 20 flat + 0 naked-short; CIFR 64%) | **0** (each subtype) |
| Analysis-vs-execution (HOLD→roll) | 32% (50/157) | **0** with `override.kind != "hold_override"` |
| Net-debit rolls | 37 | **0** with `override.kind != "net_debit_justified"` |
| Same-expiry rolls | 18 | **0** |
| ~~Short delta outside band (B5)~~ | ~~24 ≥0.40~~ | **WITHDRAWN 2026-07-22** — all 24 came from endorsed strike-targeting; a *selected* delta ≥0.40 is impossible on the target_delta path (OTM<0.40 clamp). A3 falls with it. |
| LEAP-roll cost recorded (B3 price fix) | 0/38 (33 @0.0 + 5 no-leg) | **100%** of roll_leaps |
| roll_leap net-debit short (Phase 2.5) | not gated today | **0** unless `net_debit_justified` |
| roll_leap earnings-window (Phase 2.5) | not gated today | **0** unless `earnings_override` |
| Holiday scans | 6 (3 dates) | **0** |
| ~~ITM target_strike bypass (B6)~~ | ~~present~~ | **WITHDRAWN 2026-07-21** — intentional halfway-roll mechanism |
| ~~LEAP-roll cost/benefit auto-gate (B3 gate)~~ | ~~n/a~~ | **WITHDRAWN 2026-07-22** — endorsed design; pre-empts the HITL accept/reject the promote guard surfaces |
| ~~Force LEAP delta to config 0.55 (B8)~~ | ~~n/a~~ | **WITHDRAWN 2026-07-22** — 0.80 deepest-ITM intended; B8 reduced to docstring + dead-config decision |

Plus: `pmcc_paper_run_readiness.py` **BUILT 2026-07-22 → returns exit 0** (11/11 blocking checks green;
report-only, NOT a promotion gate; tests in `tests/test_pmcc_paper_run_readiness.py`); **auto_execute
STILL false** (no automation flip is part of this plan's exit — that is a separate future decision).

## Verification (how to run)
- Unit + regression: `pytest tests/test_pmcc_logic.py tests/pmcc_regression/ -q` (per phase).
- Readiness gate: `python -m trading_corp.scripts.pmcc_paper_run_readiness --skip-network`
  (exit 0 = ready), with `tests/test_pmcc_paper_run_readiness.py` mirroring the IC tests.
- Baseline re-derivation: rerun the audit pathology detectors over `planning/
  pmcc_rec_history.csv` to confirm the "before" numbers, then over the synthetic post-fix
  corpus to confirm the "after" targets.

## Estimated total (revised 2026-07-22): Phases 0-2.5 DONE; ONE Final phase remains (B3 price-fix + B8a/b + B10) + a separate readiness-gate follow-up. The original 6-phase / 8-11-session estimate was sized for eleven defects; six collapsed under the endorsed-design test.

---
_Persisted from plan session 2026-07-21. Companion labeling/baseline artifacts:
`planning/pmcc_rec_history.csv`, `planning/pmcc_recommendation_history_20260721.md`. Audit
memory: `pmcc-logic-audit-2026-07-21.md`. No code/config/prod/deploy touched by planning._
