# Phase 2 · CP6 STAGE 1 — drift-gate + fileset scoping + sequence plan (READ-ONLY; nothing deployed)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` LIVE + ARMED, engine untouched. Stage 1 is
> read-only analysis + one operator-run READ-ONLY drift-gate runner. **No writes, no restart, no
> cutover, no bundle build.** The Stage-2 deploy runner is built only after this passes + operator OK.

Batch = branch tip **`d34c758`** vs box baseline **`3706a3a`** (Phase 2b, the ACTUAL running box).
Carries: `8dc4d97` (poller log fix) + `dcebfcc` (display batch) + Phase 2a `91ca08a`(CP2) `7e90137`(CP3)
`954ee7f`(CP4) `d34c758`(CP5).

## 1. FILESET (git `3706a3a..d34c758`, filtered to deploy files)

**DEPLOY (12 files): 11 MODIFIED + 1 NEW.** md5 = LF-normalized (`tr -d '\r' | md5sum`).

| # | file | box md5 (3706a3a baseline) | new md5 (d34c758) |
|---|------|----------------------------|-------------------|
| 1 | `config/strategies.yaml` | `ec8684da6911f0d79c08148bab07d518` | `df6ce50ab1155eaa295f772ce8de1a23` |
| 2 | `trading_corp/agents/poly_kalshi_marks.py` | `887f1bb09610a7c301dc3fc9060b37cc` | `6fd9cf5d9c2338004c5af260422d72e3` |
| 3 | `trading_corp/agents/strategies/poly_kalshi_executor.py` | `3ad0824666d5d89435c7b614a5ff1872` | `d1f871f9c3e83530dc6fba3bd58c2eae` |
| 4 | `trading_corp/agents/strategies/polymarket_copy_trader.py` | `49d3a5d01280e02d7761bd66957f7eec` | `b203db5fcc1548aa06349e4fd5dd0f77` |
| 5 | `trading_corp/main.py` (CRLF on box) | `229693a8b5a2dd809f6e8825b667cb80` | `6b8b516990443f7a5c8f264f8314dfde` |
| 6 | `trading_corp/persistence/db.py` | `9daf8bf6474f3fef712bbf217d7ab3a1` | `c0432d37d859f2376a6507f3ce06c00b` |
| 7 | `trading_corp/web/data.py` | `36180479f3df051ff43ce5f496bfd7dd` | `28db1cc33a597c85ed8ef0449e51bf0d` |
| 8 | `trading_corp/web/routes.py` | `589291482a32911e41229b60680fcd2e` | `b4d1ee00d097737a967bdef4ca76ee93` |
| 9 | `trading_corp/web/templates/home.html` | `31589243c9e8f92a0d8cfd7eb0c2d176` | `c55beec58137e56a4a2b9ce1b89e186e` |
| 10 | `trading_corp/web/templates/partials/poly_kalshi_live.html` | `176d102c3c867890d35fdaeeb5e7db03` | `020e1bf831b94feaf78378addffee328` |
| 11 | `trading_corp/web/templates/partials/poly_kalshi_live_inner.html` | `69267e6dae67b345c53e00aa09545581` | `17cff25c381d433e80742118cce753c5` |
| 12 | `trading_corp/agents/strategies/roster_split.py` **(NEW)** | *(ABSENT on box)* | `21b1ccbef6db76975364c7a8885a8166` |

**Baseline validity:** box == 3706a3a for all 12. Cross-check vs the pk_p2b install-verify md5s (the
files Phase 2b actually installed): files 2,3,6,7,8,10,11 match exactly (LF on box). `main.py` box RAW
= `295eb345…` (CRLF) but LF-normalized = `229693a8…` = my baseline (memory: "main.py is CRLF"). Files
1,4,9 (strategies.yaml / polymarket_copy_trader.py / home.html) were NOT touched by Phase 2b —
verified unchanged `18db30e..3706a3a`, so box == 3706a3a for them too.

**EXCLUDED (NOT deployed):**
- `reports/2026-08-16_poly_kalshi_two_divisions_plan/*` — 6 markdown + `pk_cutover_seed.ps1` +
  `pk_cp6_driftgate_ro.ps1` (operator-run runners, not deploy files).
- `tests/*` — `test_agent_state_multi.py`, `test_roster_split_cp3.py`, `test_roster_split_cp4.py`
  (new) + `test_poly_kalshi_executor.py`, `test_poly_kalshi_live_view.py`, `test_poly_kalshi_marks.py`,
  `test_polymarket_copy_e2_6_loop_wiring.py` (modified). Tests never deploy.
- **No whale-recency scripts** in this batch (confirmed — they were on a different branch).

## 2. MIGRATION CHECK — additive-only, NO migration
- No DDL added anywhere in the batch: `git diff 3706a3a..d34c758` added lines contain **no**
  `CREATE TABLE / ALTER TABLE / DROP / ADD COLUMN / CREATE INDEX`.
- CP2's `set_agent_state_multi` + `_upsert_agent_state_row` use the **existing `agent_state`** table
  (`INSERT … ON CONFLICT`). The Phase-2b volatile mark tables (`poly_kalshi_mark_live/_history`)
  **already exist on the box** (migration ran at the Phase 2b deploy). ⇒ No schema change, no ALTER/DROP.
  The RO runner confirms all 3 tables (`agent_state` + 2 mark tables) are present.

