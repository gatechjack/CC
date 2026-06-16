# Bitunix deploy batch — 2026-06-16 (PREPARED, not deployed)

Consolidates **4 reviewed bitunix branches** to a deploy-prep branch and ships them
**ALONE** (Polymarket excluded — it has issues) in **ONE window / ONE restart**.
This doc + the two scripts are the operator's execution guide. **§4: no deploy /
no prod write was done by the agent** — prod was read-only-inspected only.

Branch: **bitunix-deploy-batch-2026-06-16** (off current main `e27f911`).
Consolidation merge HEAD: **`37edc3c`** (the 4 merges; TARGET md5s below are its blobs).

## 1. The 4 branches (all reviewed, unmerged)

| branch | feature |
|---|---|
| b0ae39d | 10006 rate-limit fix — single-flight + TTL-cache the account snapshot |
| e947ab4 | breaker-abstain — `AccountSnapshot.equity_complete` + breaker abstains on a partial read |
| 00677c6 | #1 signed-fetch auto-book — real fill price/PnL/fee, B2-aware, P2 estimate fallback |
| ef6fa5f | B2 maker execution — POST_ONLY entry + taker fallback, **default OFF**, stop untouched |

## 2. Merge result + the snapshot()/place_order reconciliations

Merge order: b0ae39d → e947ab4 → 00677c6 → ef6fa5f. Conflicts only in `bitunix.py`
(others auto-merged or disjoint). **Both reconciliations verified — no behavior dropped:**

- **snapshot() (b0ae39d × e947ab4):** `_fetch_snapshot` now tracks **two** flags —
  `equity_complete` (stablecoin reads OK → surfaced on `AccountSnapshot` for the breaker
  to abstain) **and** `complete` (stablecoin **and** position reads OK → gates the cache;
  never cache a partial). A position-read error sets `complete=False` but leaves
  `equity_complete=True` (positions don't enter the equity sum). Returns
  `(AccountSnapshot(..., equity_complete=…), complete)`. → **single-flight/TTL caching
  (b0ae39d) AND the equity_complete signal (e947ab4) both present and correct.**
- **place_order (b0ae39d × ef6fa5f):** the cache-invalidation **wrapper** (b0ae39d) +
  the B2 **maker dispatch** (ef6fa5f) compose: `place_order(order, *, fill_timeout_s)` →
  `try: _place_order_impl(order, fill_timeout_s) finally: _invalidate_snapshot_cache()`.
  The maker dispatch (entries-only, `not reduce_only and extra["maker_entry"]`) lives in
  `_place_order_impl`; the maker/taker clones re-enter via the wrapper (each placement
  invalidates the cache). `fill_timeout_s` threads to `_observe_fill` (maker rest window).

**Gate (no git stash — vs the known-stable baseline):** 50/50 feature tests pass
(13 cache + 11 breaker + 13 auto-book + 13 B2). Full suite = **28 failures, NEW=0**
(all in the 5 pre-existing non-bitunix suites: iron_condor / robinhood_multi_leg /
tasty_options / webhooks_return_fast / paper_run_tooling), 0 collection errors →
**zero new regressions.**

## 3. Prod delivery (read-only-confirmed)

Prod is **file-deployed** (no `.git`) → scp/md5-gate/atomic-mv (the P1/P2 pattern), not
`git pull`. Repo root on prod: `/home/azureuser/trading_corp`.

### Files DEPLOYED (6 .py, full-file, md5-gated)

| prod path (under /home/azureuser/trading_corp) | BASE md5 (prod now) | TARGET md5 (merged) |
|---|---|---|
| trading_corp/brokers/bitunix.py | 64d85724…185874e | 70f7904f…226e66 |
| trading_corp/brokers/base.py | 68d40f23…960eb1 | a7886843…a9c0bcd |
| trading_corp/agents/divisions/bitunix_futures_observer.py | e30f1756…8b8b8f5 | 3067a3e9…32dd1ba |
| trading_corp/agents/divisions/bitunix_position_reconciler.py | ae2fbc74…04579c1 | bf048cd1…6b4c0f1d |
| trading_corp/brokers/bitunix_exceptions.py | 4c78ebca…6e8314 | 363b044e…9296d14 |
| trading_corp/agents/strategies/trade_plan.py | 74b9b9de…a569183f | 67f0ff2b…ff5d40 |

(BASE == main `e27f911` blob for all 6 — confirmed clean prod↔main parity at the base.)

### File DEPLOYED as a TARGETED edit (NEVER whole-file) — config/strategies.yaml

Prod `strategies.yaml` (md5 `1f9d39eb…ca1d8c`) holds **`execution_mode: live`** (line 1022)
+ kalshi-disable that the repo version lacks → a whole-file deploy would REVERT live
trading. `deploy_apply_batch.sh` inserts ONLY the 4 `maker_entry_*` keys into the `fees`
block (after `tp_is_maker`), canaried on `execution_mode==live` (aborts otherwise),
idempotent, validated, default **OFF**.

### File EXCLUDED — data_exec.py  ⚠️ (key finding)

Prod `data_exec.py` (`e3e4cca7…`) is **BEHIND main** (`dbaeaa1b…`): main has the
**polymarket E2.5** `order.execution_mode` classification block (commit `f692fa2`) that
prod doesn't (polymarket isn't deployed). The batch's ONLY data_exec change is a
**doc comment** (b0ae39d). Deploying the merged data_exec.py would carry polymarket E2.5
into the bitunix-alone batch — **forbidden**. So data_exec.py is **excluded**; bitunix
self-tags `execution_mode` via the observer (`extra["execution_mode"]`), not this block,
so nothing functional is lost. **Operator: reconcile prod↔main data_exec.py when
polymarket E2.5 deploys (separate).**

