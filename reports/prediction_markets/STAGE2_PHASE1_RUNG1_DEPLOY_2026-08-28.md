# Stage 2 · Phase 1 · RUNG 1 — DEPLOYED LIVE 2026-08-28 00:16:10Z (verified, HALTED)

**Branch:** `prediction-markets-stage2-phase1-2026-08-27` @ `a8cefb5` · **Authorized:** Jack, rung 1 of Stage 2
phase 1 ONLY (no prod-live push, no DB writes, no poller/adjudicate/rollup runs, no phase 2). **Result: the 7
nav-skeleton artifacts are LIVE and verified; the deploy was ADDITIVE (legacy pages byte-identical, new routes
404→200).**

## What deployed
7 PM-only artifacts (`web/app.py` + 5 new templates + `web/static/pm.css`), byte-verified **box == branch
`a8cefb5`** by the deploy re-hash gate (git blob + perms 644 + owner azureuser). Activation = the single pm_web
restart (all 7 are in the pm_web serve/import path; no `pm_cli`-loaded file changed; no migration — schema 9).

## Sequence + PIDs (as executed)
- **DEPLOY** (azureuser SSH; custody + backup + forced-644 + re-hash gate; NO restart): manifest-assert 7==NEW;
  pre-place custody `app.py`+`pm.css` == BASE `8563c62`, 5 templates absent; backup `app.py`+`pm.css` →
  `~/pm_stage2_codebak_20260827T234450Z/`; placed + `chmod 644`; gate all 7 blob==NEW / 644 / azureuser. `DEPLOY_OK`.
  *(Operator ran deploy before pre — harmless: deploy does not restart, so the running pm_web (24808) kept serving
  OLD code and the baseline stayed valid.)*
- **PRE baseline** (read-only; captured against the still-old running service): pmweb **24808** unchanged, engine
  **676**; 3 new routes **404** (the "before" proof); legacy `/`=94879 `/scoreboard`=91307 `/farm`=130303; schema 9;
  ppt 124 (2/115/7); pcs 7. Baseline written `~/pm_stage2_baseline.txt`. *(Fixed a runner bug first: `PPID` is a
  bash readonly special var → renamed to `PWPID`; re-ran clean.)*
- **RESTART** (root `az vm run-command`, the ONLY service mutation): pm_web **24808 → 38491**, NRestarts 0,
  **ExecMainStatus 0**, active/running, started 00:16:10Z. Engine (trading-corp) not referenced.

## POST-checks — ALL PASS (STOP conditions NONE)
| Check | Result |
|---|---|
| engine PID | **676 unchanged** |
| pm_web | **24808 → 38491**, NRestarts 0, ExecMainStatus 0, running |
| `/healthz` | 200 (schema 9) |
| Legacy `/` / `/scoreboard` / `/farm` | **94879 / 91307 / 130303 — byte-IDENTICAL to baseline** (nothing bled) |
| `/dashboard`, `/farm-league` | 404 → **200** (2338 / 4478 bytes) |
| Tiles | **exactly 15**: atp·cs2·epl·fed·golf·mlb·nba·nfl·nhl·soccer·tennis·ucl·ufc·wnba·wta — no cbb/fifwc/unknown |
| `/farm-league/atp` (real category) | **200** (2708 bytes) |
| **Casing on live** | h1 **`ATP`** present; lowercase `atp` absent |
| `cbb` / `unknown` / nonsense | all **404** |
| `/static/pm.css` | **200** (served) |
| DB | schema 9, ppt 124, pcs 7 — **untouched**; `/farm` byte-identical (no poll fired mid-window) |

**Two-sided proof satisfied:** legacy pages unchanged; new pages new; casing correct on the box (not just in tests).

## prod-live ledger (authored LOCAL, NOT pushed)
`prod-live` advanced `8563c62 → 7ca932a` (a **local** additive fast-forward commit recording the 7 artifacts,
staged blobs == `a8cefb5`; 95e78c4 stays an ancestor — MACE fork base intact). **NOT pushed — origin/prod-live
remains `8563c62`.** Pushing it is a separate authorization.

## Scope boundaries honored
No prod-live push; no DB write; **cadence untouched** (the `*/30` poller kept running — a poll fired at 00:00Z
during baseline capture, advancing `last_polled_ts` only, `/farm` unmoved — harmless, not run by us); no phase-2
work; engine, poly_kalshi_mlb, MACE, PEAD, bitunix untouched.

## Rollback (if ever needed)
Restore `app.py` + `pm.css` from `~/pm_stage2_codebak_20260827T234450Z/`, remove the 5 new templates, restart
pm_web (root az). The 5 new templates have no box counterpart, so removal fully reverts them.

**HALT. Phase 2 is not authorized.** Runners: `cc\pm_stage2_rung1_{pre,deploy,restart,post}.*`.
