# PMCC Bucket-B Phase 2.5 plan — B9 + B2-short-leg on the roll_leap path

Branch `pmcc-bucketb-phase2.5-2026-07-22` (off the Phase-2 tip). Approved 2026-07-22. Scope:
`pmcc_robinhood.py` + `tests/`. **No `strategies.yaml` key additions. auto_execute stays false.**

## 0. Why this is its own phase (not folded into Phase 3)
Phase 2 built the B9 earnings gate and the B2 credit gate into `_propose_roll_short` ONLY. The
**roll_leap** path assembles its own 4-leg compound (close short + close old LEAP + open new LEAP +
**open new short on the new LEAP**) at two sites and never calls `_propose_roll_short`, so its 4th leg
can currently ship a **net-debit short** or a **short opened into an earnings window** with neither
gate firing — the same class of coverage hole as B4's original close-without-recover.

It is deliberately **NOT** folded into Phase 3: B2-on-roll_leap must gate the **short-leg credit
only** and must NOT re-derive the 4-leg compound cost, whereas Phase 3's B3 is *specifically* about
that compound cost. Building them together invites the double-counting flagged in the OPEN ITEM. This
phase touches only the short-leg credit + earnings; the LEAP legs (2+3) are left entirely to B3.

**Verified read-only 2026-07-22 — current gate coverage of the roll_leap new-short leg:**

| Gate | `_propose_roll_short` (covered sibling) | roll_leap in `propose_orders_for_pair` | roll_leap in the scan loop |
|---|---|---|---|
| B4 atomic legs | ✓ L3266-3273 | ✓ L1223-1245 | ✓ L2171-2195 |
| B7 roll-out (`after_dte`) | ✓ L3259-3264 | ✓ L1237 | ✓ L2187 |
| **B9 earnings** | ✓ L3246-3253 | ✗ absent | ✗ absent |
| **B2 credit** | ✓ L3286-3294 | ✗ absent (short emitted L1300-1322) | ✗ absent (short emitted L2251-2274) |

B7 + B4 already cover roll_leap; **B9 + B2 are what this phase adds.** (Line numbers drift — trust the
function names / anchors.)

## 1. Where each gate lands (both roll_leap sites)
Sites: `propose_orders_for_pair` roll_leap block (~L1222-1323) and the scan-loop roll_leap block
(~L2170-2275). Both currently do: `_find_best_leap` -> `_find_best_weekly(after_dte=...)` -> emit 4
legs. Insert the gates to mirror `_propose_roll_short`'s order (**earnings -> selection -> credit**):

- **B9 (earnings) — runs FIRST**, before `_find_best_leap`. Call the existing shared
  `_earnings_gate_state(symbol)`; if state is `"blocked"` AND `_override_kind(analysis) !=
  "earnings_override"` -> `_audit_roll_abort(reason="earnings_window", extra={"gates": ...})` then
  `return []` (propose path) / `continue` (scan path). **Fail-open** preserved: `data_unavailable`
  -> not blocked, but the state is recorded.
