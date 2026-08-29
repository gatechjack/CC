# Stage 3 — RUNG 3 (WEB: R3 /live + R6 three actions) DEPLOYED LIVE 2026-08-29

**Rung 3 of the 4-rung ladder. 10 PM-ONLY web-layer files under `prediction_markets/`, activated by a pm_web
restart. This rung CHANGES what Jack sees (the inverted proof) and adds pm_web's first mutating POST routes.
Deployed via a 3-runner split (ssh stage → az-root restart → ssh post-check). No `prod-live` advance.**

## 1. The 10-file set (EXPLICIT manifest, never the raw diff — see §5)
Deployed (branch `cde1013`/`f3b7a1d` content): **M(5)** `web/app.py`, `web/templates/pm_dashboard.html`,
`pm_shell.html`, `partials/pm_prospects_rows.html`, `partials/pm_watchlist_rows.html`; **A(5)** `subdivision.py`,
`farm_actions.py`, `web/templates/pm_live_list.html`, `pm_live_subdivision.html`, `pm_live_404.html`. Tar `fad993f8`.
**EXCLUDED:** `db.py` (rung 1, done), `arm.py`/`execution.py` (rung 4), **`scripts/pm_cli.py` (moved to rung 4 — §4)**.

## 2. ★ Two new deploy findings (mechanism)
- **`pm_cli.py` cannot ship in rung 3** (the live-cron catch, §4). Moved to rung 4.
- **The pm_web restart needs az-root** (STANDING BOX QUIRK #6): pm_web is a SYSTEM service
  (`/etc/systemd/system/prediction-markets-web.service`, unit file `root:root`). `User=azureuser` governs what the
  PROCESS RUNS AS, **not who can MANAGE THE UNIT** — restarting a system unit needs root; `sudo` is forbidden + fails
  (no NOPASSWD). There is **no azureuser path**. So: FILE deploy = ssh (files are azureuser-owned `prediction_markets/`);
  ACTIVATION (restart) = az-root RunShellScript — same channel as rung 2 + the engine. **Caught proactively by reading
  the unit file, not by hitting the wall.**

## 3. The 3-runner split (fail-closed boundary sits BEFORE activation)
- **Runner 1 (ssh, azureuser):** timing guard → baseline → Gate-A (5 M == prod-live, 5 A absent) → custody + 10-file
  manifest-assert → backup (`~/pm_stage3_r3_bak`, 5 M) → extract → chmod 644 + re-hash gate. **No restart.** Any
  Gate-A/re-hash failure restores + aborts — all azureuser, pre-restart, pm_web still serving OLD code from an
  untouched process → **reversible with nothing user-visible changed.**
- **Runner 2 (az-root, Jack ran the `az`):** `systemctl restart prediction-markets-web` → poll `/healthz` 200+schema11
  → PASS or **self-rollback** (restore 5 M backups + chown azureuser + chmod 644, remove 5 A files, re-restart, report
  ROLLED-BACK, hand back — no second attempt). Terse (az truncates). Result: **`R3AZ: PASS` pm_web 42343→59422,
  engine 53046.**
- **Runner 3 (ssh, azureuser, read-only):** the full post-checks below.

## 4. ★ The pm_cli.py ordering correction (why it's a rung-4 file)
`scripts/pm_cli.py` module-level-imports `arm` (`from trading_corp.prediction_markets import (... arm, ... farm_actions ...)`,
line 24). `arm.py` is a **rung-4** file. The **live cron runs `pm_cli.py paper-poll` every 30 min** (+ refresh/adjudicate/
rollup at 05:xx) — deploying the branch `pm_cli.py` before `arm.py` would raise `ImportError` on the next poll and take
down the poller, refresh, adjudicate and rollup together. So `pm_cli.py` deploys in **rung 4** with `arm.py`+`execution.py`.
**Generalizable lesson (recorded in the transition doc): WHEN SOMETHING BECOMES SCHEDULED, EVERY PLAN THAT TOUCHES IT
NEEDS RE-READING** — the cadence was installed AFTER the phased plan was drafted, so `pm_cli.py`'s blast radius changed
from "harmless until someone types a command" to "runs unattended every 30 min" **without the file changing.**

## 5. ★ The explicit-manifest deploy rule (recorded in the doc)
`git diff prod-live..branch` lists PMCC's live files (`web/data.py`, `web/routes.py`, `division.html`,
`_pmcc_pricing.html`, config, tests) as "changes" **because the branch was cut before the PMCC `166b5ab` perf deploy** —
the branch is *behind* on them. Deploying any would REVERT PMCC's live work. **The deploy set is always an EXPLICIT
enumerated manifest, never "whatever the diff shows."** The same trap waits for rung 4.

## 6. Post-checks — ALL PASS (2026-08-29T02:20Z, runner 3)
- **health/PIDs:** healthz 200 schema 11; **pm_web 42343→59422** (restarted); **engine 53046 unchanged** (web deploy
  never restarts the engine). (`NRestarts` stays 0 — a manual `systemctl restart` doesn't bump the auto-restart counter.)
- **inverted proof (SHOULD change):** `/` 2306→2193, `/farm` 4339→4288, `/farm/ufc` 19403→**22419** — all moved by the
  R3+R6 templates (LIVE nav/card, POST forms, per-pair open counts). **Cleanly the template deploy, NOT poll:** every DB
  count is unchanged, so no poll wrote data in the window.
- **/live:** 404→**200** honest-empty ("no sub-divisions"); **/live/{a}/{c}** → **404** honest (pm_live_404 — a
  non-existent sub-division correctly not-found; none exist yet).
- **NO GET mutates:** GET on all 3 POST paths (`/farm/ufc/promote/…`, `/demote/…`, `/live/kalshi/mlb/attach/…`) →
  **405** (routes are `@app.post` POST-only; a crawler/prefetch/refresh cannot mutate), and **counts unchanged after**.
- **3 buttons no longer disabled** (on `/farm/ufc`, verified on the box HTML): demote POST-forms **0→10**;
  promote-to-live = honest **`no live account`** ×10 (0 accounts provisioned); prospects = honest **`no-search`**
  message (0 candidates — inert until Search, renders honestly, not broken).
- **schema 11; ALL PM counts unchanged; 4 money tables 0.**
- **PM-package hash diff = EXACTLY the 10** (`unexpected_moved=NONE`, `expected_not_moved=NONE`) — proves the tar
  carried what the manifest said and nothing else.

## 7. §H checkpoint + activation + state
R3/R6 render the Live list's STRUCTURE (honest-empty, no P3 data) and write the funnel/attachment lists only via the
POST routes (NOT exercised this deploy — creating the first sub-division is Jack's action). The three data bases stay
separate; no DB write occurred (all counts unchanged). **Activation = the single pm_web restart** (loads app.py +
templates + subdivision + farm_actions). Engine untouched. `origin/prod-live` still `166b5ab` (`95e78c4` reachable).
Backup `~/pm_stage3_r3_bak`. **Cron confirmation: the next `*/30` poll (decoupled — runs `pm_cli`, not pm_web, and
pm_cli was NOT deployed) is verified separately against `pm_poll.log`.** **Rung 4 (execution.py + arm.py + pm_cli.py)
remains UNAUTHORIZED. HALT.**
