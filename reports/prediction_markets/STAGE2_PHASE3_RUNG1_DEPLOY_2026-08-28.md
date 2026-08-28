# Stage 2 · Phase 3 · RUNG 1 — DEPLOYED LIVE 2026-08-28 04:03:58Z (verified, HALTED)

**Branch:** `prediction-markets-stage2-phase3-2026-08-28` @ `734c516` · **Authorized:** Jack, rung 1 of Stage 2
phase 3 ONLY (no prod-live push this pass; Stage 3 unauthorized). **Result: the Farm-League hierarchy now serves
the good URLs, the flat scoreboard/farm pages are gone, and there is one shell — verified by the inverted proof.**

## What deployed
**9 MODIFIED + 5 DELETED, no new files.** Byte-verified **box == branch `734c516`** by the deploy re-hash gate
(9 modified blob + perms 644 + owner; 5 deleted absent). **Custody covered modified AND deleted:** all 14 pre-place
== BASE `2916f44` (no drift), all 14 backed up to `~/pm_stage2_p3_codebak_20260828T035748Z/` (the 5 deleted backups
are the rollback material), the 9 placed + `chmod 644`, the 5 removed.

## Sequence + PIDs
- **PRE baseline** (read-only, phase-2 code still running): `/`=scoreboard **94879**, `/farm`=flat **130299**,
  `/farm/ufc`=**404** (route didn't exist), 5 templates **present**, ufc closed whales rendered at `/farm-league/ufc`
  (Kh4mz4t/evanng), **exact open count 109** (total 129); engine **676** / pm_web **40435**.
- **DEPLOY** (azureuser SSH; custody + backup + place/remove + gate; NO restart): manifest 9==`734c516`; all 14 ==
  BASE `2916f44`; backup 14; place 9 (644) + remove 5; gate 9 blob==NEW/644/owner + 5 ABSENT → `DEPLOY_OK`.
- **RESTART** (root `az vm run-command`): pm_web **40435 → 42343**, NRestarts 0, **ExecMainStatus 0**, active,
  started 04:03:58Z. Engine not referenced. *(Brief expected window between deploy and restart where the old
  in-memory code referenced the now-deleted templates — `/healthz` stayed 200; the restart closed it.)*

## POST-checks — ALL PASS (STOP conditions NONE) — the proof INVERTS
| Check | Result |
|---|---|
| engine PID | **676 unchanged** |
| pm_web | **40435 → 42343**, NRestarts 0, ExecMainStatus 0, running |
| **`/` CHANGED** | **94879 → 2306** (scoreboard → dashboard), `has_menucard` False→True — NOT byte-identical (byte-identical would be STOP) |
| **`/farm` CHANGED** | **130299 → 4339** (flat farm → tiles), `has_tilegrid` True |
| old + temp paths | `/scoreboard`, `/dashboard`, `/farm-league`, `/farm-league/ufc`, `/farm/list` — **all 404** (no aliases) |
| **`/farm/ufc`** | **404 → 200**; **Kh4mz4t 100%/+90 and evanng 100%/+11 intact** through the move (closed numbers byte-for-byte) |
| 5 deleted templates | **absent** on the box |
| whale + watchlist details | **200, one shell (Live sub-divisions nav), no stale `← scoreboard` crumb** |
| DB | schema 9; total 129→129; **open 109→108 (−1) — moved by the 04:00 `*/30` poll**, not the deploy |

**On the "content intact" bar:** `/farm/ufc` was 19403 B vs `/farm-league/ufc` 19444 B pre — a 41-byte diff, which
is the **live open counts** shifting with the 04:00 poll (open 109→108), **not** the repoint. The *closed* paper
results (the substitution-bug-fix content) are byte-for-byte intact. The repoint moved only routing.

## prod-live ledger (authored LOCAL, NOT pushed) — chain
`prod-live` advanced **`2916f44` → `7220e32`** (local, records the 9 modified [staged blobs == `734c516`] + the 5
deletions). **Single linear lineage: `8563c62` → `7ca932a` (phase-1) → `2916f44` (phase-2) → `7220e32` (phase-3)** —
no fork. `95e78c4` stays an ancestor (MACE fork base). **NOT pushed** — `origin/prod-live` remains `8563c62`; one
fast-forward advances it to `7220e32` through the chain when authorized.

## Activation path
All changes activated on the **single pm_web restart** — `app.py` is the entrypoint, the templates are pm_web-rendered;
the deletions took effect because the running pm_web was replaced by one whose routes/templates no longer reference
them. **No `pm_cli`-loaded file changed**; **no migration** (schema 9).

## Rollback (if ever needed)
Copy all 14 from `~/pm_stage2_p3_codebak_20260828T035748Z/` back into place — this **restores the 5 deleted pages**
AND reverts the 9 modified — then restart pm_web (root az). ~14 file copies + one restart, seconds, no DB
involvement. The deleted pages are also recoverable from git `prod-live @ 2916f44`.

## Runner-environment note (filed in the plan's STANDING BOX QUIRKS)
The deploy ran from a **32-bit** PowerShell session, so `System32` redirected to `SysWOW64` (no OpenSSH) and
`ssh`/`scp`/`az` weren't on the bare path; the runners resolved them via **`Sysnative`** / the Azure CLI path.
Filed as STANDING BOX QUIRK #4 so future deploys bake it in.

**HALT. Stage 3 is not authorized.** Runners: `cc\pm_stage2_p3_rung1_{pre,deploy,restart,post}.*`.
