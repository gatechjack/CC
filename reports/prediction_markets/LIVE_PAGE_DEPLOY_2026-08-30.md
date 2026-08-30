# PM `/live` page fix — DEPLOY COMPLETE (2026-08-30)

Deployed the `/live` live-trade + held-position fix (branch `pm-shard-scope-2026-08-30`,
commits `8aef572` build + `ff512f4` review fixes). **pm_web restart only — engine never
touched, no bitunix bounce.** Read-only web change; no order path, no broker import.

## What / why
The `/live/{account}/{category}` page rendered "no live trades yet / live copies arrive
with the execution engine" while a **real filled order existed** (`pm_subdivision_order`
id=1, the platform's first live fill). The route never read the order journal; the section
was hardcoded in R3 when the table was empty and never re-wired. Same class as the ATP
tile-vanish defect (UI cannot see data that exists). Twin defect: sizing shown as
"$0.01/copy" (true about the stored stake, misleading about behaviour — $0.01 floors to 1
contract; the copy cost $0.60).

Now: `subdivision.live_orders` / `live_positions` / `live_order_count` / `sizing_summary`
+ real template tables (Live trades, Currently held), tile trade-count, de-staled dashboard.
Held positions are **journal-derived** (net signed fills per ticker, boot_reconcile's
+YES/-NO convention), labelled as the journal's view — pm_web has no broker; the engine's
boot reconcile is what cross-checks the venue.

## Manifest (5 files, old -> new sha256)
| file | old (on box) | new (branch `ff512f4`) |
|---|---|---|
| trading_corp/prediction_markets/subdivision.py | c3e01380… | 0cfae0a9… |
| trading_corp/prediction_markets/web/app.py | 9bc29f79… | 47c75d56… |
| trading_corp/prediction_markets/web/templates/pm_live_subdivision.html | 6d1df826… | 08d9286f… |
| trading_corp/prediction_markets/web/templates/pm_live_list.html | 0cffb440… | 6fd8aad7… |
| trading_corp/prediction_markets/web/templates/pm_dashboard.html | a5b79953… | a5d3c871… |

Tests (`tests/prediction_markets/test_live_r3.py`) are on the branch but NOT deployed.

## Procedure (discipline)
- **Time:** files 23:13:13Z, restart ~23:17Z, post-check 23:18:08Z — clear of the 05:00–05:50 window.
- **Gate-A (23:12:39Z):** extracted the branch tree on the box, imported the new
  `subdivision` + `web.app` in the box venv (transitive imports resolve), py_compile OK.
- **Per-file backups:** `~/pm_live_deploy_backup_20260830T231313Z` (all 5).
- **Placement:** `install -m 0644`; **perms assertion** PASS — each file `644 azureuser:azureuser`,
  not world-writable; **box == branch** on all 5 hashes; live-tree import re-check OK before restart.
- **Restart (az-root, canonical):** `restart_pmweb.ps1` = `az vm run-command … "systemctl
  restart prediction-markets-web"` — pm_web ONLY. Board-authorized atomic execution. Provisioning succeeded.

## Post-check (23:18:08Z) — all GREEN
- **engine `trading-corp` MainPID 101836 UNCHANGED** (NRestarts 0); **pm_web 89704 -> 103913**
  (NRestarts 0, clean).
- **schema 13; `pm_subdivision_order` total=1 dry_run0=1** (no 2nd order).
- **arm state UNTOUCHED:** global `armed:true latched:false`; sub `kalshi_jack:mlb`
  `armed:false latched:true auto_trigger:count_ceiling` — the terminal safe latched state intact.
- `/healthz` 200, schema 13.
- `/live/kalshi_jack/mlb`: real trade (ticker · YES · ENTRY · sub 0.62 · fill 0.60 · fee
  0.0084 · filled) + held (1 YES @ 0.60, cost 0.60) + "not a live venue read"; sizing
  "1 contract per copy" + "flat-contracts" note. ABSENT: "no live trades yet", "0.01/copy",
  "arrive with the execution engine".
- `/live` tile "1 live trade" (0× "no live trades yet"); dashboard 0× "arrive in Phase 3";
  `/farm` 200, `/farm/mlb` 200, `/live/nope/mlb` 404.

## Rollback (if ever needed)
Restore the 5 files from `~/pm_live_deploy_backup_20260830T231313Z` (per-file), then restart
`prediction-markets-web` again. Engine untouched throughout.

## State
Branch `pm-shard-scope-2026-08-30` @ `ff512f4` (this ledger adds one more commit). **prod-live
NOT advanced** (Jack authorizes that separately, after R7.g). Box PM `/live` files == branch.
R7.g (the hand-inspected reconcile) remains Jack's separate authorization — not started.
