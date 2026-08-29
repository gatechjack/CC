# Stage 3 — RUNG 4 (EXECUTION: execution.py + arm.py + scripts/pm_cli.py) DEPLOYED LIVE 2026-08-29

**The LAST deploy rung. All PM-only. `execution.py`/`arm.py` are ENGINE-side and INERT (no caller until R7);
`scripts/pm_cli.py` is LIVE cron infrastructure. All-ssh. Cadence CONFIRMED clean. No `prod-live` advance.**
**★ WITH THIS RUNG THE BUILT R1–R6 STACK IS FULLY DEPLOYED (rungs 1 schema · 2 matcher · 3 web · 4 execution).**

## 1. The 3 files (EXPLICIT manifest, never the raw diff)
- `prediction_markets/execution.py` (A) `fecf3ca9` — the DRY-RUN chokepoint (R4). Imports `arm` + `kalshi_live` +
  `mlb_poly_kalshi_match`. **No caller until R7 → inert on disk.**
- `prediction_markets/arm.py` (A) `0f36f676` — the arm/kill control plane (R5). Standalone (does NOT import execution).
- `scripts/pm_cli.py` (M) `39d73232` → `f813f5c2` — the CLI. **Live cron target** (poller `*/30` + refresh/adjudicate/
  rollup). Its line-24 `from …prediction_markets import (… arm …)` is why it waited for this rung.

## 2. ★ Deploy channel + the scripts/ nuance (verified NOW, not from record)
- `scripts/` **dir** is owned `197609:197121` (Windows-numeric, `755`) → **NOT azureuser-writable** — same class as
  `data/` (quirk 5). **BUT `scripts/pm_cli.py` FILE is `azureuser:azureuser 644` → writable.** Deploying pm_cli.py is
  an **in-place overwrite** (`cp` truncates the existing inode → needs *file* write, not *dir* write), so **ssh works —
  NO az-root** (contrast rung 2, where the *file itself* was root-owned). `prediction_markets/` is azureuser `775`
  (arm.py/execution.py created there via ssh). **This nuance is why I verified the dir AND the file, per Jack.**

## 3. ★ Structural ordering (the load-bearing safety)
`execution.py` imports `arm`; `pm_cli.py` imports `arm`. So **`arm.py` MUST be present before the new `pm_cli.py`.**
The runner deploys **arm.py + execution.py FIRST, verifies them, THEN overwrites pm_cli.py LAST** — the new pm_cli is
NEVER on disk without `arm.py`. If a poll fires mid-deploy (before pm_cli is overwritten) it runs the OLD pm_cli
(imports neither arm nor farm_actions → safe). Timed in a poll gap (deploy 02:47:26Z, 754s to next poll; guard needs
≥150s to next `:00/:30` + ≥60s after one).

## 4. ★ The poller-import gate (fail-closed, restores on ImportError)
Immediately after overwriting pm_cli.py, the runner ran `pm_cli.py --help` (`cd $ROOT && PYTHONPATH=.` — the cron's
env). A non-zero exit or `ImportError/Traceback` → **RESTORE pm_cli.py from `~/pm_stage3_r4_bak` + abort (HAND BACK,
no second attempt)** — this catches an import break BEFORE a real poll hits it. Result: **`--help` OK, imports clean;
new subcommands parsed: `live-arm, live-attach, live-detach, live-disarm, live-status`.**

## 5. Post-checks — ALL PASS
- **box == branch** for all 3 (`0f36f676` / `fecf3ca9` / `f813f5c2`), perms `644`, owner `azureuser:azureuser`.
- **★ arm state reads DISARMED on LIVE:** `pm_cli live-status` → `{"actor":"pm_live","global":null,"global_armed":false}`
  (absent → DISARMED, the R5 fail-safe inversion — proven on the live box for the first time, not a copy); **`pm_live`
  rows in legacy `agent_state` = 0** before AND after.
- **Nothing else moved:** `/farm` **4288→4288** byte-identical; schema 11; all PM counts unchanged; 4 money tables 0;
  engine **53046** / pm_web **59422** unchanged (no restart — INERT rung); healthz 200.
- **PM-package hash diff = EXACTLY {arm.py, execution.py}** added (`unexpected=NONE`); pm_cli.py verified separately.
- **★ CADENCE CONFIRMED (the whole risk):** the **03:00:05Z poll** — the FIRST run of the NEW pm_cli.py — ran
  **CLEAN**: `captured 1, touched 14, pairs 92, incomplete 0, errors: 0`, **no ImportError/Traceback**. (The 02:30
  poll, pre-deploy, was also clean.) Verified via `cc\pm_r4_cron_wait_ro.*` — **reported only after the poll fired and
  was read.**

## 6. Activation + §H + state
`execution.py`/`arm.py` activate only when the engine runs them (R7) — deployed **inert, no engine/pm_web restart**.
`pm_cli.py`'s new subcommands activate on the next invocation (the cron) — CONFIRMED working. **No DB write** (all
counts unchanged); §H three bases untouched. **arm state = agent_state control plane (0 pm_live rows), not a PM base.**
- **★ STAGE 3 DEPLOY COMPLETE:** the built R1–R6 stack is live (schema 11 · shared matcher · web R3/live + R6 POST
  routes · execution+arm modules inert). `origin/prod-live` still **`166b5ab`** (`95e78c4` reachable) — **advancing
  prod-live is a SEPARATE authorization now that all four rungs are in.** Rollback: `~/pm_stage3_r4_bak/pm_cli.py.box_pre_r4`.
- **R5.5 boot-reconcile, R7 (first live order / MONEY GATE), R8 (parallel test) remain UNAUTHORIZED. HALT.**