## 3. DRIFT-GATE vs BOX — operator-run RO runner (paste output)
Runner: **`pk_cp6_driftgate_ro.ps1`** (authored + ASCII/parse-validated; placed at
`C:\Users\AA Incorporado\cc\`). Read-only: LF-md5 of each of the 11 modified files vs the 3706a3a
baseline (expect **11× MATCH**), asserts `roster_split.py` **ABSENT**, confirms the 3 tables exist,
and reports engine PID + open-position count + the current roster keys (cutover precondition). Run:

```
powershell -ep bypass -f .\pk_cp6_driftgate_ro.ps1
```

**PASS criteria (paste back):** all 11 `MATCH`; `ABSENT_OK roster_split.py`; `EXISTING_TABLES` = all 3;
`CUTOVER_PRECOND selected_whales` = the 4 wallets, `live_whales` = None/empty. **Any `DRIFT` → STOP +
diagnose before Stage 2.** (Gated against the BOX, not prod-live git 18db30e which lags by all of 2b.)

## 4. SHARED FILES — confirmed OUT of the fileset + byte-unchanged
`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py` are **not** in
`git diff --name-only 3706a3a..d34c758` (empty) and `git diff 386074c` on them is empty. They do not
deploy and are byte-unchanged across all of Phase 2a.

## 5. CONFIG — strategies.yaml IS in the fileset
`config/strategies.yaml` (file #1) carries the CP3 roster retarget
(`poly_kalshi_mlb.roster_actor: poly_kalshi_mlb` / `roster_key: live_whales`) — the config that points
the live loop at `live_whales`. It IS a deploy file. **This is why the cutover must seed `live_whales`
before the restart** (§6).

## 6. STAGE-2 SEQUENCE PLAN (do NOT execute — built after Stage-1 passes + OK)

Ordering is the thing that makes this deploy different (⚠️ install → cutover → restart):

1. **RO drift-gate** — `pk_cp6_driftgate_ro.ps1` → 11× MATCH, roster_split ABSENT, tables OK, roster
   precondition (selected=4, live empty). (This step.)
2. **Build bundle** (Stage-2 authoring, local) — gzip tar of the **12** deploy files @ `d34c758` →
   base64 sidecar `cp_cp6_bundle.b64`. Record bundle md5.
3. **`pk_cp6_deploy.ps1`** (operator-run) — upload bundle (chunked) → bundle-md5 check → **drift-gate**
   (LF-md5 vs 3706a3a, 11× MATCH; roster_split ABSENT) → **backup** the 11 modified (`.bak_cp6_<ts>`) →
   extract as azureuser → **install-verify** (LF-md5 == the new-md5 column, all 12) → **STOP. NO RESTART.**
   *(This is the key deviation from pk_p2b, which restarted inline — here the restart is deferred.)*
4. **`pk_cutover_seed.ps1`** (operator-run) — **DRY** (confirm the 4 wallets, live empty) → **`-Apply`**
   (atomic 3-key: seed `live_whales`=4, clear `selected_whales`/`pinned_whales`; `CUTOVER_OK`).
5. **`pk_cp6_restart_verify.ps1`** (operator-run) — `systemctl restart trading-corp` → wait for
   "Kalshi MLB copy loop online" → **verify:** new PID; `auto_execute=true / dry_run=false /
   halted=false` (re-ARM — THE check); live loop loads **4** from `live_whales`; paper excludes them;
   boot log "roster invariant OK" (or the loud error); 0 boot tracebacks; mark poller ticks; open
   position (`BALTB-TB`) still marks (flag-3: was 1 open → re-check count, bounded by the $100 + 25/day
   halts); no PCT paper Telegram. Rollback on failure: `-Reverse` the cutover + restore `.bak_cp6_<ts>`.
6. **prod-live-git catch-up** (git-only, NO box) — advance the `prod-live` mirror `18db30e → 3706a3a`
   (documents the file-overwritten Phase 2b) → `→ d34c758` (this batch), with a deploy_log commit. Pure
   git bookkeeping so the record stops lagging the box; exact mechanism (ff-merge vs deploy-commit)
   settled at Stage 2.

**Avoid the 15:40–15:58 ET restart window.** flag-3 (restart resets in-memory idempotency) is bounded
by the settlement-sweep loss-halt + the 25/day count-halt + the executor order journal.

## Stage-1 RESULT — drift-gate PASS (operator-run, verified)
`pk_cp6_driftgate_ro.ps1` run on the box (ENGINE_PID **760172**):
- **11 / 11 MATCH** — every modified deploy file == 3706a3a baseline. **No drift.**
- **`ABSENT_OK roster_split.py`** — new file not on box (clean install).
- **`EXISTING_TABLES` = `['agent_state','poly_kalshi_mark_history','poly_kalshi_mark_live']`** — all 3
  present → additive, no migration.
- **`OPEN_POSITIONS 1`** (BALTB-TB pre-game; flag-3 context).
- **Cutover precondition (confirmed):** `selected_whales` = the 4 wallets; `pinned_whales` = the **same
  4** (the §1.5 pin hazard is real — the 3-key move must clear pins); `live_whales` = **None** (empty
  until the cutover seeds it). The 4 wallets match the roster of record (`EXPECT_N=4` holds):
  - `0x16bb9951a36fce71e2ef57890b786145e0ba8492` (SDTrading)
  - `0x2dc13c6bda81b202281e796953a7323de675b33c` (xifutloong3)
  - `0x684baa57c338c2549aec0aa3f034f695d72a8409` (monkeymashingkeyboard)
  - `0x9c3ce009c9b039956665cecc4cd14de862b5e8c9` (0x0x23kj…)

**Stage 1 PASSES.** Nothing deployed; live loop untouched. Awaiting operator OK to build Stage 2
(deploy-no-restart + restart-verify runners + `cp_cp6_bundle.b64`).
