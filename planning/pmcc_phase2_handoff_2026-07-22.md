# PMCC Bucket-B — handoff (Phase 2 complete) — 2026-07-22

Written for a fresh agent with zero prior context. Everything you need to pick up at
Phase 3 (or to audit Phases 0–2) is here. Line numbers are approximate (they drift as
the file changes) — trust the **function names**.

## Branch & state
- **Branch:** `pmcc-bucketb-phase2-2026-07-21`
- **HEAD after the Phase-2 commit:** `5126965` — "PMCC Bucket-B Phase 2: roll-quality gates
  (gate-0 override + B7 + B9 + B2; B6 withdrawn)". (This handoff file is a follow-up commit on
  top of `5126965`.)
- **Not pushed.** `auto_execute` stays **false**. Nothing is live; all paper.
- **Engine module:** `trading_corp/agents/divisions/pmcc_robinhood.py`
- **Tests:** `tests/test_pmcc_logic.py` + `tests/pmcc_regression/` (detectors + frozen baseline)

## Phase status
| Phase | Scope | Status |
|---|---|---|
| 0 | Regression baseline harness (`tests/pmcc_regression/`, frozen 157-row baseline) | **complete / committed** |
| 1 | B4 (atomic legs) + B1 (HOLD precedence) + B11 (holiday guard) | **complete / committed** (`7bc44b5` + carryover `7f50ffa`, `95ba259`) |
| 2 | gate-0 override + B7 + B9 + B2 (**B6 withdrawn**) | **complete / committed** (`5126965`) |
| 3 | LEAP cluster (B8, B3) | **unstarted** |
| 4 | Delta-vs-IV (A3 → B5) | **unstarted** |
| 5 | Terminal-DTE afternoon seam (B10) + readiness gate + exit | **unstarted** |

Plans of record: `planning/pmcc_option2_bucketB_plan.md` (all phases) and
`planning/pmcc_phase2_plan.md` (Phase-2 detail). Audit memory:
`memory/pmcc-logic-audit-2026-07-21.md` (OUTSIDE the repo — the operator's auto-memory).

## Gates built (Phases 1–2) — where each lives
All in `pmcc_robinhood.py` unless noted. Evaluation order inside `_propose_roll_short`:
**B9 earnings → B7 selection → B2 credit** (abort on the FIRST failing gate).

### Phase 1 (committed earlier)
- **B4 — atomic rolls/opens.** A roll/open that can't resolve its re-open leg ABORTS (proposes
  nothing) + writes a `pmcc_roll_aborted` audit via `_audit_roll_abort`. Sites: `_propose_roll_short`,
  scan roll_leap, `propose_orders_for_pair` roll_leap, `_propose_open_pmcc`. Finders stash
  `_last_weekly_diag` / `_last_leap_diag`.
- **B1 — HOLD precedence.** `_deterministic_roll_allowed`: the DTE≤2 / ≥50%-profit trigger yields
  to an explicit LLM HOLD/WATCH (unless `override.kind == "hold_override"`; empty/None action →
  ALLOWED). 0-DTE `_terminal_dte_time_release` guard unchanged.
- **B11 — holiday guard.** `_scan_should_fire` (`trading_corp/main.py`) skips full market closures
  via `default_calendar()` (strict — never adds a fire-day).

### Phase 2 (commit 5126965)
- **Gate-0 — override contract** (see its own section below). `_OVERRIDE_KINDS` constant (~L57);
  `PMCCAnalysis.override` field (~L411); LLM output-schema key + parse in `_llm_analyze_position`
  (~L1061 schema, ~L1104 parse); `_override_kind(self, analysis)` validator (~L2350).
- **B7 — roll-out enforcement.** `_find_best_weekly` (~L3405) gained `after_dte`; an inner
  `_rolls_out(d)` predicate requires `_days_to(d) > after_dte` on roll paths (opens pass None).
  Wired at the **3 roll-path call sites**: `propose_orders_for_pair` roll_leap (~L1234, `after_dte=
  pos.short_leg_dte`), scan roll_leap (~L2184, `after_dte=leg.short_leg_dte`), `_propose_roll_short`
  (~L3230, `after_dte=leg.short_leg_dte`).
  - **`_WEEKLY_FALLBACK_MAX_DTE = 60`** (constant, ~L54): the sparse-chain fallback may only accept
    an expiry that is a plausible weekly by DTE — blocks a 365+ DTE LEAP call from being taken as a
    "weekly" (the `future[0]` LEAP-as-weekly pathology). Module constant, NOT a strategies.yaml key
    (structural safety bound, not a tunable). Never realized in the 157-row history (all short-opens
    ≤59 DTE), so it would have blocked zero historical selections.
  - **Three-way abort diag** in `_last_weekly_diag.reason`: `no_future_expiry_dates` (empty/expired
    chain) / `no_rollout_weekly` (dates exist, none roll out past the current short) /
    `no_weekly_within_ceiling` (open path, no weekly under the ceiling). B7 is **hard-enforced (no
    override).**