## 4. Execution sequence (operator, in the deploy window)

1. **Stage** (local, from the deploy-prep worktree): `bash stage_batch.sh`
   → pushes the 6 LF files to `/home/azureuser/deploy_stage_bitunix_batch` on prod.
2. **Apply** (on prod): `bash deploy_apply_batch.sh`
   → md5-gate (prod==BASE, staged==TARGET) → py_compile → backup `.bak-pre-batch-2026-06-16`
   → atomic-mv → re-verify; then the strategies.yaml targeted edit. **No restart here.**
3. **Restart** (on prod, separate): `systemctl restart trading-corp`
   → loads all 4 fixes in ONE bounce.

Rollback: restore the `.bak-pre-batch-2026-06-16` copies (+ the strategies.yaml backup) and restart.

## 5. Post-restart verification checklist

- [ ] Engine up: `systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState` →
      active/running; note new PID.
- [ ] All 6 files loaded: prod md5 == TARGET for each (re-run the gate loop).
- [ ] `execution_mode: live` + broker `paper=False` PRESERVED (startup audit `mode=LIVE`,
      `live_brokers=["bitunix"]`, `dry_run=false`); kalshi-disable PRESERVED.
- [ ] DD-cap still **0.99** (this deploy did NOT change it — strategies.yaml override untouched).
- [ ] **B2 maker flag OFF**: `bitunix_futures.fees.maker_entry_enabled == false` in the live
      config (maker must NOT auto-arm — behavior-preserving).
- [ ] Reconciler clean: `position_state_reconciled` ticking, match/miss/orphan as expected.
- [ ] No new errors: journal scan for tracebacks / `live_order_rejected` / halt-refusals.

## 6. Deploy-time live-validations (note — NOT blockers)

- **#1 close-fill fetch** (`get_recent_close_fills`): the BitUnix fill side/timestamp shape
  is inferred, not live-verified → confirms on the next real stop-out; **falls back to the
  P2 known-level estimate** if the shape differs (safe). Watch the next stop-out's
  `result_source` (`auto_booked_from_real_fill` vs `…_from_stop_level`).
- **B2 POST_ONLY path**: only exercised when the maker flag is flipped ON later (ships OFF).
  The exact POST_ONLY-would-cross rejection code is inferred → any maker rejection crosses
  to taker (safe). Validate when maker is first enabled.
