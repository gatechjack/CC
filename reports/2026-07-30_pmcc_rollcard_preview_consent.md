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
| F | Telegram `/pair` inline "✅ Approve & Execute" | `telegram_commands.py` | **RETIRED 2026-07-30 (FORK 1)** — button removed + handler neutralized (no dispatch). No estimate wired because Telegram is notification-only; execution is dashboard-only. |

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
- Surface B dispatch does not re-check earnings at decide-time (gate ran at scan build).
- `routes.py` `_pair_cache` write on the PMCC force path is vestigial — non-force PMCC
  GETs return the record panel before the cache read; never served back.

## Follow-ups resolved 2026-07-30 (FORK 1 + FORK 2)

Both forks flagged at the end of the first pass are now closed on this same held branch.

**FORK 1 — Telegram `/pair` execute keyboard retired (`telegram_commands.py`).**
The `/pair` render's actionable row previously offered "✅ Approve & Execute" (callback
`approve:SYM`), and the `execute_pair` handler was a FULL dispatch path — it called
`propose_orders_for_pair` and `data_exec.place()` **per leg** (also the only non-atomic
Telegram order path). Per CLAUDE.md (Telegram = notification-only), the Approve button
is removed from the keyboard and `execute_pair` is neutralized to a dashboard-redirect
stub with **no order path** (no build, no risk, no `place`), so even a stale `approve:`
button from an old chat degrades to a redirect instead of dispatching. `data_exec.place`
now appears **nowhere** in `telegram_commands.py`. The informational message + the
non-dispatch Defer control stay; execution is dashboard-only.

**FORK 2 — dispatch display synthesized from the stash; redundant LLM call dropped
(`routes.py`, `pmcc_preview.py`).** On a stash HIT, `execute_pair_orders` no longer calls
`analyze_symbol` (the LLM). `load_preview` now returns a `PreviewHit(orders, action)`;
the dispatch view is synthesized from the stashed `action` (`_synth_analysis_from_stash`),
so the Approve click carries no LLM latency and the post-execute view can't contradict the
approved rec. The dispatch-time earnings re-check (blocked → ABORTED bail) and the
fingerprint consent match are UNCHANGED, and a stash MISS still rebuilds via the LLM +
fingerprint-bails on a drifted contract. `regime` stays a cheap trend read (never was the
LLM). Display/consent + Telegram surface only — no order-path/auto_execute/halt/SQL change.

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

**FORK 1 — Telegram** (`tests/test_telegram_pair_no_execute.py`, new, 3):
- `/pair` render has NO `approve:` callback (execute button gone); Defer remains; no order build on render.
- `execute_pair` handler returns the dashboard redirect ("notification-only") and touches no order path.
- `handle_callback("approve:SYM")` degrades to the redirect — a stale button cannot dispatch.

**FORK 2 — stash-hit dispatch** (`tests/test_pmcc_execute_dispatch.py`, new, 3; via `create_app`/`TestClient`):
- stash HIT → `analyze_symbol` **not called** (assert `analyze_calls == 0`), no rebuild, the EXACT stashed legs fire (`place_combo` called with the stashed order ids), display populated from stash.
- stash HIT + earnings blocked → still no LLM, nothing placed, `pmcc_consent_earnings_block` logged (ABORTED bail).
- stash MISS (wrong fingerprint) → LLM rebuild path runs, then `pmcc_consent_fingerprint_mismatch` bail, nothing placed.
- Updated the stash unit test to the `PreviewHit(orders, action)` return shape (asserts the carried action).

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

### FORK 1 + FORK 2 regression (vs branch baseline `4c7662c`)

Broad branch run (all `test_pmcc_*` + all `test_telegram_*` + `test_approvals_routes`
+ `test_exec_alert`): everything passes EXCEPT the same two
`test_pmcc_paper_run_readiness` tests that already failed pre-fork (missing local
`agent_state`/`audit_event` tables — environmental). +6 new fork tests
(`test_telegram_pair_no_execute.py` 3, `test_pmcc_execute_dispatch.py` 3), all pass;
the one modified stash test still passes. FORK 1/2 touched only
`telegram_commands.py`, `routes.py`, `pmcc_preview.py` (display/consent + Telegram
surface). **Zero regression.**
