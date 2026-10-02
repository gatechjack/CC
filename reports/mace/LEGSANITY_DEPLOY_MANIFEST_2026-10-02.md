# MACE leg-sanity guard — deploy manifest + 3-task report (2026-10-02)

Branch `mace-leg-sanity-guard-2026-10-02` (off box-truth `mace-pt-mark-guard-2026-09-18`).
Built/tested/committed. **Deploy (graft) + restart are RESERVED for Jack at market close.**

---

## TASK 1 — GUARD FIX (P1, deploy at close) — BUILT + TEST-PROVEN

**The gap:** `assess_pt_mark_trust` had no intra-condor leg check — it leaned on a same-strike
shorter-dated sibling, so when the 10-16 sibling time-closed (9/25) the 10-02 XLE 60/59/70/71 false-PT
(long 71C mark 0.22 > short 70C 0.15; corrupted dead wing) slipped through.

**The fix (3 files, MACE-scoped, CODE-only — config_hash UNCHANGED `931a8214be50`):**
- `strategy.py` — new SANE sub-check in `assess_pt_mark_trust`: reject (`leg_inversion`, alert) if a
  LONG wing mark >= the SHORT it protects (`lp > sp + eps` or `lc > sc + eps`, `eps = sane_epsilon_usd
  = 0.01`). Runs after the structural check, **before** sibling/fallback → fires INDEPENDENT of any
  sibling. New `leg_marks` kwarg (default None → skipped, backward-compatible). Reuses the existing
  epsilon knob → **no config change**.
- `execution.py` — new `leg_mids(spec)` returning `{sp,lp,sc,lc}` mids; None-tolerant like `mark()`.
- `manager.py` — `_pt_mark_guard` fetches `executor.leg_mids(rung.spec)` **only on the PT-eligible
  path** and passes it to the pure guard. `_manage_one` is **byte-identical to base** (hot path + its
  test stubs untouched).

**md5 (LF) — base -> target:**

| file | base (running) | target (new) |
|---|---|---|
| config.py | 01117cca | **01117cca (UNCHANGED)** |
| strategy.py | 69eef994 | **50dd6984** |
| execution.py | 66dba55a | **5b2307bc** |
| manager.py | 1d4334d7 | **b0e9e879** |
| config/mace.yaml (sha256:12 = config_hash) | 931a8214be50 | **931a8214be50 (UNCHANGED)** |

**Tests (box-scratch, live tree untouched, service venv py3.12, `-p no:pytest_ethereum`):**
- Baseline (unmodified base): **6 pre-existing failures** (config/migration/p14/sizing/2x
  strategy_entry — all stale tests from the 0.95-BP sizing change that post-dates this branch; NOT
  guard-related).
- My code: **identical 6 failures, ZERO new** → no regression.
- Focused guard suite: **37/37 passed** (`test_mace_mark_guard.py` 3 + `test_mace_strategy_manage.py`
  34), including 6 new leg-sanity tests + all existing sibling/fallback/timely/structural tests.
- New tests prove: today's inverted-call condor rejected **with no sibling** (`leg_inversion`); put
  inversion rejected; clean condor still fires PT; near-worthless max-profit condor NOT flagged;
  `leg_marks=None` backward-compatible. Manager-wiring test replays today's shape on an **open** rung
  -> HOLDS open + `mace_pt_mark_reject(reason=leg_inversion)` + "PT held" alert.

**DEPLOY SEQUENCE (Jack, at close) — one line each:**
1. graft (box-write, drift-gated + backup + re-verify + py_compile + rollback-on-fail):
   `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\.claude\worktrees\mace-leg-sanity-guard-2026-10-02\reports\mace\mace_legsanity_graft.ps1"`
2. restart (canonical): `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"`
3. bootverify (RO — I can run): `...\reports\mace\mace_legsanity_bootverify_ro.ps1`

**ROLLBACK:** graft backs up the 3 files to `~/mace_legsanity_graft_backup_<ts>`; on any mismatch it
auto-rolls-back and does NOT restart. If a post-restart problem: restore the backup + restart.

---

## TASK 3 — the stuck 60/59/70/71 rung — ★ CORRECTION TO THE PREMISE

Task 3 says "leave it; the guard fix will stop the re-drive loop (rung HOLDS)." **That is NOT how it
plays out** — flagged, not auto-resolved:

