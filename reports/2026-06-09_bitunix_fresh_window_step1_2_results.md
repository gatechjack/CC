# Bitunix fresh paper window — Step 1 + Step 2 results (post vol-classifier fix `7834375`)

**Window start:** 2026-06-09 03:49:41 UTC. **Probes run at:** 2026-06-09 ~11:09 UTC (elapsed ≈ **7.3h**).
**Mode:** PAPER, read-only prod, operator-run SSH. No code change. This is the read-only activation + health check; **not** a flip decision.

## Step 1 — activation (F-5 watch-item): **PASS** on every criterion
- **Classifier fixed:** all 9 `htf_gate_decision` rows = `vol_tier=high` at ATR 3.98–4.03%. Pre-fix these were tagged `extreme` and size-zeroed.
- **Decisive check (S1b):** `still_extreme_under5 = 0` — zero rows in [3,5) still tagged extreme or vol-hard-zeroed. Fix is live on prod.
- **Firing resumed (S1c):** `trade_plan_decision`=5, `would_have_placed`=3, both first at 04:54/04:57Z — first fires since the bug silenced it.
- **Pre-window anchor (S1d):** last fire before window = `2026-06-02T22:15:02Z`, exactly the expected dormancy boundary.
- The four `size_mult=0.0` gate rows (07:34, 09:18, 09:21, 11:06) are **legitimate gate zeros** — `proximity_to_support` / `regime_forbids_side`, not `vol_tier_extreme`. Gate working, not the bug.

## Step 2 — window metrics: **clean across the board**
- **S2a fire rate:** 3 paper fires in the first ~7.3h ⇒ extrapolates to ~10/day, consistent with the pre-bug 06-02 **9× anchor**. Healthy, not a flood, not dormant.
- **S2b outcomes:** 3/3 **win**, avg R **0.644**, sum PnL **+$0.30**. Positive, no losses — but **n=3 says nothing about edge**; it only confirms the path resolves trades and produces wins. Sub-1R avg R = TP exits landing under 1R.
- **S2c classifier sanity:** 9 rows all `high`, ATR 3.98–4.03; **band_violations = 0**.
- **S2d hard-stop (critical):** **0** Phase-3 live-mode primitives firing in paper (no `live_exit_order_*`, `position_state_*`, `restart_resume_*`, `exit_outcome_recorded`, `orphan_broker_position_on_restart`). Paper isolation intact.
- **S2e:** **0** `agent_error` since window start.
- **S2f:** **0** reconciler-mismatch / divergence.

## Consistency cross-check
`would_have_placed`=3 (S1c) = paper fires=3 (S2a) = wins=3 (S2b) — internally consistent.
The `trade_plan_decision`=5 → 3-fire gap = 2 plans that didn't convert (dedup / cooldown / open-position). Benign, and corroborated: every fire that converted resolved as a win with zero errors.

## Live-readiness read
- **Fix:** confirmed working on prod. ✓
- **Window health:** clean — no anomalies, no live-primitive leakage, classifier banding correct. ✓
- **Flip-readiness:** **NO.** Only ~7.3h elapsed, n=3. The window is *behaving correctly*, but it carries no edge evidence yet.

## Decision gates BEFORE any paper→live flip (none satisfied)
1. **Duration** — prior practice = 7 days; operator sets length. A 7-day window closes **~2026-06-16 03:49Z**. Currently ~7.3h in. **Open — operator decides.**
2. §4 Backtester approval gate (skipped for the wiring fix; a live-flip is a higher bar). Unmet.
3. deploy_log 2026-06-02 `execution_mode` flip checklist (reconcile-state review, Path-C dry-run shape, etc.). Not done.
4. **P2 Robinhood re-login** — orthogonal, but any future restart/deploy re-hits the ~22-min device-approval hang until the pickle is regenerated. Still OPEN.

## Open fork for operator
- **Window duration is not set.** Recommend ≥7 days (matches the invalidated window's intended length). Operator decides the close date. Until then: continue read-only observation; do **not** flip `execution_mode` or `auto_execute`.
