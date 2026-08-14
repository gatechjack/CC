# Board Memo — MACE 3-Active Expansion + OQ-2 Serialization + Entry-Halt Button

**Date:** 2026-08-13 (build session) — target live 2026-08-14 15:45 ET eval, ATTENDED.
**Branch:** `claude-2026-08-13b` @ base `b11af9b` (= origin/prod-live tip at build time).
**Plan of record:** `.claude/plans/polymorphic-tinkering-crane.md` (approved). Draft
superseded by this memo: `research/mace_oq2/BOARD_MEMO_DRAFT.md`.

---

## 1. Timeline override (Board, 2026-08-13)

The GO/NO-GO soft-NO is overridden — build tonight, go 3-symbol live at the
2026-08-14 15:45 ET eval, ATTENDED. The override is on the TIMELINE ONLY; every
safety gate is reaffirmed (Board: overriding the timeline, not the safety tests).

## 2. CHECKPOINT 0 — RATIFIED, five binding rulings

1. **GDX Option A** — GDX stays in the roster. Ships a PROJECTED 2026-12-21
   ex-div date, `exdiv_guard: true`, with a **hard December-refresh tripwire**
   (calendar comment block + open-items ledger entry). VanEck publishes its
   annual distribution only in December — a confirmed 2026 date cannot exist
   yet; the Board ratified that PROJECTED-guard-ON with a tripwire satisfies
   the never-guessed-dates rule for this structurally-unannounceable case.
2. **Ex-div citations table RATIFIED** as submitted
   (`research/mace_oq2/EXDIV_EVIDENCE.md` + 3 issuer PDFs), including the IWM
   correction: the two shipped third-Monday projections were WRONG
   (9/21 → **9/15**, 12/21 → **12/15**, per iShares GPS0826-5839861).
3. **Widths + blackouts RATIFIED:** GDX w2 (w1 fallback), XLE w2 (w1
   fallback), IBIT w1, FXI w1 (**no fallback** — validator requires
   fallback < width), IWM w3 (w2 fallback). Blackouts: GDX [FOMC], XLE
   [OPEC], IBIT [], FXI [PBOC, LPR_FIX], IWM [FOMC, CPI].
4. **Roster CONFIRMED:** actives {IBIT, XLE, GDX}; backfill FXI → IWM.
   **Pre-ruling for Phase 5:** if TWO active slots fall out on live quotes,
   the backfill order FLIPS to **IWM FIRST, then FXI** (FXI's dead book —
   OI 0, 22c wide — acceptable for one slot at most). Single slot out:
   FXI first as originally ruled, subject to its liveness gate.
5. **Sequencing:** Phase 3 config package proceeds; at Checkpoint 4,
   enumerate EVERY delta from the 88f/12e baseline BY TEST NAME with
   disposition, plus drift-base evidence (`git log --oneline
   e113843..b11af9b` + `diff --stat` filtered to mace/, config/mace*,
   web/mace* showing zero MACE-runtime touches in that span).

## 3. OQ-3 REVERSAL

IBIT is promoted from overflow-only receiver to primary universe member
(`overflow_only` removed, `enabled: true`). Rationale: the 3-active expansion
needs a third non-correlated underlying (crypto); the OQ-3 overflow-only ruling
predated OQ-2 serialization, which now guarantees IBIT a bounded, audited
window slot. IBIT IVR was 9.1 at stage-A — a day-1 skip on the IVR>=25 floor
is coherent behavior, not a defect.

## 4. SURFACED DEVIATION — IBIT ships `exdiv_guard: false`

The config boot gate (`mace/config.py:396-416`, itself a Board ruling —
2026-08-09 Checkpoint 1) validates: any symbol BOTH `enabled` AND
`exdiv_guard: true` with ZERO entries for it in the ex-div calendar collects a
validation error and `load_mace_config` raises ValueError — boot REFUSAL, not
a warning. The WHY, per the gate's own rationale: an enabled+guard-on symbol
with no dates means the position-CLOSING guard is **silently inert** — a
zero-HITL engine would run believing it has ex-div protection it does not
have. Fail-closed at boot is the only honest behavior.

That gate forces the IBIT choice among exactly three options: (1)
`guard: true` + no dates = boot refusal (bricks the engine); (2) `guard: true`
+ fabricated dates = violates the never-guessed-dates rule AND is
meaningless — IBIT is a spot-bitcoin ETP that has never paid and has no
distribution schedule to guess FROM (nothing to be silently-inert ABOUT);
(3) `guard: false` with the non-payer rationale documented = the GLD/USO
precedent (both non-payers, both already ship guard:false on identical
grounds). IBIT ships (3). This is a DEVIATION from the "guard on for every active" ideal,
surfaced here for the record; it is the established non-payer precedent, not
a new policy.

## 5. Strategy parameters (Board-ruled)

- rung_risk_pct 0.055 → **0.10**
- deployment_target_pct 0.80 → **0.95**
- risk_band_max_usd 250 → **260** (risk-band ruling 0a7c1ea floors intact:
  min = 50×width → w1 [50,260], w2 [100,260], w3 [150,260])
- weekly_new_rungs_per_symbol 2 → **1**
- entry_max_attempts 5 → **2**, entry_fill_wait_sec 60 → **30**
  (~70-80s/symbol typical; 3-symbol worst ~6.5 min vs the 13-min window)
