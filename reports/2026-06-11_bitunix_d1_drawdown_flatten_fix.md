# Bitunix D1/D2 — Account-Drawdown Auto-Flatten Fix (BUILD + TEST)

**Date:** 2026-06-11 · **Branch:** `bitunix-d1-drawdown-flatten-fix-2026-06-11` (off `origin/main` `b1e4150`; unmerged)
**Scope:** §4 strategy-safety change. **BUILD + TEST only — no deploy, no merge** (operator-gated, separate step).
**Prereq for:** autonomous-live HITL removal (see `reports/2026-06-11_bitunix_hitl_removal_for_autonomous_live.md` §3/§6/§7).

> ## RESULT — the 15% account-drawdown auto-flatten now FIRES.
> A persisted account high-water-mark makes `drawdown_pct()` real (was always 0), and the
> `flatten_account` verdict now dispatches `flatten_division` on **BOTH** risk-eval paths (D2).
> Proven by a forced-15%-drawdown unit test + path-level dispatch tests on both paths, with a
> regression-proof that the score-path test fails on pre-fix code. **Zero new test regressions.**

---

## 1. Premise confirmed against live source (not just the prior report)

- **D1 — `drawdown_pct()` is correct; its input was poisoned.** `models.py:352-355` computes
  `(peak − equity)/peak`. Both observer risk-eval call sites built `AccountState` with
  `peak_equity = current account_equity` (score path `_score_and_maybe_propose_locked:1518`,
  Phase-3.1 path `_maybe_propose:3247`), forcing the numerator to 0. `account_equity` is sourced
  fresh each eval from `broker.snapshot().equity`. → drawdown always 0 → `flatten_account`
  (`risk.py:177-182`, fires at `>= 0.15`) never produced.
- **D2 — score path never dispatched the flatten.** Phase-3.1 path called
  `_maybe_flatten_on_risk_verdict(verdict_risk)` at `:3265` before its reject handling. The score
  path went straight from `evaluate()` to the reject branch with **no dispatch** — a `flatten_account`
  verdict there was logged as a plain reject and the account never flattened.

## 2. Change set (all on-branch, unmerged)

**`trading_corp/agents/divisions/bitunix_futures_observer.py`** (the fix):
- New constant `PEAK_EQUITY_AGENT_STATE_KEY = "account_peak_equity"`.
- New `_tracked_peak_equity(current_equity)` — reads the persisted peak from `agent_state`
  (`("bitunix_futures", "account_peak_equity") = {"peak": float}`), ratchets it up only, persists a
  new high, returns the post-ratchet peak. **Restart-safe** (survives re-instantiation).
  **Fail-safe:** any read error → returns `current_equity` (== pre-fix behavior, drawdown 0) so a
  persistence hiccup can never manufacture a *false* flatten; a write error logs but still returns
  the real peak so the breaker is correct for that eval.
- Both call sites now pass `peak_equity=self._tracked_peak_equity(account_equity)`.
- **D2:** added `await self._maybe_flatten_on_risk_verdict(risk_verdict)` to the score path after the
  risk-eval try/except, before the reject check — mirroring the Phase-3.1 path. No-op when
  `flatten_account` is False, so normal rejects are unaffected.

**Persistence home:** `agent_state` (operator-confirmed over `StrategyState`). It is account-level
(single `bitunix_futures` division), restart-safe, and mirrors the existing `live_orders_placed`
counter pattern — avoiding the per-strategy double-counting the prior report flagged.

No change to `models.py`, `risk.py`, or `data_exec.py` — already correct.

## 3. Test evidence (the load-bearing part)

**New file `tests/test_bitunix_drawdown_flatten.py` (8 tests):**
- `_tracked_peak_equity`: first-eval initializes peak to current; ratchets up only (100→120→110→130
  ⇒ 100,120,120,130 — a dip never lowers it); **survives restart** (fresh instance, same DB, peak
  not reset to current); read-failure **fails safe** to current.
- **D1 end-to-end (§7 criterion):** peak 100k then drop to 85k ⇒ `drawdown_pct() == 0.15` (real, not 0)
  AND the real `RiskAgent` returns `flatten_account=True`.
- **Boundary:** 14.5% does NOT flatten; exactly 15.0% and 16.0% DO (fed through the tracked peak).

**D2 dispatch on BOTH paths** (added next to existing harnesses):
- `test_bitunix_observer_pa_redeem.py` (+2): score path with a `flatten_account` verdict →
  `flatten_division("bitunix_futures")` awaited once; control — a normal reject → NOT awaited.
- `test_bitunix_futures_observer.py` (+1): Phase-3.1 path (`observe_and_decide`) flatten verdict →
  `flatten_division` awaited once (pins the pre-existing dispatch stays covered).

**Regression-proof:** with the score-path dispatch line temporarily disabled, the score-path flatten
test FAILS ("flatten_division awaited 0 times") while the control passes — confirming it is a genuine
guard, not a tautology. Restored after.

## 4. Disclosure — fixture-realism fix required by the D2 change (anomaly surfaced)

Adding the score-path flatten dispatch broke **6 pre-existing `pa_redeem` tests** at first: their
score-path fixture used a bare `MagicMock` risk verdict whose `.flatten_account` is a *truthy*
auto-child, and the fixture's `data_exec.flatten_division` was a non-awaitable `MagicMock`. Pre-fix the
score path never dispatched flatten so this was latent; the correct fix now reaches
`await flatten_division(...)` → `TypeError`. Per CLAUDE.md ("branch tests must cover existing
fixtures"), the fix was to make the **mock realistic** (a normal reject has `flatten_account=False`)
plus an awaitable `flatten_division` matching the Phase-3.1 fixture — **not** to contort production
code for an unrealistic mock. All 6 green after the one-fixture update.

## 5. No-regression gate

Environment here is Windows + **Python 3.14**, which has a large pre-existing failure set unrelated to
this work (robinhood/tasty/webhooks/iron-condor + 3 collection errors in untouched
`scripts`/`bitunix_confluence_gate` modules) — so the prod deploy_log "360 passing / 5 PMCC" baseline
does not transfer. Instead the full suite was run on **this branch vs. the pristine base `b1e4150` in
the same environment** (`git stash` + `--continue-on-collection-errors`):

- Branch failures: **31** · Pristine-base failures: **32** · Branch set is a strict **subset** of base.
- The lone delta (`test_position_state_sanity_poll::test_loop_runs_multiple_ticks_under_normal_state`)
  fails in isolation on the branch too — an order-dependent **flaky** pre-existing test
  (`get_pending_positions` AsyncMock assertion), unrelated to this change.
- **Conclusion: zero failures are unique to the branch → zero new regressions.** All bitunix
  observer/risk/drawdown tests pass.

## 6. Hard stops honored / out of scope

No prod write, no deploy, no merge. The deploy of this is a **separate operator-gated step**
(single-file deploy + restart, like B1). Phase A found the mechanism exactly as the prior report
stated — no STOP-and-surface needed. Out of scope (separate items): B1 real-fill validation, HITL
removal (gated behind this + B1), the kill surface (D3), rate cap (D4), non-interactive `--live` (§5).

## 7. Operator-gated next steps
1. Review this branch.
2. Deploy: `bitunix_futures_observer.py` is the only source file changed → single-file deploy +
   process restart (the new `agent_state` peak row self-initializes on first post-deploy eval; the
   first eval sets peak = current, so no false flatten on rollout).
3. Then B1 real-fill validation; HITL removal remains gated behind both.
