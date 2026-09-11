# MACE #2 — GDX time-exit/PT close cap (mid-band + defer) — BUILT + TESTED (2026-09-11)

BUILD-ONLY. Nothing deployed/restarted/pushed. MACE-scoped. Forked from `7683f59b` (deployed #1 box-truth), branch
`mace-gdx-cap-boxbase-2026-09-11 @ c2593e34`. Fixes the GDX overpay (report MACE_TIME_EXIT_OVERPAY_2026-09-11.md).

## The fix (per exit reason — close ladder in execution.py `close_rung`)
- **TIME (>floor) + PROFIT-TARGET (winners):** price at **MID** (`_credit_mid` from the fresh quotes the ladder
already fetches) and **cap the debit at `mid + exit_winner_band`** (default **0.10**), walking mid→mid+band across
the attempts; **never start at natural, never exceed mid+band.**
- **STOP / exdiv / gap (losers/risk):** **UNCHANGED** — start at natural (cross-the-spread), walk to the width cap;
a loser/deadline MUST fill. The band does NOT apply.
- **Fill-failure:** **TIME** → **DEFER** (rung stays OPEN, retries next tick, walking DTE down); at
`DTE <= time_exit_defer_floor_dte` (default **14**) it **FORCES natural** (some close beats carrying a defined-risk
condor to expiry). **PT** → **stays OPEN, retries, NO floor** (a winner is fine to keep).
- Routing lives in `manager._close_pricing(reason, rung, now)`; the executor's winner path skips `mark_closing` (a
defer must stay OPEN) and returns `ExitOutcome(deferred=True)` on a clean unfilled exhaust (`_exit_deferred`;
error/unconfirmed still → `_exit_exhausted` which now marks CLOSING idempotently for crash-recovery).

## GDX replay (test `test_time_winner_fills_within_band_preserves_profit_gdx_replay`)
GDX #1 today: credit 1.58, mid 1.05 at the decision tick, filled 1.52 → **+$6**. With the fix: cap = mid+band =
**1.15**; a fill at the cap books **(1.58−1.15)×100 = +$43** (vs +$6). GDX #2 (credit 1.57, mid 1.105): cap **1.205**
→ **+$36.5** (vs the actual **−$10**). If the book is at natural (1.52/1.67 » cap) the close **DEFERS** rather than
give the ~$50 to the spread — retrying daily until it fills within the band or hits the 14-DTE floor. Either way it
never crosses the whole spread on a winner.

## Config — the 2 knobs (config_hash MOVES)
`config/mace.yaml` management block:
```
exit_winner_band: 0.10
time_exit_defer_floor_dte: 14
```
**config_hash: `49476b0e` → `bfde856f1c468d11…`** (loader knobs are optional-with-default, mirroring
`min_strike_separation_usd`, so an older config still loads).

## Fork-preserved + MACE-scoped (confirmed)
- Forked from `7683f59b`: `execution.py` still carries **`mace_missed_exit` (×1)** (from #1) + **`exit_disposition_line`
(×4)** + wing-pricing/sizing — none reverted (grep-verified).
- **MACE-scoped only:** 4 files — `config/mace.yaml`, `mace/config.py`, `mace/execution.py`, `mace/manager.py`.
**NO shared-trio file** (`data_exec.py`/`main.py`/`robinhood.py`). Diff: +108/−19.

## Tests
- **6 new** (in `test_mace_execution.py`, all pass): winner starts-at-mid/caps-at-band/defers; winner fills-at-cap →
+$43 (GDX replay); STOP unchanged (natural, band not applied); PT defers-no-floor; SPY unaffected (natural within
band → fills, no defer); `_close_pricing` routing (TIME>14=winner/defer, TIME<=14=marketable-force, PT=winner,
STOP/exdiv=marketable).
- Full `test_mace_execution.py` = **45 passed**. **0 new regressions** — 3 pre-existing local failures
(`test_mace_config` config_hash CRLF artifact; `test_mace_strategy_entry` stale max_rungs 5-vs-25) fail
IDENTICALLY on clean `7683f59b` (proven by stash), unrelated to this change. Dep-heavy suites are the box-venv
pre-gate at deploy.

## Box-only gated deploy (post-review; NO FF-push; restart is Jack's)
Grafts 4 files (base = box-truth `7683f59b` → target `c2593e34`):
| file | base SHA12 (== box now) | target SHA12 |
|---|---|---|
| config/mace.yaml | `49476b0ec024` (live config_hash) | `bfde856f1c46` |
| mace/config.py | `d5aeb3652a44` | `43fabae5345a` |
| mace/execution.py | `46464fa3a03f` (== #1-deployed) | `073b6eeddbec` |
| mace/manager.py | `deb073d368e5` | `a28d4e633cae` |
Sequence: pre-gate (box-scratch full suite, 0 new) → backup → graft (base-check box==`7683f59b`) → Jack restart →
boot-verify (**config_hash == `bfde856f…`**, winner pricing live, rungs intact, 0 tracebacks). NO FF-push (git
reconcile deferred). Runners to be cut on the go (same shape as #1's).
