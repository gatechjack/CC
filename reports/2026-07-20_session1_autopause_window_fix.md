# Session 1 — Autopause window-bug fix (SAFETY) — deploy runbook

**Date:** 2026-07-20 · **Category:** correctness/safety · **Deploy:** operator-executed, SHADOW mode · **Roster:** untouched this session

## Bug (confirmed)
`_whale_autopause._query_whale_stats` aggregated each whale's round-trips over **full history** (no timestamp filter), while the operator dashboard scopes per-whale P&L to `entry_ts >= metrics_epoch`. A whale profitable pre-epoch but toxic post-epoch escaped the breaker.
- **superbeter007:** autopause saw full history **+$5.85** (> −$5 gate → not paused); operator saw forward **−$69.43** (n=79, WR 7.6%).
- Cited: `_whale_autopause.py:40-53` (query, no ts filter), `:96-101` (trigger); callers `polymarket_copy_trader.py:655`, `kalshi_copy_trader.py:668`. Operator window: `_get_metrics_epoch` `web/data.py:1281`; `_polymarket_cutoff_clause(... ts_col="entry_ts")` `:1041/4707`; `_kalshi_copy_mode_clause(... ts_col='entry_ts')` `:3852-3872`; Kalshi go-live `KALSHI_COPY_LIVE_EPOCH="2026-07-01T14:08:58+00:00"` `:3838`; Poly epoch `agent_state(polymarket_copy_trader,metrics_epoch)=2026-07-07T20:00`.

## Fix (surgical, 4 files + 1 test)
- `_whale_autopause.py`: `_query_whale_stats`/`should_autopause` gain `since_ts`; when set, add `AND entry_ts >= ?` (bound param). New `resolve_epoch(conn, agent, default)` reads `agent_state(<agent>,'metrics_epoch')` (ISO-validated, mirrors `_get_metrics_epoch`). Mirror constant `KALSHI_COPY_LIVE_EPOCH` (keep in sync with `web/data.py:3838`).
- `kalshi_copy_trader.py` / `polymarket_copy_trader.py` `_apply_autopause_filter`: compute `since = resolve_epoch(conn, self.name[, default=KALSHI_COPY_LIVE_EPOCH])`, pass `since_ts`; add `since_ts` to audit payloads. **Shadow mode** (`autopause_mode: shadow`, hot-reload): trips emit `*_whale_would_auto_pause` and are KEPT (no roster mutation); `active` restores real pause behavior.
- `config/strategies.yaml`: `autopause_mode: shadow` on both copy-trader blocks.
- `tests/test_whale_autopause_epoch.py`: superbeter007-shape (full +, forward −) trips WITH epoch / not WITHOUT; backward-compat full-history loser still trips; pre-epoch-only loser + small forward sample do not trip; `resolve_epoch` read/default/garbage. **5/5 pass; 196 related tests pass; no regressions.**

## Predicted shadow first-fire (pre-computed, read-only)
Among **currently-selected** whales, epoch-scoped gate trips exactly:
- **Polymarket: superbeter007** (n=79, WR 7.6%, −$69.43, since=2026-07-07T20:00). Civic-Static (−$7.33) does NOT trip (WR 48.4% ≥ 40). digitalnomad85 already removed 07-08.
- **Kalshi: none** (MaggieTheEagle n=3, AI.EDGE n=10, both < 30).

## Drift-gate (verified 2026-07-20; re-run at deploy time)
| File | BASE (prod) | PATCHED (target) |
|---|---|---|
| `_whale_autopause.py` | 07756b54f68c991af2cc4036a7fe2ef2 | 18cf868f1696779e437284180e3358a2 |
| `kalshi_copy_trader.py` | b2a2d1f1a2e432c30c2d1cba55b4918c | 720df3d8c5cadef044176566a09db3b9 |
| `polymarket_copy_trader.py` | 2f92049a57335337824e238c70e8c82d | 49d3a5d01280e02d7761bd66957f7eec |
| `config/strategies.yaml` | 4a42618e2131d2a9f3965d5e76e87980 | 48d2e30c19553e846e6eaad0413245eb |

Prod files are `azureuser`-owned + writable (no sudo for swap); restart needs `sudo -n systemctl`. Local files LF.

## Deploy sequence (operator; each is one non-wrapping runner from `cc`)
1. `powershell -ep bypass -f .\s1_driftgate.ps1`  — expect 4x PASS.
2. `powershell -ep bypass -f .\s1_deploy.ps1`  — upload+verify+backup+swap (aborts before swap on any md5 mismatch). NO restart. Backups: `~/trading_corp/.bak_autopause_epoch_20260720`.
3. `powershell -ep bypass -f .\s1_restart.ps1`  — run in a post-PMCC quiet window; flat-guard + restart + boot-smoke.
4. `powershell -ep bypass -f .\s1_verify.ps1`  — a few min later: full would_auto_pause list (both divisions), confirm NO real auto_paused, roster intact (14 poly whales, superbeter007 present).
Rollback: `powershell -ep bypass -f .\s1_rollback.ps1`.

## Verification acceptance criteria
- Engine active, NRestarts clean, no new tracebacks.
- `polymarket_whale_would_auto_pause` for superbeter007 with n≈79 / WR≈7.6 / pnl≈−69 / since=2026-07-07T20:00.
- ZERO `*_whale_auto_paused` since deploy; poly `selected_whales` still 14 incl superbeter007.

## Session-2 flip (no redeploy)
Edit `config/strategies.yaml` on prod: `autopause_mode: active` for the chosen division(s) (hot-reload). Do this alongside the copyability-metric fix + roster decisions.

## Findings surfaced (NOT fixed this session)
- **Kalshi autopause is a no-op on LIVE rows:** live `kalshi_round_trips` carry NO `whale_handle` in `extra_json` (post-go-live 13/13 absent; paper 3057/3674 present). The query keys on `whale_handle`, so it matches zero live rows. The window fix is correct + future-proofs but Kalshi won't pause anyone until the live round-trip recorder restores `whale_handle` (separate resolver change). *(Prioritize alongside Session 2 copyability fix — same recorder/instrumentation area.)*
- **Kalshi per-whale dashboard panel is itself full-history** (`_query_pm_whales` Kalshi block) while its aggregate tile is epoch-scoped → operator's per-whale Kalshi view and the tile disagree. Consider epoch-scoping the panel so operator+guard+tile all agree.
- **No operator-facing feed-health panel exists**; the feed-breaker counter is in-process and resets on restart (a multi-day outage spanning a restart never crosses the ≥3 threshold). Other guards (mass-exit, residual/leg_priced, drift, cold-start) are cycle-local/per-row — no window mismatch.

## Guardrails honored
No roster change (shadow). Ultra-short filter untouched. Checkpointer/shared-DB untouched. Files staged locally, uncommitted (prod is not git). Deploy execution left to operator.
