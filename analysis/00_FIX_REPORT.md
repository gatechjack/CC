# MACE entry-ladder orphan fix (#1+#2+#3) — BUILT, TESTED, DEPLOY-READY (2026-10-27)

Branch `mace-entry-partialfix-2026-10-27` @ `52df733c`, worktree `cc-mace-entry-partialfix-2026-10-27-wt`,
base prod-live `411362f8`. **Base == live box verified** (CR-stripped md5, all 6 touched files identical to
the running tree before building). Autonomous build; DEPLOY + RESTART reserved for Jack. Engine/live tree/DB
untouched (all work on /tmp scratch).

★ As in the prior tasks, the task-prompt "Deployed md5s" (manager dc04eb60 / execution 9cc1c165 / config
d441957f) are the PRE-durability-fix BASE, mislabeled. The real running base I built against: robinhood
`2753939c` / rh_broker `c8aa5789` / execution `71f1a828` / config `4a47188b` (config_hash 931a8214be50).

## The three fixes (code-only; config/mace.yaml untouched)

**#1 — stop misclassifying a transient partial (`brokers/robinhood.py`, SHARED).**
Removed `"partially_filled"` from `_TERMINAL_OPTION_STATES` (line ~1733). `_await_terminal_option_order` now
polls THROUGH a transient `partially_filled` (the ms-apart multi-execution window) to the true terminal
(`filled`/rejected/...), bounded by the EXISTING `timeout_s` (MACE passes `entry_fill_wait_sec`) / the
class default `_COMBO_FILL_TIMEOUT_S`=20s. No new hang: a partial that never resolves times out exactly as a
stuck `confirmed` already does.
- **Decision — timeout:** reuse the existing per-call `timeout_s` / `_COMBO_FILL_TIMEOUT_S`. No new constant,
  no config knob. Rationale: the bound already exists and is correct; adding a knob would MOVE config_hash
  for zero benefit (a healthy multi-exec fill completes in ms).

**#2 — a PERSISTENT partial must not clean-reject (`brokers/robinhood.py` + `mace/rh_broker.py`).**
New `class RobinhoodComboPartial(RobinhoodOrderError)` carrying `order_id` + `processed_quantity`. The
`if state == "partially_filled":` branch (post-poll, line ~1493) now raises THAT instead of a bare
`RobinhoodOrderError`. `rh_broker.place_condor` catches `RobinhoodComboPartial` BEFORE `RobinhoodOrderError`
and returns `OrderResult(state=STATE_PARTIAL, processed_quantity>0)` → `run_entry._is_partial` → `_entry_partial`
(keep the anchor + URGENT), never the `MaceOrderRejected` clean-delete. The exit ladder already routes a
partial `OrderResult` to `_exit_partial` (execution.py:1160), so this also improves a persistent EXIT partial
(was: reject-continue, which risked re-placing/over-closing).
- **Decision — subclass, not a new sibling type:** `RobinhoodComboPartial(RobinhoodOrderError)` so every
  existing `except RobinhoodOrderError` caller (PMCC/PEAD via `agents/data_exec.py`) is byte-unchanged — they
  still treat it as a non-booking failure. Only the MACE adapter catches the subclass first. Confirmed safe:
  `data_exec.py` matches `RobinhoodComboPending` by exact name and routes everything else through a catch-all
  `else` that re-raises/books-nothing — identical handling for the subclass.
- **Decision — STATE_PARTIAL:** already defined and already in `TERMINAL_STATES` but not `DEAD_STATES`, so an
  `OrderResult(STATE_PARTIAL, processed>0)` is exactly `is_partial` (terminal ∧ ¬filled ∧ processed>0). No new state.

**#3 — trust RH before deleting the anchor (`mace/execution.py`, MACE-only).**
`run_entry`'s `except MaceOrderRejected` arm now calls new `_combo_live_at_broker(rung_id)` BEFORE the clean
`_entry_standdown(clean=True)`. If any WORKING open order's `ref_id` starts with `rung_id` (a `{rung_id}-aK`
attempt), it does NOT delete — keeps the anchor + URGENT breaker (reconcile adopts it via its open-orders
ref_id match). Only a confirmed-nothing-live reject deletes (the genuine BP-exhaustion / position_effect case).
- **Decision — "live OR filled" scope / NO stop-and-flag ambiguity:** I CAN cleanly distinguish a genuine
  reject from a keep-case. After #1/#2 a filled/partial order can no longer REACH the `MaceOrderRejected` arm
  (it books, or returns a partial OrderResult upstream). So the only orders that reach it are genuine rejects:
  (a) no-id (nothing placed) and (b) terminal dead (rejected/cancelled/...). Neither is live or filled, so an
  `open_orders` working-order check fully classifies them. The "filled" leg is structurally unreachable — I did
  NOT need the skipped #4 by-ref_id terminal lookup. **Fail-SAFE = keep:** if `open_orders` is unreadable we
  cannot confirm "nothing placed", so we KEEP the anchor (reconcile/abandon-horizon owns it) — never orphan.
- **Decision — genuine reject still deletes:** the 6 historical BP-exhaustion rejects have no order id →
  `open_orders` shows nothing matching → clean-delete as before (slot freed same session). Proven by test.

