# PMCC roll-card: preview mode + consent integrity — 2026-07-30

Branch `claude-2026-07-30` off prod-live `e82a07d`. Display/consent + alert-gating layer,
plus ONE operator-authorized dispatch-sourcing change (consent addition, below). HELD for deploy.

## Defect 1 — spurious ABORTED on Re-analyze

**Root cause (verified against source):** the division panel's Re-analyze (`force=1`) handler
(`web/routes.py:964`) calls `PMCCAgent.build_trade_recommendation` → `propose_orders_for_pair`
(`pmcc_robinhood.py:1458`), which runs the FULL roll pipeline. Its abort gates call
`_audit_roll_abort` (`pmcc_robinhood.py:4795` audit write + `:4801-4824` `emit_exec_alert`
tier=ABORTED) unconditionally — a *render* produced a Telegram ping identical to a real
dispatch abort. The EARN_UNVERIF emit (`:3573-3588`) is the same class.

**Invariant established:** exec-alerts (FILLED / NO FILL / EXEC FAIL / ABORTED / NAKED LEG /
EARN_UNVERIF) fire ONLY on a genuine DISPATCH attempt — a real Approve→place, or an
autonomous scan intending to place. NEVER on re-analyze, card render, or estimate/preview build.

**Fix:** keyword-only `preview: bool = False` threaded
`build_trade_recommendation` → `propose_orders_for_pair` → `_propose_roll_short` /
`_propose_open_pmcc` → `_audit_roll_abort`. In preview: NO audit row, NO alert emit
(selection diags still populate so the panel can render the abort reason). EARN_UNVERIF
emit gated the same way.

**Caller census (verified):**

| Caller | preview | Why |
|---|---|---|
| `routes.py:964` Re-analyze force=1 | **True** | render only |
| `telegram_commands.py:265` `/pair` | **True** | render only |
| `routes.py:1072` execute (rebuild backstop) | False | genuine dispatch |
| `telegram_commands.py:450` approve callback | False | genuine dispatch |
| `scan()` roll paths (`:2308-2345` etc.) | False | autonomous dispatch intent |
| `scripts/pmcc_paper_run_readiness.py:311` | False (default) | unchanged, not on prod |

## Defect 2 — Approve surface lacks strike / debit / credit / net

### Surface audit (every PMCC roll "Approve & Execute")

| # | Surface | Route/renderer | Estimate after this change |
|---|---|---|---|
| A | Division page Expert Analysis panel (Re-analyze path) | `routes.py:846` force=1 → `_render_pair_analysis` | **YES** — strike+expiry, debit (ask), credit (bid), net, "est. — actual fill will differ"; earnings states wired |
| A′ | Division page stored-record panel (non-force GET) | `routes.py:4627` `_render_pmcc_record_panel` | **Approve REMOVED** (operator-directed): earnings state + "Re-analyze for live strike/debit/credit/net before approving" |
| B | `/approvals/pmcc-combos/{id}` | `approval_pmcc_combo_detail.html` | YES — baseline (2026-07-28, unchanged) |
| C | `/approvals` IC combo pages | `approval_combo_detail.html` | n/a — IC, not PMCC roll (out of scope) |
| D | Generic `/approvals/{id}` single-leg | `approval_detail.html:201` | not a PMCC roll combo surface (PMCC rolls are combo-tagged); unchanged |
| E | PMCC Scout open | `pmcc_scout.html:173` | open (not a roll); unchanged |
| F | Telegram `/pair` inline "✅ Approve & Execute" | `telegram_commands.py:265/:450` | **FORK — reported, not enriched**: CLAUDE.md pins Telegram as notification-only; routed through preview (Defect 1) so it emits no spurious alerts, but no estimate wired. Operator decision needed to either enrich or retire the keyboard. |

### Consent integrity (operator addition, 2026-07-30)

Dispatch previously re-analyzed from scratch on every Approve (the `routes.py:1060`
"uses cache if fresh" comment was STALE — `analyze_symbol` → `_llm_analyze_position`
has no cache), so the strike shown could silently differ from the strike fired.

**Fix — carry the previewed combo forward:**
- Re-analyze stashes the built `ProposedOrder`s (`web/pmcc_preview.py`, per-(slug,symbol),
  TTL 15 min, single-slot) and renders `preview_id` + combo `fingerprint`
  (sorted legs: type/strike/expiry/side/effect/ratio) as hidden fields in the Approve form.
