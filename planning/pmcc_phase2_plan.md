# PMCC Bucket-B Phase 2 plan — B2 + B7 + B9 + the `override` contract

Branch `pmcc-bucketb-phase2-2026-07-21` (off Phase 1 `7f50ffa`). Approved 2026-07-21 with five
amendments (folded in below). Scope: `pmcc_robinhood.py`, `tests/`, `planning/`. No
`strategies.yaml` key additions (flag if B2 needs a configurable floor). auto_execute stays false.

> **B6 WITHDRAWN 2026-07-21** (was §2, target_strike OTM guard). On investigation the ITM
> target_strike is the intentional halfway-roll mechanism (skill L103-104/L111/L135-141;
> `_select_weekly_strike` docstring L523-527), and B6's fallback would have silently defeated every
> halfway roll. Dropped from this plan, the Bucket-B plan-of-record, and the acceptance gates;
> `itm_target_strike_bypass` is no longer a gate. See `memory/pmcc-logic-audit-2026-07-21.md`
> Phase-2 note. The two target_strike tests are correct and untouched.

## 0. `PMCCAnalysis.override` contract — lands first (B1/B2/B9 consume it)
- **Shape:** `override: dict | None = None` on `PMCCAnalysis` (`pmcc_robinhood.py:382-402`) =
  `{"kind": <str>, "reason": <str>}` or None. Enum `kind`: `hold_override` (B1),
  `net_debit_justified` (B2), `earnings_override` (B9). Singular kind per rec.
- **LLM populates it (approved, additive):** one optional `"override"` key added to the output
  JSON schema in `_llm_analyze_position` (:~1006-1017), parsed at :~1046-1058. **One-sentence**
  schema instruction only — NOT a `_STANDARD_RULES`/`_BLACK_SHEEP_RULES`/skill rewrite. This is
  the mechanism that lets the LLM's judgment survive the deterministic layer.