## config_hash — CODE-ONLY, UNCHANGED
No change to `config/mace.yaml`. config_hash STAYS **931a8214be50**. This is the CODE-ONLY graft path; the
deploy must boot-verify the hash is UNCHANGED (a MOVED hash would mean an unintended config edit → investigate).

## Files changed
- `trading_corp/brokers/robinhood.py` (SHARED) `2753939c -> 1e6bbc1e` — #1 terminal-set, #2 RobinhoodComboPartial + raise.
- `trading_corp/mace/rh_broker.py`  `c8aa5789 -> bac4411a` — #2 adapter: catch RobinhoodComboPartial -> partial OrderResult.
- `trading_corp/mace/execution.py`  `71f1a828 -> e380c2f6` — #3 `_combo_live_at_broker` + reject-arm RH confirm.
- tests: `tests/test_mace_execution.py` (+2: reject-live-keeps, reject-confirm-unreadable-failsafe),
  `tests/test_mace_rh_broker.py` (+2: persistent-partial->partial OrderResult, poll-through->filled),
  `tests/test_robinhood_multi_leg.py` (replaced the stale partial-raise test with transient->filled-books +
  persistent->RobinhoodComboPartial).

## Acceptance (box-scratch, prod-faithful, zero prod touch — scp+tar overlay on /tmp, box venv, -p no:pytest_ethereum)
1. ★ ORPHAN SEQUENCE booked, not orphaned: `test_await_terminal_polls_through_transient_partial_to_filled`
   (poll partial->filled) + `test_place_multi_leg_transient_partial_polls_through_to_filled_and_books`
   (returns fills) + `test_place_condor_credit...`/`test_entry_fills...` (FILLED OrderResult -> books rung
   status=open). PASS. Runtime assertion in-scratch: `partially_filled in _TERMINAL_OPTION_STATES == False`.
2. ★ GENUINE REJECT still deletes: `test_entry_definitive_reject_deletes_anchor_same_session` (open_orders
   empty -> clean-delete, anchor gone). PASS.
3. PERSISTENT PARTIAL kept: `test_place_condor_persistent_partial_returns_partial_not_rejected` +
   `test_place_multi_leg_persistent_partial_raises_combo_partial` + `test_entry_partial_fill_is_urgent_never_booked`
   (-> _entry_partial, anchor kept, URGENT). PASS. + #3 reject-but-live:
   `test_entry_reject_but_order_live_at_rh_keeps_anchor` + `..._failsafe`. PASS.
4. FULL MACE SUITE + cross-division (robinhood/data_exec/pmcc-combo): ~535 tests, **530 passed / 5 failed**.
   The 5 failures (`test_mace_config::test_shipped_config_loads`, `test_mace_loops::test_manage_loop_fires_in_window`
   [MockManager has no .cfg -- stale vs the ALREADY-LIVE 10-23 durability fix at loops.py:130],
   `test_mace_sizing::test_size_floor_and_cap`, `test_mace_strategy_entry::test_clean_entry` + `::test_capacity_skip`)
   are **PRE-EXISTING** and in files THIS fix does not touch. **PROVEN delta = 0 NEW failures:** the same 4
   files produce the IDENTICAL 5 failures on BASE src and FIXED src (mace_pf_delta runner). My 3 edited test
   files + all new tests pass on the fixed run.
Scratch runners: `analysis/mace_pf_scratch.{ps1,sh}` (full suite), `mace_pf_baseline.{ps1,sh}` + `mace_pf_delta.{ps1,sh}` (delta proof).

## RESERVED DEPLOY STEPS (Jack — box-only, no FF-push this task)
1. **Graft the 3 SOURCE files** (code-only): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc-mace-entry-partialfix-2026-10-27-wt\analysis\mace_pf_graft.ps1"`
   - scps `cc\mace_pf_src.tgz`, then drift-gates box==BASE (robinhood 2753939c / rh_broker c8aa5789 /
     execution 71f1a828), backs up to `/home/azureuser/mace_pf_graft_backup_<ts>`, applies CR-stripped,
     verifies POST==TARGET (1e6bbc1e / bac4411a / e380c2f6), py_compiles, ROLLS BACK on any mismatch. Prints
     config_hash (expect UNCHANGED 931a8214be50). Does NOT restart.
   - (Optional) the 3 updated TEST files are for CI/repo parity only — not runtime; not grafted by this runner.
2. **RESTART: YES.** The engine imports these modules at boot; the graft is inert until restart. Run the
   canonical `restart_tc.ps1` (az root; bounces ALL divisions ~3.5min — run OFF market hours / coordinate PEAD+PM).
3. **Boot-verify (RO):** `powershell -ep bypass -f "C:\Users\AA Incorporado\cc-mace-entry-partialfix-2026-10-27-wt\analysis\mace_pf_bootverify_ro.ps1"`
   PASS = new PID, 3 md5s == TARGET, **config_hash UNCHANGED 931a8214be50**, `Robinhood MACE wired` + 5
   schedulers online (incl watchdog), 0 tracebacks (no RobinhoodComboPartial/ImportError/AttributeError), healthz/mace 200.
4. **Rollback** (if needed): restore the 3 files from `mace_pf_graft_backup_<ts>` + restart. (The graft runner
   also auto-rolls-back on in-run failure.)

STOP — built, tested, committed, deploy-ready. No deploy/restart/push performed.