- **B2 (short-leg credit) — after `new_weekly` resolves**, before emitting legs. Compute on the
  close-old-short / open-new-short pair ONLY:
  - `close_mark = pos.short_leg_mark or 0.0` (the leg-1 buy-to-close of the **old** short),
  - `open_bid = new_weekly.get("bid")`, `open_credit_conservative = open_bid if not None else
    (new_weekly.mark_price or 0.0)`,
  - `conservative_net = open_credit_conservative - close_mark`; also `mark_net`.
  - Block `conservative_net < 0` AND `_override_kind != "net_debit_justified"` ->
    `_audit_roll_abort(reason="net_debit_roll", ...)`. **LEAP legs 2+3 are ignored** (B3's domain).

## 2. B2 short-leg-only semantics + the `short_leg_expiry is None` edge case
On roll_leap the close-old-short leg (leg 1) is **conditional** — it is only emitted when
`pos.short_leg_expiry` and `pos.short_leg_strike` are set (`propose_orders_for_pair`:1250; the scan
site guards on `leg.short_leg_expiry`:2200). When there is **no existing short to buy back**:
- there is nothing to close, so the new short (leg 4) is **pure premium income** — always a credit;
- `close_mark = pos.short_leg_mark or 0.0` -> `0.0` -> `conservative_net = open_bid >= 0` -> **B2
  passes** with no special-casing needed.

This mirrors `_propose_roll_short`, which already uses `close_mark = leg.short_leg_mark or 0.0`. The
gate is therefore correct in both the roll-the-existing-short case and the fresh-short-on-a-new-LEAP
case; no separate branch is required, but the acceptance suite pins the no-old-short case explicitly
so a future refactor can't silently start blocking it.

## 3. Override valve semantics (unchanged from Phase 2)
`_override_kind(analysis)` (the existing gate-0 validator) supplies the kind. `earnings_override`
unblocks B9; `net_debit_justified` unblocks B2. Malformed/absent override -> None -> gate applies
(fail-safe). No new override kinds; no LLM/prompt change.

## 4. Audit shape (reuse verbatim, keep legs byte-identical)
- **On abort:** `_audit_roll_abort(reason=<code>, symbol=..., extra={"gates": {...},
  "conservative_net", "mark_net", "close_mark", "open_bid", "fees_included": False, "fee_gap": ...})`
  — same payload shape as `_propose_roll_short`. `gates` = `{"earnings": <tri-value>, "selection":
  "ok", "credit": <state>}`.
- **On ship:** a **separate** `_audit_division("pmcc_roll_gates", {"symbol","gates",
  "conservative_net","mark_net","override_kind"})` at the end of each roll_leap block. It is a separate
  call, so the 4 emitted order legs stay **byte-identical** to pre-2.5 (same principle Phase 2 used to
  keep `_propose_roll_short` legs byte-identical). `fees_included:false` + `fee_gap` note carried, same
  as B2 today (PRE-FEE — no fee source exists at proposal time).

## 5. Shared-helper vs duplicate (recommendation + guardrail)
- **B9:** `_earnings_gate_state` is **already shared** (open path + `_propose_roll_short` delegate to
  it). Porting = one call per site. **Zero refactor risk.**
- **B2:** extract the **pure computation only** into `_short_roll_credit(new_weekly, close_mark) ->
  (conservative_net, mark_net, open_bid)` and have `_propose_roll_short` call it too. Pure function,
  no side effects. Keep the **control flow (abort reason, return-vs-continue, audit) duplicated per
  site** (~6 lines each) — it genuinely differs across the three call sites, and that is where
  behavior risk lives.
- **Guardrail (hard):** the extraction touches `_propose_roll_short`, so it is gated by the
  **happy-path byte-equivalence check** — a credit/rolled-out/non-earnings `_propose_roll_short` roll
  must emit byte-identical legs AND an identical `pmcc_roll_gates` payload after the extraction. **If
  the pure-helper substitution shows ANY diff, stop and duplicate the ~6-line computation instead** —
  byte-equivalence over DRY (operator instruction).

## 6. Gate order (both sites) = earnings -> selection (B7/B4) -> credit
Identical to `_propose_roll_short` so the `gates` map ordering and first-failing-gate semantics match.
B9 fails open, so it does not disturb the existing B4/B7 abort reasons on sparse chains (except the
network-call concern in tests — see below).

## 7. Tests touched (existing) — DRY-RUN blast radius + fork watch
The roll_leap emit/abort tests will newly run B9 (a live `get_next_earnings`/yfinance call) and B2
(credit check). Expected to need fixture augmentation (the **dry-run** pins the exact set before any
fixture edit):
- `test_roll_leap_propose_emits_4_legs` (~L1433) and
  `test_propose_orders_promotes_roll_short_to_roll_leap_via_hard_rule` (~L1552) — will need the
  **`clear_earnings` stub** (avoid a live earnings call / coupling, exactly as Phase 2 did for the
  `_propose_roll_short` roll tests) AND a broker whose new short is a **credit** vs the old short mark.
- `test_roll_leap_aborts_when_no_qualifying_weekly` (~L1496), `test_b4b_roll_leap_aborts_when_no_leap`
  (~L2136), `test_b4c_roll_leap_aborts_when_no_weekly_for_new_leap` (~L2157) — B9 fails open, so their
  B4/B7 abort reasons should survive; but each makes a live earnings call unless stubbed -> add
  **`clear_earnings`** for determinism/speed. Assertions unchanged.
- Scan-loop roll_leap **emit** path is under-covered today (both existing emit-4-leg tests are the
  `propose_orders_for_pair` path) -> new scan-site tests rather than assume parity.

**★ Fork watch (standing rule):** if any existing roll_leap test *intrinsically* seeds a net-debit or
earnings-window roll_leap and asserts 4 legs as correct, that is a **fork -> STOP and report**, not a
rewrite. Expectation (as in Phase 2's target_strike tests): the debit/earnings is incidental to a
leg-structure assertion, so adding `override=...` or making the fixture a credit is a
corrected-not-loosened augmentation — verified **per test** during the build, not assumed.

## 8. New synthetic tests (per gate, BOTH sites)
- **B9:** roll_leap in-buffer -> abort `earnings_window`; + `earnings_override` ships; +
  `data_unavailable` recorded on a shipped roll_leap.
- **B2:** roll_leap net-debit short -> abort `net_debit_roll`; + `net_debit_justified` ships; +
  net-credit ships; + **no-old-short** case (leg-1 absent) -> B2 passes.
- **Interaction:** two gates trip -> first-failing reason + `gates` map (mirrors Phase-2 sec.5).
- **Parity:** each of the above for both `propose_orders_for_pair` and the scan loop.

## 9. Acceptance gates
- New synthetics green; roll_leap net-debit short -> 0 unless `net_debit_justified`; roll_leap
  earnings-window -> 0 unless `earnings_override`; `data_unavailable` recorded on shipped roll_leaps.
- All existing PMCC tests green after fixture augmentation.
- **Happy-path byte-equivalence:** a credit/non-earnings roll_leap emits byte-identical 4 legs to
  pre-2.5; AND `_propose_roll_short` happy-path stays byte-identical (guards the `_short_roll_credit`
  extraction).
- `auto_execute` stays false. No new `strategies.yaml` key (hard-zero net-debit block, as B2 today).

## 10. Implementation order
1. `_short_roll_credit` pure helper + refactor `_propose_roll_short` to use it -> byte-equivalence
   check on `_propose_roll_short` (STOP + duplicate if any diff).
2. B9 + B2 at `propose_orders_for_pair` roll_leap; then the scan-loop roll_leap.
3. **DRY-RUN: run the suite, report which existing roll_leap fixtures break, STOP before editing any
   fixture** (this doc's sec.7).
4. After operator confirms the blast radius: fixture augmentation (fork-check per test) + new
   synthetics + regression harness between each.
5. Show `git diff --stat` + full `pmcc_robinhood.py` diff before committing; no commit until operator
   review; path-scoped add (parallel-session files stay unstaged).