- **B9 — earnings gate on rolls** (in `_propose_roll_short`, runs FIRST). Skill HARD RULE L257
  "No new short premium within 7 DTE of earnings" (a roll opens a new short). New tri-state
  `_earnings_gate_state(symbol)` (~L835) returns `("blocked"|"clear"|"data_unavailable", reason)`;
  `_blocked_by_earnings` (~L870) now DELEGATES to it (blocked iff state=="blocked"; open path
  byte-identical). Blocks if `blocked` AND `override_kind != "earnings_override"` → abort reason
  `earnings_window`. **Fail-open** on missing yfinance data, but the state is recorded (see
  observability below).
- **B2 — credit gate** (in `_propose_roll_short`, after selection). **Conservative basis:** sell
  new weekly @ `bid`, buy old short back @ `mark` (the existing short exposes mark only, no ask) →
  `conservative_net = open_bid − close_mark`. Also computes `mark_net`. **PRE-FEE:** no fee data
  exists at proposal time → `fees_included: false` + a `fee_gap` note in the audit. Blocks
  `conservative_net < 0` AND `override_kind != "net_debit_justified"` → abort reason
  `net_debit_roll`. **Hard-zero default is a DELIBERATE tightening** — see `pmcc_phase2_plan.md` §1
  (STANDARD's ≤8%-LEAP debit latitude, skill L255, rides the `net_debit_justified` override rather
  than being re-derived in the gate; revisit if the override proves noisy in paper).
- **Amendment-4 observability.** `_audit_roll_abort` (~L3998) gained an optional `extra` dict; abort
  payloads carry a **`gates` map** of every gate evaluated up to the abort
  (e.g. `{"earnings":"clear","selection":"ok","credit":"blocked"}`). SHIPPED rolls write a separate
  `pmcc_roll_gates` audit (end of `_propose_roll_short`) recording the earnings state + nets — so a
  roll that shipped because the source was DOWN (`gates.earnings == "data_unavailable"`) is
  distinguishable from one that shipped because earnings were CLEAR. This audit is separate so the
  **order legs stay byte-identical** to pre-Phase-2.

## The override contract (gate-0)
- **Shape:** `PMCCAnalysis.override = {"kind": <str>, "reason": <str>}` or `None`.
- **Valid kinds (`_OVERRIDE_KINDS`):** `hold_override` (B1), `net_debit_justified` (B2),
  `earnings_override` (B9). One kind per rec.
- **Malformed handling (`_override_kind`):** non-dict / unknown kind / missing-or-blank reason →
  returns **None** = treated as NO override (fail-safe — the gate applies). Never raises.
- **Consumers:** B1 (`_deterministic_roll_allowed`), B2 credit gate, B9 earnings gate. **B6/B7 have
  NO override — hard-enforced.**
- **LLM populates it** via one optional `"override"` key in the `_llm_analyze_position` output
  schema (parsed defensively: `data.get("override") if isinstance(..., dict) else None`). This is
  minimal LLM-adjacent plumbing, NOT a `_STANDARD_RULES`/`_BLACK_SHEEP_RULES` redesign.

## B6 — WITHDRAWN (do not resurrect as specified)
B6 was "add an OTM guard to `target_strike` (reject ITM)". **Withdrawn on investigation:** the ITM
`target_strike` is the INTENTIONAL halfway-roll mechanism, not a bypass.
- Skill: `_BLACK_SHEEP_RULES` **L103-104** ("new strike = halfway between current short strike and
  underlying price" → strictly ITM on a breach, since spot > strike), **L111** (Major breach =
  halfway roll), **L135-141** (STRIKE TARGETING: populate `target_strike` with the midpoint
  *precisely because* the delta picker "silently breaks the rule").
- `_select_weekly_strike` **docstring L523-527** documents the `target_strike` path as a deliberate
  LLM override ("Caller is responsible for sanity — we don't second-guess").