- max_rungs_per_symbol 5, max_contracts 1 unchanged.
- `ibit_overflow_cap` + `overflow_max_per_symbol_session` retained as
  validator-required vestigial keys (commented as such in the yaml).

## 6. OQ-2 serialization (code — commits ee9cfd5 + 66cad59)

Prioritized sequential: IVR-desc primaries (fallback −1.0 stable sort = config
order at the tail), then overflow. Per-symbol dynamic deadline
`now + (cutoff − now)/symbols_remaining` recomputed from actual now (early
finishers donate time forward); audited `mace_entry_window_skip` (no symbol is
ever silently starved); `window_budget` clean stand-down via the existing
`_entry_standdown(clean=True)` path; precedence cutoff > operator_halt >
window_budget; slots-loop poll 30s → 5s. Strictly ONE ladder in flight —
concurrency was evaluated and REJECTED (it reintroduces the 08-12 dup-entry
stale-rung-snapshot bug class at N² interleavings). Chokepoint
(`_require_risk` funnel), fake-fill guard, fake-cancel guard, cancel
json-body, ref_id scheme: untouched by construction, re-proven by test.

## 7. Entry-halt button (Board addition — commits 7300985 + 3210a4a)

/mace gets its ONE write surface — an entry-halt latch (`agent_state`
robinhood_mace/entry_halt, PEAD live-dial precedent) with auto_execute:false
semantics. `POST /mace/halt` / `POST /mace/arm`, Authelia-gated,
audit-BEFORE-state (`mace_ui_halt`/`mace_ui_arm`). Halts the NEXT
symbol/attempt (honest latency stated in the UI); an already-resting order
completes its fill-or-cancel cycle; open-position management deliberately
unaffected. Tri-state UI: ARMED / HALTED (button) / HALTED (config).
Fail-safe: latch-read error == NOT halted (the button is an ADDITIONAL brake,
never a gate that can wedge entries off on a DB hiccup). 13-test matrix incl.
the Board-required manage-runs-while-halted proof.

## 8. Ex-dividend policy — shipped calendar state

Never a dividend-payer with guessed dates or guard off (GDX Option A ruling
covers the one structurally-unannounceable case; IBIT is a non-payer):

| Symbol | Dates shipped | Status | Source |
|---|---|---|---|
| XLE | 2026-09-21 (pay 9/23), 2026-12-21 (pay 12/23) | confirmed | SSGA 2026 Dividend Distribution Schedule (SPD003792) |
| GDX | 2026-12-21 | **PROJECTED** + tripwire | VanEck annual Dec payer (2025 actual ex 12/22); refresh from VanEck Dec 2026 release |
| IWM | 3/17, 6/15, **9/15**, **12/15**, 12/30 (excise) | all confirmed | iShares 2026-2028 Fund Distributions Schedule (GPS0826-5839861) |
| FXI | 2026-12-15, 2026-12-30 (excise) | confirmed | iShares schedule, footnote (h) |
| IBIT | none (non-payer) | guard:false | see §4 |

XLE's quarterly ~Sep 21 ex-div sits INSIDE the 30-45 DTE window — its guard is
live from day 1. SPY/QQQ/TLT/GLD/USO data unchanged; EWZ stays
structured-empty (boot-gated if ever enabled+guarded).

## 9. Added gate + fallback ladder

Live credit-floor reconfirmation: each active symbol must clear its
0.30×width floor on LIVE intraday quotes (morning shadow-eval) before 15:45
or it does not go active. Fallback ladder: full 3 → partial actives →
code-only at a proven ≤2 subset. Watch item: XLE's 68/69 strike-gap could
no_wing a w2 build at some spots (noted in the yaml comment) — the w1
fallback + shadow-eval cover it.

## 10. Deploy discipline

ONE deploy, one restart, operator-run via paste-runners (command-paste-rule),
complete ≤13:00 ET, never 15:40-15:58 ET. Drift-gate vs prod-live tip
`b11af9b`; prod-live advanced same session; deploy_log entry (draft:
`research/mace_oq2/DEPLOY_LOG_DRAFT.md`) includes this memo + ex-div
citations. Kill-switches unchanged (auto_execute:false hot, standby:true hot,
--live-divisions removal + restart) plus the new UI halt button. GLD = 0 open
rungs confirmed at boot verify; SPY's 2 open W33 rungs verified managed
through deploy + restart.

## 11. Verification status at memo time

Targeted MACE suite: **202 tests green across 14 files** (config,
strategy_entry, strategy_manage, execution, overflow_dup_entry,
manager_window, halt_button, loops, web_wiring, risk_adapter,
risk_chokepoint, breakers, exdiv, ex_dividend_calendar) — includes the full
Board-mandated matrix (window-overflow, mid-flight ladder failure, cancel-404
under deadline, risk-reject on symbol 2/3, dup-entry regression at 3 symbols,
reserve binding mid-eval, fill-at-cutoff, halt-mid-eval all sub-cases,
N≤2 fallback equivalence). Full-suite baseline verification (88f/12e held,
0 new MACE failures) = Phase 4, Checkpoint 4 report to follow.
