# CP7 report — deploy + both epochs + restart + verify (operator-run)

**Status: DEPLOYED LIVE & VERIFIED. Phase 1 complete.** Branch `poly-kalshi-mlb-phase1-2026-08-15`.

## Live-money / live-loop status (lead)
- **The 5-file deploy + restart SUCCEEDED.** Engine restarted 2026-08-16 20:52:41 UTC, PID **753629 → 756639**, LIVE (`system/startup dry_run:false`), 0 boot tracebacks.
- **`poly_kalshi_mlb` is LIVE / ARMED / UNHALTED:** boot journal `Poly->Kalshi MLB copy WIRED (auto_execute=True -> dry_run=False, stake=$5.0, halt=$100.0)` + `loop online (poll=7.0s, dry_run=False)`; `StrategyState.halted=False`. Real-money trading resumed at the restart; **no orders placed at boot**.

## What CP7 deployed
Operator ran `pk_cp7_driftcheck_ro.ps1` (stage 1) → `pk_cp7_deploy.ps1` (stage 2) → `pk_cp7_finish_verify.ps1` (finish). Deploy = a 25.7 KB **patch** of exactly 5 runtime files (whale-recency scripts on the branch were **excluded** — standalone, not runtime-imported), applied with a drift-gate + `patch --dry-run` + per-file md5 verify:
| file | prod-live (was) | new (now) |
|---|---|---|
| config/divisions.yaml | e91c3aac | d3cfe1eb |
| config/strategies.yaml | 60bb0947 | ec8684da |
| trading_corp/agents/kalshi_resolver.py | 360adc81 | 272454bb |
| trading_corp/agents/strategies/poly_kalshi_executor.py | be3ac001 | 1397ef5a |
| trading_corp/web/data.py | 76448e33 | 0e4bcd90 |

Then **both epochs set to the same split instant** `2026-08-16T20:29:25+00:00` (poly_kalshi at CP6; polymarket_copy_trader at CP7-finish).

## Deploy verification (from the two operator-run outputs, verbatim facts)
- Drift-gate: all 5 `MATCH` prod-live baseline → `DRIFT_GATE_OK`. Backups `.bak_cp7_20260816_205240`. `PATCH_DRYRUN_OK` → `INSTALL_VERIFIED all 5 md5 == new-expected`.
- Boot: `POLY_KALSHI_ONLINE_SECONDS 25`, `Registered kalshi broker for division=poly_kalshi_mlb (paper=False)` (**division registered**), armed/wired as above.
- **Both dashboards read 0 from the epoch:** poly_kalshi `n_resolved=0 / history=0 / open=0 / badge=0` (badge==list); PCT `n_resolved=0`.
- **On-disk history retained (reversible):** PCT legacy (no epoch) `n_resolved=9722, wins=5362, voids=255, realized=$208.32`; 3 poly_kalshi audit rows retained.
- **PCT epoch:** `2026-07-07T20:00:54 → 2026-08-16T20:29:25 OK True`.
- **poly_kalshi ARMED/unhalted:** `halted False reason None`.
- **No equity double-count:** `EQUITY_ROWS [('kalshi_arbitrage', 27618)]` — **no** `poly_kalshi_mlb` row (poly_kalshi wires no equity loop; the equity gate is satisfied).

## One bug hit + fixed (transparency)
The deploy runner's epoch sub-step ran the python as a **file** (`python3 /tmp/cp7_epoch.py`), so `sys.path[0]=/tmp` → `ModuleNotFoundError: trading_corp`. The 5-file deploy + restart were unaffected. Fixed in `pk_cp7_finish_verify.ps1` by piping via **stdin** (`python3 -`, cwd on sys.path) — the CP6-proven pattern — which then set the PCT epoch + ran the deep verify cleanly.

## Remaining / follow-ups (NOT blockers)
- **Gross-vs-net fee reconciliation (CP4 residual):** deferred to the **first post-CP7 settled fill** — verify Kalshi's `pnl_dollars` == `qty*(1-fill_price)` against the real KAREN settlement. If Kalshi nets fees, net `fill_fee` in the resolver (CP3 persists it). No post-CP3 fill has settled yet.
- **prod-live advance (git bookkeeping):** prod-live must be advanced with the **5 deployed files only** (NOT the whale-recency scripts) + a deploy_log.md entry, so the next deploy's drift-gate baseline is correct. This branch's deploy_log is a stale fork (last entry 2026-08-14); the authoritative CP7 deploy_log entry belongs on prod-live. **Pending operator direction** (and a push decision).

## Ops
- Backups on box: `.bak_cp7_20260816_205240` (all 5 files). **Rollback:** `pk_cp7_rollback.ps1` (restores + restart; reverts code only — the epochs are intentional, reverted by deleting the two agent_state metrics_epoch rows).
- Runners (operator machine, `pk_*.ps1` convention): `pk_cp7_driftcheck_ro.ps1`, `pk_cp7_deploy.ps1` (+ `cp7_diff.b64`), `pk_cp7_finish_verify.ps1`, `pk_cp7_rollback.ps1`.
- Kill: `auto_execute:false` in strategies.yaml (hot) or persist a halt; restart to re-read.
