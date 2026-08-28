# Stage 3 — RUNG 1 (SCHEMA: migrations 010 + 011) DEPLOYED LIVE 2026-08-28

**Rung 1 of the 4-rung Stage-3 deploy ladder (`TRANSITION_STAGE3_DEPLOY_2026-08-28.md §B`). PM-ONLY. Behaviour-neutral.
Live PM DB advanced schema 9 → 11.** Deployed under Jack's explicit per-rung authorization; halted after post-checks.
**No `prod-live` advance and no push this pass** (ledger recorded on the branch only — see §5).

## 1. What deployed
- **One file:** `trading_corp/prediction_markets/db.py`, branch-tip `f3b7a1d` version (carries `MIGRATION_010`,
  `MIGRATION_011`, and the `SCHEMA_HEAD` constant from R2). Content sha256 **`13c6a730…518503`**.
- **Migration 010** — money-layer schema (PURE DDL): `pm_account`, `pm_subdivision` (FIXED sizing + risk-cap columns,
  `market_types` default `'moneyline,total,spread'`), `pm_subdivision_order` (per-sub-division live-order journal).
- **Migration 011** — `pm_subdivision_attachment` (PURE DDL; the farm→money bridge, joined ON CATEGORY, reversible
  `active` flag). 010 + 011 landed in ONE rung (each additive `CREATE … IF NOT EXISTS`, per-migration transaction).

## 2. Gate-A + deploy mechanics (fail-closed runner `cc\pm_r1_deploy.*`)
- **Fresh single-file Gate-A at deploy moment:** box `db.py` sha256 == prod-live base **`106e2b03…83815`** (verified
  immediately before overwrite — the REVISED STOP: proceed only if the box's PM `db.py` still equals `166b5ab`).
- **Staging:** exact `f3b7a1d` blob bytes `scp`'d to `~/pm_stage3_r1_stage_dbpy.new`, sha256 re-verified `13c6a730`
  on the box before use.
- **Backups (kept):** online DB backup from a `mode=ro` connection → `~/pm_stage3_r1_bak_20260828T231807Z/prediction_markets.db`
  (`integrity_check=ok`, `version=9`, sha256 `a49ae0b8…`); code backup `…/db.py.box_pre_r1`.
- **Placement:** copy into place → `chown azureuser:azureuser` → `chmod 644` → re-hash gate (sha256 `13c6a730`,
  perms `644`, owner OK). (The tar-664 quirk does not apply to an `scp`/`cp` placement, but 644 is forced + asserted.)
- **Activation — EXPLICIT controlled `init_db()` (the chosen fork, option a):**
  `cd /home/azureuser/trading_corp && PYTHONPATH=. venv/bin/python -c "from trading_corp.prediction_markets.db import init_db; print(init_db())"`
  → applied 9→10→11, each migration its own transaction. (Not left to the `*/30` poller — deterministic, immediately verifiable.)
- **Cadence guard:** the runner aborts before any mutation if within 120s of the next `:00/:30` poll or 60s after one.
  Deploy ran 23:18:07Z (713s to next poll) — clear of the 05:00–05:50Z window.

## 3. Post-checks — ALL PASS (2026-08-28T23:18Z)
- `MAX(version)=11`; `schema_version` rows exactly **1..11** (atomic, no gap/dupe).
- `PRAGMA quick_check=ok`.
- 4 money tables **present + EMPTY** (`pm_account`/`pm_subdivision`/`pm_subdivision_order`/`pm_subdivision_attachment`).
- **No existing table dropped; non-money-table schema digest UNCHANGED** (no rebuild/alter — the mig-002/006 lesson held).
- **Every baseline count UNCHANGED** (before → after): `pm_watchlist` 114 (pinned/active=1 **92**, active=0 **22**),
  `pm_paper_trade` 140 (open **113** / closed **21** / pending **6**), `pm_paper_category_stats` 9, `pm_whale` 14,
  `pm_closed_position` 29852, `pm_category_stats` 114, **`pm_paper_config` 3→3** (PURE DDL — no config row written, the
  009 property held), `pm_roster` 114, `pm_analysis_cache` 0, `pm_score_snapshot` 134.
- **Shared services undisturbed (no restart):** engine `trading-corp.service` MainPID **53046** NRestarts 0 (since
  21:30:05Z), pm_web `prediction-markets-web.service` MainPID **42343** NRestarts 0 (since 04:03:58Z).
- **pm_web reflects it:** `/healthz` now `{"…","pm_db_schema_version":11}`; `/`, `/farm` 200. The 4 cadence crons intact.
- Box `db.py` persists as `13c6a730` (5 hits of `MIGRATION_010/011/SCHEMA_HEAD`).

## 4. §H checkpoint (three data bases stay separate)
Rung 1 adds only EMPTY money-layer tables (the Live basis's future journal + account/sub-division/attachment structure),
read by no live code path. It writes NOTHING to the Prospect (`pm_closed_position`/`pm_category_stats`) or Watchlist
(`pm_paper_trade`/`pm_paper_category_stats`) bases — all their counts are unchanged. Three bases stay separate.

## 5. ★ Corrected box-ahead picture (LEDGER-ACCURACY — recorded, NOT acted on)
A full-tree git-tracked blob compare (box vs `origin/prod-live 166b5ab`, runner `cc\pm_r1_drift_ro.*`) established that
the box leads prod-live by **more than the transition doc's "8 MACE files."** `166b5ab` is a **PMCC-only** FF off
`7220e32`, so it lags **every** division's direct-to-box deploys:
- **MACE-namespace (9):** `mace/{broker_port,config,domain,execution,manager,rh_broker,strategy}.py`, `web/mace_view.py`,
  `web/templates/mace_live.html`.
- **Non-MACE package (~10, all documented deploys):** `agents/strategies/{pead_strategy,pead_signal,pead_backtest_driver}.py`
  (PEAD 2026-08-26 ISSC→IA + rename-defense), `brokers/{robinhood,kalshi_live,base,tastytrade}.py`, `main.py`,
  `persistence/db.py`, `agents/divisions/_observer_test.py`.
- **Config/data (operationally edited):** `config/nasdaq_composite.txt` (ISSC→IA universe), `config/mace.yaml`,
  `data/research_starter_universes/large_mid_cap.json`.
- **`MISSING=946`** = the box is a deployed SUBSET (tests/reports/deploy-archives/scripts/runbooks never ship there).

**Every package drift maps to a documented division deploy — none unexplained.** The `trading_corp/prediction_markets/`
package + rung-2/3 shared files (`data/mlb_poly_kalshi_match.py`, `utils/secrets.py`) match `166b5ab` byte-for-byte, so
PM's Gate-A is per-file against prod-live. **When PM eventually advances prod-live, that commit will record PM's
artifacts on `166b5ab` and will NOT describe the box fully** — the ~9 MACE + ~10 non-MACE package files above lead it,
folded in by their owning divisions later. The eventual prod-live commit message MUST say so. **This pass advances
nothing and pushes nothing.**

## 6. State after rung 1
- Branch `prediction-markets-stage3-2026-08-28`; `origin/prod-live` still `166b5ab` (unchanged; `95e78c4` reachable).
- Live PM DB **schema 11**, behaviour-neutral (money tables empty, read by no live code). Cadence live.
- **Rungs 2 (matcher), 3 (web R3+R6), 4 (execution) remain UNAUTHORIZED. R5.5 / R7 / R8 unauthorized. HALT.**
- Rollback material (if ever needed): DB backup + code backup in `~/pm_stage3_r1_bak_20260828T231807Z/`.
