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
(to be filled with run evidence)

## Regression vs e82a07d
(to be filled — apples-to-apples: same suites, prod-venv flags)