- B6's fallback (ITM → reject → delta selection) would have silently defeated every halfway roll —
  the exact failure the skill warns of. History: only 3 of 57 rolls with strike+spot recorded had an
  ITM new short, none a clean halfway-midpoint, all board_rejected.
- **Removed** from the Phase-2 plan + Bucket-B plan-of-record; `itm_target_strike_bypass` dropped
  as an acceptance gate (detector left in the harness, 0 historical). The two `target_strike` tests
  are correct and were left with their assertions intact.

## ★★ Standing caveat (apply to every remaining item BEFORE building it)
> The audit's method was to map YAML + prompt-level rules against Python enforcement and flag the
> gaps. **An unenforced rule is only a defect if enforcement was intended — not if the rule is
> deliberately delegated to the LLM layer.** In this system a substantial amount of behavior lives
> in the LLM by design, and the operator TRUSTS that layer. Before treating any remaining Bucket-B
> item as a bug, confirm the gap is unintended rather than deliberate delegation. THREE findings have
> already collapsed under this test: (1) STRC / held-stock adoption, (2) `get_universe` scanning
> every held stock, (3) B6 target_strike ITM.

### Item-2 scan verdicts for the unstarted items (investigation only — no specs changed)
- **B10 (afternoon terminal-DTE seam) — REAL (Phase 5).** The terminal-DTE release is EXPLICITLY a
  deterministic code job (skill L152 "deterministic Python guard `_terminal_dte_time_release`
  overrides … regardless of LLM judgment"). B10 just fires it on the 15:00 seam. Not delegated.
- **B8 (LEAP strike delta band) — LIKELY REAL, confirm the 0.80 hardcode.** Skill L270 "Acceptable
  LEAP delta range: 0.55-0.80" AGREES with config `long_leg.delta_min:0.55`; code
  `_select_leap_strike` hardcodes ≥0.80 (top of band). Deterministic path. Before building, confirm
  whether 0.80 is intended conservatism (deepest-ITM = most stock-like) or the config was meant to
  drive it.
- **B5 (IV-conditioned short delta) — ⚠ CANDIDATE ENDORSED-DESIGN.** The skill already gives the LLM
  the regime delta table (BS L100; STANDARD L193-196) and the LLM sets `target_delta`;
  `_short_target_delta`'s static 0.25 is only the FALLBACK. The "24 deltas ≥0.40" are plausibly
  LLM-set aggressive deltas + halfway-rolls (both endorsed) — SAME CATEGORY AS B6. Investigate before
  Phase 4. **A3 (IV wiring) exists only to support B5 → falls away if B5 does.**
- **B3 (LEAP-roll cost/benefit gate) — SPLIT.** The old-LEAP-mark-0.0 DATA defect is REAL (fix so
  the roll cost is visible). But the auto-BLOCKING cost/benefit gate may collide with the design
  intent that `_promote_to_roll_leap_if_hard_rule` SURFACES the 4-leg compound for the OPERATOR to
  accept/reject (skill L171-184, L259-266) — a deterministic block could pre-empt a HITL decision.
  Investigate the gate half before Phase 3; the price-fix half is unambiguous.

## Roll-down finding (investigation only — DO NOT implement)
Prompted by a lone RIOT anomaly (29→24 short, spot 24.08, board_rejected). **A roll-down IS
legitimate — it belongs in the LLM layer, not a deterministic gate.** The skill PERMITS roll-downs:
LEAP roll-down at delta<0.40 (**L269**), and the SHORT OTM-roll after a drop re-strikes below an old
now-deep-OTM strike (**L108** "OTM roll at 50%+ profit … open new 7-DTE delta 0.20-0.30"). A blanket
`new_strike >= old_strike` invariant would misfire on both — same category as B6. The RIOT case
(roll down to ~ATM 0.539 delta) matches no rule but is a defensible discretionary mean-reversion bet
already caught by HITL; evidence is thin (1 clean case; 69 rolls lack strike/spot). **No new Bucket-B
item; at most a monitor-only metric.**

## Fixtures changed across Phases 1–2 (and why)
**Phase 1** added test *families* (no existing-fixture data rewrites): the B4 aborts
(`test_b4a`–`test_b4d`, `test_b4_scan_roll_aborts_on_sparse_chain`), B1 HOLD precedence
(`test_b1a`–`test_b1e`), B11 holiday, plus the audit-capture infra (`_CaptureLogger`, `cap_logger`,
`agent_logged`, `_roll_leap_analysis`, `_abort_event`).

**Phase 2** (all in `tests/test_pmcc_logic.py` unless noted):
- **`_broker_with_chains`** — added a strictly-later **35-DTE** weekly alongside the 14-DTE one.
  Reason: several roll tests hold a 14–30-DTE short and expect a roll; under B7 the new short must
  expire strictly later, so a chain whose only weekly is 14 DTE can't satisfy a roll of a ≥14-DTE
  short. Both weeklies share strike/delta so no strike assertion shifts; opens still pick the nearest
  (14-DTE).
- **`test_scan_rolls_existing_pmcc_in_options_only_account`** — inline broker moved to a 35-DTE
  weekly; assert TIGHTENED from a lenient `in actions` subset to exact leg count + named actions.
- **`test_scan_proposes_roll_at_50pct_profit`** and **`test_b1d_llm_early_roll_still_rolls`** —
  assert TIGHTENED from lenient (`close in actions` / subset `<=`) to exact leg count + named
  actions. (`test_scan_proposes_roll_at_21_dte` was already strict; passes via the shared 35-DTE.)
- **`test_roll_leap_propose_emits_4_legs`** and **`test_propose_orders_promotes_roll_short_to_roll_
  leap_via_hard_rule`** — new weekly moved 7→14 DTE (+ target_dte) so the roll_leap short rolls OUT
  instead of same-expiry (7→7). Intent (4-leg compound) unchanged.
- **`test_roll_leap_emits_3_legs_when_no_qualifying_weekly` → REWRITTEN + RENAMED
  `test_roll_leap_aborts_when_no_qualifying_weekly`.** It had encoded the LEAP-as-weekly pathology
  as expected (lenient "3 OR 4 legs"); now asserts the atomic 0-leg abort + `sparse_chain_no_weekly_
  for_new_leap`. Docstring records it was a corrected-not-loosened test. **★ LESSON: lenient
  leg-count asserts ("3 or 4") cannot catch structural regressions — assert exact counts.**
- **`test_propose_roll_short_uses_target_strike_when_set`** and
  **`test_propose_roll_short_falls_back_to_delta_when_target_strike_none`** — added
  `override={"kind":"net_debit_justified", ...}` (their deep-ITM breach fixture makes the roll a net
  debit, incidental to the strike-selection assertion, which is unchanged) + the `clear_earnings`
  fixture.
- **New fixtures/helpers:** `clear_earnings` (stubs `get_next_earnings` → far-future date so roll
  tests don't couple to the live earnings calendar; applied to roll tests ONLY, open-path tests
  untouched); `_credit_roll_broker` / `_deep_itm_breach_broker` (B9/B2 scenarios).
- **New tests:** B7 (`test_b7_find_best_weekly_selects_strictly_later_on_roll`,
  `test_b7_propose_roll_short_opens_strictly_later`, `test_b7_same_expiry_only_chain_aborts`), B2
  (`test_b2_unauthorized_net_debit_roll_blocks`, `test_b2_net_credit_roll_ships`), B9
  (`test_b9_roll_blocked_within_earnings_buffer`, `test_b9_roll_ships_with_earnings_override`,
  `test_b9_data_unavailable_recorded_on_shipped_roll`); +1 detector acceptance in
  `tests/pmcc_regression/test_baseline.py` (`test_same_expiry_roll_detector_and_b7_acceptance`).

## ★★ OPEN ITEM — B9/B2 do NOT gate the roll_leap path (coverage hole, needs operator decision)
**Mechanism.** The B9 earnings gate and the B2 credit gate live ONLY in `_propose_roll_short`. The
**roll_leap** path assembles a 4-leg compound (close short + close old LEAP + open new LEAP + **open
new short on the new LEAP**) in two other places — `propose_orders_for_pair` roll_leap (~L1234) and
scan roll_leap (~L2187) — and neither runs B9 or B2. Its **4th leg opens a new short**, so a LEAP
roll can currently ship a **net-debit short** or a **short into an earnings window** with **neither
gate firing.**

**Why this is promoted from "known gap" to open item.** This is a coverage hole of the SAME CLASS
as B4's original close-without-recover: a correctness rule applied to one path (`_propose_roll_short`)
but not its sibling (roll_leap), where the sibling does the same risky thing (opens a short). It is
not merely "out of scope"; it is an inconsistency that a LEAP roll silently exploits.

**What DOES cover roll_leap today (verified read-only 2026-07-22):**
| Gate | roll_leap new short covered? | How |
|---|---|---|
| B4 atomic legs (Phase 1) | **YES** | both new legs resolved before proposing any close; aborts if either missing |
| B7 roll-out filter + `_WEEKLY_FALLBACK_MAX_DTE=60` ceiling | **YES** | `after_dte` wired at all 3 roll-path `_find_best_weekly` calls incl. both roll_leap sites (L1237, L2187) |
| Gate-0 override contract | field present, but **moot** on roll_leap (B9/B2 don't run) |
| **B9 earnings gate** | **NO** | `_earnings_gate_state` called only in `_propose_roll_short` (L3246) |
| **B2 credit gate** | **NO** | `conservative_net` logic only in `_propose_roll_short` (L3290+) |

**Implementation nuance (B2/B3 boundary — for whoever scopes the fix).** B9 ports cleanly to
roll_leap: the 4th leg opens new short premium, so the skill's "no new short premium within 7 DTE of
earnings" applies identically. B2's short-leg credit basis (`open_bid - close_mark`) also ports to
the close-old-short (leg 1) vs open-new-short (leg 4) pair. BUT roll_leap also swaps the LEAP (legs
2+3), so the FULL 4-leg compound cost is **B3's** domain (the LEAP-roll cost/benefit gate), not B2's.
A B2-on-roll_leap fix should gate the **short-leg credit + earnings only** and must NOT re-derive the
compound cost — doing so would double-count with B3.

**Needs an OPERATOR DECISION on sequencing:** fold the B9/B2 extension to the roll_leap path into
**Phase 3** (LEAP cluster — natural home, roll_leap is a LEAP operation), OR give it its **own Bucket-B
item number**. Do NOT build it before that decision. (Filed to `BACKLOG.md` and
`planning/pmcc_option2_bucketB_plan.md` so it's visible outside this handoff.)

## Known gaps (carry forward)
- **B2 is PRE-FEE** (`fees_included: false`): no fee data exists at proposal time (RH broker: none;
  `FillEvent.fee` post-fill only; `ProposedOrder`: none). The conservative net captures SPREAD, not
  fees; the `fee_gap` note says so. A fee-aware net needs a fee source that doesn't exist yet.
- **B9 FAILS OPEN** on missing yfinance data (thin names lack earnings dates) — but records
  `gates.earnings == "data_unavailable"` on the shipped-roll `pmcc_roll_gates` audit so it's visible.
- **Open-path tests still call `get_next_earnings` live** (the open path has always earnings-gated;
  the `clear_earnings` stub was scoped to roll tests only, by operator instruction). Full hermeticity
  is a separate cleanup.

## Standing operator rules (do not violate)
- **Fork rule:** if any test cannot be made valid without changing WHAT IT ASSERTS, STOP and report
  it as a fork. **Never rewrite a test to match new behavior.** (A corrected-not-loosened rewrite is
  allowed only when the old test asserted a pathology; document the history in the docstring.)
- **Happy-path byte-equivalence before commit:** a credit/rolled-out/non-earnings roll must emit
  byte-identical legs to the pre-change baseline (stash-vs-current diff, pair_id + ts stripped).
- **Show the full `pmcc_robinhood.py` diff + `git diff --stat` before committing. No commit until
  operator review.**
- **Path-scoped `git add`** — the parallel-session files (`config/strategies.yaml`,
  `trading_corp/agents/strategies/_whale_autopause.py`, `kalshi_copy_trader.py`,
  `polymarket_copy_trader.py`) belong to OTHER sessions and must stay UNSTAGED.
- **No prod deploy. auto_execute stays false.** Scope is `pmcc_robinhood.py`, `tests/`, `planning/`,
  `memory/` only.

## What green looks like now
- `pytest tests/test_pmcc_logic.py tests/pmcc_regression/ -q` → **118 passed** (~13s).
- Targeted gate check: `pytest tests/test_pmcc_logic.py -k "b7 or b9 or b2 or roll_leap_aborts or
  net_debit or target_strike"` → **20 passed**.
- The regression harness (`tests/pmcc_regression/test_baseline.py`) still pins the FROZEN 157-row
  baseline counts (18 same-expiry, 37 net-debit, etc.) — that is the "before" oracle, not a post-fix
  measurement; post-fix acceptance is proven behaviorally by the gate tests.

## Suggested next step (Phase 3) — but confirm scope with the operator first
Phase 3 = B8 + B3 (LEAP cluster). **Before building:** apply the standing caveat — B3's blocking gate
and B8's 0.80 hardcode both need the "is this intended?" check above. B3's old-LEAP-mark-0.0 DATA fix
is unambiguous; the auto-blocking gate is not.