- The rung is latched **`status=closing`** (since the 10:13 winner-PT attempt errored ->
  `_exit_exhausted` -> `mark_closing`). `_manage_one` drives a `closing` rung straight to `close_rung`
  (line 417-418) **before and independent of the PT guard**, and `reconcile()` only touches
  `open`/`submitting` — it **never reopens `closing`**. So across the deploy restart the rung stays
  `closing` and keeps re-driving the marketable close (~every 16 min, empty-response) — **the guard
  fix does NOT reach it.**
- The guard fix's value here is forward-looking: it PREVENTS a future false-PT on an **open** rung
  (and makes a reset-to-open safe).
- **To actually stop today's loop, the rung must be reset `closing -> open`** — the proven
  `mace_xle_reset.py` (from the 9/19 toolkit, same rung_id, guards on status=closing + exit fields
  NULL) does exactly this. **RESERVED DB write; it existed for this rung since 9/19 and targets it
  as-is.** Running it AFTER the guard is live => loop stops immediately AND the guard (leg-sanity) now
  HOLDS the rung on the next false-PT attempt -> managed normally.
- "Leave it (no action)" is financially safe (intact, defined-risk $140, both shorts OTM, DTE ~28) but
  the loop CONTINUES until the close fills / expiry / a reset. **Jack decides: leave looping, or run
  the reset (`...\reports\mace\mace_xle_reset.ps1`) after bootverify.** I did NOT run it.

---

## TASK 2 — ORPHAN ADOPT (reserved DB write; AFTER task-1 deploy + bootverify)

**Origin (RO, confirmed):** opening order **`6aaaf21e`** created **2026-09-16 19:46:38Z** (15:46 ET =
MACE's XLE entry instant), `opening_strategy=iron_condor`, credit, gfd — a MACE 09-16 entry whose DB
row was lost (crash/restart between the RH fill and the durable promote; older logs rotated). Its
order-id is in the same sequential series as rung A (6aac438e/09-17) and rung B (6aad950b/09-18). Live
at RH, no DB row in any status — the only DB<->RH mismatch account-wide; balanced/defined-risk, NOT
naked, but UNMANAGED.

**Adopt row (reconstructed from the RH fill; values verified against live RH 2026-10-02):**

| field | value |
|---|---|
| rung_id | `mace-XLE-2026-10-30-59.5-58-70-71.5-20260916` |
| symbol / status / expiry | XLE / open / 2026-10-30 |
| legs | sell P59.5 / buy P58 / sell C70 / buy C71.5 |
| width_dollars / contracts | 1.5 / 2 |
| credit_actual | 0.45 (net, order 6aaaf21e) |
| max_risk_usd | 210.0 = (1.5-0.45)*100*2 |
| pt_debit | 0.23 = round(0.45*0.50, 0.01) |
| entry_ts / entry_order_id | 2026-09-16T19:46:38+00:00 / 6aaaf21e-910c-429e-8ef5-1213ad4ca920 |
| entry_iso_week | 2026-W38 |
| pt_order_id / exit_* / realized_pnl / extra_json / entry_atm_iv | NULL |

**Runner (idempotent — aborts if the rung already exists; backs up pre-state; post-verifies open +
managed):** `powershell -ep bypass -f "...\reports\mace\mace_orphan_adopt.ps1"` — py_compile clean.
**SEQUENCING (load-bearing):** run ONLY after the guard is live + boot-verified, else MACE could
immediately false-PT-close the orphan on the same XLE-10-30 dead-wing risk.

---

## ARTIFACTS (reports/mace/)
mace_legsanity_graft.{sh,ps1}+.tar.gz, mace_legsanity_bootverify_ro.{py,ps1},
mace_legsanity_scratch.ps1 + mace_guard_scratch.sh (reused), mace_legsanity_base.ps1,
mace_legsanity_focus.{sh,ps1}, mace_orphan_adopt.{py,ps1}, mace_pycheck.ps1. Reset (existing 9/19):
mace_xle_reset.{py,ps1}. Tars rebuildable via `git archive HEAD trading_corp tests config pyproject.toml`.

**No FF-push. Nothing deployed. Reserved actions (graft/restart/DB-writes) await Jack.**