- **Consumers:** `_deterministic_roll_allowed` (B1 — allow HOLD→roll when
  `kind=="hold_override"`, completing Phase-1's escape hatch); B2 credit gate; B9 earnings gate.
  **B6/B7 have NO override** (hard-enforced).
- **Malformed (fail-safe):** non-dict / missing-blank reason / unknown kind → treated as **no
  override** (gate applies); never raises. `_override_kind(analysis)` returns the validated kind
  or None; every gate calls it. Regression detectors already key off `override_kind`.

## 1. B2 — credit gate (`_propose_roll_short`, after `new_weekly` resolves at :3182)
- **Price basis: CONSERVATIVE (amendment 2), not mark.** open credit = `new_weekly["bid"]`
  (sell at bid); close debit = `leg.short_leg_mark` (buy-to-close). NOTE: the existing short leg
  comes from `get_option_positions_detail` which exposes **mark only, no ask** — so the close
  uses mark (the strictest available); the "ask-whichever-worse" is unavailable without an extra
  chain query (flagged, not done). `conservative_net = open_bid − close_mark`.
- **Also compute `mark_net`** = `(new_weekly.mark_price or bid) − close_mark` (what the card
  shows). Both go in the audit payload so a blocked roll is understandable without re-derivation.
- **FEES (amendment 3): the gate is PRE-FEE.** No fee data exists at proposal time (RH broker:
  none; `FillEvent.fee`=0.0 post-fill only, `models.py:122`; `ProposedOrder`: none). The
  conservative net captures **spread**, not fees. Audit payload records
  `fees_included: false` + a `fee_gap` note (RH per-contract regulatory/exchange fees excluded)
  so a pre-fee net is never silently called "net."
- **Block:** `conservative_net < 0` AND `_override_kind != "net_debit_justified"` → abort via
  `_audit_roll_abort` (reason `net_debit_roll`). Baseline 37 → **0 unless override.**
- **strategies.yaml flag:** default is a **hard zero** net-debit block (no new key). If you later
  want a configurable credit floor (reinstate `minimum_credit`) that's a config key — flagged,
  not added.
- **★ Hard-zero is a DELIBERATE tightening (2026-07-22), not an oversight.** The skill's STANDARD
  block permits a debit **≤ 8% of LEAP value** (`_STANDARD_RULES` L255 "Never roll for debit > 8%
  of LEAP value"); BLACK SHEEP is stricter ("Always for credit", L102). We chose a **hard-zero**
  deterministic block and let that STANDARD ≤8%-LEAP latitude ride the **`net_debit_justified`
  override** rather than re-deriving LEAP value in the gate. Rationale: the gate stays simple and
  regime-agnostic; the LLM (which knows the regime and the LEAP value) is the right place to
  authorize a permitted small debit. Consequence: every STANDARD small-debit roll now requires an
  explicit override or it blocks — stricter-by-default than the skill's letter, but faithful to
  its intent (credit-or-justified). **Revisit if the override proves noisy in paper** (e.g. if
  legitimate ≤8%-LEAP STANDARD rolls block often, reinstate a LEAP-value-aware threshold or a
  configurable floor).

## 2. B7 — roll-out enforcement (`_find_best_weekly`) — DONE 2026-07-21
- `_find_best_weekly` gained an `after_dte` param; roll paths (`_propose_roll_short`, both
  roll_leap sites) pass the current short's DTE; the date filter additionally requires
  `_days_to(d) > after_dte` (strictly later). Opens pass None. Covers both roll_short and
  roll_leap short-leg selection. **No override.** Baseline 18 → **0.**
- **Fallback ceiling (own finding):** new `_WEEKLY_FALLBACK_MAX_DTE = 60` module constant bounds
  the sparse-chain fallback so a LEAP-DTE contract can never be taken as a "weekly" (the
  LEAP-as-weekly hazard — latent, never realized in history, all short-opens ≤59 DTE). Constant,
  not a strategies.yaml key. 3-way abort diag: `no_future_expiry_dates` / `no_rollout_weekly` /
  `no_weekly_within_ceiling`.
- **Bucket-C fork resolved:** `test_roll_leap_emits_3_legs_when_no_qualifying_weekly` (which
  encoded the LEAP-as-weekly pathology as expected via a lenient `3 OR 4` assert) rewritten +
  renamed `test_roll_leap_aborts_when_no_qualifying_weekly` (atomic 0-leg abort). Lenient
  leg-count asserts were a blind spot — the 3 lenient Bucket-A/B tests were tightened to exact
  counts while updating their fixtures.

## 4. B9 — earnings gate on rolls (`_propose_roll_short`)
- Reuse `_blocked_by_earnings` (:818) → `get_next_earnings` (yfinance) + `_earnings_buffer_days`
  (:808, existing `strategies.yaml earnings_buffer_days`, default 7). No new config key. Same
  helper the open path uses (:2990). Block if in-buffer AND `_override_kind != "earnings_override"`
  → abort (reason `earnings_window`).
- **FAIL-OPEN OBSERVABILITY (amendment 4):** `_blocked_by_earnings` returns not-blocked on
  missing yfinance data. Every roll records the earnings-data state in the audit payload —
  `earnings: "blocked" | "clear" | "data_unavailable"` — so a roll that shipped because the data
  source was DOWN is distinguishable from one that shipped because earnings were genuinely clear.
  (Requires distinguishing "no earnings within buffer" from "no earnings data at all" — a small
  extension to how the earnings result is read.)

## 5. Interaction & evaluation order
Selection gate (in `_find_best_weekly`): B7 roll-out. Proceed gates (in `_propose_roll_short`):
B9 earnings, B2 credit. **Order in `_propose_roll_short`:**
1. **B9 earnings** (independent — abort early if in-buffer w/o override).
2. **B7 selection** via `_find_best_weekly`; no valid roll-out weekly → existing **B4 abort**
   (`sparse_chain_no_weekly`).
3. **B2 credit** on the selected weekly.

**Winning reason / audit:** abort on the FIRST failing gate in that order; its reason code is
`pmcc_roll_aborted.reason`. Payload also carries a `gates` map of every gate evaluated up to the
abort (e.g. `{"earnings":"clear","selection":"ok","credit":"blocked"}`) → tells which gate
stopped it and that earlier ones passed. **Singular-override limitation:** a roll tripping two
gates can be authorized for at most one; the other still blocks → conservative fail-safe abort.
A `kinds` list is a future extension if multi-override is wanted.

## 6. Tests
- **Existing PMCC suite — expected pass** (complete-chain roll tests seed a credit roll on a later
  expiry). **Standing rule:** if any existing test encodes a same-expiry OR net-debit roll as
  expected, **stop and report as a fork** — do NOT rewrite it. (Applied in B7: the Bucket-C test
  was a corrected-not-loosened rewrite; the two target_strike tests were correct and untouched.)
- **New synthetic (per gate):** override malformed/valid recognition; B2 net-debit abort +
  override-ships + net-credit-ships (+ conservative-vs-mark payload); B7 later-expiry selected /
  same-expiry→abort (DONE); B9 in-buffer abort + override-ships + data-unavailable recorded;
  interaction (two gates → priority reason + `gates` map; single override doesn't unblock the
  other). Regression harness: `net_debit_roll` / `same_expiry_roll` → 0 unless override.

## 7. Acceptance gates
`net_debit_roll → 0` unless `net_debit_justified`; `same_expiry_roll → 0` (DONE);
earnings-window rolls `→ 0` unless `earnings_override`; all pre-existing PMCC tests green;
**happy-path (credit, rolled-out, non-earnings) roll emits byte-identical legs to pre-Phase-2**
(stash-vs-current check as in Phase 1).

## Implementation order
override contract → B7 selection (DONE) → B9 → B2, gate by gate, regression harness between each.
Show `git diff --stat` + full `pmcc_robinhood.py` diff before committing; do not commit until
operator review; path-scoped add (parallel-session files stay unstaged).
