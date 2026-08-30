# Shard rungs 1-2 + yes_bid fix — DEPLOY PLAN (Option a; Jack authorizes each step, I HALT)

**Date:** 2026-08-30 · **Branch:** `pm-shard-scope-2026-08-30` @ `8d49910` · Jack ruled **Option (a)**: deploy rungs
1-2 + the `yes_bid` fix now — smallest change that un-blocks placement, and it lands the shard gate BEFORE any live
order. **Every step is Jack-authorized; I build + validate the runners and HALT.**

## MANIFEST — 3 files, ALL PM-ONLY (no SHARED file changed)

| file | change | SHARED / PM-ONLY | writable-by |
|---|---|---|---|
| `trading_corp/prediction_markets/shard_balance.py` | **NEW** (rung 1) | PM-ONLY | azureuser (ssh) |
| `trading_corp/prediction_markets/execution.py` | gate 6b + `evaluate(shard_balances=…)` | PM-ONLY | azureuser (ssh) |
| `trading_corp/prediction_markets/live_driver.py` | exchange_index on market dict, per-cycle shard fetch, alarm, **yes_bid fix** | PM-ONLY | azureuser (ssh) |

**No SHARED files** (the matcher `mlb_poly_kalshi_match.py` and broker `kalshi_live.py` are UNCHANGED). Files deploy
via **ssh** (the `prediction_markets/` dir is azureuser-writable) — **no az-root file deploy**.

## ★ ACTIVATION — YES, this needs an ENGINE restart (shared service → Jack coordinates)

The driver runs **inside the `trading-corp` engine**, which holds the old `execution.py`/`live_driver.py` in memory
and does not import the new `shard_balance.py`. **The fix + gate 6b take effect ONLY on an engine restart.** The
engine is a **SHARED service** — **bitunix runs 24/7 on it**, plus MACE/PEAD/PMCC — so the restart is Jack's
coordinated **az-root** `Desktop\restart_tc.ps1`. **pm_web does NOT need a restart** (these are engine/driver files,
not web files).

## ★★ ARMED THROUGH THE DEPLOY? — NO. Disarm → deploy → restart → verify → re-arm (my recommendation, = Jack's instinct)

The arm state is PERSISTED (armed). A restart with the NEW (fixed) code + a clean boot-reconcile would come up
**armed and now ABLE to place** → the first live order could fire as a **side effect of the restart**. To make the
first order happen because we TURNED IT ON:

1. **DISARM** (`live-disarm --global`) — persisted; the engine will come up disarmed.
2. **DEPLOY** the 3 files (backups + Gate-A) — **no restart in this step**.
3. **RESTART** the engine (Jack's `restart_tc.ps1`, az-root, shared) — comes up **DISARMED** (we disarmed) with the
   new code. **Placement is NOT possible here.**
4. **POST-CHECKS** (read-only, disarmed) — incl. the would-place proof (below). Placement still not possible.
5. **RE-ARM** (`pm_arm.ps1`: arm-time shard-3 gate → arm global+sub → confirm) — **★ placement becomes possible ONLY
   HERE**, deliberately, after the fix is proven.

**Recommendation: YES, disarm for the deploy and re-arm after** — the first order should be an act, not a restart
artifact. (Strong rec; Jack rules.)

## CAPS — still bounding the first order

The R7.f step-1 caps are set on `kalshi_jack/mlb` (written 18:04Z, verified): **`max_orders_per_day=1`,
`per_order_usd_cap=2.0`, `daily_usd_cap=2.0`, `max_open_usd=2.0`** (+ `fixed_stake_usd=0.01`=1 contract, slippage 2c,
liquidity_ratio 0.75). The deploy does NOT touch `pm_subdivision`, so they persist. **Post-check re-confirms them via
`sub_config_from_row`** before re-arm. Worst case after re-arm: one ~$0.50-1 order, then gate-8 hard-stop.

## POST-CHECKS (step 4, read-only, DISARMED)

1. Engine PID **changed** (restart happened) + NRestarts, pm_web unchanged.
2. Arm state **DISARMED** (`effective_armed:false`), `pm_subdivision_order` still **0**.
3. Caps re-confirmed via `sub_config_from_row` (1 / $2 / $2 / $2).
4. **★ THE FIX PROOF — disarmed would-place:** walk SDTrading's real genuinely-open MLB signals through the DEPLOYED
   `fetch_market_context` + `evaluate`; assert the market dict now carries `yes_bid_dollars` (was dropped) and a real
   signal reaches **WOULD-PLACE** (or a LEGITIMATE non-illiquid skip: `skip:shard_underfunded` / `skip:no_quote` /
   `skip:duplicate`) instead of the phantom `skip:illiquid`. Runs both with gate 6b OFF (isolates the liquidity fix)
   and with gate 6b ON (the full deployed decision, per-shard). This is the proof the fix works — **available BEFORE
   arming.**
5. PM-package re-hash: the 3 deployed files match the branch; nothing else in the package changed.

## RUNNERS (built + validated; Jack authorizes each)

- **S1** `pm_shard_deploy_disarm.ps1` — `live-disarm --global` + confirm `effective_armed:false`.  (live arm write)
- **S2** `pm_shard_deploy.ps1` — backup execution.py+live_driver.py → deploy 3 files (ssh tar) → force 644 → **Gate-A
  (py_compile + TRANSITIVE import in the service dir)** → re-hash. **NO restart.**  (live file write)
- **S3** `Desktop\restart_tc.ps1` — Jack's canonical az-root engine restart (SHARED; Jack coordinates).
- **S4** `pm_shard_deploy_postcheck.ps1` — the read-only post-checks above (incl. the would-place proof).  (read-only)
- **S5** `pm_arm.ps1` — the existing arm runner (shard-3 gate → arm global+sub → confirm).  (live arm write)

**ROLLBACK (before re-arm):** restore the 2 backups + `rm shard_balance.py` (+ another restart if already restarted).
The system is DISARMED throughout the rollback window, so no order can fire.

*Each step HALTS for Jack's explicit authorization. Nothing is deployed, restarted, disarmed, or armed by me.*
