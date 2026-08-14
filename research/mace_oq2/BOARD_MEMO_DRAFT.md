# DRAFT — Board Memo: MACE 3-Active Expansion + OQ-2 + Entry-Halt Button

Draft for Phase 3 finalization (final memo lands in planning/ with the deploy_log
entry once the roster is picked). Records the 2026-08-13 Board rulings.

## Decisions recorded

1. **Timeline override (Board, 2026-08-13):** the GO/NO-GO soft-NO is overridden
   — build tonight, go 3-symbol live at the 2026-08-14 15:45 ET eval, ATTENDED.
   The override is on the TIMELINE ONLY; every safety gate is reaffirmed
   ("I'm overriding the timeline, not the safety tests").

2. **OQ-3 REVERSAL:** IBIT is promoted from overflow-only receiver to primary
   universe candidate (`overflow_only` removed, `enabled: true`). Rationale:
   the 3-active expansion needs a third non-correlated underlying; the OQ-3
   overflow-only ruling predated OQ-2 serialization, which now guarantees IBIT
   a bounded, audited window slot. IBIT IVR was 9.1 at stage-A — a day-1 skip
   on the IVR>=25 floor is coherent behavior, not a defect.

3. **Universe:** defined set SPY/IWM/GDX/IBIT/XLE + legacy GLD/TLT/USO/EWZ/FXI
   all present; target actives {IBIT, XLE, GDX}; backfill ladder FXI -> IWM;
   SPY + GLD -> enabled:false (SPY's 2 open W33 rungs remain fully managed —
   manage/exit/reconcile never read `enabled`). Final roster = Board pick at
   the deploy checkpoint after the live credit-floor shadow-eval.

4. **Strategy parameters (Board-ruled):**
   - rung_risk_pct 0.055 -> **0.10**
   - deployment_target_pct 0.80 -> **0.95**
   - risk_band_max_usd 250 -> **260**
   - weekly_new_rungs_per_symbol 2 -> **1**
   - entry_max_attempts 5 -> **2**, entry_fill_wait_sec 60 -> **30**
     (~70-80s/symbol typical; 3-symbol worst ~6.5 min vs 13-min window)
   - max_rungs_per_symbol 5, max_contracts 1 unchanged.

5. **OQ-2 serialization (code, commits ee9cfd5 + 66cad59):** prioritized
   sequential — IVR-desc primaries then overflow; per-symbol dynamic deadline
   now + (cutoff-now)/symbols_remaining (early finishers donate forward);
   audited mace_entry_window_skip (never silent starvation); window_budget
   clean stand-down; precedence cutoff > operator_halt > window_budget;
   strictly ONE ladder in flight (concurrency rejected — it reintroduces the
   08-12 dup-entry bug class).

6. **Entry-halt button (Board addition, commits 7300985 + 3210a4a):** /mace
   gets the ONE write surface — an entry-halt latch (agent_state
   robinhood_mace/entry_halt) with auto_execute:false semantics. Halts the
   NEXT symbol/attempt (honest latency stated in the UI); a resting order
   completes its fill-or-cancel cycle; open-position management unaffected.
   Audit-before-state (mace_ui_halt/mace_ui_arm, actor mace_operations).
   Fail-safe: latch-read error == NOT halted (entries proceed under the
   existing kill-switches; the button is an ADDITIONAL brake, never a gate
   that can wedge entries off on a DB hiccup).

7. **Ex-dividend policy holds:** never a dividend-payer with guessed dates or
   guard off. XLE/IWM/FXI issuer-confirmed (SSGA SPD003792; iShares
   GPS0826-5839861). IWM's two shipped projections were WRONG (9/21 -> 9/15,
   12/21 -> 12/15) — corrected from the issuer schedule. GDX 2026 date is
   structurally unannounced (VanEck publishes in December); ships PROJECTED
   2026-12-21 guard-ON with a December refresh tripwire — Board to ratify at
   Checkpoint 0 that this satisfies the rule for a date that cannot exist yet.
   Evidence: research/mace_oq2/EXDIV_EVIDENCE.md + 3 issuer PDFs alongside.

8. **Added gate:** live credit-floor reconfirmation — each active symbol must
   clear its 0.30 x width floor on LIVE intraday quotes (morning shadow-eval)
   before 15:45 or it does not go active. Fallback ladder: full 3 -> partial
   actives -> code-only at a proven <=2 subset.

## Deploy discipline

ONE deploy, one restart, operator-run via paste-runners, complete <=13:00 ET,
never 15:40-15:58. Drift-gate vs prod-live tip b11af9b; prod-live advanced same
session; deploy_log entry includes this memo + ex-div citations. Kill-switches
unchanged (auto_execute:false hot, standby:true hot, --live-divisions removal)
plus the new UI halt button.
