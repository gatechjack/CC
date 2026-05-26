# Next-session prompt — post-analyze-whale + pre-Sunday-fire pickup

Resuming after the 2026-05-26 ~03:45 UTC session that closed out the polymarket watchlist clustering bug (2-phase fix), shipped the analyze-whale CLI + dashboard endpoint, and strengthened the deploy-import-graph-audit memory from awareness to a mechanical checklist gate.

## State on prod

**`origin/main` head:** `7ffc20b`. All session commits ff-merged to main and pushed. Working tree clean modulo parallel-session `docs/Deployment notes.txt` (operator-owned untracked).

**Latent on prod (exercises Sunday):**
- Clustering fix: `_select_resolved_buys_window` dedupes by `(cid, outcome_index)`. Deployed 2026-05-26 22:20 UTC.
- PnL aggregation fix: `_aggregate_window_to_decisions` sums fills per decision; closes the value-axis loop. Deployed 2026-05-26 01:42 UTC.
- Both fixes live in `trading_corp/scripts/seed_polymarket_watchlist_deep.py` (post-deploy md5 `906435c92c498f4bc54d4c9b88d74aa9`).

**Live on prod (exercises now):**
- Analyze-Whale dashboard endpoint at `POST /api/polymarket/watchlist/analyze/{wallet}` — Magamyman smoke verified HTTP 200 in 4.3s at $0.0015. Operator can click "Analyze" on any watchlist row immediately.
- Analyze-Whale CLI at `python -m trading_corp.scripts.analyze_polymarket_whale <wallet|user_name>` — works on prod (modules deployed there) AND locally.
- Phase A modules (compute core + Haiku narrator + namespace-isolated cache) on prod.
- Haiku 4.5 wired into `config/agents.yaml` + `cost.py` _PRICING table.
- Final `trading-corp.service` PID 1462117, ActiveEnter 2026-05-26 03:30:19 UTC.

**Operator-state on prod:**
- `agent_state(polymarket_copy_trader, watch_only_whales)`: 53 rows from the 2026-05-26 00:44 UTC manual fire under clustering-fix-only code. This is STALE — Sunday's overwrite produces the corrected list.
- `selected_whales` / `pinned_whales`: 13/16 wallets, last touched 02:01 UTC (parallel-session promotions, NOT from this work).
- `metrics_epoch`: 2026-05-23T15:30:15 (unchanged).
- Promotion PAUSED across all windowed columns until the Sunday verification gate passes.

## The Sunday verification gate

**Sun 2026-05-31 ~13:00 UTC** — first weekly seed fire under BOTH fixes. Verification criteria (per `reports/2026-05-26_polymarket_clustering_fix_plan.md` + `reports/2026-05-26_polymarket_pnl_aggregation_fix_plan.md`):

1. **Roster size in 97-172 band** (expected ~136 per replay against cached 329-wallet corpus).
2. **No 100% WR rows.**
3. **`window_size_n` column reflects distinct decisions, not fill counts** (Magamyman would show n≈98 not n=100).
4. **Provisional flag fires on n<50 rows.**
5. **Clean exit + wall-clock in 20-35 min band.**
6. **Low fetch-error noise** (a handful of "max historical activity offset of 3000 exceeded" is normal for deep-history whales; not a blocker).

**If all 6 pass: promotion unpauses NORMALLY.** No SELL-footprint forensics gate. The held-vs-realized PnL caveat is a per-whale review note the operator inspects with the new Analyze button, NOT a hard pre-promote check.

**If outside band:** STOP and investigate before promoting. Don't read the slot.

## If Sunday hasn't fired yet (most likely state on session start)

The Sunday fire is the load-bearing next event. Options if the session starts before then:

- **Use the Analyze button on the existing 53-row watchlist** to review specific candidates' true decision-level edge under REDEEM-grounded math. The cohort is broken (under-counted PnL on cluster-heavy whales), but the per-whale audit is correct on whoever happens to be in the slot.
- **Work other backlog items.** Highest-priority open work (from BACKLOG):
  - **C-1 secret rotation** (P0 CRITICAL, blocked on C-7). 13 distinct credential rotations.
  - **C-7 rejected-webhook audit plaintext leak** (P0 CRITICAL prerequisite to C-1).
  - **Tastytrade rotation runbook** (P1, untouched).
  - **43 deferred package bumps** from C-6 lockfile drift (P1).
  - **kalshi_weather forecast quality follow-ups** (parallel-session work; check the most recent EOS snapshot).
  - **Architecture follow-up:** `trading-corp.service` single-process tax (P3, filed this session — would let UI deploys skip the strategy-pause).
- **Don't touch:** anything that re-deploys the watchlist seed before Sunday's fire (would invalidate the verification window). Don't re-tune the floors (`min_resolved_buys`, `provisional_threshold`, `min_windowed_wr`, `min_windowed_pnl`) — the plan ratified them as-is until decision-counted real data confirms.

## Hard constraints (carry forward)

- **Promotion stays PAUSED** until the Sunday gate passes. Don't promote off the current 53-row slot.
- **Don't re-deploy the seed** before Sunday's natural fire.
- **CLAUDE.md § 4 applies** to anything touching seed_polymarket_watchlist_deep.py, agents/risk.py, broker adapters, agent_state schema changes, runbook edits.
- **`[[deploy-import-graph-audit]]` is now a mechanical checklist gate, NOT awareness.** Before any prod file transfer, resolve the transitive import closure + ls-check each on prod. Operator-local CLI modules are committed to main but NOT on prod's disk by default.
- **Capped Python via `scripts\run_capped.ps1` for anything touching trading_corp/ or tests/.**

## Files to read first if you need full context

1. `BACKLOG.md` top EOS snapshot (2026-05-26 ~03:45 UTC) — full session arc.
2. `runbooks/deploy_log.md` 2026-05-26 entries (22:20 UTC, 01:42 UTC, 03:30 UTC) — what's on prod and rollback recipes.
3. `reports/2026-05-26_polymarket_clustering_fix_plan.md` — Option A rationale.
4. `reports/2026-05-26_polymarket_pnl_aggregation_fix_plan.md` — REDEEM-grounded math identity proof.
5. `~/.claude/plans/memoized-scribbling-glacier.md` — analyze-whale design doc.
6. Memory: `[[pm-watchlist-windowed-live]]` + `[[analyze-whale-shipped]]` + `[[polymarket-whale-scoring-edge]]` + `[[deploy-import-graph-audit]]`.

## Pickup default if no operator direction

If session starts post-Sunday-fire (after 2026-05-31 ~13:00 UTC): immediately run the 6-criterion verification gate against the new `watch_only_whales` slot. Report PASS/FAIL per criterion. If PASS, surface and wait for the operator to lift the promotion pause. If FAIL, STOP and investigate.

If session starts pre-Sunday: await operator direction or pick from the backlog above.
