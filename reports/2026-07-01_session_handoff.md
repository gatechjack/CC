# Session Handoff — 2026-07-01 — Kill-paper deployed + Regime-aware SFP research lead

## Baseline (start next session here)
- **Prod engine LIVE + healthy** — PID 34501 (booted 03:42 UTC after RH-pickle recovery), NRestarts=0,
  0 tracebacks. Bitunix SFP (BTC+ETH), futures, PEAD, Kalshi, Polymarket all wired.
- **git:** `main == origin/main == a73044e` (a concurrent session's Kalshi V2 + dashboard work; NOT this
  session's). **This session's main-destined changes are on branch `session-wrap-2026-07-01`** (off `a73044e`;
  = kill-paper config + deploy pkg + these doc updates), pushed to origin, **UNMERGED to main** (deferred — see
  below). cc working checkout is on `tooling-run-capped-python-alias-fix-2026-06-21` (its own branch — fine).
- **★ Prod strategies.yaml (`1ec7832b`) is AHEAD of `main` (`740d1a02` base)** until `session-wrap-2026-07-01`
  merges to main. **Do NOT deploy `main`→prod config before that merge, or the SOL/XRP kill reverts.**

## What shipped this session
1. **Kill-paper — DEPLOYED + VERIFIED LIVE 2026-07-01.** `bitunix_sfp` dropped SOL+XRP from `symbols` AND
   `symbol_modes` (BTC/ETH stay live); stuck SOL paper row `e450302a` expired. prod `740d1a02→1ec7832b`.
   ★ Corrected the handoff's "symbol_modes only" — that would've flipped SOL/XRP to LIVE (`mode_for()` defaults
   absent→`arm:trading`); had to drop from `symbols` too. deploy_log 2026-07-01; pkg
   `deploy/2026-06-30_kill_paper_sol_xrp/`.
2. **⚠ RH-pickle outage (agent-caused, ~20 min) + recovery.** The kill-paper restart hung the whole engine on
   an expired RH pickle (interactive 2FA challenge blocks boot). Operator refreshed the pickle
   (`rh_pickle_refresh.ps1`), restart booted clean. Lesson: [[prod-restart-rh-pickle-hazard]] — verify pickle
   freshness before ANY full-unit restart. Exposure was LOW (0 engine-managed positions).
3. **★ Regime-aware SFP — the research lead (the session's main output).** See below.

## THE LEAD — Regime-aware SFP (for the next build, under external-agent review now)
Full self-contained brief: `Desktop/bitunix_reports/revised_sfp_strategy_lead_2026-07-01.md`. Harness (4
spikes, reproducible): branch `sfp-regime-research-2026-07-01` (`42e4869`, pushed), `spike_pivot_degree/`
(reuses the real `SfpModeBDetector`; data re-dumped from prod `bitunix_bar_history` each run).

- **Current live long-only SFP is the weak baseline** — negative in-regime (bear), near-inert (pivot-50 fires
  ~5×/46d).
- **Spike results (46d, 4 coins, 3m→15m):** (1) pivot-degree = data-thin, null-fail; (2) longs' 2:1 target is
  the worst end (nearer target cuts bleed but never positive); (3) SHORT SFPs flip positive in the bear (but
  that's largely bear-beta); (4) **REGIME conditioning is the lead — trend-aligned +0.29R vs counter-trend ~0;
  longs bleed ONLY in downtrends (−0.32R), breakeven in up/range.** Robust across 3 regime formulas.
- **Proposed build:** regime classifier (candidate 15m EMA-200 + slope: UP/DOWN/RANGE) selects the side —
  long SFP in up/range, short SFP in down/range, never counter-trend.
- **Honest gate:** 46d = ONE regime (bear); short leg is partly bear-beta (untested in a real bull); regime
  result not yet null/OOS-hardened; no fees/slippage. Validate across regimes via the operator's WEEKLY review;
  first regime-flip = make-or-break. SOL tuning is subsumed (same knobs).

## Open items (next session)
1. **★ Merge `session-wrap-2026-07-01` → main + push origin** once `cc-merge-wt` releases `main`. Restores
   `main == prod` parity (currently prod is ahead on the kill-paper). Command: from a worktree on main,
   `git merge --no-ff session-wrap-2026-07-01` (clean — disjoint from Kalshi). Then prune the redundant
   `kill-paper-sol-xrp-2026-06-30` branch.
2. **Futures division halted on 1 orphan** — one open position on the futures account (manual? or stale/phantom
   from a futures auto-trade). Futures stays halted until it reconciles. Determine what it is.
3. **Regime-aware SFP build** — after the external-agent review, next build step per the brief's open questions
   (regime signal choice, RANGE handling, target scaling, sizing, whipsaw/hysteresis).
4. Cosmetic: stale `bitunix_sfp` "conservative"/0.0025 sizing comments (live is 0.10/0.20/lev25).

## Agent/operator boundary this session
Agent drove read-only diagnostics AND (operator-authorized) the kill-paper apply + restart over SSH. Operator
ran the interactive RH pickle refresh (2FA). A concurrent session held `main` throughout (Kalshi V2).
