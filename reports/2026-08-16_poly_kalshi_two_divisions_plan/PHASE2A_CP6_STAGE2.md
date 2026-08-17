# Phase 2 · CP6 STAGE 2 — deploy runners BUILT (operator drives the sequence; nothing run)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` LIVE + ARMED (PID 760172). These runners perform the
> ONE batched deploy — they restart the armed loop and seed the roster. **Nothing has been run.** The
> operator runs step 1 → pastes → step 2 → pastes → step 3 → pastes, in order. Abort-safe at every step.

Batch = branch tip `d34c758` over box baseline `3706a3a` (Stage-1 drift-gate: **11/11 MATCH**).

## Bundle
- **`cp_cp6_bundle.b64`** placed at `C:\Users\AA Incorporado\cc\` (378,956 b64 chars; gz 284,215 B).
- **`BUNDLE_MD5 = e6e7feaf39daa911aaf5ffd448f75808`** (`base64 -d | md5sum`, hardcoded in the deploy
  runner's abort check).
- Contents: the **12** deploy files @ `d34c758`, **LF-normalized** — every staged file's LF-md5 was
  verified == the Stage-1 new-md5 table before bundling. (LF content ⇒ install-verify is unambiguous;
  `main.py` normalizes CRLF→LF on the box, functionally inert for Python.)

## The four runners (operator-run, IN ORDER)

### 1. `pk_cp6_deploy.ps1` — INSTALL, **NO RESTART** (the key deviation)
`powershell -ep bypass -f .\pk_cp6_deploy.ps1`
Chunk-uploads the bundle → **BUNDLE_MD5** check (abort `ABORT_BUNDLE_MD5`) → **drift-gate** (11× LF-md5
== 3706a3a, abort `ABORT_DRIFT`; `roster_split.py` ABSENT, abort `ABORT_NEW_PRESENT`) → **backup** the 11
modified (`.bak_cp6_<ts>`, prints `BACKUP_SUFFIX`) → extract as azureuser → **install-verify** all 12
LF-md5 == new-expected (abort `ABORT_INSTALL`, backups intact) → prints `NO_RESTART … still on OLD code`
+ confirms `set_agent_state_multi` is on disk. **The running engine stays on OLD code until step 3.**

### 2. `pk_cutover_seed.ps1` — DRY, then `-Apply` (authored @CP5; validated vs temp DB)
`powershell -ep bypass -f .\pk_cutover_seed.ps1` → confirm `selected_whales` = the 4 wallets,
`live_whales` empty. Then `… .\pk_cutover_seed.ps1 -Apply` → atomic 3-key
`set_agent_state_multi` (live_whales := the 4, selected_whales := [], pinned_whales := minus the 4) →
read-back `assert_disjoint` → `CUTOVER_OK True`. (Preflight aborts if `set_agent_state_multi` absent — it
won't be; step 1 installed it.)

### 3. `pk_cp6_restart_verify.ps1` — RESTART onto new code + seeded roster, then verify
`powershell -ep bypass -f .\pk_cp6_restart_verify.ps1`
`systemctl restart` → wait for "MLB copy loop online". **Verify (paste):**
- new **PID**; `POLY_KALSHI_ONLINE_SECONDS > 0`.
- **re-ARM** — journal `MLB copy WIRED (auto_execute=True -> dry_run=False …)` + `loop online … dry_run=False`
  + Python `ARM halted=False` (THE critical check; if not armed → rollback).
- **retarget live** — `CONFIG_RETARGET roster_actor=poly_kalshi_mlb roster_key=live_whales`;
  `LIVE_WHALES n=4` + `SELECTED_WHALES n=0` + `PINNED_WHALES n=0` (the loop reads the 4 FROM live_whales;
  the paper sim has nothing to paper).
- **boot invariant** — journal `roster invariant OK` + Python `INVARIANT_OK live=4 paper=0 disjoint`.
- **open position rides** — `OPEN_POSITIONS 1` (flag-3: no re-order), dashboard shows BALTB-TB still
  marking (poller alive on new code).
- **paper Telegram killed** — `PAPER_TELEGRAM_CARDS_SINCE_RESTART 0`.
- 0 `Traceback`/`CRITICAL` in the boot window.

### 4. `pk_cp6_rollback.ps1` — abort path (build-alongside)
`powershell -ep bypass -f .\pk_cp6_rollback.ps1 -BackupSuffix .bak_cp6_<ts> -CutoverWasApplied`
⚠️ **Ordering hazard handled:** if step 2 already seeded `live_whales` (emptying `selected_whales`), the
OLD restored code reads `selected_whales` and would watch nobody. With `-CutoverWasApplied`, the rollback
**FIRST reverses the cutover** (move `live_whales` back to `selected_whales`+`pinned_whales`) via a
**self-contained** `BEGIN IMMEDIATE` transaction that does NOT depend on `set_agent_state_multi` (which
the restore removes) — validated vs a temp DB (post-cutover live=4 → live=0/selected=4/pinned=4) — THEN
restores the 11 from `.bak_cp6_<ts>`, removes `roster_split.py`, restarts, and confirms the old code has
its roster back. (Omit `-CutoverWasApplied` if the failure is before step 2.)

## 5. prod-live-git catch-up (git-only, AFTER verify passes — no box touch)
Once step 3 verifies green, advance the `prod-live` mirror to reflect the box (it lags: git at `18db30e`,
box at `3706a3a` + now this batch): document Phase 2b (`→ 3706a3a`) then this deploy (`→ d34c758`) via the
established deploy-commit mechanism (commit message = deploy log; runtime files only). Pure git
bookkeeping so the NEXT deploy's baseline is honest. I finalize the exact git commands at execution.

## Validation done (no prod touched)
- All 3 new runners: **0 non-ASCII, `[scriptblock]::Create` parse OK**, no-BOM, placed at cc root.
- All embedded Python heredocs **compile** (`ast.parse`).
- Bundle: 12/12 staged LF-md5 == Stage-1 new-md5 table; BUNDLE_MD5 computed.
- Cutover logic (CP5) + rollback reverse logic proven vs temp DBs. **No az call executed.**

## Ops summary (short pastes, in order)
```
powershell -ep bypass -f .\pk_cp6_deploy.ps1
powershell -ep bypass -f .\pk_cutover_seed.ps1
powershell -ep bypass -f .\pk_cutover_seed.ps1 -Apply
powershell -ep bypass -f .\pk_cp6_restart_verify.ps1
```
Rollback if needed: `powershell -ep bypass -f .\pk_cp6_rollback.ps1 -BackupSuffix .bak_cp6_<ts> -CutoverWasApplied`
Avoid the 15:40–15:58 ET restart window.
