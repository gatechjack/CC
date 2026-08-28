# Stage 2 · Phase 2 · RUNG 1 — DEPLOYED LIVE 2026-08-28 02:00:07Z (verified, HALTED)

**Branch:** `prediction-markets-stage2-phase2-2026-08-28` @ `9d07038` · **Authorized:** Jack, rung 1 of Stage 2
phase 2 ONLY (no prod-live push this pass; Phase 3 + Stage 3 unauthorized). **Result: the 8 per-category-content
artifacts are LIVE and verified; the filled Watchlist renders real PAPER numbers for the first time.**

## What deployed
8 PM artifacts, byte-verified **box == branch `9d07038`** by the deploy re-hash gate (git blob + perms 644 +
owner azureuser). **Modified-vs-new custody split** (against the phase-1 ledger `7ca932a` = the box's prior state):
- **4 MODIFIED** (pre-place `box==BASE 7ca932a` + per-file backup): `positions.py`, `web/app.py`,
  `web/templates/pm_farm_category.html`, `web/static/pm.css`.
- **4 NEW** (asserted absent pre-place; rollback = remove): `pm_watchlist_whale.html` +
  `partials/{pm_watchlist_rows, pm_prospects_rows, pm_paper_trade_rows}.html`.

## Sequence + PIDs
- **PRE baseline** (read-only): engine **676** / pm_web **38491**; `/watchlist/…` **404** (before-proof);
  legacy `/`=94879 `/scoreboard`=91307 `/farm`=130305; **exact open count = 114** (total 126: 2 closed / 114 open
  / 10 pending); pcs 7; schema 9.
- **DEPLOY** (azureuser SSH; custody + backup + forced-644 + gate; NO restart): 8 manifest == `9d07038`; 4 MODIFIED
  == BASE `7ca932a` (no drift); 4 NEW absent; backup `~/pm_stage2_p2_codebak_20260828T015717Z/`; gate all 8
  blob==NEW / 644 / azureuser → `DEPLOY_OK`.
- **RESTART** (root `az vm run-command`, the ONLY service mutation): pm_web **38491 → 40435**, NRestarts 0,
  **ExecMainStatus 0**, active/running, started 02:00:07Z. Engine not referenced.

## POST-checks — ALL PASS (STOP conditions NONE)
| Check | Result |
|---|---|
| engine PID | **676 unchanged** |
| pm_web | **38491 → 40435**, NRestarts 0, ExecMainStatus 0, running |
| `/healthz` | 200 (schema 9) |
| **① exact open count** | baseline **114** → post **111** (−3); total 126→127 — **moved by the 02:00 `*/30` poll** (fingerprint advanced), NOT the deploy |
| legacy `/` / `/scoreboard` | **byte-IDENTICAL** (94879 / 91307) |
| **③ `/farm` HARD check** | 130305 → 130301 MOVED, **but a poll fired** → poll-classified (n_open/poll_state), not a bleed → NOT a STOP (a no-poll move would have STOPped; `pm_macros.html` untouched) |
| `/watchlist/…` | **404 → 200** (new route live) |
| **② ufc Watchlist real PAPER content** | `/farm-league/ufc` 2710→**19444 B**; **Kh4mz4t** closed=1 **100%** net **+90** (roi +856%), **evanng** closed=1 **100%** net **+11** (roi +12.9%) — page renders each name/win/net (checked against live-DB values, not hardcoded); **4751346 open=4** (R6 open count) |

**The substitution-bug fix is now visible with real content** — the pinned list shows genuine *paper* win% / net
PnL, not a borrowed completed-lane number. Legacy pages unchanged; engine + DB untouched (schema 9).

## prod-live ledger (authored LOCAL, NOT pushed) — how it chains
`prod-live` advanced **`7ca932a` → `2916f44`** (a **local** additive commit, parent `7ca932a`, staged blobs ==
`9d07038` == the deploy-gate box hashes). **Single linear lineage: `8563c62` (origin tip) → `7ca932a` (phase-1
ledger) → `2916f44` (phase-2 ledger)** — no fork, no divergent unpushed commits. `95e78c4` stays an ancestor
(MACE fork base). **NOT pushed** — `origin/prod-live` remains `8563c62`; a future push fast-forwards it to
`2916f44` **through `7ca932a`**, advancing both ledgers together.

## Activation path
All 8 activated on the **single pm_web restart** — `web/app.py` is the entrypoint; the templates are pm_web-rendered;
`pm.css` is pm_web-served; **`positions.py` is imported by the pm_web request path** (via the whale/watchlist
loaders). **No `pm_cli`-loaded file changed** (paper.py / db.py / the client untouched); **no migration** (schema 9).

## Rollback (if ever needed)
`cp` the 4 MODIFIED from `~/pm_stage2_p2_codebak_20260828T015717Z/` back, remove the 4 new templates, restart
pm_web (root az). The 4 new templates have no box counterpart, so removal fully reverts them.

**HALT. Phase 3 and Stage 3 are not authorized.** Runners: `cc\pm_stage2_p2_rung1_{pre,deploy,restart,post}.*`.