- `execute_pair_orders` dispatches the STASHED combo when (id, fingerprint, TTL) validate —
  no re-analysis; earnings gate re-checked at dispatch (blocked → bail + re-surface +
  ABORTED audit/alert: a clicked Approve IS a genuine dispatch attempt).
- Backstop (stash miss/expired/engine restart): rebuild runs live (alerts armed); if the
  rebuilt fingerprint ≠ the fingerprint the operator approved → bail + re-surface with the
  fresh estimate, NO placement, `pmcc_consent_fingerprint_mismatch` event.
- Price may still drift — dispatch reprice (`reprice_combo_from_quotes` + existing reprice
  consent guard) unchanged; the card says "est. — actual fill will differ." STRIKE/legs
  shown == strike/legs fired.

### Residual gaps / observations (reported, out of scope)
- Telegram approve callback (surface F) still rebuilds at dispatch with no estimate shown.
- Surface B dispatch does not re-check earnings at decide-time (gate ran at scan build).
- `routes.py:973` `_pair_cache` write on the PMCC force path is vestigial — non-force PMCC
  GETs return the record panel at `:873-874` before the cache read; never served back.

## Tests

New/changed test files (run capped via `scripts\run_capped.ps1 python -m pytest`,
interpreter Python 3.14.4, deps present locally):

**Defect 1 — preview suppresses alerts/audit** (`tests/test_pmcc_logic.py`, +6, reuse
the existing roll brokers/fixtures so it's apples-to-apples):
- `test_preview_roll_short_abort_no_alert_no_audit` — preview roll_short abort → `[]`,
  no `pmcc_roll_aborted` audit, no ABORTED emit.
- `test_dispatch_roll_short_abort_still_fires` — parity: the SAME abort at dispatch
  (preview=False) DOES audit + emit exactly one ABORTED (suppression is preview-only).
- `test_preview_roll_short_earn_unverified_no_alert_no_audit` — source='none' preview
  ships 2 legs but writes no `pmcc_earnings_unverified` audit and no EARN_UNVERIF emit.
- `test_preview_roll_leap_block_no_alert_no_audit` — preview roll_leap earnings-block → `[]`, silent.
- `test_preview_roll_leap_ship_writes_no_gate_audit` — preview roll_leap ship → 4 legs, no `pmcc_roll_gates` audit.
- `test_preview_combo_identical_to_dispatch` — previewed legs == dispatched legs (same sides/strikes/expiries/effects) → the stash carries with zero drift.

**Defect 2 — division panel + stash** (`tests/test_pmcc_rollcard_preview.py`, new, 12):
- stash: fingerprint price-independent; changes with strike; hit→single-use; wrong id/fp/None miss; TTL expiry; empty→None.
- division panel render: estimate strike/expiry/debit/credit/net + "actual fill will differ" + consent token hidden fields; earnings-blocked hides Approve + shows recommendation; earnings-unverified keeps Approve + flag + shows the no-estimate reason (no estimate block).
- `test_panel_estimate_equals_dispatch_natural` — CONSENT LOCK: the net the panel prints == the natural `reprice_combo_from_quotes` derives the placed limit from (net − give_up = 0.58).
- `test_record_panel_hides_approve_and_prompts_reanalyze` — stored-record panel renders NO Approve + the "approve that exact combo" re-analyze prompt.
- `_exec_consent_mismatch_html` smoke.

Plus the pre-existing `tests/test_pmcc_roll_card.py` (15, Enhancement A/B + consent lock) still passes unchanged.

## Regression vs e82a07d (apples-to-apples)

Ran the two pre-existing PMCC test files on a detached worktree at `e82a07d` and on
the branch, same interpreter/flags:

| File | e82a07d | branch | delta |
|---|---|---|---|
| test_pmcc_logic.py | 155 | 161 | +6 (preview-suppression) |
| test_pmcc_roll_card.py | 15 | 15 | 0 (untouched) |
| test_pmcc_rollcard_preview.py | — | 12 | +12 (new) |
| **total** | **170 pass** | **188 pass** | **+18, 0 removed/modified** |

All 170 baseline tests still pass on the branch (no existing test modified or dropped).

Broader run (all `test_pmcc_*` + `test_approvals_routes` + `test_exec_alert` +
telegram): everything passes on the branch EXCEPT
`test_pmcc_paper_run_readiness.py::{all_blocking_pass_on_production_config,
formatter_and_known_limitations_block}` — which fail **byte-identically on e82a07d**
with `no such table: agent_state` / `audit_event` (the local sandbox has no
initialized DB; environmental, pre-existing, not touched by this change).

**Verdict: zero regression.** HELD for deploy — no prod push, no restart.
